import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from frw.native_updates import UpdateDialog, UpdateManager
from frw.updates import parse_release
from test_updates import release_fixture


class NativeUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.manager=UpdateManager(Path(self.temporary.name),enabled=True)
        self.manager.timer.stop()
        self.manager._next_due_poll=float('inf')

    def tearDown(self):
        self.manager.finish()
        self.manager.pool.shutdown(wait=True)
        self.manager.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()

    def test_manual_mode_does_not_automatically_download_or_install(self):
        with patch.object(self.manager,'download') as download:
            self.manager._complete('check',parse_release(release_fixture(),'0.7.1'),None)
            download.assert_not_called()
        self.assertIsNone(self.manager.countdown)
        dialog=UpdateDialog(self.manager)
        self.assertEqual(dialog.install_button.text(),'Download update')
        self.assertTrue(dialog.install_button.isEnabled())
        dialog.reject()

    def test_auto_countdown_defers_for_edits_and_postponement_survives_restart(self):
        manager=self.manager
        manager.save_preferences(True,True)
        manager.release=parse_release(release_fixture(),'0.7.1')
        manager._complete('download',Path('fixture.exe'),None)
        self.assertEqual(manager.countdown,60)
        manager.can_install=lambda:False
        manager.tick();self.assertEqual(manager.countdown,60)
        manager.can_install=lambda:True
        manager.tick();self.assertEqual(manager.countdown,59)
        manager.postpone();self.assertIsNone(manager.countdown)
        self.assertFalse(manager._automatic_allowed())
        with patch.object(manager,'download') as download:
            manager._complete('check',manager.release,None)
            download.assert_not_called()

    def test_install_prepares_only_when_verified_download_exists(self):
        manager=self.manager
        manager.release=parse_release(release_fixture(),'0.7.1')
        plans=[];manager.installReady.connect(plans.append)
        with patch.object(manager,'download') as download:
            manager.install();download.assert_called_once()
        self.assertEqual(plans,[])
        manager.installer=Path('fixture.exe')
        manager.resume_receiver=lambda:True
        with patch('frw.native_updates.prepare_update',return_value={'helper':'fixture','request':'fixture'}) as prepare:
            manager.install()
            self.assertTrue(prepare.call_args.args[-1])
        self.assertEqual(len(plans),1)

    def test_error_or_disabling_auto_cancels_countdown(self):
        self.manager.countdown=20
        self.manager._complete('download',None,ValueError('Hash mismatch'))
        self.assertIsNone(self.manager.countdown)
        self.assertIn('Hash mismatch',self.manager.status)
        self.manager.countdown=20
        self.manager.save_preferences(False,False)
        self.assertIsNone(self.manager.countdown)
        self.assertIn('disabled',self.manager.status)

    def test_opt_in_starts_known_update_and_respects_postponement(self):
        manager=self.manager
        manager.release=parse_release(release_fixture(),'0.7.1')
        with patch.object(manager,'download') as download:
            manager.save_preferences(True,True)
            download.assert_called_once()
        manager.installer=Path('fixture.exe')
        manager.save_preferences(True,True)
        self.assertEqual(manager.countdown,60)
        manager.postpone()
        manager.save_preferences(True,True)
        self.assertIsNone(manager.countdown)
