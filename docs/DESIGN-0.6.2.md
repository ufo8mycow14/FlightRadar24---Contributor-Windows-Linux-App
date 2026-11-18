# FlightRadar24 0.6.2 — correction to the 0.6.1 panel placement

Date: 19 November 2026. The 0.6.1 build misread "the length of the screen"
as a full-height left panel. As originally asked:

- The **Aircraft box floats along the bottom** of the map, spanning its
  length (left to right) at a modest depth (280 px, collapsible to a slim
  bottom bar). Find, filters, preset, alert and shown-count were
  reorganised into two compact rows so most of the box is the table, which
  now shows all eleven columns without horizontal scrolling on wide
  windows.
- The **selected-aircraft details** are a compact floating popup at the
  left (392 px wide, height capped at 540 px and kept above the bottom
  box), not a full-height panel. It closes with its × button.
- The map scale and tile-status banner keep clear of the bottom box, and
  the summary/empty-state banner sit right of the details popup.

Everything else from 0.6.1 stands: reception/sharing/receiver status in
the very top bar, OpenStreetMap attribution in the bottom bar at the
right, and no change to reception, decoding, uploading, filters, alerts
or history.
