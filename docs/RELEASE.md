# Windows download and source files

[Download the Windows x64 installer](https://github.com/ufo8mycow14/FlightRadar24---Contributor-Windows-Linux-App/releases/download/v0.9.0/FlightRadar24-0.9.0-Windows-x64-Setup.exe)

Version 0.9.0 is a native Windows desktop application. It needs no Python installation, browser, Raspberry Pi or Linux virtual machine. Linux remains a source/build target awaiting native verification; macOS is deferred.

## Install and start

1. Download `FlightRadar24-0.9.0-Windows-x64-Setup.exe` from [Releases](https://github.com/ufo8mycow14/FlightRadar24---Contributor-Windows-Linux-App/releases/tag/v0.9.0).
2. Run setup. The app and supported USB driver components are included. An existing healthy WinUSB binding is preserved. Windows elevation and driver-signature checks still apply.
3. If you want to send received aircraft to Flightradar24, select **Install optional Flightradar24 sharing**. Setup downloads its unchanged component from `repo-feed.flightradar24.com` over HTTPS and checks both the archive and executable against pinned SHA-256 values. This step needs internet access and Windows `tar.exe` (included with Windows 10 version 1803 and later).
4. Open **FlightRadar24** from the Start menu. Configure your source and actual antenna location in **Receiver settings**, save, then start reception. F1 opens the offline guide.
5. Sharing remains disabled until you configure your own account/sharing key and enable it. A sharing-component download does not register an account, start a feeder or upload aircraft.

You may leave optional sharing unselected and use local reception, maps, filters, alerts and statistics. Rerun setup later and select the optional sharing task to add it. If that download fails, local reception remains available and setup reports the failure; retry when the connection is available. Setup never overwrites a different pre-existing feeder executable.

The installer is unsigned. Check the file name, download location and published checksum; no Microsoft verified-publisher identity or WHQL certification is claimed. Windows x64 is the tested build architecture. App upgrades retain the established per-user settings, protected secrets and history directory. Close the app normally before upgrading.

## Release files

| File | Purpose |
| --- | --- |
| `FlightRadar24-0.9.0-Windows-x64-Setup.exe` | App, receiver runtime, notices and offline driver setup, with optional vendor sharing download. |
| `FlightRadar24-0.9.0-source.zip` | Project source, tests, installer source, current native patches and build instructions. |
| `FlightRadar24-0.9.0-corresponding-source.zip` | Exact dependency source archives, the app source ZIP, helper source/overlays, notices and source index. |
| `FlightRadar24-0.9.0-Windows-x64-Portable.zip` | Extract-and-run receiver; USB drivers and optional sharing require the installer. |
| `SHA256SUMS.txt` | SHA-256 digests for the four release files. |

All source downloads are available at no charge from the same release page. Sources and binaries retain their own licences. The source archive contains the upstream decoder plus the current Windows patch, Blink plus its current Windows patch, terminal-supervisor/guest-helper source, complete libwdi source/overlays, Cygwin, GCC runtime source package, pyModeS, pyserial, PyInstaller, PySide/Shiboken, QtBase, QtSvg and QtImageFormats. The selected Qt libraries/plugins remain separate replaceable DLLs; the package does not use Qt WebEngine.

`RELEASE-SOURCES.json` lists exact source download addresses, versions and checksums. The bundled source archives are the primary corresponding-source download; upstream URLs provide additional provenance. `BUILDING.md` and the libwdi source `BUILD.md` explain compiler/toolchain versions and local changes. The decoder's Tor-derived smartlist notice and supplementary ZIP licence are retained with their upstream attribution.

The official proprietary `fr24feed` executable, user profiles, keys, receiver history, diagnostic logs and private repository history are excluded from these public files. The installer obtains the vendor component only if the person installing chooses optional sharing.

## In-app updates

Support → Software updates offers manual installation and optional automatic installation. Checks run every 12 hours while the Windows app is open and catch up at startup if overdue. Automatic installation is off by default. Downloads require the expected repository, stable newer version, installer name/size and matching GitHub SHA-256 digest. A restart countdown can be postponed; saved settings/history and prior receiver state are preserved. Windows elevation may still prompt. No service runs while the app is closed. Version 0.7.1 and earlier need this installer once to gain the updater.

## Verification scope

The optional worldwide waypoint layer contains 119,716 historical fixes (cycle 2013.10). The yellow dotted aircraft guide suggests fixes ahead of the observed ground track; it does not show or imply a filed route. Local imports, selection and aircraft warning colours remain available. Not for navigation.

Statistics table refreshes now batch column sizing and reuse unchanged cells, fixing long interface stalls when switching tabs with populated encounter history.

Version 0.7.0 introduced per-field aircraft labels, live hover details, and yellow/red estimated-conflict warnings on both aircraft. Conflict tests exercise simultaneous horizontal/vertical proximity, stale or missing observations, ground exclusion, different crossing times, altitude bands and date-line/polar geometry. Native tests cover warning priority, filtering, expiry, label controls and hover selection. These estimates are not TCAS or navigation guidance; see [the method and primary references](CONFLICT-ALERTS.md).

The A12E RTL2832U single-interface receiver driver fix is included. Live Windows reception with that device and the prior RTL-SDR Blog V4 was verified. The public installer excludes the proprietary vendor executable and offers its optional verified download.

Independent full-screen, docking, layout restoration and selection were tested with native Qt widgets. Interactive testing on two physical monitors was unavailable because Windows denied desktop automation access; no physical two-monitor validation is claimed.

Past account-side upload confirmation covers the development station. They do not guarantee another receiver, antenna or account. Active vendor MLAT, external email/phone receipt, Linux runtime and screen-reader conformance remain unverified. No driver rebinding is performed merely to test an already working device.
