from pathlib import Path
import io, json, subprocess, sys, tempfile, threading, unittest, urllib.error, urllib.request, zipfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'work/applibs')]
from frw.config import Settings, validate
from frw.control import Controller
from frw.runtime import Receiver
from frw.server import ControlServer


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.temp.name))
        self.receiver = Receiver(self.settings, ROOT/'work/runtime')
        self.shutdowns = []
        self.controller = Controller(self.receiver, lambda: self.shutdowns.append('shutdown'))

    def tearDown(self):
        self.receiver.feeder.signup_stop()
        self.receiver.stop()
        self.receiver.log.close()
        self.temp.cleanup()

    def test_native_settings_errors_and_live_mutation_guard(self):
        initial = self.settings.value.copy()
        invalid = self.controller.execute('/api/settings', 'POST', {'settings': {'gain': float('nan')}})
        self.assertEqual(invalid.status, 400)
        self.assertEqual(self.settings.value, initial)
        running = threading.Event()
        release = threading.Event()
        def worker():
            running.set()
            release.wait(3)
        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(running.wait(1))
        self.receiver.thread = thread
        try:
            denied = self.controller.execute('/api/settings', 'POST', {'settings': {'gain': 32}})
            self.assertEqual(denied.status, 400)
            self.assertIn('Stop the receiver', denied.data['error'])
            self.assertEqual(self.settings.value, initial)
        finally:
            release.set()
            thread.join(3)
            self.receiver.thread = None
        saved = self.controller.execute('/api/settings', 'POST', {'settings': {'gain': 32}})
        self.assertEqual(saved.status, 200)
        self.assertEqual(Settings(Path(self.temp.name)).value['gain'], 32)
        self.assertEqual(self.controller.execute('/api/settings').data['gain'], 32)

    def test_linux_device_scan_uses_usb_test_tool_without_touching_running_receiver(self):
        tool = self.receiver.bundle/'tools/rtl_test'
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.touch()
        self.receiver.bundle.mkdir(parents=True, exist_ok=True)
        result = subprocess.CompletedProcess([], 1, b'', b'Failed to open device #1.')
        banner_only = subprocess.TimeoutExpired(['rtl_test'], 1, output=b'Using device 0: RTL2838UHIDIR')
        with patch('frw.control.platform.system', return_value='Linux'), \
             patch('frw.control.subprocess.run', side_effect=[banner_only]) as run:
            scanned = self.controller.execute('/api/devices').data
            self.assertEqual(scanned['devices'], [])
            self.assertIn('timed out before confirming', scanned['error'])
            self.assertEqual(run.call_count, 1)
        opened = subprocess.TimeoutExpired(['rtl_test'], 1,
                                           output=b'Using device 0: RTL2838UHIDIR\nSupported gain values (29):')
        with patch('frw.control.platform.system', return_value='Linux'), \
             patch('frw.control.subprocess.run', side_effect=[opened, result]) as run:
            scanned = self.controller.execute('/api/devices').data
            self.assertEqual(scanned['devices'][0]['index'], '0')
            self.assertIn('RTL2838UHIDIR', scanned['devices'][0]['FriendlyName'])
            active = threading.Event(); release = threading.Event()
            thread = threading.Thread(target=lambda: (active.set(), release.wait(2)))
            thread.start(); self.assertTrue(active.wait(1))
            self.receiver.thread = thread
            blocked = self.controller.execute('/api/devices').data
            self.assertIn('Stop the receiver', blocked['error'])
            self.assertEqual(run.call_count, 2)
            release.set(); thread.join(2); self.receiver.thread = None

    def test_import_preview_export_and_diagnostics_do_not_expose_key_or_location(self):
        key = 'a123456789abcdef'
        text = 'receiver="dvbt"\nfr24key="'+key+'"\n'
        preview = self.controller.execute('/api/import', 'POST', {'text': text})
        self.assertEqual(preview.status, 200)
        self.assertTrue(preview.data['has_imported_key'])
        self.assertNotIn(key, json.dumps(preview.data))
        self.assertFalse(self.settings.key_path.exists())
        self.assertFalse(self.settings.path.exists())
        applied = self.controller.execute('/api/import/apply', 'POST', {'text': text})
        self.assertEqual(applied.status, 200)
        self.assertEqual(self.settings.key(), key)
        self.assertNotIn(key.encode(), self.settings.key_path.read_bytes())
        values = validate({**self.settings.value, 'latitude': -34.912345, 'longitude': 138.612345})
        self.settings.save(values)
        exported = self.controller.execute('/api/export')
        self.assertEqual(exported.filename, 'fr24feed.ini')
        self.assertEqual(exported.mime, 'text/plain; charset=utf-8')
        self.assertNotIn(key, exported.data)
        self.receiver.log.add('test', 'Do not export log content '+key)
        diagnostic = self.controller.execute('/api/diagnostics')
        self.assertEqual((diagnostic.status, diagnostic.mime), (200, 'application/zip'))
        with zipfile.ZipFile(io.BytesIO(diagnostic.data)) as archive:
            contents = b''.join(archive.read(name) for name in archive.namelist())
            for secret in (key, '-34.912345', '138.612345', 'Do not export log content'):
                self.assertNotIn(secret.encode(), contents)

    def test_pi24_profile_roundtrip_does_not_activate_vendor_direct_usb(self):
        document = ('receiver="dvbt"\npath="/usr/lib/fr24/dump1090"\nlogmode="2"\n'
                    'mlat="no"\nmlat-without-gps="no"\nwindowmode="1"\n'
                    'procargs="--gain 19.7 --net"\nraw="yes"\nfr24key="0123456789abcdef"\n')
        self.settings.value.update(receiver='beast-tcp', bind_interface='0.0.0.0', gain=20.7)
        preview = self.controller.execute('/api/import', 'POST', {'text': document})
        self.assertEqual(preview.status, 200)
        self.assertEqual(preview.data['settings']['feed_mode'], 'relay')
        self.assertFalse(self.settings.path.exists())
        self.assertNotIn('0123456789abcdef', repr(preview.data))
        applied = self.controller.execute('/api/import/apply', 'POST', {'text': document})
        self.assertEqual(applied.status, 200)
        self.assertEqual(self.settings.value['gain'], 20.7)
        self.assertEqual(self.settings.value['receiver'], 'beast-tcp')
        self.assertFalse(self.settings.value['raw'])
        self.assertTrue(self.settings.value['pi24']['raw'])
        reloaded = Settings(self.settings.directory)
        self.assertEqual(reloaded.value['pi24']['windowmode'], 1)
        with patch.object(self.settings, 'key', side_effect=AssertionError('export must not read a key')):
            exported = self.controller.execute('/api/export/pi24')
        self.assertEqual(exported.status, 200)
        self.assertIn('windowmode="1"', exported.data)
        self.assertIn('mlat="no"', exported.data)
        self.assertIn('procargs="--gain 19.7 --net"', exported.data)
        self.assertNotIn('fr24key', exported.data)
        self.assertNotIn('0123456789abcdef', exported.data)

    def test_unavailable_direct_input_does_not_touch_live_state(self):
        config = {**self.settings.value, 'feed_mode': 'direct-usb'}
        result = self.controller.execute('/api/settings', 'POST', {'settings': config})
        self.assertEqual(result.status, 400)
        self.assertFalse(self.settings.path.exists())
        self.assertEqual(self.settings.value['feed_mode'], 'relay')
        self.settings.value = config
        with patch.object(self.settings, 'key', side_effect=AssertionError('unexpected credential read')):
            result = self.controller.execute('/api/start', 'POST', {})
        self.assertEqual(result.status, 400)
        self.assertIn('DVB-T', result.data['error'])
        self.assertIsNone(self.receiver.thread)
        self.assertFalse((self.receiver.feeder.root/'etc/fr24feed.ini').exists())

    def test_shutdown_is_scheduled_after_signup_and_receiver_cleanup(self):
        order = []
        cleanup_started = threading.Event()
        cleanup_release = threading.Event()
        def stop():
            order.append('receiver stopping')
            cleanup_started.set()
            cleanup_release.wait(3)
            order.append('receiver stopped')
        controller = Controller(self.receiver, lambda: order.append('shutdown'))
        results = []
        with patch.object(self.receiver.feeder, 'signup_stop', side_effect=lambda: order.append('signup stopped')), \
             patch.object(self.receiver, 'stop', side_effect=stop):
            thread = threading.Thread(target=lambda: results.append(controller.execute('/api/shutdown', 'POST')))
            thread.start()
            try:
                self.assertTrue(cleanup_started.wait(1))
                self.assertNotIn('shutdown', order)
                self.assertEqual(controller.execute('/api/status').status, 200)
            finally:
                cleanup_release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(order, ['signup stopped', 'receiver stopping', 'receiver stopped', 'shutdown'])
        self.assertEqual(results[0].status, 200)

    def test_native_and_http_use_one_operation_lock_without_blocking_status(self):
        server = ControlServer(('127.0.0.1', 0), self.receiver, ROOT/'src/frw/web')
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        entered = threading.Event()
        release = threading.Event()
        stop_called = threading.Event()
        def start():
            entered.set()
            release.wait(3)
        responses = []
        errors = []
        def http_stop():
            try:
                request = urllib.request.Request(server.url+'api/stop', data=b'{}',
                    headers={'Authorization': 'Bearer '+server.token, 'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=4) as response:
                    responses.append(json.load(response))
            except Exception as error:
                errors.append(error)
        try:
            with patch.object(self.receiver, 'start', side_effect=start), \
                 patch.object(self.receiver, 'stop', side_effect=stop_called.set):
                native_thread = threading.Thread(target=lambda: server.controller.execute('/api/start', 'POST'))
                native_thread.start()
                self.assertTrue(entered.wait(1))
                request = urllib.request.Request(server.url+'api/status', headers={'Authorization': 'Bearer '+server.token})
                with urllib.request.urlopen(request, timeout=2) as response:
                    self.assertEqual(json.load(response)['state'], 'stopped')
                http_thread = threading.Thread(target=http_stop)
                http_thread.start()
                self.assertFalse(stop_called.wait(.1))
                release.set()
                native_thread.join(3)
                http_thread.join(3)
                self.assertFalse(native_thread.is_alive())
                self.assertFalse(http_thread.is_alive())
                self.assertTrue(stop_called.is_set())
                self.assertEqual(errors, [])
                self.assertEqual(responses, [{'ok': True}])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(server.url+'api/status', timeout=2)
            self.assertEqual(caught.exception.code, 401)
        finally:
            release.set()
            server.shutdown()
            server_thread.join(3)
            server.server_close()

    def test_device_scan_uses_decoder_and_does_not_need_http(self):
        result = SimpleNamespace(stdout=b'0\tGeneric\tRTL2832U\t00000001\n', returncode=0)
        with patch('frw.control.decoder_executable', return_value=Path('dummy-decoder')), \
             patch('frw.control.subprocess.run', return_value=result) as run:
            response = self.controller.execute('/api/devices')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.data['devices'][0]['index'], '0')
        self.assertIsNone(response.data['error'])
        self.assertEqual(run.call_count, 1)

    def test_invalid_requests_return_structured_results(self):
        for path, method, data, expected in (
            ('/api/missing', 'GET', None, 404),
            ('/api/missing', 'POST', {}, 404),
            ('/api/status', 'DELETE', None, 405),
            ('/api/key', 'POST', [], 400),
            ('/api/aircraft/not-hex', 'GET', None, 400),
            ('/api/aircraft/ABCDEF', 'GET', None, 404),
        ):
            with self.subTest(path=path, method=method):
                result = self.controller.execute(path, method, data)
                self.assertEqual(result.status, expected)
                self.assertIsInstance(result.data['error'], str)


if __name__ == '__main__':
    unittest.main(verbosity=2)
