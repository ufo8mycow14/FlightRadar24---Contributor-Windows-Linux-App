"""Bounded, display-only access to the Flightradar24 live positions API."""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import os
import re
import threading
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import ConfigError, protect
from .fr24_catalog import ENDPOINTS

DEFAULT_API_SETTINGS = {
    'enabled': False,
    'endpoint': 'full',
    'interval_seconds': 60,
    'bounds': '',
    'limit': 20,
}
MIN_INTERVAL_SECONDS = 15
MAX_INTERVAL_SECONDS = 3600
MAX_LIMIT = 5000
ROUTE_PARAMETERS = {endpoint.path: frozenset(key for key, _, _ in endpoint.fields)
                    for endpoint in ENDPOINTS if '{' not in endpoint.path}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _retry_after_seconds(value):
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return min(86400, int(value[:10]))
    if not value:
        return None
    try:
        parsed = dt.datetime.strptime(value, '%a, %d %b %Y %H:%M:%S GMT').replace(tzinfo=dt.timezone.utc)
        return min(86400, max(0, int((parsed - dt.datetime.now(dt.timezone.utc)).total_seconds())))
    except (ValueError, OverflowError):
        return None


def validate_api_settings(settings):
    if not isinstance(settings, dict):
        raise ConfigError('API settings must be an object.')
    if set(settings) - set(DEFAULT_API_SETTINGS) - {'has_token'}:
        raise ConfigError('Unknown API settings.')
    value = dict(DEFAULT_API_SETTINGS)
    value.update({key: item for key, item in settings.items() if key in DEFAULT_API_SETTINGS})
    if type(value['enabled']) is not bool:
        raise ConfigError('API enabled must be true or false.')
    if value['endpoint'] not in ('light', 'full'):
        raise ConfigError('Choose the light or full live positions endpoint.')
    if type(value['interval_seconds']) is not int or not MIN_INTERVAL_SECONDS <= value['interval_seconds'] <= MAX_INTERVAL_SECONDS:
        raise ConfigError(f'API interval must be {MIN_INTERVAL_SECONDS}–{MAX_INTERVAL_SECONDS} seconds.')
    if type(value['limit']) is not int or not 1 <= value['limit'] <= MAX_LIMIT:
        raise ConfigError(f'API result limit must be 1–{MAX_LIMIT}.')
    if not isinstance(value['bounds'], str) or len(value['bounds']) > 100:
        raise ConfigError('Enter map bounds as north,south,west,east.')
    if value['enabled'] or value['bounds']:
        parts = value['bounds'].split(',')
        try:
            north, south, west, east = (float(part.strip()) for part in parts)
        except (ValueError, TypeError):
            raise ConfigError('Enter map bounds as north,south,west,east.') from None
        if len(parts) != 4 or any(not (-90 <= number <= 90) for number in (north, south)) or any(not (-180 <= number <= 180) for number in (west, east)) or north <= south or east <= west:
            raise ConfigError('Bounds need north above south and east of west, within geographic limits.')
        value['bounds'] = ','.join(format(number, '.7g') for number in (north, south, west, east))
    return value


def _validate_token(token):
    if not isinstance(token, str) or len(token) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise ConfigError('Enter a valid API token.')
    return token


class Fr24Overlay:
    def __init__(self, profile_dir):
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = self.profile_dir / 'fr24-api-settings.json'
        self._token_path = self.profile_dir / ('fr24-api-token.dpapi' if os.name == 'nt' else 'fr24-api-token')
        self._lock = threading.RLock()
        self._settings_status = 'API disabled'
        self._settings = self._read_settings()
        if self._settings['enabled'] and not self._token():
            self._settings['enabled'] = False
            self._settings_status = 'API disabled: token unavailable'
        elif self._settings['enabled']:
            self._settings_status = 'API ready'
        self._records = []
        self._received_at = None
        self._status = self._settings_status
        self._next_due = 0.0
        self._generation = 0
        self._worker = None
        self._closed = False
        self._failures = 0
        self._rate_limit_failures = 0
        self._cooldown_until = 0.0
        self._last_request_at = -math.inf
        self._last_usage_at = -math.inf
        self._network_lock = threading.Lock()

    def _read_settings(self):
        try:
            raw = json.loads(self._settings_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            raw = {}
        except (OSError, UnicodeError, ValueError, RecursionError):
            self._settings_status = 'API settings could not be read; review API settings'
            return dict(DEFAULT_API_SETTINGS)
        try:
            return validate_api_settings(raw)
        except ConfigError:
            self._settings_status = 'API settings are invalid; review API settings'
            return dict(DEFAULT_API_SETTINGS)

    def _token(self):
        if not self._token_path.exists():
            return ''
        try:
            raw = self._token_path.read_bytes()
            return _validate_token((protect(raw, True) if os.name == 'nt' else raw).decode('utf-8'))
        except (OSError, UnicodeError, ValueError):
            return ''

    def preferences(self):
        with self._lock:
            result = copy.deepcopy(self._settings)
            try:
                result['has_token'] = self._token_path.stat().st_size > 0
            except OSError:
                result['has_token'] = False
            return result

    def configure(self, settings, token=None):
        value = validate_api_settings(settings)
        if token is not None:
            token = _validate_token(token)
        if value['enabled'] and not (token or (self._token() if token is None else '')):
            raise ConfigError('Enter an API token before enabling the API overlay.')
        with self._lock:
            if token is None and self._settings == value:
                return
            if token is not None:
                previous = self._token_path.read_bytes() if self._token_path.exists() else None
                if token:
                    raw = token.encode('utf-8')
                    self._write_private(self._token_path, protect(raw) if os.name == 'nt' else raw)
                else:
                    self._token_path.unlink(missing_ok=True)
            try:
                self._write_private(self._settings_path, json.dumps(value, indent=2).encode('utf-8'))
            except OSError:
                if token is not None:
                    if previous is None:
                        self._token_path.unlink(missing_ok=True)
                    else:
                        self._write_private(self._token_path, previous)
                raise
            self._generation += 1
            self._settings = value
            self._records = []
            self._received_at = None
            self._next_due = 0.0
            self._status = 'API ready' if value['enabled'] else 'API disabled'
            self._settings_status = self._status

    @staticmethod
    def _write_private(path, payload):
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.')
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(payload)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def tick(self):
        with self._lock:
            if self._closed or not self._settings['enabled'] or self._worker is not None and self._worker.is_alive():
                return
            now = time.monotonic()
            if now < max(self._next_due, self._cooldown_until, self._last_request_at + self._settings['interval_seconds']):
                return
            token = self._token()
            if not token:
                self._status = 'API token unavailable'
                return
            generation = self._generation
            settings = copy.deepcopy(self._settings)
            self._next_due = max(now + settings['interval_seconds'], self._cooldown_until)
            worker = threading.Thread(target=self._fetch_worker, args=(generation, settings, token), daemon=True)
            self._worker = worker
            worker.start()

    def _fetch_worker(self, generation, settings, token):
        failure = None
        try:
            records = self._request(settings, token, lambda secret, path, params:
                                    self._provider_request(secret, path, params, generation))
            status = f'API updated · {len(records)} aircraft'
            received_at = time.time()
        except Exception as error:
            failure = error
            records = None
            status = self._safe_error(error)
            received_at = None
        with self._lock:
            if self._closed or generation != self._generation:
                return
            if records is not None:
                self._records = records
                self._received_at = received_at
                self._failures = 0
            else:
                self._failures += 1
                if isinstance(failure, urllib.error.HTTPError) and failure.code in (400, 401, 402, 403, 404, 422):
                    self._next_due = time.monotonic() + 3600
                elif self._failures > 1:
                    delay = min(900, 15 * (2 ** min(self._failures - 1, 6)))
                    self._next_due = max(self._next_due, time.monotonic() + delay)
            self._status = status

    @staticmethod
    def _request(settings, token, transport=None):
        path = '/live/flight-positions/' + settings['endpoint']
        request = transport or Fr24Overlay._request_json
        payload = request(token, path, {'bounds': settings['bounds'], 'limit': settings['limit']})
        if isinstance(payload, dict) and isinstance(payload.get('data'), list):
            payload = payload['data']
        if not isinstance(payload, list) or len(payload) > settings['limit'] or any(not isinstance(row, dict) for row in payload):
            raise ValueError('Unexpected API response format')
        return payload

    def request_json(self, path, params):
        with self._lock:
            generation = self._generation
            token = self._token()
        if not token:
            raise ConfigError('Store a token in View → Flightradar24 API settings before requesting data.')
        try:
            return self._provider_request(token, path, params, generation)
        except ConfigError:
            raise
        except Exception as error:
            raise ConfigError(self._safe_error(error)) from None

    def _provider_request(self, token, path, params, generation):
        with self._network_lock:
            with self._lock:
                now = time.monotonic()
                if self._closed or self._generation != generation:
                    raise ConfigError('API configuration changed; request cancelled.')
                if now < self._cooldown_until:
                    raise ConfigError('API provider cooldown is active.')
                if path == '/usage' and now - self._last_usage_at < 60:
                    raise ConfigError('API usage requests are limited to once per minute.')
                wait = self._last_request_at + self._settings['interval_seconds'] - now
                if wait > 0:
                    raise ConfigError(f'Wait {math.ceil(wait)} seconds before the next API request (your refresh interval).')
                self._last_request_at = now
                if path == '/usage':
                    self._last_usage_at = now
            try:
                result = self._request_json(token, path, params)
                with self._lock:
                    if self._closed or self._generation != generation:
                        raise ConfigError('API configuration changed; result discarded.')
                    self._rate_limit_failures = 0
                    self._cooldown_until = 0.0
                return result
            except urllib.error.HTTPError as error:
                with self._lock:
                    if error.code == 429:
                        self._rate_limit_failures += 1
                        retry = error.headers.get('Retry-After', '') if error.headers else ''
                        delay = _retry_after_seconds(retry)
                        delay = delay if delay is not None else min(3600, 30 * (2 ** min(self._rate_limit_failures - 1, 7)))
                        self._cooldown_until = time.monotonic() + delay
                    elif error.code in (401, 402):
                        self._next_due = time.monotonic() + 3600
                raise

    @staticmethod
    def _request_json(token, path, params):
        if not isinstance(path, str):
            raise ConfigError('Unsupported API endpoint or parameter.')
        allowed = ROUTE_PARAMETERS.get(path)
        if re.fullmatch(r'/static/(?:airports/[A-Z0-9]{3,4}/(?:light|full)|airlines/[A-Z0-9]{3}/light)', path):
            allowed = frozenset()
        if allowed is None or not isinstance(params, dict) or set(params) - allowed:
            raise ConfigError('Unsupported API endpoint or parameter.')
        for key, value in params.items():
            if not isinstance(value, (str, int, float)) or isinstance(value, bool) or isinstance(value, float) and not math.isfinite(value) or len(str(value)) > 512:
                raise ConfigError('Invalid API query parameter.')
        query = urllib.parse.urlencode(params)
        url = 'https://fr24api.flightradar24.com/api' + path + (('?' + query) if query else '')
        request = urllib.request.Request(url, headers={
            'Accept': 'application/json',
            'Accept-Version': 'v1',
            'Authorization': 'Bearer ' + token,
            'User-Agent': 'FlightRadar24-Desktop',
        })
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=15) as response:
            body = response.read(8 * 1024 * 1024 + 1)
        if len(body) > 8 * 1024 * 1024:
            raise ValueError('API response exceeded size limit')
        return json.loads(body.decode('utf-8'))

    @staticmethod
    def _safe_error(error):
        if isinstance(error, urllib.error.HTTPError):
            if error.code == 401:
                return 'API authentication failed (401). Check the API token.'
            if error.code == 402:
                return 'API credits are insufficient (402). Check your subscription.'
            if error.code == 403:
                return 'API access denied (403). Check your token permissions and subscription plan.'
            if error.code == 429:
                retry = error.headers.get('Retry-After', '') if error.headers else ''
                seconds = _retry_after_seconds(retry)
                if seconds is not None:
                    return f'API rate limit reached (429). Retry after {seconds} seconds.'
                return 'API rate limit reached (429). Wait before the next request.'
            return f'API request failed ({error.code}).'
        if isinstance(error, (urllib.error.URLError, TimeoutError, OSError)):
            return 'API request could not connect. Check internet access and try again.'
        if isinstance(error, ConfigError):
            return 'API request was held by its rate or lifecycle guard.'
        return 'API returned an invalid response.'

    def snapshot(self):
        with self._lock:
            if self._received_at is not None and time.time() - self._received_at > max(90, self._settings['interval_seconds'] * 3):
                self._records = []
                self._received_at = None
                if self._settings['enabled']:
                    self._status = 'API data expired · ' + self._status
            return {'records': copy.deepcopy(self._records), 'received_at': self._received_at, 'status': self._status, 'enabled': self._settings['enabled']}

    def close(self):
        with self._lock:
            self._closed = True
            self._generation += 1
            self._records = []
            self._received_at = None
