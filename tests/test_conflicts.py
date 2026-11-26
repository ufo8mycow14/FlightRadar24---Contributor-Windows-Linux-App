"""Focused behavioural coverage for the pure conflict estimator."""
import copy
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from frw.conflicts import _limits, _prepare, evaluate_conflicts

NOW = 1_700_000_000.0
FIELDS = ('latitude', 'longitude', 'altitude_baro', 'groundspeed', 'track',
          'baro_vertical_rate', 'on_ground')


def plane(icao, lat, lon, altitude=30000, speed=0, track=0, vertical_rate=0,
          ground=False, age=0, callsign=None):
    """Build a complete aircraft observation row with controllable field ages."""
    return {
        'icao': icao, 'callsign': callsign or icao, 'latitude': lat, 'longitude': lon,
        'altitude_baro': altitude, 'groundspeed': speed, 'track': track,
        'baro_vertical_rate': vertical_rate, 'on_ground': ground,
        'field_times': {name: NOW-age for name in FIELDS},
    }


def evaluate(a, b, now=NOW):
    return evaluate_conflicts({a['icao']: a, b['icao']: b}, now)


class ConflictTests(unittest.TestCase):
    def test_position_alignment_transports_track_across_the_pole(self):
        row = plane('AAAAAA', 89.999, 0, altitude=15000, speed=360, track=0, age=5)
        aligned = _prepare('AAAAAA', row, NOW)
        self.assertAlmostEqual(aligned['track'], 180, places=4)
        self.assertGreater(aligned['lat'], 89.9)
        self.assertLess(aligned['lat'], 89.999)

    def test_implausible_altitude_and_vertical_rate_are_unassessed(self):
        for key in ('altitude_baro', 'baro_vertical_rate'):
            row = plane('AAAAAA', 0, 0)
            row[key] = 1e308
            self.assertEqual(evaluate_conflicts([row], NOW)['unassessed'], 1)

    def test_head_on_red_marks_both_and_does_not_mutate_rows(self):
        rows = {'ABCDEF': plane('ABCDEF', 0, -.005, speed=450, track=90),
                '123456': plane('123456', 0, .005, speed=450, track=270)}
        original = copy.deepcopy(rows)
        result = evaluate_conflicts(rows, NOW)
        self.assertEqual(rows, original)
        self.assertEqual(result['pairs'], 1)
        self.assertEqual(result['alerts']['ABCDEF']['level'], 'red')
        self.assertEqual(result['alerts']['123456']['partners'][0]['icao'], 'ABCDEF')

    def test_red_volume_is_solved_independently_from_yellow_entry(self):
        a = plane('AAAAAA', 0, 0, altitude=30000)
        b = plane('BBBBBB', 0, .1/60, altitude=31000, vertical_rate=-6000)
        partner = evaluate(a, b)['alerts']['AAAAAA']['partners'][0]
        self.assertEqual(partner['level'], 'red')
        self.assertAlmostEqual(partner['seconds'], 3.0, places=1)
        self.assertAlmostEqual(partner['vertical_ft'], 700, delta=1)

    def test_currently_inside_red_volume_alerts_even_when_diverging(self):
        a = plane('AAAAAA', 0, 0, altitude=15000, speed=450, track=270)
        b = plane('BBBBBB', 0, .05/60, altitude=15100, speed=450, track=90)
        item = evaluate(a, b)['alerts']['AAAAAA']['partners'][0]
        self.assertEqual(item['level'], 'red')
        self.assertEqual(item['seconds'], 0)

    def test_parallel_traffic_outside_horizontal_limit_is_clear(self):
        a = plane('AAAAAA', 0, 0, altitude=15000, speed=300, track=90)
        b = plane('BBBBBB', .03, 0, altitude=15000, speed=300, track=90)
        self.assertFalse(evaluate(a, b)['alerts'])

    def test_crossing_routes_reaching_intersection_at_different_times(self):
        # A reaches (0, 0) after about 24 s; B passed it before the evaluation instant.
        a = plane('AAAAAA', 0, -.2, altitude=15000, speed=30, track=90)
        b = plane('BBBBBB', -.2, 0, altitude=15000, speed=450, track=0)
        self.assertFalse(evaluate(a, b)['alerts'])

    def test_vertical_window_that_does_not_overlap_horizontal_window_is_clear(self):
        a = plane('AAAAAA', 0, 0, altitude=20000, speed=6000, track=90)
        b = plane('BBBBBB', 0, .1/60, altitude=21200, speed=6000, track=270,
                  vertical_rate=-6000)
        self.assertFalse(evaluate(a, b)['alerts'])

    def test_vertical_closure_triggers_pair(self):
        a = plane('AAAAAA', 0, 0, altitude=20000, vertical_rate=600)
        b = plane('BBBBBB', .01, 0, altitude=20800, vertical_rate=-600)
        self.assertEqual(evaluate(a, b)['pairs'], 1)

    def test_altitude_uses_its_own_timestamp_for_alignment(self):
        a = plane('AAAAAA', 0, 0, altitude=30000)
        b = plane('BBBBBB', 0, .05/60, altitude=31000, vertical_rate=-6000)
        b['field_times']['altitude_baro'] = NOW-5
        self.assertEqual(evaluate(a, b)['alerts']['AAAAAA']['partners'][0]['level'], 'red')

    def test_position_coordinates_must_have_matching_timestamps(self):
        a, b = plane('AAAAAA', 0, 0), plane('BBBBBB', 0, .01)
        b['field_times']['longitude'] -= .1
        result = evaluate(a, b)
        self.assertEqual((result['assessed'], result['unassessed']), (1, 1))

    def test_rejects_stale_missing_ground_invalid_and_gnss_only_rows(self):
        rows = {
            'AAAAAA': plane('AAAAAA', 0, 0, age=5.01),
            'BBBBBB': plane('BBBBBB', 0, 0, ground=True),
            'CCCCCC': plane('CCCCCC', 91, 0),
            'DDDDDD': plane('DDDDDD', 0, 0),
            'EEEEEE': plane('EEEEEE', 0, 0),
        }
        del rows['DDDDDD']['track']
        rows['EEEEEE']['altitude_baro'] = None
        rows['EEEEEE']['altitude_gnss'] = 30000
        result = evaluate_conflicts(rows, NOW)
        self.assertEqual((result['assessed'], result['unassessed']), (0, 5))

    def test_age_exactly_five_seconds_is_fresh_and_time_is_mockable(self):
        row = plane('ABCDEF', 0, 0, age=5)
        with patch('frw.conflicts.time.time', return_value=NOW):
            result = evaluate_conflicts([row])
        self.assertEqual(result['assessed'], 1)

    def test_nonfinite_now_and_malformed_rows_are_counted_unassessed(self):
        self.assertEqual(evaluate_conflicts([None, {}, {'icao': 'bad'}], math.nan)['unassessed'], 3)
        self.assertEqual(evaluate_conflicts([None, {}, {'icao': 'bad'}], NOW)['unassessed'], 3)

    def test_duplicate_normalized_identity_is_not_compared_to_itself(self):
        a = plane('abcdef', 0, 0)
        b = plane('ABCDEF', 0, .001)
        result = evaluate_conflicts([a, b], NOW)
        self.assertEqual((result['assessed'], result['unassessed'], result['pairs']), (1, 1, 0))

    def test_dateline_proximity(self):
        a = plane('AAAAAA', 0, 179.999, altitude=15000)
        b = plane('BBBBBB', 0, -179.999, altitude=15000)
        self.assertIn('AAAAAA', evaluate(a, b)['alerts'])

    def test_antipodes_are_rejected_before_local_tangent_projection(self):
        a = plane('AAAAAA', 0, 0, altitude=15000)
        b = plane('BBBBBB', 0, 180, altitude=15000)
        self.assertFalse(evaluate(a, b)['alerts'])

    def test_implausible_speed_is_unassessed(self):
        a = plane('AAAAAA', 0, 0, speed=1500.1)
        result = evaluate_conflicts([a], NOW)
        self.assertEqual((result['assessed'], result['unassessed']), (0, 1))

    def test_summary_is_bounded_but_partner_details_are_complete(self):
        rows = {f'{index:06X}': plane(f'{index:06X}', 0, index*.0001)
                for index in range(1, 6)}
        result = evaluate_conflicts(rows, NOW)
        alert = result['alerts']['000001']
        self.assertEqual(len(alert['partners']), 4)
        self.assertIn('and 1 more', alert['summary'])

    def test_high_latitude_differing_longitudes_use_common_tangent_velocities(self):
        a = plane('AAAAAA', 89.9, 0, altitude=15000, speed=300, track=90)
        b = plane('BBBBBB', 89.9, .1, altitude=15000, speed=300, track=270)
        result = evaluate(a, b)
        self.assertIn('AAAAAA', result['alerts'])
        self.assertEqual(result['alerts']['AAAAAA']['level'], 'red')

    def test_agl_fallback_altitude_band_boundaries(self):
        self.assertEqual(_limits(4999), (25, 15, .33, .20, 850, 600))
        self.assertEqual(_limits(5000), (40, 25, .75, .55, 850, 600))
        self.assertEqual(_limits(9999), (40, 25, .75, .55, 850, 600))
        self.assertEqual(_limits(10000), (45, 30, 1.0, .8, 850, 600))
        self.assertEqual(_limits(20000), (48, 35, 1.3, 1.1, 850, 700))

    def test_yellow_vertical_threshold_remains_850_through_fl420(self):
        self.assertEqual(_limits(30001)[4:], (850, 700))
        self.assertEqual(_limits(42000)[4:], (850, 700))
        self.assertEqual(_limits(42001)[4:], (1200, 800))

    def test_disabled_evaluation_assesses_but_returns_no_alerts(self):
        result = evaluate_conflicts([plane('ABCDEF', 0, 0)], NOW, enabled=False)
        self.assertEqual(result['assessed'], 1)
        self.assertEqual(result['alerts'], {})

    def test_alert_details_summary_names_partner_severity_time_and_separation(self):
        a, b = plane('AAAAAA', 0, 0), plane('BBBBBB', 0, .001)
        alert = evaluate(a, b)['alerts']['AAAAAA']
        self.assertIn('red with BBBBBB', alert['summary'])
        self.assertIn('NM /', alert['summary'])
        self.assertEqual(set(alert['partners'][0]),
                         {'icao', 'callsign', 'level', 'seconds', 'horizontal_nm', 'vertical_ft'})


if __name__ == '__main__':
    unittest.main()
