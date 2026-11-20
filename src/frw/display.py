"""Display preferences and aviation formatting derived only from received data.

Pure helpers: no Qt, no network, no invented aircraft metadata. Every value
shown by these formatters comes from a field the receiver actually decoded;
missing or non-finite values render as an explicit placeholder.
"""
from __future__ import annotations
import math
from .aircraft import distance_nm
from .config import ConfigError

DISPLAY_DEFAULT = {
    'units': 'aviation',          # 'aviation' (ft / kt / NM) or 'metric' (m / km/h / km)
    'map_labels': 'callsign',     # 'callsign', 'detailed' or 'none'
    'range_rings': True,          # station-centred range rings on the map
    'follow_selected': False,     # keep the selected aircraft centred on the map
    'cardinals': True,            # N/E/S/W compass and range-ring cardinal labels
    'emergency_flash': True,      # flash emergency aircraft red on the map (off = steady red)
    'north_reference': 'true',    # 'true' or 'magnetic' for bearings/compass
    'magnetic_variation': 0.0,    # degrees, east positive, user-entered from a chart
}
NORTH_REFERENCES = ('true', 'magnetic')
UNITS = ('aviation', 'metric')
MAP_LABELS = ('callsign', 'detailed', 'none')
EMERGENCY_SQUAWKS = {
    '7500': 'Unlawful interference (squawk 7500)',
    '7600': 'Radio failure (squawk 7600)',
    '7700': 'General emergency (squawk 7700)',
}
PLACEHOLDER = '—'
FT_PER_M = 3.280839895
KM_PER_NM = 1.852
KMH_PER_KT = 1.852


def validate_display(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DISPLAY_DEFAULT):
        raise ConfigError('Invalid display preferences.')
    c = {**DISPLAY_DEFAULT, **value}
    if c['units'] not in UNITS:
        raise ConfigError('Choose aviation or metric display units.')
    if c['map_labels'] not in MAP_LABELS:
        raise ConfigError('Choose a map label style.')
    for key in ('range_rings', 'follow_selected', 'cardinals', 'emergency_flash'):
        if type(c[key]) is not bool:
            raise ConfigError(key + ' must be true or false.')
    if c['north_reference'] not in NORTH_REFERENCES:
        raise ConfigError('Choose true or magnetic north for bearings and the compass.')
    variation = c['magnetic_variation']
    if isinstance(variation, bool) or not isinstance(variation, (int, float)) or not math.isfinite(variation) or not -30 <= variation <= 30:
        raise ConfigError('Magnetic variation: enter degrees from -30 to 30 (east positive).')
    c['magnetic_variation'] = float(variation)
    return c


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def flight_level(altitude_ft):
    """Approximate flight level (altitude in hundreds of feet), or None.

    Broadcast barometric altitude is pressure-based; at and above 10,000 ft the
    FL form is the customary shorthand. Below that, plain feet are clearer and
    avoid implying a standard-pressure reference the receiver cannot confirm.
    """
    if not _number(altitude_ft) or altitude_ft < 10000:
        return None
    return int(round(altitude_ft / 100.0))


def format_altitude(altitude_ft, units='aviation'):
    if not _number(altitude_ft):
        return PLACEHOLDER
    if units == 'metric':
        return f'{round(altitude_ft / FT_PER_M):,} m'
    text = f'{round(altitude_ft):,} ft'
    level = flight_level(altitude_ft)
    return f'{text} · FL{level}' if level is not None else text


def format_speed(knots, units='aviation'):
    if not _number(knots):
        return PLACEHOLDER
    if units == 'metric':
        return f'{round(knots * KMH_PER_KT)} km/h'
    return f'{round(knots)} kt'


def format_distance(nm, units='aviation'):
    if not _number(nm):
        return PLACEHOLDER
    if units == 'metric':
        return f'{nm * KM_PER_NM:.0f} km' if nm >= 10 else f'{nm * KM_PER_NM:.1f} km'
    return f'{nm:.0f} NM' if nm >= 10 else f'{nm:.1f} NM'


def trend_arrow(vertical_rate_fpm):
    """Climb/descent glyph. Level band avoids flicker from ±small noise."""
    if not _number(vertical_rate_fpm):
        return ''
    if vertical_rate_fpm >= 100:
        return '▲'
    if vertical_rate_fpm <= -100:
        return '▼'
    return '→'


def format_vertical_rate(fpm, units='aviation'):
    if not _number(fpm):
        return PLACEHOLDER
    arrow = trend_arrow(fpm)
    if units == 'metric':
        return f'{arrow} {fpm / FT_PER_M / 60:+.1f} m/s'.strip()
    return f'{arrow} {round(fpm):+} ft/min'.strip()


def format_track(degrees):
    if not _number(degrees):
        return PLACEHOLDER
    return f'{round(degrees) % 360:03d}°'


def format_seen(seconds):
    if not _number(seconds) or seconds < 0:
        return PLACEHOLDER
    if seconds < 60:
        return f'{seconds:.0f} s'
    return f'{int(seconds) // 60} m {int(seconds) % 60} s'


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing from point 1 to point 2, 0–359."""
    a, b = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(b)
    y = math.cos(a) * math.sin(b) - math.sin(a) * math.cos(b) * math.cos(dlon)
    return round(math.degrees(math.atan2(x, y))) % 360


def distance_bearing(station, row):
    """(distance NM, bearing deg) from the station to a positioned row, else None."""
    if not station or station[0] is None or station[1] is None:
        return None
    lat, lon = row.get('latitude'), row.get('longitude')
    if not _number(lat) or not _number(lon):
        return None
    return distance_nm(station[0], station[1], lat, lon), bearing_deg(station[0], station[1], lat, lon)


def north_offset_deg(display):
    """True bearing at which the chosen reference north lies (0 for true north)."""
    display = display or {}
    if display.get('north_reference') == 'magnetic':
        variation = display.get('magnetic_variation', 0.0)
        return float(variation) if _number(variation) else 0.0
    return 0.0


def display_bearing(true_degrees, display):
    """Convert a true bearing to the user's chosen reference, rounded 000-359."""
    if not _number(true_degrees):
        return None
    return round(true_degrees - north_offset_deg(display)) % 360


def bearing_suffix(display):
    return '°M' if (display or {}).get('north_reference') == 'magnetic' else '°T'


def emergency_text(row):
    """Human-readable emergency description for a row, or '' when none is broadcast."""
    squawk = str(row.get('squawk') or '').strip()
    if squawk in EMERGENCY_SQUAWKS:
        return EMERGENCY_SQUAWKS[squawk]
    if row.get('emergency_state') in (1, 2, 3, 4, 5, 6):
        return 'Emergency state broadcast by transponder'
    return ''


def is_emergency(row):
    return bool(emergency_text(row))


def destination_point(latitude, longitude, bearing, distance):
    """Spherical destination point; distance in NM. Used for range rings."""
    delta = distance / 3440.065
    a, theta = math.radians(latitude), math.radians(bearing)
    lat2 = math.asin(max(-1.0, min(1.0, math.sin(a) * math.cos(delta) +
                                   math.cos(a) * math.sin(delta) * math.cos(theta))))
    lon2 = math.radians(longitude) + math.atan2(math.sin(theta) * math.sin(delta) * math.cos(a),
                                                math.cos(delta) - math.sin(a) * math.sin(lat2))
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180


def sort_rows(rows, key, reverse=False, station=None):
    """Stable sort with missing values always last, in either direction."""
    if not key:
        return list(rows)

    def value(row):
        if key == 'distance':
            result = distance_bearing(station, row)
            return result[0] if result else None
        if key == 'bearing':
            result = distance_bearing(station, row)
            return result[1] if result else None
        v = row.get(key)
        if isinstance(v, str):
            return v.strip().upper() or None
        return v if _number(v) else None

    present = [r for r in rows if value(r) is not None]
    missing = [r for r in rows if value(r) is None]
    present.sort(key=value, reverse=reverse)
    return present + missing


def quick_match(row, query):
    """Case-insensitive substring match on callsign/ICAO for the find box."""
    q = (query or '').strip().upper()
    if not q:
        return True
    return q in str(row.get('callsign') or '').upper() or q in str(row.get('icao') or '').upper()
