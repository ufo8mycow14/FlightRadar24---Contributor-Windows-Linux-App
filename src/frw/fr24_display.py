"""Merge Flightradar24 API records into a display-only aircraft view."""
from __future__ import annotations

import copy
import datetime
import math
import re
import time


_HEX = re.compile(r"^[0-9A-F]{6}$")
_FIELD_MAP = {
    "hex": "icao", "icao": "icao", "callsign": "callsign", "lat": "latitude",
    "lon": "longitude", "alt": "altitude", "gspeed": "groundspeed",
    "vspeed": "vertical_rate", "track": "track", "squawk": "squawk",
    "category": "category",
}
_NUMERIC_RANGES = {
    "latitude": (-90, 90), "longitude": (-180, 180), "altitude": (-2000, 100000),
    "groundspeed": (0, 2000), "vertical_rate": (-20000, 20000), "track": (0, 360),
}


def _identity(record):
    for key in ("icao", "hex", "icao24"):
        value = record.get(key)
        if isinstance(value, str):
            normalized = value.strip().upper()
            if _HEX.fullmatch(normalized):
                return normalized
    return None


def _provider_id(record):
    for key in ("fr24_id", "id", "flight_id"):
        value = record.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value and len(value) <= 128:
                return value
        elif isinstance(value, int) and not isinstance(value, bool) and value.bit_length() <= 128:
            return str(value)
    return None


def _api_key(record):
    provider_id = _provider_id(record)
    if provider_id:
        return "id:" + provider_id
    address = _identity(record)
    if address:
        return "icao:" + address
    return None


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _record_time(record, fallback, now):
    value = record.get("timestamp")
    if _number(value):
        stamp = float(value)
        return stamp if 946684800 <= stamp <= now + 86400 else fallback
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            stamp = parsed.timestamp()
            return stamp if 946684800 <= stamp <= now + 86400 else fallback
        except (ValueError, OverflowError, OSError):
            return fallback
    return fallback


def _safe_status(value):
    return value[:160] if isinstance(value, str) else ""


def _api_only_row(record, key, received_at, now):
    fallback = received_at if _number(received_at) else now
    if not _number(fallback) or fallback < 946684800 or fallback > now + 86400:
        fallback = now
    stamp = _record_time(record, fallback, now)
    row = {"source": "fr24-api", "data_source": "Flightradar24 API",
           "fr24_data": copy.deepcopy(record), "fr24_identity": key}
    address = _identity(record)
    row["icao"] = address if address else "FR24:" + key.removeprefix("id:")
    for source_key, target_key in _FIELD_MAP.items():
        value = record.get(source_key)
        if value is None or value == "":
            continue
        if target_key in _NUMERIC_RANGES:
            if isinstance(value, bool):
                continue
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            lower, upper = _NUMERIC_RANGES[target_key]
            if not math.isfinite(value) or not lower <= value <= upper:
                continue
        elif target_key == "callsign":
            if not isinstance(value, str):
                continue
            value = value.strip()[:64]
            if not value:
                continue
        elif target_key in ("icao", "squawk", "category"):
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                continue
            value = str(value)[:64]
        row.setdefault(target_key, value)
    lat, lon = row.get("latitude"), row.get("longitude")
    if _number(lat) and _number(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
        row["position_time"] = stamp
    else:
        row.pop("latitude", None)
        row.pop("longitude", None)
    row.update(last_seen=stamp, seen=max(0, now - stamp), messages=0,
               field_sources={key: "Flightradar24 API" for key in row
                              if key not in {"fr24_data", "field_sources"}},
               field_times={key: stamp for key in row
                            if key not in {"fr24_data", "field_times", "field_sources"}},
               field_ages={key: round(max(0, now - stamp), 1) for key in row
                           if key not in {"fr24_data", "field_ages", "field_sources"}})
    return row


def merge_display_rows(local_rows: list, api_snapshot: dict, now=None) -> list:
    """Return copied local rows augmented with API data and unmatched API rows.

    Matching uses ICAO24 only. Callsigns and mutable input rows are never used
    to establish identity. The returned rows are for display and must not be
    passed to receiver ingestion, persistence, alerting, statistics, or outputs.
    """
    now = time.time() if now is None else now
    local = [copy.deepcopy(row) for row in local_rows]
    snapshot = api_snapshot if isinstance(api_snapshot, dict) else {}
    records = snapshot.get("records")
    if not isinstance(records, list):
        return local
    by_address = {}
    for index, row in enumerate(local):
        address = _identity(row)
        if address:
            by_address.setdefault(address, index)
    received_at = snapshot.get("received_at")
    added = set()
    seen_addresses = set()
    api_rows = {}
    ordered_records = sorted((record for record in records if isinstance(record, dict)),
                             key=lambda item: (_identity(item) is None, _provider_id(item) is None,
                                               -_record_time(item, 0, now)))
    for record in ordered_records:
        key = _api_key(record)
        address = _identity(record)
        if key is None or key in added:
            continue
        added.add(key)
        if address is not None:
            if address in seen_addresses:
                continue
            seen_addresses.add(address)
        index = by_address.get(address) if address else None
        if index is not None:
            matched = local[index]
            api_row = _api_only_row(record, key, received_at, now)
            matched["fr24_data"] = api_row["fr24_data"]
            matched["fr24_received_at"] = received_at
            if matched.get("latitude") is None and matched.get("longitude") is None and "position_time" in api_row:
                matched["fr24_position"] = True
                for field in ("latitude", "longitude", "position_time"):
                    matched[field] = api_row[field]
                for field in ("field_times", "field_ages", "field_sources"):
                    matched[field] = {**(matched.get(field) or {}), **{
                        axis: api_row[field][axis] for axis in ("latitude", "longitude")}}
        else:
            row = _api_only_row(record, key, received_at, now)
            row["fr24_status"] = _safe_status(snapshot.get("status"))
            api_rows[key] = row
    local.extend(api_rows.values())
    return local
