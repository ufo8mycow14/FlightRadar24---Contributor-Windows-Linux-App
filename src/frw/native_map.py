"""Native Qt map and range radar. Only background tiles use the network."""
from __future__ import annotations

from collections import OrderedDict
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .native_details import NativeAircraftDetails

TILE_SIZE = 256
MAX_LATITUDE = 85.05112878


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _position(latitude, longitude):
    return (_number(latitude) and _number(longitude) and -90 <= latitude <= 90
            and -180 <= longitude <= 180)


def _project(latitude, longitude):
    """Return normalized Web Mercator coordinates, allowing unwrapped longitude."""
    latitude = max(-MAX_LATITUDE, min(MAX_LATITUDE, latitude))
    rad = math.radians(latitude)
    return (longitude + 180) / 360, (1 - math.asinh(math.tan(rad)) / math.pi) / 2


def _nearby(value, reference):
    return value + round(reference - value)


def _plane(painter, point, heading, selected=False):
    painter.save()
    painter.translate(point)
    painter.rotate(float(heading) if _number(heading) else 0)
    painter.setPen(QPen(QColor('#694700' if selected else '#155b51'), 1.2))
    painter.setBrush(QColor('#ffbd45' if selected else '#38c8ad'))
    painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in (
        (0, -16), (3, -4), (13, 4), (13, 7), (3, 4), (2, 11),
        (6, 14), (6, 16), (0, 14), (-6, 16), (-6, 14), (-2, 11),
        (-3, 4), (-13, 7), (-13, 4), (-3, -4))]))
    painter.restore()


def _distance_bearing(origin, destination):
    a, b = map(math.radians, (origin[0], destination[0]))
    dlat = b - a
    dlon = math.radians(destination[1] - origin[1])
    hav = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    distance = 3440.065 * 2 * math.asin(math.sqrt(max(0, min(1, hav))))
    bearing = math.atan2(math.sin(dlon) * math.cos(b),
                         math.cos(a) * math.sin(b) - math.sin(a) * math.cos(b) * math.cos(dlon))
    return distance, bearing


class _MapCanvas(QWidget):
    aircraftSelected = Signal(str)

    def __init__(self, parent=None, cache_dir=None):
        super().__init__(parent)
        self.setMinimumSize(300, 260)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Map of received aircraft. Arrow keys pan; plus and minus zoom. Double-click zooms in.')
        self.setMouseTracking(True)
        self.zoom = 2
        self.center = QPointF(.5, .5)
        self.rows = []
        self.station = None
        self.selected = ''
        self._tiles = OrderedDict()
        self._failed = {}
        self._inflight = {}
        self._needed = []
        self._markers = []
        self._press = None
        self._last_drag = None
        self._dragged = False
        self._network = QNetworkAccessManager(self)
        if cache_dir is not None:
            cache = QNetworkDiskCache(self._network)
            cache.setCacheDirectory(str(cache_dir))
            cache.setMaximumCacheSize(64 * 1024 * 1024)
            self._network.setCache(cache)
        self._tile_problem = False

    @property
    def world_size(self):
        return TILE_SIZE * 2 ** self.zoom

    def _screen(self, latitude, longitude):
        x, y = _project(latitude, longitude)
        x = _nearby(x, self.center.x())
        return QPointF(self.width() / 2 + (x - self.center.x()) * self.world_size,
                       self.height() / 2 + (y - self.center.y()) * self.world_size)

    def set_center(self, latitude, longitude, zoom=None):
        if zoom is not None:
            self.zoom = max(0, min(18, int(zoom)))
        x, y = _project(latitude, longitude)
        self.center = QPointF(x % 1, max(0, min(1, y)))
        self.update()

    def zoom_at(self, delta, point=None):
        zoom = max(0, min(18, self.zoom + delta))
        if zoom == self.zoom:
            return
        anchor = point if point is not None else QPointF(self.width() / 2, self.height() / 2)
        offset = anchor - QPointF(self.width() / 2, self.height() / 2)
        world_anchor = self.center + offset / self.world_size
        self.zoom = zoom
        center = world_anchor - offset / self.world_size
        self.center = QPointF(center.x() % 1, max(0, min(1, center.y())))
        self.update()

    def _pump_tiles(self):
        now = time.monotonic()
        for key in self._needed:
            if len(self._inflight) >= 6:
                break
            if key in self._tiles or key in self._inflight or self._failed.get(key, 0) > now:
                continue
            z, x, y = key
            request = QNetworkRequest(QUrl(f'https://tile.openstreetmap.org/{z}/{x}/{y}.png'))
            request.setRawHeader(b'User-Agent', b'FlightRadar24-Windows/0.1 (native ADS-B receiver; QtNetwork)')
            request.setRawHeader(b'Accept', b'image/png')
            request.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                                 QNetworkRequest.CacheLoadControl.PreferCache)
            request.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, True)
            request.setTransferTimeout(12000)
            reply = self._network.get(request)
            self._inflight[key] = reply
            reply.finished.connect(lambda key=key, reply=reply: self._tile_finished(key, reply))

    def _tile_finished(self, key, reply):
        from PySide6.QtGui import QPixmap
        self._inflight.pop(key, None)
        pixmap = QPixmap()
        ok = reply.error() == QNetworkReply.NetworkError.NoError and pixmap.loadFromData(reply.readAll())
        if ok:
            self._tiles[key] = pixmap
            self._tiles.move_to_end(key)
            while len(self._tiles) > 256:
                self._tiles.popitem(last=False)
            self._failed.pop(key, None)
        else:
            self._failed[key] = time.monotonic() + 30
            # Keep failure tracking bounded as the user pans around the world.
            if len(self._failed) > 512:
                self._failed = dict(list(self._failed.items())[-256:])
        reply.deleteLater()
        self._tile_problem = any(self._failed.get(key, 0) > time.monotonic() for key in self._needed)
        self.update()
        self._pump_tiles()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#dbe8ed'))
        world = self.world_size
        left = self.center.x() * world - self.width() / 2
        top = self.center.y() * world - self.height() / 2
        count = 2 ** self.zoom
        needed = []
        for tile_y in range(max(0, math.floor(top / TILE_SIZE)),
                            min(count - 1, math.floor((top + self.height()) / TILE_SIZE)) + 1):
            for tile_x in range(math.floor(left / TILE_SIZE), math.floor((left + self.width()) / TILE_SIZE) + 1):
                key = (self.zoom, tile_x % count, tile_y)
                if key not in needed:
                    needed.append(key)
                box = QRectF(tile_x * TILE_SIZE - left, tile_y * TILE_SIZE - top, TILE_SIZE, TILE_SIZE)
                tile = self._tiles.get(key)
                if tile is not None:
                    self._tiles.move_to_end(key)
                    painter.drawPixmap(box, tile, QRectF(tile.rect()))
                else:
                    painter.setPen(QPen(QColor('#bdcfd6'), 1))
                    painter.drawRect(box)
        self._needed = needed
        painter.setClipRect(self.rect())
        for row in sorted(self.rows, key=lambda row: row.get('icao') == self.selected):
            chosen = row.get('icao') == self.selected
            trail = [point for point in row.get('trail', [])
                     if isinstance(point, (list, tuple)) and len(point) >= 2 and _position(point[0], point[1])]
            if trail:
                trail.append((row['latitude'], row['longitude']))
                points = []
                reference = _nearby(_project(row['latitude'], row['longitude'])[0], self.center.x())
                for latitude, longitude, *_ in reversed(trail):
                    x, y = _project(latitude, longitude)
                    x = _nearby(x, reference)
                    points.append(QPointF((x - self.center.x()) * world + self.width() / 2,
                                          (y - self.center.y()) * world + self.height() / 2))
                    reference = x
                painter.setPen(QPen(QColor('#ba7a04' if chosen else '#238a78'), 3 if chosen else 2))
                painter.drawPolyline(QPolygonF(points))
        if self.station is not None:
            center = self._screen(*self.station)
            painter.setPen(QPen(QColor('white'), 2))
            painter.setBrush(QColor('#087768'))
            painter.drawEllipse(center, 6, 6)
        self._markers = []
        painter.setFont(QFont('Segoe UI', 9))
        for row in sorted(self.rows, key=lambda row: row.get('icao') == self.selected):
            point = self._screen(row['latitude'], row['longitude'])
            if not QRectF(self.rect()).adjusted(-40, -40, 40, 40).contains(point):
                continue
            chosen = row.get('icao') == self.selected
            _plane(painter, point, row.get('track'), chosen)
            label = str(row.get('callsign') or row.get('icao') or '').strip()
            width = painter.fontMetrics().horizontalAdvance(label) + 8
            box = QRectF(point.x() + 15, point.y() - 10, width, 20)
            painter.setPen(QPen(QColor('#bd933e' if chosen else '#8bbcaf'), 1))
            painter.setBrush(QColor('#fff1ce' if chosen else '#f2fffb'))
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QColor('#172e3d'))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
            self._markers.append((str(row.get('icao', '')), point, box))
        painter.setClipping(False)
        painter.setPen(QColor('#163645'))
        if not self.rows:
            self._banner(painter, 'Waiting for aircraft with a position. Aircraft without coordinates remain in the table.', 12)
        if self._tile_problem:
            self._banner(painter, 'Map tiles unavailable. Aircraft positions and range radar still update.', self.height() - 42)
        self._draw_scale(painter)
        painter.end()
        self._pump_tiles()

    def _banner(self, painter, text, y):
        box = QRectF(10, y, max(50, self.width() - 20), 30)
        painter.fillRect(box, QColor(255, 255, 255, 225))
        painter.drawText(box.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignCenter, text)

    def _draw_scale(self, painter):
        latitude = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * self.center.y()))))
        metres_per_pixel = 40075016.686 * math.cos(math.radians(latitude)) / self.world_size
        desired = 110 * metres_per_pixel
        if desired <= 0:
            return
        magnitude = 10 ** math.floor(math.log10(desired))
        distance = next((n * magnitude for n in (5, 2, 1) if n * magnitude <= desired), magnitude / 2)
        width = distance / metres_per_pixel
        y = self.height() - 13
        label = f'{distance / 1000:g} km' if distance >= 1000 else f'{distance:g} m'
        painter.fillRect(QRectF(9, y - 24, max(90, width + 12), 30), QColor(255, 255, 255, 220))
        painter.setPen(QPen(QColor('#203f4b'), 2))
        painter.drawLine(QPointF(15, y), QPointF(15 + width, y))
        painter.drawLine(QPointF(15, y - 5), QPointF(15, y))
        painter.drawLine(QPointF(15 + width, y - 5), QPointF(15 + width, y))
        painter.drawText(QRectF(15, y - 23, width + 10, 18), label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._press = event.position()
            self._last_drag = event.position()
            self._dragged = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None:
            if (event.position() - self._press).manhattanLength() > 4:
                self._dragged = True
            if self._dragged:
                delta = event.position() - self._last_drag
                center = self.center - delta / self.world_size
                self.center = QPointF(center.x() % 1, max(0, min(1, center.y())))
                self.update()
            self._last_drag = event.position()
            event.accept()
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            if not self._dragged:
                for icao, point, box in reversed(self._markers):
                    delta = event.position() - point
                    if delta.x() ** 2 + delta.y() ** 2 <= 20 ** 2 or box.contains(event.position()):
                        self.aircraftSelected.emit(icao)
                        break
            self._press = self._last_drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            # The first click already selected any aircraft. Do not reuse its
            # drag or marker coordinates for the release after the zoom.
            self._press = self._last_drag = None
            self._dragged = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.zoom_at(1, event.position())
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        self.zoom_at(1 if event.angleDelta().y() > 0 else -1, event.position())
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_at(1)
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.zoom_at(-1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            x = 80 if key == Qt.Key.Key_Right else -80 if key == Qt.Key.Key_Left else 0
            y = 80 if key == Qt.Key.Key_Down else -80 if key == Qt.Key.Key_Up else 0
            center = self.center + QPointF(x, y) / self.world_size
            self.center = QPointF(center.x() % 1, max(0, min(1, center.y())))
            self.update()
        else:
            return super().keyPressEvent(event)
        event.accept()


class NativeMap(QWidget):
    """Worldwide native tile map; received positions are independent of tile access."""
    aircraftSelected = Signal(str)

    def __init__(self, parent=None, cache_dir=None):
        super().__init__(parent)
        self._centered = False
        self._station = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.summary = QLabel('Local airspace · waiting for aircraft positions')
        self.summary.setWordWrap(True)
        toolbar.addWidget(self.summary, 1)
        self.zoom_out = QPushButton('−')
        self.zoom_in = QPushButton('+')
        for button, label in ((self.zoom_out, 'Zoom out'), (self.zoom_in, 'Zoom in')):
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setFixedWidth(34)
            toolbar.addWidget(button)
        self.home_button = QPushButton('Antenna')
        self.fit_button = QPushButton('Fit aircraft')
        toolbar.addWidget(self.home_button)
        toolbar.addWidget(self.fit_button)
        layout.addLayout(toolbar)
        self.canvas = _MapCanvas(self, cache_dir)
        layout.addWidget(self.canvas, 1)
        footer = QHBoxLayout()
        hint = QLabel('Drag to pan · double-click or wheel to zoom · select an aircraft')
        hint.setWordWrap(True)
        footer.addWidget(hint, 1)
        attribution = QLabel('© <a style="color: #9fd1ff" href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>')
        attribution.setOpenExternalLinks(True)
        footer.addWidget(attribution)
        layout.addLayout(footer)
        self.zoom_out.clicked.connect(lambda: self.canvas.zoom_at(-1))
        self.zoom_in.clicked.connect(lambda: self.canvas.zoom_at(1))
        self.home_button.clicked.connect(self.center_home)
        self.fit_button.clicked.connect(self.fit_aircraft)
        self.canvas.aircraftSelected.connect(self.aircraftSelected.emit)
        self.home_button.setEnabled(False)
        self.fit_button.setEnabled(False)

    def update_aircraft(self, rows, config, selected=''):
        rows = list(rows or [])
        self.canvas.rows = [row for row in rows if _position(row.get('latitude'), row.get('longitude'))]
        self.canvas.selected = selected or ''
        config = config or {}
        station = ((config['latitude'], config['longitude'])
                   if _position(config.get('latitude'), config.get('longitude')) else None)
        if station != self._station:
            self._station = station
            if station is not None:
                self.canvas.set_center(*station, zoom=9)
                self._centered = True
        self.canvas.station = station
        if not self._centered and self.canvas.rows:
            first = self.canvas.rows[0]
            self.canvas.set_center(first['latitude'], first['longitude'], 9)
            self._centered = True
        count = len(self.canvas.rows)
        source = 'Recorded playback' if config.get('receiver') == 'iq-file' else 'Live positions'
        self.summary.setText(f'Local airspace · {source} · {count} aircraft with a position'
                             + (f' · {len(rows) - count} awaiting coordinates' if len(rows) != count else ''))
        self.home_button.setEnabled(station is not None)
        self.fit_button.setEnabled(bool(count))
        self.canvas.update()

    def center_home(self):
        if self._station is not None:
            self.canvas.set_center(*self._station, zoom=9)

    def focus_aircraft(self, icao):
        for row in self.canvas.rows:
            if row.get('icao') == icao:
                self.canvas.set_center(row['latitude'], row['longitude'])
                break

    def fit_aircraft(self):
        rows = self.canvas.rows
        if not rows:
            return
        xs = sorted(_project(row['latitude'], row['longitude'])[0] % 1 for row in rows)
        gaps = [(xs[(i + 1) % len(xs)] + (1 if i == len(xs) - 1 else 0) - x, i)
                for i, x in enumerate(xs)]
        _, index = max(gaps)
        start = xs[(index + 1) % len(xs)]
        points = []
        for row in rows:
            x, y = _project(row['latitude'], row['longitude'])
            x %= 1
            if x < start:
                x += 1
            points.append((x, y))
        left, right = min(x for x, _ in points), max(x for x, _ in points)
        top, bottom = min(y for _, y in points), max(y for _, y in points)
        width, height = max(40, self.canvas.width() - 110), max(40, self.canvas.height() - 110)
        scale = min(width / max(right - left, 1e-9), height / max(bottom - top, 1e-9))
        max_zoom = 9 if len(rows) == 1 else 11
        self.canvas.zoom = max(0, min(max_zoom, math.floor(math.log2(scale / TILE_SIZE))))
        self.canvas.center = QPointF(((left + right) / 2) % 1, (top + bottom) / 2)
        self.canvas.update()


class _RadarCanvas(QWidget):
    aircraftSelected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.setAccessibleName('Offline range radar showing received aircraft positions')
        self.rows, self.station, self.selected, self.range_nm = [], None, '', 200
        self._markers = []

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#102b3b'))
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = max(10, min(self.width(), self.height()) / 2 - 30)
        painter.setFont(QFont('Segoe UI', 9))
        for ring in range(1, 5):
            r = radius * ring / 4
            painter.setPen(QPen(QColor('#315466'), 1))
            painter.drawEllipse(center, r, r)
            painter.setPen(QColor('#92b4c7'))
            painter.drawText(QPointF(center.x() + 5, center.y() - r + 14), f'{self.range_nm * ring / 4:g}')
        painter.setPen(QPen(QColor('#315466'), 1))
        for index in range(8):
            angle = index * math.pi / 4
            painter.drawLine(center, center + QPointF(math.sin(angle) * radius, -math.cos(angle) * radius))
        painter.setPen(QColor('#92b4c7'))
        for text, x, y in (('N', 0, -radius - 10), ('E', radius + 12, 4), ('S', 0, radius + 20), ('W', -radius - 20, 4)):
            painter.drawText(center + QPointF(x - 3, y), text)
        self._markers = []
        if self.station is not None:
            def point(latitude, longitude):
                distance, bearing = _distance_bearing(self.station, (latitude, longitude))
                return center + QPointF(math.sin(bearing), -math.cos(bearing)) * (distance / self.range_nm * radius), distance
            clip = QPainterPath()
            clip.addEllipse(center, radius, radius)
            painter.setClipPath(clip)
            for row in sorted(self.rows, key=lambda row: row.get('icao') == self.selected):
                position, distance = point(row['latitude'], row['longitude'])
                if distance > self.range_nm:
                    continue
                chosen = row.get('icao') == self.selected
                trail = [point(item[0], item[1])[0] for item in row.get('trail', [])
                         if isinstance(item, (list, tuple)) and len(item) >= 2 and _position(item[0], item[1])]
                if len(trail) > 1:
                    painter.setPen(QPen(QColor('#f3ba51' if chosen else '#317967'), 2 if chosen else 1))
                    painter.drawPolyline(QPolygonF(trail))
                _plane(painter, position, row.get('track'), chosen)
                painter.setPen(QColor('#ffe0a1' if chosen else '#c8e3df'))
                painter.drawText(position + QPointF(15, 4), str(row.get('callsign') or row.get('icao') or ''))
                self._markers.append((str(row.get('icao', '')), position))
            painter.setClipping(False)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#bfdae4'))
            painter.drawEllipse(center, 3, 3)
        else:
            painter.setPen(QColor('#bfdae4'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Set your antenna location\nor wait for an aircraft position')
        painter.end()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for icao, point in reversed(self._markers):
                delta = event.position() - point
                if delta.x() ** 2 + delta.y() ** 2 < 20 ** 2:
                    self.aircraftSelected.emit(icao)
                    return
        super().mouseReleaseEvent(event)


class RangeRadar(QWidget):
    aircraftSelected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.caption = QLabel('Range radar · offline')
        self.caption.setWordWrap(True)
        header.addWidget(self.caption, 1)
        self.range_control = QComboBox()
        self.range_control.setAccessibleName('Radar range in nautical miles')
        for value in (50, 100, 200, 300):
            self.range_control.addItem(f'{value} NM', value)
        self.range_control.setCurrentIndex(2)
        header.addWidget(self.range_control)
        layout.addLayout(header)
        self.canvas = _RadarCanvas(self)
        layout.addWidget(self.canvas, 1)
        self.range_control.currentIndexChanged.connect(lambda: self.set_range(self.range_control.currentData()))
        self.canvas.aircraftSelected.connect(self.aircraftSelected.emit)

    def set_range(self, nm):
        if _number(nm) and nm > 0:
            self.canvas.range_nm = float(nm)
            index = next((i for i in range(self.range_control.count())
                          if self.range_control.itemData(i) == nm), -1)
            was_blocked = self.range_control.blockSignals(True)
            if index < 0:
                self.range_control.addItem(f'{nm:g} NM', nm)
                index = self.range_control.count() - 1
            self.range_control.setCurrentIndex(index)
            self.range_control.blockSignals(was_blocked)
            self.canvas.update()

    def update_aircraft(self, rows, config, selected=''):
        self.canvas.rows = [row for row in rows or [] if _position(row.get('latitude'), row.get('longitude'))]
        config = config or {}
        antenna = _position(config.get('latitude'), config.get('longitude'))
        self.canvas.station = ((config['latitude'], config['longitude']) if antenna else
                               (self.canvas.rows[0]['latitude'], self.canvas.rows[0]['longitude']) if self.canvas.rows else None)
        self.canvas.selected = selected or ''
        if self.canvas.station is not None:
            lat, lon = self.canvas.station
            self.caption.setText(f'Range radar · {lat:.4f}°, {lon:.4f}° · {"antenna" if antenna else "first aircraft"}')
        else:
            self.caption.setText('Range radar · offline')
        self.canvas.update()