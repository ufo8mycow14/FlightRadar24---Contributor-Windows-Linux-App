# Changes

## 0.7.0

- Both aircraft in an estimated close approach flash yellow, escalating to red for the shorter/tighter window. Uses fresh airborne pressure-altitude and motion data; separate enable/flashing switches, stale-data expiry and hidden-partner checks. Estimates are explicitly not TCAS or manoeuvre guidance.
- Choose individual map-label fields and hover over aircraft for more received information. Existing presets, units, click-to-open details and compact map layout are retained.
- The bundled WinUSB setup now supports the A12E RTL2832U single-interface 0BDA:2832 receiver while preserving healthy drivers and other interfaces.

## 0.6.4

- Removed receiver starting/started/stopping/stopped and normal shutdown notices from over the map. Routine receiver status stays in the top bar; errors remain visible.
- Published a Windows installer with optional vendor sharing download, a portable receiver package, matching application/dependency sources and checksums.

## 0.6.3

- The bottom Aircraft table can detach into its own resizable window for another monitor. Move to monitor, independent full screen (F11, Esc to exit), and Dock back controls keep the map and table synchronised. Closing the table docks it; saved layouts reopen, with a bottom-drawer fallback when a monitor is disconnected.
- Emergency aircraft now flash red on the map: the plane marker alternates bright red/pale about once a second with a pulsing red ring and an inverted red label, so a 7500/7600/7700 or transponder emergency state draws the eye immediately. Display & units can switch flashing off for a steady red marker instead.

## 0.6.2

- Corrected the aircraft list placement: it is again a floating box along the bottom of the map, now spanning the map's length at a modest depth (collapsible to a slim bar), with Find and filter controls reorganised into two compact rows so more of the box is table.
- Selected-aircraft details are back to a compact floating popup at the left (capped height, scrolling) instead of a full-height panel.

## 0.6.1

- Layout corrections from live use: reception metrics, sharing status and receiver status now run across the very top bar (right-aligned before the window buttons) instead of a bottom strip; transient notices appear as a chip over the map.
- The aircraft list now runs the full length of the screen at the left at its established width, collapsible to a slim rail; selected-aircraft details pop up floating beside it (covering it on narrow windows).
- OpenStreetMap attribution moved into the bottom bar at the right-hand corner, freeing the map's bottom edge.

## 0.6.0

- Rebuilt the home screen around the map: it now fills the Airspace page, with the aircraft list, Find and filters overlaid on the left (collapsible), live details on the right, zoom/antenna/fit controls overlaid on the map, and reception and sharing status on a single strip along the bottom.
- Start/Stop receiver (with icons), receiver health and the page tabs now share one top line beneath the menu bar.
- Removed the separate range-radar box. Its job is done on the map itself: dotted range rings every 50 NM (or 50 km), distance labels, and N/E/S/W cardinal markers, plus a compass rose.
- Added a north-reference choice: bearings, cardinals and the compass can read in true north or magnetic north using the variation you enter for your location (°T/°M shown in the table). The background map remains true-north up.

## 0.5.0

- Modernised the Airspace workspace: aviation/metric display units with flight levels, distance and bearing from your antenna, climb/descent trend arrows, on-ground state, and red emergency-squawk highlighting in the table and on the map.
- Added a Find box (Ctrl+F), click-to-sort table columns (missing values stay last), station range rings, detailed map labels, follow-selected map centring, and View toggles for the radar and table.
- Window geometry, splitter proportions and layout are now remembered between launches; the header also reports maximum received range and any emergency squawks.
- Display preferences live under Receiver & settings → Storage & alerts → Display & units and affect presentation only — reception, uploading, filters, alerts and history are unchanged. Existing profiles migrate automatically.

## 0.4.0

- Added a native Support menu and searchable offline help covering receiver setup, airspace, filters, alerts, statistics, storage, sharing and troubleshooting.
- Added an isolated practice workspace with prefilled dummy receiver values, real filter evaluation, synthetic alert previews and storage examples. `--practice` opens it without a profile or receiver.
- Added About with the running version, Kieran O'Rourke, open-source licence and independent-project attribution.
- Added a safe, prefilled support report template, guide export and copyable project/issue addresses.
- Unified the app/installer version and documented supported versus tested USB hardware.
- Prepared a public source repository with fresh history, GPL v3-or-later licensing, user/developer documentation and synthetic screenshots. Private evidence and optional native/vendor binaries are excluded.
- Added a downloadable Windows x64 receiver installer with bundled offline USB setup and an optional, integrity-checked sharing-component download from Flightradar24. The proprietary feeder is not in the public installer. Matching dependency sources, source digests and build instructions accompany the release.

## 0.3.0

- Added display filters, alert rules and delivery channels, structured statistics, airport proximity, CSV export and configurable bounded history/log storage.

## 0.2.x

- Introduced the standalone Qt desktop, native worldwide map and broadcast details, offline WinUSB provisioning for allowed devices and Linux build adapters.

