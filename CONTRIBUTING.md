# Contributing to FlightRadar24

Start with the [README](README.md), [user guide](docs/USER-GUIDE.md) and [build instructions](BUILDING.md). Keep the application native: Qt Widgets, Qt Network and QPainter; no browser engine or webview in the desktop flow.

## Changes

1. Create a branch for a focused change. Keep unrelated edits separate.
2. Preserve receiver/key profiles and keep fictional examples separate from live inputs.
3. Exercise the affected tests and describe what you actually verified. State hardware, operating system and service limitations.
4. Update help when behaviour or labels change. Edit `src/frw/data/help.json`, then run `python scripts/generate-help.py` to regenerate the public guide.
5. Open a pull request with the problem, resulting behaviour and validation. Do not commit application profiles, raw logs, API/feeder keys, private coordinates or vendor binaries.

The source-only checks do not require proprietary/native runtime payloads. Tests that open a radio, run a native decoder or contact a real notification service need a separately prepared environment and explicit destinations.

## Reporting problems

Use Support → Copy support report template. Include the app version, OS family/architecture, reproducible steps, expected behaviour and actual error. Redact screenshots and inspect any diagnostics before attaching them to a public issue. Never attach your entire data directory.

## Licence

Contributions to project-authored code and documentation use GNU GPL v3 or later unless an existing component-specific licence applies. Retain upstream notices and explain any new dependency and its provenance. Do not assume that a vendor binary can be redistributed because it can be downloaded.
