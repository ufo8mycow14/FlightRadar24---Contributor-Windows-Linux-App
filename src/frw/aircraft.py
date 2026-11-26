from __future__ import annotations
import collections, datetime, math, re, threading, time
from pyModeS import PipeDecoder

# Only decoded observations are retained. In particular, no registration, aircraft
# model, route, or airline is inferred from an address or callsign.
DETAIL_FIELDS = frozenset('''callsign groundspeed track heading magnetic_heading true_track
    indicated_airspeed true_airspeed mach baro_vertical_rate gnss_vertical_rate
    inertial_vertical_rate geo_minus_baro squawk category wake_vortex on_ground
    flight_status flight_status_text capability capability_text vertical_status
    sensitivity_level reply_information cross_link_capability surveillance_status
    nic_b nuc_p nac_v selected_altitude selected_altitude_source selected_altitude_mcp
    selected_altitude_fms baro_pressure_setting selected_heading target_altitude_source
    autopilot vnav_mode altitude_hold_mode approach_mode lnav_mode tcas_operational
    roll track_rate emergency_state version capability_class operational_mode
    nic_supplement_a nac_p sil sil_supplement nic_baro hrd acas_operational
    acas_resolution_advisory threat_type_indicator issued_ra corrective downward_sense
    increased_rate sense_reversal altitude_crossing positive no_above no_below no_left
    no_right ra_terminated multiple_threat threat_icao threat_altitude threat_range
    threat_bearing supported_bds config overlay_command_capability mode_s_subnetwork_version
    transponder_level5 uplink_elm_throughput downlink_elm_throughput
    aircraft_identification_capability squitter_capability surveillance_identifier_code
    common_usage_gicb_capability mode_s_specific_services acas_hybrid_surveillance
    acas_rtca_version dte_status downlink_request utility_message movement track_status gva sda
    ident_active single_antenna signal_dbfs corrected_bits sbs_alert sbs_emergency sbs_ident
    wind_speed wind_direction static_air_temperature static_pressure humidity turbulence
    figure_of_merit wind_shear microburst icing radio_height wake_vortex_hazard
    cpr_format cpr_lat cpr_lon mv ra_active atc_services adsb_1090_in adsb_uat_in
    nic_c surface_dimensions_code antenna_offset_code'''.split())
IDENTITY_FIELDS = frozenset('''callsign category category_typecode wake_vortex version
    capability capability_text capability_class hrd supported_bds config
    overlay_command_capability mode_s_subnetwork_version transponder_level5
    uplink_elm_throughput downlink_elm_throughput aircraft_identification_capability
    squitter_capability surveillance_identifier_code common_usage_gicb_capability
    mode_s_specific_services acas_hybrid_surveillance acas_rtca_version
    data_source df typecode subtype bds bds_candidates'''.split())
HEADER_FIELDS = frozenset(('altitude','squawk','flight_status','flight_status_text',
                           'df','icao','crc_valid','icao_verified','bds_candidates','downlink_request','utility_message',
                           'signal_dbfs','corrected_bits'))
HISTORY_FIELDS = frozenset(('altitude_baro','altitude_gnss','altitude_unspecified',
                            'groundspeed','indicated_airspeed','true_airspeed','mach'))
FIELD_TTL = 60
IDENTITY_TTL = 300
HISTORY_TTL = 600
HISTORY_LIMIT = 300

def _fresh(row,key,now):
    ttl=IDENTITY_TTL if key in IDENTITY_FIELDS else FIELD_TTL
    return key in row and now-row['field_times'].get(key,0)<=ttl

def distance_nm(lat1,lon1,lat2,lon2):
    a,b=math.radians(lat1),math.radians(lat2);dl=math.radians(lon2-lon1)
    h=math.sin((b-a)/2)**2+math.cos(a)*math.cos(b)*math.sin(dl/2)**2
    return 3440.065*2*math.asin(math.sqrt(min(1,max(0,h))))

class Aircraft:
    def __init__(self,config,observer=None):
        self.observer=observer
        self.config=config;self.lock=threading.RLock();self.rows={};self.total=0;self.rejected=0;self.last=None
        self.events=collections.deque(maxlen=20000);self.trusted={}
        ref=None if config['latitude'] is None else (config['latitude'],config['longitude'])
        self.decoder=PipeDecoder(surface_ref=ref,include_meteo=config.get('meteo',False),eviction_ttl=max(300,config['aircraft_ttl']))
    def frame(self,frame,now=None):
        now=time.time() if now is None else now
        if len(frame.data) not in (7,14):return None
        with self.lock:
            try:d=dict(self.decoder.decode(frame.data.hex().upper(),timestamp=now))
            except (ValueError,KeyError,IndexError):self.rejected+=1;return None
            icao=d.get('icao')
            if not icao or d.get('error') or d.get('crc_valid') is False:
                self.rejected+=1;return None
            if d.get('crc_valid') is True:self.trusted[icao]=now
            elif now-self.trusted.get(icao,0)>120:
                self.rejected+=1;return None
            if frame.signal:
                d['signal_dbfs'] = round(20*math.log10(frame.signal/255),1)
            if frame.corrected is not None:
                d['corrected_bits'] = frame.corrected
            if d.get('df') in (20,21):
                d['downlink_request'] = frame.data[1] >> 3
                d['utility_message'] = ((frame.data[1]&7)<<3) | (frame.data[2]>>5)
                status = frame.data[0]&7
                d['flight_status'] = status
                d['flight_status_text'] = ('No alert, airborne','No alert, on ground','Alert, airborne',
                    'Alert, on ground','Alert and IDENT','IDENT','Reserved','Reserved')[status]
            if d.get('df') in (17,18) and d.get('typecode')==31 and d.get('version')==2 and d.get('subtype') in (0,1):
                mode = d['operational_mode']
                if mode >> 14 == 0:
                    d.update(sda=(mode>>8)&3,ident_active=bool(mode&0x1000),single_antenna=bool(mode&0x400),
                             ra_active=bool(mode&0x2000),atc_services=bool(mode&0x0800))
                capability = d['capability_class']
                if capability >> 14 == 0:
                    d['adsb_1090_in'] = bool(capability & 0x1000)
                    d['adsb_uat_in'] = bool(capability & (0x20 if d['subtype']==0 else 0x100))
                    if d['subtype']==1:
                        d.update(nac_v=(capability>>5)&7,nic_c=(capability>>4)&1,
                                 surface_dimensions_code=capability&15,antenna_offset_code=mode&255)
                if d['subtype']==0:
                    d['gva'] = (frame.data[10]>>6)&3
            return self._update(icao,d,now)
    def _update(self,icao,d,now):
        if not re.fullmatch('[A-Fa-f0-9]{6}',icao):return None
        if len(self.rows)>10000:self.prune(now)
        if len(self.rows)>10000 and icao not in self.rows:return None
        self.total+=1;self.last=now;self.events.append(now)
        r=self.rows.setdefault(icao,{'icao':icao,'messages':0,'first_seen':now,'trail':[],
                                     'field_times':{},'field_sources':{},'_field_updates':{},'_history':[]})
        r.update(last_seen=max(now,r.get('last_seen',now)),messages=r['messages']+1)
        df,tc=d.get('df'),d.get('typecode',0)
        source=('ADS-B' if df==17 else 'Extended squitter (DF18)' if df==18 else
                'Mode S / Comm-B' if df in (20,21) else 'Mode S' if df is not None else 'SBS/BaseStation input')
        provenance=source+(f' TC{tc}' if df in (17,18) else f' DF{df}' if df is not None else '')
        if d.get('bds') and df in (20,21):provenance+=f" BDS {d['bds']}"
        if df in (20,21) and d.get('bds') in ('4,4','4,5'):provenance+=' (inferred weather)'
        updates={}
        def put(key,value,origin=None):
            if origin is None:origin=provenance
            if now<r['_field_updates'].get(key,-math.inf):return
            if value is None:
                r['_field_updates'][key]=now
                r.pop(key,None)
                return
            if isinstance(value,float) and not math.isfinite(value):return
            if not isinstance(value,(str,int,float,bool,list)):return
            r['_field_updates'][key]=now
            r[key]=list(value) if isinstance(value,list) else value
            r['field_times'][key]=now;r['field_sources'][key]=origin
            updates[key]=value
        put('data_source',source,source)
        for key in ('df','typecode','subtype'):
            if key in d:put(key,d[key])
        # pyModeS also decodes the first candidate when BDS inference is ambiguous.
        # Those payload values are not observations we can safely identify.
        if len(d.get('bds_candidates') or [])>1:
            d={key:value for key,value in d.items() if key in HEADER_FIELDS}
            r.pop('bds',None)
            provenance=f'{source} DF{df} header'
        elif df in (20,21):
            r.pop('bds_candidates',None)
            if d.get('bds') == '4,5' and 'wake_vortex' in d:
                d['wake_vortex_hazard'] = d.pop('wake_vortex')
        for key in ('bds','bds_candidates'):
            if key in d:put(key,d[key],source)
        # Reserved ADS-B formats must not be interpreted using a different layout.
        if ((tc==19 and d.get('subtype') not in (1,2,3,4)) or
            (tc==29 and d.get('subtype')!=1) or
            (tc==31 and (d.get('subtype') not in (0,1) or d.get('version') not in (0,1,2)
                        or (d.get('version')==0 and d.get('subtype')==1)))):
            d={}
        for key in DETAIL_FIELDS:
            if key in d:put(key,d[key])
        if 'category' in d and 1<=tc<=4:put('category_typecode',tc)
        if 'altitude' in d:
            key=('altitude_gnss' if df in (17,18) and 20<=tc<=22 else
                 'altitude_baro' if df in (0,4,16,20) or df in (17,18) and 9<=tc<=18 else
                 'altitude_unspecified')
            put(key,d['altitude'])
        if 'airspeed' in d and d.get('airspeed_type') in ('IAS','TAS'):
            put('indicated_airspeed' if d['airspeed_type']=='IAS' else 'true_airspeed',d['airspeed'])
        if 'vertical_rate' in d:
            key={'BARO':'baro_vertical_rate','GNSS':'gnss_vertical_rate'}.get(d.get('vr_source'),'vertical_rate_unspecified')
            put(key,d['vertical_rate'])
        if df in (17,18) and d:
            if 5<=tc<=8:put('on_ground',True)
            elif 9<=tc<=22:put('on_ground',False)
        elif d.get('flight_status') in (0,1,2,3):put('on_ground',d['flight_status'] in (1,3))
        self._history(r,updates,now)
        valid_position=False
        if d.get('latitude') is not None and d.get('longitude') is not None:
            lat,lon=d['latitude'],d['longitude']
            valid_position=(-90<=lat<=90 and -180<=lon<=180)
            if valid_position and self.config['latitude'] is not None:
                dist=distance_nm(self.config['latitude'],self.config['longitude'],lat,lon)
                valid_position=dist<=self.config['range_nm']
                if valid_position:put('distance_nm',round(dist,1))
            if valid_position and now>=r.get('position_time',0):
                put('latitude',lat);put('longitude',lon);r['position_time']=now
                if not r['trail'] or now-r['trail'][-1][2]>2:r['trail']=(r['trail']+[[lat,lon,now]])[-120:]
        kind=1 if 'callsign' in d else 4 if tc==19 and d else 2 if 5<=tc<=8 and d else 3 if 'latitude' in updates else 5 if 'altitude' in d else 6 if 'squawk' in d else 8
        self._aliases(r,now)
        if self.observer:
            # Copy fresh scalar observations only; disk and network work belongs
            # to the bounded activity worker, never the radio decoder thread.
            observed={key:val for key,val in r.items() if key in ('icao','altitude_source','vr_source') or
                      key in r['field_times'] and _fresh(r,key,now) and isinstance(val,(str,int,float,bool))}
            observed['field_ages']={key:round(max(0,now-stamp),1) for key,stamp in r['field_times'].items() if key in observed}
            if updates.get('callsign'):
                observed['callsign_received'] = now
            self.observer(observed,now)
        sbs_row={'icao':icao}
        for key in ('callsign','groundspeed','track','squawk','on_ground','latitude','longitude'):
            if key in updates:sbs_row[key]=updates[key]
        for key in ('altitude_baro','altitude_gnss','altitude_unspecified'):
            if key in updates:sbs_row['altitude']=updates[key];break
        for key in ('baro_vertical_rate','gnss_vertical_rate','inertial_vertical_rate','vertical_rate_unspecified'):
            if key in updates:sbs_row['vertical_rate']=updates[key];break
        return self.sbs(sbs_row,kind,now)

    @staticmethod
    def _aliases(r,now):
        def alias(target,candidates,source_key=None):
            selected=next((key for key in candidates if _fresh(r,key,now)),None)
            if selected is None:
                r.pop(target,None)
                if source_key:r.pop(source_key,None)
                return
            r[target]=r[selected]
            r['field_times'][target]=r['field_times'][selected]
            r['field_sources'][target]=r['field_sources'][selected]
            if source_key:
                r[source_key]={'altitude_baro':'BARO','altitude_gnss':'GNSS',
                    'altitude_unspecified':'SBS unspecified','baro_vertical_rate':'BARO',
                    'gnss_vertical_rate':'GNSS','inertial_vertical_rate':'INERTIAL',
                    'vertical_rate_unspecified':'SBS unspecified'}[selected]
                r['field_times'][source_key]=r['field_times'][selected]
                r['field_sources'][source_key]=r['field_sources'][selected]
        alias('altitude',('altitude_baro','altitude_gnss','altitude_unspecified'),'altitude_source')
        rates=sorted(('baro_vertical_rate','gnss_vertical_rate','inertial_vertical_rate','vertical_rate_unspecified'),
                     key=lambda key:r['field_times'].get(key,-math.inf),reverse=True)
        alias('vertical_rate',rates,'vr_source')

    @staticmethod
    def _history(r,updates,now):
        values={key:value for key,value in updates.items() if key in HISTORY_FIELDS}
        history=r['_history']
        if values:
            # One sparse sample per two-second interval. Fields retain their exact
            # observation times; nothing is carried forward from another interval.
            bucket=math.floor(now/2)
            sample=next((point for point in reversed(history) if math.floor(point['time']/2)==bucket),None)
            if sample is None:
                sample={'time':now,'field_times':{}};history.append(sample)
            sample['time']=min(now,sample['time'])
            for key,value in values.items():
                if now>=sample['field_times'].get(key,-math.inf):
                    sample[key]=value;sample['field_times'][key]=now
            if 'altitude_unspecified' in values:
                sample['altitude']=sample['altitude_unspecified']
                sample['field_times']['altitude']=sample['field_times']['altitude_unspecified']
            history.sort(key=lambda point:point['time'])
        cutoff=r['last_seen']-HISTORY_TTL
        r['_history']=[point for point in history if point['time']>=cutoff][-HISTORY_LIMIT:]

    def _view(self,value,now,history=False):
        self._aliases(value,now)
        r={key:val for key,val in value.items() if not key.startswith('_')}
        r['field_times']=dict(value['field_times']);r['field_sources']=dict(value['field_sources'])
        r['field_ages']={key:round(max(0,now-stamp),1) for key,stamp in value['field_times'].items()}
        for key in value['field_times']:
            if not _fresh(value,key,now):r.pop(key,None)
            elif isinstance(r.get(key),list):r[key]=list(r[key])
        r['seen']=round(max(0,now-r['last_seen']),1)
        r['trail']=[list(point) for point in value['trail'] if now-point[2]<=HISTORY_TTL]
        if history:
            r['history']=[{**point,'field_times':dict(point['field_times'])}
                          for point in value['_history'] if now-point['time']<=HISTORY_TTL]
        return r

    def detail(self,icao):
        now=time.time()
        with self.lock:
            self.prune(now)
            value=self.rows.get(icao.upper())
            return self._view(value,now,history=True) if value is not None else None
    def ingest_sbs(self,line,now=None):
        fields=line.strip().split(',')
        if len(fields)<22 or fields[0]!='MSG' or not re.fullmatch('[A-Fa-f0-9]{6}',fields[4]):return
        d={};mapping={10:'callsign',11:'altitude',12:'groundspeed',13:'track',14:'latitude',15:'longitude',16:'vertical_rate',17:'squawk'}
        try:
            for i,k in mapping.items():
                if fields[i]:d[k]=fields[i] if k in ('callsign','squawk') else float(fields[i])
            if any(isinstance(v,float) and not math.isfinite(v) for v in d.values()):return
            if fields[21]:d['on_ground']=fields[21]=='-1'
            for index,key in ((18,'sbs_alert'),(19,'sbs_emergency'),(20,'sbs_ident')):
                if fields[index] in ('-1','0','1'):d[key]=fields[index]!='0'
        except ValueError:return
        with self.lock:self._update(fields[4].upper(),d,time.time() if now is None else now)
    @staticmethod
    def sbs(r,kind,now):
        dt=datetime.datetime.fromtimestamp(now);date=dt.strftime('%Y/%m/%d');clock=dt.strftime('%H:%M:%S.%f')[:12]
        def field(key):
            v=r.get(key)
            if v is None:return ''
            if isinstance(v,float):return str(round(v,6))
            return str(v).replace(',','').replace('\r','').replace('\n','')
        values=['MSG',str(kind),'1','1',r['icao'],'1',date,clock,date,clock,
                field('callsign') if kind==1 else '',field('altitude') if kind in (2,3,5,6) else '',
                field('groundspeed') if kind==4 else '',field('track') if kind==4 else '',
                field('latitude') if kind in (2,3) else '',field('longitude') if kind in (2,3) else '',
                field('vertical_rate') if kind==4 else '',field('squawk') if kind==6 else '',
                '', '', '', ('-1' if r['on_ground'] else '0') if 'on_ground' in r else '']
        return (','.join(values)+'\r\n').encode('ascii',errors='replace')
    def prune(self,now):
        self.rows={k:v for k,v in self.rows.items() if now-v['last_seen']<self.config['aircraft_ttl']}
        self.trusted={k:v for k,v in self.trusted.items() if now-v<120}
        for value in self.rows.values():
            for key in value['field_times']:
                if not _fresh(value,key,now):value.pop(key,None)
            value['_history']=[point for point in value['_history'] if now-point['time']<=HISTORY_TTL]
    def snapshot(self):
        now=time.time()
        with self.lock:
            self.prune(now)
            rows=[self._view(value,now) for value in self.rows.values()]
            return {'aircraft':sorted(rows,key=lambda r:r.get('callsign',r['icao'])),'messages':self.total,
                    'rejected':self.rejected,'messages_per_second':round(sum(t>now-10 for t in self.events)/10,1),
                    'last_message_age':round(now-self.last,1) if self.last else None}
