"""Pi24 template controls stay separate from the active Windows relay."""
from pathlib import Path
import copy
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'work/desktoplibs'), str(ROOT / 'work/applibs')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
except ImportError as error:
    raise unittest.SkipTest('Native Qt dependencies are not installed.') from error

from frw.config import DEFAULT
from frw.native_pages import NativePages


class MockController:
    def __init__(self):
        self.settings = copy.deepcopy(DEFAULT)
        self.settings.update(feed_enabled=True, feed_mode='relay')
        self.calls = []

    def invoke(self, path, data, callback):
        self.calls.append((path, copy.deepcopy(data)))
        if path == '/api/settings' and data:
            self.settings = copy.deepcopy(data['settings'])
            value = {'ok': True}
        elif path == '/api/settings':
            value = copy.deepcopy(self.settings)
            value['has_key'] = False
        elif path == '/api/export/pi24':
            value = 'receiver="dvbt"\nmlat="yes"\n'
        else:
            value = {'ok': True}
        QTimer.singleShot(0, lambda: callback(value, None))


class Pi24PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = MockController()
        self.pages = NativePages(self.controller.invoke)
        self.pages.pause_polling()

    def tearDown(self):
        self.pages.stop_polling()
        self.pages.close()
        self.pages.deleteLater()
        self.app.processEvents()

    def drain(self):
        for _ in range(8):
            self.app.processEvents()

    def test_direct_mode_is_unavailable_and_relay_remains_selected(self):
        self.pages.update_settings(self.controller.settings, force=True)
        self.assertFalse(self.pages._dirty)
        self.assertEqual(self.pages.feed_mode.currentData(), 'relay')
        self.assertFalse(self.pages.feed_mode.model().item(1).isEnabled())
        self.assertTrue(self.controller.settings['feed_enabled'])
        self.assertIn('saved as a template', self.pages.pi24_state.text())

    def test_saving_pi24_template_keeps_active_relay(self):
        self.pages.update_settings(self.controller.settings, force=True)
        self.pages.pi24_fields['bs'].setChecked(True)
        self.pages.pi24_logmode.setCurrentIndex(self.pages.pi24_logmode.findData(2))
        self.pages.pi24_procargs.setText('--gain 48 --net')
        self.pages._save_pi24(self.pages.pi24_save_button)
        self.drain()
        saved = self.controller.settings
        self.assertEqual(saved['feed_mode'], 'relay')
        self.assertEqual(saved['receiver'], 'usb')
        self.assertTrue(saved['feed_enabled'])
        self.assertEqual(saved['pi24']['procargs'], '--gain 48 --net')
        self.assertTrue(saved['pi24']['bs'])
        self.assertEqual(saved['pi24']['logmode'], 2)
        self.assertEqual(self.pages.feed_mode.currentData(), 'relay')

    def test_unsafe_decoder_arguments_are_rejected_before_save(self):
        self.pages.pi24_procargs.setText('--gain -10 && whoami')
        self.pages._save_pi24(self.pages.pi24_save_button)
        self.assertFalse(any(path == '/api/settings' and data for path, data in self.controller.calls))
        self.assertTrue(self.pages.banner.property('error'))

    def test_copy_pi24_gain_only_edits_unsaved_windows_receiver_setting(self):
        self.pages.pi24_procargs.setText('--gain 48 --net')
        self.pages._apply_pi24_gain()
        self.assertEqual(self.pages._value('gain'), 48.0)
        self.assertIn('gain', self.pages._dirty)
        self.assertFalse(any(path == '/api/settings' and data for path, data in self.controller.calls))

    def test_apply_pi24_values_copies_only_supported_controls_without_saving(self):
        self.pages.update_settings(self.controller.settings, force=True)
        self.pages.fields['device'].setText('1')
        self.pages._edited('device')
        original = {key: self.pages._value(key) for key in ('device', 'ppm', 'bias_tee', 'raw_port', 'bs_port')}
        self.pages.pi24_procargs.setText('--gain 48 --net')
        self.pages.pi24_fields['raw'].setChecked(True)
        self.pages.pi24_fields['bs'].setChecked(True)
        self.pages.pi24_logmode.setCurrentIndex(self.pages.pi24_logmode.findData(2))

        self.pages._apply_pi24_windows_settings()

        self.assertEqual(self.pages._value('gain'), 48.0)
        self.assertTrue(self.pages._value('raw'))
        self.assertTrue(self.pages._value('bs'))
        self.assertEqual(self.pages._value('logmode'), 1)
        self.assertEqual({key: self.pages._value(key) for key in original}, original)
        self.assertTrue({'gain', 'raw', 'bs', 'logmode'}.issubset(self.pages._dirty))
        self.assertFalse(any(path == '/api/settings' and data for path, data in self.controller.calls))

    def test_apply_pi24_outputs_refuses_lan_binding_without_mutating_fields(self):
        self.pages.update_settings(self.controller.settings, force=True)
        self.pages.fields['bind_interface'].setCurrentIndex(
            self.pages.fields['bind_interface'].findData('0.0.0.0'))
        self.pages.pi24_procargs.setText('--gain 42 --net')
        self.pages.pi24_fields['raw'].setChecked(True)
        before = {key: self.pages._value(key) for key in ('gain', 'raw', 'bs', 'logmode')}

        self.pages._apply_pi24_windows_settings()

        self.assertEqual({key: self.pages._value(key) for key in before}, before)
        self.assertTrue(self.pages.banner.property('error'))
        self.assertIn('This PC only', self.pages.banner.text())
        self.assertFalse(any(path == '/api/settings' and data for path, data in self.controller.calls))

    def test_apply_pi24_values_requires_usb_receiver(self):
        self.pages.update_settings(self.controller.settings, force=True)
        self.pages.fields['receiver'].setCurrentIndex(
            self.pages.fields['receiver'].findData('beast-tcp'))
        self.pages.pi24_procargs.setText('--gain 42 --net')
        before = {key: self.pages._value(key) for key in ('gain', 'raw', 'bs', 'logmode')}

        self.pages._apply_pi24_windows_settings()

        self.assertEqual({key: self.pages._value(key) for key in before}, before)
        self.assertTrue(self.pages.banner.property('error'))
        self.assertFalse(any(path == '/api/settings' and data for path, data in self.controller.calls))

    def test_page_exposes_all_pi24_sample_values(self):
        values = self.controller.settings['pi24']
        self.pages.update_settings(self.controller.settings, force=True)
        self.assertEqual(self.pages.pi24_logmode.currentData(), values['logmode'])
        self.assertEqual(self.pages.pi24_windowmode.currentData(), values['windowmode'])
        self.assertEqual(self.pages.pi24_path.text(), values['path'])
        self.assertEqual(self.pages.pi24_procargs.text(), values['procargs'])
        self.assertEqual(self.pages.pi24_fields['mpx'].isChecked(), values['mpx'])

    def test_pi24_export_uses_saved_profile_endpoint_without_key(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'fr24feed.ini'
            with patch('frw.native_pages.QFileDialog.getSaveFileName', return_value=(str(target), '')):
                self.pages._export('/api/export/pi24', 'Export saved Pi24 profile',
                                   'fr24feed.ini', 'INI files (*.ini)', self.pages.pi24_export_button)
                self.drain()
            self.assertEqual(target.read_text(encoding='utf-8'), 'receiver="dvbt"\nmlat="yes"\n')
            self.assertTrue(any(path == '/api/export/pi24' for path, _ in self.controller.calls))
            self.assertNotIn('fr24key', target.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
