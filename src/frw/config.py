"""Typed local settings and per-user sharing-key storage."""
from __future__ import annotations
import base64, copy, ctypes, json, math, os, platform, re
from pathlib import Path
from .platform_support import key_storage_description

DEFAULT = {
    'receiver': 'usb', 'device': '0', 'host': '127.0.0.1', 'input_port': 30005,
    'serial_port': 'COM3', 'baudrate': 115200, 'iq_file': '', 'sample_rate': 2400000,
    'gain': -10.0, 'ppm': 0, 'agc': False, 'bias_tee': False,
    'bandwidth_khz':0, 'mode_ac':True, 'capture_mb':0, 'meteo':False,
    'error_correction': 1, 'phase_enhance': False, 'dc_filter': True,
    'latitude': None, 'longitude': None, 'altitude_ft': None,
    'range_nm': 300, 'aircraft_ttl': 60, 'global_timeout': 600,
    'raw': False, 'raw_port': 30002, 'bs': False, 'bs_port': 30003,
    'beast': False, 'beast_port': 30005, 'bind_interface': '127.0.0.1',
    'logmode': 2, 'autostart_receiver': False,
    'feed_enabled': False, 'feed_mode': 'relay', 'mlat': True, 'mlat_without_gps': True,
    'pi24': {
        'bs': False, 'raw': False, 'mpx': False, 'logmode': 1, 'windowmode': 0,
        'path': '/usr/lib/fr24/dump1090', 'procargs': '--gain -10 --net',
    },
    'original': {},
}
if os.name != 'nt':
    DEFAULT['serial_port'] = '/dev/ttyUSB0'
RECEIVERS = ('usb', 'beast-tcp', 'avr-tcp', 'sbs-tcp', 'beast-serial', 'avr-serial', 'iq-file')
PI24_DECODERS = ('/usr/lib/fr24/dump1090', '/usr/bin/dump1090-mutability', '/usr/bin/dump1090-fa')

class ConfigError(ValueError): pass

def validate(config):
    if not isinstance(config, dict): raise ConfigError('Settings must be an object.')
    unknown = set(config) - set(DEFAULT)
    if unknown: raise ConfigError('Unknown settings: ' + ', '.join(sorted(unknown)))
    c = copy.deepcopy(DEFAULT); c.update(config)
    if c['receiver'] not in RECEIVERS: raise ConfigError('Choose a supported receiver input.')
    if c['feed_mode'] not in ('relay', 'direct-usb'): raise ConfigError('Choose relay or direct USB feeding.')
    for key in ['agc','bias_tee','phase_enhance','dc_filter','raw','bs','beast','autostart_receiver','feed_enabled','mlat','mlat_without_gps','mode_ac','meteo']:
        if type(c[key]) is not bool: raise ConfigError(key + ' must be true or false.')
    limits = {'input_port':(1,65535),'raw_port':(1024,65535),'bs_port':(1024,65535),
              'beast_port':(1024,65535),'ppm':(-1000,1000),'range_nm':(1,21600),
              'aircraft_ttl':(10,3600),'global_timeout':(0,86400),'gain':(-10,60),
              'latitude':(-90,90),'longitude':(-180,180),'altitude_ft':(-2000,30000)}
    for key,(lo,hi) in limits.items():
        v=c[key]
        if v is None and key in ('latitude','longitude','altitude_ft'): continue
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ConfigError(f'{key}: enter a number from {lo} to {hi}.')
        if key.endswith('port') or key in ('ppm','aircraft_ttl','global_timeout'):
            if int(v)!=v: raise ConfigError(key+' must be a whole number.')
            c[key]=int(v)
    if (c['latitude'] is None)!=(c['longitude'] is None): raise ConfigError('Enter both latitude and longitude, or leave both empty.')
    if -10 < c['gain'] < 0: raise ConfigError('Gain must be -10 for automatic gain, or from 0 to 60 dB.')
    if c['sample_rate'] not in (2000000,2400000): raise ConfigError('Sample rate must be 2 MHz or 2.4 MHz.')
    if type(c['bandwidth_khz']) is not int or not (c['bandwidth_khz']==0 or 300<=c['bandwidth_khz']<=3200):
        raise ConfigError('Tuner bandwidth: use 0 for the driver default, or 300–3200 kHz.')
    if type(c['capture_mb']) is not int or not 0<=c['capture_mb']<=4096:
        raise ConfigError('Raw message capture budget must be 0–4096 MiB; 0 disables recording.')
    if platform.system() == 'Linux' and c['receiver'] in ('usb', 'iq-file') and c['sample_rate'] != 2400000:
        raise ConfigError('The Linux decoder uses a fixed 2.4 MHz sample rate for USB and I/Q input.')
    if c['error_correction'] not in (0,1,2) or c['logmode'] not in (0,1,2): raise ConfigError('Invalid correction or logging mode.')
    if not isinstance(c['pi24'], dict) or set(c['pi24']) != set(DEFAULT['pi24']):
        raise ConfigError('Invalid Flightradar24 feeder settings.')
    vendor = c['pi24']
    for key in ('bs', 'raw', 'mpx'):
        if type(vendor[key]) is not bool: raise ConfigError(f'Flightradar24 {key} must be true or false.')
    if type(vendor['logmode']) is not int or vendor['logmode'] not in (0,1,2):
        raise ConfigError('Flightradar24 log mode must be 0, 1, or 2.')
    if type(vendor['windowmode']) is not int or vendor['windowmode'] not in (0,1,2):
        raise ConfigError('Invalid Flightradar24 window mode.')
    if vendor['path'] not in PI24_DECODERS:
        raise ConfigError('Unsupported Flightradar24 DVB-T decoder path.')
    procargs = vendor['procargs']
    if not isinstance(procargs, str) or len(procargs) > 64 or any(ch in procargs for ch in '\r\n\0;&|`$<>'):
        raise ConfigError('Invalid Flightradar24 decoder arguments.')
    match = re.fullmatch(r'--gain (-10|(?:0|[1-9][0-9]?)(?:\.\d+)?)(?: --net)?', procargs)
    if not match:
        raise ConfigError('Supported Flightradar24 decoder arguments are --gain <dB> with optional --net.')
    gain = float(match.group(1))
    if not math.isfinite(gain) or gain < 0 and gain != -10 or gain > 60:
        raise ConfigError('Flightradar24 decoder gain must be -10 or 0–60 dB.')
    if c['feed_mode'] == 'direct-usb' and c['receiver'] != 'usb':
        raise ConfigError('Direct USB sharing requires the USB receiver input.')
    if c['baudrate'] not in (57600,115200,921600,1000000,3000000): raise ConfigError('Unsupported baud rate.')
    for key in ('device','host','serial_port','iq_file','bind_interface'):
        if not isinstance(c[key],str) or len(c[key])>1024 or any(ch in c[key] for ch in '\r\n\0'):
            raise ConfigError('Invalid '+key+'.')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}',c['host']): raise ConfigError('Enter a host name or IP address without a URL scheme.')
    if c['bind_interface'] not in ('127.0.0.1','0.0.0.0'): raise ConfigError('Choose local access or LAN access for data streams.')
    if os.name == 'nt':
        if not re.fullmatch(r'COM[1-9][0-9]{0,2}',c['serial_port'],re.I): raise ConfigError('Use a Windows serial port such as COM3.')
    elif not re.fullmatch(r'/dev/[A-Za-z0-9_./-]{1,240}',c['serial_port']) or '..' in Path(c['serial_port']).parts:
        raise ConfigError('Use a local serial device path such as /dev/ttyUSB0.')
    ports=[c[k+'_port'] for k in ('raw','bs','beast') if c[k]]
    if len(set(ports))!=len(ports): raise ConfigError('Enabled output ports must be different.')
    if c['receiver']=='sbs-tcp' and (c['raw'] or c['beast'] or c['feed_enabled']):
        raise ConfigError('SBS input contains decoded reports; use AVR or Beast input for raw output or Flightradar24 feeding.')
    if c['receiver']=='iq-file' and c['feed_enabled']: raise ConfigError('Recorded I/Q playback must not be uploaded as live traffic.')
    if not isinstance(c['original'],dict) or len(c['original'])>150: raise ConfigError('Invalid original settings.')
    for k,v in c['original'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',k) or not isinstance(v,str) or len(v)>2048 or any(ch in v for ch in '\r\n\0'):
            raise ConfigError('Original INI contains an invalid key or value.')
        if k.casefold()=='fr24key': raise ConfigError('Store the sharing key in its protected field.')
    return c

def parse_ini(text):
    if not isinstance(text,str) or len(text)>65536: raise ConfigError('INI file must be smaller than 64 KB.')
    result={}
    for n,line in enumerate(text.splitlines(),1):
        line=line.strip()
        if not line or line.startswith(('#',';')):continue
        if '=' not in line: raise ConfigError(f'INI line {n}: expected key=value.')
        k,v=line.split('=',1);k=k.strip();v=v.strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',k): raise ConfigError(f'INI line {n}: invalid key.')
        if k in result:raise ConfigError(f'INI line {n}: duplicate {k}.')
        if v.startswith(('"',"'")):
            if len(v)<2 or v[-1]!=v[0]:raise ConfigError(f'INI line {n}: unmatched quote.')
            v=v[1:-1]
        else:v=v.split('#',1)[0].strip()
        result[k]=v
    return result

def format_ini(values):
    lines=[]
    for k,v in values.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',k):raise ConfigError('Invalid INI key.')
        v=str(v)
        if any(x in v for x in '\r\n\0"'):raise ConfigError('INI values cannot contain newlines or quotation marks.')
        lines.append(f'{k}="{v}"')
    return '\n'.join(lines)+'\n'

def parse_pi24_ini(text):
    """Parse supported Pi24 settings without returning or storing its key."""
    parsed = parse_ini(text)
    if parsed.get('receiver', 'dvbt').lower() != 'dvbt':
        raise ConfigError('The imported Flightradar24 configuration is not DVB-T direct USB.')
    allowed = {'receiver', 'fr24key', 'bs', 'raw', 'path', 'logmode', 'procargs',
               'windowmode', 'mpx', 'mlat', 'mlat-without-gps'}
    unknown = set(parsed) - allowed
    if unknown: raise ConfigError('Unsupported Flightradar24 settings: ' + ', '.join(sorted(unknown)))
    values = copy.deepcopy(DEFAULT['pi24'])
    result = {'pi24': values}
    for key in ('bs', 'raw', 'mpx'):
        if key in parsed:
            if parsed[key].lower() not in ('yes', 'no'): raise ConfigError(f'Invalid Flightradar24 {key} setting.')
            values[key] = parsed[key].lower() == 'yes'
    for key in ('logmode', 'windowmode'):
        if key in parsed:
            if not re.fullmatch(r'\d+', parsed[key]): raise ConfigError(f'Invalid Flightradar24 {key}.')
            values[key] = int(parsed[key])
    for key in ('path', 'procargs'):
        if key in parsed: values[key] = parsed[key]
    for source, target in (('mlat', 'mlat'), ('mlat-without-gps', 'mlat_without_gps')):
        if source in parsed:
            if parsed[source].lower() not in ('yes', 'no'): raise ConfigError(f'Invalid Flightradar24 {source} setting.')
            result[target] = parsed[source].lower() == 'yes'
    validate({**DEFAULT, 'feed_mode': 'direct-usb', 'receiver': 'usb', **result})
    return result

def pi24_profile_values(config):
    """Settings for a Pi24 configuration file, independent of the active receiver."""
    c = validate(config)
    vendor = c['pi24']
    return {
        'receiver': 'dvbt',
        'bs': 'yes' if vendor['bs'] else 'no', 'raw': 'yes' if vendor['raw'] else 'no',
        'path': vendor['path'], 'logmode': vendor['logmode'], 'procargs': vendor['procargs'],
        'windowmode': vendor['windowmode'], 'mpx': 'yes' if vendor['mpx'] else 'no',
        'mlat': 'yes' if c['mlat'] else 'no',
        'mlat-without-gps': 'yes' if c['mlat_without_gps'] else 'no',
    }

def protect(data: bytes, decrypt=False):
    if os.name!='nt': raise ConfigError('Sharing-key storage requires Windows DPAPI.')
    class Blob(ctypes.Structure): _fields_=[('size',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buf=ctypes.create_string_buffer(data);src=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));dst=Blob()
    fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(src),None,None,None,None,1,ctypes.byref(dst)):raise ctypes.WinError()
    try:return ctypes.string_at(dst.data,dst.size)
    finally:ctypes.windll.kernel32.LocalFree(dst.data)

class Settings:
    def __init__(self,directory:Path):
        self.directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        if os.name != 'nt':
            try: os.chmod(directory,0o700)
            except OSError: pass
        self.path=directory/'settings.json';self.key_path=directory/('sharing-key.dpapi' if os.name=='nt' else 'sharing-key')
        self.value=validate(json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {})
    def save(self,c):
        c=validate(c);p=self.path.with_suffix('.tmp')
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream: stream.write(json.dumps(c,indent=2))
        if os.name != 'nt': os.chmod(p,0o600)
        os.replace(p,self.path);self.value=c
    def key(self):
        if not self.key_path.exists(): return ''
        raw = self.key_path.read_bytes()
        return (protect(raw,True) if os.name=='nt' else raw).decode('ascii')
    def save_key(self,value):
        if not isinstance(value,str) or (value and not re.fullmatch(r'[A-Fa-f0-9]{16}',value)):
            raise ConfigError('A Flightradar24 sharing key must contain 16 hexadecimal characters.')
        if value:
            p=self.key_path.with_suffix('.tmp')
            payload = protect(value.encode('ascii')) if os.name=='nt' else value.encode('ascii')
            fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            try:
                with os.fdopen(fd,'wb') as stream: stream.write(payload)
            except Exception:
                try: os.close(fd)
                except OSError: pass
                raise
            if os.name != 'nt': os.chmod(p,0o600)
            os.replace(p,self.key_path)
        elif self.key_path.exists():self.key_path.unlink()
    def public(self):return {**copy.deepcopy(self.value),'has_key':self.key_path.exists(),'key_storage':key_storage_description()}
