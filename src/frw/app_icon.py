"""Shared FlightRadar24 application identity for Qt and Windows packaging."""
from __future__ import annotations

import struct

from PySide6.QtCore import QByteArray, QBuffer, QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPixmap


def _render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#18374e'))
    painter.drawRoundedRect(0, 0, size, size, size * .19, size * .19)
    painter.setBrush(QColor('#79dbc8'))
    path = QPainterPath()
    scale = size / 64
    path.moveTo(QPointF(48 * scale, 13 * scale))
    path.lineTo(QPointF(54 * scale, 17 * scale))
    path.lineTo(QPointF(38 * scale, 31 * scale))
    path.lineTo(QPointF(38 * scale, 48 * scale))
    path.lineTo(QPointF(33 * scale, 52 * scale))
    path.lineTo(QPointF(29 * scale, 35 * scale))
    path.lineTo(QPointF(15 * scale, 46 * scale))
    path.lineTo(QPointF(14 * scale, 53 * scale))
    path.lineTo(QPointF(10 * scale, 56 * scale))
    path.lineTo(QPointF(9 * scale, 44 * scale))
    path.lineTo(QPointF(19 * scale, 34 * scale))
    path.lineTo(QPointF(8 * scale, 31 * scale))
    path.lineTo(QPointF(12 * scale, 27 * scale))
    path.lineTo(QPointF(26 * scale, 27 * scale))
    path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return image


def create_app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(_render(size)))
    return icon


def write_windows_icon(path) -> None:
    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        image = _render(size)
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        if not image.save(buffer, 'PNG'):
            raise RuntimeError('Could not encode the FlightRadar24 application icon.')
        images.append((size, bytes(payload)))
    offset = 6 + 16 * len(images)
    entries = []
    payloads = []
    for size, data in images:
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        payloads.append(data)
        offset += len(data)
    path = str(path)
    with open(path, 'wb') as stream:
        stream.write(struct.pack('<HHH', 0, 1, len(images)))
        stream.writelines(entries)
        stream.writelines(payloads)
