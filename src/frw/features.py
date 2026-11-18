"""Validated local display, alert and history preferences. No receiver secrets."""
from __future__ import annotations
import copy
import fnmatch
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from .aircraft import distance_nm
from .config import ConfigError, protect
from .display import DISPLAY_DEFAULT, validate_display

FILTER_DEFAULT = dict(callsigns='', icaos='', squawks='', altitude_min=None, altitude_max=None,
                      speed_min=None, speed_max=None, distance_max=None, source='any',
                      state='any', category='', positioned=False, emergency=False)
CHANNELS = ('in_app', 'sound', 'desktop', 'email', 'webhook')
FEATURE_DEFAULT = {
    'filter': copy.deepcopy(FILTER_DEFAULT), 'filter_enabled': False, 'presets': [],
    'alerts_enabled': False, 'rules': [], 'quiet_start': '', 'quiet_end': '',
    'history_enabled': True, 'retention_days': 30, 'storage_mb': 256,
    'sample_seconds': 15, 'log_max_mb': 20, 'log_days': 3, 'airports': [],
    'display': copy.deepcopy(DISPLAY_DEFAULT),
    'delivery': {'email_enabled': False, 'smtp_host': '', 'smtp_port': 587,
                 'smtp_user': '', 'smtp_from': '', 'email_to': '', 'smtp_tls': 'starttls',
                 'webhook_enabled': False, 'webhook_kind': 'ntfy'},
}

def text(value, limit=128):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ConfigError('Enter plain text with at most ' + str(limit) + ' characters.')
    return value.strip()

def number(value, low, high, name, *, optional=False, integer=False):
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ConfigError(f'{name}: enter a number from {low} to {high}.')
    if integer and int(value) != value:
        raise ConfigError(name + ' must be a whole number.')
    return int(value) if integer else value

def tokens(value):
    return [v.strip().upper() for v in value.split(',') if v.strip()]

def validate_filter(value):
    if not isinstance(value, dict) or set(value) - set(FILTER_DEFAULT):
        raise ConfigError('Invalid filter fields.')
    c = {**FILTER_DEFAULT, **value}
    for key, pattern in [('callsigns', r'[A-Z0-9-]+\*?'), ('icaos', r'[0-9A-F]{6}'), ('squawks', r'[0-7]{4}')]:
        c[key] = text(c[key], 512).upper()
        if len(tokens(c[key])) > 32 or any(not re.fullmatch(pattern, s) for s in tokens(c[key])):
            raise ConfigError('Invalid ' + key + '. Use comma-separated codes; callsigns allow a final *.')
    for prefix, low, high in [('altitude', -2000, 100000), ('speed', 0, 2000)]:
        for suffix in ('min', 'max'):
            key = prefix + '_' + suffix
            c[key] = number(c[key], low, high, key, optional=True)
        if c[prefix+'_min'] is not None and c[prefix+'_max'] is not None and c[prefix+'_min'] > c[prefix+'_max']:
            raise ConfigError(prefix + ': minimum must not exceed maximum.')
    c['distance_max'] = number(c['distance_max'], 0, 21600, 'Distance NM', optional=True)
    if c['source'] not in ('any', 'ADS-B', 'Extended squitter (DF18)', 'Mode S', 'Mode S / Comm-B', 'SBS/BaseStation input'):
        raise ConfigError('Choose a supported data source.')
    if c['state'] not in ('any', 'airborne', 'ground'):
        raise ConfigError('Choose a flight state.')
    c['category'] = text(c['category'], 80)
    for key in ('positioned', 'emergency'):
        if type(c[key]) is not bool:
            raise ConfigError(key + ' must be true or false.')
    return c

def has_conditions(c):
    return any(c.get(k) != v for k, v in FILTER_DEFAULT.items())

def matches(row, criteria, station=None):
    """AND across fields, OR within code lists. Unknown values never match ranges."""
    for key, field in [('callsigns', 'callsign'), ('icaos', 'icao'), ('squawks', 'squawk')]:
        patterns = tokens(criteria.get(key, ''))
        value = str(row.get(field, '')).strip().upper()
        if patterns and not any(fnmatch.fnmatchcase(value, p) for p in patterns):
            return False
    for name, key in [('altitude', 'altitude'), ('speed', 'groundspeed')]:
        value = row.get(key)
        for suffix, compare in [('min', lambda a, b: a >= b), ('max', lambda a, b: a <= b)]:
            bound = criteria.get(name+'_'+suffix)
            if bound is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or not compare(value, bound)):
                return False
    if criteria.get('positioned') and (row.get('latitude') is None or row.get('longitude') is None):
        return False
    if criteria.get('distance_max') is not None:
        dist = None
        if station and station[0] is not None and row.get('latitude') is not None and row.get('longitude') is not None:
            dist = distance_nm(*station, row['latitude'], row['longitude'])
        if dist is None or dist > criteria['distance_max']:
            return False
    if criteria.get('source', 'any') != 'any' and row.get('data_source') != criteria['source']:
        return False
    if criteria.get('state', 'any') != 'any' and row.get('on_ground') is not (criteria['state'] == 'ground'):
        return False
    if criteria.get('category') and criteria['category'].casefold() not in str(row.get('wake_vortex', '')).casefold():
        return False
    if criteria.get('emergency') and str(row.get('squawk', '')) not in ('7500', '7600', '7700') and row.get('emergency_state') not in (1,2,3,4,5,6):
        return False
    return True

def validate_features(value):
    if not isinstance(value, dict) or set(value) - set(FEATURE_DEFAULT):
        raise ConfigError('Invalid feature preferences.')
    c = copy.deepcopy(FEATURE_DEFAULT)
    c.update(copy.deepcopy(value))
    c['filter'] = validate_filter(c['filter'])
    c['display'] = validate_display(c.get('display'))
    for key in ('filter_enabled', 'alerts_enabled', 'history_enabled'):
        if type(c[key]) is not bool:
            raise ConfigError(key + ' must be true or false.')
    for key, lo, hi in [('retention_days', 1, 3650), ('storage_mb', 8, 4096), ('sample_seconds', 5, 300), ('log_max_mb', 1, 1024), ('log_days', 1, 365)]:
        c[key] = number(c[key], lo, hi, key, integer=True)
    for key in ('quiet_start', 'quiet_end'):
        if c[key] and not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', c[key]):
            raise ConfigError('Quiet hours use HH:MM in local time, or leave both empty.')
    if bool(c['quiet_start']) != bool(c['quiet_end']):
        raise ConfigError('Set both quiet-hour times or leave both empty.')
    for key, limit in [('presets', 20), ('rules', 50), ('airports', 100)]:
        if not isinstance(c[key], list) or len(c[key]) > limit:
            raise ConfigError(f'{key}: maximum {limit} entries.')
    c['presets'] = [{'name': text(p['name'], 60), 'filter': validate_filter(p['filter'])} for p in c['presets']]
    ids = set()
    for rule in c['rules']:
        allowed = {'id', 'name', 'enabled', 'filter', 'channels', 'cooldown_seconds', 'include_playback'}
        if not isinstance(rule, dict) or set(rule) != allowed:
            raise ConfigError('Invalid alert rule.')
        rule['id'] = text(rule['id'], 64)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', rule['id']) or rule['id'] in ids:
            raise ConfigError('Alert IDs must be unique.')
        ids.add(rule['id'])
        rule['name'] = text(rule['name'], 80)
        if not rule['name']:
            raise ConfigError('Name the alert rule.')
        rule['filter'] = validate_filter(rule['filter'])
        if not has_conditions(rule['filter']):
            raise ConfigError('An alert needs at least one matching condition.')
        if type(rule['enabled']) is not bool or type(rule['include_playback']) is not bool:
            raise ConfigError('Invalid rule switch.')
        if not isinstance(rule['channels'], list) or not rule['channels'] or any(v not in CHANNELS for v in rule['channels']):
            raise ConfigError('Choose alert channels.')
        rule['cooldown_seconds'] = number(rule['cooldown_seconds'], 30, 86400, 'Cooldown seconds', integer=True)
    for airport in c['airports']:
        if not isinstance(airport, dict) or set(airport) != {'code', 'name', 'latitude', 'longitude', 'radius_nm'}:
            raise ConfigError('Invalid airport reference.')
        airport['code'] = text(airport['code'], 12).upper()
        airport['name'] = text(airport['name'], 100)
        if not re.fullmatch(r'[A-Z0-9-]{2,12}', airport['code']):
            raise ConfigError('Use an airport code such as YPAD.')
        for key, lo, hi in [('latitude', -90, 90), ('longitude', -180, 180), ('radius_nm', .1, 100)]:
            airport[key] = number(airport[key], lo, hi, key)
    if len({a['code'] for a in c['airports']}) != len(c['airports']):
        raise ConfigError('Airport codes must be unique.')
    delivery = c['delivery']
    if not isinstance(delivery, dict) or set(delivery) != set(FEATURE_DEFAULT['delivery']):
        raise ConfigError('Invalid delivery settings.')
    for key in ('email_enabled', 'webhook_enabled'):
        if type(delivery[key]) is not bool:
            raise ConfigError('Invalid delivery switch.')
    for key in ('smtp_host', 'smtp_user', 'smtp_from', 'email_to'):
        delivery[key] = text(delivery[key], 254)
    delivery['smtp_port'] = number(delivery['smtp_port'], 1, 65535, 'SMTP port', integer=True)
    if delivery['smtp_tls'] not in ('starttls', 'ssl') or delivery['webhook_kind'] not in ('ntfy', 'discord', 'json'):
        raise ConfigError('Choose secure delivery options.')
    if delivery['email_enabled']:
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', delivery['smtp_host']):
            raise ConfigError('Enter an SMTP hostname.')
        if any(not re.fullmatch(r'[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+', delivery[k]) for k in ('smtp_from', 'email_to')):
            raise ConfigError('Enter one sender and one recipient email address.')
    return c

def validate_endpoint(value):
    value = text(value, 2048)
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
        raise ConfigError('Use an HTTPS webhook/topic URL without embedded login details or a fragment.')
    return value

class FeatureSettings:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'features.json'
        self.secret_path = self.directory / ('alert-secrets.dpapi' if os.name == 'nt' else 'alert-secrets')
        self.value = validate_features(json.loads(self.path.read_text('utf-8')) if self.path.exists() else {})

    def secrets(self):
        if not self.secret_path.exists():
            return {}
        raw = self.secret_path.read_bytes()
        return json.loads((protect(raw, True) if os.name == 'nt' else raw).decode('utf-8'))

    @staticmethod
    def _write(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
        os.replace(temporary, path)

    def save(self, value, secret_changes=None):
        value = validate_features(value)
        secrets = self.secrets()
        if secret_changes is not None:
            if not isinstance(secret_changes, dict) or set(secret_changes) - {'smtp_password', 'webhook_url', 'webhook_token'}:
                raise ConfigError('Invalid alert credentials.')
            for key, entry in secret_changes.items():
                entry = text(entry, 2048)
                if key == 'webhook_url' and entry:
                    validate_endpoint(entry)
                if entry:
                    secrets[key] = entry
                else:
                    secrets.pop(key, None)
        if value['delivery']['webhook_enabled'] and not secrets.get('webhook_url'):
            raise ConfigError('Store an HTTPS webhook/topic URL before enabling webhook delivery.')
        if value['delivery']['email_enabled'] and value['delivery']['smtp_user'] and not secrets.get('smtp_password'):
            raise ConfigError('Store the SMTP/app password before enabling this authenticated email account.')
        if secret_changes is not None:
            raw = json.dumps(secrets).encode('utf-8')
            self._write(self.secret_path, protect(raw) if os.name == 'nt' else raw)
        self._write(self.path, json.dumps(value, indent=2).encode('utf-8'))
        self.value = value

    def public(self):
        secrets = self.secrets()
        return {'settings': copy.deepcopy(self.value), 'stored_secrets': {k: bool(secrets.get(k)) for k in ('smtp_password', 'webhook_url', 'webhook_token')}}
