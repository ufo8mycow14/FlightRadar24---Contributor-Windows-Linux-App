"""Recovery state checks with mocked child processes and isolated temp files."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'work/applibs')]
from frw.runtime import Feeder, Receiver, hidden_startupinfo


class RuntimeRecoveryTests(unittest.TestCase):
    def test_windows_decoder_recovery_clears_prior_error(self):
        with tempfile.TemporaryDirectory() as directory:
            receiver = Receiver.__new__(Receiver)
            receiver.directory = Path(directory)
            receiver.bundle = Path(directory) / 'bundle'
            receiver.stop_event = threading.Event()
            receiver.state = 'reconnecting'
            receiver.error = 'Previous decoder failure'
            receiver.log = SimpleNamespace(add=lambda *_args: None)
            process = Mock()
            process.wait.return_value = 0

            def observe_recovered_reader(_limit):
                self.assertEqual(receiver.state, 'receiving')
                self.assertEqual(receiver.error, '')
                return b''

            process.stdout.readline.side_effect = observe_recovered_reader
            with patch('frw.runtime.platform.system', return_value='Windows'), \
                 patch('frw.runtime.decoder_executable', return_value=Path('dump1090.exe')), \
                 patch('frw.runtime.decoder_config', return_value='synthetic config'), \
                 patch('frw.runtime.subprocess.Popen', return_value=process), \
                 patch('frw.runtime.threading.Thread'):
                receiver._native({'receiver': 'usb', 'device': '0', 'global_timeout': 0})
            process.stdout.close.assert_called_once()

    def test_feeder_successful_retry_clears_previous_attempt_error(self):
        with tempfile.TemporaryDirectory() as directory:
            receiver = SimpleNamespace(directory=Path(directory), log=Mock())
            feeder = Feeder(receiver)
            process = Mock()
            process.wait.return_value = 0
            process.poll.return_value = 0

            def observe_recovered_reader(_limit):
                self.assertEqual(feeder.state, 'running — upload unconfirmed')
                self.assertEqual(feeder.error, '')
                feeder.stop_event.set()
                return b''

            process.stdout.readline.side_effect = observe_recovered_reader
            with patch('frw.runtime.platform.system', return_value='Windows'), \
                 patch('frw.runtime.subprocess.Popen', side_effect=[OSError('Synthetic launch failure'), process]) as launch, \
                 patch.object(feeder.stop_event, 'wait', return_value=False) as retry_wait:
                feeder._run(['synthetic-feeder'], {})
            self.assertEqual(launch.call_count, 2)
            if os.name == 'nt':
                for call in launch.call_args_list:
                    self.assert_hidden_console(call.kwargs)
            retry_wait.assert_called_once_with(10)
            receiver.log.add.assert_called_once_with('fr24feed', 'Synthetic launch failure')
            self.assertEqual(feeder.error, '')

    def assert_hidden_console(self, options):
        self.assertTrue(options['creationflags'] & subprocess.CREATE_NO_WINDOW)
        startup = options['startupinfo']
        self.assertTrue(startup.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(startup.wShowWindow, subprocess.SW_HIDE)

    @unittest.skipUnless(os.name == 'nt', 'Windows process startup contract')
    def test_signup_helper_is_hidden_without_losing_redirected_io(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            terminal = root / 'bundle/feeder/frw-pty.exe'
            terminal.parent.mkdir(parents=True)
            terminal.write_bytes(b'synthetic helper')
            receiver = SimpleNamespace(directory=root, bundle=root / 'bundle', thread=None,
                                       settings=SimpleNamespace(value={}))
            feeder = Feeder(receiver)
            process = Mock()
            with patch.object(feeder, 'prepare', return_value=(['synthetic-blink.exe'], {})), \
                 patch('frw.runtime.platform.system', return_value='Windows'), \
                 patch('frw.runtime.cygpath', return_value='/synthetic-blink.exe'), \
                 patch('frw.runtime.subprocess.Popen', return_value=process) as launch, \
                 patch('frw.runtime.threading.Thread'):
                feeder.signup_start()
            options = launch.call_args.kwargs
            self.assert_hidden_console(options)
            self.assertEqual(options['stdin'], subprocess.PIPE)
            self.assertEqual(options['stdout'], subprocess.PIPE)
            self.assertEqual(options['stderr'], subprocess.STDOUT)
            self.assertIs(feeder.signup_process, process)

    def test_non_windows_helpers_have_no_windows_startup_info(self):
        with patch('frw.runtime.os.name', 'posix'):
            self.assertIsNone(hidden_startupinfo())


if __name__ == '__main__':
    unittest.main(verbosity=2)
