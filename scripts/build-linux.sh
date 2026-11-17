#!/usr/bin/env bash
set -euo pipefail

# Reproducibly build the Linux x86_64 receiver decoder and desktop bundle.
# Build on the target architecture; PyInstaller does not cross-compile.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'This workflow currently supports Linux x86_64 only.' >&2
  exit 2
fi
base_python="${PYTHON:-python3}"
build_venv="${FR24_BUILD_VENV:-$root/work/linux-build-venv}"
if [[ ! -x "$build_venv/bin/python" ]]; then
  "$base_python" -m venv "$build_venv"
fi
python_bin="$build_venv/bin/python"
source_dir="$root/work/linux-runtime/dump1090-fa"
rtlsdr_dir="$root/work/linux-runtime/rtl-sdr-blog"
rtlsdr_prefix="$root/work/linux-runtime/rtl-sdr-prefix"
dist="$root/dist/linux-x86_64"
build="$root/build/linux-x86_64"
app="$dist/FlightRadar24"
feeder="${FR24_LINUX_FEEDER:-$root/work/runtime/feeder/fr24feed}"
decoder_commit='a80ba8f82a74c90a29619ddbc10909c561198541' # FlightAware dump1090 v9.0
rtlsdr_commit='aed0ea19f3a273370a13c9009b96313c75d54c7b' # RTL-SDR Blog v1.4.0, RTL-SDR Blog V4 support

command -v gcc >/dev/null || { echo 'Install build-essential first.' >&2; exit 2; }
command -v cmake >/dev/null || { echo 'Install cmake first.' >&2; exit 2; }
command -v pkg-config >/dev/null || { echo 'Install pkg-config first.' >&2; exit 2; }
pkg-config --exists libusb-1.0 || { echo 'Install libusb-1.0-0-dev first.' >&2; exit 2; }
pkg-config --exists ncurses || { echo 'Install libncurses-dev first.' >&2; exit 2; }

mkdir -p "$root/work/linux-runtime"
mkdir -p "$build"
if [[ ! -d "$rtlsdr_dir/.git" ]]; then
  git clone --filter=blob:none https://github.com/rtlsdrblog/rtl-sdr-blog.git "$rtlsdr_dir"
fi
git -C "$rtlsdr_dir" fetch --depth 1 origin "$rtlsdr_commit"
git -C "$rtlsdr_dir" checkout --detach "$rtlsdr_commit"
cmake -S "$rtlsdr_dir" -B "$build/rtl-sdr" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$rtlsdr_prefix" -DINSTALL_UDEV_RULES=OFF -DDETACH_KERNEL_DRIVER=ON
cmake --build "$build/rtl-sdr" --parallel "$(nproc)"
cmake --install "$build/rtl-sdr"
rtlsdr_pc="$(find "$rtlsdr_prefix" -name librtlsdr.pc -print -quit)"
if [[ -z "$rtlsdr_pc" ]]; then echo 'RTL-SDR Blog build did not install pkg-config metadata.' >&2; exit 2; fi
export PKG_CONFIG_PATH="$(dirname "$rtlsdr_pc")${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
rtlsdr_libdir="$(pkg-config --variable=libdir librtlsdr)"
if [[ ! -d "$source_dir/.git" ]]; then
  git clone --filter=blob:none https://github.com/flightaware/dump1090.git "$source_dir"
fi
git -C "$source_dir" fetch --depth 1 origin "$decoder_commit"
git -C "$source_dir" checkout --detach "$decoder_commit"
make -C "$source_dir" clean
PKG_CONFIG_PATH="$PKG_CONFIG_PATH" LD_LIBRARY_PATH="$rtlsdr_libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  make -C "$source_dir" -j"$(nproc)" RTLSDR=yes BLADERF=no HACKRF=no LIMESDR=no SOAPYSDR=no CPUFEATURES=yes
LD_LIBRARY_PATH="$rtlsdr_libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$source_dir/dump1090" --version >/dev/null

rm -rf "$dist" "$build"
mkdir -p "$dist" "$build"

"$python_bin" -m pip install -r "$root/requirements-build.txt" -r "$root/requirements.txt"
"$python_bin" -m pip install 'PySide6-Essentials==6.11.3' 'shiboken6==6.11.3'
"$python_bin" -m PyInstaller "$root/src/launcher.py" --name FlightRadar24 --onedir --noconfirm \
  --distpath "$dist" --workpath "$build/pyinstaller" --specpath "$build" \
  --paths "$root/src" --collect-all pyModeS --hidden-import serial --noupx

# Install platform runtimes only after PyInstaller creates/replaces the app tree.
mkdir -p "$app/runtime/decoder" "$app/runtime/feeder" "$app/runtime/tools" "$app/runtime/licenses"
install -m 0755 "$source_dir/dump1090" "$app/runtime/decoder/dump1090"
install -m 0644 "$source_dir/COPYING" "$app/runtime/decoder/COPYING"
git -C "$source_dir" rev-parse HEAD > "$app/runtime/decoder/SOURCE-COMMIT.txt"
mkdir -p "$app/runtime/lib"
cp -a "$rtlsdr_libdir"/librtlsdr.so* "$app/runtime/lib/"
install -m 0755 "$rtlsdr_prefix/bin/rtl_test" "$app/runtime/tools/rtl_test"
LD_LIBRARY_PATH="$app/runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  ldd "$app/runtime/tools/rtl_test" > "$app/runtime/tools/DEPENDENCIES.txt"
if grep -q 'not found' "$app/runtime/tools/DEPENDENCIES.txt"; then
  echo 'RTL-SDR test tool has unresolved shared-library dependencies.' >&2
  exit 2
fi
install -m 0644 "$rtlsdr_dir/COPYING" "$app/runtime/decoder/RTL-SDR-BLOG-COPYING"
git -C "$rtlsdr_dir" rev-parse HEAD > "$app/runtime/decoder/RTL-SDR-BLOG-SOURCE-COMMIT.txt"
PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" - "$app/runtime/rtl-sdr-udev.rules" <<'PY'
import pathlib, sys
from frw.platform_support import rtl_sdr_udev_rules
pathlib.Path(sys.argv[1]).write_text(rtl_sdr_udev_rules(), encoding='ascii')
PY
if [[ ! -f "$feeder" ]]; then
  echo "The bundled Linux Flightradar24 feeder is missing: $feeder" >&2
  echo 'Set FR24_LINUX_FEEDER to the supplied ELF64 feeder binary.' >&2
  exit 2
fi
"$python_bin" - "$feeder" <<'PY'
import pathlib, struct, sys
data = pathlib.Path(sys.argv[1]).read_bytes()[:20]
if len(data) < 20 or data[:4] != b'\x7fELF' or data[4] != 2 or struct.unpack_from('<H', data, 18)[0] != 62:
    raise SystemExit('FR24_LINUX_FEEDER must be an ELF64 x86_64 executable')
PY
install -m 0755 "$feeder" "$app/runtime/feeder/fr24feed"
if [[ -d "$root/work/runtime/licenses/qt" ]]; then
  cp -a "$root/work/runtime/licenses/qt" "$app/runtime/licenses/"
else
  echo 'Missing Qt redistribution notices in work/runtime/licenses/qt.' >&2
  exit 2
fi
cp -a "$root/work/runtime/licences/decoder-components" "$app/runtime/licenses/"
cp -a "$root/work/runtime/licences/fr24feed" "$app/runtime/licenses/"
LD_LIBRARY_PATH="$app/runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$python_bin" "$root/scripts/check-linux-decoder.py" "$app/runtime/decoder/dump1090" "$build/synthetic-check"
LD_LIBRARY_PATH="$app/runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ldd "$app/runtime/decoder/dump1090" > "$app/runtime/decoder/DEPENDENCIES.txt"
if grep -q 'not found' "$app/runtime/decoder/DEPENDENCIES.txt"; then
  echo 'The Linux decoder has unresolved shared-library dependencies; see runtime/decoder/DEPENDENCIES.txt.' >&2
  exit 2
fi
ldd "$app/runtime/feeder/fr24feed" > "$app/runtime/feeder/DEPENDENCIES.txt" 2>&1 || true
install -m 0644 "$root/README.md" "$root/THIRD-PARTY-NOTICES.md" "$app/"
install -m 0755 "$root/scripts/install-linux.sh" "$dist/install-linux.sh"
install -m 0755 "$root/scripts/check-linux-desktop.sh" "$dist/check-linux-desktop.sh"
"$dist/check-linux-desktop.sh" "$app/FlightRadar24"
echo "Built Linux x86_64 application: $app"
echo 'The native feeder path is packaged but upload/signup require a Linux host test before release readiness.'
