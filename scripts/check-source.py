"""Run source-only tests without native/vendor payloads or external destinations."""
from pathlib import Path
import os
import sys
import unittest

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root/'tests'), str(root/'src'), str(root/'work/desktoplibs'), str(root/'work/applibs')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
names = ['test_pi24_config', 'test_pi24_pages', 'test_app_icon', 'test_fr24_api', 'test_fr24_catalog', 'test_fr24_display', 'test_fr24_isolation', 'test_native_fr24',
         'test_aircraft_details', 'test_aircraft_symbols', 'test_conflicts', 'test_control', 'test_display', 'test_desktop', 'test_features',
         'test_instance', 'test_launcher', 'test_updates', 'test_native_updates', 'test_waypoints', 'test_native_details', 'test_native_features',
         'test_native_map', 'test_native_pages', 'test_platform_support', 'test_runtime_recovery', 'test_support']
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
raise SystemExit(not result.wasSuccessful())
