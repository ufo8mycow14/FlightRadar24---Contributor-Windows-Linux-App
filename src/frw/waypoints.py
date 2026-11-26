"""Offline waypoint reference data and bounded, local-only imports."""
from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import dataclass
import gzip
import io
import json
import math
from pathlib import Path
import re

MAX_BYTES = 12 * 1024 * 1024
MAX_POINTS = 100000
BUNDLED = Path(__file__).parent / 'data/waypoints-au.json'


@dataclass(frozen=True)
class Waypoint:
    ident: str
    latitude: float
    longitude: float


def validate_points(rows):
    points, seen = [], set()
    for number, row in enumerate(rows, 1):
        if number > MAX_POINTS:
            raise ValueError('Waypoint files are limited to 100,000 records.')
        try:
            ident, latitude, longitude = row
            if not isinstance(ident, str):
                raise ValueError()
            ident = ident.strip().upper()
            if isinstance(latitude, bool) or isinstance(longitude, bool):
                raise ValueError()
            latitude, longitude = float(latitude), float(longitude)
            if (not re.fullmatch(r'[A-Z0-9_-]{1,16}', ident) or
                    not math.isfinite(latitude) or not -90 <= latitude <= 90 or
                    not math.isfinite(longitude) or not -180 <= longitude <= 180):
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError(f'Invalid waypoint at record {number}; use an identifier and decimal latitude/longitude.') from None
        point = Waypoint(ident, latitude, longitude)
        if point not in seen:
            seen.add(point)
            points.append(point)
    if not points:
        raise ValueError('The file contains no waypoints.')
    return tuple(sorted(points, key=lambda p: (p.ident, p.latitude, p.longitude)))


def read_bounded(path):
    path = Path(path)
    opener = gzip.open if path.suffix.lower() == '.gz' else open
    with opener(path, 'rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Waypoint files are limited to 12 MB after decompression.')
    return raw.decode('utf-8-sig')


def import_points(path):
    """CSV: ident,latitude,longitude; or X-Plane/FlightGear fix.dat (600/1101/1200)."""
    text = read_bounded(path)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and lines[0] in ('I', 'A'):
        if len(lines) < 3 or not re.match(r'^(600|1100|1101|1200)\s+Version\b', lines[1]):
            raise ValueError('Unsupported X-Plane fix-file version.')
        rows = []
        for line in lines[2:]:
            if line == '99':
                break
            fields = line.split()
            if len(fields) < 3:
                raise ValueError('Incomplete waypoint record in fix file.')
            rows.append((fields[2], fields[0], fields[1]))
        return validate_points(rows)
    reader = csv.DictReader(io.StringIO(text))
    fields = {str(f).strip().lower(): f for f in reader.fieldnames or []}
    if not all(key in fields for key in ('ident', 'latitude', 'longitude')):
        raise ValueError('CSV needs ident,latitude,longitude columns, with decimal degrees.')
    try:
        return validate_points((row[fields['ident']], row[fields['latitude']], row[fields['longitude']]) for row in reader)
    except csv.Error as error:
        raise ValueError('Invalid CSV waypoint data: ' + str(error)) from None


class WaypointCatalog:
    def __init__(self, document):
        self.points = validate_points(document['points'])
        self.title = str(document.get('title', 'Imported waypoints'))[:120]
        self.cycle = str(document.get('cycle', 'Unspecified / unverified'))[:80]
        self.source = str(document.get('source', 'Local file'))[:256]
        self.historical = document.get('historical') is True
        self.bins = defaultdict(list)
        for point in self.points:
            self.bins[(math.floor(point.latitude / 5), math.floor(((point.longitude + 180) % 360 - 180) / 5))].append(point)

    def visible(self, west, south, east, north):
        """Bounds may be unwrapped across the dateline; each waypoint is returned once."""
        longitudes = {((x + 36) % 72) - 36 for x in range(math.floor(west / 5), math.floor(east / 5) + 1)}
        for y in range(max(-18, math.floor(south / 5)), min(18, math.floor(north / 5)) + 1):
            for x in sorted(longitudes):
                for point in self.bins.get((y, x), ()):
                    lon = point.longitude + round(((west + east) / 2 - point.longitude) / 360) * 360
                    if south <= point.latitude <= north and west <= lon <= east:
                        yield point

    @property
    def caption(self):
        prefix = 'Historical reference' if self.historical else 'Imported reference'
        return f'{prefix} · {self.cycle}'


class WaypointStore:
    def __init__(self, directory):
        self.path = Path(directory) / 'waypoints.json'
        self.document = {'selected': 'bundled'}
        self.error = ''
        try:
            if self.path.exists():
                candidate = json.loads(read_bounded(self.path))
                if candidate.get('selected') not in ('bundled', 'imported'):
                    raise ValueError('Invalid waypoint selection.')
                if 'imported' in candidate:
                    WaypointCatalog(candidate['imported'])
                if candidate['selected'] == 'imported' and 'imported' not in candidate:
                    raise ValueError('Imported waypoint data is missing.')
                self.document = candidate
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.error = 'Saved waypoint data could not be read; using the historical reference. ' + str(error)

    def catalog(self):
        if self.document['selected'] == 'imported':
            return WaypointCatalog(self.document['imported'])
        return WaypointCatalog(json.loads(read_bounded(BUNDLED)))

    def save(self, document):
        raw = json.dumps(document, ensure_ascii=True)
        if len(raw.encode('utf-8')) > MAX_BYTES:
            raise ValueError('Imported waypoint data exceeds the storage limit.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(raw, encoding='utf-8')
        temporary.replace(self.path)
        self.document, self.error = document, ''

    def import_file(self, path, cycle=''):
        points = import_points(path)
        document = {'title': 'Imported waypoints', 'cycle': cycle.strip()[:80] or 'Unspecified / unverified',
                    'source': Path(path).name[:256], 'historical': False,
                    'points': [[p.ident, p.latitude, p.longitude] for p in points]}
        self.save({'selected': 'imported', 'imported': document})
        return self.catalog()

    def use_bundled(self):
        self.save({**self.document, 'selected': 'bundled'})
        return self.catalog()

    def use_imported(self):
        if 'imported' not in self.document:
            raise ValueError('Import a waypoint file first.')
        self.save({**self.document, 'selected': 'imported'})
        return self.catalog()
