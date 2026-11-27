"""Explicit mappings from the supplied Pi feeder's user configuration."""
import copy, shlex
from .config import ConfigError, PI24_DECODERS, parse_ini, parse_pi24_ini, format_ini, pi24_profile_values, validate

def import_ini(text, current):
    source = parse_ini(text); c = copy.deepcopy(current); warnings = []
    if any(k.casefold() == 'fr24key' and k != 'fr24key' for k in source):
        raise ConfigError('Use the lowercase fr24key field for the protected sharing key.')
    pi_profile = source.get('receiver') == 'dvbt' and source.get('path') in PI24_DECODERS
    if pi_profile:
        fields = {'receiver','bs','raw','path','logmode','procargs','windowmode','mpx','mlat','mlat-without-gps'}
        c.update(parse_pi24_ini(format_ini({k:v for k,v in source.items() if k in fields})))
        warnings.append('Pi24 profile and MLAT preferences imported. The active receiver, tuner and listeners are unchanged. Use the Flightradar24 settings page to copy supported values to Windows.')
        for key in sorted(source.keys()-fields-{'fr24key'}):
            warnings.append('Not applied to the Pi24 profile: '+key)
        return validate(c), source.get('fr24key'), warnings
    c['original'] = {k:v for k,v in source.items() if k!='fr24key'}
    receiver_map = {'dvbt':'usb','beast-tcp':'beast-tcp','avr-tcp':'avr-tcp','beast':'beast-serial',
                    'avr':'avr-serial','microadsb':'avr-serial'}
    if 'receiver' in source:
        if source['receiver'] not in receiver_map:
            raise ConfigError('This INI uses '+source['receiver']+'. This build supports RTL-SDR 1090 MHz, Beast and AVR inputs. Kinetic binary and UAT receivers cannot be imported as working equivalents.')
        c['receiver'] = receiver_map[source['receiver']]
    for key in ('raw','bs','mlat'):
        if key in source: c[key] = source[key].lower()=='yes'
    if 'mlat-without-gps' in source: c['mlat_without_gps'] = source['mlat-without-gps'].lower()=='yes'
    for old,new in [('baudrate','baudrate'),('gt','global_timeout'),('logmode','logmode')]:
        if source.get(old):
            try: c[new] = int(source[old])
            except ValueError: raise ConfigError(old+' must be a whole number.')
    if source.get('host'):
        host,sep,port = source['host'].rpartition(':')
        if sep:
            c['host'] = host.strip('[]')
            try: c['input_port'] = int(port)
            except ValueError: raise ConfigError('Host must contain a valid TCP port.')
        else: c['host'] = source['host']
    if source.get('path'):
        if source['path'].upper().startswith('COM'): c['serial_port'] = source['path'].upper()
        else: warnings.append('Linux device path preserved. Select the corresponding Windows COM port.')
    if source.get('mpx')=='yes': warnings.append('MPX is preserved for Pi24. The Windows receiver does not produce Kinetic MPX.')
    if source.get('bind-interface'):
        if source['bind-interface'] in ('127.0.0.1','0.0.0.0'): c['bind_interface'] = source['bind-interface']
        else: warnings.append('The Pi interface address was preserved. Windows stream access remains on the selected local/LAN interface.')
    if source.get('procargs'):
        try: args = shlex.split(source['procargs'])
        except ValueError as e: raise ConfigError('Invalid procargs: '+str(e))
        i = 0
        while i<len(args):
            flag = args[i]; i += 1
            mapping = {'--gain':('gain',float),'--ppm':('ppm',int),'--device-index':('device',str),
                       '--lat':('latitude',float),'--lon':('longitude',float),
                       '--net-ro-port':('raw_port',int),'--net-sbs-port':('bs_port',int),'--net-bo-port':('beast_port',int)}
            if flag in mapping:
                if i>=len(args): raise ConfigError('Missing value for '+flag)
                key,kind = mapping[flag]
                try: c[key] = kind(args[i])
                except ValueError: raise ConfigError('Invalid value for '+flag)
                i += 1
            elif flag=='--net': warnings.append('The Pi --net flag is replaced by the Windows app and explicit stream switches.')
            elif flag=='--agc': c['agc'] = True
            elif flag=='--fix': c['error_correction'] = 1
            elif flag=='--no-fix': c['error_correction'] = 0
            elif flag=='--phase-enhance': c['phase_enhance'] = True
            else: warnings.append('Preserved but not executed: '+flag)
    known = {'receiver','fr24key','raw','bs','mlat','mlat-without-gps','baudrate','gt','logmode','host','path','mpx','bind-interface','procargs'}
    for k in source.keys()-known: warnings.append('Preserved but not applied: '+k)
    return validate(c), source.get('fr24key'), warnings

def export_ini(c):
    values = dict(c['original'])
    reverse = {'usb':'dvbt','beast-tcp':'beast-tcp','avr-tcp':'avr-tcp','beast-serial':'beast','avr-serial':'avr','sbs-tcp':'sbs1tcp','iq-file':'dvbt'}
    values.update(receiver=reverse[c['receiver']], raw='yes' if c['raw'] else 'no', bs='yes' if c['bs'] else 'no',
                  mlat='yes' if c['mlat'] else 'no', logmode=str(c['logmode']), gt=str(c['global_timeout']))
    values['mlat-without-gps'] = 'yes' if c['mlat_without_gps'] else 'no'
    values['bind-interface'] = c['bind_interface']
    if c['receiver'].endswith('tcp'): values['host'] = f"{c['host']}:{c['input_port']}"
    if c['receiver'].endswith('serial'): values.update(path=c['serial_port'], baudrate=str(c['baudrate']))
    if c['receiver']=='usb': values['procargs'] = f"--gain {c['gain']} --ppm {c['ppm']} --device-index {c['device']}"
    values.pop('fr24key',None)
    return '# Sharing key intentionally excluded. Windows settings.json is the full app configuration.\n'+format_ini(values)

def export_pi24_profile(config):
    return '# Pi24 direct-USB profile, not the active Windows feeder INI. Sharing key omitted.\n'+format_ini(pi24_profile_values(config))
