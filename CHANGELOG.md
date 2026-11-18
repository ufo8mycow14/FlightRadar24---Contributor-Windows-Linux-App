# Changes

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

