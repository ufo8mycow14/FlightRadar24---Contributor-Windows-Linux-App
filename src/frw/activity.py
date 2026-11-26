"""Bounded structured history and rule alerts, independent of the diagnostic log.

Decoder callbacks only enqueue small, validated observations. SQLite, retention,
aggregation and delivery run outside both the decoder and GUI threads.
"""
from __future__ import annotations
import collections
import copy
import datetime as dt
import email.message
import json
import math
import queue
import smtplib
import sqlite3
import ssl
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from .aircraft import distance_nm
from .features import FeatureSettings, matches, validate_endpoint

OBSERVATION_KEYS = ('icao', 'callsign', 'latitude', 'longitude', 'altitude', 'altitude_source',
    'groundspeed', 'track', 'vertical_rate', 'vr_source', 'squawk', 'on_ground',
    'wake_vortex', 'category', 'category_typecode', 'emergency_state', 'data_source', 'field_ages', 'callsign_received')

def plain(value, maximum=160):
    return ''.join(c for c in str(value) if ord(c) >= 32)[:maximum]

def quiet_now(config, timestamp):
    start, end = config['quiet_start'], config['quiet_end']
    if not start or start == end:
        return False
    clock = dt.datetime.fromtimestamp(timestamp).strftime('%H:%M')
    return start <= clock < end if start < end else clock >= start or clock < end

def alert_content(rule, row, timestamp, mode):
    identity = plain(row.get('callsign') or row['icao'])
    title = f"{'[PLAYBACK] ' if mode == 'playback' else ''}{plain(rule['name'], 80)} · {identity}"
    stamp = dt.datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec='seconds')
    parts = [f"{identity} · ICAO {row['icao']}", f"Rule: {plain(rule['name'])}", f"Received: {stamp}"]
    for key, label, unit in [('altitude', 'Altitude', 'ft'), ('groundspeed', 'Ground speed', 'kt'),
                            ('track', 'Ground track', '°'), ('vertical_rate', 'Vertical rate', 'ft/min'),
                            ('squawk', 'Squawk', '')]:
        value = row.get(key)
        if value is not None:
            source = row.get('altitude_source') if key == 'altitude' else row.get('vr_source') if key == 'vertical_rate' else None
            parts.append(f"{label}: {value} {unit}".strip() + (f' ({source})' if source else ''))
            if key in row.get('field_ages',{}):
                parts[-1]+=f" · {row['field_ages'][key]:g}s old"
    if row.get('latitude') is not None and row.get('longitude') is not None:
        parts.append(f"Position: {row['latitude']:.6f}, {row['longitude']:.6f}")
        if 'latitude' in row.get('field_ages',{}):
            parts[-1]+=f" · {row['field_ages']['latitude']:g}s old"
    if row.get('emergency_state') is not None:
        parts.append(f"Broadcast emergency state: {row['emergency_state']}")
    parts.append('Source: ' + plain(row.get('data_source', 'Unknown')))
    parts.append('Received observations; route and airport movements are not confirmed.')
    return title, '\n'.join(parts)

class AlertEngine:
    def __init__(self):
        self.states = collections.OrderedDict()
        self.last_global = collections.deque()
        self.suppressed = 0

    def restore(self,events,now):
        for event in events:
            if event.get('rule_id') and now-event['time']<=86400:
                self.states[(event['rule_id'],event['icao'],event['mode'])]={'active':False,'seen':event['time'],'fired':event['time']}
                if now-event['time']<60:
                    self.last_global.append(event['time'])

    def process(self, row, timestamp, mode, config, station):
        if not config['alerts_enabled']:
            return []
        result = []
        for rule in config['rules']:
            if not rule['enabled'] or (mode == 'playback' and not rule['include_playback']):
                continue
            key = (rule['id'], row['icao'], mode)
            previous = self.states.pop(key, {'active': False, 'seen': 0, 'fired': -math.inf})
            matched = matches(row, rule['filter'], station)
            entering = matched and (not previous['active'] or timestamp - previous['seen'] > 120)
            previous.update(active=matched, seen=timestamp)
            if entering and timestamp - previous['fired'] >= rule['cooldown_seconds']:
                while self.last_global and timestamp - self.last_global[0] >= 60:
                    self.last_global.popleft()
                # Rate limiting is explicit; a busy rule cannot swamp the desktop or SMTP.
                previous['fired'] = timestamp
                if len(self.last_global) >= 20:
                    self.suppressed += 1
                else:
                    self.last_global.append(timestamp)
                    title, body = alert_content(rule, row, timestamp, mode)
                    result.append({'id': uuid.uuid4().hex, 'time': timestamp, 'rule': rule['name'],
                        'rule_id':rule['id'],
                        'icao': row['icao'], 'callsign': row.get('callsign', ''), 'mode': mode,
                        'title': title, 'body': body, 'channels': list(rule['channels']),
                        'quiet': quiet_now(config, timestamp), 'delivery': 'Quiet hours: saved in app' if quiet_now(config, timestamp) else 'Saved in app'})
            self.states[key] = previous
        while len(self.states) > 20000:
            self.states.popitem(last=False)
        return result

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sightings (
 bucket INTEGER, mode TEXT, icao TEXT, messages INTEGER NOT NULL, positioned INTEGER NOT NULL,
 max_alt REAL, max_speed REAL, min_distance REAL, PRIMARY KEY(bucket,mode,icao));
CREATE TABLE IF NOT EXISTS sources(bucket INTEGER,mode TEXT,source TEXT,messages INTEGER,
 PRIMARY KEY(bucket,mode,source));
CREATE TABLE IF NOT EXISTS encounters(
 id TEXT PRIMARY KEY,icao TEXT,mode TEXT,first REAL,last REAL,callsign TEXT,messages INTEGER,
 max_alt REAL,max_speed REAL,min_distance REAL,source TEXT);
CREATE INDEX IF NOT EXISTS encounter_lookup ON encounters(icao,mode,last);
CREATE INDEX IF NOT EXISTS encounter_time ON encounters(last);
CREATE TABLE IF NOT EXISTS callsign_history(mode TEXT, callsign TEXT, last REAL NOT NULL,
 estimated INTEGER NOT NULL, PRIMARY KEY(mode,callsign));
CREATE INDEX IF NOT EXISTS callsign_history_time ON callsign_history(last);
CREATE TABLE IF NOT EXISTS samples(t REAL,mode TEXT,icao TEXT,lat REAL,lon REAL,alt REAL,speed REAL,track REAL);
CREATE INDEX IF NOT EXISTS sample_time ON samples(t);
CREATE INDEX IF NOT EXISTS sample_aircraft ON samples(icao,mode,t);
CREATE TABLE IF NOT EXISTS coverage(bucket INTEGER,mode TEXT,sector INTEGER,distance REAL,
 PRIMARY KEY(bucket,mode,sector));
CREATE TABLE IF NOT EXISTS airport_hits(encounter TEXT,code TEXT,name TEXT,radius REAL,first REAL,last REAL,distance REAL,
 PRIMARY KEY(encounter,code));
CREATE INDEX IF NOT EXISTS airport_time ON airport_hits(last);
CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,t REAL,icao TEXT,mode TEXT,event TEXT);
CREATE INDEX IF NOT EXISTS alert_time ON alerts(t);
CREATE TABLE IF NOT EXISTS runtime(bucket INTEGER,mode TEXT,frames INTEGER,rejected INTEGER,seconds REAL,
 PRIMARY KEY(bucket,mode));
CREATE TABLE IF NOT EXISTS uploads(t INTEGER PRIMARY KEY,aircraft INTEGER);
'''

class HistoryStore:
    def __init__(self, directory):
        self.path = Path(directory) / 'airspace.sqlite3'
        self.lock = threading.RLock()

    def connect(self, config):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=3)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA auto_vacuum=FULL')
        connection.execute('PRAGMA journal_mode=DELETE')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.executescript(SCHEMA)
        if connection.execute('PRAGMA user_version').fetchone()[0] < 2:
            with connection:
                connection.execute('''INSERT OR IGNORE INTO callsign_history
                    SELECT mode,UPPER(TRIM(callsign)),MAX(last),1 FROM encounters
                    WHERE TRIM(callsign)!='' GROUP BY mode,UPPER(TRIM(callsign))''')
                connection.execute('PRAGMA user_version=2')
        # Reserve half of the selected history budget for the rollback journal.
        # Each transaction is bounded and no disk-based VACUUM copy is created.
        pages = max(256, (config['storage_mb'] * 1024 * 1024 // 2 - 524288) // 4096)
        self.prune(connection, config, time.time(), target_pages=pages)
        connection.execute(f'PRAGMA max_page_count={pages}')
        return connection

    @staticmethod
    def prune(connection, config, timestamp, target_pages=None):
        cutoff = timestamp - config['retention_days'] * 86400
        def remove_before(stamp):
            for table, field in [('sightings', 'bucket'), ('sources', 'bucket'), ('encounters', 'last'),
                                 ('samples', 't'), ('coverage', 'bucket'), ('airport_hits', 'last'),
                                 ('alerts', 't'), ('runtime', 'bucket'), ('uploads', 't'), ('callsign_history','last')]:
                connection.execute(f'DELETE FROM {table} WHERE {field} < ?', (stamp,))
            connection.commit()
        remove_before(cutoff)
        if target_pages is None:
            return
        # Prune oldest slices until the database fits, preserving the newest data.
        for _ in range(1000):
            used = connection.execute('PRAGMA page_count').fetchone()[0] - connection.execute('PRAGMA freelist_count').fetchone()[0]
            if used < target_pages * .90:
                break
            times = [r[0] for r in connection.execute('''SELECT MIN(bucket) FROM sightings UNION ALL
                SELECT MIN(t) FROM samples UNION ALL SELECT MIN(t) FROM alerts UNION ALL
                SELECT MIN(last) FROM encounters UNION ALL SELECT MIN(bucket) FROM runtime
                UNION ALL SELECT MIN(bucket) FROM coverage UNION ALL SELECT MIN(bucket) FROM sources
                UNION ALL SELECT MIN(last) FROM airport_hits UNION ALL SELECT MIN(t) FROM uploads
                UNION ALL SELECT MIN(last) FROM callsign_history''') if r[0] is not None]
            if not times:
                break
            remove_before(min(times) + 3600)

    def write(self, observations, events, config, station, runtime=None, upload=None):
        if not config['history_enabled'] and not events:
            return
        with self.lock:
            connection = self.connect(config)
            try:
                if config['history_enabled']:
                    for offset in range(0,len(observations),50):
                        chunk=observations[offset:offset+50]
                        for attempt in range(2):
                            try:
                                self._observations(connection,chunk,config,station)
                                connection.commit()
                                break
                            except sqlite3.OperationalError as error:
                                connection.rollback()
                                if getattr(error,'sqlite_errorcode',None)!=sqlite3.SQLITE_FULL or attempt:
                                    raise
                                used=connection.execute('PRAGMA page_count').fetchone()[0]
                                self.prune(connection,config,time.time(),target_pages=max(256,used//2))
                    if runtime:
                        timestamp, mode, frames, rejected, seconds = runtime
                        connection.execute('''INSERT INTO runtime VALUES(?,?,?,?,?) ON CONFLICT(bucket,mode) DO UPDATE SET
                            frames=frames+excluded.frames,rejected=rejected+excluded.rejected,seconds=seconds+excluded.seconds''',
                            (int(timestamp//60)*60, mode, frames, rejected, seconds))
                    if upload:
                        connection.execute('INSERT OR IGNORE INTO uploads VALUES(?,?)', upload)
                for event in events:
                    connection.execute('INSERT OR REPLACE INTO alerts VALUES(?,?,?,?,?)',
                        (event['id'], event['time'], event['icao'], event['mode'], json.dumps(event)))
                connection.commit()
            finally:
                connection.close()

    @staticmethod
    def _observations(connection, observations, config, station):
        grouped = {}
        for timestamp, mode, row in observations:
            grouped.setdefault((mode, row['icao']), []).append((timestamp, row))
        segments=[]
        for key, entries in grouped.items():
            current=[]
            for entry in sorted(entries,key=lambda pair:pair[0]):
                if current and entry[0]-current[-1][0]>=1200:
                    segments.append((key,current));current=[]
                current.append(entry)
            if current:
                segments.append((key,current))
        for (mode, icao), entries in segments:
            latest = entries[-1][1]
            callsign = next((row['callsign'].strip().upper() for _, row in reversed(entries)
                             if (row.get('callsign') or '').strip()), '')
            receptions = {}
            for _, row in entries:
                name = (row.get('callsign') or '').strip().upper()
                if name and row.get('callsign_received') is not None:
                    receptions[name] = max(row['callsign_received'], receptions.get(name, -math.inf))
            connection.executemany('''INSERT INTO callsign_history VALUES(?,?,?,0)
                ON CONFLICT(mode,callsign) DO UPDATE SET last=excluded.last,estimated=0
                WHERE callsign_history.estimated=1 OR excluded.last>callsign_history.last''',
                [(mode, name, stamp) for name, stamp in receptions.items()])
            first, last = entries[0][0], entries[-1][0]
            encounter = connection.execute('SELECT id,first,last FROM encounters WHERE icao=? AND mode=? ORDER BY last DESC LIMIT 1', (icao, mode)).fetchone()
            encounter_id = encounter['id'] if encounter and first - encounter['last'] < 1200 else uuid.uuid4().hex
            altitudes = [r['altitude'] for _, r in entries if r.get('altitude') is not None]
            speeds = [r['groundspeed'] for _, r in entries if r.get('groundspeed') is not None]
            distances = [distance_nm(*station, r['latitude'], r['longitude']) for _, r in entries
                         if station and station[0] is not None and r.get('latitude') is not None and r.get('longitude') is not None]
            max_alt, max_speed, min_dist = max(altitudes, default=None), max(speeds, default=None), min(distances, default=None)
            connection.execute('''INSERT INTO encounters VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                last=MAX(last,excluded.last),callsign=COALESCE(NULLIF(excluded.callsign,''),callsign),messages=messages+excluded.messages,
                max_alt=MAX(COALESCE(max_alt,excluded.max_alt),COALESCE(excluded.max_alt,max_alt)),
                max_speed=MAX(COALESCE(max_speed,excluded.max_speed),COALESCE(excluded.max_speed,max_speed)),
                min_distance=MIN(COALESCE(min_distance,excluded.min_distance),COALESCE(excluded.min_distance,min_distance)),source=excluded.source''',
                (encounter_id, icao, mode, first, last, callsign, len(entries), max_alt, max_speed, min_dist, latest.get('data_source', 'Unknown')))
            last_sample = connection.execute('SELECT MAX(t) FROM samples WHERE icao=? AND mode=?', (icao, mode)).fetchone()[0] or -math.inf
            for timestamp, row in entries:
                bucket = int(timestamp//60)*60
                position = row.get('latitude') is not None and row.get('longitude') is not None
                distance = distance_nm(*station, row['latitude'], row['longitude']) if position and station and station[0] is not None else None
                connection.execute('''INSERT INTO sightings VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(bucket,mode,icao) DO UPDATE SET
                    messages=messages+1,positioned=positioned+excluded.positioned,
                    max_alt=MAX(COALESCE(max_alt,excluded.max_alt),COALESCE(excluded.max_alt,max_alt)),
                    max_speed=MAX(COALESCE(max_speed,excluded.max_speed),COALESCE(excluded.max_speed,max_speed)),
                    min_distance=MIN(COALESCE(min_distance,excluded.min_distance),COALESCE(excluded.min_distance,min_distance))''',
                    (bucket, mode, icao, 1, int(position), row.get('altitude'), row.get('groundspeed'), distance))
                connection.execute('''INSERT INTO sources VALUES(?,?,?,1) ON CONFLICT(bucket,mode,source) DO UPDATE SET messages=messages+1''',
                    (bucket, mode, row.get('data_source', 'Unknown')))
                if timestamp-last_sample >= config['sample_seconds']:
                    connection.execute('INSERT INTO samples VALUES(?,?,?,?,?,?,?,?)',
                        (timestamp, mode, icao, row.get('latitude'), row.get('longitude'), row.get('altitude'), row.get('groundspeed'), row.get('track')))
                    last_sample = timestamp
                if distance is not None:
                    lat1, lat2 = math.radians(station[0]), math.radians(row['latitude'])
                    delta = math.radians(row['longitude']-station[1])
                    bearing = math.degrees(math.atan2(math.sin(delta)*math.cos(lat2), math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(delta))) % 360
                    sector = int((bearing+22.5)//45) % 8
                    connection.execute('''INSERT INTO coverage VALUES(?,?,?,?) ON CONFLICT(bucket,mode,sector) DO UPDATE SET distance=MAX(distance,excluded.distance)''', (bucket, mode, sector, distance))
                if position:
                    for airport in config['airports']:
                        away = distance_nm(airport['latitude'], airport['longitude'], row['latitude'], row['longitude'])
                        if away <= airport['radius_nm']:
                            connection.execute('''INSERT INTO airport_hits VALUES(?,?,?,?,?,?,?) ON CONFLICT(encounter,code) DO UPDATE SET
                                last=MAX(last,excluded.last),distance=MIN(distance,excluded.distance)''',
                                (encounter_id, airport['code'], airport['name'], airport['radius_nm'], timestamp, timestamp, away))

    def delivery(self, event, config):
        if not self.path.exists():
            return
        with self.lock:
            connection = self.connect(config)
            try:
                connection.execute('UPDATE alerts SET event=? WHERE id=?', (json.dumps(event), event['id']))
                connection.commit()
            finally:
                connection.close()

    def report(self, config, hours=24, mode='live', now=None):
        now = time.time() if now is None else now
        cutoff = now-hours*3600
        with self.lock:
            connection = self.connect(config)
            try:
                def rows(sql, args=()):
                    return [dict(r) for r in connection.execute(sql, args)]
                args = (int(cutoff//60)*60, mode)
                summary = dict(connection.execute('''SELECT COALESCE(SUM(messages),0) messages,COUNT(DISTINCT icao) aircraft,
                    COALESCE(SUM(positioned),0) positioned_reports,MAX(max_alt) max_alt,MAX(max_speed) max_speed,
                    MIN(min_distance) closest_nm FROM sightings WHERE bucket>=? AND mode=?''', args).fetchone())
                summary.update(dict(connection.execute('''SELECT COALESCE(SUM(frames),0) frames,COALESCE(SUM(rejected),0) rejected,
                    COALESCE(SUM(seconds),0) receiving_seconds FROM runtime WHERE bucket>=? AND mode=?''', args).fetchone()))
                encounters = rows('''WITH page AS (
                    SELECT * FROM encounters WHERE last>=? AND mode=? ORDER BY last DESC,id LIMIT 500
                ), totals AS (
                    SELECT aircraft.icao,COUNT(*) times_seen FROM (SELECT DISTINCT icao FROM page) aircraft
                    JOIN encounters history ON history.icao=aircraft.icao AND history.mode=? GROUP BY aircraft.icao
                )
                SELECT page.*,calls.last callsign_last_seen,calls.estimated callsign_last_seen_estimated,totals.times_seen
                FROM page JOIN totals USING(icao) LEFT JOIN callsign_history calls
                    ON calls.mode=page.mode AND calls.callsign=UPPER(TRIM(page.callsign))
                ORDER BY page.last DESC,page.id''', (cutoff, mode, mode))
                today = dt.date.fromtimestamp(now)
                for encounter in encounters:
                    stamp = encounter['callsign_last_seen']
                    encounter['days_ago'] = (today - dt.date.fromtimestamp(stamp)).days if stamp is not None else None
                    if stamp is not None:
                        encounter['callsign_last_seen'] = dt.datetime.fromtimestamp(stamp).astimezone().isoformat(timespec='seconds')
                summary['encounters'] = connection.execute('SELECT COUNT(*) FROM encounters WHERE last>=? AND mode=?', (cutoff, mode)).fetchone()[0]
                summary['upload_aircraft_reports'] = connection.execute('SELECT COALESCE(SUM(aircraft),0) FROM uploads WHERE t>=?', (cutoff,)).fetchone()[0] if mode == 'live' else 0
                chart_width = 60 if hours <= 1 else 3600 if hours <= 48 else 86400
                traffic = rows('''SELECT CAST(bucket / ? AS INTEGER)*? t,SUM(messages) messages,COUNT(DISTINCT icao) aircraft
                    FROM sightings WHERE bucket>=? AND mode=? GROUP BY t ORDER BY t''', (chart_width, chart_width, *args))
                sources = rows('SELECT source,SUM(messages) messages FROM sources WHERE bucket>=? AND mode=? GROUP BY source ORDER BY messages DESC', args)
                coverage = rows('SELECT sector,MAX(distance) distance FROM coverage WHERE bucket>=? AND mode=? GROUP BY sector ORDER BY sector', args)
                airports = rows('''SELECT code,name,radius,COUNT(*) encounters,MIN(distance) closest_nm FROM airport_hits
                    WHERE last>=? AND encounter IN (SELECT id FROM encounters WHERE mode=?) GROUP BY code,name,radius ORDER BY encounters DESC LIMIT 100''', (cutoff, mode))
                alerts = [json.loads(r[0]) for r in connection.execute("SELECT event FROM alerts WHERE t>=? AND (mode=? OR mode='test') ORDER BY t DESC LIMIT 200", (cutoff, mode))]
                oldest = connection.execute('SELECT MIN(bucket) FROM sightings WHERE mode=?', (mode,)).fetchone()[0]
                if traffic:
                    start=int((max(cutoff,oldest) if hours==87600 and oldest else cutoff)//chart_width)*chart_width
                    end=int(now//chart_width)*chart_width
                    if (end-start)//chart_width>120:
                        chart_width=math.ceil((end-start)/120/86400)*86400
                        traffic=rows('''SELECT CAST(bucket / ? AS INTEGER)*? t,SUM(messages) messages,COUNT(DISTINCT icao) aircraft
                            FROM sightings WHERE bucket>=? AND mode=? GROUP BY t ORDER BY t''',(chart_width,chart_width,*args))
                        start=int(start//chart_width)*chart_width
                        end=int(now//chart_width)*chart_width
                    indexed={point['t']:point for point in traffic}
                    traffic=[indexed.get(stamp,{'t':stamp,'messages':0,'aircraft':0}) for stamp in range(start,end+1,chart_width)]
                return dict(summary=summary, traffic=traffic, sources=sources, coverage=coverage,
                    encounters=encounters, airports=airports, alerts=alerts, oldest=oldest, hours=hours,
                    mode=mode, bucket_seconds=chart_width, storage_bytes=self.size(), storage_limit_bytes=config['storage_mb']*1024*1024,
                    retention_days=config['retention_days'], history_enabled=config['history_enabled'], generated=now)
            finally:
                connection.close()

    def size(self):
        return sum(p.stat().st_size for p in (self.path, Path(str(self.path)+'-journal'), Path(str(self.path)+'-wal'), Path(str(self.path)+'-shm')) if p.exists())

    def recent_alerts(self,config):
        if not self.path.exists():
            return []
        with self.lock:
            connection=self.connect(config)
            try:
                return [json.loads(r[0]) for r in reversed(connection.execute('SELECT event FROM alerts WHERE t>=? ORDER BY t DESC LIMIT 20000',(time.time()-86400,)).fetchall())]
            finally:
                connection.close()

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None

def deliver_event(event, config, secrets):
    """Explicit opt-in channels. Verified TLS, timeouts, no redirects or retries."""
    results = []
    delivery = config['delivery']
    if 'email' in event['channels']:
        if not delivery['email_enabled']:
            results.append('Email off')
        else:
            try:
                message = email.message.EmailMessage()
                message['Subject'] = event['title']
                message['From'], message['To'] = delivery['smtp_from'], delivery['email_to']
                message.set_content(event['body'])
                context = ssl.create_default_context()
                if delivery['smtp_tls'] == 'ssl':
                    client = smtplib.SMTP_SSL(delivery['smtp_host'], delivery['smtp_port'], timeout=8, context=context)
                else:
                    client = smtplib.SMTP(delivery['smtp_host'], delivery['smtp_port'], timeout=8)
                with client:
                    if delivery['smtp_tls'] == 'starttls':
                        client.ehlo(); client.starttls(context=context); client.ehlo()
                    if delivery['smtp_user']:
                        client.login(delivery['smtp_user'], secrets.get('smtp_password', ''))
                    client.send_message(message)
                results.append('Email accepted by SMTP server')
            except Exception:
                # Exceptions can contain password-bearing URLs and server replies.
                results.append('Email failed: check credentials, TLS and server settings')
    if 'webhook' in event['channels']:
        if not delivery['webhook_enabled']:
            results.append('Webhook off')
        else:
            try:
                url = validate_endpoint(secrets.get('webhook_url', ''))
                headers = {'User-Agent': 'FlightRadar24-Community/0.3.0'}
                if secrets.get('webhook_token'):
                    headers['Authorization'] = 'Bearer ' + secrets['webhook_token']
                kind = delivery['webhook_kind']
                if kind == 'ntfy':
                    body = (event['title']+'\n\n'+event['body']).encode('utf-8')
                    headers['Content-Type'] = 'text/plain; charset=utf-8'
                else:
                    payload = {'content': (event['title']+'\n'+event['body'])[:1900], 'allowed_mentions': {'parse': []}} if kind == 'discord' else {
                        'title': event['title'], 'message': event['body'], 'icao': event['icao'], 'time': event['time']}
                    body = json.dumps(payload).encode('utf-8')
                    headers['Content-Type'] = 'application/json'
                request = urllib.request.Request(url, data=body, headers=headers, method='POST')
                with urllib.request.build_opener(_NoRedirect()).open(request, timeout=8) as response:
                    if not 200 <= response.status < 300:
                        raise OSError('Delivery rejected')
                results.append('Webhook accepted by server')
            except Exception:
                results.append('Webhook failed: check URL, token and service limits')
    return '; '.join(results)

class ActivityService:
    def __init__(self, directory, runtime_status=None):
        self.settings = FeatureSettings(directory)
        self.store = HistoryStore(directory)
        self.engine = AlertEngine()
        self.queue = queue.Queue(maxsize=10000)
        self.deliveries = queue.Queue(maxsize=50)
        self.lock = threading.RLock()
        self.events = collections.deque(maxlen=200)
        self.dropped = self.delivery_dropped = 0
        self.error = ''
        self.stop_event = threading.Event()
        self.thread = self.delivery_thread = None
        self.station = None
        self.mode = 'live'
        self.runtime_status = runtime_status
        self._runtime_previous = (0, 0)
        self._last_tick = time.monotonic()

    def start(self, receiver_config):
        self.stop()
        self.mode = 'playback' if receiver_config['receiver'] == 'iq-file' else 'live'
        self.station = (receiver_config['latitude'], receiver_config['longitude'])
        self._runtime_previous = (0, 0)
        self._last_tick = time.monotonic()
        self.stop_event.clear()
        self.engine=AlertEngine()
        try:
            self.engine.restore(self.store.recent_alerts(self.settings.value),time.time())
        except (OSError,sqlite3.Error,ValueError):
            self.error='Could not restore retained alert cooldowns.'
        self.thread = threading.Thread(target=self._run, name='airspace-history', daemon=True)
        self.delivery_thread = threading.Thread(target=self._deliver, name='airspace-alert-delivery', daemon=True)
        self.thread.start(); self.delivery_thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(8)
            if self.thread.is_alive():
                raise RuntimeError('History worker is still finishing; try stopping again.')
        while True:
            try:
                event,config=self.deliveries.get_nowait()
            except queue.Empty:
                break
            with self.lock:
                event['delivery']='External delivery cancelled when the receiver stopped'
            try:
                self.store.delivery(event,config)
            except (OSError,sqlite3.Error):
                pass
        if self.delivery_thread and self.delivery_thread is not threading.current_thread():
            self.delivery_thread.join(20)
            if self.delivery_thread.is_alive():
                raise RuntimeError('Alert delivery is still finishing; try stopping again.')

    def submit(self, row, timestamp):
        observation = {key: row[key] for key in OBSERVATION_KEYS if key in row}
        try:
            self.queue.put_nowait((timestamp, self.mode, observation))
        except queue.Full:
            self.dropped += 1

    def _run(self):
        while not self.stop_event.is_set() or not self.queue.empty():
            batch = []
            deadline = time.monotonic()+.5
            while len(batch) < 500 and time.monotonic()<deadline:
                try:
                    batch.append(self.queue.get(timeout=max(.001,deadline-time.monotonic())))
                except queue.Empty:
                    break
            events = []
            with self.lock:
                config = copy.deepcopy(self.settings.value)
                for stamp, mode, row in batch:
                    events.extend(self.engine.process(row, stamp, mode, config, self.station))
            runtime, upload = None, None
            if self.runtime_status:
                frames, rejected, sent_time, sent_count = self.runtime_status()
                tick = time.monotonic()
                runtime = (time.time(), self.mode, max(0, frames-self._runtime_previous[0]), max(0, rejected-self._runtime_previous[1]), min(5, tick-self._last_tick))
                self._runtime_previous, self._last_tick = (frames, rejected), tick
                if self.mode == 'live' and sent_time > 0 and sent_count >= 0:
                    upload = (sent_time, sent_count)
            try:
                self.store.write(batch, events, config, self.station, runtime, upload)
                self.error = ''
            except (OSError, sqlite3.Error):
                self.dropped += len(batch)
                self.error = 'History storage unavailable or full. Reception continues; check disk space and retention settings.'
            with self.lock:
                self.events.extend(events)
            for event in events:
                if event['quiet'] or self.stop_event.is_set() or not any(c in event['channels'] for c in ('email', 'webhook')):
                    continue
                try:
                    self.deliveries.put_nowait((event, config))
                except queue.Full:
                    self.delivery_dropped += 1
                    event['delivery'] = 'Delivery queue full; saved in app'

    def _deliver(self):
        while not self.stop_event.is_set():
            try:
                event, config = self.deliveries.get(timeout=.3)
            except queue.Empty:
                continue
            try:
                with self.lock:
                    config=copy.deepcopy(self.settings.value)
                    secrets=self.settings.secrets()
                    rule=next((r for r in config['rules'] if r['id']==event.get('rule_id')),None)
                if not config['alerts_enabled'] or not rule or not rule['enabled']:
                    result='External delivery cancelled: alert rule is off'
                else:
                    outgoing={**event,'channels':[c for c in event['channels'] if c in rule['channels']]}
                    result = deliver_event(outgoing, config, secrets)
                with self.lock:
                    event['delivery'] += '; ' + result
                self.store.delivery(event, config)
            except (OSError, ValueError, sqlite3.Error):
                with self.lock:
                    event['delivery'] = 'Delivery failed; check notification settings'

    def snapshot(self):
        with self.lock:
            return {'events': copy.deepcopy(list(self.events)), 'dropped_reports': self.dropped,
                    'suppressed_alerts': self.engine.suppressed, 'delivery_dropped': self.delivery_dropped, 'error': self.error}

    def save(self, config, secrets=None):
        with self.lock:
            old_rules={r['id']:r for r in self.settings.value['rules']}
            self.settings.save(config, secrets)
            new_rules={r['id']:r for r in self.settings.value['rules']}
            for key in list(self.engine.states):
                if key[0] not in new_rules:
                    self.engine.states.pop(key,None)
                elif old_rules.get(key[0])!=new_rules[key[0]]:
                    self.engine.states[key]['active']=False
        # Apply reduced size/retention immediately, even while reception is stopped.
        with self.store.lock:
            connection = self.store.connect(self.settings.value)
            connection.close()

    def test_external(self):
        config=copy.deepcopy(self.settings.value)
        channels=[key for key,enabled in [('email',config['delivery']['email_enabled']),('webhook',config['delivery']['webhook_enabled'])] if enabled]
        if not channels:
            return {'delivery':'No external delivery channel is enabled. Configure and save email or webhook settings first.'}
        event={'id':uuid.uuid4().hex,'time':time.time(),'rule':'TEST','icao':'000000','callsign':'TEST',
            'mode':'test','title':'FlightRadar24 · TEST notification',
            'body':'TEST ONLY. Synthetic aircraft TEST (ICAO 000000). This checks your configured alert delivery; it is not live aircraft data.',
            'channels':channels,'quiet':False,'delivery':'Test requested'}
        result=deliver_event(event,config,self.settings.secrets())
        event['delivery']=result
        self.store.write([], [event],config,self.station)
        with self.lock:
            self.events.append(event)
        return {'delivery':result}
