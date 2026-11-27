import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from frw.config import ConfigError
from frw.fr24_api import Fr24Overlay, validate_api_settings


class Response:
    def __init__(self, payload, status=200, headers=None):
        self.payload = json.dumps(payload).encode()
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.payload[:size]


class Fr24OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.overlay = Fr24Overlay(Path(self.temp.name))

    def tearDown(self):
        self.overlay.close()
        self.temp.cleanup()

    def configure(self, **changes):
        settings = dict(self.overlay.preferences())
        settings.pop('has_token')
        settings.update(changes)
        self.overlay.configure(settings, token='fixture-token')

    def test_defaults_and_bounds_validation(self):
        preferences = self.overlay.preferences()
        self.assertFalse(preferences['enabled'])
        self.assertEqual(preferences['endpoint'], 'full')
        self.assertFalse(preferences['has_token'])
        with self.assertRaises(ConfigError):
            validate_api_settings({'enabled': True, 'bounds': ''})
        with self.assertRaises(ConfigError):
            validate_api_settings({'enabled': True, 'bounds': '0,1,2,3'})
        valid = validate_api_settings({'enabled': True, 'bounds': '-30,-35,130,140'})
        self.assertEqual(valid['bounds'], '-30,-35,130,140')

    @unittest.skipUnless(os.name == 'nt', 'DPAPI token persistence is Windows-only')
    def test_token_is_protected_and_never_returned_in_preferences(self):
        with patch('frw.fr24_api.protect', side_effect=lambda value, decrypt=False: value[::-1]):
            self.configure(enabled=True, bounds='-30,-35,130,140')
            self.assertTrue(self.overlay.preferences()['has_token'])
            self.assertNotIn('fixture-token', json.dumps(self.overlay.preferences()))
            self.assertNotIn(b'fixture-token', (Path(self.temp.name) / 'fr24-api-token.dpapi').read_bytes())

    def test_request_is_bounded_and_uses_authorization_header(self):
        captured = []
        def open_request(request, timeout):
            captured.append((request, timeout))
            return Response({'data': [{'icao': 'abc123', 'lat': -34.9, 'lon': 138.6}]})
        settings = validate_api_settings({'enabled': True, 'bounds': '-30,-35,130,140', 'limit': 20})
        opener = type('Opener', (), {'open': staticmethod(open_request)})
        with patch('frw.fr24_api.urllib.request.build_opener', return_value=opener()):
            rows = Fr24Overlay._request(settings, 'fixture-token')
        request, timeout = captured[0]
        self.assertEqual(rows[0]['icao'], 'abc123')
        self.assertEqual(timeout, 15)
        self.assertIn('bounds=', request.full_url)
        self.assertIn('limit=20', request.full_url)
        self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-token')
        self.assertNotIn('fixture-token', request.full_url)

    def test_rate_error_is_sanitized(self):
        from frw.fr24_api import Fr24Overlay
        from urllib.error import HTTPError
        error = HTTPError('https://api.example/private', 429, 'too many', {'Retry-After': '120'}, None)
        message = Fr24Overlay._safe_error(error)
        self.assertIn('120 seconds', message)
        self.assertNotIn('api.example', message)

    def test_poll_failure_updates_safe_status_and_cooldown(self):
        from urllib.error import HTTPError
        self.configure(enabled=True, bounds='-30,-35,130,140')
        failure = HTTPError('https://provider.invalid/path', 429, 'private response', {'Retry-After': '120'}, None)
        with patch('frw.fr24_api.Fr24Overlay._request_json', side_effect=failure):
            self.overlay._fetch_worker(self.overlay._generation, self.overlay._settings, 'private-token')
        snapshot = self.overlay.snapshot()
        self.assertIn('rate limit reached (429)', snapshot['status'])
        self.assertNotIn('private-token', json.dumps(snapshot))
        self.assertNotIn('provider.invalid', snapshot['status'])
        self.assertEqual(self.overlay._cooldown_until > time.monotonic(), True)

    def test_settings_change_discards_previous_snapshot(self):
        self.overlay._records = [{'icao': 'abc123'}]
        self.overlay._received_at = time.time()
        settings = dict(self.overlay.preferences())
        settings.pop('has_token')
        settings['interval_seconds'] = 120
        self.overlay.configure(settings)
        self.assertEqual(self.overlay.snapshot()['records'], [])

    def test_poll_is_opt_in_single_flight_and_discards_late_response(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(*args):
            entered.set()
            release.wait(4)
            return {'data': [{'hex': 'ABC123'}]}
        with patch.object(self.overlay, '_request_json', side_effect=delayed) as call:
            self.overlay.tick()
            call.assert_not_called()
            self.configure(enabled=True, bounds='2,1,3,4')
            self.overlay.tick()
            try:
                self.assertTrue(entered.wait(2))
                self.overlay.tick()
                self.overlay.configure({**self.overlay.preferences(), 'enabled': False}, token='')
            finally:
                release.set()
                self.overlay._worker.join(3)
            self.assertFalse(self.overlay._worker.is_alive())
            self.assertEqual(call.call_count, 1)
            self.assertFalse(self.overlay.preferences()['has_token'])
            self.assertEqual(self.overlay.snapshot()['records'], [])

    def test_manual_queries_obey_interval_and_do_not_echo_provider_errors(self):
        from urllib.error import HTTPError
        self.configure(interval_seconds=90)
        with patch('frw.fr24_api.time.monotonic', return_value=200), patch.object(self.overlay, '_request_json', return_value={'data': []}):
            self.assertEqual(self.overlay.request_json('/usage', {}), {'data': []})
            with self.assertRaisesRegex(ConfigError, '90 seconds'):
                self.overlay.request_json('/flight-tracks', {'flight_id': 'abcd1234'})
        error = HTTPError('https://fr24api.flightradar24.com', 403, 'private token echo', {}, None)
        with patch('frw.fr24_api.time.monotonic', return_value=300), patch.object(self.overlay, '_request_json', side_effect=error):
            with self.assertRaisesRegex(ConfigError, '403') as raised:
                self.overlay.request_json('/usage', {})
            self.assertNotIn('private', str(raised.exception))

    def test_network_boundary_refuses_redirects_and_unexpected_paths(self):
        from frw.fr24_api import _NoRedirectHandler
        self.assertIsNone(_NoRedirectHandler().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid'))
        for path in ('https://elsewhere.invalid', '//elsewhere.invalid', '/api/feeder', '/static/airports/KJFK/../../private'):
            with self.subTest(path=path), self.assertRaises(ConfigError):
                Fr24Overlay._request_json('fixture-token', path, {})

    def test_corrupt_profile_fails_closed(self):
        self.overlay._settings_path.write_bytes(b'\xff\xfe')
        restarted = Fr24Overlay(self.temp.name)
        try:
            self.assertFalse(restarted.preferences()['enabled'])
            self.assertIn('settings', restarted.snapshot()['status'])
        finally:
            restarted.close()


if __name__ == '__main__':
    unittest.main()
