"""Application identity must match the icon embedded in Windows builds."""
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path
import re

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from frw.app_icon import create_app_icon, write_windows_icon


class ApplicationIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_qt_icon_has_expected_raster_sizes(self):
        sizes = {size.width() for size in create_app_icon().availableSizes()}
        self.assertEqual(sizes, {16, 24, 32, 48, 64, 128, 256})

    def test_windows_icon_contains_png_images_at_qt_sizes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'FlightRadar24.ico'
            write_windows_icon(path)
            data = path.read_bytes()
        reserved, icon_type, count = struct.unpack_from('<HHH', data)
        self.assertEqual((reserved, icon_type, count), (0, 1, 7))
        dimensions = []
        for index in range(count):
            width, height, _, _, planes, bits, length, offset = struct.unpack_from(
                '<BBBBHHII', data, 6 + index * 16)
            self.assertEqual((planes, bits), (1, 32))
            self.assertEqual(data[offset:offset + 8], b'\x89PNG\r\n\x1a\n')
            self.assertLessEqual(offset + length, len(data))
            dimensions.append((width or 256, height or 256))
        self.assertEqual(dimensions, [(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])

    def test_windows_build_and_start_menu_shortcut_use_executable_icon(self):
        build = (ROOT / 'build.py').read_text(encoding='utf-8')
        installer = (ROOT / 'installer/FlightRadar24.iss').read_text(encoding='utf-8')
        desktop = (ROOT / 'src/frw/desktop.py').read_text(encoding='utf-8')
        launcher = (ROOT / 'src/launcher.py').read_text(encoding='utf-8')
        self.assertIn("'--icon',str(icon_path)", build)
        shortcut_ids = re.findall(r'AppUserModelID: "([^"]+)"', installer)
        self.assertEqual(shortcut_ids, ['FlightRadar24', 'FlightRadar24'])
        self.assertEqual(installer.count('IconFilename: "{app}\\FlightRadar24.exe"'), 2)
        self.assertIn('create_app_icon()', desktop)
        process_id = re.search(
            r'SetCurrentProcessExplicitAppUserModelID\([\'"]([^\'"]+)[\'"]\)', launcher)
        self.assertIsNotNone(process_id)
        self.assertEqual(set(shortcut_ids), {process_id.group(1)})
        self.assertNotIn("'✈'", desktop)
        self.assertIn('FlightRadar24', desktop)


if __name__ == '__main__':
    unittest.main()
