# FlightRadar24 0.6.1 — layout corrections from live use

Date: 19 November 2026. Status: implemented from the owner's screenshots of
the installed 0.6.0 build; supersedes the panel placement in DESIGN-0.6.0.

1. **Status strip to the very top bar.** The bottom overlay strip (aircraft/
   message counts at left, Flightradar24 upload/MLAT status at right) moved
   into the top menu/tool bar at the right, alongside the receiver status,
   immediately before the window minimise/maximise/close buttons in the
   title bar above. Labels cap and hide progressively on narrow windows;
   full text stays on tooltips. Transient notices (e.g. "Receiver
   started.") now appear as a small chip over the top of the map instead of
   a second line in the strip — they also still appear in the window
   status bar as before.
2. **Aircraft list full length.** The list box keeps its established
   580 px width from the owner's screenshot but now runs the full length
   of the map at the left (top to bottom), so many more rows are visible.
   It still collapses, now to a slim 42 px full-length rail. Selected
   aircraft details pop up floating immediately beside the list; on
   narrow windows where both cannot fit, the details cover the list until
   closed. The map summary chip and empty-map banner shift right of the
   panels instead of hiding behind them.
3. **Attribution in the bottom bar.** The OpenStreetMap contributors chip
   moved from a map overlay into the window's bottom bar at the
   right-hand corner (a permanent status-bar widget), freeing the map's
   bottom-right corner for the scale bar.

No reception, decoding, upload, filtering, alert or history behaviour changed.
