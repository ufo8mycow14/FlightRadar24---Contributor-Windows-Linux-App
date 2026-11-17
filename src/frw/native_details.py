"""Native aircraft telemetry panel for the standalone Windows application."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)


GROUPS = [
    ("Identity & status", [
        ("icao", "ICAO24 address", "text"), ("squawk", "Squawk", "text"),
        ("wake_vortex", "Emitter category", "text"), ("category", "Category code", "category"),
        ("on_ground", "Flight state", "ground"), ("emergency_state", "Emergency status", "emergency"),
    ]),
    ("Position", [
        ("latitude", "Latitude", "coordinate"), ("longitude", "Longitude", "coordinate"),
        ("distance_nm", "Distance from antenna", "distance"),
    ]),
    ("Flight measurements", [
        ("altitude_baro", "Barometric altitude", "feet"), ("altitude_gnss", "GNSS altitude", "feet"),
        ("altitude_unspecified", "Altitude (source unspecified)", "feet"),
        ("groundspeed", "Ground speed", "knots"), ("indicated_airspeed", "Indicated airspeed", "knots"),
        ("true_airspeed", "True airspeed", "knots"), ("mach", "Mach", "mach"),
        ("track", "Ground track", "degrees"), ("true_track", "True track", "degrees"),
        ("heading", "Heading", "heading"), ("magnetic_heading", "Magnetic heading", "degrees"),
        ("vertical_rate", "Vertical rate", "vertical"), ("baro_vertical_rate", "Barometric vertical rate", "vertical"),
        ("gnss_vertical_rate", "GNSS vertical rate", "vertical"), ("inertial_vertical_rate", "Inertial vertical rate", "vertical"),
        ("vertical_rate_unspecified", "Vertical rate (source unspecified)", "vertical"),
        ("geo_minus_baro", "GNSS minus barometric altitude", "feet"),
        ("roll", "Roll angle", "degrees"), ("track_rate", "Track rate", "turn"),
    ]),
    ("Reported targets & modes", [
        ("selected_altitude", "Selected altitude", "feet"), ("selected_altitude_source", "Selected altitude source", "text"),
        ("selected_altitude_mcp", "MCP selected altitude", "feet"), ("selected_altitude_fms", "FMS selected altitude", "feet"),
        ("target_altitude_source", "Target altitude source", "text"), ("selected_heading", "Selected heading", "degrees"),
        ("baro_pressure_setting", "Pressure setting", "pressure"), ("autopilot", "Autopilot", "boolean"),
        ("vnav_mode", "VNAV", "boolean"), ("lnav_mode", "LNAV", "boolean"),
        ("altitude_hold_mode", "Altitude hold", "boolean"), ("approach_mode", "Approach mode", "boolean"),
    ]),
    ("Signal integrity & capability", [
        ("version", "ADS-B version", "code"), ("nac_p", "Position accuracy (NACp)", "quality"),
        ("nac_v", "Velocity accuracy (NACv)", "quality"), ("nuc_p", "Legacy NUCp code (from typecode)", "quality"),
        ("sil", "Source integrity (SIL)", "quality"), ("sil_supplement", "SIL supplement", "code"),
        ("nic_b", "NIC B / SAF bit", "code"), ("nic_supplement_a", "NIC supplement A", "code"),
        ("nic_baro", "Barometric integrity bit", "code"), ("hrd", "Heading reference", "reference"),
        ("capability_class", "Capability bitfield", "hex"), ("operational_mode", "Operational bitfield", "hex"),
        ("tcas_operational", "TCAS operational", "boolean"), ("acas_operational", "ACAS operational", "boolean"),
        ("issued_ra", "Resolution advisory issued", "boolean"), ("ra_terminated", "Resolution advisory terminated", "boolean"),
        ("multiple_threat", "Multiple threats", "boolean"),
    ]),
]

IGNORED_EXTRA = {
    "icao", "messages", "first_seen", "last_seen", "seen", "trail", "history", "position_time",
    "field_times", "field_ages", "field_sources", "data_source", "callsign", "altitude_source",
    "vr_source", "category_typecode", "altitude",
}


def _present(value):
    return value is not None and value != ""


def _fmt_number(value, unit="", digits=0):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "Unavailable"
    text = f"{value:,.{digits}f}"
    if digits:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {unit}" if unit else text


def _age_text(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s ago"
    return f"{seconds // 3600}h ago"


def _display(key, value, kind, detail):
    if not _present(value):
        return "Not received"
    if kind == "feet": return _fmt_number(value, "ft")
    if kind == "knots": return _fmt_number(value, "kt", 1)
    if kind == "degrees": return _fmt_number(value, "°", 1)
    if kind == "distance": return _fmt_number(value, "NM", 1)
    if kind == "coordinate":
        return f"{value:.6f}" if isinstance(value, (int, float)) and math.isfinite(value) else "Unavailable"
    if kind == "vertical": return _fmt_number(value, "ft/min")
    if kind == "pressure": return _fmt_number(value, "hPa", 1)
    if kind == "turn": return _fmt_number(value, "°/s", 2)
    if kind == "mach": return f"{value:.3f}" if isinstance(value, (int, float)) and math.isfinite(value) else "Unavailable"
    if kind == "boolean": return "On / yes" if value is True else "Off / no" if value is False else str(value)
    if kind == "ground": return "On ground" if value else "Airborne"
    if kind == "emergency":
        names = ("No emergency reported", "General emergency", "Medical / lifeguard", "Minimum fuel",
                 "No communications", "Unlawful interference", "Downed aircraft", "Reserved")
        return names[value] if isinstance(value, int) and 0 <= value < len(names) else f"Code {value}"
    if kind == "reference": return "True north" if value == 0 else "Magnetic north" if value == 1 else f"Code {value}"
    if kind == "quality": return "0 · unknown / unavailable" if value == 0 else f"Code {value}"
    if kind == "hex": return f"0x{value:X}" if isinstance(value, int) else "Unavailable"
    if kind == "category": return str(value) + (f" · typecode {detail['category_typecode']}" if _present(detail.get("category_typecode")) else "")
    if kind == "heading":
        suffix = " true" if detail.get("hrd") == 0 else " magnetic" if detail.get("hrd") == 1 else ""
        return _fmt_number(value, "°", 1) + suffix
    return str(value)


def _label(text="", name="", *, selectable=False):
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class _MetricCard(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("flightMetric")
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)
        self.caption = _label(title, "metricCaption")
        self.value = _label("—", "metricValue", selectable=True)
        self.source = _label("", "metricSource")
        self.note = _label("Not received", "fieldNote")
        for label in (self.caption, self.value, self.source, self.note):
            layout.addWidget(label)
        self.field_key = None

    def set_value(self, key, value, source, note):
        self.field_key = key
        self.value.setText(value)
        self.source.setText(source)
        self.note.setText(note)
        self.setAccessibleName(self.caption.text())
        self.setAccessibleDescription(" · ".join((value, source, note)))


class _DetailSection(QFrame):
    """A native, keyboard-operable disclosure with stable expansion state."""

    def __init__(self, title):
        super().__init__()
        self.setObjectName("broadcastSection")
        self.setMinimumWidth(0)
        self.title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toggle = QToolButton()
        self.toggle.setText(title.replace("&", "&&"))
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle.setMinimumWidth(0)
        self.toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.toggle.setAccessibleName(title)
        self.body = QWidget()
        self.body.setMinimumWidth(0)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(12, 4, 12, 12)
        self.body_layout.setSpacing(8)
        self.empty = _label("No fields received yet.", "fieldNote")
        self.body_layout.addWidget(self.empty)
        layout.addWidget(self.toggle)
        layout.addWidget(self.body)
        self.toggle.toggled.connect(self._set_expanded)
        self._set_expanded(False)

    def _set_expanded(self, expanded):
        self.body.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle.setAccessibleDescription("Expanded" if expanded else "Collapsed")


class HistoryChart(QWidget):
    """Compact dark chart with gaps between sparse, timestamped reports."""

    def __init__(self, series, unit, parent=None):
        super().__init__(parent)
        self.series = series
        self.unit = unit
        self.samples = []
        self.setMinimumWidth(0)
        self.setFixedHeight(116)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Altitude history" if unit == "ft" else "Speed history")

    def sizeHint(self):
        return QSize(280, 116)

    def set_samples(self, samples):
        self.samples = list(samples or [])[-300:]
        summaries = []
        for label, _color, points in self.series_points():
            stamp, value = points[-1]
            unit = "feet" if self.unit == "ft" else "knots"
            summaries.append(f"{label}: latest {_fmt_number(value)} {unit}, {_age_text(time.time() - stamp)}; "
                             f"{len(points)} received samples.")
        self.setAccessibleDescription(
            f"Last ten minutes of received reports, in {self.unit}. "
            "Gaps longer than thirty seconds are not connected. "
            + (" ".join(summaries) if summaries else "No received samples in this period."))
        self.update()

    def series_points(self, now=None):
        """Use field report times, with sample time for legacy records lacking them."""
        now = time.time() if now is None else now
        result = []
        for key, label, color in self.series:
            points = []
            for sample in self.samples:
                value_key = key
                value = sample.get(key)
                if key == "altitude_unspecified" and not _present(value):
                    if not _present(sample.get("altitude_baro")) and not _present(sample.get("altitude_gnss")):
                        value_key = "altitude"
                        value = sample.get(value_key)
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                    continue
                stamp = (sample.get("field_times") or {}).get(value_key, sample.get("time"))
                if not isinstance(stamp, (int, float)) or not now - 600 <= stamp <= now + 2:
                    continue
                points.append((stamp, float(value)))
            if points:
                result.append((label, color, sorted(set(points))))
        return result

    @staticmethod
    def segments(points):
        result = []
        for point in points:
            if not result or point[0] - result[-1][-1][0] > 30:
                result.append([])
            result[-1].append(point)
        return result

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#142635"))
        painter.setFont(QFont("Segoe UI", 8))
        now = time.time()
        series = self.series_points(now)
        values = [value for _label, _color, points in series for _stamp, value in points]
        if not values:
            painter.setPen(QColor("#a8becd"))
            painter.drawText(self.rect().adjusted(8, 4, -8, -4),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             "Waiting for received " + ("altitude" if self.unit == "ft" else "speed") + " samples")
            painter.end()
            return
        low, high = min(values), max(values)
        if low == high:
            margin = 100 if self.unit == "ft" else 10
            low -= margin
            high += margin
        axis_width = max(painter.fontMetrics().horizontalAdvance(f"{value:,.0f}") for value in (low, high)) + 9
        rect = QRectF(self.rect().adjusted(axis_width, 27, -6, -22))

        def screen_point(stamp, value):
            return QPointF(rect.left() + (stamp - (now - 600)) / 600 * rect.width(),
                           rect.bottom() - (value - low) / (high - low) * rect.height())

        for i in range(3):
            value = low + (high - low) * i / 2
            y = rect.bottom() - i / 2 * rect.height()
            painter.setPen(QPen(QColor("#2a4254"), 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor("#a8becd"))
            painter.drawText(QRectF(0, y - 8, axis_width - 7, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{value:,.0f}")
        painter.drawText(QRectF(rect.left(), rect.bottom() + 5, 85, 17), Qt.AlignmentFlag.AlignLeft, "10 min ago")
        painter.drawText(QRectF(rect.right() - 40, rect.bottom() + 5, 40, 17), Qt.AlignmentFlag.AlignRight, "Now")
        legend_width = self.width() / len(series)
        for index, (label, color_text, points) in enumerate(series):
            color = QColor(color_text)
            painter.save()
            painter.setClipRect(rect.adjusted(-3, -3, 3, 3))
            for segment in self.segments(points):
                path = QPainterPath()
                for i, (stamp, value) in enumerate(segment):
                    point = screen_point(stamp, value)
                    if i == 0:
                        path.moveTo(point)
                    else:
                        path.lineTo(point)
                    painter.setPen(QPen(color, 1.5))
                    painter.setBrush(color)
                    painter.drawEllipse(point, 1.8, 1.8)
                painter.setPen(QPen(color, 1.6))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
            painter.restore()
            x = index * legend_width + 3
            painter.fillRect(QRectF(x, 9, 8, 3), color)
            painter.setPen(QColor("#d4e2ec"))
            painter.drawText(QRectF(x + 12, 1, legend_width - 15, 18), Qt.AlignmentFlag.AlignLeft, label)
        painter.end()


class NativeAircraftDetails(QWidget):
    """Source-labelled flight overview, sparse history and expandable reports."""

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.snapshot = {}
        self.input_type = "usb"
        self.connection_error = False
        self.lost = False
        self.updated_at = time.time()
        self.error_message = ""
        self.rows = {}
        self.extra_rows = {}
        self.extra_notes = {}
        self.row_widgets = {}
        self.sections = {}
        self.setObjectName("aircraftDetails")
        self.setMinimumWidth(0)
        self._build()
        self._render()
        self.age_timer = QTimer(self)
        self.age_timer.timeout.connect(self._refresh_ages)
        self.age_timer.start(1000)

    def _build(self):
        self.setStyleSheet("""
            QWidget#aircraftDetails, QWidget#detailsContent { background: #10202e; color: #e6eff5; }
            QWidget#aircraftDetails QLabel { background: transparent; border: none; padding: 0; color: #e6eff5; font: 10pt 'Segoe UI'; }
            QFrame#nativeDetailsHeader { background: #152b3b; border: none; border-bottom: 1px solid #315066; }
            QWidget#aircraftDetails QLabel#detailEyebrow { color: #91b2c7; font-size: 9px; font-weight: 600; }
            QWidget#aircraftDetails QLabel#detailsTitle { color: #ffffff; font-size: 25px; font-weight: 700; }
            QWidget#aircraftDetails QLabel#detailSubtitle { color: #b8ccda; font-size: 11px; }
            QWidget#aircraftDetails QLabel#detailSource { color: #8be1db; font-size: 11px; }
            QWidget#aircraftDetails QLabel#detailReception { color: #a7eee1; background: #20483f; border-radius: 4px; padding: 3px 6px; font-size: 11px; }
            QWidget#aircraftDetails QLabel#detailReception[state='quiet'] { color: #ffda9b; background: #4a3924; }
            QWidget#aircraftDetails QLabel#detailReception[state='error'] { color: #ffd0c8; background: #4e3033; }
            QWidget#aircraftDetails QLabel#detailReception[state='waiting'] { color: #c4d5e1; background: #2a4051; }
            QWidget#aircraftDetails QLabel#detailMessage { color: #b8ccda; font-size: 11px; }
            QPushButton#closeAircraft { background: #253f51; color: #dceaf3; border: 1px solid #446176; border-radius: 5px; padding: 0; font: 18px 'Segoe UI'; }
            QPushButton#closeAircraft:hover { background: #35596f; }
            QFrame#flightMetric, QFrame#positionSummary, QFrame#historySection { background: #142635; border: 1px solid #2b465a; border-radius: 7px; }
            QWidget#aircraftDetails QLabel#metricCaption { color: #b1c8d7; font-size: 11px; }
            QWidget#aircraftDetails QLabel#metricValue { color: #ffffff; font-size: 24px; font-weight: 600; }
            QWidget#aircraftDetails QLabel#metricSource { color: #85ddd8; font-size: 11px; }
            QWidget#aircraftDetails QLabel#fieldNote { color: #a8becd; font-size: 10px; }
            QWidget#aircraftDetails QLabel#summaryCaption { color: #a8becd; font-size: 10px; }
            QWidget#aircraftDetails QLabel#summaryValue { color: #edf5fa; font-size: 12px; font-weight: 600; }
            QWidget#aircraftDetails QLabel#sectionCaption { color: #c3d6e4; font-size: 11px; font-weight: 600; }
            QFrame#broadcastSection { background: #142635; border: 1px solid #2b465a; border-radius: 5px; }
            QFrame#broadcastSection QToolButton { background: transparent; color: #d4e4ee; border: none; padding: 10px 8px; text-align: left; font: 11px 'Segoe UI'; }
            QFrame#broadcastSection QToolButton:hover { background: #213c50; }
            QFrame#broadcastSection QToolButton:focus, QPushButton#closeAircraft:focus { border: 2px solid #8be1db; }
            QWidget#aircraftDetails QLabel#broadcastName { color: #afc7d7; font-size: 11px; }
            QWidget#aircraftDetails QLabel#broadcastValue { color: #eff5f9; font-size: 12px; }
            QWidget#aircraftDetails QScrollArea { background: #10202e; border: none; }
            QWidget#aircraftDetails QScrollBar:vertical { background: #10202e; width: 10px; margin: 0; }
            QWidget#aircraftDetails QScrollBar::handle:vertical { background: #496479; border-radius: 4px; min-height: 30px; }
            QWidget#aircraftDetails QScrollBar::add-line:vertical, QWidget#aircraftDetails QScrollBar::sub-line:vertical { height: 0; }
            QWidget#aircraftDetails QScrollBar::add-page:vertical, QWidget#aircraftDetails QScrollBar::sub-page:vertical { background: transparent; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame()
        header.setObjectName("nativeDetailsHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(6)
        headline = QHBoxLayout()
        titlebox = QVBoxLayout()
        titlebox.setSpacing(1)
        titlebox.addWidget(_label("AIRCRAFT DETAILS", "detailEyebrow"))
        self.callsign = _label("Aircraft details", "detailsTitle", selectable=True)
        self.subtitle = _label("", "detailSubtitle", selectable=True)
        titlebox.addWidget(self.callsign)
        titlebox.addWidget(self.subtitle)
        headline.addLayout(titlebox, 1)
        close = QPushButton("×")
        close.setObjectName("closeAircraft")
        close.setFixedSize(30, 30)
        close.setAccessibleName("Close aircraft details")
        close.clicked.connect(self.closed.emit)
        headline.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        header_layout.addLayout(headline)
        badges = QHBoxLayout()
        badges.setSpacing(8)
        self.status = _label("Waiting for a report", "detailReception")
        self.status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.source = _label("Source not reported", "detailSource")
        badges.addWidget(self.status, 0)
        badges.addWidget(self.source, 1)
        header_layout.addLayout(badges)
        self.message = _label("", "detailMessage")
        header_layout.addWidget(self.message)
        outer.addWidget(header)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.content.setObjectName("detailsContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 12, 12, 14)
        self.content_layout.setSpacing(10)
        metric_grid = QGridLayout()
        metric_grid.setSpacing(8)
        metric_grid.setColumnStretch(0, 1)
        metric_grid.setColumnStretch(1, 1)
        self.metrics = {}
        for i, (key, title) in enumerate((("altitude", "Altitude"), ("speed", "Ground speed"),
                                          ("track", "Ground track"), ("vertical_rate", "Vertical rate"))):
            card = _MetricCard(title)
            self.metrics[key] = card
            metric_grid.addWidget(card, i // 2, i % 2)
        self.content_layout.addLayout(metric_grid)
        self.summary = QFrame()
        self.summary.setObjectName("positionSummary")
        summary_layout = QGridLayout(self.summary)
        summary_layout.setContentsMargins(12, 10, 12, 10)
        summary_layout.setHorizontalSpacing(12)
        summary_layout.setVerticalSpacing(8)
        summary_layout.setColumnStretch(0, 1)
        summary_layout.setColumnStretch(1, 1)
        self.summary_values = {}
        self.summary_notes = {}
        for i, (key, title) in enumerate((("latitude", "LATITUDE"), ("longitude", "LONGITUDE"),
                                         ("distance_nm", "FROM ANTENNA"), ("squawk", "SQUAWK"),
                                         ("on_ground", "FLIGHT STATE"), ("emergency_state", "EMERGENCY"))):
            box = QVBoxLayout()
            box.setSpacing(2)
            value = _label("—", "summaryValue", selectable=True)
            note = _label("Not received", "fieldNote")
            self.summary_values[key] = value
            self.summary_notes[key] = note
            box.addWidget(_label(title, "summaryCaption"))
            box.addWidget(value)
            box.addWidget(note)
            summary_layout.addLayout(box, i // 2, i % 2)
        self.content_layout.addWidget(self.summary)
        self.history_altitude = HistoryChart([
            ("altitude_baro", "Baro", "#6ce4d1"), ("altitude_gnss", "GNSS", "#c4a4fa"),
            ("altitude_unspecified", "Other", "#a4b9cb"),
        ], "ft")
        self.history_speed = HistoryChart([
            ("groundspeed", "Ground", "#6ce4d1"), ("indicated_airspeed", "IAS", "#edc381"),
            ("true_airspeed", "TAS", "#8fb9ff"),
        ], "kt")
        history_section = QFrame()
        history_section.setObjectName("historySection")
        history_layout = QVBoxLayout(history_section)
        history_layout.setContentsMargins(10, 10, 10, 8)
        history_layout.setSpacing(3)
        for title, chart in (("ALTITUDE · ft", self.history_altitude), ("SPEED · kt", self.history_speed)):
            history_layout.addWidget(_label(title, "sectionCaption"))
            history_layout.addWidget(chart)
        history_layout.addWidget(_label("Received samples · gaps show missing reports", "fieldNote"))
        self.content_layout.addWidget(history_section)
        self.content_layout.addWidget(_label("BROADCAST DETAILS", "sectionCaption"))
        self.field_layouts = {}
        for title, fields in GROUPS:
            self._add_field_section(title, fields)
        self.extra_section = _DetailSection("Additional received data")
        self.extra_grid = QGridLayout()
        self.extra_grid.setColumnStretch(0, 1)
        self.extra_grid.setColumnStretch(1, 1)
        self.extra_section.body_layout.addLayout(self.extra_grid)
        self.content_layout.addWidget(self.extra_section)
        footnote = _label("Received transmissions only. Model, registration, route and photos are not provided by this receiver.", "fieldNote")
        self.content_layout.addWidget(footnote)
        self.content_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll, 1)

    def _add_field_section(self, title, fields):
        section = _DetailSection(title)
        self.sections[title] = section
        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.field_layouts[title] = grid
        section.body_layout.addLayout(grid)
        for i, (key, label, kind) in enumerate(fields):
            name = _label(label, "broadcastName")
            value = _label("Not received", "broadcastValue", selectable=True)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            note = _label("", "fieldNote")
            note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(name, i * 2, 0)
            grid.addWidget(value, i * 2, 1)
            grid.addWidget(note, i * 2 + 1, 0, 1, 2)
            self.rows[key] = (value, note, kind, label)
            self.row_widgets[key] = (name, value, note)
        if title == "Signal integrity & capability":
            section.body_layout.addWidget(_label("Broadcast codes and flags; not signal strength or reception percentages.", "fieldNote"))
        self.content_layout.addWidget(section)

    def set_detail(self, detail, input_type="usb", connection_error=False):
        """Apply a detail endpoint response without rebuilding or scrolling the pane."""
        if not isinstance(detail, dict):
            detail = {}
        previous_icao = self.snapshot.get("icao")
        next_icao = detail.get("icao")
        if next_icao and next_icao != previous_icao:
            self.scroll.verticalScrollBar().setValue(0)
        self.snapshot = dict(detail)
        self.input_type = input_type
        self.connection_error = bool(connection_error)
        self.lost = False
        self.error_message = ""
        self.updated_at = time.time()
        self._render()

    def set_error(self, message="Details temporarily unavailable"):
        """Show a transient details-fetch failure while retaining the latest report."""
        self.error_message = str(message)
        self._render()

    def mark_lost(self):
        """Retain the last snapshot and mark its source aircraft as expired."""
        self.lost = True
        self.connection_error = False
        self.error_message = ""
        self._render()

    def clear(self):
        self.snapshot = {}
        self.lost = False
        self.connection_error = False
        self.error_message = ""
        self._render()

    def _field_age(self, key):
        stamp = (self.snapshot.get("field_times") or {}).get(key)
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp):
            return max(0, time.time() - stamp)
        age = (self.snapshot.get("field_ages") or {}).get(key)
        if isinstance(age, (int, float)) and not isinstance(age, bool) and math.isfinite(age):
            return max(0, age + time.time() - self.updated_at)
        return None

    def _field_note(self, key, *, provenance=True):
        row = self.snapshot
        age = self._field_age(key)
        if not _present(row.get(key)):
            if age is None:
                return "Not received"
            note = "Stale · unavailable · " + _age_text(age)
        else:
            note = _age_text(age) if age is not None else "Age not reported"
        if (self.lost or self.connection_error or self.error_message) and key != "icao":
            note = "Last known · " + note
        source = (row.get("field_sources") or {}).get(key)
        if provenance and source:
            note += " · via " + str(source)
        return note

    def _choose_field(self, keys):
        return next((key for key in keys if _present(self.snapshot.get(key))),
                    next((key for key in keys if self._field_age(key) is not None), keys[0]))

    def _render_overview(self):
        row = self.snapshot
        altitude_key = self._choose_field(("altitude_baro", "altitude_gnss", "altitude_unspecified", "altitude"))
        altitude_source = {"altitude_baro": "Barometric", "altitude_gnss": "GNSS",
                           "altitude_unspecified": "Source unspecified"}.get(altitude_key, str(row.get("altitude_source") or "Source unspecified"))
        track_key = self._choose_field(("track", "true_track"))
        rate_key = self._choose_field(("vertical_rate", "baro_vertical_rate", "gnss_vertical_rate",
                                       "inertial_vertical_rate", "vertical_rate_unspecified"))
        rate_source = {"baro_vertical_rate": "Barometric", "gnss_vertical_rate": "GNSS",
                       "inertial_vertical_rate": "Inertial", "vertical_rate_unspecified": "Source unspecified"}.get(rate_key, str(row.get("vr_source") or "Source unspecified"))
        for name, key, source, digits in (
            ("altitude", altitude_key, "ft · " + altitude_source, 0),
            ("speed", "groundspeed", "kt · Ground speed", 1),
            ("track", track_key, "° · " + ("True track" if track_key == "true_track" else "Ground track"), 1),
            ("vertical_rate", rate_key, "ft/min · " + rate_source, 0),
        ):
            value = _fmt_number(row.get(key), digits=digits) if _present(row.get(key)) else "—"
            self.metrics[name].set_value(key, value, source, self._field_note(key, provenance=False))
        for key, value_label in self.summary_values.items():
            kind = self.rows[key][2]
            value = _display(key, row.get(key), kind, row) if _present(row.get(key)) else "—"
            value_label.setText(value)
            self.summary_notes[key].setText(self._field_note(key, provenance=False))

    def _render(self):
        row = self.snapshot
        icao = str(row.get("icao", ""))
        callsign = row.get("callsign")
        self.callsign.setText(str(callsign).strip() if _present(callsign) else icao or "Aircraft details")
        self.subtitle.setText("ICAO24  " + icao if icao else "Callsign not received")
        source = row.get("data_source") or "Source not reported"
        if self.input_type == "iq-file":
            source = "Recorded " + source
        elif self.input_type == "usb" and row.get("data_source"):
            source = "Terrestrial " + source
        else:
            transport = {"beast-tcp": "Beast TCP", "beast-serial": "Beast serial", "avr-tcp": "AVR TCP",
                         "avr-serial": "AVR serial", "sbs-tcp": "SBS TCP"}.get(self.input_type)
            if transport:
                source += " via " + transport
        self.source.setText(source)
        seen = None
        if isinstance(row.get("last_seen"), (int, float)) and math.isfinite(row["last_seen"]):
            seen = max(0, time.time() - row["last_seen"])
        elif isinstance(row.get("seen"), (int, float)) and math.isfinite(row["seen"]):
            seen = max(0, row["seen"] + time.time() - self.updated_at)
        state = "receiving"
        if self.connection_error:
            state = "error"
            self.status.setText("Connection interrupted")
            self.message.setText("Receiver connection interrupted. Showing last known reports.")
        elif self.lost:
            state = "quiet"
            self.status.setText("Signal lost")
            self.message.setText("Reception stopped. Showing this aircraft’s last known reports.")
        elif not row:
            state = "waiting"
            self.status.setText("Waiting for a report")
            self.message.setText("Select an aircraft on the map or in the list.")
        else:
            quiet = seen is not None and seen > 15
            state = "quiet" if quiet or self.error_message else "receiving"
            self.status.setText("Details unavailable" if self.error_message else "Recorded reception" if self.input_type == "iq-file" else "Reception quiet" if quiet else "Receiving")
            self.message.setText(self.error_message or ("Last received " + _age_text(seen) if seen is not None else "Reception time not reported"))
        if self.status.property("state") != state:
            self.status.setProperty("state", state)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
        for key, (value_label, note_label, kind, _label_text) in self.rows.items():
            value = row.get(key)
            age = self._field_age(key)
            value_label.setText("Stale · unavailable" if not _present(value) and age is not None else _display(key, value, kind, row))
            note = self._field_note(key)
            if key == "vertical_rate" and row.get("vr_source"):
                note += " · " + str(row["vr_source"])
            note_label.setText(note.replace("Stale · unavailable · ", "") if age is not None or _present(value) else "")
            provenance = (row.get("field_sources") or {}).get(key)
            value_label.setToolTip("Received via " + str(provenance) if provenance else "")
            for widget in self.row_widgets[key]:
                widget.setVisible(_present(value) or age is not None)
        for title, fields in GROUPS:
            count = sum(_present(row.get(key)) or self._field_age(key) is not None for key, _label_text, _kind in fields)
            section = self.sections[title]
            section.empty.setVisible(count == 0)
            section.toggle.setText(title.replace("&", "&&") + (f"  ·  {count}" if count else "  ·  none"))
            section.toggle.setAccessibleDescription(("Expanded" if section.toggle.isChecked() else "Collapsed") + f"; {count} received fields")
        self._render_overview()
        self._update_extra(row)
        history = row.get("history") if isinstance(row.get("history"), list) else []
        self.history_altitude.set_samples(history)
        self.history_speed.set_samples(history)

    def _update_extra(self, row):
        known = set(self.rows) | IGNORED_EXTRA
        reported_keys = set(row) | set(row.get("field_times") or {}) | set(row.get("field_ages") or {})
        scalars = [(key, row.get(key)) for key in reported_keys
                   if key not in known and not isinstance(row.get(key), (dict, list, tuple))
                   and (_present(row.get(key)) or self._field_age(key) is not None)]
        keys = {key for key, _ in scalars}
        for key in list(set(self.extra_rows) - keys):
            for widget in self.extra_rows.pop(key):
                self.extra_grid.removeWidget(widget)
                widget.deleteLater()
            note = self.extra_notes.pop(key)
            self.extra_grid.removeWidget(note)
            note.deleteLater()
        ordered = sorted(scalars)
        for key, val in ordered:
            value_text = _display(key, val, "boolean" if isinstance(val, bool) else "text", row) if _present(val) else "Stale · unavailable"
            if key in self.extra_rows:
                self.extra_rows[key][1].setText(value_text)
            else:
                label = _label(key.replace("_", " ").capitalize(), "broadcastName")
                value = _label(value_text, "broadcastValue", selectable=True)
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.extra_rows[key] = (label, value)
                self.extra_notes[key] = _label("", "fieldNote")
                self.extra_notes[key].setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.extra_notes[key].setText(self._field_note(key).replace("Stale · unavailable · ", ""))
        while self.extra_grid.count():
            self.extra_grid.takeAt(0)
        for index, (key, _val) in enumerate(ordered):
            label, value = self.extra_rows[key]
            self.extra_grid.addWidget(label, index * 2, 0)
            self.extra_grid.addWidget(value, index * 2, 1)
            self.extra_grid.addWidget(self.extra_notes[key], index * 2 + 1, 0, 1, 2)
        self.extra_section.setVisible(bool(ordered))
        self.extra_section.empty.setVisible(not ordered)
        self.extra_section.toggle.setText(f"Additional received data  ·  {len(ordered)}")

    def _refresh_ages(self):
        if self.snapshot:
            # Text updates preserve selection, disclosure state, focus and scroll.
            self._render()
