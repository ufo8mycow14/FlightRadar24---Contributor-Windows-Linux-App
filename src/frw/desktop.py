"""Native Qt desktop. No web engine, browser process, or HTTP transport."""
from __future__ import annotations
import csv
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QDockWidget, QTabWidget, QFileDialog, QToolBar, QComboBox, QDialog, QSystemTrayIcon)
from .control import Controller
from .native_map import NativeMap, RangeRadar, NativeAircraftDetails
from .native_pages import NativePages
from .features import FEATURE_DEFAULT, matches
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
        bar = QToolBar('Receiver controls', self)
        bar.setMovable(False)
        self.addToolBar(bar)
        self.start_button, self.stop_button = QPushButton('Start receiver'), QPushButton('Stop receiver')
        self.start_button.clicked.connect(lambda: self.operation('start'))
        self.stop_button.clicked.connect(lambda: self.operation('stop'))
        bar.addWidget(self.start_button)
        bar.addWidget(self.stop_button)
        bar.addSeparator()
        self.health = QLabel('  Connecting to receiver…')
        self.health.setTextFormat(Qt.TextFormat.PlainText)
        bar.addWidget(self.health)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        dashboard = QWidget()
        layout = QVBoxLayout(dashboard)
        layout.setContentsMargins(8, 8, 8, 8)
        self.metrics = QLabel('Waiting for receiver status…')
        self.metrics.setObjectName('metrics')
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        self.feed_status = QLabel('Flightradar24 upload: stopped')
        self.feed_status.setTextFormat(Qt.TextFormat.PlainText)
        self.feed_status.setWordWrap(True)
        layout.addWidget(self.feed_status)
        self.message = QLabel('')
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        if self.feature_model:
            filters=QHBoxLayout()
            filters.addWidget(self._feature_button('Filters…',self.edit_filters))
            self.preset_combo=QComboBox()
            self.preset_combo.setAccessibleName('Airspace filter preset')
            self.preset_combo.setMinimumContentsLength(18)
            self.preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.preset_combo.setMaximumWidth(240)
            self.preset_combo.addItem('All aircraft',None)
            self.preset_combo.activated.connect(self.choose_preset)
            filters.addWidget(self.preset_combo)
            filters.addWidget(self._feature_button('Clear filters',lambda:self.feature_model.save({'filter_enabled':False})))
            filters.addWidget(self._feature_button('Alert for selected aircraft…',lambda:self.alerts_page.add(self.selected) if self.selected else self.notice('Select an aircraft first.',False)))
            self.filter_status=QLabel('All received aircraft')
            self.filter_status.setTextFormat(Qt.TextFormat.PlainText)
            self.filter_status.setWordWrap(True)
            filters.addWidget(self.filter_status,1)
            layout.addLayout(filters)
        self.map = NativeMap(cache_dir=self.receiver.directory / 'map-cache')
        self.map.aircraftSelected.connect(self.select_aircraft)
        vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.addWidget(self.map)
        lower = QSplitter(Qt.Orientation.Horizontal)
        self.radar = RangeRadar()
        self.radar.aircraftSelected.connect(self.select_aircraft)
        lower.addWidget(self.radar)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(['Callsign', 'ICAO', 'Altitude ft', 'Speed kt',
                                             'Track °', 'V/S ft/min', 'Squawk', 'Seen s', 'Position'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._table_selected)
        self.table.itemDoubleClicked.connect(lambda item: self.map.focus_aircraft(self.selected))
        lower.addWidget(self.table)
        lower.setSizes([350, 950])
        vertical.addWidget(lower)
        vertical.setSizes([560, 230])
        vertical.setStretchFactor(0, 3)
        vertical.setStretchFactor(1, 1)
        layout.addWidget(vertical, 1)
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
        self.details_dock = QDockWidget('Aircraft · live broadcast data', self)
        self.details_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea)
        self.details_dock.setWidget(self.details)
        self.details_dock.setMinimumWidth(330)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.details_dock)
        self.details_dock.hide()
        self.details.closed.connect(self.clear_selection)
        self.details_dock.visibilityChanged.connect(self._details_visibility)
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
        if self.selected and self.selected not in {r['icao'] for r in self.visible_rows()}:
            self.clear_selection()
        self._update_positions();self._update_table()

    def edit_filters(self):
        FilterDialog(self.feature_model,self).exec()

    def choose_preset(self,index):
        criteria=self.preset_combo.itemData(index)
        self.feature_model.save({'filter_enabled':criteria is not None,**({'filter':criteria} if criteria is not None else {})})

    def visible_rows(self):
        if not self.features.get('filter_enabled'):
            return self.rows
        station=(self.config.get('latitude'),self.config.get('longitude'))
        return [r for r in self.rows if matches(r,self.features['filter'],station)]

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

    def _status_notice(self, text):
        self.notice(text, True)
        self._poll_notice = str(text)

    def _clear_status_notice(self):
        if self._poll_notice is not None and self.message.text() == self._poll_notice:
            self.message.hide()
            self.message.clear()
            if self.statusBar().currentMessage() == self._poll_notice:
                self.statusBar().clearMessage()
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
                self.health.setText('  Receiver status unavailable')
                self.details.set_error(str(error))
                return
            self._clear_status_notice()
            self.last_status = status
            self.rows = status.get('aircraft', [])
            state = status.get('state', 'stopped')
            playback = status.get('input', self.config.get('receiver')) == 'iq-file'
            self.health.setText('  Receiver: ' + state + (' · Recorded playback' if playback else ''))
            running = state in ('starting', 'receiving', 'reconnecting')
            self.start_button.setEnabled(not running and not self._action_pending)
            self.stop_button.setEnabled(running and not self._action_pending)
            positioned = sum(r.get('latitude') is not None and r.get('longitude') is not None for r in self.rows)
            self.metrics.setText(f"{len(self.rows)} aircraft  ·  {positioned} with positions  ·  {status.get('messages_per_second', 0)} messages/s  ·  {status.get('messages', 0):,} messages")
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
        self.radar.update_aircraft(rows, self.config, self.selected)
        if self.feature_model:
            self.filter_status.setText(f'{len(rows)} / {len(self.rows)} aircraft shown'+(' · '+describe(self.features['filter']) if self.features['filter_enabled'] else ' · All aircraft'))

    def _update_table(self):
        self.table.blockSignals(True)
        rows=self.visible_rows()
        self.table.setRowCount(len(rows))
        selected_row = None
        for index, row in enumerate(rows):
            values = [row.get(key) for key in ('callsign', 'icao', 'altitude', 'groundspeed', 'track', 'vertical_rate', 'squawk', 'seen')]
            playback = self.last_status.get('input', self.config.get('receiver')) == 'iq-file'
            values.append(('Recorded' if playback else 'Live') if row.get('latitude') is not None and row.get('longitude') is not None else 'Not received')
            for column, value in enumerate(values):
                text = '—' if value is None else f'{value:g}' if isinstance(value, float) else str(value).strip()
                item = self.table.item(index, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(index, column, item)
                item.setText(text)
                item.setData(Qt.ItemDataRole.UserRole, row['icao'])
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
        self.details_dock.show()
        if not self._details_width_set:
            self.resizeDocks([self.details_dock], [400], Qt.Orientation.Horizontal)
            self._details_width_set = True
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

    def _details_visibility(self, visible):
        if not visible and not self._closing:
            self.selected = ''
            self.details.clear()
            self._update_positions()
            self._update_table()

    def clear_selection(self):
        self.selected = ''
        self.details.clear()
        self.details_dock.hide()
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
        QMainWindow, QWidget { background: #14202e; color: #e8edf4; font-size: 10pt; }
        QToolBar, QMenuBar, QStatusBar { background: #1c2c3c; padding: 5px; }
        QPushButton { background: #25445b; border: 1px solid #45647a; border-radius: 4px; padding: 6px 12px; }
        QPushButton:hover { background: #336581; } QPushButton:disabled { color: #718293; background: #1c2c3c; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit { background: #0d1823; border: 1px solid #45647a; padding: 4px; }
        QTabBar::tab { background: #1c2c3c; padding: 9px 18px; } QTabBar::tab:selected { background: #285775; }
        QHeaderView::section { background: #25445b; padding: 5px; border: none; }
        QTableWidget, QListWidget { background: #101b27; alternate-background-color: #172b3b; gridline-color: #293d51; selection-background-color: #285775; selection-color: #ffffff; }
        QGroupBox { border: 1px solid #3b5367; border-radius: 5px; margin-top: 14px; padding-top: 12px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; } QLabel#helpText { color: #a8b9cb; }
        QLabel#metrics { font-size: 14pt; font-weight: 600; padding: 4px; }
        QSplitter::handle { background: #30485d; } QDockWidget::title { padding: 8px; background: #25445b; }
        QToolTip { background: #e8edf4; color: #14202e; }
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
