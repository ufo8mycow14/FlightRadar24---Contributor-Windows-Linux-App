"""Reproduce the complete historical FlightGear waypoint reference."""
from pathlib import Path
import gzip
import hashlib
import json
import math
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'e6c31a1942262085021e5952f7a48e1063c9419a'
URL = f'https://gitlab.com/flightgear/fgdata/-/raw/{COMMIT}/Navaids/fix.dat.gz'
SOURCE_SHA256 = '0be045e8acdcb2cf0656084063d58768976da4cd02902542e1bdb1fe44c23b95'
SOURCE_RECORDS = 119720
IDENT = re.compile(r'[A-Z0-9_-]{1,16}')


def main():
    with urllib.request.urlopen(URL, timeout=30) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('Pinned FlightGear source exceeds the expected download limit.')
    source_sha256 = hashlib.sha256(raw).hexdigest()
    if source_sha256 != SOURCE_SHA256:
        raise ValueError('Pinned FlightGear source checksum changed.')
    text = gzip.decompress(raw).decode('latin-1')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    assert 'data cycle 2013.10' in lines[1] and 'General Public License' in lines[1]
    points = set()
    source_records = excluded_records = 0
    for line in lines[2:]:
        if line == '99':
            break
        fields = line.split()
        source_records += 1
        if len(fields) < 3:
            excluded_records += 1
            continue
        latitude, longitude, ident = fields[:3]
        try:
            latitude, longitude = float(latitude), float(longitude)
        except ValueError:
            excluded_records += 1
            continue
        if (not IDENT.fullmatch(ident.upper()) or not math.isfinite(latitude) or
                not math.isfinite(longitude) or not -90 <= latitude <= 90 or
                not -180 <= longitude <= 180):
            excluded_records += 1
            continue
        points.add((ident.upper(), latitude, longitude))
    if source_records != SOURCE_RECORDS or not points:
        raise ValueError(f'Unexpected pinned dataset record count: {source_records}.')
    document = {'title': 'Worldwide historical fixes', 'cycle': '2013.10',
                'source': 'Robin A. Peel / FlightGear', 'historical': True,
                'licence': 'GPL-2.0-or-later', 'copyright_notice': lines[1],
                'upstream_url': URL, 'upstream_commit': COMMIT,
                'upstream_sha256': source_sha256, 'source_records': source_records,
                'excluded_malformed_records': excluded_records,
                'modification': 'Worldwide fix records converted to JSON; malformed identifiers or coordinates omitted by FlightRadar24 contributors, 2026-11-26.',
                'points': sorted(points)}
    # One waypoint per line keeps the data easy to inspect and modify.
    encoded = json.dumps({k: v for k, v in document.items() if k != 'points'}, indent=2, ensure_ascii=False)
    encoded = encoded[:-2] + ',\n  "points": [\n' + ',\n'.join('    '+json.dumps(p) for p in document['points']) + '\n  ]\n}\n'
    target = ROOT / 'src/frw/data/waypoints-worldwide.json'
    target.write_text(encoded, encoding='utf-8')
    assert json.loads(target.read_text('utf-8'))['points'] == [list(p) for p in sorted(points)]
    print(f'Wrote {len(points):,} worldwide historical waypoints; excluded {excluded_records} malformed records; source cycle 2013.10; upstream SHA256 {source_sha256}')


if __name__ == '__main__':
    main()
