"""Offscreen coverage for the optional native PySide aircraft details pane."""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "work" / "desktoplibs")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PySide6  # noqa: F401
except ImportError as exc:  # Qt itself is optional in protocol-only test environments.
    QApplication = None
    QT_IMPORT_ERROR = str(exc)
else:
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolButton
    from frw.native_details import NativeAircraftDetails
    QT_IMPORT_ERROR = ""


@unittest.skipIf(QApplication is None, "PySide6 unavailable: " + QT_IMPORT_ERROR)
class NativeAircraftDetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        self.widget = NativeAircraftDetails()
        self.widget.resize(420, 820)
        self.widget.show()
        self.app.processEvents()

    def tearDown(self):
        self.widget.close()
        self.app.processEvents()

    @staticmethod
    def detail(icao="A00010"):
        now = time.time()
        history = [
            {"time": now - 55, "field_times": {"altitude_baro": now - 55, "groundspeed": now - 55},
             "altitude_baro": 9000, "groundspeed": 100},
            {"time": now - 10, "field_times": {"altitude_gnss": now - 10, "groundspeed": now - 10},
             "altitude_gnss": 9300, "groundspeed": 0},
            {"time": now, "field_times": {"altitude_unspecified": now, "groundspeed": now},
             "altitude_unspecified": 0, "altitude": 0, "groundspeed": 0},
        ]
        return {
            "icao": icao, "callsign": "TEST10", "data_source": "ADS-B", "messages": 42,
            "last_seen": now, "seen": 0, "altitude_baro": 12000, "altitude_gnss": 12500,
            "groundspeed": 0, "autopilot": False, "emergency_state": 0, "on_ground": False,
            "field_times": {"altitude_baro": now - 3, "altitude_gnss": now - 4, "groundspeed": now - 2,
                            "autopilot": now - 1, "on_ground": now - 1},
            "field_sources": {"altitude_baro": "ADS-B TC 9", "altitude_gnss": "ADS-B TC 20",
                              "groundspeed": "ADS-B TC 19"},
            "history": history, "df": 17, "typecode": 19,
        }

    def test_received_zero_false_sources_and_sparse_history(self):
        detail = self.detail()
        self.widget.set_detail(detail)
        self.app.processEvents()

        self.assertEqual(self.widget.status.text(), "Receiving")
        self.assertEqual(self.widget.source.text(), "Terrestrial ADS-B")
        for key, expected in (
            ("groundspeed", "0 kt"), ("autopilot", "Off / no"),
            ("emergency_state", "No emergency reported"), ("on_ground", "Airborne"),
            ("altitude_baro", "12,000 ft"), ("altitude_gnss", "12,500 ft"),
        ):
            self.assertEqual(self.widget.rows[key][0].text(), expected)
        self.assertIn("via ADS-B TC 9", self.widget.rows["altitude_baro"][1].text())
        self.assertEqual(len(self.widget.history_altitude.samples), 3)
        self.assertEqual(self.widget.history_altitude.samples[-1]["field_times"]["altitude_unspecified"],
                         detail["history"][-1]["field_times"]["altitude_unspecified"])
        self.assertEqual(self.widget.extra_rows["df"][1].text(), "17")
        self.assertFalse(self.widget.grab().toImage().isNull())

    def test_errors_lost_snapshot_and_recovery(self):
        detail = self.detail()
        self.widget.set_detail(detail)
        self.widget.set_error("Details temporarily unavailable")
        self.assertEqual(self.widget.message.text(), "Details temporarily unavailable")
        self.assertEqual(self.widget.status.text(), "Details unavailable")

        self.widget.set_detail(detail, connection_error=True)
        self.assertEqual(self.widget.status.text(), "Connection interrupted")
        self.widget.mark_lost()
        self.assertEqual(self.widget.status.text(), "Signal lost")
        self.assertEqual(self.widget.rows["altitude_baro"][0].text(), "12,000 ft")

        self.widget.set_detail(detail)
        self.assertEqual(self.widget.status.text(), "Receiving")
        self.assertTrue(self.widget.message.text().startswith("Last received"))

    def test_scroll_identity_extra_rows_and_close_signal(self):
        detail = self.detail()
        self.widget.set_detail(detail)
        self.widget.set_detail({**detail, "df": 17, "typecode": 19, "subtype": 1})
        self.widget.set_detail({**detail, "subtype": 1})
        self.widget.set_detail({**detail, "bds": "5,0", "subtype": 1})
        positions = [self.widget.extra_grid.getItemPosition(i) for i in range(self.widget.extra_grid.count())]
        self.assertEqual(len(positions), len(set(positions)))

        self.widget.scroll.verticalScrollBar().setValue(300)
        self.app.processEvents()
        old_scroll = self.widget.scroll.verticalScrollBar().value()
        self.widget.set_detail({**detail, "altitude_baro": 12100})
        self.app.processEvents()
        self.assertEqual(self.widget.scroll.verticalScrollBar().value(), old_scroll)
        self.widget.set_detail({**detail, "icao": "A00011"})
        self.app.processEvents()
        self.assertEqual(self.widget.scroll.verticalScrollBar().value(), 0)

        closed = []
        self.widget.closed.connect(lambda: closed.append(True))
        self.widget.findChild(QPushButton).click()
        self.assertEqual(closed, [True])

    def test_empty_state_and_chart_gaps_use_only_received_samples(self):
        self.assertEqual(self.widget.status.text(), "Waiting for a report")
        detail = self.detail()
        samples = detail["history"]
        self.widget.set_detail(detail)
        chart = self.widget.history_altitude
        self.assertEqual(chart.samples, samples)
        # The 45-second separation remains sparse; there is no forward-filled report.
        self.assertNotIn("altitude_baro", chart.samples[1])
        self.assertNotIn("altitude_gnss", chart.samples[0])
        self.assertFalse(chart.grab().toImage().isNull())
        self.widget.resize(360, 820)
        self.app.processEvents()
        self.assertEqual(self.widget.scroll.horizontalScrollBar().maximum(), 0)

    def test_received_markup_like_text_stays_literal_plain_text(self):
        detail = self.detail()
        detail["callsign"] = "<b>TEST10</b>"
        detail["wake_vortex"] = "<img src=x>"
        detail["flight_status_text"] = "<script>literal</script>"
        self.widget.set_detail(detail)
        self.assertEqual(self.widget.callsign.text(), "<b>TEST10</b>")
        self.assertEqual(self.widget.callsign.textFormat().name, "PlainText")
        self.assertEqual(self.widget.rows["wake_vortex"][0].text(), "<img src=x>")
        self.assertEqual(self.widget.rows["wake_vortex"][0].textFormat().name, "PlainText")
        self.assertEqual(self.widget.extra_rows["flight_status_text"][1].text(), "<script>literal</script>")
        self.assertEqual(self.widget.extra_rows["flight_status_text"][1].textFormat().name, "PlainText")

    def test_primary_metrics_select_real_sources_and_distinguish_missing_from_stale(self):
        detail = self.detail()
        detail.update(altitude=12000, altitude_source="barometric", heading=90)
        self.widget.set_detail(detail)
        altitude = self.widget.metrics["altitude"]
        self.assertEqual(altitude.field_key, "altitude_baro")
        self.assertEqual(altitude.value.text(), "12,000")
        self.assertIn("Barometric", altitude.source.text())
        self.assertEqual(self.widget.metrics["speed"].value.text(), "0")
        self.assertEqual(self.widget.metrics["track"].value.text(), "—")
        self.assertEqual(self.widget.metrics["track"].note.text(), "Not received")
        self.assertNotIn("altitude", self.widget.extra_rows)  # Alias is not another measurement.

        detail.update(altitude_baro=None, altitude_gnss=0, altitude=0, altitude_source="GNSS")
        self.widget.set_detail(detail)
        self.assertEqual(altitude.field_key, "altitude_gnss")
        self.assertEqual(altitude.value.text(), "0")
        self.assertIn("GNSS", altitude.source.text())
        detail.update(altitude_gnss=None, altitude=None)
        self.widget.set_detail(detail)
        self.assertEqual(altitude.value.text(), "—")
        self.assertIn("Stale · unavailable", altitude.note.text())
        self.assertIn("ago", altitude.note.text())
        self.assertEqual(self.widget.rows["altitude_baro"][0].text(), "Stale · unavailable")

        self.widget.set_detail({"icao": "A00010", "altitude": 0, "altitude_source": "Unspecified",
                                "true_track": 0, "gnss_vertical_rate": 0})
        self.assertEqual(altitude.field_key, "altitude")
        self.assertEqual(altitude.value.text(), "0")
        self.assertEqual(self.widget.metrics["track"].value.text(), "0")
        self.assertIn("GNSS", self.widget.metrics["vertical_rate"].source.text())

    def test_disclosures_work_by_keyboard_preserve_state_and_expose_received_fields(self):
        detail = self.detail()
        self.widget.set_detail(detail)
        section = self.widget.sections["Reported targets & modes"]
        self.assertFalse(section.toggle.isChecked())
        self.assertTrue(section.body.isHidden())
        self.assertTrue(self.widget.metrics["altitude"].isVisible())
        section.toggle.setFocus()
        QTest.keyClick(section.toggle, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertTrue(section.toggle.isChecked())
        self.assertFalse(section.body.isHidden())
        self.assertFalse(self.widget.rows["autopilot"][0].isHidden())
        self.assertEqual(self.widget.rows["autopilot"][0].text(), "Off / no")
        self.assertTrue(self.widget.rows["vnav_mode"][0].isHidden())
        self.widget.set_detail({**detail, "autopilot": True})
        self.app.processEvents()
        self.assertTrue(section.toggle.isChecked())
        self.assertTrue(section.toggle.hasFocus())
        self.assertEqual(self.widget.rows["autopilot"][0].text(), "On / yes")
        QTest.keyClick(section.toggle, Qt.Key.Key_Space)
        self.assertTrue(section.body.isHidden())

    def test_narrow_layout_contains_visible_controls_and_long_received_text(self):
        detail = self.detail()
        detail.update(callsign="TEST-LONG-CALLSIGN", latitude=-12.345678, longitude=123.456789,
                      distance_nm=123.4, squawk="1200", wake_vortex="A long received emitter category",
                      vr_source="Barometric", vertical_rate=-1200,
                      flight_status_text="Synthetic received status for layout verification")
        self.widget.set_detail(detail, input_type="beast-serial")
        for width in (420, 360, 330):
            self.widget.resize(width, 900)
            self.app.processEvents()
            self.assertEqual(self.widget.width(), width)
            self.assertEqual(self.widget.content.width(), self.widget.scroll.viewport().width())
            self.assertEqual(self.widget.scroll.horizontalScrollBar().maximum(), 0)
            self.assertLess(self.widget.summary.mapTo(self.widget, QPoint()).y() + self.widget.summary.height(), 700)
            for section in (*self.widget.sections.values(), self.widget.extra_section):
                section.toggle.setChecked(True)
            self.app.processEvents()
            children = self.widget.content.findChildren(QLabel) + self.widget.content.findChildren(QToolButton)
            for child in children:
                if child.isVisibleTo(self.widget.content):
                    origin = child.mapTo(self.widget.content, QPoint())
                    self.assertGreaterEqual(origin.x(), 0)
                    self.assertLessEqual(origin.x() + child.width(), self.widget.content.width(), child.objectName())
            for section in (*self.widget.sections.values(), self.widget.extra_section):
                section.toggle.setChecked(False)

    def test_ages_transport_and_lost_state_remain_honest(self):
        now = time.time()
        with patch("frw.native_details.time.time", return_value=now):
            self.widget.set_detail({"icao": "A00010", "data_source": "ADS-B", "groundspeed": 0,
                                    "field_ages": {"groundspeed": 4}}, input_type="sbs-tcp")
        self.assertEqual(self.widget.source.text(), "ADS-B via SBS TCP")
        with patch("frw.native_details.time.time", return_value=now + 10):
            self.widget._refresh_ages()
            self.assertEqual(self.widget.metrics["speed"].note.text(), "14s ago")
            self.widget.mark_lost()
        self.assertIn("Last known", self.widget.metrics["speed"].note.text())
        self.widget.set_detail(self.detail(), input_type="iq-file")
        self.assertEqual(self.widget.status.text(), "Recorded reception")
        self.assertEqual(self.widget.source.text(), "Recorded ADS-B")

    def test_chart_uses_field_timestamps_and_breaks_long_gaps_without_filling(self):
        now = time.time()
        chart = self.widget.history_altitude
        chart.set_samples([
            {"time": now - 1, "field_times": {"altitude_baro": now - 90}, "altitude_baro": 0, "altitude": 0},
            {"time": now - 1, "field_times": {"altitude_baro": now - 70}, "altitude_baro": 100},
            {"time": now - 1, "field_times": {"altitude_baro": now - 20}, "altitude_baro": 200},
            {"time": now - 2, "field_times": {"altitude": now - 60}, "altitude": 50},
            {"time": now - 700, "altitude_gnss": 800},
        ])
        series = {label: points for label, _color, points in chart.series_points(now)}
        self.assertEqual(series["Baro"], [(now - 90, 0.0), (now - 70, 100.0), (now - 20, 200.0)])
        self.assertEqual([len(segment) for segment in chart.segments(series["Baro"])], [2, 1])
        self.assertEqual(series["Other"], [(now - 60, 50.0)])
        self.assertNotIn("GNSS", series)
        self.assertIn("latest 200 feet", chart.accessibleDescription())

    def test_expired_extra_fields_retain_age_provenance_and_last_known_context(self):
        now = time.time()
        detail = {"icao": "A00010", "custom_report": 0, "field_times": {"custom_report": now - 80},
                  "field_sources": {"custom_report": "Synthetic source"}}
        self.widget.set_detail(detail)
        self.assertEqual(self.widget.extra_rows["custom_report"][1].text(), "0")
        self.assertIn("via Synthetic source", self.widget.extra_notes["custom_report"].text())
        detail.pop("custom_report")
        self.widget.set_detail(detail)
        self.widget.mark_lost()
        self.assertEqual(self.widget.extra_rows["custom_report"][1].text(), "Stale · unavailable")
        self.assertIn("Last known", self.widget.extra_notes["custom_report"].text())
        self.assertIn("via Synthetic source", self.widget.extra_notes["custom_report"].text())


if __name__ == "__main__":
    unittest.main()
