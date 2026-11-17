"""Native main-window integration: in-process workers, selection and shutdown."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'work/desktoplibs'), str(ROOT / 'work/applibs')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    import PySide6
except ImportError:
    QT_AVAILABLE = False
else:
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
    from PySide6.QtWidgets import QApplication
    from frw.config import DEFAULT
    from frw.desktop import FlightRadarWindow
    from frw.native_map import _MapCanvas
    QT_AVAILABLE = True


class FakeReceiver:
    def __init__(self, directory):
        self.directory = Path(directory)
        config = copy.deepcopy(DEFAULT)
        config['autostart_receiver'] = False
        self.settings = SimpleNamespace(value=config, directory=self.directory,
                                        public=lambda: {**copy.deepcopy(config), 'has_key': False})
        self.log = SimpleNamespace(snapshot=lambda: [], add=Mock(), close=Mock(), configure=Mock())
        self.feeder = SimpleNamespace(signup_process=None, signup_stop=Mock(),
                                      signup_snapshot=lambda: {'running': False, 'text': ''})
        self.aircraft = SimpleNamespace(detail=self.detail)
        self.thread = None
        self.status_error = False
        self.shutdown_failures = 0
        self.start_entered, self.start_release = threading.Event(), threading.Event()
        self.stop_entered, self.stop_release = threading.Event(), threading.Event()
        self.start_release.set()
        self.stop_release.set()
        self.detail_gates = {}
        self.work_threads = []
        self.status = {'state': 'stopped', 'input': 'usb', 'aircraft': [], 'messages': 0,
                       'messages_per_second': 0, 'feeder': {'state': 'stopped', 'monitor': {}}}
        now = time.time()
        self.details = {icao: {'icao': icao, 'callsign': call, 'altitude_baro': altitude,
                               'latitude': 0, 'longitude': .1, 'last_seen': now,
                               'field_times': {}, 'history': [], 'data_source': 'ADS-B'}
                        for icao, call, altitude in [('A00001', 'FIRST', 10000), ('A00002', 'SECOND', 12000)]}

    def snapshot(self):
        self.work_threads.append(threading.get_ident())
        if self.status_error:
            raise RuntimeError('Synthetic status failure')
        return copy.deepcopy(self.status)

    def detail(self, icao):
        self.work_threads.append(threading.get_ident())
        if icao in self.detail_gates:
            entered, release = self.detail_gates[icao]
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test detail gate timed out')
        return copy.deepcopy(self.details.get(icao))

    def start(self):
        self.work_threads.append(threading.get_ident())
        self.start_entered.set()
        if not self.start_release.wait(5):
            raise RuntimeError('Test start gate timed out')
        self.status['state'] = 'receiving'

    def stop(self):
        self.work_threads.append(threading.get_ident())
        self.stop_entered.set()
        if not self.stop_release.wait(5):
            raise RuntimeError('Test stop gate timed out')
        if self.shutdown_failures:
            self.shutdown_failures -= 1
            raise RuntimeError('Synthetic stop failure')
        self.status['state'] = 'stopped'


@unittest.skipUnless(QT_AVAILABLE, 'Install PySide6 Essentials to test the native desktop')
class DesktopIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail('Timed out waiting for native operation')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='frw-desktop-test-')
        self.receiver = FakeReceiver(self.directory.name)
        self.map_network = patch.object(_MapCanvas, '_pump_tiles')
        self.map_network.start()
        self.no_http = patch.object(socket.socket, 'bind', side_effect=AssertionError('Desktop opened a listening socket'))
        self.no_http.start()
        self.window = FlightRadarWindow(self.receiver)
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.show()
        self.window.timer.stop()
        self.window.activation_timer.stop()
        self.wait_for(lambda: bool(self.window.last_status) and not self.window._poll_pending)

    def tearDown(self):
        self.receiver.shutdown_failures = 0
        self.receiver.start_release.set()
        self.receiver.stop_release.set()
        for entered, release in self.receiver.detail_gates.values():
            release.set()
        self.window.close()
        self.wait_for(lambda: self.window._can_close, timeout=5)
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.map_network.stop()
        self.no_http.stop()
        self.directory.cleanup()

    def refresh(self):
        self.wait_for(lambda: not self.window._poll_pending)
        self.window.poll()
        self.wait_for(lambda: not self.window._poll_pending)

    def test_slow_mutation_does_not_block_gui_or_status(self):
        self.receiver.start_release.clear()
        self.window.operation('start')
        self.wait_for(self.receiver.start_entered.is_set)
        gui_ticks = []
        QTimer.singleShot(0, lambda: gui_ticks.append(threading.get_ident()))
        self.receiver.status['messages'] = 55
        self.refresh()
        self.wait_for(lambda: bool(gui_ticks))
        self.assertTrue(self.window._action_pending)
        self.assertEqual(self.window.last_status['messages'], 55)
        self.assertEqual(gui_ticks, [threading.get_ident()])
        self.assertTrue(all(t != threading.get_ident() for t in self.receiver.work_threads))
        self.receiver.start_release.set()
        self.wait_for(lambda: not self.window._action_pending)

    def test_async_shutdown_finishes_cleanup_before_close(self):
        self.receiver.stop_release.clear()
        self.window.close()
        self.wait_for(self.receiver.stop_entered.is_set)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window._closing)
        self.assertFalse(self.window._can_close)
        self.assertFalse(self.window.tabs.isEnabled())
        self.assertFalse(self.receiver.log.close.called)
        gui_ticks = []
        QTimer.singleShot(0, lambda: gui_ticks.append(True))
        self.wait_for(lambda: bool(gui_ticks))
        self.receiver.stop_release.set()
        self.wait_for(lambda: self.window._can_close)
        self.assertTrue(self.window.dispatcher.closed)
        self.assertFalse(self.window.isVisible())
        self.receiver.feeder.signup_stop.assert_called_once()
        self.receiver.log.close.assert_called_once()

    def test_failed_shutdown_resumes_pages_and_allows_retry(self):
        self.receiver.shutdown_failures = 1
        self.window.close()
        self.wait_for(lambda: not self.window._closing)
        self.assertTrue(self.window.tabs.isEnabled())
        self.assertFalse(self.window.pages._closed)
        self.assertTrue(self.window.pages._timer.isActive())
        self.assertFalse(self.window.dispatcher.closed)
        self.window.pages.refresh_settings()
        self.wait_for(lambda: self.window.pages._settings_requests == 0)
        self.window.close()
        self.wait_for(lambda: self.window._can_close)
        self.assertTrue(self.window.pages._closed)

    def test_status_recovery_clears_only_its_notice_and_reads_actual_upload(self):
        self.receiver.status_error = True
        self.refresh()
        self.assertIn('Synthetic status failure', self.window.message.text())
        self.receiver.status_error = False
        self.refresh()
        self.assertTrue(self.window.message.isHidden())
        self.receiver.status_error = True
        self.refresh()
        self.window.notice('Unrelated settings error', True)
        self.receiver.status_error = False
        self.receiver.status['feeder']['monitor'] = {'feed-status': 'connected', 'mlat-ok': 'NOT-PERMITTED',
            'feed_last_ac_sent_num': '3', 'feed_last_ac_sent_time': str(int(time.time()) - 5), 'feed_num_ac_tracked': '42'}
        self.refresh()
        self.assertEqual(self.window.message.text(), 'Unrelated settings error')
        self.assertFalse(self.window.message.isHidden())
        self.assertIn('3 aircraft in last report', self.window.feed_status.text())
        self.assertIn('Not permitted by service', self.window.feed_status.text())
        self.assertNotIn('42', self.window.feed_status.text())

    def test_playback_and_disabled_upload_are_labelled(self):
        self.window.config.update(receiver='iq-file', feed_enabled=True)
        self.receiver.status.update(input='iq-file', aircraft=[self.receiver.details['A00001']])
        self.receiver.status['feeder']['monitor'] = {'feed-status': 'connected'}
        self.refresh()
        self.assertIn('Recorded playback', self.window.health.text())
        self.assertIn('Recorded playback', self.window.map.summary.text())
        self.assertEqual(self.window.table.item(0, 8).text(), 'Recorded')
        self.assertIn('Upload: Disabled (recorded playback)', self.window.feed_status.text())
        self.window.config.update(receiver='usb', feed_enabled=False)
        self.receiver.status['input'] = 'usb'
        self.refresh()
        self.assertIn('Live positions', self.window.map.summary.text())
        self.assertEqual(self.window.table.item(0, 8).text(), 'Live')
        self.assertIn('Upload: Disabled', self.window.feed_status.text())
        self.assertNotIn('Upload: connected', self.window.feed_status.text())

    def test_selection_rejects_stale_detail_and_clears_dock_snapshot(self):
        self.receiver.status['aircraft'] = [self.receiver.details['A00001'], self.receiver.details['A00002']]
        self.refresh()
        entered, release = threading.Event(), threading.Event()
        self.receiver.detail_gates['A00001'] = (entered, release)
        self.window.select_aircraft('A00001')
        self.wait_for(entered.is_set)
        self.window.select_aircraft('A00002')
        self.assertEqual(self.window.details.snapshot, {})
        release.set()
        self.wait_for(lambda: self.window.details.snapshot.get('icao') == 'A00002')
        self.assertEqual(self.window.details.callsign.text(), 'SECOND')
        self.window.details_dock.hide()
        self.app.processEvents()
        self.assertEqual(self.window.selected, '')
        self.assertEqual(self.window.details.snapshot, {})
        self.assertEqual(self.window.table.selectedItems(), [])
        self.window.select_aircraft('A00001')
        self.wait_for(lambda: self.window.details.snapshot.get('icao') == 'A00001')
        self.receiver.details.pop('A00001')
        self.refresh()
        self.wait_for(lambda: self.window.details.lost)
        self.assertEqual(self.window.details.snapshot['icao'], 'A00001')
        self.assertEqual(self.window.details.status.text(), 'Signal lost')


if __name__ == '__main__':
    unittest.main()