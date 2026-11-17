from pathlib import Path
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from frw.platform_support import data_directory, is_linux_x64, rtl_sdr_udev_rules
from frw.runtime import ConfigError, Feeder, decoder_executable, linux_decoder_argv
from frw.config import DEFAULT, ConfigError, validate


class PlatformSupportTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX path semantics require a Linux test host')
    def test_linux_xdg_profile_path(self):
        with patch('frw.platform_support.platform.system', return_value='Linux'), \
             patch.dict(os.environ, {'XDG_DATA_HOME': '/tmp/fr24-test-data'}):
            self.assertEqual(data_directory(), Path('/tmp/fr24-test-data/FlightRadarWindows'))

    def test_windows_and_macos_profile_paths(self):
        with patch('frw.platform_support.platform.system', return_value='Windows'), \
             patch.dict(os.environ, {'LOCALAPPDATA': 'C:/Users/test/AppData/Local'}):
            self.assertEqual(data_directory(), Path('C:/Users/test/AppData/Local/FlightRadarWindows'))
        with patch('frw.platform_support.platform.system', return_value='Darwin'), \
             patch('frw.platform_support.Path.home', return_value=Path('/Users/test')):
            self.assertEqual(data_directory(), Path('/Users/test/Library/Application Support/FlightRadarWindows'))

    def test_linux_decoder_does_not_accept_windows_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            decoder = Path(directory) / 'decoder'
            decoder.mkdir()
            (decoder / 'dump1090.exe').write_bytes(b'MZ')
            with patch('frw.runtime.platform.system', return_value='Linux'):
                with self.assertRaisesRegex(ConfigError, 'Windows decoder'):
                    decoder_executable(Path(directory))

    def test_udev_rule_is_scoped_to_known_receiver_ids(self):
        rules = rtl_sdr_udev_rules()
        self.assertIn('ATTR{idVendor}=="0bda", ATTR{idProduct}=="2832"', rules)
        self.assertIn('MODE="0660", GROUP="plugdev", TAG+="uaccess"', rules)
        self.assertNotIn('MODE="0666"', rules)

    def test_linux_decoder_argv_uses_loopback_beast_for_live_and_recorded_input(self):
        live = dict(DEFAULT)
        live.update(receiver='usb', device='1', gain=-10, ppm=3, error_correction=2,
                    latitude=-34.7, longitude=138.6)
        argv = linux_decoder_argv(Path('/opt/fr24/dump1090'), live, 39005)
        self.assertEqual(argv[0], str(Path('/opt/fr24/dump1090')))
        self.assertIn('--device-index', argv)
        self.assertEqual(argv[argv.index('--device-index') + 1], '1')
        self.assertEqual(argv[argv.index('--net-bind-address') + 1], '127.0.0.1')
        self.assertEqual(argv[argv.index('--net-bo-port') + 1], '39005')
        for option in ('--net-ri-port', '--net-ro-port', '--net-sbs-port', '--net-bi-port'):
            self.assertEqual(argv[argv.index(option) + 1], '0')
        self.assertIn('--fix-2bit', argv)
        recorded = dict(DEFAULT)
        recorded.update(receiver='iq-file', iq_file='/tmp/synthetic.uc8', error_correction=0)
        argv = linux_decoder_argv(Path('/opt/fr24/dump1090'), recorded, 39006)
        self.assertIn('--ifile', argv)
        self.assertEqual(argv[argv.index('--ifile') + 1], '/tmp/synthetic.uc8')
        self.assertIn('--throttle', argv)
        self.assertNotIn('--device-index', argv)
        self.assertIn('--no-fix', argv)

    def test_linux_native_inputs_require_decoder_sample_rate(self):
        with patch('frw.config.platform.system', return_value='Linux'):
            with self.assertRaisesRegex(ConfigError, 'fixed 2.4 MHz'):
                validate({'sample_rate': 2000000})
            self.assertEqual(validate({'sample_rate': 2400000})['sample_rate'], 2400000)

    def test_architecture_detector(self):
        with patch('frw.platform_support.platform.system', return_value='Linux'), \
             patch('frw.platform_support.platform.machine', return_value='x86_64'):
            self.assertTrue(is_linux_x64())
        with patch('frw.platform_support.platform.system', return_value='Linux'), \
             patch('frw.platform_support.platform.machine', return_value='aarch64'):
            self.assertFalse(is_linux_x64())

    def test_linux_feeder_uses_private_native_config_and_no_blink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'bundle'
            executable = bundle / 'feeder' / 'fr24feed'
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'ELF test')
            executable.chmod(0o700)
            receiver = SimpleNamespace(
                directory=root / 'profile', bundle=bundle, feed_format='beast',
                feed_stream=SimpleNamespace(port=39001), settings=SimpleNamespace(key=lambda: '0123456789abcdef'))
            (root / 'profile').mkdir()
            feeder = Feeder(receiver)
            with patch('frw.runtime.platform.system', return_value='Linux'):
                argv, env = feeder.prepare({'mlat': True, 'mlat_without_gps': True, 'global_timeout': 600})
            self.assertEqual(argv[0], str(executable))
            self.assertTrue(any(item.startswith('--config-file=') for item in argv))
            self.assertTrue(any(item.startswith('--monitor-file=') for item in argv))
            self.assertNotIn('blink.exe', ' '.join(argv).lower())
            config = (root / 'profile' / 'feeder' / 'etc' / 'fr24feed.ini').read_text()
            self.assertIn('receiver="beast-tcp"', config)
            self.assertIn('fr24key="0123456789abcdef"', config)
            if os.name != 'nt':
                self.assertEqual((root / 'profile' / 'feeder' / 'etc' / 'fr24feed.ini').stat().st_mode & 0o777, 0o600)

    def test_feeder_retry_budget_resets_after_a_long_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receiver = SimpleNamespace(directory=root, log=SimpleNamespace(add=lambda *args: None))
            feeder = Feeder(receiver)
            feeder.root.mkdir(parents=True)
            (feeder.root / 'dev' / 'shm').mkdir(parents=True)
            feeder.stop_event.clear()
            proc = Mock()
            proc.stdout.readline.return_value = b''
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            wait_count = 0
            def stop_after_three_waits(_timeout):
                nonlocal wait_count
                wait_count += 1
                if wait_count == 3:
                    feeder.stop_event.set()
                    return True
                return False
            feeder.stop_event.wait = stop_after_three_waits
            with patch('frw.runtime.subprocess.Popen', return_value=proc) as popen, \
                 patch('frw.runtime.time.monotonic', side_effect=[0, 300, 0, 300, 0, 300]):
                feeder._run(['fr24feed'], {})
            self.assertEqual(popen.call_count, 3)
            self.assertEqual(wait_count, 3)


if __name__ == '__main__':
    unittest.main()
