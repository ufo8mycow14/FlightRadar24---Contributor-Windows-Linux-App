"""Native controls against an in-memory controller; no device/profile access."""
from pathlib import Path
import copy
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'work/desktoplibs'), str(ROOT / 'work/applibs')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLineEdit
except ImportError as error:
    raise unittest.SkipTest('Native Qt dependencies are not installed.') from error
from frw.config import DEFAULT
from frw.native_pages import GROUPS, NativePages


class MockController:
    def __init__(self):
        self.settings = dict(copy.deepcopy(DEFAULT), has_key=False)
        self.signup = {'running': False, 'text': ''}
        self.pending = []
        self.defer = False

    def invoke(self, path, data, callback):
        if path == '/api/settings':
            if data is None:
                value = copy.deepcopy(self.settings)
            else:
                self.settings.update(copy.deepcopy(data['settings']))
                value = {'ok': True}
        elif path == '/api/devices':
            value = {'devices': [{'FriendlyName': 'Test receiver', 'Status': 'ready',
                                   'InstanceId': 'synthetic', 'index': '2'}]}
        elif path == '/api/import':
            value = {'settings': dict(self.settings, receiver='beast-tcp'),
                     'has_imported_key': False, 'warnings': ['Synthetic warning']}
        elif path == '/api/import/apply':
            self.settings['receiver'] = 'beast-tcp'
            value = {'ok': True, 'warnings': []}
        elif path == '/api/key':
            self.settings['has_key'] = bool(data['key'])
            value = {'ok': True}
        elif path == '/api/signup/start':
            self.signup.update(running=True, text='Email:')
            value = {'ok': True}
        elif path == '/api/signup/answer':
            self.signup['text'] = 'Location:'
            value = {'ok': True}
        elif path == '/api/signup/stop':
            self.signup['running'] = False
            value = {'ok': True}
        elif path == '/api/signup':
            value = dict(self.signup)
        elif path == '/api/logs':
            value = [{'time': 'now', 'source': 'test', 'message': 'Synthetic log'}]
        else:
            value = {'ok': True}
        if self.defer:
            self.pending.append((callback, value))
        else:
            QTimer.singleShot(0, lambda: callback(value, None))

    def complete(self, index=0, error=None):
        callback, value = self.pending.pop(index)
        callback(value if error is None else None, error)


class NativePagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = MockController()
        self.pages = NativePages(self.controller.invoke)
        self.pages.pause_polling()

    def drain(self):
        for _ in range(8):
            self.app.processEvents()

    def tearDown(self):
        self.pages.stop_polling()
        self.pages.close()
        self.pages.deleteLater()
        self.drain()

    def edit(self, key, text):
        self.pages.fields[key].setText(text)
        self.pages._edited(key)

    def test_quality_baseline_requires_active_full_minute_and_survives_stop(self):
        quality = dict(seconds=59,mode_s=10,accepted=9,mode_ac=1,accepted_rate=.2)
        status = dict(state='receiving',input='usb',quality=quality)
        self.pages.update_status(status)
        self.assertFalse(self.pages.baseline_button.isEnabled())
        quality['seconds'] = 60
        self.pages.update_status(status)
        self.pages.baseline_button.click()
        self.assertIn('1 rejected', self.pages.baseline_summary.text())
        self.assertIn('unavailable', self.pages.baseline_summary.text())
        self.pages.update_status(dict(state='stopped'))
        self.assertFalse(self.pages.baseline_button.isEnabled())
        self.assertIn('0.2/s', self.pages.baseline_summary.text())
        self.pages.update_status(dict(state='receiving',input='sbs-tcp'))
        self.assertIn('decoded text', self.pages.quality_summary.text())

    def test_fields_defaults_password_and_input_visibility(self):
        self.assertEqual(set(self.pages.fields), {key for keys in GROUPS.values() for key in keys})
        self.assertTrue(all(self.pages._value(key) == DEFAULT[key] for key in self.pages.fields))
        self.assertEqual(self.pages.key_edit.echoMode(), QLineEdit.EchoMode.Password)
        combo = self.pages.fields['receiver']
        combo.setCurrentIndex(combo.findData('beast-tcp'))
        self.assertTrue(self.pages._rows['host'][0].isRowVisible(self.pages._rows['host'][1]))
        self.assertFalse(self.pages._rows['device'][0].isRowVisible(self.pages._rows['device'][1]))

    def test_unsaved_edit_survives_settings_refresh(self):
        self.edit('host', 'draft.example')
        self.pages.update_settings(dict(DEFAULT, host='saved.example'))
        self.assertEqual(self.pages.fields['host'].text(), 'draft.example')
        self.assertEqual(self.pages.config['host'], 'saved.example')

    def test_save_reload_and_running_state_controls(self):
        self.edit('host', '127.0.0.2')
        button = self.pages._protected_buttons[0]
        self.pages._save(('receiver', 'tuning', 'location'), button)
        self.drain()
        self.assertEqual(self.controller.settings['host'], '127.0.0.2')
        self.assertNotIn('host', self.pages._dirty)
        self.pages.update_status({'state': 'receiving', 'feeder': {'state': 'running',
            'monitor': {'feed_status': 'connected', 'mlat-ok': 'NOT-PERMITTED'}}})
        self.assertFalse(button.isEnabled())
        self.assertFalse(self.pages.start_button.isEnabled())
        self.assertTrue(self.pages.stop_button.isEnabled())
        self.assertIn('Not permitted by service', self.pages.feeder_summary.text())

    def test_usb_selection(self):
        self.pages._scan(self.pages.scan_button)
        self.drain()
        self.pages.device_list.setCurrentRow(0)
        self.pages._use_device()
        self.assertEqual(self.pages._value('device'), '2')
        self.assertEqual(self.pages._value('receiver'), 'usb')

    def test_import_requires_current_preview_and_clears_text(self):
        self.pages.ini_text.setPlainText('receiver=beast-tcp')
        self.pages._preview_import(self.pages.preview_button)
        self.drain()
        self.assertTrue(self.pages.apply_button.isEnabled())
        self.pages.ini_text.appendPlainText('host=localhost:30005')
        self.assertFalse(self.pages.apply_button.isEnabled())
        self.pages._preview_import(self.pages.preview_button)
        self.drain()
        self.pages._apply_import(self.pages.apply_button)
        self.drain()
        self.assertFalse(self.pages.ini_text.toPlainText())
        self.assertEqual(self.pages._value('receiver'), 'beast-tcp')
        self.assertFalse(self.pages._dirty)

    def test_key_and_official_wizard(self):
        self.pages.key_edit.setText('synthetic-test-key')
        self.pages._save_key(self.pages.key_save_button)
        self.drain()
        self.assertFalse(self.pages.key_edit.text())
        self.assertTrue(self.pages.has_key)
        self.pages._start_signup(self.pages.register_button)
        self.drain()
        self.assertTrue(self.pages.signup_answer.isEnabled())
        self.assertEqual(self.pages.signup_text.toPlainText(), 'Email:')
        self.pages.signup_answer.setText('synthetic answer')
        self.pages._answer_signup()
        self.drain()
        self.assertFalse(self.pages.signup_answer.text())
        self.assertEqual(self.pages.signup_text.toPlainText(), 'Location:')
        self.pages._stop_signup(self.pages.cancel_signup_button)
        self.drain()
        self.assertFalse(self.pages.signup_answer.isEnabled())
        self.assertFalse(self.pages._signup_open)

    def test_save_preserves_new_invalid_draft_and_blocks_competing_saves(self):
        self.controller.defer = True
        self.edit('latitude', '1.25')
        self.edit('longitude', '2.5')
        button = self.pages._protected_buttons[0]
        self.pages._save(('receiver', 'tuning', 'location'), button)
        self.assertTrue(all(not item.isEnabled() for item in self.pages._protected_buttons))
        self.edit('latitude', '-')
        self.controller.complete()
        self.controller.complete()
        self.assertEqual(self.pages.fields['latitude'].text(), '-')
        self.assertEqual(self.pages.config['latitude'], 1.25)
        self.assertIn('latitude', self.pages._dirty)
        self.assertTrue(button.isEnabled())

    def test_stale_settings_and_preview_responses_are_ignored(self):
        self.controller.defer = True
        self.controller.settings['host'] = 'old.example'
        self.pages.refresh_settings()
        self.pages._request('/api/key', {'key': 'synthetic'}, button=self.pages.key_save_button)
        self.controller.complete()
        self.assertNotEqual(self.pages.config['host'], 'old.example')
        self.controller.complete()
        self.pages.ini_text.setPlainText('receiver=usb')
        self.pages._preview_import(self.pages.preview_button)
        self.pages.ini_text.setPlainText('receiver=beast-tcp')
        self.controller.complete()
        self.assertFalse(self.pages.apply_button.isEnabled())
        self.assertIsNone(self.pages._import_preview)

    def test_error_and_shutdown_pause_keep_controls_usable(self):
        self.controller.defer = True
        self.pages._request('/api/key', {'key': 'synthetic'}, button=self.pages.key_save_button)
        self.pages.pause_polling()
        self.controller.complete(error=ValueError('Synthetic failure'))
        self.assertTrue(self.pages.key_save_button.isEnabled())
        self.assertEqual(self.pages.banner.text(), 'Synthetic failure')
        self.assertFalse(self.pages._closed)
        self.pages.resume_polling()
        self.assertTrue(self.pages._timer.isActive())
        self.pages.refresh_logs()
        self.pages.pause_polling()
        self.controller.complete()
        self.assertIn('Synthetic log', self.pages.log_output.toPlainText())
        self.pages.stop_polling()
        self.pages.resume_polling()
        self.assertFalse(self.pages._timer.isActive())


if __name__ == '__main__':
    unittest.main()
