# FlightRadar24 0.6.0 — map-first home screen

Date: 19 November 2026. Status: delivered 19 November 2026 — packaged, installed over 0.5.0 with
profile verification, live-checked, and the sanitised source published to
GitHub. See HANDOFF.md and the evidence/*-0.6.0.json records.

## Request

The home screen should be dominated by the flight map (about 90% of the
screen below the menu bar) in the style of the Flightradar24 website, with
everything else as overlays on the left and right of the map. The separate
Range Radar box should go; its role moves onto the map as dotted 50 NM
range rings. Adding N/E/S/W cardinals requires a user option for the north
reference. Page tabs and Start/Stop Receiver (icon + name) should share a
single line; find/filters/alerts should be compact.

## Implemented

- **One top line:** Start/Stop receiver buttons (▶/■ icons with names) sit
  in the tab bar's left corner and receiver status at its far right —
  immediately before the window buttons in the title bar above — all on
  the same line as the page tabs, under the menu bar.
- **Map fills the Airspace page.** All former chrome rows are gone:
  - Bottom floating box (collapsible to a chip, remembered, modest
    580×300 size): Find, Filters/preset/Clear, alert-for-selected, filter
    status and the sortable aircraft list.
  - Clicking a plane pops its live details up in a floating box on the
    left (replacing the right-hand dock; closed by its × button), and
    zoom/antenna/fit icon buttons sit on the map's right edge.
  - Bottom overlay strip: reception metrics (left) and Flightradar24
    upload/MLAT status (right), notices on a second line when present.
  - Summary chip top-left, compass top-right, scale and OpenStreetMap
    attribution bottom-right.
- **Range radar removed.** The map draws dotted rings every 50 NM
  (50 km in metric) around the antenna with distance labels and N/E/S/W
  cardinal markers on the rings. The RangeRadar widget class remains in
  the source for its unit tests but is no longer placed in the window.
- **North reference option** (Display & units): True north (default) or
  Magnetic north using a magnetic variation the user enters for their
  location (degrees, east positive; the app does not calculate variation).
  The compass, ring cardinals and table bearings (°T/°M header) follow the
  choice. The background tile map is Web Mercator and remains true-north
  up; this is stated in the settings help text rather than rotating the
  tile engine underneath the user's bearings.
- New display keys (`cardinals`, `north_reference`, `magnetic_variation`)
  migrate into existing profiles with defaults; reception, uploading,
  filters, alerts and history are unchanged.

## Verification

- `scripts/check-source.py`: 120 tests OK (1 POSIX-only skip), including
  new north-reference conversion/validation and map-first layout tests;
  the one filter test that asserted on the removed radar now asserts on
  the map rows (same visible-rows path).
- Offscreen synthetic previews (`work/preview_display.py`,
  `display-0.6.0-*.png`): full-width map, overlays, one-line top bar,
  50 NM dotted ring with cardinals, compass, and a 920×650 minimum-size
  check were visually inspected. Synthetic fixtures only.
