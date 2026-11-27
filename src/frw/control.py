"""In-process application operations shared by the desktop and optional HTTP API.

Calls may block while starting/stopping processes or scanning devices. Desktop
adapters run execute() on a worker; this module has no GUI or HTTP dependency.
"""
from __future__ import annotations
from dataclasses import dataclass
import io, json, os, platform, re, subprocess, threading, zipfile
from typing import Any, Callable
from urllib.parse import urlsplit, parse_qs
from .config import ConfigError, validate
from .migration import import_ini, export_ini, export_pi24_profile
from .runtime import HIDDEN, check_feeder_input, decoder_executable


@dataclass(frozen=True)
class ControlResult:
    status: int
    data: Any
    mime: str = 'application/json; charset=utf-8'
    filename: str | None = None


class Controller:
    def __init__(self, receiver, shutdown: Callable[[], None]):
        self.receiver = receiver
        self.settings = receiver.settings
        self.shutdown = shutdown
        self.operation_lock = threading.Lock()

    def execute(self, path: str, method: str = 'GET', data: dict | None = None) -> ControlResult:
        """Return the same result for native callers and authenticated HTTP callers."""
        try:
            if method == 'GET':
                return self._get(path)
            if method != 'POST':
                return ControlResult(405, {'error': 'Use GET or POST requests.'})
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ConfigError('Request must be an object.')
            with self.operation_lock:
                return self._post(path, data)
        except (ConfigError, ValueError, KeyError, TypeError) as error:
            return ControlResult(400 if method == 'POST' else 500, {'error': str(error)})
        except Exception as error:
            if method == 'POST':
                self.receiver.log.add('app', str(error))
            return ControlResult(500, {'error': str(error)})

    def _get(self, path):
        receiver = self.receiver
        parsed=urlsplit(path)
        path=parsed.path
        if path == '/api/features':
            return ControlResult(200, receiver.activity.settings.public())
        if path == '/api/statistics':
            query = parse_qs(parsed.query)
            hours_text=query.get('hours',['24'])[0]
            hours=int(hours_text) if hours_text.isdigit() and len(hours_text)<8 else -1
            mode = query.get('mode', ['live'])[0]
            if hours not in (1,24,168,720,87600) or mode not in ('live','playback'):
                return ControlResult(400, {'error':'Choose a supported history period and data source.'})
            report = receiver.activity.store.report(receiver.activity.settings.value,hours,mode)
            report['health'] = receiver.activity.snapshot()
            return ControlResult(200, report)
        if path == '/api/status':
            return ControlResult(200, receiver.snapshot())
        if path.startswith('/api/aircraft/'):
            icao = path.removeprefix('/api/aircraft/').upper()
            if not re.fullmatch(r'[0-9A-F]{6}', icao):
                return ControlResult(400, {'error': 'Use a six-digit hexadecimal aircraft address.'})
            detail = receiver.aircraft.detail(icao)
            if detail is None:
                return ControlResult(404, {'error': 'Aircraft is no longer being received.'})
            return ControlResult(200, detail)
        if path == '/api/settings':
            return ControlResult(200, self.settings.public())
        if path == '/api/logs':
            return ControlResult(200, receiver.log.snapshot())
        if path == '/api/signup':
            return ControlResult(200, receiver.feeder.signup_snapshot())
        if path == '/api/export':
            return ControlResult(200, export_ini(self.settings.value), 'text/plain; charset=utf-8', 'fr24feed.ini')
        if path == '/api/export/pi24':
            return ControlResult(200, export_pi24_profile(self.settings.value), 'text/plain; charset=utf-8', 'fr24feed-pi24.ini')
        if path == '/api/diagnostics':
            return ControlResult(200, self.diagnostics(), 'application/zip', 'flight-radar-diagnostics.zip')
        if path == '/api/devices':
            return ControlResult(200, self.devices())
        return ControlResult(404, {'error': 'Not found.'})

    def _post(self, path, data):
        receiver, settings = self.receiver, self.settings
        if path == '/api/alerts/test':
            return ControlResult(200,receiver.activity.test_external())
        if path == '/api/features':
            receiver.activity.save(data['settings'], data.get('secrets'))
            config=receiver.activity.settings.value
            receiver.log.set_limits(config['log_max_mb'],config['log_days'])
            return ControlResult(200, receiver.activity.settings.public())
        if path == '/api/start':
            receiver.start()
        elif path == '/api/stop':
            receiver.stop()
        elif path == '/api/restart':
            check_feeder_input(settings.value)
            receiver.stop()
            receiver.start()
        elif path == '/api/settings':
            config = validate(data['settings'])
            check_feeder_input(config)
            if receiver.thread and receiver.thread.is_alive():
                raise ConfigError('Stop the receiver before changing settings.')
            if receiver.feeder.signup_process and receiver.feeder.signup_process.poll() is None:
                raise ConfigError('Finish or cancel registration first.')
            settings.save(config)
            receiver.log.configure(config['logmode'])
        elif path == '/api/key':
            if receiver.thread and receiver.thread.is_alive():
                raise ConfigError('Stop the receiver before changing its sharing key.')
            settings.save_key(data.get('key', ''))
        elif path == '/api/import':
            config, key, warnings = import_ini(data.get('text', ''), settings.value)
            # Preview never writes settings or the key.
            return ControlResult(200, {'settings': config, 'has_imported_key': bool(key), 'warnings': warnings})
        elif path == '/api/import/apply':
            if receiver.thread and receiver.thread.is_alive():
                raise ConfigError('Stop the receiver before importing settings.')
            config, key, warnings = import_ini(data.get('text', ''), settings.value)
            if key:
                settings.save_key(key)
            settings.save(config)
            receiver.log.configure(config['logmode'])
            return ControlResult(200, {'ok': True, 'warnings': warnings})
        elif path == '/api/signup/start':
            receiver.feeder.signup_start()
        elif path == '/api/signup/answer':
            receiver.feeder.signup_answer(data.get('answer', ''))
        elif path == '/api/signup/stop':
            receiver.feeder.signup_stop()
        elif path == '/api/shutdown':
            receiver.feeder.signup_stop()
            receiver.stop()
            # The adapter schedules its own exit only after backend cleanup.
            self.shutdown()
        else:
            return ControlResult(404, {'error': 'Not found.'})
        return ControlResult(200, {'ok': True})

    def devices(self):
        if platform.system() == 'Linux':
            if self.receiver.thread and self.receiver.thread.is_alive():
                return {'devices': [], 'error': 'Stop the receiver before scanning USB devices; the tuner can be opened by one program at a time.'}
            utility = self.receiver.bundle/'tools/rtl_test'
            if not utility.is_file():
                return {'devices': [], 'error': 'The bundled RTL-SDR device scanner is missing.'}
            env = dict(os.environ)
            private_lib = self.receiver.bundle/'lib'
            if private_lib.is_dir():
                env['LD_LIBRARY_PATH'] = str(private_lib) + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
            devices = []
            last_error = ''
            for index in range(8):
                try:
                    result = subprocess.run([str(utility), '-t', '-d', str(index)], capture_output=True,
                                             timeout=1.5, env=env, creationflags=HIDDEN)
                    return_code = result.returncode
                    output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
                except subprocess.TimeoutExpired as error:
                    return_code = None
                    stdout = error.stdout or b''; stderr = error.stderr or b''
                    output = (stdout + stderr).decode('utf-8', errors='replace')
                except OSError:
                    return {'devices': [], 'error': 'Could not start the bundled RTL-SDR device scanner.'}
                last_error = output[-500:]
                match = re.search(rf'Using device\s+{index}:\s*([^\r\n]+)', output, re.I)
                # rtl_test prints the selected index before rtlsdr_open(). The
                # gain-table line only occurs after a successful device open;
                # timeouts before that point are inconclusive, not detection.
                opened = 'Supported gain values (' in output
                if match and opened:
                    name = match.group(1).strip()
                    devices.append({'FriendlyName': f'Index {index}: {name}', 'Status': 'Available',
                                    'InstanceId': 'RTL-SDR ' + name, 'index': str(index)})
                    continue
                lower = output.lower()
                if any(marker in lower for marker in ('failed to open rtlsdr device', 'usb_claim_interface',
                                                       'usb_open error', 'permission denied', 'access denied')):
                    if not devices:
                        return {'devices': [], 'error': 'Linux found the RTL-SDR USB device but could not open it. Install the bundled udev rule, reconnect the receiver, and scan again.'}
                    break
                if return_code is None and index == 0:
                    return {'devices': [], 'error': 'RTL-SDR detection timed out before confirming that Linux could open the device. Check the USB connection and udev permissions.'}
                if devices:
                    break
                if index == 0:
                    break
            if devices:
                return {'devices': devices, 'error': None}
            if 'claim' in last_error.lower() or 'permission' in last_error.lower() or 'access' in last_error.lower():
                return {'devices': [], 'error': 'Linux could not access the RTL-SDR USB device. Install the bundled udev rule, reconnect the receiver, and scan again.'}
            return {'devices': [], 'error': 'No compatible RTL-SDR device was found. Check the USB connection and udev permissions.'}
        try:
            decoder = decoder_executable(self.receiver.bundle)
        except ConfigError as error:
            return {'devices': [], 'error': str(error)}
        try:
            native = subprocess.run([str(decoder), '--list-devices'], capture_output=True, timeout=8, creationflags=HIDDEN)
            devices = []
            for line in native.stdout.decode('utf-8', errors='replace').splitlines():
                parts = line.split('\t')
                if len(parts) == 4 and parts[0].isdigit():
                    devices.append({'FriendlyName': f'Index {parts[0]}: {parts[1]} {parts[2]}',
                                    'Status': 'Listed by RTL-SDR library', 'InstanceId': 'Serial: '+parts[3],
                                    'index': parts[0]})
            if devices:
                return {'devices': devices, 'error': None}
        except (OSError, subprocess.TimeoutExpired):
            pass
        if platform.system() != 'Windows':
            return {'devices': [], 'error': 'Receiver enumeration failed. Check native libusb and the scoped RTL-SDR udev permissions.'}
        script = "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match '^USB\\\\VID_(0BDA|0CCD|1D19|1F4D|1B80|15F4)' -or $_.FriendlyName -match 'RTL283|RTL-SDR|R820|DVB-T' } | Select-Object FriendlyName,Status,InstanceId | ConvertTo-Json -Compress"
        try:
            process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                     capture_output=True, timeout=12, creationflags=HIDDEN)
            if process.returncode:
                return {'devices': [], 'error': 'Windows device enumeration failed.'}
            value = json.loads(process.stdout.decode('utf-8', errors='replace') or '[]')
            return {'devices': value if isinstance(value, list) else [value], 'error': None}
        except Exception as error:
            return {'devices': [], 'error': str(error)}

    def diagnostics(self):
        data = io.BytesIO()
        # Never export keys, registration transcripts, station coordinates or logs.
        config = dict(self.settings.value)
        config.pop('original', None)
        for key in ('latitude', 'longitude', 'altitude_ft', 'host', 'iq_file', 'serial_port', 'device'):
            config[key] = '[redacted]'
        status = self.receiver.snapshot()
        status.pop('aircraft', None)
        status.pop('activity', None)
        status['feeder']['monitor'] = '[excluded: may contain station location]'
        with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('status.json', json.dumps(status, indent=2))
            archive.writestr('settings-redacted.json', json.dumps(config, indent=2))
            archive.writestr('system.json', json.dumps({'system': platform.system(), 'version': platform.version(),
                                                       'architecture': platform.machine()}, indent=2))
            archive.writestr('README.txt', 'This diagnostic archive omits sharing keys, receiver location and registration answers. Logs are viewable locally but excluded because they may contain locations or identifiers.\n')
        return data.getvalue()
