"""Small OS-specific paths and capability reporting for FlightRadar24."""
from __future__ import annotations

import os
import platform
from pathlib import Path


def data_directory(app_name: str = 'FlightRadarWindows') -> Path:
    """Return a per-user writable profile directory without creating it."""
    home = Path.home()
    system = platform.system()
    if system == 'Windows':
        root = Path(os.environ.get('LOCALAPPDATA', home / 'AppData' / 'Local'))
    elif system == 'Darwin':
        root = home / 'Library' / 'Application Support'
    else:
        xdg = Path(os.environ.get('XDG_DATA_HOME', home / '.local' / 'share')).expanduser()
        root = xdg if xdg.is_absolute() else home / '.local' / 'share'
    return root / app_name


def key_storage_description() -> str:
    if os.name == 'nt':
        return 'Encrypted for this Windows user with DPAPI.'
    if platform.system() == 'Darwin':
        return 'Stored in a per-user file protected by macOS file permissions.'
    return 'Stored in a per-user file readable only by your Linux account.'


def is_linux_x64() -> bool:
    return platform.system() == 'Linux' and platform.machine().lower() in ('x86_64', 'amd64')


def rtl_sdr_udev_rules() -> str:
    """Rules for known RTL-SDR USB IDs; caller must explicitly install them."""
    ids = ('0bda:2832', '0bda:2838', '0bda:2839', '0bda:283a', '0bda:283b',
           '0ccd:00a9', '1d19:1101', '1f4d:b803', '1b80:d393', '15f4:0131')
    return ''.join(f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="{v}", ATTR{{idProduct}}=="{p}", MODE="0660", GROUP="plugdev", TAG+="uaccess"\n'
                   for pair in ids for v, p in (pair.split(':'),))
