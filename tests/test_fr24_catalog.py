import sys
import os
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "work/desktoplibs"), str(ROOT / "work/applibs")]

from frw.fr24_catalog import BY_KEY, ENDPOINTS, make_query


class Fr24CatalogTests(unittest.TestCase):
    def test_every_endpoint_is_explicit_and_path_is_fixed(self):
        self.assertGreaterEqual(len(ENDPOINTS), 14)
        self.assertEqual(len({item.key for item in ENDPOINTS}), len(ENDPOINTS))
        self.assertTrue(all(item.path.startswith("/") and ".." not in item.path for item in ENDPOINTS))

    def test_unknown_endpoint_and_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            make_query("https://example.invalid", {})
        with self.assertRaises(ValueError):
            make_query("usage", {"token": "secret"})

    def test_required_fields_and_historic_timestamp_are_validated(self):
        with self.assertRaises(ValueError):
            make_query("tracks", {})
        with self.assertRaises(ValueError):
            make_query("historic_full", {"timestamp": "2025-01-01"})
        path, params = make_query("historic_full", {"timestamp": "1767225600", "bounds": "1,0,2,3", "limit": "20"})
        self.assertEqual(path, BY_KEY["historic_full"].path)
        self.assertEqual(params, {"timestamp": "1767225600", "bounds": "1,0,2,3", "limit": "20"})

    def test_flight_summary_requires_one_query_mode(self):
        with self.assertRaises(ValueError):
            make_query("summary_light", {})
        with self.assertRaises(ValueError):
            make_query("summary_light", {"flight_ids": "abc", "flight_datetime_from": "2025-01-01"})
        with self.assertRaises(ValueError):
            make_query("summary_light", {"flight_ids": ",".join(str(i) for i in range(16))})
        path, params = make_query("summary_light", {"flight_ids": "abc,def", "limit": "10"})
        self.assertEqual(path, BY_KEY["summary_light"].path)
        self.assertEqual(params, {"flight_ids": "abc,def", "limit": "10"})

    def test_parameter_lengths_and_result_limit_are_bounded(self):
        with self.assertRaises(ValueError):
            make_query("airports_full", {"code": "A" * 201})
        with self.assertRaises(ValueError):
            make_query("live_full", {"bounds": "1,2,3,4", "limit": "1000"})
        with self.assertRaises(ValueError):
            make_query("live_full", {"bounds": "1,0,2,3,4"})

    def test_static_endpoints_use_verified_provider_paths(self):
        path, params = make_query("airports_full", {"code": "yssy"})
        self.assertEqual(path, "/static/airports/YSSY/full")
        self.assertEqual(params, {})
        path, params = make_query("airlines_light", {"code": "qfa"})
        self.assertEqual(path, "/static/airlines/QFA/light")
        self.assertEqual(params, {})
        with self.assertRaises(ValueError):
            make_query("airports_light", {"code": "../../evil"})

    def test_events_use_provider_id_and_event_type_query(self):
        path, params = make_query("events_full", {"flight_ids": "38a384da", "event_types": "takeoff,landed"})
        self.assertEqual(path, "/historic/flight-events/full")
        self.assertEqual(params, {"flight_ids": "38a384da", "event_types": "takeoff,landed"})
        with self.assertRaises(ValueError):
            make_query("events_full", {"flight_ids": "38a384da", "event_types": "not-a-provider-event"})
        path, params = make_query("events_light", {"flight_ids": "38a384da", "event_types": "diverting,diverted"})
        self.assertEqual(path, "/historic/flight-events/light")
        self.assertEqual(params["event_types"], "diverting,diverted")
        with self.assertRaises(ValueError):
            make_query("events_full", {"flight_ids": "38a384da", "event_datetime_from": "2025-01-01T00:00:00Z",
                                        "event_types": "all"})

    def test_summary_date_range_is_bounded_and_requires_a_filter(self):
        with self.assertRaises(ValueError):
            make_query("summary_light", {"flight_datetime_from": "2025-01-01T00:00:00Z",
                                          "flight_datetime_to": "2025-01-20T00:00:00Z", "airports": "YSSY"})
        with self.assertRaises(ValueError):
            make_query("summary_light", {"flight_datetime_from": "2025-01-01T00:00:00Z",
                                          "flight_datetime_to": "2025-01-02T00:00:00Z"})
        path, params = make_query("summary_light", {"flight_datetime_from": "2025-01-01T00:00:00Z",
                                                      "flight_datetime_to": "2025-01-02T00:00:00Z",
                                                      "flights": "QFA123"})
        self.assertEqual(path, "/flight-summary/light")
        self.assertEqual(params["flight_datetime_to"], "2025-01-02T00:00:00Z")

    def test_usage_period_matches_provider_enum(self):
        path, params = make_query("usage", {"period": "1y"})
        self.assertEqual(path, "/usage")
        self.assertEqual(params, {"period": "1y"})
        with self.assertRaises(ValueError):
            make_query("usage", {"period": "2y"})


try:
    import PySide6
except ImportError:
    QT_AVAILABLE = False
else:
    import os
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from frw.native_fr24_catalog import Fr24CatalogDialog
    QT_AVAILABLE = True


@unittest.skipUnless(QT_AVAILABLE, "Install PySide6 Essentials to test the native API browser")
class Fr24CatalogDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def spin(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail("Timed out waiting for API dialog result")

    def test_dialog_renders_api_response_and_uses_fixed_path(self):
        overlay = Mock()
        overlay.request_json.return_value = {"data": [{"hex": "ABC123", "lat": 1.25}]}
        dialog = Fr24CatalogDialog(overlay)
        index = dialog.endpoint.findData("live_full")
        dialog.endpoint.setCurrentIndex(index)
        dialog.fields["bounds"].setText("1,0,2,3")
        dialog.run_query()
        self.spin(lambda: not dialog.busy)
        overlay.request_json.assert_called_once_with("/live/flight-positions/full", {"bounds": "1,0,2,3", "limit": "20"})
        self.assertIn('"hex": "ABC123"', dialog.output.toPlainText())
        dialog.deleteLater()
        self.app.processEvents()

    def test_close_during_query_disconnects_result_receiver(self):
        entered, release = threading.Event(), threading.Event()

        def wait_for_release(_path, _params):
            entered.set()
            release.wait(2)
            return {"ok": True}

        overlay = Mock()
        overlay.request_json.side_effect = wait_for_release
        dialog = Fr24CatalogDialog(overlay)
        dialog.endpoint.setCurrentIndex(dialog.endpoint.findData("usage"))
        dialog.run_query()
        self.assertTrue(entered.wait(2))
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        release.set()
        time.sleep(.05)
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
