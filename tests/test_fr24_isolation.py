"""Guard the data direction between the FR24 API display and receiver paths."""
from pathlib import Path
import copy
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "work" / "applibs"))

from frw.activity import HistoryStore
from frw.aircraft import Aircraft
from frw.config import DEFAULT
from frw.features import FeatureSettings
from frw.fr24_display import merge_display_rows
from frw.runtime import Receiver
from frw.streams import Frame


class Fr24DataIsolationTests(unittest.TestCase):
    def test_api_display_rows_never_enter_local_storage_or_receiver_outputs(self):
        with tempfile.TemporaryDirectory(prefix="frw-fr24-isolation-") as directory:
            observed = []
            receiver = Receiver.__new__(Receiver)
            receiver.aircraft = Aircraft(copy.deepcopy(DEFAULT),
                                         lambda row, stamp: observed.append((stamp, "live", row)))
            receiver.capture = Mock()
            receiver.quality = Mock()
            receiver.quality.next_summary = float("inf")
            receiver.frames = 0
            receiver.last_frame = None
            receiver.outputs = {name: Mock() for name in ("raw", "beast", "bs")}
            receiver.feed_stream = Mock()
            receiver.feed_format = "beast"
            frame = Frame(bytes.fromhex("8D40621D58C382D690C8AC2863A7"), 0x1A1234561A12,
                          0x80, 0)

            Receiver._frame(receiver, frame)

            local_snapshot = receiver.aircraft.snapshot()
            self.assertTrue(local_snapshot["aircraft"])
            local_aircraft = local_snapshot["aircraft"][0]
            address = local_aircraft["icao"]
            self.assertEqual(address, "40621D")
            local_observation = observed[0][2]
            api_records = [
                {"hex": address, "flight": "API-DUPLICATE", "lat": -34.9,
                 "lon": 138.6, "registration": "API-ONLY-FIELD"},
                {"fr24_id": "api-only-flight", "flight": "API-ONLY",
                 "lat": -30.0, "lon": 140.0},
            ]
            display_rows = merge_display_rows(
                local_snapshot["aircraft"],
                {"records": api_records, "received_at": time.time(), "status": "API updated"})

            self.assertEqual(len(display_rows), len(local_snapshot["aircraft"]) + 1)
            matched = next(row for row in display_rows if row["icao"] == address)
            self.assertEqual(matched.get("callsign"), local_aircraft.get("callsign"))
            self.assertEqual((matched["latitude"], matched["longitude"]), (-34.9, 138.6))
            self.assertTrue(matched["fr24_position"])
            self.assertIsNone(local_aircraft.get("latitude"))
            self.assertEqual(matched["fr24_data"]["registration"], "API-ONLY-FIELD")
            self.assertTrue(any(row.get("source") == "fr24-api" for row in display_rows))
            self.assertTrue(all("fr24_data" not in row for row in local_snapshot["aircraft"]))

            receiver.outputs["raw"].publish.assert_called_once_with(frame.avr())
            receiver.outputs["beast"].publish.assert_called_once_with(frame.beast())
            receiver.outputs["bs"].publish.assert_called_once()
            receiver.feed_stream.publish.assert_called_once_with(frame.beast())
            receiver.capture.submit.assert_called_once_with(
                unittest.mock.ANY, "mode-s", frame.data, frame.timestamp, frame.signal, frame.corrected)
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0][2], local_observation)
            self.assertNotIn("fr24_data", observed[0][2])
            self.assertNotEqual(observed[0][2].get("callsign"), "API-DUPLICATE")

            feature_settings = FeatureSettings(Path(directory)).value
            store = HistoryStore(Path(directory))
            store.write(observed, [], feature_settings, None)
            report = store.report(feature_settings, hours=24, mode="live")
            self.assertTrue(report["encounters"])
            self.assertEqual({row["icao"] for row in report["encounters"]}, {address})
            self.assertNotIn("API-ONLY-FIELD", repr(report))
            self.assertNotIn("API-ONLY", repr(report))
            self.assertNotIn("API-DUPLICATE", repr(report))


if __name__ == "__main__":
    unittest.main()
