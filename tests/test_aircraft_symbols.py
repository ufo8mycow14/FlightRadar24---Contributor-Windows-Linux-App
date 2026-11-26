import time
import unittest

from frw.aircraft import Aircraft
from frw.aircraft_symbols import aircraft_symbol, symbol_label
from frw.config import DEFAULT
from frw.display import aircraft_hover_html


class AircraftSymbolTests(unittest.TestCase):
    def test_category_set_disambiguates_rotorcraft_from_space_and_obstacle(self):
        for tc, expected in ((4, 'helicopter'), (3, 'space'), (2, 'obstacle'), (1, 'unknown')):
            self.assertEqual(aircraft_symbol({'category': 7, 'category_typecode': tc}), expected)

    def test_incomplete_reserved_and_untrusted_hints_stay_unknown(self):
        for row in ({}, {'category': 7}, {'category': 7, 'typecode': 4},
                    {'category': 0, 'category_typecode': 4},
                    {'category': 5, 'category_typecode': 3},
                    {'category': 2, 'category_typecode': 2},
                    {'category': True, 'category_typecode': 4},
                    {'category': '7', 'category_typecode': '4'},
                    {'callsign': 'HELI123', 'wake_vortex': 'Rotorcraft', 'groundspeed': 70},
                    {'on_ground': True}):
            self.assertEqual(aircraft_symbol(row), 'unknown', row)

    def test_live_identity_survives_other_typecodes_and_expires(self):
        aircraft = Aircraft(dict(DEFAULT))
        now = time.time()
        aircraft._update('A00001', {'df':17,'typecode':4,'category':7,'wake_vortex':'Rotorcraft'}, now)
        aircraft._update('A00001', {'df':17,'typecode':19,'subtype':1,'groundspeed':90}, now+1)
        self.assertEqual(aircraft_symbol(aircraft._view(aircraft.rows['A00001'], now+2)), 'helicopter')
        self.assertEqual(aircraft_symbol(aircraft._view(aircraft.rows['A00001'], now+301)), 'unknown')
        aircraft._update('A00001', {'df':17,'typecode':4,'category':0}, now+302)
        self.assertEqual(aircraft_symbol(aircraft._view(aircraft.rows['A00001'], now+302)), 'unknown')

    def test_hover_names_category_without_inventing_a_model(self):
        row = {'icao':'A00001','category':7,'category_typecode':4}
        text = aircraft_hover_html(row,{})
        self.assertIn('Map symbol', text)
        self.assertIn('Helicopter / rotorcraft', text)
        self.assertEqual(symbol_label({}), 'Unknown category')


if __name__ == '__main__':
    unittest.main()
