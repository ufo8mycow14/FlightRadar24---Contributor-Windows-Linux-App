import copy
import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from frw.fr24_display import merge_display_rows


class Fr24DisplayMergeTests(unittest.TestCase):
    def test_matching_icao_augments_local_row_without_replacing_it(self):
        local = [{"icao": "a00001", "callsign": "LOCAL1", "latitude": -34.9,
                  "data_source": "ADS-B", "messages": 9}]
        api_record = {"hex": "A00001", "flight": "API123", "lat": 10.5,
                      "lon": 20.5, "registration": "VH-ABC", "nested": {"x": 1}}
        before = copy.deepcopy((local, api_record))
        result = merge_display_rows(local, {"records": [api_record], "received_at": 100,
                                            "status": "Connected"}, now=110)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["callsign"], "LOCAL1")
        self.assertEqual(result[0]["latitude"], -34.9)
        self.assertEqual(result[0]["data_source"], "ADS-B")
        self.assertEqual(result[0]["messages"], 9)
        self.assertEqual(result[0]["fr24_data"], api_record)
        self.assertEqual(result[0]["fr24_received_at"], 100)
        self.assertEqual((local, api_record), before)
        result[0]["fr24_data"]["nested"]["x"] = 2
        self.assertEqual(api_record["nested"]["x"], 1)

    def test_callsign_does_not_match_and_fr24_id_keeps_missing_hex_aircraft(self):
        local = [{"icao": "A00001", "callsign": "SAME"}]
        records = [
            {"flight": "SAME", "id": "opaque-7", "lat": "-30.0", "lon": "140.0"},
            {"flight": "SAME", "id": "opaque-8", "lat": None, "lon": None},
        ]
        result = merge_display_rows(local, {"records": records, "received_at": 95}, now=100)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[1]["icao"], "FR24:opaque-7")
        self.assertEqual(result[1]["source"], "fr24-api")
        self.assertEqual(result[1]["latitude"], -30.0)
        self.assertEqual(result[2]["icao"], "FR24:opaque-8")
        self.assertIsNone(result[2].get("latitude"))

    def test_invalid_and_duplicate_records_are_ignored_safely(self):
        result = merge_display_rows([], {"records": [None, {}, {"hex": "bad"},
                                                       {"hex": "ABCDEF"}, {"icao": "abcdef"}]}, now=10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["icao"], "ABCDEF")

    def test_fr24_v1_fields_and_position_timestamp_are_normalized(self):
        record = {"fr24_id": "opaque", "hex": "ABCDEF", "callsign": "TEST",
                  "lat": -34.9, "lon": 138.6, "alt": 12000, "gspeed": 210,
                  "vspeed": -500, "track": 0, "timestamp": 1800000000}
        row = merge_display_rows([], {"records": [record], "received_at": 1800000010}, now=1800000015)[0]
        self.assertEqual(row["altitude"], 12000.0)
        self.assertEqual(row["groundspeed"], 210.0)
        self.assertEqual(row["vertical_rate"], -500.0)
        self.assertEqual(row["track"], 0.0)
        self.assertEqual(row["last_seen"], 1800000000)
        self.assertEqual(row["seen"], 15)
        self.assertEqual(row["position_time"], 1800000000)

    def test_naive_timestamp_is_utc_and_unsafe_values_are_omitted(self):
        utc_stamp = datetime.datetime(2026, 11, 27, tzinfo=datetime.timezone.utc).timestamp()
        record = {"fr24_id": "opaque", "callsign": {"bad": "shape"}, "flight": "FLIGHT1",
                  "lat": True, "lon": 138.6, "alt": 10**1000, "gspeed": True,
                  "track": 0, "timestamp": "2026-11-27T00:00:00"}
        row = merge_display_rows([], {"records": [record], "received_at": utc_stamp + 5},
                                 now=utc_stamp + 10)[0]
        self.assertEqual(row["last_seen"], utc_stamp)
        self.assertEqual(row["seen"], 10)
        self.assertNotIn("callsign", row)
        self.assertNotIn("latitude", row)
        self.assertNotIn("longitude", row)
        self.assertNotIn("altitude", row)
        self.assertNotIn("groundspeed", row)
        self.assertEqual(row["track"], 0)

    def test_id_records_are_prioritized_over_hex_only_aliases(self):
        records = [{"hex": "ABCDEF", "callsign": "HEX ONLY"},
                   {"fr24_id": "stable", "hex": "ABCDEF", "callsign": "WITH ID"},
                   {"fr24_id": "stable", "callsign": "ID WITHOUT HEX"}]
        result = merge_display_rows([], {"records": records}, now=100)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["icao"], "ABCDEF")
        self.assertEqual(result[0]["callsign"], "WITH ID")
        self.assertEqual(result[0]["fr24_identity"], "id:stable")

    def test_invalid_coordinates_are_not_plotted_and_shared_id_deduplicates(self):
        records = [{"fr24_id": "same", "hex": "ABCDEF", "lat": 91, "lon": 5},
                   {"fr24_id": "same", "lat": 30, "lon": 40}]
        result = merge_display_rows([], {"records": records}, now=100)
        self.assertEqual(len(result), 1)
        self.assertNotIn("latitude", result[0])
        self.assertNotIn("longitude", result[0])

    def test_missing_snapshot_records_returns_local_copy(self):
        local = [{"icao": "A00001", "trail": [[1, 2, 3]]}]
        result = merge_display_rows(local, {}, now=10)
        self.assertEqual(result, local)
        self.assertIsNot(result[0], local[0])
        result[0]["trail"][0][0] = 99
        self.assertEqual(local[0]["trail"][0][0], 1)


if __name__ == "__main__":
    unittest.main()
