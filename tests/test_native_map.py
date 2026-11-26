"""Native map tests with no network calls or receiver/profile changes."""
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'work/desktoplibs')]
os.environ.setdefault('QT_QPA_PLATFORM', os.environ.get('FRW_TEST_QT_PLATFORM', 'offscreen'))
try:
    from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QPointF, Qt
    from PySide6.QtGui import QColor, QHelpEvent, QPixmap
    from PySide6.QtNetwork import QNetworkReply
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:
    QT_AVAILABLE = False
else:
    from frw.native_map import NativeMap, RangeRadar, _MapCanvas, _nearby, _project
    QT_AVAILABLE = True


@unittest.skipUnless(QT_AVAILABLE, 'Install PySide6 Essentials to test native widgets')
class NativeMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_pump = _MapCanvas._pump_tiles
        self.no_network = patch.object(_MapCanvas, '_pump_tiles')
        self.no_network.start()
        self.addCleanup(self.no_network.stop)
        self.map = NativeMap()
        self.map.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.map.resize(900, 600)
        self.map.show()
        self.app.processEvents()

    def tearDown(self):
        self.map.close()
        self.map.deleteLater()
        self.app.processEvents()

    def test_emergency_aircraft_flashes_and_steady_red_option(self):
        canvas = self.map.canvas
        canvas.rows = [dict(icao='E00001', latitude=0, longitude=.1, track=90, squawk='7700', trail=[]),
                       dict(icao='E00002', latitude=0, longitude=.2, track=90, squawk='1200', trail=[])]
        canvas._flash_on = False
        self.assertFalse(canvas._emergency_flash_on())
        canvas._tick_flash()
        self.assertTrue(canvas._emergency_flash_on())
        canvas._tick_flash()
        self.assertFalse(canvas._emergency_flash_on())
        # No emergencies: flashing settles off and stops repainting.
        canvas.rows = [canvas.rows[1]]
        canvas._flash_on = True
        canvas._tick_flash()
        self.assertFalse(canvas._flash_on)
        # Preference off: steady red (flash_on reported true, timer never toggles).
        self.map.set_display({'emergency_flash': False})
        canvas.rows = [dict(icao='E00001', latitude=0, longitude=.1, squawk='7600', trail=[])]
        canvas._flash_on = False
        canvas._tick_flash()
        self.assertFalse(canvas._flash_on)
        self.assertTrue(canvas._emergency_flash_on())
        self.assertEqual(self.map.canvas.display['emergency_flash'], False)

    def test_date_line_fit_and_track_unwrap(self):
        rows = [dict(icao='EAST', latitude=-17, longitude=179.8),
                dict(icao='WEST', latitude=-17.1, longitude=-179.8)]
        self.map.update_aircraft(rows, {})
        self.map.fit_aircraft()
        for row in rows:
            point = self.map.canvas._screen(row['latitude'], row['longitude'])
            self.assertGreater(point.x(), 45)
            self.assertLess(point.x(), self.map.canvas.width() - 45)
            self.assertGreater(point.y(), 45)
            self.assertLess(point.y(), self.map.canvas.height() - 45)
        self.assertGreaterEqual(self.map.canvas.zoom, 9)
        east = _project(-17, 179.8)[0]
        west = _nearby(_project(-17, -179.8)[0], east)
        self.assertLess(abs(west - east), .002)

    def test_conflict_flashing_steady_colour_and_emergency_priority(self):
        canvas = self.map.canvas
        row = dict(icao='A00001', latitude=0, longitude=.1, squawk='1200')
        self.map.update_aircraft([row], {}, conflicts={'alerts': {'A00001': {'level': 'yellow'}}})
        canvas._flash_on = False
        self.assertEqual(canvas._warning_style(row), ('yellow', False))
        canvas._tick_flash()
        self.assertEqual(canvas._warning_style(row), ('yellow', True))
        self.map.set_display({'conflict_flash': False})
        canvas._flash_on = False
        self.assertEqual(canvas._warning_style(row), ('yellow', True))
        self.assertEqual(canvas._warning_style({**row, 'squawk': '7700'}), ('red', False))
        self.map.set_display({'conflict_enabled': False})
        self.assertEqual(canvas._warning_style(row)[0], '')

    def test_custom_labels_respect_selection_and_hover_still_selects(self):
        row = dict(icao='A00001', callsign='TEST101', latitude=0, longitude=.1,
                   altitude=12000, groundspeed=210, track=90, squawk='1200')
        self.map.update_aircraft([row], dict(latitude=0, longitude=0), 'A00001')
        self.map.set_display({'map_labels': 'none'})
        self.app.processEvents()
        canvas = self.map.canvas
        icao, point, box = canvas._markers[0]
        self.assertEqual(icao, 'A00001')
        self.assertTrue(box.isEmpty())  # Selection does not override hidden labels.
        with patch('frw.native_map.QToolTip.showText') as show, patch('frw.native_map.QToolTip.hideText') as hide:
            event = QHelpEvent(QEvent.Type.ToolTip, point.toPoint(), canvas.mapToGlobal(point.toPoint()))
            self.app.sendEvent(canvas, event)
            self.assertEqual(canvas._hover_icao, 'A00001')
            self.assertIn('TEST101', show.call_args.args[1])
            selected = []
            self.map.aircraftSelected.connect(selected.append)
            QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint())
            self.assertEqual(selected, ['A00001'])
            self.assertEqual(canvas._hover_icao, '')
            hide.assert_called()

    def test_label_hit_test_hover_refresh_and_disable(self):
        row = dict(icao='A00001', callsign='TEST101', latitude=0, longitude=.1, groundspeed=200)
        self.map.set_display({'map_labels': 'custom', 'map_label_fields': ['icao', 'groundspeed']})
        self.map.update_aircraft([row], dict(latitude=0, longitude=0))
        self.app.processEvents()
        canvas = self.map.canvas
        point = canvas._markers[0][2].center()
        self.assertEqual(canvas._hit_aircraft(point), 'A00001')
        with patch('frw.native_map.QToolTip.showText') as show, patch('frw.native_map.QToolTip.hideText'):
            canvas._hover_point = point
            canvas._refresh_hover()
            self.assertIn('200', show.call_args.args[1])
            canvas.rows[0] = {**row, 'groundspeed': 220}
            canvas._refresh_hover()
            self.assertIn('220', show.call_args.args[1])
            self.map.set_display({'aircraft_hover': False})
            canvas._refresh_hover()
            self.assertEqual(canvas._hover_icao, '')

    def test_worldwide_fit_can_zoom_out(self):
        self.map.canvas.setFixedSize(300, 260)
        self.app.processEvents()
        rows = [dict(icao=str(i), latitude=lat, longitude=lon)
                for i, (lat, lon) in enumerate(((50, -100), (-30, 30), (40, 130), (-60, -40)))]
        self.map.update_aircraft(rows, {})
        self.map.fit_aircraft()
        for row in rows:
            point = self.map.canvas._screen(row['latitude'], row['longitude'])
            self.assertTrue(self.map.canvas.rect().contains(point.toPoint()))
        self.assertLessEqual(self.map.canvas.zoom, 1)

    def test_zero_coordinates_invalid_positions_and_stable_camera(self):
        rows = [dict(icao='ZERO', latitude=0, longitude=.1, track=0, trail=[]),
                dict(icao='BAD', latitude=91, longitude=0),
                dict(icao='MISSING', latitude=None, longitude=0)]
        config = dict(latitude=0, longitude=0)
        self.map.update_aircraft(rows, config)
        self.assertEqual(len(self.map.canvas.rows), 1)
        self.assertTrue(self.map.home_button.isEnabled())
        self.map.canvas.zoom_at(-1, QPointF(0, 0))
        center, zoom = QPointF(self.map.canvas.center), self.map.canvas.zoom
        self.map.update_aircraft(rows, config, 'ZERO')
        self.assertEqual(self.map.canvas.center, center)
        self.assertEqual(self.map.canvas.zoom, zoom)
        self.map.center_home()
        self.assertEqual(self.map.canvas.center, QPointF(.5, .5))

    def test_map_selection_uses_marker_position(self):
        self.map.update_aircraft([dict(icao='A00001', latitude=0, longitude=.1, track=0, trail=[])],
                                 dict(latitude=0, longitude=0))
        self.app.processEvents()
        selected = []
        self.map.aircraftSelected.connect(selected.append)
        point = self.map.canvas._screen(0, .1)
        QTest.mouseClick(self.map.canvas, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, point.toPoint())
        self.assertEqual(selected, ['A00001'])

    def test_double_click_zooms_once_at_pointer_across_date_line_and_stops_at_maximum(self):
        canvas = self.map.canvas
        canvas.set_center(-17, 179.8, 6)
        anchor = QPointF(int(canvas.width() * .7), int(canvas.height() * .35))
        offset = anchor - QPointF(canvas.width() / 2, canvas.height() / 2)
        before = canvas.center + offset / canvas.world_size
        QTest.mouseDClick(canvas, Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier, anchor.toPoint())
        after = canvas.center + offset / canvas.world_size
        self.assertEqual(canvas.zoom, 7)
        self.assertAlmostEqual((after.x() - before.x() + .5) % 1 - .5, 0, places=12)
        self.assertAlmostEqual(after.y(), before.y(), places=12)
        self.assertIsNone(canvas._press)
        canvas.set_center(-17, 179.8, 18)
        center = QPointF(canvas.center)
        QTest.mouseDClick(canvas, Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier, anchor.toPoint())
        self.assertEqual(canvas.zoom, 18)
        self.assertEqual(canvas.center, center)

    def test_double_click_preserves_first_click_aircraft_selection(self):
        self.map.update_aircraft([dict(icao='A00001', latitude=0, longitude=.1, track=0, trail=[])],
                                 dict(latitude=0, longitude=0))
        self.app.processEvents()
        canvas = self.map.canvas
        selected = []
        self.map.aircraftSelected.connect(selected.append)
        point = canvas._screen(0, .1).toPoint()
        # Qt delivers the first press/release before the second double-click event.
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        zoom = canvas.zoom
        QTest.mouseDClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        self.assertEqual(canvas.zoom, zoom + 1)
        self.assertEqual(selected, ['A00001'])
        QTest.mouseDClick(canvas, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
        self.assertEqual(canvas.zoom, zoom + 1)
        self.assertEqual(selected, ['A00001'])

    def test_async_tile_queue_and_memory_cache_are_bounded(self):
        canvas = self.map.canvas
        canvas._needed = [(8, i, 1) for i in range(20)]
        network = Mock()
        canvas._network = network
        self.original_pump(canvas)
        self.original_pump(canvas)
        self.assertEqual(network.get.call_count, 6)
        self.assertEqual(len(canvas._inflight), 6)
        self.assertTrue(all(call.args[0].url().host() == 'tile.openstreetmap.org'
                            for call in network.get.call_args_list))
        pixmap = QPixmap(2, 2)
        pixmap.fill(QColor('white'))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, 'PNG')
        buffer.close()
        canvas._tiles.update({(7, i, 0): pixmap for i in range(256)})
        reply = Mock()
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        reply.readAll.return_value = data
        canvas._tile_finished((9, 0, 0), reply)
        self.assertEqual(len(canvas._tiles), 256)
        self.assertIn((9, 0, 0), canvas._tiles)
        self.assertNotIn((7, 0, 0), canvas._tiles)

    def test_tile_failure_keeps_aircraft_overlay(self):
        canvas = self.map.canvas
        row = dict(icao='OFFLINE', latitude=0, longitude=.1, track=0, trail=[])
        self.map.update_aircraft([row], dict(latitude=0, longitude=0))
        canvas._needed = [(9, 256, 256)]
        reply = Mock()
        reply.error.return_value = QNetworkReply.NetworkError.HostNotFoundError
        canvas._tile_finished((9, 256, 256), reply)
        self.assertTrue(canvas._tile_problem)
        canvas._network = Mock()
        self.original_pump(canvas)
        self.assertEqual(canvas._network.get.call_count, 0)
        self.app.processEvents()
        self.assertEqual([entry[0] for entry in canvas._markers], ['OFFLINE'])

    def test_radar_range_and_selection(self):
        radar = RangeRadar()
        radar.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        radar.resize(500, 400)
        radar.show()
        radar.update_aircraft([dict(icao='ZERO', latitude=0, longitude=.1, track=0,
                                    trail=[[0, .05], [0, .1]])], dict(latitude=0, longitude=0), 'ZERO')
        radar.set_range(50)
        self.app.processEvents()
        self.assertEqual(radar.canvas.range_nm, 50)
        self.assertEqual(radar.range_control.currentData(), 50)
        self.assertEqual(radar.canvas.station, (0, 0))
        selected = []
        radar.aircraftSelected.connect(selected.append)
        QTest.mouseClick(radar.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         radar.canvas._markers[0][1].toPoint())
        self.assertEqual(selected, ['ZERO'])
        radar.close()
        radar.deleteLater()


if __name__ == '__main__':
    unittest.main()
