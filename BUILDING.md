# Build and run FlightRadar24

## Source application

Use 64-bit Python 3.12. Create a virtual environment in the checkout and install the pinned application/desktop requirements:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-desktop.txt
.\.venv\Scripts\python.exe src/launcher.py --practice
```

Practice needs no receiver, native runtime payload, account or profile. It does not make network requests. For the full native desktop use `.\.venv\Scripts\pythonw.exe src/launcher.py`; `pythonw` avoids a console window on Windows. Source execution supports Beast/AVR/SBS input through Python. USB and I/Q input additionally require the platform decoder, and registration/upload require the optional feeder runtime.

On Linux use `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt -r requirements-desktop.txt`, and `.venv/bin/python src/launcher.py --practice`. Run in a graphical session with Qt's platform dependencies installed. Linux execution is not yet verified by this project's Windows test results.

The launcher retains compatibility with project-local dependencies in `work/applibs` and `work/desktoplibs`. Normal users can use the virtual environment instead. Normal desktop operation is Qt Widgets, Qt Network and QPainter; no WebEngine or browser process is used.

## Source-only tests

```powershell
.\.venv\Scripts\python.exe scripts/check-source.py
.\.venv\Scripts\python.exe scripts/generate-help.py
```

`check-source.py` runs the portable application-logic/native-widget tests without requiring privately staged binaries or a radio. It uses offscreen Qt and temporary test profiles. Linux-specific skips and OS differences must be reported as such. It does not establish live radio, upload, MLAT, real email delivery or screen-reader conformance.

The complete development installation can run `python -m unittest discover -s tests -p "test_*.py"`. Some additional integration tests need the prepared runtime, synthetic I/Q recording and original INI fixture. Missing private build payloads are not included in the public source snapshot.

Help articles live in `src/frw/data/help.json`. Regenerate `docs/USER-GUIDE.md` with `scripts/generate-help.py`. `scripts/preview-support.py` renders the two documentation screenshots from isolated native widgets and dummy data; it never reads a live profile.

## Complete Windows package

The public repository intentionally excludes native/vendor binaries and private operational evidence. It is sufficient for source UI/practice and supported Python stream inputs. Building a complete offline USB/feeder installer requires independently preparing the following runtime tree under `work/runtime`:

```text
runtime/
  decoder/dump1090.exe
  feeder/blink.exe, cygwin1.dll, cyggcc_s-seh-1.dll
  feeder/frw-pty.exe, fr24feed, pgrep, guest-probes
  bin/cygwin-console-helper.exe
  drivers/winusb/wdi-simple.exe, manifest.json, source and licence material
  licences/  (native components and their exact notices)
  licenses/qt/  (Qt/PySide licences, provenance and matching-source references)
```

The official `fr24feed` binary is proprietary. The public Windows installer excludes it. Its optional sharing task obtains the unchanged, pinned 1.0.57-1 archive directly from the vendor over HTTPS, verifies the archive and executable SHA-256 values, and retains its notices. The receiver and driver setup work without that optional download. See [release and corresponding-source information](docs/RELEASE.md).

Once a complete runtime has been lawfully prepared:

```powershell
python -m pip install -r requirements.txt -r requirements-desktop.txt -r requirements-build.txt
python build.py --dist-dir C:\FlightRadarBuild\dist --work-dir C:\FlightRadarBuild\build
python installer/build_setup.py --package C:\FlightRadarBuild\dist\FlightRadar24 --output C:\FlightRadarBuild\setup --iscc 'C:\path\to\ISCC.exe'
```

For a public receiver package, prepare the runtime without `runtime/feeder/fr24feed` and pass `--public-release` to `installer/build_setup.py`. That option refuses a package containing the proprietary executable and enables the optional vendor-download task. Keep the compatibility runtime, offline driver helper and all notices. Pair the installer with the matching app source, dependency source archive and SHA256SUMS file on the same release page. `docs/RELEASE-SOURCES.json` records the exact dependency source versions and digests for 0.7.0.

Test the optional download only in disposable folders with `FRW_TEST_VENDOR_DOWNLOAD=1 python -m unittest discover -s tests -p test_feeder_download.py -v`. Without that variable, the official-network integration test is skipped while rejection/preservation tests still run. These checks do not register an account, run a feeder, change a USB driver or read a receiver profile.

Use Inno Setup 6 for the installer. The windowed one-folder package contains `FlightRadar24.exe`, `_internal`, `runtime`, licences and help. Keep them together. Qt shared libraries/plugins remain replaceable, and Qt WebEngine is excluded. For network source checkouts, keep build output/temp files on the build machine.

The builder narrows DLL discovery to the interpreter/Windows paths and cleans its analysis cache to avoid importing incompatible toolkit ICU DLLs. It requires Cygwin's matching console companion to prevent a visible fallback terminal during helper startup. Neither the build nor portable app installs a service, startup task or firewall rule. The complete installer checks only supported USB IDs/interfaces using its hash-pinned helper; Windows elevation and signature checks still apply.

## Windows native sources

- Decoder: [gvanem/Dump1090](https://github.com/gvanem/Dump1090), v1.0.1 (archive root `gvanem-Dump1090-6a8abff`). Apply `work/source-delivery/dump1090-windows.patch` to the matching upstream tree. Local changes cover UC8 conversion, sample-clock/overlap accounting, timestamped output and enumeration. Build `src/Dump1090.vcxproj` with Visual Studio 2022 C++ tools, Windows SDK, `Release`, `x64`, toolset `v143`. Upstream writes `dump1090.exe` at the source root; place it under `work/runtime/decoder`. Retain all upstream dependency notices; the top-level licence is not a blanket grant for every embedded component.
- Compatibility runtime: [Blink](https://github.com/jart/blink), 1.1.0. Apply `work/source-delivery/blink-windows.patch`. Build with Cygwin x64 using upstream `./configure` and `make -j4`. The development environment used Cygwin 3.6.12-1 and GCC 14.4.0. Copy the matching Blink and runtime DLLs into the feeder directory, plus the matching `cygwin-console-helper.exe` under `runtime/bin`.
- Terminal supervisor: compile `work/source-delivery/frw-pty.c` using Cygwin `gcc -O2 -Wall -Wextra -Werror … -lutil`. It uses a controlling terminal for official signup and a Windows job for child cleanup. Its source has an MIT licence.
- Discovery helpers: run `python work/source-delivery/build_guest_pgrep.py work/runtime/feeder/pgrep` and `python work/source-delivery/build_guest_probe_dispatcher.py work/runtime/feeder/guest-probes`. These are narrow vendor-probe responses, not a general Linux shell.
- USB provisioning: the complete development package uses libwdi 1.5.1 with AMD64-only, device/interface restrictions. The public installer source validates the manifest and pinned helper hash but does not contain the helper payload. See [third-party notices](THIRD-PARTY-NOTICES.md) before packaging.

The Blink patch implements `FUTEX_WAIT_BITSET` only for `MATCH_ANY` waits with absolute realtime/monotonic deadlines. It does not establish general Linux syscall compatibility. The unchanged official feeder's Windows path remains a user-mode compatibility integration. TLS certificate/hostname verification is retained.

## Linux x86-64 build path

`scripts/build-linux.sh` must run on Linux x86-64. It builds pinned FlightAware dump1090 v9.0 and RTL-SDR Blog v1.4.0 sources, packages a separately supplied feeder and runs a synthetic decoder check. Set `FR24_LINUX_FEEDER` to a lawfully obtained Linux x86-64 feeder. Supply the referenced synthetic recording from your development test assets when running decoder checks. The script does not download or fabricate a feeder credential.

Required system packages include the compiler toolchain, CMake, pkg-config, Python venv, libusb development files, ncurses development files and Qt X11/XCB runtime dependencies. Check generated `ldd` reports for unresolved libraries. Linux USB/IQ uses 2.4 MS/s. Installation and desktop-check helpers are under `scripts/`; udev permission changes are a separate administrative action.

The Linux build is experimental and needs a Linux graphical session, radio and service tests before release. No macOS package is currently provided.

## Version and publication

`src/frw/__init__.py` supplies the displayed application version and the installer's Python default. Keep the `.iss` fallback consistent when changing it. The project licence is in `LICENSE`/`COPYRIGHT`; dependencies keep their own terms.

The private canonical checkout has historical runtime payloads and operational evidence. Public publication uses an explicit source allowlist and a fresh root commit, preserving the private remote/history. Do not make that existing private repository public or upload a whole data/profile directory as a shortcut.
The 0.9.0 Windows decoder adds optional Mode A/C demodulation and native 2.4 MHz Mode S signal/correction metadata. The upstream ZIP is pinned in `docs/RELEASE-SOURCES.json`. Patch SHA-256: `dcfc923da045e146f664b01ecd1ccddc40c9db6ee69217de4e2716d6cbde671f`; packaged decoder SHA-256: `336b5f3dee8855d3a6df15721a11a8ba8262b06cf46eef29ac8dd364a543c513`. Run `python -m unittest discover -s tests -p test_receiver_waveforms.py` on Windows with the built decoder under `work/runtime/decoder` (or set `FRW_TEST_DECODER`). The test uses synthetic UC8, not a live radio.

