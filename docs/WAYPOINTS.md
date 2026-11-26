# Aviation waypoints

Click **WPT** on the map, or **View → Aviation waypoints**, to show or hide the layer. The preference is saved with the display settings and starts off. You can also change it under **Receiver settings → Storage & alerts → Display & units**. Save pending settings before using the quick toggle.

Small purple triangles distinguish waypoints from aircraft. Zoom in to see points and identifiers; labels thin out where they would overlap. Hover shows the identifier, coordinates, source and source cycle/date. Aircraft selection and aircraft hover take priority at overlapping positions. Waypoints are reference locations; their presence does not establish an aircraft's intended route.

## Bundled Australian reference

The bundle contains **5,185 historical fixes from cycle October 2013**. The map displays that date while the layer is enabled. It is not a current AIRAC database and is not suitable for navigation or flight planning. Some fixes may have changed or been removed since that snapshot.

The geographic subset covers mainland Australia, Tasmania and nearby offshore locations, plus boxes around Cocos, Christmas and Norfolk islands. Those boxes are selection boundaries, not sovereign airspace boundaries or a complete Australian FIR definition. Other regions are not bundled in this first release.

Source: Robin A. Peel's FlightGear `Navaids/fix.dat.gz`, [upstream file commit](https://gitlab.com/flightgear/fgdata/-/blob/e6c31a1942262085021e5952f7a48e1063c9419a/Navaids/fix.dat.gz). Copyright © 2013 Robin A. Peel, **GPL-2.0-or-later**. The original notice, immutable URL, source hash, bounding boxes and conversion notice are retained in the editable JSON. The full licence is in `src/frw/data/WAYPOINTS-GPL-2.0.txt`. Run `python scripts/prepare-waypoint-reference.py` to reproduce the geographic subset from that pinned source.

This app does not bundle the separately licensed [Airservices General Aviation Data](https://data.airservicesaustralia.com/data-product/general-aviation-data). Its publication terms do not grant unrestricted extraction or redistribution of the public AIP. A current official dataset needs the appropriate permission/licence.

## Import newer reference data

Open **View → Aviation waypoint data…**. Enter the source cycle/date if known and select **Import waypoint file…**. Only import data you are entitled to use. The app displays your supplied date as a reference label; it does not certify its currency or accuracy.

Supported formats:

- UTF-8 CSV with `ident,latitude,longitude` headers. Latitude and longitude are decimal degrees, south/west negative. Extra columns are ignored.
- X-Plane / FlightGear fix files, formats 600, 1100, 1101 or 1200, with an `I` or `A` header, version line, latitude/longitude/identifier records and optional `99` terminator. Extra record fields are ignored. Plain `.dat` and gzip `.gz` are accepted.

Synthetic CSV example:

```csv
ident,latitude,longitude
TESTA,-35.0,139.0
TESTB,-35.2,139.3
```

Files are limited to 12 MB after decompression and 100,000 records. Coordinates must be finite and within latitude/longitude bounds. Invalid files leave the previous dataset intact. Identical records are deduplicated, while the same identifier at different coordinates is retained.

An import replaces the selected layer rather than merging old and new points. **Use historical reference** returns to the bundle; **Use last import** returns to the saved import. Neither action deletes the original file. Imported coordinates and the source filename stay in `waypoints.json` within your local profile; they are not uploaded or included in public software releases. Program updates retain that file. Data is loaded locally, with no waypoint web service, account or background waypoint download.
