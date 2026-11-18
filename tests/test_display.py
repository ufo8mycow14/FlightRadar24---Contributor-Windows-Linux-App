"""Display formatting, validation and Airspace workspace behaviour (0.5.0)."""
from __future__ import annotations
import copy, os, sys, tempfile, time, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'work/desktoplibs'), str(ROOT / 'work/applibs')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from frw.config import ConfigError
from frw.display import (bearing_deg, bearing_suffix, destination_point, display_bearing, distance_bearing, emergency_text,
    flight_level, format_altitude, format_distance, format_seen, format_speed,
    format_track, format_vertical_rate, is_emergency, quick_match, sort_rows,
    trend_arrow, validate_display)
from frw.features import validate_features

try:
    import PySide6  # noqa: F401
except ImportError:
    QT_AVAILABLE = False
else:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from frw.config import DEFAULT
    from frw.desktop import FlightRadarWindow
    from frw.native_map import _MapCanvas
    QT_AVAILABLE = True


class DisplayFormattingTests(unittest.TestCase):
    def test_altitude_and_flight_level(self):
        self.assertEqual(format_altitude(35000), '35,000 ft · FL350')
        self.assertEqual(format_altitude(4500), '4,500 ft')
        self.assertEqual(format_altitude(10000), '10,000 ft · FL100')
        self.assertEqual(format_altitude(35000, 'metric'), '10,668 m')
        self.assertEqual(format_altitude(None), '—')
        self.assertEqual(format_altitude(float('nan')), '—')
        self.assertIsNone(flight_level(9999))
        self.assertEqual(flight_level(37400), 374)

    def test_speed_distance_vertical_track_seen(self):
        self.assertEqual(format_speed(449.6), '450 kt')
        self.assertEqual(format_speed(100, 'metric'), '185 km/h')
        self.assertEqual(format_distance(9.94), '9.9 NM')
        self.assertEqual(format_distance(123.4), '123 NM')
        self.assertEqual(format_distance(100, 'metric'), '185 km')
        self.assertIn('▲', format_vertical_rate(1800))
        self.assertIn('▼', format_vertical_rate(-1800))
        self.assertEqual(trend_arrow(50), '→')
        self.assertEqual(format_vertical_rate(None), '—')
        self.assertEqual(format_track(359.6), '000°')
        self.assertEqual(format_track(90), '090°')
        self.assertEqual(format_seen(42), '42 s')
        self.assertEqual(format_seen(125), '2 m 5 s')

    def test_bearing_distance_and_destination(self):
        self.assertEqual(bearing_deg(0, 0, 1, 0), 0)
        self.assertEqual(bearing_deg(0, 0, 0, 1), 90)
        self.assertEqual(bearing_deg(0, 0, -1, 0), 180)
        result = distance_bearing((0, 0), {'latitude': 0, 'longitude': 1})
        self.assertAlmostEqual(result[0], 60.04, delta=0.1)
        self.assertEqual(result[1], 90)
        self.assertIsNone(distance_bearing(None, {'latitude': 0, 'longitude': 1}))
        self.assertIsNone(distance_bearing((0, 0), {'icao': 'ABC123'}))
        lat, lon = destination_point(0, 0, 0, 60.04)
        self.assertAlmostEqual(lat, 1.0, delta=0.01)
        self.assertAlmostEqual(lon, 0.0, delta=0.01)

    def test_north_reference_and_magnetic_variation(self):
        magnetic = validate_display({'north_reference': 'magnetic', 'magnetic_variation': 8})
        self.assertEqual(display_bearing(90, magnetic), 82)
        self.assertEqual(display_bearing(5, magnetic), 357)
        self.assertEqual(bearing_suffix(magnetic), '°M')
        self.assertEqual(display_bearing(90, validate_display({})), 90)
        self.assertEqual(bearing_suffix(validate_display({})), '°T')
        self.assertIsNone(display_bearing(None, magnetic))
        with self.assertRaises(ConfigError):
            validate_display({'north_reference': 'grid'})
        with self.assertRaises(ConfigError):
            validate_display({'magnetic_variation': 45})

    def test_emergency_detection(self):
        self.assertTrue(is_emergency({'squawk': '7700'}))
        self.assertIn('Radio failure', emergency_text({'squawk': '7600'}))
        self.assertIn('Unlawful', emergency_text({'squawk': '7500'}))
        self.assertTrue(is_emergency({'emergency_state': 3}))
        self.assertFalse(is_emergency({'squawk': '1200'}))
        self.assertFalse(is_emergency({}))

    def test_sort_missing_last_and_quick_match(self):
        rows = [{'icao': 'B', 'altitude': 3000}, {'icao': 'A'}, {'icao': 'C', 'altitude': 1000}]
        self.assertEqual([r['icao'] for r in sort_rows(rows, 'altitude')], ['C', 'B', 'A'])
        self.assertEqual([r['icao'] for r in sort_rows(rows, 'altitude', reverse=True)], ['B', 'C', 'A'])
        self.assertEqual([r['icao'] for r in sort_rows(rows, None)], ['B', 'A', 'C'])
        self.assertTrue(quick_match({'callsign': 'QFA123'}, 'qfa'))
        self.assertTrue(quick_match({'icao': '7C1234'}, '7c12'))
        self.assertFalse(quick_match({'callsign': 'QFA123'}, 'vir'))

    def test_display_validation_and_feature_migration(self):
        self.assertEqual(validate_display({})['units'], 'aviation')
        self.assertEqual(validate_display({'units': 'metric'})['map_labels'], 'callsign')
        with self.assertRaises(ConfigError):
            validate_display({'units': 'imperial'})
        with self.assertRaises(ConfigError):
            validate_display({'range_rings': 'yes'})
        with self.assertRaises(ConfigError):
            validate_display({'unknown': True})
        # A pre-0.5 features file (no display key) must load with defaults.
        migrated = validate_features({})
        self.assertFalse(migrated['display']['follow_selected'])
        self.assertTrue(migrated['display']['range_rings'])


class _FakeReceiver:
    def __init__(self, directory):
        self.directory = Path(directory)
        config = copy.deepcopy(DEFAULT)
        config.update(autostart_receiver=False, latitude=0.0, longitude=0.0)
        self.settings = SimpleNamespace(value=config, directory=self.directory,
                                        public=lambda: {**copy.deepcopy(config), 'has_key': False})
        self.log = SimpleNamespace(snapshot=lambda: [], add=Mock(), close=Mock(), configure=Mock())
        self.feeder = SimpleNamespace(signup_process=None, signup_stop=Mock(),
                                      signup_snapshot=lambda: {'running': False, 'text': ''})
        self.aircraft = SimpleNamespace(detail=lambda icao: None)
        self.thread = None
        self.status = {'state': 'receiving', 'input': 'usb', 'aircraft': [], 'messages': 0,
                       'messages_per_second': 0, 'feeder': {'state': 'stopped', 'monitor': {}}}

    def snapshot(self):
        return copy.deepcopy(self.status)

    def start(self):
        self.status['state'] = 'receiving'

    def stop(self):
        self.status['state'] = 'stopped'


@unittest.skipUnless(QT_AVAILABLE, 'Install PySide6 Essentials to test the native desktop')
class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls._original_pump = _MapCanvas._pump_tiles
        _MapCanvas._pump_tiles = lambda self: None

    @classmethod
    def tearDownClass(cls):
        _MapCanvas._pump_tiles = cls._original_pump

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='frw-display-test-')
        self.receiver = _FakeReceiver(self.directory.name)
        self.window = FlightRadarWindow(self.receiver)
        now = time.time()
        self.window.rows = [
            {'icao': '7C0001', 'callsign': 'QFA123', 'altitude': 35000, 'groundspeed': 450,
             'track': 90, 'vertical_rate': 0, 'squawk': '1200', 'seen': 2.0,
             'latitude': 0.0, 'longitude': 1.0, 'last_seen': now},
            {'icao': '7C0002', 'callsign': 'VOZ456', 'altitude': 5000, 'groundspeed': 180,
             'track': 270, 'vertical_rate': -1500, 'squawk': '7700', 'seen': 5.0,
             'latitude': 0.5, 'longitude': 0.0, 'last_seen': now},
            {'icao': '7C0003', 'callsign': '', 'seen': 9.0, 'last_seen': now},
        ]
        self.window._update_table()

    def tearDown(self):
        self.window._can_close = True
        self.window.close()
        self.directory.cleanup()

    def test_table_formats_units_distance_and_emergency(self):
        table = self.window.table
        self.assertEqual(table.columnCount(), 11)
        self.assertEqual(table.item(0, 2).text(), '35,000 ft · FL350')
        self.assertEqual(table.item(0, 7).text(), '60 NM')
        self.assertEqual(table.item(0, 8).text(), '090°')
        self.assertIn('General emergency', table.item(1, 6).toolTip())
        self.assertEqual(table.item(2, 2).text(), '—')
        self.assertEqual(table.item(2, 10).text(), 'Not received')

    def test_find_box_and_sorting(self):
        self.window._search_changed('voz')
        self.assertEqual([r['icao'] for r in self.window.visible_rows()], ['7C0002'])
        self.window._search_changed('')
        self.window._sort_by_column(2)  # altitude ascending, missing last
        self.assertEqual([r['icao'] for r in self.window.visible_rows()], ['7C0002', '7C0001', '7C0003'])
        self.window._sort_by_column(2)  # toggle descending
        self.assertEqual([r['icao'] for r in self.window.visible_rows()], ['7C0001', '7C0002', '7C0003'])

    def test_map_first_layout_and_list_overlay(self):
        # The range-radar box is gone; reception/sharing status is in the very
        # top bar, attribution in the bottom bar, the aircraft list floats
        # along the bottom spanning the map, and details pop up at the left.
        self.assertFalse(hasattr(self.window, 'radar'))
        self.assertFalse(hasattr(self.window, 'details_dock'))
        self.assertFalse(hasattr(self.window, 'status_panel'))
        self.assertIs(self.window.list_panel.parent(), self.window.map.canvas)
        self.assertIs(self.window.details_panel.parent(), self.window.map.canvas)
        self.assertIs(self.window.map.attribution.parent(), self.window.statusBar())
        self.assertIs(self.window.menuBar().cornerWidget(Qt.Corner.TopRightCorner), self.window.top_status)
        self.assertIs(self.window.metrics.parent(), self.window.top_status)
        self.assertIs(self.window.feed_status.parent(), self.window.top_status)
        self.assertIs(self.window.health.parent(), self.window.top_status)
        anchors = {id(widget): anchor for widget, anchor in self.window.map._overlays}
        self.assertEqual(anchors[id(self.window.list_panel)], 'bottom-full')
        self.assertEqual(anchors[id(self.window.details_panel)], 'details-left')
        self.assertEqual(anchors[id(self.window.message)], 'top-message')
        self.assertIs(self.window.table.parent(), self.window._list_body)
        self.assertEqual(self.window.list_panel._overlay_height, 280)
        self.window._set_list_collapsed(True)
        self.assertEqual(self.window.list_panel._overlay_height, 42)
        self.assertLess(self.window.list_panel.height(), 280)
        self.window._set_list_collapsed(False)
        self.assertEqual(self.window.details_panel._overlay_height, 540)
        self.assertTrue(self.window.details_panel.isHidden())
        self.window.select_aircraft('7C0001')
        self.assertFalse(self.window.details_panel.isHidden())
        self.window.clear_selection()
        self.assertTrue(self.window.details_panel.isHidden())
        headers = [self.window.table.horizontalHeaderItem(i).text() for i in range(11)]
        self.assertIn('°T', headers[8])
        self.window.features = validate_features({'display': {'north_reference': 'magnetic', 'magnetic_variation': 10}})
        self.window._update_table_headers()
        self.window._update_table()
        headers = [self.window.table.horizontalHeaderItem(i).text() for i in range(11)]
        self.assertIn('°M', headers[8])
        self.assertEqual(self.window.table.item(0, 8).text(), '080°')  # 090 true - 10 variation

    def test_metric_units_change_headers_and_values(self):
        self.window.features = validate_features({'display': {'units': 'metric'}})
        self.window._update_table_headers()
        self.window._update_table()
        headers = [self.window.table.horizontalHeaderItem(i).text() for i in range(11)]
        self.assertIn('km/h', headers[3])
        self.assertEqual(self.window.table.item(0, 2).text(), '10,668 m')


if __name__ == '__main__':
    unittest.main()
