# FlightRadar24 0.5.0 — Airspace workspace design

Date: 18 November 2026. Status: implemented, packaged and smoke-tested 18 November 2026 (118 source
tests, packaged playback/display checks, Inno installer compiled). 0.5.0 was subsequently
installed with user approval on 18 November 2026 and live-verified
(reception and feeder connection; see HANDOFF.md). See HANDOFF.md and
evidence/native-delivery-0.5.0.json.

## Review findings (0.4.0)

- The native Qt desktop is functionally rich (map, range radar, broadcast
  details, filters/presets, alerts, statistics, bounded history, offline help
  and practice mode), but the Airspace tab under-used the decoded data:
  altitude/speed/vertical-rate were shown as raw numbers with no units,
  flight level, trend, distance or bearing, and emergency squawks were not
  visually distinguished in the table.
- Finding one aircraft among many required building a saved filter; there was
  no quick find or table sorting.
- The map labelled every aircraft with callsign only, had no distance
  reference around the antenna, and could not follow a selected aircraft.
- Window size, splitter proportions and panel visibility reset every launch.
- Settings surface is otherwise coherent: receiver/sharing in
  `config.py`, feature preferences in `features.py` with strict validation
  and DPAPI-protected secrets. New preferences therefore belong in
  `features.json`, not receiver settings.

## Research basis

Mature ADS-B displays (tar1090/dump1090-fa class interfaces) consistently
offer: altitude/speed in the aircraft label, sortable tables, range rings
around the receiver, unit selection, and track following. RSSI/signal,
airline, registration, aircraft type, route and photos are **not** available
from this project's decoded Mode S/ADS-B stream (pyModeS fields actually
retained in `aircraft.py`), so none were added or implied. Everything in
0.5.0 is derived from fields the receiver already decodes: altitude,
groundspeed, track, vertical rate, squawk, emergency state, on-ground,
position, seen age, plus the configured station position.

## Implemented

- `src/frw/display.py` (pure, Qt-free): display-preference validation;
  aviation/metric formatters; flight level at/above 10,000 ft; climb/descent
  trend arrows (±100 ft/min level band); great-circle distance/bearing and
  destination-point maths; emergency detection (7500/7600/7700 and
  transponder emergency state); stable sorting with missing values last;
  callsign/ICAO quick match. Missing/non-finite values render as `—`.
- Features schema gains a validated `display` object
  (`units`, `map_labels`, `range_rings`, `follow_selected`). Pre-0.5
  `features.json` files load with defaults (tested migration).
- Airspace table: 11 columns including Distance and Bearing from the
  antenna; unit-aware headers/values; emergency rows highlighted with an
  explanatory tooltip; explicit On ground / Live / Recorded / Not received
  states; click-to-sort headings; Find box (Ctrl+F) layered over saved
  filters without altering them.
- Map: dashed station range rings (NM or km bands), label modes
  (callsign / detailed with FL+speed / none except selected), red emergency
  ring, follow-selected centring.
- Workspace: remembered window geometry/splitters/panels (QSettings),
  View toggles for radar/table, header adds maximum received range and
  emergency count, refreshed Fusion stylesheet (focus-visible controls,
  primary Start action, rounded panels, styled scrollbars).
- Preferences UI: Display & units group in Storage & alerts, stating that
  display choices never change reception, uploading, alerts or history.
- Docs: help topic, regenerated user guide, README, CHANGELOG; version
  0.5.0 in `frw/__init__.py` and the installer fallback.
- Tests: `tests/test_display.py` (9 tests) added to `scripts/check-source.py`;
  one legacy desktop test updated for the new Position column index.

## Deliberate non-goals

- No new external data, accounts or network calls; no airline/registration/
  route/photo inference; no RSSI (not in the decoded stream); no change to
  decoding, feeder, MLAT, alert matching or history semantics.
- The legacy `--headless`/web surface is unchanged; the shipped interface is
  the native desktop.
- Packaging, installer rebuild, live-receiver acceptance and public release
  remain separate, explicitly authorised steps.

## Verification

- `scripts/check-source.py`: 118 tests OK (1 POSIX-only skip).
- Offscreen synthetic previews rendered by `work/preview_display.py`
  (`display-0.5.0-*-preview.png` in the setup output folder): table units,
  FL, distance/bearing, emergency row, rings, detailed labels and the new
  settings group visually checked. Synthetic fixtures only — not live
  reception evidence.
