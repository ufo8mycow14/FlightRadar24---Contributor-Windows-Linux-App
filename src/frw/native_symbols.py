"""Original vector silhouettes for the native map and its compact symbol guide."""
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QGridLayout, QLabel, QVBoxLayout

from .aircraft_symbols import SYMBOLS


@lru_cache(maxsize=len(SYMBOLS))
def symbol_path(symbol):
    """Silhouette coordinates occupy a 32-unit disc; nose is at the top."""
    shapes = []

    def polygon(points):
        path = QPainterPath()
        path.addPolygon(QPolygonF([QPointF(*p) for p in points]))
        path.closeSubpath()
        shapes.append(path)

    def ellipse(x, y, w, h):
        path = QPainterPath()
        path.addEllipse(QRectF(x, y, w, h))
        shapes.append(path)

    def body(width=2, nose=-15, tail=13):
        polygon([(0, nose), (width, nose+4), (width, tail-3), (0, tail), (-width, tail-3), (-width, nose+4)])

    def wings(span, leading, tip, trailing):
        polygon([(0, leading), (span, tip), (span, tip+2), (0, trailing), (-span, tip+2), (-span, tip)])

    def tail(span=6):
        wings(span, 8, 11, 12)

    if symbol == 'light':
        body(1.6); wings(14, -4, -3, 0); tail(5)
        polygon([(-5, -11), (5, -11), (5, -10), (-5, -10)])
    elif symbol == 'small':
        body(2); wings(13, -4, -1, 3); tail(6)
        for x in (-7, 7):
            ellipse(x-1.3, -5, 2.6, 8)
    elif symbol in ('large', 'vortex', 'heavy'):
        body(2.5 if symbol == 'heavy' else 2)
        wings(14, -5 if symbol == 'vortex' else -4, 4, 3); tail(6)
        for x in ((-10, -6, 6, 10) if symbol == 'heavy' else (-7, 7)):
            ellipse(x-1.2, -1 if abs(x)==6 else 1, 2.4, 6)
        if symbol == 'vortex':
            polygon([(-14, 3), (-13, 3), (-12, 7), (-14, 6)])
            polygon([(14, 3), (13, 3), (12, 7), (14, 6)])
    elif symbol == 'performance':
        body(1.8)
        polygon([(0,-8),(11,8),(11,10),(4,7),(5,13),(0,11),(-5,13),(-4,7),(-11,10),(-11,8)])
    elif symbol == 'helicopter':
        ellipse(-3.5, -10, 7, 14)
        polygon([(-1.4,1),(1.4,1),(1,13),(-1,13)])
        polygon([(-5,11),(5,11),(5,12.5),(-5,12.5)])
        # Static crossed rotor blades remain legible even when warnings flash.
        polygon([(-12,-9),(-11,-11),(12,5),(11,7)])
        polygon([(11,-11),(12,-9),(-11,7),(-12,5)])
        for x in (-5, 5):
            polygon([(x-.5,-5),(x+.5,-5),(x+.5,6),(x-.5,6)])
    elif symbol == 'glider':
        body(1, -12, 11); wings(15,-3,-2,0); tail(4)
    elif symbol == 'balloon':
        ellipse(-10,-14,20,22)
        polygon([(-7,3),(7,3),(3,12),(-3,12)])
        polygon([(-3,12),(3,12),(3,15),(-3,15)])
    elif symbol == 'parachute':
        polygon([(-14,-4),(-12,-9),(-7,-13),(0,-15),(7,-13),(12,-9),(14,-4),(7,-6),(0,-4),(-7,-6)])
        for x in (-13,0,13):
            polygon([(x,-4),(x+1,-4),(1,9),(-1,9)])
        ellipse(-2,8,4,4); polygon([(-1,11),(1,11),(2,15),(-2,15)])
    elif symbol == 'ultralight':
        polygon([(0,-13),(14,8),(2,4),(0,8),(-2,4),(-14,8)])
        polygon([(-.7,2),(.7,2),(.7,13),(-.7,13)])
    elif symbol == 'uav':
        # Generic long-wing uncrewed category symbol, not a specific drone model.
        body(1.3,-9,12); wings(15,-2,-1,2)
        polygon([(0,8),(6,12),(5,14),(0,11),(-5,14),(-6,12)])
    elif symbol == 'space':
        body(3,-15,12)
        polygon([(-3,0),(-9,11),(-9,13),(-3,9),(3,9),(9,13),(9,11),(3,0)])
    elif symbol == 'vehicle':
        polygon([(-5,-11),(5,-11),(6,-8),(6,11),(-6,11),(-6,-8)])
        for x in (-7,5):
            for y in (-7,5):
                polygon([(x,y),(x+2,y),(x+2,y+4),(x,y+4)])
    elif symbol == 'obstacle':
        polygon([(0,-14),(13,10),(-13,10)])
    else:
        # Directional marker: category unknown, rather than implying a jet.
        polygon([(0,-14),(9,7),(0,3),(-9,7)])

    result = QPainterPath()
    for path in shapes:
        result = result.united(path)
    return result


def draw_aircraft_symbol(painter, point, heading=0, symbol='unknown', selected=False,
                         emergency=False, flash_on=False, conflict=''):
    painter.save()
    painter.translate(point)
    # Lighter-than-air, parachutists and obstacles have no directional silhouette.
    if symbol not in ('balloon', 'parachute', 'obstacle'):
        painter.rotate(heading)
    size = SYMBOLS.get(symbol, SYMBOLS['unknown'])[1]
    painter.scale(size/32, size/32)
    if emergency or conflict == 'red':
        edge, fill = '#7a1220', '#ff2d3f' if flash_on else '#fff0f1'
    elif conflict == 'yellow':
        edge, fill = '#705400', '#ffdf00' if flash_on else '#fffde2'
    else:
        edge, fill = ('#694700', '#ffbd45') if selected else ('#155b51', '#38c8ad')
    pen = QPen(QColor(edge), 1.3)
    pen.setCosmetic(True)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QColor(fill))
    painter.drawPath(symbol_path(symbol))
    # Simple cockpit/window marks also distinguish vehicle and rotorcraft.
    if symbol == 'helicopter':
        painter.drawLine(QPointF(-2,-5), QPointF(2,-5))
    elif symbol == 'vehicle':
        painter.drawLine(QPointF(-4,-6), QPointF(4,-6))
    elif symbol == 'obstacle':
        painter.drawLine(QPointF(0,-5), QPointF(0,2))
        painter.drawPoint(QPointF(0,6))
    painter.restore()


class AircraftSymbolGuide(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Aircraft symbol guide')
        self.setMinimumWidth(650)
        layout = QVBoxLayout(self)
        title = QLabel('Aircraft symbol guide')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        layout.addWidget(title)
        intro = QLabel('Shapes and sizes follow the received ADS-B emitter category. '
                       'They indicate a broad category, not an exact model, engine type or true scale. '
                       'Unknown or unavailable categories use a directional marker.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setColumnStretch(1,1)
        grid.setColumnStretch(3,1)
        for index, (symbol, (label, _)) in enumerate(SYMBOLS.items()):
            pixmap = QPixmap(56,56)
            pixmap.fill(QColor('#eff7fb'))
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            draw_aircraft_symbol(painter, QPointF(28,28), symbol=symbol)
            painter.end()
            icon = QLabel()
            icon.setFixedSize(56,56)
            icon.setPixmap(pixmap)
            icon.setAccessibleName(label)
            text = QLabel(label)
            text.setWordWrap(True)
            row, column = divmod(index, 2)
            grid.addWidget(icon,row,column*2)
            grid.addWidget(text,row,column*2+1)
        layout.addLayout(grid)
        note = QLabel('Amber = selected · Flashing red = emergency · Yellow/red = estimated conflict. '
                      'Hover over a symbol for its category and received flight details.')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
