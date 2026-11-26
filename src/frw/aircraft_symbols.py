"""Broad map symbols from received ADS-B emitter categories, never model guesses."""
from __future__ import annotations

# Keys use the identification message typecode, NOT the most recent message's
# typecode (which will usually be a position/velocity message).
CATEGORY_SYMBOLS = {
    (4, 1): 'light', (4, 2): 'small', (4, 3): 'large',
    (4, 4): 'vortex', (4, 5): 'heavy', (4, 6): 'performance', (4, 7): 'helicopter',
    (3, 1): 'glider', (3, 2): 'balloon', (3, 3): 'parachute',
    (3, 4): 'ultralight', (3, 6): 'uav', (3, 7): 'space',
    (2, 1): 'vehicle', (2, 3): 'vehicle',
    (2, 4): 'obstacle', (2, 5): 'obstacle', (2, 6): 'obstacle', (2, 7): 'obstacle',
}

# Label, nominal height in logical pixels. Size indicates category, not true scale.
SYMBOLS = {
    'unknown': ('Unknown category', 28),
    'light': ('Light aircraft', 24),
    'small': ('Small aircraft · medium 1', 29),
    'large': ('Large aircraft · medium 2', 34),
    'vortex': ('High-vortex aircraft', 36),
    'heavy': ('Heavy aircraft', 40),
    'performance': ('High-performance aircraft', 32),
    'helicopter': ('Helicopter / rotorcraft', 32),
    'glider': ('Glider / sailplane', 32),
    'balloon': ('Lighter-than-air', 28),
    'parachute': ('Parachutist / skydiver', 26),
    'ultralight': ('Ultralight / hang-glider / paraglider', 28),
    'uav': ('Uncrewed aircraft', 28),
    'space': ('Space / transatmospheric vehicle', 32),
    'vehicle': ('Surface vehicle', 24),
    'obstacle': ('Ground obstacle', 24),
}


def aircraft_symbol(row):
    """Only a complete received category pair selects a specific silhouette.

    Aircraft._view expires both fields after the identity TTL. A bare category,
    callsign, speed or on-ground state cannot establish a type or propulsion.
    """
    tc, category = row.get('category_typecode'), row.get('category')
    if type(tc) is not int or type(category) is not int:
        return 'unknown'
    return CATEGORY_SYMBOLS.get((tc, category), 'unknown')


def symbol_label(row):
    return SYMBOLS[aircraft_symbol(row)][0]


def symbol_radius(symbol):
    # All paths fit within a unit-radius circle; include outline and click margin.
    return max(20, SYMBOLS.get(symbol, SYMBOLS['unknown'])[1] / 2 + 3)
