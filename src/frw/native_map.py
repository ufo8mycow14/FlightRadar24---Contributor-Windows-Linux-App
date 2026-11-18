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
from .display import DISPLAY_DEFAULT, destination_point, flight_level, is_emergency, north_offset_deg

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
        self.banner_left = 10
        self.bottom_reserved = 0
        self.display = dict(DISPLAY_DEFAULT)
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
        if self.station is not None and self.display.get('range_rings', True):
            self._draw_range_rings(painter)
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
            emergency = is_emergency(row)
            _plane(painter, point, row.get('track'), chosen)
            if emergency:
                painter.setPen(QPen(QColor('#c62f3b'), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(point, 20, 20)
            mode = self.display.get('map_labels', 'callsign')
            label = str(row.get('callsign') or row.get('icao') or '').strip()
            detail = ''
            if mode == 'detailed' or chosen:
                level = flight_level(row.get('altitude'))
                alt = f'FL{level}' if level is not None else (f"{row['altitude']:,.0f} ft" if _number(row.get('altitude')) else '')
                spd = f"{row['groundspeed']:,.0f} kt" if _number(row.get('groundspeed')) else ''
                detail = ' · '.join(part for part in (alt, spd) if part)
            show_label = mode != 'none' or chosen or emergency
            box = QRectF(point.x() + 15, point.y() - 10, 0, 0)
            if show_label and label:
                lines = [label] + ([detail] if detail else [])
                width = max(painter.fontMetrics().horizontalAdvance(line) for line in lines) + 10
                box = QRectF(point.x() + 15, point.y() - 10, width, 20 if len(lines) == 1 else 34)
                painter.setPen(QPen(QColor('#c62f3b' if emergency else '#bd933e' if chosen else '#8bbcaf'), 1.5 if emergency else 1))
                painter.setBrush(QColor('#ffe9ea' if emergency else '#fff1ce' if chosen else '#f2fffb'))
                painter.drawRoundedRect(box, 3, 3)
                painter.setPen(QColor('#7a1220' if emergency else '#172e3d'))
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, '\n'.join(lines))
            self._markers.append((str(row.get('icao', '')), point, box))
        painter.setClipping(False)
        painter.setPen(QColor('#163645'))
        if not self.rows:
            self._banner(painter, 'Waiting for aircraft with a position. Aircraft without coordinates remain in the list.', 46)
        if self._tile_problem:
            self._banner(painter, 'Map tiles unavailable. Aircraft positions and range rings still update.', self.height() - self.bottom_reserved - 52)
        self._draw_scale(painter)
        self._draw_compass(painter)
        painter.end()
        self._pump_tiles()

    def _draw_range_rings(self, painter):
        """Dotted 50-unit range rings around the antenna, replacing the old radar box."""
        metric = self.display.get('units') == 'metric'
        steps = range(50, 501, 50) if metric else range(50, 301, 50)
        origin = self._screen(*self.station)
        offset = north_offset_deg(self.display)
        painter.save()
        ring_font = QFont('Segoe UI', 8)
        painter.setFont(ring_font)
        drawn = []
        for step in steps:
            distance_nm = step / 1.852 if metric else float(step)
            points = []
            for bearing in range(0, 361, 6):
                lat, lon = destination_point(self.station[0], self.station[1], bearing, distance_nm)
                points.append(self._screen(lat, lon))
            radius = max(abs(points[0].y() - origin.y()), abs(points[15].x() - origin.x()))
            if radius < 24 or radius > max(self.width(), self.height()) * 1.6:
                continue
            painter.setPen(QPen(QColor(8, 119, 104, 130), 1.2, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF(points))
            label = f'{step} km' if metric else f'{step} NM'
            top = points[3]
            painter.setPen(QColor('#087768'))
            painter.drawText(QRectF(top.x() + 4, top.y() - 9, 64, 16), label)
            drawn.append(distance_nm)
        if drawn and self.display.get('cardinals', True):
            bold = QFont('Segoe UI', 9); bold.setBold(True)
            painter.setFont(bold)
            view = QRectF(self.rect()).adjusted(-4, -4, 4, 4)
            for label, bearing in (('N', 0), ('E', 90), ('S', 180), ('W', 270)):
                point = None
                for distance_nm in reversed(drawn):
                    lat, lon = destination_point(self.station[0], self.station[1], (bearing + offset) % 360, distance_nm)
                    candidate = self._screen(lat, lon)
                    if view.contains(candidate):
                        point = candidate
                        break
                if point is None:
                    continue
                painter.setPen(QPen(QColor('#087768'), 1))
                painter.setBrush(QColor(255, 255, 255, 215))
                painter.drawEllipse(point, 10, 10)
                painter.setPen(QColor('#c62f3b' if label == 'N' else '#163645'))
                painter.drawText(QRectF(point.x() - 10, point.y() - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def _draw_compass(self, painter):
        """Compass rose whose N follows the chosen true/magnetic reference."""
        if not self.display.get('cardinals', True):
            return
        offset = north_offset_deg(self.display)
        centre = QPointF(self.width() - 78, 46)
        radius = 30
        painter.save()
        painter.setPen(QPen(QColor(22, 54, 69, 190), 1))
        painter.setBrush(QColor(255, 255, 255, 215))
        painter.drawEllipse(centre, radius, radius)
        bold = QFont('Segoe UI', 8); bold.setBold(True)
        painter.setFont(bold)
        for label, bearing in (('N', 0), ('E', 90), ('S', 180), ('W', 270)):
            angle = math.radians(bearing + offset - 90)
            point = centre + QPointF(math.cos(angle) * (radius - 9), math.sin(angle) * (radius - 9))
            painter.setPen(QColor('#c62f3b' if label == 'N' else '#163645'))
            painter.drawText(QRectF(point.x() - 7, point.y() - 7, 14, 14), Qt.AlignmentFlag.AlignCenter, label)
        needle = math.radians(offset - 90)
        tip = centre + QPointF(math.cos(needle) * (radius - 16), math.sin(needle) * (radius - 16))
        painter.setPen(QPen(QColor('#c62f3b'), 2))
        painter.drawLine(centre, tip)
        painter.setFont(QFont('Segoe UI', 7))
        painter.setPen(QColor('#163645'))
        painter.drawText(QRectF(centre.x() - radius, centre.y() + radius + 1, radius * 2, 12),
                         Qt.AlignmentFlag.AlignCenter, 'MAG' if self.display.get('north_reference') == 'magnetic' else 'TRUE')
        painter.restore()

    def _banner(self, painter, text, y):
        width = self.width() - self.banner_left - 10
        if width < 300:
            return
        box = QRectF(self.banner_left, y, width, 44 if width < 700 else 30)
        painter.fillRect(box, QColor(255, 255, 255, 225))
        painter.drawText(box.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

    def _draw_scale(self, painter):
        latitude = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * self.center.y()))))
        metres_per_pixel = 40075016.686 * math.cos(math.radians(latitude)) / self.world_size
        desired = 110 * metres_per_pixel
        if desired <= 0:
            return
        magnitude = 10 ** math.floor(math.log10(desired))
        distance = next((n * magnitude for n in (5, 2, 1) if n * magnitude <= desired), magnitude / 2)
        width = distance / metres_per_pixel
        y = self.height() - self.bottom_reserved - (48 if getattr(self.parent(), 'attribution', None) is not None and self.parent().attribution.parent() is self else 13)
        x = self.width() - width - 22
        label = f'{distance / 1000:g} km' if distance >= 1000 else f'{distance:g} m'
        painter.fillRect(QRectF(x - 6, y - 24, max(90, width + 12), 30), QColor(255, 255, 255, 220))
        painter.setPen(QPen(QColor('#203f4b'), 2))
        painter.drawLine(QPointF(x, y), QPointF(x + width, y))
        painter.drawLine(QPointF(x, y - 5), QPointF(x, y))
        painter.drawLine(QPointF(x + width, y - 5), QPointF(x + width, y))
        painter.drawText(QRectF(x, y - 23, width + 10, 18), label)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        parent = self.parent()
        if parent is not None and hasattr(parent, '_position_overlays'):
            parent._position_overlays()

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
        self._overlays = []          # (widget, anchor) registered by the window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.canvas = _MapCanvas(self, cache_dir)
        layout.addWidget(self.canvas, 1)
        # Overlays drawn over the map, Flightradar24-style: the map keeps the space.
        self.summary = QLabel('Local airspace · waiting for aircraft positions', self.canvas)
        self.summary.setObjectName('mapChip')
        self.summary.setWordWrap(False)
        self.summary.setToolTip('Drag to pan · double-click or wheel to zoom · select an aircraft for details')
        controls = QWidget(self.canvas)
        controls.setObjectName('mapControls')
        column = QVBoxLayout(controls)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self.zoom_in = QPushButton('+')
        self.zoom_out = QPushButton('−')
        self.home_button = QPushButton('◎')
        self.fit_button = QPushButton('⛶')
        for button, label in ((self.zoom_in, 'Zoom in'), (self.zoom_out, 'Zoom out'),
                              (self.home_button, 'Centre on antenna'), (self.fit_button, 'Fit aircraft')):
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setFixedSize(38, 38)
            column.addWidget(button)
        self._controls = controls
        self.attribution = QLabel('© <a style="color: #9fd1ff" href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>', self.canvas)
        self.attribution.setObjectName('mapChip')
        self.attribution.setOpenExternalLinks(True)
        self.zoom_out.clicked.connect(lambda: self.canvas.zoom_at(-1))
        self.zoom_in.clicked.connect(lambda: self.canvas.zoom_at(1))
        self.home_button.clicked.connect(self.center_home)
        self.fit_button.clicked.connect(self.fit_aircraft)
        self.canvas.aircraftSelected.connect(self.aircraftSelected.emit)
        self.home_button.setEnabled(False)
        self.fit_button.setEnabled(False)
        for widget in (self.summary, self._controls, self.attribution):
            widget.show(); widget.raise_()

    def add_overlay(self, widget, anchor='left'):
        """Attach a window-owned panel over the map ('left' list or 'bottom' status strip)."""
        widget.setParent(self.canvas)
        self._overlays.append((widget, anchor))
        widget.show(); widget.raise_()
        self._position_overlays()

    def refresh_overlays(self):
        self._position_overlays()

    def _position_overlays(self):
        width, height = self.canvas.width(), self.canvas.height()
        if width <= 0 or height <= 0:
            return
        # Bottom drawer first: it reserves vertical space at the map bottom.
        bottom_reserved = 0
        for widget, anchor in self._overlays:
            if anchor == 'bottom-full':
                wanted = getattr(widget, '_overlay_height', 280)
                actual = max(40, min(wanted, max(40, height - 70)))
                widget.setFixedWidth(max(200, width - 20))
                widget.setFixedHeight(actual)
                widget.move(10, height - actual - 10)
                if not widget.isHidden():
                    bottom_reserved = actual + 10
            elif anchor == 'bottom' and not widget.isHidden():
                widget.setFixedWidth(max(200, width - 20))
                widget.adjustSize()
                widget.move(10, height - widget.height() - 8)
                bottom_reserved = max(bottom_reserved, widget.height() + 8)
            elif anchor == 'bottom-left':
                wanted = getattr(widget, '_overlay_height', 280)
                actual = max(40, min(wanted, max(40, height - 70)))
                widget.setFixedHeight(actual)
                widget.move(10, height - actual - 10)
                if not widget.isHidden():
                    bottom_reserved = max(bottom_reserved, actual + 10)
        self.canvas.bottom_reserved = bottom_reserved
        # Left panels next: a compact details popup, capped in height and
        # kept above the bottom drawer rather than running full-length.
        occupied_left = 0
        for widget, anchor in self._overlays:
            if anchor == 'left':
                widget.move(10, 10)
                widget.setFixedHeight(max(160, height - bottom_reserved - 20))
                if not widget.isHidden():
                    occupied_left = max(occupied_left, 10 + widget.width())
        for widget, anchor in self._overlays:
            if anchor == 'details-left':
                wanted = getattr(widget, '_overlay_height', 540)
                available = max(180, height - bottom_reserved - 24)
                widget.setFixedHeight(min(wanted, available))
                widget.move(10, 10)
                if not widget.isHidden():
                    occupied_left = max(occupied_left, 10 + widget.width())
        free_x = occupied_left + 10 if occupied_left else 10
        self.canvas.banner_left = free_x
        free_width = max(0, width - free_x - 120)  # leave room for the compass
        self.summary.setVisible(free_width >= 240)
        if free_width >= 240:
            self.summary.move(free_x, 10)
            self.summary.setFixedWidth(min(560, free_width))
            self.summary.setFixedHeight(28)
        for widget, anchor in self._overlays:
            if anchor == 'top-message':
                widget.setFixedWidth(max(120, min(760, width - free_x - 20)))
                widget.adjustSize()
                widget.move(free_x + max(0, (free_width - widget.width()) // 2), 46)
        self._controls.adjustSize()
        usable_height = max(120, height - bottom_reserved)
        self._controls.move(width - self._controls.width() - 10,
                            max(92, usable_height // 2 - self._controls.height() // 2))
        if self.attribution.parent() is self.canvas:
            self.attribution.adjustSize()
            self.attribution.move(width - self.attribution.width() - 8,
                                  height - bottom_reserved - self.attribution.height() - 6)
            self.attribution.raise_()
        self.summary.raise_(); self._controls.raise_()
        for widget, _anchor in self._overlays:
            widget.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_overlays()

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

    def set_display(self, display):
        self.canvas.display = {**DISPLAY_DEFAULT, **(display or {})}
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