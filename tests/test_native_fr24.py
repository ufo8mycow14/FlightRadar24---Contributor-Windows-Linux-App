import unittest

from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from frw.native_fr24 import Fr24Dialog


class OverlayFixture:
    def __init__(self, values=None):
        self.value = {
            'enabled': False,
            'endpoint': 'full',
            'interval_seconds': 60,
            'bounds': '90,-90,-180,180',
            'limit': 20,
            'has_token': False,
        }
        if values:
            self.value.update(values)

    def preferences(self):
        return dict(self.value)

    def configure(self, settings, token=None):
        self.saved = settings, token
        has_token = bool(self.value.get('has_token')) if token is None else bool(token)
        self.value = {**settings, 'has_token': has_token}


class NativeFr24DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_defaults_are_disabled_and_api_token_is_never_prefilled(self):
        dialog = Fr24Dialog(OverlayFixture({'has_token': True}))
        self.assertFalse(dialog.enabled.isChecked())
        self.assertEqual(dialog.endpoint.currentData(), 'full')
        self.assertEqual(dialog.interval.value(), 60)
        self.assertTrue(dialog.token.isEnabled())
        self.assertEqual(dialog.token.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.token.text(), '')
        self.assertEqual(dialog.token_action(), None)
        self.assertTrue(dialog.remove_token_button.isEnabled())
        self.assertIn('9,600 credits/hour', dialog.credit_estimate.text())
        dialog.reject()

    def test_settings_are_nonsecret_and_bounds_use_n_s_w_e_order(self):
        dialog = Fr24Dialog(OverlayFixture())
        dialog.enabled.setChecked(True)
        dialog.use_bounds.setChecked(True)
        dialog.endpoint.setCurrentIndex(dialog.endpoint.findData('light'))
        dialog.interval.setValue(120)
        dialog.bounds['north'].setValue(-10.25)
        dialog.bounds['south'].setValue(-43.5)
        dialog.bounds['west'].setValue(112.0)
        dialog.bounds['east'].setValue(154.0)
        dialog.limit.setValue(240)
        dialog.token.setText('secret-example')
        saved = dialog.preferences()
        self.assertEqual(saved, {
            'enabled': True, 'endpoint': 'light', 'interval_seconds': 120,
            'bounds': '-10.25,-43.5,112,154', 'limit': 240,
            'has_token': False,
        })
        self.assertNotIn('secret-example', repr(saved))
        self.assertEqual(dialog.token_action(), 'secret-example')
        dialog.reject()

    def test_token_keep_replace_and_remove_actions(self):
        dialog = Fr24Dialog(OverlayFixture({'has_token': True}))
        self.assertIsNone(dialog.token_action())
        dialog.token.setText('replacement-token')
        self.assertEqual(dialog.token_action(), 'replacement-token')
        dialog.token.clear()
        dialog.remove_token_button.click()
        self.assertEqual(dialog.token_action(), '')
        self.assertFalse(dialog.enabled.isChecked())
        self.assertFalse(dialog.remove_token_button.isEnabled())
        dialog.reject()

    def test_manual_query_cadence_is_editable_with_overlay_off(self):
        dialog = Fr24Dialog(OverlayFixture())
        self.assertTrue(dialog.interval.isEnabled())
        self.assertFalse(dialog.endpoint.isEnabled())
        dialog.enabled.setChecked(True)
        dialog.use_bounds.setChecked(True)
        self.assertTrue(dialog.interval.isEnabled())
        self.assertTrue(all(control.isEnabled() for control in dialog.bounds.values()))
        dialog.reject()

    def test_save_configures_overlay_and_unchanged_save_does_not_reset_it(self):
        overlay = OverlayFixture()
        dialog = Fr24Dialog(overlay)
        dialog.endpoint.setCurrentIndex(dialog.endpoint.findData('light'))
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(overlay.saved[1], None)

        unchanged = Fr24Dialog(overlay)
        unchanged.accept()
        self.assertEqual(unchanged.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(overlay.saved[0]['endpoint'], 'light')

    def test_normalized_saved_bounds_survive_reopen_without_reconfiguration(self):
        overlay = OverlayFixture({
            'enabled': True,
            'bounds': '1,-1,1,2',
            'has_token': True,
        })
        dialog = Fr24Dialog(overlay)
        self.assertEqual(dialog.preferences()['bounds'], '1,-1,1,2')
        dialog.accept()
        self.assertFalse(hasattr(overlay, 'saved'))

    def test_cancel_clears_token_from_dialog(self):
        dialog = Fr24Dialog(OverlayFixture())
        dialog.token.setText('temporary-secret')
        dialog.reject()
        self.assertEqual(dialog.token.text(), '')
        self.assertIsNone(dialog.token_action())

    def test_removing_token_disables_and_saves_overlay(self):
        overlay = OverlayFixture({'enabled': True, 'has_token': True,
                                  'bounds': '1,-1,1,2'})
        dialog = Fr24Dialog(overlay)
        dialog.remove_token_button.click()
        self.assertFalse(dialog.enabled.isChecked())
        dialog.accept()
        self.assertEqual(overlay.saved[1], '')
        self.assertFalse(overlay.value['enabled'])
        self.assertFalse(overlay.value['has_token'])


if __name__ == '__main__':
    unittest.main()
