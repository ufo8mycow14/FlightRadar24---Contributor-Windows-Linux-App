"""Receiver lifecycle and the unchanged official Flightradar24 feeder."""
from __future__ import annotations
import collections, datetime, ipaddress, json, logging, logging.handlers, os, platform, re
import math, shutil, socket, ssl, subprocess, threading, time
import signal
from pathlib import Path
from .aircraft import Aircraft
from .capture import MessageCapture
from .activity import ActivityService
from .config import ConfigError, format_ini, parse_ini
from .platform_support import key_storage_description
from .streams import AVRParser, BeastParser, Broadcast, Frame

HIDDEN = 0x08000000 if os.name == 'nt' else 0

def hidden_startupinfo():
    """Give Windows helper startup an explicit hidden-window hint as well."""
    if os.name != 'nt': return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return info

def cygpath(path):
    path = Path(path).resolve()
    if path.drive.startswith('\\\\'):
        return path.as_posix()
    return '/cygdrive/' + path.drive[0].lower() + path.as_posix()[2:]

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def terminate_process_tree(process, timeout=4):
    """Stop the Linux process group, or the Windows child process."""
    if not process: return
    is_linux = platform.system() == 'Linux'
    if is_linux:
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    elif process.poll() is None:
        process.terminate()
    try: process.wait(timeout)
    except subprocess.TimeoutExpired:
        if is_linux:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        else:
            process.kill()
        process.wait(2)

def prepare_trust_store(root):
    # The Linux feeder cannot read Windows' certificate store itself.
    certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
    if not certificates:
        raise ConfigError('Windows has no trusted certificates available for secure feeder connections.')
    pem = ''.join(ssl.DER_cert_to_PEM_cert(certificate) for certificate in sorted(set(certificates)))
    for name in ('etc/ssl/certs/ca-certificates.crt','etc/ssl/certs/ca-bundle.crt','usr/lib/ssl/cert.pem'):
        path = root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(pem,encoding='ascii')
    # OpenSSL 3.5's default hybrid group fails under this emulator. Standard
    # TLS 1.3 groups complete verified handshakes with the vendor service.
    (root/'etc/ssl/openssl.cnf').write_text(
        'openssl_conf = openssl_init\nconfig_diagnostics = 1\n\n'
        '[openssl_init]\nssl_conf = ssl_configuration\n\n'
        '[ssl_configuration]\nsystem_default = tls_system_default\n\n'
        '[tls_system_default]\nGroups = X25519:P-256\n',encoding='ascii')

def decoder_executable(bundle: Path) -> Path:
    """Resolve a native decoder and reject Windows binaries on Linux."""
    windows = platform.system() == 'Windows'
    if platform.system() not in ('Windows', 'Linux'):
        raise ConfigError('Native receiver decoding is not available for this operating system yet.')
    name = 'dump1090.exe' if windows else 'dump1090'
    path = Path(bundle) / 'decoder' / name
    if not path.is_file():
        if not windows and (Path(bundle)/'decoder'/'dump1090.exe').is_file():
            raise ConfigError('This package contains a Windows decoder. Install a Linux x86_64 package with a native dump1090 binary.')
        raise ConfigError(f'The native decoder is missing from the package: {name}.')
    if not windows and not os.access(path, os.X_OK):
        raise ConfigError('The Linux decoder is not marked executable. Extract the package with file permissions preserved.')
    return path

def linux_decoder_argv(decoder: Path, c: dict, beast_port: int) -> list[str]:
    """Build the command line for the pinned FlightAware Linux decoder."""
    if c['receiver'] == 'iq-file':
        argv = [str(decoder), '--ifile', c['iq_file'], '--throttle', '--net',
                '--net-bind-address', '127.0.0.1', '--net-ri-port', '0', '--net-ro-port', '0',
                '--net-sbs-port', '0', '--net-bi-port', '0', '--net-bo-port', str(beast_port)]
    else:
        argv = [str(decoder), '--device-index', c['device'], '--gain', str(c['gain']),
                '--freq', '1090000000', '--ppm', str(c['ppm']), '--net',
                '--net-bind-address', '127.0.0.1', '--net-ri-port', '0', '--net-ro-port', '0',
                '--net-sbs-port', '0', '--net-bi-port', '0', '--net-bo-port', str(beast_port)]
    if c.get('mode_ac', True): argv.append('--modeac')
    if c['receiver'] == 'usb' and c['agc']: argv.append('--enable-agc')
    if c['error_correction'] == 0:
        argv.append('--no-fix')
    else:
        argv.append('--fix')
        if c['error_correction'] >= 2: argv.append('--fix-2bit')
    if c['latitude'] is not None:
        argv += ['--lat', str(c['latitude']), '--lon', str(c['longitude'])]
    return argv

class EventLog:
    def __init__(self, directory, mode):
        self.lines = collections.deque(maxlen=1200)
        self.lock = threading.RLock()
        self.handler = None
        self.directory = directory
        self.max_mb, self.days = 20, 3
        self._pruned = 0
        self.configure(mode)
    def set_limits(self, max_mb, days):
        with self.lock:
            self.max_mb, self.days = max_mb, days
            if self.handler:
                self.handler.maxBytes = max_mb * 1024 * 1024 // 4
                path=self.directory/'receiver.log'
                if path.exists() and path.stat().st_size>=self.handler.maxBytes:
                    self.handler.doRollover()
            self._prune()
    def _prune(self):
        now = time.time()
        candidates = []
        for path in self.directory.glob('receiver.log*'):
            if path.is_symlink() or not re.fullmatch(r'receiver\.log(?:\.\d+|\.\d{4}-\d{2}-\d{2})?', path.name):
                continue
            if path.name != 'receiver.log' and now-path.stat().st_mtime > self.days*86400:
                path.unlink(missing_ok=True)
            else:
                candidates.append(path)
        total = sum(p.stat().st_size for p in candidates)
        for path in sorted(candidates, key=lambda p:p.stat().st_mtime):
            if total <= self.max_mb*1024*1024:
                break
            if path.name != 'receiver.log':
                total -= path.stat().st_size
                path.unlink(missing_ok=True)
        self._pruned = now
    def configure(self, mode):
        with self.lock:
            if self.handler: self.handler.close()
            self.handler = None
            if mode:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.handler = logging.handlers.RotatingFileHandler(
                    self.directory/'receiver.log', maxBytes=self.max_mb*1024*1024//4, backupCount=3, encoding='utf-8')
                self._prune()
    def add(self, source, message):
        # The vendor sometimes prints keys in diagnostic messages.
        message = re.sub(r'(?i)\b[0-9a-f]{16}\b', '[sharing key redacted]', str(message))
        message = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', message).strip()[:3000]
        if not message: return
        stamp = datetime.datetime.now().isoformat(timespec='seconds')
        with self.lock:
            self.lines.append({'time':stamp, 'source':source, 'message':message})
            if self.handler:
                record = logging.LogRecord('frw', logging.INFO, '', 0, f'{stamp} [{source}] {message}', (), None)
                self.handler.handle(record)
                if time.time()-self._pruned>60:
                    self._prune()
    def snapshot(self):
        with self.lock: return list(self.lines)
    def close(self):
        with self.lock:
            if self.handler: self.handler.close(); self.handler = None

def decoder_config(c):
    values = {
        'homepos':f"{c['latitude'] or 0},{c['longitude'] or 0}", 'location':'false',
        'aircrafts':'NUL', 'airports':'NUL', 'prefer-adsb-lol':'false', 'logfile':'NUL',
        'raw-timestamps':'true', 'raw-quality':'true', 'mode-ac':str(c.get('mode_ac',True)).lower(), 'loops':0, 'freq':'1090M',
        'samplerate':c['sample_rate'], 'gain':'auto' if c['gain'] == -10 else c['gain'],
        'rtlsdr-ppm':c['ppm'], 'max-distance':round(c['range_nm']*1852),
        'agc':str(c['agc']).lower(), 'bias-t':str(c['bias_tee']).lower(),
        'DC-filter':str(c['dc_filter']).lower(), 'phase-enhance':str(c['phase_enhance']).lower(),
        'error-correct1':str(c['error_correction']>=1).lower(),
        'error-correct2':str(c['error_correction']>=2).lower(), 'cpr-trace':'false',
        'interactive-ttl':c['aircraft_ttl'], 'speech-enable':'false',
    }
    if c['latitude'] is None: values.pop('homepos')
    if c['receiver']=='usb' and c.get('bandwidth_khz'): values['rtlsdr-bandwidth']=c['bandwidth_khz']*1000
    return ''.join(f'{k}={v}\n' for k,v in values.items())

class ReceptionQuality:
    def __init__(self, config=None):
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.next_summary = self.started + 60
        self.buckets = collections.deque(maxlen=60)
        self.settings = ''
        self.reported_gain = None
        self.gain_steps = ()
        self.device_index = config['device'] if config and config['receiver'] == 'usb' else None
        if config and config['receiver'] in ('iq-file', 'usb'):
            parts = [f"{config['sample_rate']/1e6:g} MHz", f"CRC fix {config['error_correction']}",
                     f"A/C {'on' if config['mode_ac'] else 'off'}"]
            if config['receiver'] == 'usb':
                parts.extend(['requested gain auto' if config['gain']==-10 else f"requested {config['gain']:g} dB",
                              f"{config['ppm']} ppm", f"AGC {'on' if config['agc'] else 'off'}"])
                if os.name == 'nt':
                    parts.extend([f"BW {config['bandwidth_khz']} kHz" if config['bandwidth_khz'] else 'BW default',
                                  f"bias tee {'on' if config['bias_tee'] else 'off'}"])
            if os.name == 'nt':
                parts.append(f"DC filter {'on' if config['dc_filter'] else 'off'}")
                if config['sample_rate'] == 2000000:
                    parts.append(f"phase {'on' if config['phase_enhance'] else 'off'}")
            self.settings = ', '.join(parts)

    def observe(self, frame, accepted):
        second = int(time.monotonic())
        with self.lock:
            if not self.buckets or self.buckets[-1][0]!=second:
                self.buckets.append((second,collections.Counter()))
            counts = self.buckets[-1][1]
            if len(frame.data)==2:
                counts['mode_ac'] += 1
                return
            counts['mode_s'] += 1
            counts['accepted'] += int(accepted)
            if frame.corrected is not None:
                counts['measured_corrections'] += 1
                if frame.corrected: counts['corrected'] += 1
                elif accepted: counts['accepted_without_correction'] += 1
            if frame.signal:
                counts['levels'] += 1
                counts['power'] += (frame.signal/255)**2
                counts['strong'] += int(frame.signal>=181)

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            totals = collections.Counter()
            for second, bucket in self.buckets:
                if second>int(now)-60: totals.update(bucket)
            duration = min(60,max(.001,now-self.started))
            return {'seconds':int(duration),'settings':self.settings,'reported_gain':self.reported_gain,
                    'gain_steps':self.gain_steps,'device_index':self.device_index,
                    'mode_s':totals['mode_s'],'accepted':totals['accepted'],
                    'mode_ac':totals['mode_ac'],'accepted_rate':round(totals['accepted']/duration,2),
                    'clean_rate':round(totals['accepted_without_correction']/duration,2)
                        if totals['mode_s'] and totals['measured_corrections']==totals['mode_s'] else None,
                    'mean_dbfs':round(10*math.log10(totals['power']/totals['levels']),1) if totals['levels'] else None,
                    'strong_pct':round(100*totals['strong']/totals['levels'],1) if totals['levels'] else None,
                    'corrected_pct':round(100*totals['corrected']/totals['measured_corrections'],1) if totals['measured_corrections'] else None}

class Receiver:
    def __init__(self, settings, bundle):
        self.settings = settings; self.bundle = Path(bundle)
        self.directory = settings.directory
        self.log = EventLog(self.directory/'logs', settings.value['logmode'])
        self.activity = ActivityService(self.directory, self._activity_runtime)
        self.log.set_limits(self.activity.settings.value['log_max_mb'], self.activity.settings.value['log_days'])
        self.lock = threading.RLock(); self.stop_event = threading.Event()
        self.thread = None; self.process = None; self.connection = None
        self.state = 'stopped'; self.error = ''; self.started = None
        self.aircraft = Aircraft(settings.value); self.outputs = {}; self.feed_stream = None
        self.last_frame = None; self.frames = 0
        self.capture = None
        self.quality = ReceptionQuality()
        self.feeder = Feeder(self)
    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive(): raise ConfigError('The receiver is already running.')
            if self.feeder.signup_process and self.feeder.signup_process.poll() is None: raise ConfigError('Finish or cancel registration before starting reception.')
            c = dict(self.settings.value)
            if c['receiver'] in ('usb','iq-file'):
                decoder_executable(self.bundle)
            if c['receiver'] == 'iq-file' and not Path(c['iq_file']).is_file():
                raise ConfigError('Choose an existing unsigned 8-bit I/Q recording (.uc8).')
            if c['feed_enabled'] and not self.settings.key():
                raise ConfigError('Enter your Flightradar24 sharing key, or turn off uploading for local reception.')
            self.stop_event.clear(); self.error = ''; self.frames = 0; self.last_frame = time.time()
            self.aircraft = Aircraft(c, self.activity.submit); self.outputs = {}
            self.thread = None
            self.capture = None
            try:
                self.activity.start(c)
                self.capture = MessageCapture(self.directory,c['capture_mb'],c['receiver'])
                for name in ('raw','bs','beast'):
                    if c[name]: self.outputs[name] = Broadcast(c['bind_interface'], c[name+'_port'])
                self.feed_stream = Broadcast('127.0.0.1', 0) if c['feed_enabled'] else None
                self.feed_format = 'beast' if c['receiver'] in ('usb','iq-file','beast-tcp','beast-serial') else 'avr'
                self.state = 'starting'; self.started = time.time()
                self.thread = threading.Thread(target=self._run, args=(c,), daemon=True, name='receiver')
                self.thread.start()
                if c['feed_enabled']: self.feeder.start(c)
            except Exception:
                self.stop(); raise
            self.log.add('app', 'Receiver started: '+c['receiver'])
    def stop(self):
        self.stop_event.set()
        self.feeder.stop()
        connection = self.connection
        if connection:
            try: connection.close()
            except OSError: pass
        process = self.process
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(4)
            except subprocess.TimeoutExpired: process.kill(); process.wait(2)
        thread = self.thread
        if thread and thread.ident is not None and thread is not threading.current_thread(): thread.join(10)
        if thread and thread.is_alive():
            raise ConfigError('The receiver is still stopping. Wait before changing settings.')
        for output in self.outputs.values(): output.close()
        self.outputs = {}
        if self.feed_stream: self.feed_stream.close(); self.feed_stream = None
        self.state = 'stopped'; self.log.add('app', 'Receiver stopped.')
        self.activity.stop()
        if self.capture and (thread is None or thread.ident is None): self.capture.close()
    def _activity_runtime(self):
        monitor = self.feeder.snapshot().get('monitor') or {}
        try:
            sent_time=int(float(monitor.get('feed_last_ac_sent_time',0)))
            sent_count=int(float(monitor.get('feed_last_ac_sent_num',-1)))
        except (ValueError,TypeError,OverflowError):
            sent_time,sent_count=0,-1
        return self.frames,self.aircraft.rejected,sent_time,sent_count
    def _frame(self, frame):
        self.last_frame = time.time(); self.frames += 1
        legacy = len(frame.data)==2
        if self.capture:
            self.capture.submit(self.last_frame,'mode-ac' if legacy else 'mode-s',frame.data,
                                frame.timestamp,frame.signal,frame.corrected)
        sbs = None if legacy else self.aircraft.frame(frame)
        self.quality.observe(frame, sbs is not None)
        now = time.monotonic()
        if now >= self.quality.next_summary:
            self.quality.next_summary = now + 60
            report = self.quality.snapshot()
            totals = self.capture.status() if self.capture else {}
            self.log.add('reception-quality', json.dumps({
                'seconds':report['seconds'],'mode_s':report['mode_s'],'accepted':report['accepted'],
                'mode_ac':report['mode_ac'],'accepted_rate':report['accepted_rate'],'clean_rate':report['clean_rate'],
                'mean_dbfs':report['mean_dbfs'],'strong_pct':report['strong_pct'],'corrected_pct':report['corrected_pct'],
                'archived':totals.get('written',0),'archive_dropped':totals.get('dropped',0)
            },separators=(',',':')))
        # Forward valid and undecoded Mode-S frames; the official feeder validates its own data.
        if 'raw' in self.outputs: self.outputs['raw'].publish(frame.avr())
        if 'beast' in self.outputs: self.outputs['beast'].publish(frame.beast())
        if 'bs' in self.outputs: self.outputs['bs'].publish(sbs)
        feed_stream = self.feed_stream
        if feed_stream and not legacy:
            feed_stream.publish(frame.beast() if self.feed_format == 'beast' else frame.avr())
    def _run(self, c):
        retry = 1
        while not self.stop_event.is_set():
            try:
                self.quality = ReceptionQuality(c)
                if c['receiver'] in ('usb','iq-file'): self._native(c)
                else: self._stream(c)
                if c['receiver'] == 'iq-file':
                    self.state = 'playback complete'; break
                if not self.stop_event.is_set(): raise OSError('Receiver disconnected.')
            except Exception as e:
                if self.stop_event.is_set(): break
                self.error = str(e); self.state = 'reconnecting'; self.log.add('receiver', self.error)
                if c['receiver'] == 'iq-file': self.state = 'error'; break
                self.stop_event.wait(min(retry, 30)); retry = min(retry*2, 30)
            finally:
                if self.connection:
                    try: self.connection.close()
                    except OSError: pass
                    self.connection = None
                process = self.process
                if process and process.poll() is None:
                    process.terminate()
                    try: process.wait(3)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(2)
                self.process = None
        # Recorded playback and terminal errors must release listeners too.
        self.feeder.stop()
        outputs,self.outputs = self.outputs,{}
        for output in outputs.values(): output.close()
        stream,self.feed_stream = self.feed_stream,None
        if stream: stream.close()
        if self.capture: self.capture.close()
    def _native(self, c):
        if platform.system() == 'Linux':
            return self._native_linux(c)
        self.last_frame = time.time()
        path = self.directory/'receiver.cfg'; path.write_text(decoder_config(c), encoding='utf-8')
        argv = [str(decoder_executable(self.bundle)), '--config', str(path), '--raw']
        argv += ['--infile', c['iq_file']] if c['receiver']=='iq-file' else ['--device', c['device']]
        p = subprocess.Popen(argv, cwd=self.directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             creationflags=HIDDEN)
        self.process = p; self.state = 'receiving'; self.error = ''; parser = AVRParser()
        def watchdog():
            while p.poll() is None and not self.stop_event.wait(1):
                if c['global_timeout'] and time.time() - self.last_frame > c['global_timeout']:
                    self.log.add('receiver', 'No frames within the configured timeout; reconnecting.')
                    p.terminate(); return
        threading.Thread(target=watchdog, daemon=True).start()
        try:
            while not self.stop_event.is_set():
                line = p.stdout.readline(16384)
                if not line: break
                frames = parser.feed(line)
                if frames:
                    for frame in frames: self._frame(frame)
                elif line.strip():
                    gain = re.search(rb'Gain reported by device: (AUTO|\d+\.\d+ dB)\.', line)
                    if gain: self.quality.reported_gain = gain[1].decode()
                    steps = re.search(rb'Supported gains: ([0-9., -]+)', line)
                    if steps:
                        self.quality.gain_steps = tuple(sorted({float(value) for value in
                            re.findall(rb'-?\d+\.\d+', steps[1]) if 0 <= float(value) <= 60}))
                    self.log.add('decoder', line.decode(errors='replace'))
            rc = p.wait(4)
            if rc and not self.stop_event.is_set(): raise OSError(f'Decoder exited ({rc}). Check the receiver log and USB device permissions.')
        finally: p.stdout.close()

    def _native_linux(self, c):
        """Run FlightAware's pinned Linux decoder and ingest Beast on loopback."""
        self.last_frame = time.time()
        decoder = decoder_executable(self.bundle)
        beast_port = free_port()
        argv_config = dict(c)
        if c['receiver'] == 'iq-file': argv_config['iq_file'] = '-'
        argv = linux_decoder_argv(decoder, argv_config, beast_port)
        env = dict(os.environ)
        private_lib = self.bundle/'lib'
        if private_lib.is_dir():
            env['LD_LIBRARY_PATH'] = str(private_lib) + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
        p = subprocess.Popen(argv, cwd=self.directory, env=env, stdin=subprocess.PIPE if c['receiver']=='iq-file' else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=HIDDEN)
        self.process = p
        self.state = 'starting'
        def collect_output():
            for line in iter(p.stdout.readline, b''):
                if line.strip(): self.log.add('decoder', line.decode(errors='replace'))
        threading.Thread(target=collect_output, daemon=True, name='decoder-log').start()
        connection = None
        input_writer = None
        input_errors = []
        deadline = time.monotonic() + 15
        try:
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                if p.poll() is not None:
                    raise OSError(f'Linux decoder exited ({p.returncode}). Check the RTL-SDR USB permissions and device index.')
                try:
                    connection = socket.create_connection(('127.0.0.1', beast_port), timeout=0.5)
                    break
                except OSError:
                    self.stop_event.wait(0.15)
            if self.stop_event.is_set(): return
            if connection is None:
                raise OSError('Linux decoder did not open its loopback Beast output within 15 seconds.')
            self.connection = connection
            connection.settimeout(1)
            self.state = 'receiving'; self.error = ''
            parser = BeastParser()
            if c['receiver'] == 'iq-file':
                def write_recording():
                    try:
                        with open(c['iq_file'], 'rb') as recording:
                            shutil.copyfileobj(recording, p.stdin, length=65536)
                    except (BrokenPipeError, OSError) as error:
                        if not self.stop_event.is_set(): input_errors.append(error)
                    finally:
                        try: p.stdin.close()
                        except (BrokenPipeError, OSError, ValueError): pass
                input_writer = threading.Thread(target=write_recording, daemon=True, name='iq-file-input')
                input_writer.start()
            while not self.stop_event.is_set():
                try: data = connection.recv(16384)
                except (socket.timeout, TimeoutError): data = None
                if data == b'':
                    if c['receiver'] == 'iq-file':
                        input_writer.join(2)
                        if input_writer.is_alive():
                            raise OSError('Recorded I/Q input writer did not finish after decoder EOF.')
                        if input_errors:
                            raise OSError('Could not finish writing the recorded I/Q input.') from input_errors[0]
                        try: exit_code = p.wait(timeout=3)
                        except subprocess.TimeoutExpired: exit_code = None
                        if exit_code == 1:
                            self.state = 'playback complete'
                            input_writer.join(2)
                            return
                    raise OSError('Linux decoder Beast connection closed.')
                if data:
                    for frame in parser.feed(data): self._frame(frame)
                if input_errors:
                    raise OSError('Could not finish writing the recorded I/Q input.') from input_errors[0]
                if c['global_timeout'] and time.time()-self.last_frame > c['global_timeout']:
                    raise OSError('No frames within the configured global timeout.')
        finally:
            if connection:
                try: connection.close()
                except OSError: pass
            self.connection = None
            if p.poll() is None:
                p.terminate()
                try: p.wait(4)
                except subprocess.TimeoutExpired: p.kill(); p.wait(2)
            if p.stdout: p.stdout.close()
            if input_writer is not None: input_writer.join(2)
    def _stream(self, c):
        serial_mode = c['receiver'].endswith('serial')
        if serial_mode:
            import serial
            connection = serial.Serial(c['serial_port'], c['baudrate'], timeout=1)
        else: connection = socket.create_connection((c['host'],c['input_port']), timeout=5); connection.settimeout(1)
        self.connection = connection; self.state = 'receiving'; self.error = ''; self.last_frame = time.time()
        parser = BeastParser() if c['receiver'].startswith('beast') else AVRParser()
        buffer = bytearray()
        discard_line = False
        while not self.stop_event.is_set():
            try: data = connection.read(4096) if serial_mode else connection.recv(16384)
            except (socket.timeout, TimeoutError): data = None
            if data == b'' and not serial_mode: raise OSError('Remote receiver closed the connection.')
            if data:
                if c['receiver']=='sbs-tcp':
                    if discard_line:
                        _, end, data = data.partition(b'\n')
                        discard_line = not end
                    buffer.extend(data)
                    while b'\n' in buffer:
                        line, _, remainder = buffer.partition(b'\n'); buffer = bytearray(remainder)
                        if len(line) > 65536:
                            if self.capture: self.capture.discard()
                            continue
                        if self.capture: self.capture.submit(time.time(),'sbs',bytes(line)+b'\n')
                        self.aircraft.ingest_sbs(line.decode('ascii',errors='replace'))
                        self.last_frame = time.time(); self.frames += 1
                        if 'bs' in self.outputs: self.outputs['bs'].publish(line+b'\n')
                    if len(buffer)>65536:
                        if self.capture: self.capture.discard()
                        buffer.clear()
                        discard_line = True
                else:
                    for frame in parser.feed(data): self._frame(frame)
            if c['global_timeout'] and time.time()-self.last_frame > c['global_timeout']:
                raise OSError('No frames within the configured global timeout.')
    def snapshot(self):
        return {'state':self.state, 'error':self.error, 'started':self.started, 'frames':self.frames,
                'input':self.settings.value['receiver'], 'streams':{k:v.port for k,v in self.outputs.items()},
                'activity':self.activity.snapshot(),
                'quality':self.quality.snapshot(), 'capture':self.capture.status() if self.capture else {},
                'feeder':self.feeder.snapshot(), **self.aircraft.snapshot()}

class Feeder:
    def __init__(self, receiver):
        self.receiver = receiver; self.process = None; self.thread = None; self.stop_event = threading.Event()
        self.state = 'stopped'; self.error = ''; self.root = receiver.directory/'feeder'
        self.http_port = None; self.signup_process = None; self.signup_text = ''; self.signup_lock = threading.RLock()
        self.signup_thread = None; self.signup_master_fd = None; self.signup_config_path = None
        stale = self.root/'etc/fr24feed.ini'
        if stale.exists(): stale.unlink()
    def prepare(self, c, signup=False):
        if platform.system() == 'Linux':
            return self._prepare_linux(c, signup)
        if platform.system() != 'Windows':
            raise ConfigError('Flightradar24 feeder setup is currently available on Windows and Linux only.')
        for d in ('etc','dev/shm','var/log/fr24feed','tmp','run','bin'): (self.root/d).mkdir(parents=True,exist_ok=True)
        (self.root/'dev/shm/decoder.txt').unlink(missing_ok=True)
        prepare_trust_store(self.root)
        # The official binary is unchanged. Only its small runtime filesystem is supplied.
        origin = self.receiver.bundle/'feeder/fr24feed'
        if not origin.is_file(): raise ConfigError('Official feeder is missing from the package.')
        dest = self.root/'bin/fr24feed'
        if not dest.exists() or dest.stat().st_size != origin.stat().st_size: shutil.copyfile(origin,dest)
        probes = self.receiver.bundle/'feeder/guest-probes'
        if not probes.is_file():
            raise ConfigError('The guest service-probe helper is missing. Extract the complete package again.')
        # This exact-command dispatcher is not a general Linux shell. The guest
        # has neither a Linux cron updater nor a fa-mlat-client installation.
        shutil.copyfile(probes,self.root/'bin/bash')
        (self.root/'etc/hosts').write_text('127.0.0.1 localhost\n::1 localhost\n',encoding='ascii')
        (self.root/'etc/nsswitch.conf').write_text('hosts: files dns\n',encoding='ascii')
        # Reuse the current Windows DNS servers, with no OS configuration changes.
        try:
            result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
                'Get-DnsClientServerAddress -AddressFamily IPv4 | ForEach-Object ServerAddresses | Select-Object -Unique'],
                capture_output=True,timeout=8,creationflags=HIDDEN)
            addresses = [str(ipaddress.IPv4Address(x.strip())) for x in result.stdout.decode(errors='replace').splitlines() if x.strip()]
        except (ValueError,OSError,subprocess.TimeoutExpired): addresses = []
        if addresses: (self.root/'etc/resolv.conf').write_text(''.join('nameserver '+a+'\n' for a in addresses),encoding='ascii')
        self.http_port = free_port()
        config = {'receiver':'beast-tcp' if self.receiver.feed_format=='beast' else 'avr-tcp',
                  'host':f'127.0.0.1:{self.receiver.feed_stream.port}' if self.receiver.feed_stream else '127.0.0.1:30005',
                  'bs':'no','raw':'no','mpx':'no','bind-interface':'127.0.0.1',
                  'httpd':'no','http-listen-port':self.http_port,'logmode':'0','gt':c['global_timeout'],
                  'mlat':'yes' if c['mlat'] and self.receiver.feed_format=='beast' else 'no',
                  'mlat-without-gps':'yes' if c['mlat_without_gps'] else 'no'}
        if signup:
            # In signup mode the vendor also interprets host as its service hostname.
            # Let it use its own service default; supply the receiver address only when feeding.
            config.pop('host')
            discovery = self.receiver.bundle/'feeder/pgrep'
            if not discovery.is_file():
                raise ConfigError('The registration discovery helper is missing. Extract the complete package again.')
            (self.root/'usr/bin').mkdir(parents=True,exist_ok=True)
            shutil.copyfile(discovery,self.root/'usr/bin/pgrep')
        else: config['fr24key'] = self.receiver.settings.key()
        (self.root/'etc/fr24feed.ini').write_text(format_ini(config),encoding='utf-8')
        env = dict(os.environ)
        env['SSL_CERT_FILE'] = '/etc/ssl/certs/ca-certificates.crt'
        env['OPENSSL_CONF'] = '/etc/ssl/openssl.cnf'
        # Emulator debug logging is separate from the official feeder's captured output.
        env['BLINK_LOG_FILENAME'] = '/dev/null'
        env['PATH'] = str(self.receiver.bundle/'feeder')+';'+env.get('PATH','')
        # SystemRoot is necessary for Cygwin networking; the parent environment retains it.
        argv = [str(self.receiver.bundle/'feeder/blink.exe'),'-m','-C',cygpath(self.root)]
        if signup: argv += ['/bin/fr24feed','--signup']
        else: argv += ['/bin/fr24feed']
        return argv,env

    def _prepare_linux(self, c, signup=False):
        """Prepare the vendor's bundled native Linux ELF without the Blink layer."""
        for d in ('etc', 'dev/shm', 'var/log/fr24feed', 'tmp', 'run', 'bin'):
            (self.root/d).mkdir(parents=True, exist_ok=True)
        monitor = self.root/'dev/shm/decoder.txt'
        monitor.unlink(missing_ok=True)
        origin = self.receiver.bundle/'feeder/fr24feed'
        if not origin.is_file(): raise ConfigError('Official Linux feeder is missing from the package.')
        if not os.access(origin, os.X_OK):
            raise ConfigError('Official Linux feeder is not executable. Extract the Linux package with file permissions preserved.')
        self.http_port = free_port()
        config = {'receiver':'beast-tcp' if self.receiver.feed_format=='beast' else 'avr-tcp',
                  'host':f'127.0.0.1:{self.receiver.feed_stream.port}' if self.receiver.feed_stream else '127.0.0.1:30005',
                  'bs':'no','raw':'no','mpx':'no','bind-interface':'127.0.0.1',
                  'httpd':'no','http-listen-port':self.http_port,'logmode':'0','gt':c['global_timeout'],
                  'mlat':'yes' if c['mlat'] and self.receiver.feed_format=='beast' else 'no',
                  'mlat-without-gps':'yes' if c['mlat_without_gps'] else 'no'}
        if signup:
            config.pop('host')
        else:
            config['fr24key'] = self.receiver.settings.key()
        config_path = self.root/'etc/fr24feed.ini'
        config_path.write_text(format_ini(config), encoding='utf-8')
        os.chmod(config_path, 0o600)
        self.signup_config_path = config_path
        env = dict(os.environ)
        cert = next((p for p in ('/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt') if Path(p).is_file()), None)
        if cert: env['SSL_CERT_FILE'] = cert
        env['OPENSSL_CONF'] = '/etc/ssl/openssl.cnf'
        argv = [str(origin), f'--config-file={config_path}', f'--monitor-file={monitor}']
        if signup: argv.append('--signup')
        return argv, env
    def start(self, c):
        self.stop_event.clear(); self.error = ''
        argv,env = self.prepare(c)
        if platform.system() == 'Windows':
            terminal = self.receiver.bundle/'feeder/frw-pty.exe'
            if not terminal.is_file():
                raise ConfigError('The feeder process supervisor is missing. Extract the complete package again.')
            argv = [str(terminal),'--no-input',cygpath(Path(argv[0]))]+argv[1:]
        self.thread = threading.Thread(target=self._run,args=(argv,env),daemon=True,name='feeder')
        self.thread.start()
    def _run(self, argv, env):
        attempt = 0
        while not self.stop_event.is_set():
            p = None
            try:
                self.state = 'starting'
                # Every attempt must produce its own vendor status, including retries.
                (self.root/'dev/shm/decoder.txt').unlink(missing_ok=True)
                p = subprocess.Popen(argv,cwd=self.root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL,creationflags=HIDDEN,startupinfo=hidden_startupinfo(),
                                     start_new_session=platform.system() == 'Linux')
                self.process = p; self.state = 'running — upload unconfirmed'; self.error = ''; process_started = time.monotonic()
                while not self.stop_event.is_set():
                    line = p.stdout.readline(16384)
                    if not line: break
                    self.receiver.log.add('fr24feed',line.decode(errors='replace'))
                rc = p.wait(4); p.stdout.close()
                if self.stop_event.is_set(): break
                if time.monotonic() - process_started >= 300:
                    # Treat a long healthy session followed by a disconnect as a
                    # fresh outage; short crash loops still stop after three tries.
                    attempt = 0
                self.error = f'Official feeder exited ({rc}); see its log.'; self.state = 'error'
                self.receiver.log.add('fr24feed', self.error)
            except Exception as e:
                self.error = str(e); self.state = 'error'; self.receiver.log.add('fr24feed',self.error)
            finally:
                if p:
                    if platform.system() == 'Linux' or p.poll() is None:
                        terminate_process_tree(p)
                    p.stdout.close()
            attempt += 1
            if attempt >= 3: break
            if self.stop_event.wait(10*attempt): break
        if not self.stop_event.is_set(): self.state = 'error'
    def stop(self):
        self.stop_event.set()
        p = self.process
        if p and (platform.system() == 'Linux' or p.poll() is None):
            terminate_process_tree(p)
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(5)
        self.process = None; self.state = 'stopped'
        path = self.root/'etc/fr24feed.ini'
        path.unlink(missing_ok=True)
    def snapshot(self):
        # Monitor data comes from the official feeder. A running process is not evidence of accepted uploads.
        fields = {}
        path = self.root/'dev/shm/decoder.txt'
        if self.process and self.process.poll() is None and path.exists():
            try:
                for line in path.read_text(errors='replace')[:100000].splitlines():
                    if '=' in line:
                        k,v = line.split('=',1)
                        if re.fullmatch(r'[a-zA-Z0-9_-]+',k) and not any(x in k.lower() for x in ('key','email','token')):
                            fields[k] = v[:500]
            except OSError: pass
        state = 'connected to Flightradar24' if fields.get('feed_status') == 'connected' else self.state
        return {'state':state,'error':self.error,'monitor':fields,
                'version':'1.0.57-1', 'compatibility':'Experimental Windows runtime; verify each station using vendor monitor status and account activity.'}
    def signup_start(self):
        if self.receiver.thread and self.receiver.thread.is_alive(): raise ConfigError('Stop the receiver before registration.')
        if self.signup_process and self.signup_process.poll() is None: raise ConfigError('Registration is already open.')
        self.receiver.feed_format = 'beast'
        argv,env = self.prepare(self.receiver.settings.value,True)
        self.signup_text = ''
        if platform.system() == 'Linux':
            import pty
            master, slave = pty.openpty()
            try:
                p = subprocess.Popen(argv, cwd=self.root, env=env, stdin=slave, stdout=slave,
                                     stderr=slave, start_new_session=True)
            finally:
                os.close(slave)
            self.signup_master_fd = master
        else:
            terminal = self.receiver.bundle/'feeder/frw-pty.exe'
            if not terminal.is_file():
                raise ConfigError('The registration terminal helper is missing. Extract the complete package again.')
            argv = [str(terminal),cygpath(Path(argv[0]))]+argv[1:]
            p = subprocess.Popen(argv,cwd=self.root,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,creationflags=HIDDEN,startupinfo=hidden_startupinfo())
        self.signup_process = p
        def reader():
            saved = False
            while True:
                if self.signup_master_fd is not None:
                    try: chunk = os.read(self.signup_master_fd, 1)
                    except OSError: chunk = b''
                    b = chunk
                else:
                    b = p.stdout.read(1)
                if not b: break
                with self.signup_lock: self.signup_text = (self.signup_text+b.decode('utf-8',errors='replace'))[-30000:]
                # The vendor may wait indefinitely after saving a completed signup.
                # Closing its terminal input allows the supervisor to reap the tree.
                if not saved and re.search(r'Saving settings to /etc/fr24feed\.ini\.\.\.\s*OK', self.signup_text):
                    saved = True
                    if self.signup_master_fd is None:
                        try: p.stdin.close()
                        except (BrokenPipeError,OSError,ValueError): pass
            p.wait()
            if self.signup_master_fd is None:
                p.stdout.close(); p.stdin.close()
            path = self.signup_config_path or (self.root/'etc/fr24feed.ini')
            try:
                result = parse_ini(path.read_text(encoding='utf-8'))
                if result.get('fr24key'):
                    self.receiver.settings.save_key(result['fr24key'])
                    with self.signup_lock: self.signup_text += '\n'+key_storage_description()+'\n'
            except (OSError,ValueError): pass
            finally:
                path.unlink(missing_ok=True)
                if self.signup_master_fd is not None:
                    try: os.close(self.signup_master_fd)
                    except OSError: pass
                    self.signup_master_fd = None
        self.signup_thread = threading.Thread(target=reader,daemon=True)
        self.signup_thread.start()
    def signup_answer(self, answer):
        p = self.signup_process
        if not p or p.poll() is not None: raise ConfigError('Registration is not running.')
        if not isinstance(answer,str) or len(answer)>512 or any(c in answer for c in '\r\n\0'):
            raise ConfigError('Enter one answer, up to 512 characters.')
        if self.signup_master_fd is not None:
            os.write(self.signup_master_fd, (answer+'\n').encode('utf-8'))
        else:
            p.stdin.write((answer+'\n').encode('utf-8')); p.stdin.flush()
    def signup_snapshot(self):
        with self.signup_lock:
            text = re.sub(r'(?i)\b[0-9a-f]{16}\b','[sharing key stored securely]',self.signup_text)
        return {'running':bool(self.signup_process and self.signup_process.poll() is None),'text':text}
    def signup_stop(self):
        if self.signup_process and self.signup_process.poll() is None:
            if self.signup_master_fd is not None:
                try: os.close(self.signup_master_fd)
                except OSError: pass
                self.signup_master_fd = None
            else:
                try: self.signup_process.stdin.close()
                except (BrokenPipeError,OSError,ValueError): pass
            try: self.signup_process.wait(4)
            except subprocess.TimeoutExpired:
                terminate_process_tree(self.signup_process)
        if self.signup_thread: self.signup_thread.join(4)
        self.signup_process = None
