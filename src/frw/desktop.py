"""Native Qt desktop. No web engine, browser process, or HTTP transport."""
from __future__ import annotations
import csv
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QSettings
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QFont, QBrush, QShortcut, QKeySequence, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QLineEdit, QFrame, QSizePolicy,
    QAbstractItemView, QHeaderView, QDockWidget, QTabWidget, QFileDialog, QToolBar, QComboBox, QDialog, QSystemTrayIcon)
from .control import Controller
from .native_map import NativeMap, NativeAircraftDetails
from .native_pages import NativePages
from .features import FEATURE_DEFAULT, matches
from .display import (DISPLAY_DEFAULT, bearing_suffix, display_bearing, distance_bearing, emergency_text, format_altitude,
    format_distance, format_seen, format_speed, format_track, format_vertical_rate,
    is_emergency, quick_match, sort_rows)
from .native_features import FeatureModel, FilterDialog, StatisticsPage, AlertsPage, FeaturePreferences, describe
from .native_support import HelpDialog, PracticeDialog, AboutDialog
from .support import support_summary
from . import PROJECT_URL, __version__


class OperationError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = status


class Dispatcher(QObject):
    completed = Signal(object, object, object)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='desktop')
        self.mutations = ThreadPoolExecutor(max_workers=1, thread_name_prefix='operations')
        self.closed = False
        self.completed.connect(self._deliver)

    def invoke(self, path, data=None, callback=None):
        if self.closed:
            return
        def run():
            try:
                result = self.controller.execute(path, 'GET' if data is None else 'POST', data)
                if result.status >= 400:
                    raise OperationError(str(result.data.get('error', 'Operation failed.')), result.status)
                value, error = result.data, None
            except Exception as exc:
                value, error = None, exc
            self.completed.emit(callback, value, error)
        (self.pool if data is None else self.mutations).submit(run)

    def _deliver(self, callback, value, error):
        if callback is not None:
            callback(value, error)

    def finish(self):
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.mutations.shutdown(wait=False, cancel_futures=True)


def _control_icon(kind):
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if kind == 'start':
        painter.setBrush(QColor('#2ee6a8')); painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([QPointF(4, 2), QPointF(16, 9), QPointF(4, 16)]))
    else:
        painter.setBrush(QColor('#ff7a6e')); painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(3, 3, 12, 12, 2, 2)
    painter.end()
    return QIcon(pixmap)


class FlightRadarWindow(QMainWindow):
    def __init__(self, receiver, instance=None):
        super().__init__()
        self.receiver, self.instance = receiver, instance
        self.config = receiver.settings.public()
        self.selected, self.rows, self.last_status = '', [], {}
        self._poll_pending = self._detail_pending = self._action_pending = False
        self._details_width_set = False
        self._closing = self._can_close = False
        self._poll_notice = None
        self.features = dict(FEATURE_DEFAULT)
        self._search_text = ''
        self._sort_key = None
        self._sort_reverse = False
        self._ui_settings = QSettings('FlightRadar24 Community', 'FlightRadar24')
        self._seen_alerts = set()
        self._notification_events = {}
        self.tray = None
        self.controller = Controller(receiver, lambda: None)
        self.dispatcher = Dispatcher(self.controller, self)
        self.feature_model = FeatureModel(self.dispatcher.invoke,self) if hasattr(receiver,'activity') else None
        self.setWindowTitle('FlightRadar24')
        self.resize(1440, 960)
        self.setMinimumSize(920, 650)
        self._build()
        if self.feature_model:
            self.feature_model.changed.connect(self._features_changed)
            self.feature_model.notice.connect(self.notice)
            self.feature_model.localTest.connect(self._test_notification)
            self.feature_model.load()
            self._build_notifications()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.activation_timer = QTimer(self)
        self.activation_timer.setInterval(350)
        self.activation_timer.timeout.connect(self._activation)
        self.activation_timer.start()
        QTimer.singleShot(0, self.poll)
        if self.config.get('autostart_receiver'):
            QTimer.singleShot(100, lambda: self.operation('start'))

    def _build(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        # One top line: receiver start/stop (icon + name), health, then the page tabs.
        corner = QWidget()
        corner_row = QHBoxLayout(corner)
        corner_row.setContentsMargins(6, 2, 6, 2)
        corner_row.setSpacing(6)
        self.start_button = QPushButton(_control_icon('start'), 'Start receiver')
        self.start_button.setObjectName('primaryButton')
        self.stop_button = QPushButton(_control_icon('stop'), 'Stop receiver')
        self.start_button.clicked.connect(lambda: self.operation('start'))
        self.stop_button.clicked.connect(lambda: self.operation('stop'))
        corner_row.addWidget(self.start_button)
        corner_row.addWidget(self.stop_button)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopLeftCorner)
        # Reception metrics, sharing status and receiver status live at the
        # very top, in the menu/tool bar at the right — immediately before
        # the window minimise/maximise/close buttons in the title bar above.
        self.top_status = QWidget()
        self.top_status.setObjectName('topStatus')
        top_row = QHBoxLayout(self.top_status)
        top_row.setContentsMargins(10, 1, 10, 1)
        top_row.setSpacing(14)
        self.metrics = QLabel('No aircraft received yet')
        self.metrics.setObjectName('metrics')
        self.metrics.setTextFormat(Qt.TextFormat.PlainText)
        self.metrics.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.metrics.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.metrics.setMinimumWidth(0)
        top_row.addWidget(self.metrics, 3)
        self.feed_status = QLabel('Flightradar24 upload: stopped')
        self.feed_status.setObjectName('feedStatus')
        self.feed_status.setTextFormat(Qt.TextFormat.PlainText)
        self.feed_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.feed_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.feed_status.setMinimumWidth(0)
        top_row.addWidget(self.feed_status, 6)
        self.health = QLabel('Waiting for receiver status…')
        self.health.setTextFormat(Qt.TextFormat.PlainText)
        self.health.setObjectName('receiverStatus')
        self.health.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.health.setMinimumWidth(150)
        top_row.addWidget(self.health, 0)
        self.menuBar().setCornerWidget(self.top_status, Qt.Corner.TopRightCorner)
        # Airspace: the map fills the page; everything else overlays it.
        dashboard = QWidget()
        layout = QVBoxLayout(dashboard)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.map = NativeMap(cache_dir=self.receiver.directory / 'map-cache')
        self.map.aircraftSelected.connect(self.select_aircraft)
        layout.addWidget(self.map, 1)
        # Floating aircraft drawer along the bottom of the map, spanning its
        # length; two compact control rows leave the rest for the table.
        self.list_panel = QFrame()
        self.list_panel.setObjectName('overlayPanel')
        self.list_panel._overlay_height = 280
        panel_layout = QVBoxLayout(self.list_panel)
        panel_layout.setContentsMargins(8, 6, 8, 8)
        panel_layout.setSpacing(6)
        header = QHBoxLayout()
        self._list_title = QLabel('Aircraft')
        self._list_title.setObjectName('panelTitle')
        self._list_title.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        header.addWidget(self._list_title)
        find_label = QLabel('Find:')
        find_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        header.addWidget(find_label)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText('Find callsign or ICAO…  (Ctrl+F)')
        self.search_box.setAccessibleName('Find aircraft by callsign or ICAO address')
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMaximumWidth(520)
        self.search_box.textChanged.connect(self._search_changed)
        header.addWidget(self.search_box, 1)
        header.addStretch(1)
        self._collapse_button = QPushButton('▾')
        self._collapse_button.setAccessibleName('Collapse aircraft list')
        self._collapse_button.setToolTip('Collapse the aircraft list to give the map more room')
        self._collapse_button.setFixedWidth(30)
        self._collapse_button.clicked.connect(self._toggle_list_panel)
        header.addWidget(self._collapse_button)
        panel_layout.addLayout(header)
        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(lambda: (self.tabs.setCurrentIndex(0), self._set_list_collapsed(False), self.search_box.setFocus(), self.search_box.selectAll()))
        self._list_body = QWidget()
        body = QVBoxLayout(self._list_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        if self.feature_model:
            controls = QHBoxLayout()
            controls.addWidget(self._feature_button('Filters…', self.edit_filters))
            self.preset_combo = QComboBox()
            self.preset_combo.setAccessibleName('Airspace filter preset')
            self.preset_combo.setMinimumContentsLength(14)
            self.preset_combo.setMaximumWidth(340)
            self.preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.preset_combo.addItem('All aircraft', None)
            self.preset_combo.activated.connect(self.choose_preset)
            controls.addWidget(self.preset_combo, 1)
            controls.addWidget(self._feature_button('Clear', lambda: self.feature_model.save({'filter_enabled': False})))
            controls.addWidget(self._feature_button('Alert for selected aircraft…', lambda: self.alerts_page.add(self.selected) if self.selected else self.notice('Select an aircraft first.', False)))
            self.filter_status = QLabel('All received aircraft')
            self.filter_status.setTextFormat(Qt.TextFormat.PlainText)
            self.filter_status.setWordWrap(True)
            controls.addWidget(self.filter_status, 2)
            body.addLayout(controls)
        self.table = QTableWidget(0, 11)
        self._table_columns = ['callsign', 'icao', 'altitude', 'groundspeed', 'track',
                               'vertical_rate', 'squawk', 'distance', 'bearing', 'seen', 'position']
        self._update_table_headers()
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().sectionClicked.connect(self._sort_by_column)
        self.table.setToolTip('Select a row for live broadcast details. Double-click centres the map. Click a column heading to sort; aircraft missing that value stay last.')
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header_view.setStretchLastSection(True)
        for column, width in enumerate((96, 72, 132, 80, 58, 112, 68, 96, 70, 58, 92)):
            self.table.setColumnWidth(column, width)
        self.table.itemSelectionChanged.connect(self._table_selected)
        self.table.itemDoubleClicked.connect(lambda item: self.map.focus_aircraft(self.selected))
        body.addWidget(self.table, 1)
        panel_layout.addWidget(self._list_body, 1)
        self._list_collapsed = False
        self.map.add_overlay(self.list_panel, 'bottom-full')
        # Transient notices appear as a small chip over the top of the map;
        # persistent reception/sharing status is in the top bar (above).
        self.message = QLabel('')
        self.message.setObjectName('mapMessage')
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map.add_overlay(self.message, 'top-message')
        self.message.hide()
        self.tabs.addTab(dashboard, 'Airspace')
        self.pages = NativePages(self.dispatcher.invoke)
        self.pages.update_settings(self.config)
        self.pages.notice.connect(self.notice)
        self.pages.settingsChanged.connect(self._settings_changed)
        self.tabs.addTab(self.pages, 'Receiver & settings')
        if self.feature_model:
            self.statistics_page=StatisticsPage(self.feature_model)
            self.alerts_page=AlertsPage(self.feature_model,lambda:self.show_page('features'))
            self.tabs.insertTab(1,self.statistics_page,'Statistics')
            self.tabs.insertTab(2,self.alerts_page,'Alerts')
            self.feature_preferences=FeaturePreferences(self.feature_model)
            self.pages.pages['features']=self.feature_preferences
            self.pages.tabs.addTab(self.feature_preferences,'Storage & alerts')
        self.details = NativeAircraftDetails()
        self.details_panel = QFrame()
        self.details_panel.setObjectName('overlayPanel')
        self.details_panel.setFixedWidth(392)
        self.details_panel._overlay_height = 540
        details_layout = QVBoxLayout(self.details_panel)
        details_layout.setContentsMargins(4, 4, 4, 4)
        details_layout.addWidget(self.details)
        self.map.add_overlay(self.details_panel, 'details-left')
        self.details_panel.hide()
        self._restore_ui_state()
        self.details.closed.connect(self.clear_selection)
        menu = self.menuBar().addMenu('&File')
        for title, callback in [('Export aircraft CSV…', self.export_aircraft), ('Logs and diagnostics', lambda: self.show_page('logs')), ('Exit', self.close)]:
            action = QAction(title, self)
            action.triggered.connect(callback)
            menu.addAction(action)
        menu = self.menuBar().addMenu('&View')
        for title, name in [('Receiver', 'receiver'), ('Data settings', 'settings'), ('Flightradar24 sharing', 'sharing')]:
            action = QAction(title, self)
            action.triggered.connect(lambda checked=False, page=name: self.show_page(page))
            menu.addAction(action)
        menu.addSeparator()
        self._table_action = QAction('Aircraft list', self, checkable=True, checked=True)
        self._table_action.triggered.connect(lambda checked: self._set_list_collapsed(not checked))
        menu.addAction(self._table_action)
        follow_action = QAction('Follow selected aircraft on map', self)
        follow_action.triggered.connect(lambda: self.show_page('features'))
        follow_action.setToolTip('Display preference: Receiver & settings → Storage & alerts → Display & units')
        menu.addAction(follow_action)
        self.map.attribution.setObjectName('bottomAttribution')
        self.statusBar().addPermanentWidget(self.map.attribution)
        self.map.refresh_overlays()
        self._layout_top_status()
        self.statusBar().showMessage('Native desktop · received ADS-B / Mode S data')
        support_menu = self.menuBar().addMenu('&Support')
        for title, callback, shortcut in [
            ('Search help…', self.open_help, 'F1'),
            ('Practice with dummy data…', lambda: PracticeDialog(self).exec(), ''),
            ('Logs and diagnostics', lambda: self.show_page('logs'), ''),
            ('Copy support report template', lambda: self.copy_support(support_summary()), ''),
            ('Copy GitHub project address', lambda: self.copy_support(PROJECT_URL), ''),
            ('Copy issue tracker address', lambda: self.copy_support(PROJECT_URL + '/issues'), ''),
            ('About FlightRadar24…', lambda: AboutDialog(self).exec(), '')]:
            action = QAction(title, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(callback)
            support_menu.addAction(action)

    def open_help(self):
        dialog = HelpDialog(self)
        dialog.exec()

    def copy_support(self, text):
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage('Copied to clipboard. Nothing submitted.', 7000)

    @staticmethod
    def _feature_button(title,callback):
        result=QPushButton(title)
        result.clicked.connect(lambda checked=False:callback())
        return result

    def _layout_top_status(self):
        if not hasattr(self, 'top_status'):
            return
        # QMenuBar sizes its corner widget from its size hint (not a forced
        # width), so cap each label instead and let the bar right-align us.
        available = max(220, self.width() - 190)
        self.top_status.setMinimumWidth(0)
        self.top_status.setMaximumWidth(16777215)
        show_feed = available >= 1000
        show_metrics = available >= 560
        self.feed_status.setVisible(show_feed)
        self.metrics.setVisible(show_metrics)
        self.health.setMaximumWidth(240)
        if show_feed:
            self.metrics.setMaximumWidth(max(120, int(available * 0.27)))
            self.feed_status.setMaximumWidth(max(160, available - self.metrics.maximumWidth() - 270))
        elif show_metrics:
            self.metrics.setMaximumWidth(max(160, available - 270))
        self.top_status.adjustSize()
        self.menuBar().updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_top_status()

    def _set_list_collapsed(self, collapsed):
        self._list_collapsed = bool(collapsed)
        self._list_body.setVisible(not collapsed)
        self.list_panel._overlay_height = 42 if collapsed else 280
        self._collapse_button.setText('▴' if collapsed else '▾')
        self._collapse_button.setAccessibleName('Expand aircraft list' if collapsed else 'Collapse aircraft list')
        if hasattr(self, '_table_action'):
            self._table_action.setChecked(not collapsed)
        self.map.refresh_overlays()

    def _toggle_list_panel(self):
        self._set_list_collapsed(not self._list_collapsed)

    def _display(self):
        return {**DISPLAY_DEFAULT, **(self.features.get('display') or {})}

    def _station(self):
        lat, lon = self.config.get('latitude'), self.config.get('longitude')
        return (lat, lon) if lat is not None and lon is not None else None

    def _search_changed(self, text):
        self._search_text = text or ''
        self._update_positions()
        self._update_table()

    def _sort_by_column(self, column):
        key = self._table_columns[column]
        if key == 'position':
            return
        if self._sort_key == key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key, self._sort_reverse = key, False
        self.table.horizontalHeader().setSortIndicator(
            column, Qt.SortOrder.DescendingOrder if self._sort_reverse else Qt.SortOrder.AscendingOrder)
        self._update_table()

    def _update_table_headers(self):
        metric = self._display()['units'] == 'metric'
        self.table.setHorizontalHeaderLabels([
            'Callsign', 'ICAO', 'Altitude (m)' if metric else 'Altitude (ft · FL)',
            'Speed (km/h)' if metric else 'Speed (kt)', 'Track',
            'V/S (m/s)' if metric else 'V/S (ft/min)', 'Squawk',
            'Distance (km)' if metric else 'Distance (NM)', 'Bearing ' + bearing_suffix(self._display()), 'Seen', 'Position'])

    def _restore_ui_state(self):
        try:
            geometry = self._ui_settings.value('window/geometry')
            if geometry:
                self.restoreGeometry(geometry)
            state = self._ui_settings.value('window/state')
            if state:
                self.restoreState(state)
            if str(self._ui_settings.value('airspace/listCollapsed', 'false')).lower() == 'true':
                self._set_list_collapsed(True)
        except Exception:
            pass  # a corrupt UI preference must never block startup

    def _save_ui_state(self):
        try:
            self._ui_settings.setValue('window/geometry', self.saveGeometry())
            self._ui_settings.setValue('window/state', self.saveState())
            self._ui_settings.setValue('airspace/listCollapsed', bool(self._list_collapsed))
            self._ui_settings.sync()
        except Exception:
            pass

    def _features_changed(self,config):
        self.features=config
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear();self.preset_combo.addItem('All aircraft',None)
        if config['filter_enabled']:
            self.preset_combo.addItem('Current custom filter',config['filter'])
            self.preset_combo.setCurrentIndex(1)
        for preset in config['presets']:
            self.preset_combo.addItem(preset['name'],preset['filter'])
        self.preset_combo.blockSignals(False)
        self.map.set_display(self._display())
        self._update_table_headers()
        if self.selected and self.selected not in {r['icao'] for r in self.visible_rows()}:
            self.clear_selection()
        self._update_positions();self._update_table()

    def edit_filters(self):
        FilterDialog(self.feature_model,self).exec()

    def choose_preset(self,index):
        criteria=self.preset_combo.itemData(index)
        self.feature_model.save({'filter_enabled':criteria is not None,**({'filter':criteria} if criteria is not None else {})})

    def visible_rows(self):
        rows = self.rows
        if self.features.get('filter_enabled'):
            station=(self.config.get('latitude'),self.config.get('longitude'))
            rows = [r for r in rows if matches(r,self.features['filter'],station)]
        if self._search_text.strip():
            rows = [r for r in rows if quick_match(r, self._search_text)]
        return sort_rows(rows, self._sort_key, self._sort_reverse, self._station())

    def _build_notifications(self):
        pixmap=QPixmap(64,64);pixmap.fill(QColor('#18374e'))
        painter=QPainter(pixmap);painter.setPen(QColor('#79dbc8'));painter.setFont(QFont('Segoe UI Symbol',36));painter.drawText(pixmap.rect(),Qt.AlignmentFlag.AlignCenter,'✈');painter.end()
        self.setWindowIcon(QIcon(pixmap))
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray=QSystemTrayIcon(QIcon(pixmap),self)
            self.tray.setToolTip('FlightRadar24 · aircraft alerts')
            self.tray.messageClicked.connect(self._open_alerts)
            self.tray.activated.connect(lambda reason:self._open_alerts() if reason==QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.show()

    def _open_alerts(self):
        if not self._closing:
            self.showNormal();self.raise_();self.activateWindow();self.tabs.setCurrentWidget(self.alerts_page)

    def _test_notification(self):
        self._notify({'id':'local-test','title':'FlightRadar24 · TEST notification',
            'body':'Local test only. Aircraft alerts can include received identity, position, altitude, speed, squawk and time.',
            'channels':['sound','desktop'],'quiet':False})
        self.notice('Local test requested. Sound uses the system chime; desktop visibility depends on OS notification settings.',False)

    def _notify(self,event):
        if event.get('quiet'):
            return
        if 'sound' in event['channels']:
            QApplication.beep()
        if 'desktop' in event['channels']:
            if self.tray and QSystemTrayIcon.supportsMessages():
                self.tray.showMessage(event['title'],event['body'],QSystemTrayIcon.MessageIcon.Information,10000)
            else:
                self.statusBar().showMessage(event['title']+' · '+event['body'].replace('\n',' · '),15000)

    def _process_alerts(self,status):
        if not self.feature_model:
            return
        self.alerts_page.update_status(status)
        for event in (status.get('activity') or {}).get('events',[]):
            if event['id'] not in self._seen_alerts:
                self._seen_alerts.add(event['id'])
                self._notify(event)
        if len(self._seen_alerts)>1000:
            self._seen_alerts={event['id'] for event in (status.get('activity') or {}).get('events',[])}

    def show_page(self, name):
        self.tabs.setCurrentWidget(self.pages)
        self.pages.set_page(name)

    def _activation(self):
        if self.instance and self.instance.activation_requested():
            if self.isMinimized():
                self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()

    def notice(self, text, error=False):
        self._poll_notice = None
        self.statusBar().showMessage(str(text), 15000)
        self.message.setText(str(text))
        self.message.setStyleSheet('color: #ffb2a8;' if error else 'color: #96d8b3;')
        self.message.setVisible(bool(text))
        if hasattr(self, 'map'):
            self.map.refresh_overlays()

    def _status_notice(self, text):
        self.notice(text, True)
        self._poll_notice = str(text)

    def _clear_status_notice(self):
        if self._poll_notice is not None and self.message.text() == self._poll_notice:
            self.message.hide()
            self.message.clear()
            if self.statusBar().currentMessage() == self._poll_notice:
                self.statusBar().clearMessage()
            if hasattr(self, 'map'):
                self.map.refresh_overlays()
        self._poll_notice = None

    def _settings_changed(self, config):
        self.config = config
        self._update_positions()

    def operation(self, name):
        if self._action_pending or self._closing:
            return
        self._action_pending = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.notice('Starting receiver…' if name == 'start' else 'Stopping receiver…')
        def done(value, error=None):
            self._action_pending = False
            if self._closing:
                return
            self.notice(str(error) if error else 'Receiver ' + ('started.' if name == 'start' else 'stopped.'), bool(error))
            self.poll()
        self.dispatcher.invoke('/api/' + name, {}, done)

    def poll(self):
        if self._closing or self._poll_pending:
            return
        self._poll_pending = True
        def done(status, error=None):
            self._poll_pending = False
            if self._closing:
                return
            if error:
                self._status_notice('Receiver status unavailable: ' + str(error))
                self.health.setText('Receiver status unavailable')
                self.details.set_error(str(error))
                return
            self._clear_status_notice()
            self.last_status = status
            self.rows = status.get('aircraft', [])
            state = status.get('state', 'stopped')
            playback = status.get('input', self.config.get('receiver')) == 'iq-file'
            self.health.setText('Receiver: ' + state + (' · Recorded playback' if playback else ''))
            running = state in ('starting', 'receiving', 'reconnecting')
            self.start_button.setEnabled(not running and not self._action_pending)
            self.stop_button.setEnabled(running and not self._action_pending)
            positioned = sum(r.get('latitude') is not None and r.get('longitude') is not None for r in self.rows)
            station = self._station()
            ranges = [distance_bearing(station, r)[0] for r in self.rows if distance_bearing(station, r)] if station else []
            emergencies = sum(1 for r in self.rows if is_emergency(r))
            extra = (f"  ·  Max range {format_distance(max(ranges), self._display()['units'])}" if ranges else '') + (f"  ·  ⚠ {emergencies} emergency squawk" if emergencies else '')
            self.metrics.setToolTip(f"{len(self.rows)} aircraft  ·  {positioned} with positions  ·  {status.get('messages_per_second', 0)} messages/s  ·  {status.get('messages', 0):,} messages" + extra)
            self.metrics.setText(f"{len(self.rows)} aircraft  ·  {positioned} with positions  ·  {status.get('messages_per_second', 0)} messages/s  ·  {status.get('messages', 0):,} messages" + extra)
            feeder = status.get('feeder') or {}
            monitor = feeder.get('monitor') or {}
            mlat = (monitor.get('mlat_status') or monitor.get('mlat-status') or
                    {'YES': 'Active', 'NO': 'Not active', 'NOT-PERMITTED': 'Not permitted by service'}.get(monitor.get('mlat-ok')) or
                    monitor.get('mlat_problem') or ('Awaiting service status' if self.config.get('mlat') else 'Off'))
            upload = ('Disabled (recorded playback)' if playback else 'Disabled' if not self.config.get('feed_enabled') else
                      monitor.get('feed_status') or monitor.get('feed-status') or 'Not confirmed')
            transmission = 'No transmission reported'
            try:
                sent_at = float(monitor.get('feed_last_ac_sent_time', 0))
                sent = float(monitor.get('feed_last_ac_sent_num', -1))
                if math.isfinite(sent_at) and sent_at > 0 and math.isfinite(sent) and sent >= 0:
                    age = max(0, int(time.time() - sent_at))
                    transmission = f'{int(sent)} aircraft in last report · {age // 60}m {age % 60}s ago'
            except (TypeError, ValueError, OverflowError):
                pass
            self.feed_status.setText(f"Flightradar24: {feeder.get('state', 'stopped')}  ·  Upload: {upload}  ·  {transmission}  ·  MLAT: {mlat}")
            self.feed_status.setToolTip(self.feed_status.text())
            if status.get('error') or feeder.get('error'):
                self._status_notice(status.get('error') or feeder.get('error'))
            self.pages.update_status(status)
            self._process_alerts(status)
            self._update_positions()
            self._update_table()
            self._poll_detail()
        self.dispatcher.invoke('/api/status', None, done)

    def _update_positions(self):
        rows=self.visible_rows()
        self.map.update_aircraft(rows, self.config, self.selected)
        if self._display()['follow_selected'] and self.selected:
            self.map.focus_aircraft(self.selected)
        if self.feature_model:
            self.filter_status.setText(f'{len(rows)} / {len(self.rows)} aircraft shown'+(' · '+describe(self.features['filter']) if self.features['filter_enabled'] else ' · All aircraft')+(' · Find: '+self._search_text.strip() if self._search_text.strip() else ''))

    def _update_table(self):
        self.table.blockSignals(True)
        rows=self.visible_rows()
        display = self._display()
        units = display['units']
        station = self._station()
        playback = self.last_status.get('input', self.config.get('receiver')) == 'iq-file'
        self.table.setRowCount(len(rows))
        selected_row = None
        emergency_bg = QBrush(QColor('#5a1620'))
        emergency_fg = QBrush(QColor('#ffd7db'))
        for index, row in enumerate(rows):
            result = distance_bearing(station, row)
            emergency = is_emergency(row)
            if row.get('on_ground') is True:
                position = 'On ground'
            elif row.get('latitude') is not None and row.get('longitude') is not None:
                position = 'Recorded' if playback else 'Live'
            else:
                position = 'Not received'
            values = [
                str(row.get('callsign') or '').strip() or '—',
                str(row.get('icao') or ''),
                format_altitude(row.get('altitude'), units),
                format_speed(row.get('groundspeed'), units),
                format_track(row.get('track')),
                format_vertical_rate(row.get('vertical_rate'), units),
                str(row.get('squawk') or '—'),
                format_distance(result[0], units) if result else '—',
                f'{display_bearing(result[1], display):03d}°' if result else '—',
                format_seen(row.get('seen')),
                position,
            ]
            for column, text in enumerate(values):
                item = self.table.item(index, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(index, column, item)
                item.setText(text)
                item.setData(Qt.ItemDataRole.UserRole, row['icao'])
                if emergency:
                    item.setBackground(emergency_bg)
                    item.setForeground(emergency_fg)
                    item.setToolTip(emergency_text(row) + '. Shown as broadcast; not independently verified.')
                else:
                    item.setBackground(QBrush())
                    item.setForeground(QBrush())
                    item.setToolTip('')
            if result:
                self.table.item(index, 7).setToolTip('Great-circle distance from your antenna position.')
                self.table.item(index, 8).setToolTip('Initial bearing from your antenna, in your chosen north reference (Display & units).')
            if row['icao'] == self.selected:
                selected_row = index
        if selected_row is not None:
            self.table.selectRow(selected_row)
        else:
            self.table.clearSelection()
        self.table.blockSignals(False)

    def _table_selected(self):
        items = self.table.selectedItems()
        if items:
            self.select_aircraft(items[0].data(Qt.ItemDataRole.UserRole))

    def select_aircraft(self, icao):
        if self._closing:
            return
        if icao != self.selected:
            self.details.clear()
        self.selected = icao
        self.details_panel.show()
        self.map.refresh_overlays()
        self._update_positions()
        self._update_table()
        self._poll_detail()

    def _poll_detail(self):
        if not self.selected or self._detail_pending or self._closing:
            return
        selected = self.selected
        self._detail_pending = True
        def done(detail, error=None):
            self._detail_pending = False
            if self._closing:
                return
            if selected != self.selected:
                self._poll_detail()
                return
            if error:
                if getattr(error, 'status', None) == 404:
                    self.details.mark_lost()
                else:
                    self.details.set_error(str(error))
            else:
                self.details.set_detail(detail, self.config.get('receiver', 'usb'))
        self.dispatcher.invoke('/api/aircraft/' + selected, None, done)

    def clear_selection(self):
        self.selected = ''
        self.details.clear()
        self.details_panel.hide()
        self.map.refresh_overlays()
        self._update_positions()
        self._update_table()

    def export_aircraft(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export current aircraft', 'aircraft.csv', 'CSV files (*.csv)')
        if not path:
            return
        fields = ['icao', 'callsign', 'latitude', 'longitude', 'altitude', 'groundspeed', 'track', 'vertical_rate', 'squawk', 'seen']
        try:
            with Path(path).open('w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(handle, fields, extrasaction='ignore')
                writer.writeheader()
                for row in self.visible_rows():
                    writer.writerow({key: "'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value
                                     for key, value in row.items() if key in fields})
            self.notice('Aircraft exported.')
        except OSError as error:
            self.notice(str(error), True)

    def closeEvent(self, event):
        if self._can_close:
            event.accept()
            return
        event.ignore()
        if self._closing:
            return
        self._save_ui_state()
        self._closing = True
        self.timer.stop()
        self.activation_timer.stop()
        self.pages.pause_polling()
        if self.feature_model:
            self.statistics_page.closed=True
            self.statistics_page.timer.stop()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.tabs.setEnabled(False)
        self.notice('Closing FlightRadar24 · stopping receiver and sharing…')
        def done(value, error=None):
            if error:
                self._closing = False
                self.tabs.setEnabled(True)
                self.pages.resume_polling()
                if self.feature_model:
                    self.statistics_page.closed=False
                    self.statistics_page.timer.start()
                self.notice('Could not stop all receiver components: ' + str(error) + '. Close again to retry.', True)
                self.timer.start()
                self.activation_timer.start()
                return
            self.pages.stop_polling()
            self.dispatcher.finish()
            self.receiver.log.close()
            if self.tray:
                self.tray.hide()
            self._can_close = True
            self.close()
        self.dispatcher.invoke('/api/shutdown', {}, done)


def apply_desktop_style(app):
    app.setApplicationName('FlightRadar24')
    app.setApplicationVersion(__version__)
    app.setOrganizationName('FlightRadar24 Community')
    app.setStyle('Fusion')
    app.setStyleSheet('''
        QMainWindow, QWidget { background: #0f1a26; color: #e8edf4; font-size: 10pt; selection-background-color: #2f6db3; }
        QToolBar, QMenuBar, QStatusBar { background: #162635; padding: 5px; border: none; }
        QMenuBar::item:selected, QMenu::item:selected { background: #2f6db3; }
        QMenu { background: #162635; border: 1px solid #45647a; padding: 4px; }
        QPushButton { background: #21394f; border: 1px solid #45647a; border-radius: 6px; padding: 6px 12px; }
        QPushButton:hover { background: #2c506e; } QPushButton:pressed { background: #2f6db3; }
        QPushButton:disabled { color: #718293; background: #162635; }
        QPushButton#primaryButton { background: #1d6f5f; border-color: #38c8ad; font-weight: 600; }
        QPushButton#primaryButton:hover { background: #238a78; }
        QPushButton:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 2px solid #79dbc8; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit { background: #0b141f; border: 1px solid #45647a; border-radius: 5px; padding: 5px; }
        QTabWidget::pane { border: 1px solid #30485d; border-radius: 6px; }
        QTabBar::tab { background: #162635; padding: 9px 18px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
        QTabBar::tab:selected { background: #285775; color: #ffffff; } QTabBar::tab:hover { background: #21394f; }
        QHeaderView::section { background: #21394f; padding: 6px; border: none; font-weight: 600; }
        QHeaderView::section:hover { background: #2c506e; }
        QTableWidget, QListWidget { background: #0d1826; alternate-background-color: #142636; gridline-color: #24384c; selection-background-color: #285775; selection-color: #ffffff; border-radius: 6px; }
        QGroupBox { border: 1px solid #3b5367; border-radius: 8px; margin-top: 14px; padding-top: 12px; font-weight: 600; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; } QLabel#helpText { color: #a8b9cb; }
        QLabel#metrics { font-size: 10pt; font-weight: 600; padding: 1px 2px; background: transparent; }
        QWidget#topStatus { background: transparent; }
        QLabel#feedStatus { color: #cfe0ee; padding: 1px 2px; background: transparent; }
        QLabel#mapMessage { background: rgba(13, 24, 38, 232); border: 1px solid #45647a; border-radius: 8px; padding: 6px 12px; font-weight: 600; }
        QLabel#bottomAttribution { background: transparent; padding: 2px 6px; }
        QFrame#overlayPanel { background: rgba(13, 24, 38, 232); border: 1px solid #45647a; border-radius: 10px; }
        QFrame#overlayPanel QWidget { background: transparent; }
        QFrame#overlayPanel QTableWidget { background: rgba(13, 24, 38, 235); }
        QFrame#overlayPanel QLineEdit, QFrame#overlayPanel QComboBox { background: #0b141f; }
        QLabel#panelTitle { font-size: 11pt; font-weight: 700; background: transparent; }
        QLabel#receiverStatus { font-weight: 600; color: #bfe3d8; padding: 3px 4px; }
        QLabel#mapChip { background: rgba(13, 24, 38, 215); color: #e8edf4; border: 1px solid #45647a; border-radius: 8px; padding: 4px 10px; font-weight: 600; }
        QWidget#mapControls { background: transparent; }
        QWidget#mapControls QPushButton { background: rgba(22, 38, 53, 235); font-size: 14pt; border-radius: 8px; padding: 0px; }
        QSplitter::handle { background: #30485d; } QSplitter::handle:horizontal { width: 5px; } QSplitter::handle:vertical { height: 5px; }
        QDockWidget::title { padding: 8px; background: #21394f; }
        QScrollBar:vertical, QScrollBar:horizontal { background: #0b141f; border: none; width: 12px; height: 12px; }
        QScrollBar::handle { background: #30485d; border-radius: 6px; min-height: 24px; min-width: 24px; }
        QScrollBar::handle:hover { background: #45647a; } QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
        QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; }
        QToolTip { background: #e8edf4; color: #14202e; border-radius: 4px; padding: 4px; }
    ''')
def run_desktop(receiver, instance=None, on_ready=None, *, start_receiver=False):
    app = QApplication.instance() or QApplication([])
    apply_desktop_style(app)
    window = FlightRadarWindow(receiver, instance)
    window.show()
    if start_receiver and not receiver.settings.value.get('autostart_receiver'):
        QTimer.singleShot(100, lambda: window.operation('start'))
    if on_ready:
        QTimer.singleShot(0, on_ready)
    return app.exec()
