"""Pure estimated conflict calculations for timestamped aircraft observations.

These estimates are a display aid, not airborne collision avoidance or guidance.
"""
from __future__ import annotations

import math
import re
import time

_EARTH_NM = 3440.065
_KNOTS_TO_NMPS = 1.0 / 3600.0
# A generous upper bound for a received ground-speed observation. It is used
# both as an input sanity check and to prove the spatial-hash search radius.
_MAX_SPEED_KT = 1500.0
_MAX_SEARCH_NM = 2 * _MAX_SPEED_KT * 48 / 3600.0 + 1.3
_CELL_NM = 42.0
_ICAO = re.compile(r"^[0-9A-Fa-f]{6}$")


def _limits(altitude_ft):
    """Return yellow/red horizon, horizontal radius, and vertical radius."""
    if altitude_ft < 5000:
        return (25, 15, .33, .20, 850, 600)
    if altitude_ft < 10000:
        return (40, 25, .75, .55, 850, 600)
    if altitude_ft < 20000:
        return (45, 30, 1.00, .80, 850, 600)
    if altitude_ft <= 42000:
        return (48, 35, 1.30, 1.10, 850, 700)
    return (48, 35, 1.30, 1.10, 1200, 800)


def _number(value):
    """Accept finite real observations, excluding bools and coercible strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _destination(latitude, longitude, track, distance_nm):
    """Advance position and tangent direction together along a great circle."""
    angular = distance_nm / _EARTH_NM
    lat, lon, bearing = map(math.radians, (latitude, longitude, track))
    position = (math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat))
    east = (-math.sin(lon), math.cos(lon), 0.0)
    north = (-math.sin(lat)*math.cos(lon), -math.sin(lat)*math.sin(lon), math.cos(lat))
    direction = tuple(east[i]*math.sin(bearing) + north[i]*math.cos(bearing) for i in range(3))
    moved = tuple(position[i]*math.cos(angular) + direction[i]*math.sin(angular) for i in range(3))
    tangent = tuple(-position[i]*math.sin(angular) + direction[i]*math.cos(angular) for i in range(3))
    lat2, lon2 = math.asin(max(-1.0, min(1.0, moved[2]))), math.atan2(moved[1], moved[0])
    east2 = (-math.sin(lon2), math.cos(lon2), 0.0)
    north2 = (-math.sin(lat2)*math.cos(lon2), -math.sin(lat2)*math.sin(lon2), math.cos(lat2))
    track2 = math.degrees(math.atan2(_dot(tangent, east2), _dot(tangent, north2))) % 360
    return math.degrees(lat2), (math.degrees(lon2) + 180) % 360 - 180, track2


def _prepare(icao, row, now):
    """Validate one complete row and align its measurements to evaluation time."""
    if not isinstance(icao, str) or not _ICAO.fullmatch(icao) or not isinstance(row, dict):
        return None
    times = row.get('field_times')
    if not isinstance(times, dict):
        return None
    required = ('latitude', 'longitude', 'altitude_baro', 'groundspeed', 'track',
                'baro_vertical_rate', 'on_ground')
    values = {}
    for field in required:
        stamp = _number(times.get(field))
        value = row.get(field) if field == 'on_ground' else _number(row.get(field))
        if value is None or stamp is None or not 0 <= now - stamp <= 5:
            return None
        values[field] = value

    # A latitude/longitude pair is one observation and cannot be mixed across times.
    if values['on_ground'] is not False or times['latitude'] != times['longitude']:
        return None
    lat, lon = values['latitude'], values['longitude']
    alt, speed = values['altitude_baro'], values['groundspeed']
    track, vertical_rate = values['track'], values['baro_vertical_rate']
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    if speed < 0 or speed > _MAX_SPEED_KT or not 0 <= track <= 360:
        return None
    if not -2000 <= alt <= 100000 or abs(vertical_rate) > 20000:
        return None

    position_age = now - float(times['latitude'])
    altitude_age = now - float(times['altitude_baro'])
    lat, lon, track = _destination(lat, lon, track, speed * _KNOTS_TO_NMPS * position_age)
    alt += vertical_rate * altitude_age / 60.0
    return {
        'icao': icao.upper(), 'callsign': str(row.get('callsign') or '').strip()[:32],
        'lat': lat, 'lon': lon, 'alt': alt, 'speed': speed,
        'track': track % 360, 'vr': vertical_rate,
    }


def _ecef(aircraft):
    """Return the unit Earth-centered position vector for an aircraft."""
    lat, lon = math.radians(aircraft['lat']), math.radians(aircraft['lon'])
    return (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))


def _dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _unit(v):
    magnitude = math.sqrt(_dot(v, v))
    return tuple(x / magnitude for x in v) if magnitude > 1e-15 else None


def _common_tangent(a, b):
    """Project positions and local tangent velocities in a shared midpoint frame."""
    pa, pb = _ecef(a), _ecef(b)
    normal = _unit(tuple(pa[i] + pb[i] for i in range(3)))
    if normal is None:  # Antipodes have no unique midpoint; use the first point's frame.
        normal = pa
    east = _unit((-normal[1], normal[0], 0.0))
    if east is None:  # At a pole, choose a stable tangent direction from aircraft A.
        east = _unit(_cross((0.0, 1.0, 0.0), normal)) or (1.0, 0.0, 0.0)
    north = _unit(_cross(normal, east))
    positions = []
    velocities = []
    for plane, pos in ((a, pa), (b, pb)):
        # Project the spherical chord into the common tangent plane.
        delta = tuple((pos[i] - pa[i]) * _EARTH_NM for i in range(3))
        positions.append((_dot(delta, east), _dot(delta, north)))
        lat, lon, tr = map(math.radians, (plane['lat'], plane['lon'], plane['track']))
        local_east = (-math.sin(lon), math.cos(lon), 0.0)
        local_north = (-math.sin(lat)*math.cos(lon), -math.sin(lat)*math.sin(lon), math.cos(lat))
        ve = plane['speed'] * _KNOTS_TO_NMPS * math.sin(tr)
        vn = plane['speed'] * _KNOTS_TO_NMPS * math.cos(tr)
        vector = tuple(ve*local_east[i] + vn*local_north[i] for i in range(3))
        velocities.append((_dot(vector, east), _dot(vector, north)))
    return (positions[1][0]-positions[0][0], positions[1][1]-positions[0][1]), (
        velocities[1][0]-velocities[0][0], velocities[1][1]-velocities[0][1])


def _xyz_nm(plane):
    """Return Earth-centered coordinates in nautical miles."""
    return tuple(component * _EARTH_NM for component in _ecef(plane))


def _candidate_pairs(planes):
    """Yield conservative nearby pairs from a 3-D ECEF spatial hash.

    Over the longest 48 second horizon, two bounded 1500 kt aircraft can
    close at most 40 NM. Adding the largest horizontal threshold gives 41.3
    NM. A 42 NM cubic cell and its 26 neighbours therefore cannot omit a
    qualifying pair (Euclidean chord distance is no greater than arc length).
    """
    buckets = {}
    points = [_xyz_nm(plane) for plane in planes]
    cells = [tuple(math.floor(value / _CELL_NM) for value in point) for point in points]
    for index, cell in enumerate(cells):
        buckets.setdefault(cell, []).append(index)
    for index, (cell, point) in enumerate(zip(cells, points)):
        cx, cy, cz = cell
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other_index in buckets.get((cx+dx, cy+dy, cz+dz), ()):
                        if other_index <= index:
                            continue
                        other = points[other_index]
                        chord_squared = sum((point[axis]-other[axis])**2 for axis in range(3))
                        if chord_squared <= _MAX_SEARCH_NM**2:
                            yield index, other_index


def _entry_interval(relative_position, relative_velocity, radius, horizon):
    """Solve the time interval when linear horizontal range is within radius."""
    vx, vy = relative_velocity
    x, y = relative_position
    aa = vx*vx + vy*vy
    bb = 2*(x*vx + y*vy)
    cc = x*x + y*y - radius*radius
    if aa < 1e-18:
        return (0.0, horizon) if cc <= 0 else None
    discriminant = bb*bb - 4*aa*cc
    if discriminant < 0:
        return (0.0, horizon) if cc <= 0 else None
    root = math.sqrt(max(0.0, discriminant))
    start = max(0.0, (-bb-root)/(2*aa))
    end = min(horizon, (-bb+root)/(2*aa))
    return (start, end) if start <= end else None


def _volume_entry(a, b, limits, red=False):
    """Return protected-volume entry time and separation, or None."""
    horizon = limits[1] if red else limits[0]
    horizontal_radius = limits[3] if red else limits[2]
    vertical_radius = limits[5] if red else limits[4]
    position, velocity = _common_tangent(a, b)
    horizontal = _entry_interval(position, velocity, horizontal_radius, horizon)
    if horizontal is None:
        return None

    initial_vertical = b['alt'] - a['alt']
    vertical_velocity = (b['vr'] - a['vr']) / 60.0
    if abs(vertical_velocity) < 1e-12:
        vertical = (0.0, horizon) if abs(initial_vertical) <= vertical_radius else None
    else:
        roots = ((-vertical_radius-initial_vertical)/vertical_velocity,
                 (vertical_radius-initial_vertical)/vertical_velocity)
        vertical = (max(0.0, min(roots)), min(horizon, max(roots)))
        if vertical[0] > vertical[1]:
            vertical = None
    if vertical is None:
        return None

    entry = max(horizontal[0], vertical[0])
    end = min(horizontal[1], vertical[1])
    if entry > end:
        return None
    x = position[0] + velocity[0]*entry
    y = position[1] + velocity[1]*entry
    vertical_separation = abs(initial_vertical + vertical_velocity*entry)
    return entry, math.hypot(x, y), vertical_separation


def evaluate_conflicts(rows, now=None, enabled=True):
    """Evaluate fresh rows and return deterministic pairwise conflict estimates.

    ``seconds`` is time until entry into the reported level's simultaneous
    horizontal/vertical protected volume. Separation fields are evaluated at
    that entry time. Red and yellow volumes are solved independently.
    """
    now = time.time() if now is None else _number(now)
    if now is None:
        now = math.nan
    if isinstance(rows, dict):
        items = list(rows.items())
    else:
        try:
            items = [(r.get('icao') if isinstance(r, dict) else None, r) for r in rows]
        except TypeError:
            items = [(None, None)]

    prepared = []
    seen = set()
    for icao, row in items:
        plane = _prepare(icao, row, now) if math.isfinite(now) else None
        if plane is not None and plane['icao'] not in seen:
            seen.add(plane['icao'])
            prepared.append(plane)
    result = {'alerts': {}, 'assessed': len(prepared),
              'unassessed': len(items)-len(prepared), 'pairs': 0}
    if not enabled or not math.isfinite(now):
        result['unassessed'] = len(items) if not math.isfinite(now) else result['unassessed']
        result['assessed'] = 0 if not math.isfinite(now) else result['assessed']
        return result

    alerts = {}
    pair_count = 0
    for first_index, second_index in _candidate_pairs(prepared):
        first, second = prepared[first_index], prepared[second_index]
        band = _limits(max(first['alt'], second['alt']))
        red = _volume_entry(first, second, band, red=True)
        yellow = _volume_entry(first, second, band)
        if red is None and yellow is None:
            continue
        level, encounter = ('red', red) if red is not None else ('yellow', yellow)
        pair_count += 1
        for own, partner in ((first, second), (second, first)):
            alert = alerts.setdefault(own['icao'], {'level': 'yellow', 'partners': [], 'summary': ''})
            alert['level'] = 'red' if level == 'red' else alert['level']
            seconds, horizontal, vertical = encounter
            alert['partners'].append({
                'icao': partner['icao'], 'callsign': partner['callsign'], 'level': level,
                'seconds': round(seconds, 1), 'horizontal_nm': round(horizontal, 3),
                'vertical_ft': round(vertical, 0),
            })
    for alert in alerts.values():
        alert['partners'].sort(key=lambda p: (p['level'] != 'red', p['seconds'], p['icao']))
        shown = alert['partners'][:3]
        alert['summary'] = 'Estimated conflict: ' + '; '.join(
            f"{p['level']} with {p['callsign'] or p['icao']} in {p['seconds']:.1f}s "
            f"at {p['horizontal_nm']:.3f} NM / {p['vertical_ft']:.0f} ft"
            for p in shown)
        if len(alert['partners']) > len(shown):
            alert['summary'] += f"; and {len(alert['partners'])-len(shown)} more"
    result.update(alerts=alerts, pairs=pair_count)
    return result
