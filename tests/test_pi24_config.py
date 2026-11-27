from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from frw.config import (
    DEFAULT, ConfigError, format_ini, parse_pi24_ini, pi24_profile_values, validate,
)


class Pi24ConfigTests(unittest.TestCase):
    def test_defaults_keep_existing_relay_mode(self):
        config = validate({})
        self.assertEqual(config['feed_mode'], 'relay')
        self.assertEqual(config['pi24']['path'], '/usr/lib/fr24/dump1090')
        self.assertEqual(config['pi24']['procargs'], '--gain -10 --net')

    def test_import_pi24_settings_without_retaining_key(self):
        values = parse_pi24_ini('''receiver="dvbt"
fr24key="abcdef0123456789"
bs="no"
raw="no"
path="/usr/lib/fr24/dump1090"
logmode="1"
procargs="--gain -10 --net"
windowmode="0"
mpx="no"
mlat="yes"
mlat-without-gps="yes"
''')
        self.assertEqual(values['pi24'], DEFAULT['pi24'])
        self.assertTrue(values['mlat'])
        self.assertTrue(values['mlat_without_gps'])
        self.assertNotIn('fr24key', values)

    def test_import_preserves_disabled_mlat_flags(self):
        values = parse_pi24_ini('''receiver="dvbt"
mlat="no"
mlat-without-gps="no"
''')
        self.assertFalse(values['mlat'])
        self.assertFalse(values['mlat_without_gps'])

    def test_export_pi24_profile_independently_of_active_source(self):
        config = validate({'receiver': 'beast-tcp'})
        rendered = format_ini(pi24_profile_values(config))
        self.assertIn('receiver="dvbt"', rendered)
        self.assertNotIn('fr24key', rendered)
        self.assertIn('mlat="yes"', rendered)
        self.assertIn('mlat-without-gps="yes"', rendered)
        self.assertEqual(config['receiver'], 'beast-tcp')

    def test_network_input_cannot_be_selected_as_a_direct_device(self):
        with self.assertRaises(ConfigError):
            validate({'feed_mode': 'direct-usb', 'receiver': 'beast-tcp'})

    def test_invalid_vendor_arguments_and_path_are_rejected(self):
        for setting in (
            {'procargs': '--gain -10 --net; whoami'},
            {'procargs': '--gain -10 --net --net-only'},
            {'path': '/tmp/other-decoder'},
            {'logmode': 3},
            {'windowmode': 9},
        ):
            with self.subTest(setting=setting), self.assertRaises(ConfigError):
                validate({'pi24': {**DEFAULT['pi24'], **setting}})

    def test_import_rejects_non_dvbt_or_unknown_settings(self):
        with self.assertRaises(ConfigError):
            parse_pi24_ini('receiver="beast-tcp"')
        with self.assertRaises(ConfigError):
            parse_pi24_ini('receiver="dvbt"\nunknown="value"')


if __name__ == '__main__':
    unittest.main()
