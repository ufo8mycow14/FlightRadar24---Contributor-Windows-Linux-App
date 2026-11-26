"""Reproduce the historical Australia-region waypoint subset; no current AIRAC claim."""
from pathlib import Path
import gzip
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'e6c31a1942262085021e5952f7a48e1063c9419a'
URL = f'https://gitlab.com/flightgear/fgdata/-/raw/{COMMIT}/Navaids/fix.dat.gz'
# Geographic subset, including offshore fixes and Cocos, Christmas and Norfolk islands.
# These boxes are a coverage selection, not a definition of sovereign airspace.
BOUNDS = [(110, -45, 155, -9), (95, -14, 107, -8), (166, -31, 170, -27)]


def main():
    with urllib.request.urlopen(URL, timeout=30) as response:
        raw = response.read(4 * 1024 * 1024)
    text = gzip.decompress(raw).decode('latin-1')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    assert 'data cycle 2013.10' in lines[1] and 'General Public License' in lines[1]
    points = set()
    for line in lines[2:]:
        if line == '99':
            break
        latitude, longitude, ident = line.split()[:3]
        latitude, longitude = float(latitude), float(longitude)
        if any(w <= longitude <= e and s <= latitude <= n for w, s, e, n in BOUNDS):
            points.add((ident, latitude, longitude))
    document = {'title': 'Australia-region historical fixes', 'cycle': '2013.10',
                'source': 'Robin A. Peel / FlightGear', 'historical': True,
                'licence': 'GPL-2.0-or-later', 'copyright_notice': lines[1],
                'upstream_url': URL, 'upstream_sha256': hashlib.sha256(raw).hexdigest(),
                'coverage_boxes_west_south_east_north': BOUNDS,
                'modification': 'Geographic subset converted to JSON by FlightRadar24 contributors, 2026-11-26.',
                'points': sorted(points)}
    # One waypoint per line keeps the data easy to inspect and modify.
    encoded = json.dumps({k: v for k, v in document.items() if k != 'points'}, indent=2, ensure_ascii=False)
    encoded = encoded[:-2] + ',\n  "points": [\n' + ',\n'.join('    '+json.dumps(p) for p in document['points']) + '\n  ]\n}\n'
    target = ROOT / 'src/frw/data/waypoints-au.json'
    target.write_text(encoded, encoding='utf-8')
    assert json.loads(target.read_text('utf-8'))['points'] == [list(p) for p in sorted(points)]
    print(f'Wrote {len(points):,} historical waypoints; source cycle 2013.10; upstream SHA256 {document["upstream_sha256"]}')


if __name__ == '__main__':
    main()
