"""Launcher ownership and native-default regressions without receiver side effects."""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
launcher = importlib.import_module('launcher')


class LauncherTests(unittest.TestCase):
    def _modules(self, *, acquired=True, state=None, on_run=None):
        state = state if state is not None else {'owned': False, 'calls': []}

        class Instance:
            def __init__(self, directory):
                self.directory = directory
                state['calls'].append('instance')

            def acquire(self):
                state['calls'].append('acquire')
                state['owned'] = acquired
                return acquired

            def request_activation(self):
                state['calls'].append('activate')

            def close(self):
                state['calls'].append('instance.close')

        class Settings:
            def __init__(self, directory):
                self.directory = directory
                self.value = {'autostart_receiver': False}
                state['calls'].append('settings')
                if acquired:
                    assert state['owned'], 'profile lock must precede settings access'

        class Log:
            def close(self):
                state['calls'].append('log.close')

            def add(self, *args):
                pass

        class Feeder:
            def signup_stop(self):
                state['calls'].append('signup.stop')

        class Receiver:
            def __init__(self, settings, bundle):
                state['calls'].append('receiver')
                assert state['owned'], 'profile lock must precede receiver creation'
                self.settings = settings
                self.bundle = bundle
                self.directory = settings.directory
                self.feeder = Feeder()
                self.log = Log()

            def stop(self):
                state['calls'].append('receiver.stop')

        desktop = types.ModuleType('frw.desktop')

        def run_desktop(receiver, instance, on_ready, *, start_receiver=False):
            state['calls'].append('desktop')
            state['start_receiver'] = start_receiver
            state['saved_autostart'] = receiver.settings.value['autostart_receiver']
            on_ready()
            if on_run:
                on_run(state)
            return 0

        desktop.run_desktop = run_desktop
        return state, Instance, Settings, Receiver, desktop

    def test_update_launch_waits_for_cleanup_and_is_cancelled_on_cleanup_error(self):
        import frw.config, frw.instance, frw.runtime, frw.platform_support
        for fail in (False,True):
            state, Instance, Settings, Receiver, desktop = self._modules()
            original_run = desktop.run_desktop
            def run(receiver, *args, **kwargs):
                receiver.pending_update = {'helper':'test','request':'test'}
                if fail:
                    def stop(): raise RuntimeError('Synthetic stop failure')
                    receiver.stop = stop
                return original_run(receiver,*args,**kwargs)
            desktop.run_desktop = run
            with tempfile.TemporaryDirectory() as temporary, \
                 patch.object(frw.instance,'ProfileInstance',Instance), \
                 patch.object(frw.platform_support,'data_directory',return_value=Path(temporary)), \
                 patch.object(frw.config,'Settings',Settings), \
                 patch.object(frw.runtime,'Receiver',Receiver), \
                 patch.dict(sys.modules,{'frw.desktop':desktop}), \
                 patch('frw.updates.launch_update',side_effect=lambda _:state['calls'].append('update')) as launch:
                if fail:
                    with self.assertRaises(RuntimeError): launcher.main([])
                    launch.assert_not_called()
                else:
                    self.assertEqual(launcher.main([]),0)
                    self.assertGreater(state['calls'].index('update'),state['calls'].index('instance.close'))
                    self.assertGreater(state['calls'].index('update'),state['calls'].index('receiver.stop'))

    def test_default_is_native_and_profile_lock_precedes_profile_access(self):
        state, Instance, Settings, Receiver, desktop = self._modules()
        import frw.config, frw.instance, frw.runtime, frw.platform_support
        original_import = __import__

        def no_http_default(name, *args, **kwargs):
            if name == 'frw.server':
                raise AssertionError('native launch must not import the HTTP server')
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(frw.instance, 'ProfileInstance', Instance), \
             patch.object(frw.platform_support, 'data_directory', return_value=Path(temporary)), \
             patch.object(frw.config, 'Settings', Settings), \
             patch.object(frw.runtime, 'Receiver', Receiver), \
             patch.dict(sys.modules, {'frw.desktop': desktop}), \
             patch('builtins.__import__', side_effect=no_http_default):
            result = launcher.main([])

        self.assertEqual(result, 0)
        self.assertLess(state['calls'].index('acquire'), state['calls'].index('settings'))
        self.assertLess(state['calls'].index('acquire'), state['calls'].index('receiver'))
        self.assertIn('desktop', state['calls'])
        self.assertIn('receiver.stop', state['calls'])
        self.assertIn('instance.close', state['calls'])

    def test_start_receiver_is_one_launch_only_and_preserves_saved_preference(self):
        state, Instance, Settings, Receiver, desktop = self._modules()
        import frw.config, frw.instance, frw.runtime, frw.platform_support
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(frw.instance, 'ProfileInstance', Instance), \
             patch.object(frw.platform_support, 'data_directory', return_value=Path(temporary)), \
             patch.object(frw.config, 'Settings', Settings), \
             patch.object(frw.runtime, 'Receiver', Receiver), \
             patch.dict(sys.modules, {'frw.desktop': desktop}):
            self.assertEqual(launcher.main(['--start-receiver']), 0)
        self.assertTrue(state['start_receiver'])
        self.assertFalse(state['saved_autostart'])

    def test_duplicate_instance_only_requests_activation_and_preserves_owner_session(self):
        state, Instance, Settings, Receiver, desktop = self._modules(acquired=False)
        import frw.config, frw.instance, frw.runtime, frw.platform_support
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / 'session.json'
            original = {'pid': 777, 'mode': 'desktop', 'ready': True}
            session.write_text(json.dumps(original), encoding='utf-8')
            with patch.object(frw.instance, 'ProfileInstance', Instance), \
                 patch.object(frw.platform_support, 'data_directory', return_value=Path(temporary)), \
                 patch.object(frw.config, 'Settings', Settings), \
                 patch.object(frw.runtime, 'Receiver', Receiver), \
                 patch.dict(sys.modules, {'frw.desktop': desktop}):
                self.assertEqual(launcher.main(['--session-file', str(session)]), 0)
            self.assertEqual(json.loads(session.read_text(encoding='utf-8')), original)
        self.assertEqual(state['calls'], ['instance', 'acquire', 'activate', 'instance.close'])

    def test_cleanup_does_not_remove_a_session_replaced_by_another_owner(self):
        state = {'owned': False, 'calls': []}
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / 'session.json'

            def replace_session(_state):
                # Runs after this launcher publishes its ready record.
                session.write_text(json.dumps({'pid': 12345, 'mode': 'desktop', 'ready': True}), encoding='utf-8')

            state, Instance, Settings, Receiver, desktop = self._modules(state=state, on_run=replace_session)
            import frw.config, frw.instance, frw.runtime, frw.platform_support
            with patch.object(frw.instance, 'ProfileInstance', Instance), \
                 patch.object(frw.platform_support, 'data_directory', return_value=Path(temporary)), \
                 patch.object(frw.config, 'Settings', Settings), \
                 patch.object(frw.runtime, 'Receiver', Receiver), \
                 patch.dict(sys.modules, {'frw.desktop': desktop}):
                launcher.main(['--session-file', str(session)])
            self.assertEqual(json.loads(session.read_text(encoding='utf-8'))['pid'], 12345)


if __name__ == '__main__':
    unittest.main()
