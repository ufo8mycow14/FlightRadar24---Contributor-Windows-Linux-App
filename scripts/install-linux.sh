#!/usr/bin/env bash
set -euo pipefail

# Install the unpacked Linux bundle into this user's home directory.
source_dir="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/FlightRadar24}"
if [[ ! -x "$source_dir/FlightRadar24" || ! -d "$source_dir/runtime" ]]; then
  echo 'Pass the unpacked FlightRadar24 application directory as the first argument.' >&2
  exit 2
fi
home="${HOME:?HOME is not set}"
target="$home/.local/opt/FlightRadar24"
desktop_dir="${XDG_DATA_HOME:-$home/.local/share}/applications"
mkdir -p "$target" "$desktop_dir"
cp -a "$source_dir/." "$target/"
chmod 0755 "$target/FlightRadar24"
cat > "$desktop_dir/flightradar24.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=FlightRadar24
Comment=Local ADS-B receiver and aircraft map
Exec=$target/FlightRadar24
Terminal=false
Categories=Utility;Science;
StartupWMClass=FlightRadar24
EOF
chmod 0644 "$desktop_dir/flightradar24.desktop"

rules="$target/runtime/rtl-sdr-udev.rules"
if [[ -f "$rules" ]]; then
  read -r -p 'Install the bundled RTL-SDR USB permission rules? This uses sudo to write /etc/udev/rules.d. [y/N] ' answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    sudo install -m 0644 "$rules" /etc/udev/rules.d/99-flightradar24-rtl-sdr.rules
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=usb
  fi
fi

echo 'FlightRadar24 is installed. Launch it from the applications menu or run:'
printf '  %q\n' "$target/FlightRadar24"
