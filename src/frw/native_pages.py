"""Native receiver, settings, registration and diagnostics controls.

The supplied invoke function runs controller work outside the GUI thread and
delivers callback(value, error=None) on the GUI thread. These pages neither open
a browser nor start a local HTTP server.
"""
from __future__ import annotations

import copy
import math
import os
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from .config import DEFAULT, validate
from .platform_support import key_storage_description


INPUT_CHOICES = (
    ('usb', 'USB RTL-SDR · 1090 MHz'), ('beast-tcp', 'Beast TCP'),
    ('avr-tcp', 'AVR TCP'), ('sbs-tcp', 'SBS TCP · decoded reports'),
    ('beast-serial', 'Beast serial / COM'), ('avr-serial', 'AVR serial / COM'),
    ('iq-file', 'Recorded I/Q file · local playback'),
)
GROUPS = {
    'receiver': ('receiver', 'device', 'host', 'input_port', 'serial_port',
                 'baudrate', 'iq_file', 'sample_rate'),
    'tuning': ('gain', 'bandwidth_khz', 'ppm', 'error_correction', 'agc', 'bias_tee',
               'dc_filter', 'phase_enhance', 'mode_ac', 'meteo', 'capture_mb'),
    'location': ('latitude', 'longitude', 'altitude_ft', 'range_nm'),
    'sharing': ('feed_enabled', 'mlat', 'mlat_without_gps'),
    'outputs': ('raw', 'raw_port', 'bs', 'bs_port', 'beast', 'beast_port', 'bind_interface'),
    'operation': ('global_timeout', 'aircraft_ttl', 'logmode', 'autostart_receiver'),
}
LABELS = {
    'receiver': 'Input type', 'device': 'USB decoder index', 'host': 'Input host',
    'input_port': 'Input TCP port', 'serial_port': 'Windows COM port' if os.name == 'nt' else 'Serial device path',
    'baudrate': 'Serial baud rate', 'iq_file': 'Recorded .uc8 file',
    'sample_rate': 'Sample rate', 'gain': 'Tuner gain (dB)',
    'ppm': 'Frequency correction (PPM)', 'error_correction': 'Bit error correction',
    'agc': 'Digital automatic gain', 'bias_tee': 'Bias tee power',
    'bandwidth_khz': 'Tuner bandwidth (kHz)', 'mode_ac': 'Decode anonymous Mode A/C replies',
    'meteo': 'Try identifying Comm-B weather reports', 'capture_mb': 'Local message archive (MiB)',
    'dc_filter': 'DC filter', 'phase_enhance': 'Phase enhancement',
    'latitude': 'Latitude (decimal degrees)', 'longitude': 'Longitude (decimal degrees)',
    'altitude_ft': 'Antenna altitude AMSL (feet)', 'range_nm': 'Maximum position range (NM)',
    'feed_enabled': 'Upload live reception to Flightradar24',
    'mlat': 'Request MLAT with timestamped input',
    'mlat_without_gps': 'Allow MLAT without a GPS receiver',
    'raw': 'Enable RAW / AVR output', 'raw_port': 'RAW port',
    'bs': 'Enable BaseStation / SBS output', 'bs_port': 'SBS port',
    'beast': 'Enable Beast output', 'beast_port': 'Beast port',
    'bind_interface': 'Data stream access',
    'global_timeout': 'Reconnect after no frames (seconds)',
    'aircraft_ttl': 'Remove stale aircraft after (seconds)',
    'logmode': 'Diagnostic file logging', 'autostart_receiver': 'Start receiver when this app opens',
}
CHOICES = {
    'receiver': INPUT_CHOICES,
    'baudrate': tuple((value, str(value)) for value in (57600, 115200, 921600, 1000000, 3000000)),
    'sample_rate': ((2400000, '2.4 MHz'),) if os.name != 'nt' else ((2400000, '2.4 MHz'), (2000000, '2.0 MHz')),
    'error_correction': ((0, 'Off'), (1, '1 bit'), (2, 'Up to 2 bits')),
    'bind_interface': (('127.0.0.1', 'This PC only'), ('0.0.0.0', 'Local network · all interfaces')),
    'logmode': ((0, 'No file log'), (1, 'On'), (2, 'On · default')),
}
INTEGER_LIMITS = {
    'input_port': (1, 65535), 'raw_port': (1024, 65535),
    'bs_port': (1024, 65535), 'beast_port': (1024, 65535),
    'ppm': (-1000, 1000), 'global_timeout': (0, 86400), 'aircraft_ttl': (10, 3600),
    'bandwidth_khz': (0, 3200), 'capture_mb': (0, 4096),
}
OPTIONAL_NUMBERS = frozenset(('latitude', 'longitude', 'altitude_ft'))
HINTS = {
    'device': '0 is the first compatible receiver; 1 is the second. Scanned native devices can be selected below.',
    'gain': '−10 selects automatic gain. Manual gain is 0–60 dB.',
    'bandwidth_khz': '0 uses the driver default; otherwise choose 300–3200 kHz. Supported widths depend on the tuner.',
    'bias_tee': 'Supplies DC power to the antenna socket. Enable only for compatible powered equipment.',
    'capture_mb': '0 disables recording. Otherwise the oldest private message segments rotate out at this limit.',
    'meteo': 'Weather formats are inferred, not labelled by the aircraft. Ambiguous payloads stay unidentified.',
    'latitude': 'South is negative. Leave both coordinates blank for local reception without a reference position.',
    'longitude': 'West is negative.',
    'altitude_ft': 'Use the same antenna altitude during official registration.',
    'iq_file': 'Recorded playback stays local; uploading is disabled.',
    'global_timeout': '0 disables the inactivity timeout.',
}


class NativePages(QWidget):
    """Settings pages driven by the shared, asynchronous application controller."""

    notice = Signal(str, bool)
    settingsChanged = Signal(dict)
    apiSettingsRequested = Signal()

    def __init__(self, invoke, parent=None):
        super().__init__(parent)
        self.invoke = invoke
        self.config = copy.deepcopy(DEFAULT)
        self.key_storage = key_storage_description()
        self.has_key = False
        self.fields = {}
        self.pages = {}
        self._rows = {}
        self._dirty = set()
        self._populating = False
        self._running = False
        self._signup_running = False
        self._signup_open = False
        self._signup_pending = False
        self._logs_pending = False
        self._busy_buttons = set()
        self._settings_requests = 0
        self._settings_generation = 0
        self._protected_buttons = []
        self._import_preview = None
        self._last_logs = None
        self._last_signup_text = None
        self._last_monitor = None
        self._current_sample = None
        self._baseline_rate = None
        self._sample_rate = None
        self._gain_options = None
        self._closed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextFormat(Qt.TextFormat.PlainText)
        self.banner.setObjectName('settingsNotice')
        self.banner.hide()
        layout.addWidget(self.banner)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._build_receiver()
        self._build_settings()
        self._build_sharing()
        self._build_logs()
        self.tabs.currentChanged.connect(self._page_changed)
        self.update_settings(DEFAULT)
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._poll_auxiliary)
        self._timer.start()

    def _page(self, name, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(14)
        scroll.setWidget(body)
        self.pages[name] = scroll
        self.tabs.addTab(scroll, title)
        return layout

    @staticmethod
    def _help(text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName('helpText')
        return label

    def _button(self, text, callback, *, protected=False):
        button = QPushButton(text)
        button.clicked.connect(lambda checked=False: callback(button))
        if protected:
            self._protected_buttons.append(button)
        return button

    @staticmethod
    def _buttons(*widgets):
        row = QHBoxLayout()
        has_editor = False
        for widget in widgets:
            expands = isinstance(widget, QLineEdit)
            has_editor = has_editor or expands
            row.addWidget(widget, 1 if expands else 0)
        if not has_editor:
            row.addStretch(1)
        return row

    def _field_group(self, title, group):
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(10)
        for key in GROUPS[group]:
            default = DEFAULT[key]
            if key in CHOICES:
                control = QComboBox()
                for value, label in CHOICES[key]:
                    control.addItem(label, value)
                control.currentIndexChanged.connect(lambda index, field=key: self._edited(field))
            elif isinstance(default, bool):
                control = QCheckBox(LABELS[key])
                control.toggled.connect(lambda checked, field=key: self._edited(field))
            elif key in INTEGER_LIMITS:
                control = QSpinBox()
                control.setRange(*INTEGER_LIMITS[key])
                control.setGroupSeparatorShown(False)
                control.valueChanged.connect(lambda value, field=key: self._edited(field))
            elif key in ('gain', 'range_nm'):
                control = QDoubleSpinBox()
                control.setRange(*((-10, 60) if key == 'gain' else (1, 21600)))
                control.setDecimals(1)
                control.setSingleStep(0.1 if key == 'gain' else 1)
                control.valueChanged.connect(lambda value, field=key: self._edited(field))
            else:
                control = QLineEdit()
                control.setMaxLength(1024)
                if key in OPTIONAL_NUMBERS:
                    control.setPlaceholderText('Optional')
                control.textEdited.connect(lambda value, field=key: self._edited(field))
            control.setObjectName('setting_' + key)
            self.fields[key] = control
            wrapper = QWidget()
            column = QVBoxLayout(wrapper)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            if key == 'iq_file':
                browse = self._button('Browse…', lambda button: self._choose_recording())
                column.addLayout(self._buttons(control, browse))
            else:
                column.addWidget(control)
            if key == 'gain':
                self.gain_steps = QComboBox()
                self.gain_steps.setToolTip('Reported by the last receiver at this USB index. Selecting a step edits the form; it does not retune a running radio.')
                self.gain_steps.activated.connect(self._select_gain_step)
                column.addWidget(self.gain_steps)
                self.gain_steps.hide()
            if key in HINTS:
                column.addWidget(self._help(HINTS[key]))
            if isinstance(default, bool):
                form.addRow(wrapper)
            else:
                form.addRow(LABELS[key], wrapper)
            self._rows[key] = (form, wrapper)
        return box

    def _build_receiver(self):
        page = self._page('receiver', 'Receiver')
        self.receiver_state = self._help('Receiver stopped')
        page.addWidget(self.receiver_state)
        self.start_button = self._button('Start receiver', lambda b: self._request('/api/start', {}, button=b))
        self.stop_button = self._button('Stop receiver', lambda b: self._request('/api/stop', {}, button=b))
        page.addLayout(self._buttons(self.start_button, self.stop_button))
        comparison = QGroupBox('Reception quality · last 60 seconds')
        layout = QVBoxLayout(comparison)
        self.quality_summary = self._help('No receiver sample yet.')
        self.baseline_button = self._button('Keep this minute as baseline', lambda _: self._keep_sample())
        self.baseline_button.setEnabled(False)
        self.baseline_summary = self._help('Keep a baseline, change one setting, then compare another full minute under similar traffic.')
        self.capture_summary = self._help('')
        for widget in (self.quality_summary, self.baseline_button, self.baseline_summary, self.capture_summary):
            layout.addWidget(widget)
        page.addWidget(comparison)
        page.addWidget(self._field_group('Input', 'receiver'))
        devices = QGroupBox('USB receivers')
        layout = QVBoxLayout(devices)
        self.scan_button = self._button('Scan USB receivers', self._scan)
        self.use_device_button = self._button('Use selected decoder index', lambda b: self._use_device())
        self.use_device_button.setEnabled(False)
        layout.addLayout(self._buttons(self.scan_button, self.use_device_button))
        self.device_list = QListWidget()
        self.device_list.setMinimumHeight(90)
        self.device_list.setMaximumHeight(180)
        self.device_list.itemSelectionChanged.connect(self._device_selection)
        layout.addWidget(self.device_list)
        self.device_note = self._help('Connect the receiver and scan to list available USB devices.')
        layout.addWidget(self.device_note)
        page.addWidget(devices)
        page.addWidget(self._field_group('Tuning', 'tuning'))
        page.addWidget(self._field_group('Antenna location', 'location'))
        page.addWidget(self._button('Save receiver settings',
            lambda b: self._save(('receiver', 'tuning', 'location'), b), protected=True))
        page.addWidget(self._help('Stop the receiver before saving changes. Start it again to apply the saved settings.'))
        page.addStretch(1)

    def _build_settings(self):
        page = self._page('settings', 'Data & settings')
        page.addWidget(self._field_group('Data outputs', 'outputs'))
        page.addWidget(self._field_group('Operation', 'operation'))
        page.addWidget(self._button('Save data and operation settings',
            lambda b: self._save(('outputs', 'operation'), b), protected=True))
        box = QGroupBox('Import an existing fr24feed.ini')
        layout = QVBoxLayout(box)
        layout.addWidget(self._help('Choose or paste an INI file, review the preview, then apply it. An imported sharing key is stored using Windows encryption.'))
        layout.addWidget(self._button('Choose INI file…', lambda b: self._choose_ini()))
        self.ini_text = QPlainTextEdit()
        self.ini_text.setPlaceholderText('Paste fr24feed.ini contents here')
        self.ini_text.setMinimumHeight(130)
        self.ini_text.setMaximumHeight(230)
        self.ini_text.textChanged.connect(self._invalidate_import)
        layout.addWidget(self.ini_text)
        self.preview_button = self._button('Preview import', self._preview_import)
        self.apply_button = self._button('Apply imported settings', self._apply_import, protected=True)
        layout.addLayout(self._buttons(self.preview_button, self.apply_button))
        self.import_result = QPlainTextEdit()
        self.import_result.setReadOnly(True)
        self.import_result.setMaximumHeight(140)
        self.import_result.hide()
        layout.addWidget(self.import_result)
        page.addWidget(box)
        page.addWidget(self._button('Export settings to INI…', lambda b: self._export('/api/export',
            'Export settings', 'fr24feed.ini', 'INI files (*.ini);;All files (*)', b)))
        page.addWidget(self._help('Exported settings omit the stored sharing key.'))
        page.addStretch(1)

    def _build_sharing(self):
        page = self._page('sharing', 'Flightradar24')
        page.addWidget(self._help('Incoming API data and outgoing receiver sharing are independent. An API token is not a sharing key.'))
        page.addWidget(self._button('Flightradar24 API settings…', lambda button: self.apiSettingsRequested.emit()))
        pi24 = QGroupBox('FR24 feeder and USB input')
        pi24_layout = QVBoxLayout(pi24)
        pi24_layout.addWidget(self._help(
            'With a USB input the Windows decoder controls the physical RTL-SDR. The official uploader reads its timestamped stream. A Pi24 profile is separate from that active connection.'
        ))
        self.feed_mode = QComboBox()
        self.feed_mode.setObjectName('feed_mode')
        self.feed_mode.addItem('Use the app’s receiver stream (relay)', 'relay')
        self.feed_mode.addItem('Direct DVB-T USB (unavailable in this Windows runtime)', 'direct-usb')
        self.feed_mode.model().item(1).setEnabled(False)
        self.feed_mode.currentIndexChanged.connect(self._feed_mode_changed)
        pi24_layout.addWidget(self.feed_mode)
        self.pi24_state = self._help('Configuration applies when you next start the receiver.')
        self.pi24_state.setWordWrap(True)
        pi24_layout.addWidget(self.pi24_state)
        pi24_form_box = QWidget()
        self.pi24_form = QFormLayout(pi24_form_box)
        self.pi24_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.pi24_form.setVerticalSpacing(8)
        self.pi24_fields = {}
        for key, label in (('bs', 'BaseStation / SBS output'), ('raw', 'RAW output'), ('mpx', 'MPX output')):
            control = QCheckBox(label)
            control.setObjectName('pi24_' + key)
            control.toggled.connect(lambda checked: self._edited('pi24'))
            self.pi24_fields[key] = control
            self.pi24_form.addRow(control)
        self.pi24_logmode = QComboBox()
        self.pi24_logmode.setObjectName('pi24_logmode')
        for value, label in ((0, 'Off'), (1, '48 hours'), (2, '72 hours')):
            self.pi24_logmode.addItem(label, value)
        self.pi24_logmode.currentIndexChanged.connect(lambda _: self._edited('pi24'))
        self.pi24_fields['logmode'] = self.pi24_logmode
        self.pi24_form.addRow('Feeder log retention', self.pi24_logmode)
        self.pi24_path = QLineEdit()
        self.pi24_path.setObjectName('pi24_path')
        self.pi24_path.setMaxLength(512)
        self.pi24_path.setToolTip('Saved as a Pi24 profile value. It is not applied by the Windows relay.')
        self.pi24_path.textEdited.connect(lambda _: self._edited('pi24'))
        self.pi24_fields['path'] = self.pi24_path
        self.pi24_form.addRow('FR24 decoder path', self.pi24_path)
        self.pi24_procargs = QLineEdit()
        self.pi24_procargs.setObjectName('pi24_procargs')
        self.pi24_procargs.setMaxLength(128)
        self.pi24_procargs.setPlaceholderText('--gain -10 --net')
        self.pi24_procargs.setToolTip('Saved as a Pi24 profile value. It is not applied by the Windows relay.')
        self.pi24_procargs.textEdited.connect(lambda _: self._edited('pi24'))
        self.pi24_fields['procargs'] = self.pi24_procargs
        self.pi24_form.addRow('Decoder options', self.pi24_procargs)
        self.pi24_gain_button = self._button(
            'Use Pi24 gain in Receiver settings', lambda _: self._apply_pi24_gain())
        self.pi24_gain_button.setObjectName('pi24_use_gain')
        self.pi24_form.addRow(self.pi24_gain_button)
        self.pi24_apply_windows_button = self._button(
            'Apply supported Pi24 settings to Windows receiver',
            lambda _: self._apply_pi24_windows_settings())
        self.pi24_apply_windows_button.setObjectName('pi24_apply_windows_settings')
        self.pi24_form.addRow(self.pi24_apply_windows_button)
        pi24_layout.addWidget(self._help(
            'Copying changes the Windows controls, not the running receiver. Save gain on the Receiver page; save RAW/SBS and local logging under Data & settings. The app does not run the Pi command line or apply its MPX, window mode or log retention.'
        ))
        self.pi24_windowmode = QComboBox()
        self.pi24_windowmode.setObjectName('pi24_windowmode')
        for mode in (0, 1, 2):
            self.pi24_windowmode.addItem(str(mode), mode)
        self.pi24_windowmode.currentIndexChanged.connect(lambda _: self._edited('pi24'))
        self.pi24_fields['windowmode'] = self.pi24_windowmode
        self.pi24_form.addRow('Feeder window mode', self.pi24_windowmode)
        pi24_layout.addWidget(pi24_form_box)
        self.pi24_save_button = self._button('Save Pi24 profile and MLAT settings', self._save_pi24, protected=True)
        pi24_layout.addWidget(self.pi24_save_button)
        self.pi24_export_button = self._button('Export saved Pi24 profile…', lambda b: self._export(
            '/api/export/pi24', 'Export saved Pi24 profile', 'fr24feed.ini', 'INI files (*.ini);;All files (*)', b))
        pi24_layout.addWidget(self.pi24_export_button)
        pi24_layout.addWidget(self._help('Exports saved Pi24 template values without your sharing key. Add the key securely on the target feeder before using the file.'))
        page.addWidget(pi24)
        page.addWidget(self._field_group('Sharing', 'sharing'))
        page.addWidget(self._button('Save sharing settings', lambda b: self._save(('sharing',), b), protected=True))
        key_box = QGroupBox('Sharing key')
        layout = QVBoxLayout(key_box)
        self.key_status = self._help('No sharing key is stored yet.')
        layout.addWidget(self.key_status)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setMaxLength(256)
        self.key_edit.setPlaceholderText('Paste a sharing key to store it securely')
        self.key_edit.setObjectName('sharing_key')
        layout.addWidget(self.key_edit)
        self.key_save_button = self._button('Store sharing key', self._save_key, protected=True)
        self.key_clear_button = self._button('Remove stored key', self._clear_key, protected=True)
        layout.addLayout(self._buttons(self.key_save_button, self.key_clear_button))
        page.addWidget(key_box)
        signup = QGroupBox('Official receiver registration')
        layout = QVBoxLayout(signup)
        layout.addWidget(self._help('The official feeder wizard runs inside this window. Answer its prompts below. Stop the receiver before starting registration.'))
        self.register_button = self._button('Start official registration', self._start_signup, protected=True)
        self.cancel_signup_button = self._button('Cancel registration', self._stop_signup)
        self.cancel_signup_button.setEnabled(False)
        layout.addLayout(self._buttons(self.register_button, self.cancel_signup_button))
        self.signup_text = QPlainTextEdit()
        self.signup_text.setReadOnly(True)
        self.signup_text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.signup_text.setMinimumHeight(180)
        self.signup_text.setPlaceholderText('Official registration prompts appear here.')
        layout.addWidget(self.signup_text)
        self.signup_answer = QLineEdit()
        self.signup_answer.setMaxLength(1024)
        self.signup_answer.setPlaceholderText('Answer the current prompt')
        self.signup_answer.returnPressed.connect(self._answer_signup)
        self.answer_button = self._button('Send answer', lambda b: self._answer_signup())
        self.signup_answer.setEnabled(False)
        self.answer_button.setEnabled(False)
        layout.addLayout(self._buttons(self.signup_answer, self.answer_button))
        page.addWidget(signup)
        monitor = QGroupBox('Feeder status')
        layout = QVBoxLayout(monitor)
        self.feeder_summary = self._help('Feeder stopped')
        layout.addWidget(self.feeder_summary)
        self.monitor_text = QPlainTextEdit()
        self.monitor_text.setReadOnly(True)
        self.monitor_text.setMinimumHeight(140)
        self.monitor_text.setMaximumHeight(280)
        layout.addWidget(self.monitor_text)
        page.addWidget(monitor)
        page.addStretch(1)

    def _build_logs(self):
        page = self._page('logs', 'Logs & diagnostics')
        self.follow_logs = QCheckBox('Follow new log entries')
        self.follow_logs.setChecked(True)
        self.log_refresh_button = self._button('Refresh logs', lambda b: self.refresh_logs(b))
        page.addLayout(self._buttons(self.follow_logs, self.log_refresh_button))
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log_output.setMinimumHeight(360)
        page.addWidget(self.log_output, 1)
        page.addWidget(self._button('Save diagnostic archive…', lambda b: self._export('/api/diagnostics',
            'Save diagnostic archive', 'flight-radar-diagnostics.zip', 'ZIP archives (*.zip)', b)))
        page.addWidget(self._help('The diagnostic archive excludes sharing keys, antenna coordinates and registration answers. Logs remain available here.'))

    def _edited(self, key):
        if not self._populating:
            self._dirty.add(key)
        if key in ('receiver', 'sample_rate') and not self._populating:
            self._input_visibility()

    def _feed_mode_changed(self, _index):
        self._edited('feed_mode')
        self._update_feed_mode_note()

    def _update_feed_mode_note(self, receiver=None):
        direct = self.feed_mode.currentData() == 'direct-usb'
        if direct:
            text = 'Direct USB is unavailable in this Windows runtime. The saved Pi24 values are a configuration template; the working Windows receiver remains in relay mode.'
            if receiver:
                text += f' Feeder currently reports receiver: {receiver}.'
        else:
            text = 'For USB input the Windows decoder controls the stick; the FR24 uploader reads a stream. Pi24-specific fields are saved as a template, not applied to the uploader. MLAT settings request participation; the service reports permission.'
            if receiver:
                text += f' Feeder currently reports receiver: {receiver}.'
        self.pi24_state.setText(text)

    def _input_visibility(self):
        receiver = self.fields['receiver'].currentData()
        native = receiver in ('usb', 'iq-file')
        windows = os.name == 'nt'
        rules = {'device': receiver == 'usb', 'host': receiver.endswith('tcp'),
                 'input_port': receiver.endswith('tcp'), 'serial_port': receiver.endswith('serial'),
                 'baudrate': receiver.endswith('serial'), 'iq_file': receiver == 'iq-file',
                 'sample_rate': native, 'gain': receiver == 'usb', 'agc': receiver == 'usb',
                 'ppm': receiver == 'usb', 'bias_tee': windows and receiver == 'usb',
                 'bandwidth_khz': windows and receiver == 'usb', 'mode_ac': native,
                 'error_correction': native, 'dc_filter': windows and native,
                 'phase_enhance': windows and native and self._value('sample_rate') == 2000000,
                 'meteo': receiver != 'sbs-tcp'}
        for key, visible in rules.items():
            form, wrapper = self._rows[key]
            form.setRowVisible(wrapper, visible)

    def _value(self, key):
        widget = self.fields[key]
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            return widget.value()
        text = widget.text().strip()
        if key in OPTIONAL_NUMBERS:
            if not text:
                return None
            try:
                value = float(text)
            except ValueError:
                raise ValueError(LABELS[key] + ': enter a number or leave it empty.') from None
            if not math.isfinite(value):
                raise ValueError(LABELS[key] + ': enter a finite number.')
            return value
        return text

    def update_settings(self, settings, *, force=False):
        """Refresh saved values without overwriting any in-progress field edits."""
        if not isinstance(settings, dict):
            return
        self.config = copy.deepcopy(DEFAULT)
        self.config.update({key: copy.deepcopy(value) for key, value in settings.items() if key in DEFAULT})
        self.has_key = bool(settings.get('has_key', self.has_key))
        self.key_storage = str(settings.get('key_storage') or self.key_storage)
        if force:
            self._dirty.clear()
        self._populating = True
        try:
            for key, widget in self.fields.items():
                if key in self._dirty:
                    continue
                value = self.config[key]
                if isinstance(widget, QCheckBox):
                    widget.setChecked(value)
                elif isinstance(widget, QComboBox):
                    index = widget.findData(value)
                    if index >= 0:
                        widget.setCurrentIndex(index)
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    widget.setValue(value)
                else:
                    widget.setText('' if value is None else str(value))
            if 'feed_mode' not in self._dirty:
                index = self.feed_mode.findData(self.config['feed_mode'])
                if index >= 0:
                    self.feed_mode.setCurrentIndex(index)
            if 'pi24' not in self._dirty:
                for key, control in self.pi24_fields.items():
                    value = self.config['pi24'][key]
                    if isinstance(control, QCheckBox):
                        control.setChecked(value)
                    elif isinstance(control, QComboBox):
                        control.setCurrentIndex(control.findData(value))
                    else:
                        control.setText(str(value))
        finally:
            self._populating = False
        self._update_feed_mode_note()
        self.key_status.setText(self.key_storage if self.has_key else 'No sharing key is stored yet.')
        self._input_visibility()
        self._sync_buttons()

    def refresh_settings(self, *, force=False, callback=None):
        generation = self._settings_generation
        def received(value):
            if generation != self._settings_generation:
                return
            self.update_settings(value, force=force)
            self.settingsChanged.emit(copy.deepcopy(value))
            if callback:
                callback(value)
        self._request('/api/settings', callback=received)

    def _save(self, groups, button):
        keys = {key for group in groups for key in GROUPS[group]}
        try:
            proposed = copy.deepcopy(self.config)
            proposed.update({key: self._value(key) for key in keys})
            proposed = validate(proposed)
        except (ValueError, TypeError) as error:
            self._notify(str(error), True)
            return
        submitted = {key: self._value(key) for key in keys}
        def saved(value):
            for key, submitted_value in submitted.items():
                try:
                    unchanged = self._value(key) == submitted_value
                except (ValueError, TypeError):
                    unchanged = False
                if unchanged:
                    self._dirty.discard(key)
            self.config = proposed
            self.refresh_settings()
            self._notify('Settings saved. Start the receiver to use them.')
        self._request('/api/settings', {'settings': proposed}, saved, button)

    def _save_pi24(self, button):
        try:
            submitted = {
                'feed_mode': 'relay',
                'pi24': {
                    'bs': self.pi24_fields['bs'].isChecked(),
                    'raw': self.pi24_fields['raw'].isChecked(),
                    'mpx': self.pi24_fields['mpx'].isChecked(),
                    'logmode': self.pi24_logmode.currentData(),
                    'windowmode': self.pi24_windowmode.currentData(),
                    'path': self.pi24_path.text().strip(),
                    'procargs': self.pi24_procargs.text().strip(),
                },
                'mlat': self._value('mlat'),
                'mlat_without_gps': self._value('mlat_without_gps'),
            }
            proposed = copy.deepcopy(self.config)
            proposed.update(copy.deepcopy(submitted))
            proposed = validate(proposed)
        except (ValueError, TypeError) as error:
            self._notify(str(error), True)
            return

        def saved(_value):
            if self.feed_mode.currentData() != submitted['feed_mode']:
                index = self.feed_mode.findData(submitted['feed_mode'])
                if index >= 0:
                    self.feed_mode.setCurrentIndex(index)
            self._dirty.discard('feed_mode')
            current_pi24 = {key: control.currentData() if isinstance(control, QComboBox)
                            else control.isChecked() if isinstance(control, QCheckBox)
                            else control.text().strip()
                            for key, control in self.pi24_fields.items()}
            if current_pi24 == submitted['pi24']:
                self._dirty.discard('pi24')
            self.config = proposed
            self.refresh_settings()
            for key in ('mlat','mlat_without_gps'):
                if self._value(key) == submitted[key]:
                    self._dirty.discard(key)
            self._notify('Pi24 profile saved. Only the MLAT settings apply to the Windows uploader on its next start.')

        self._request('/api/settings', {'settings': proposed}, saved, button)

    def _apply_pi24_gain(self):
        if self._value('receiver') != 'usb':
            self._notify('Select USB RTL-SDR under Receiver settings before copying a Pi24 gain value.', True)
            return
        try:
            options = self.pi24_procargs.text().strip()
            validate({**self.config, 'pi24': {**self.config['pi24'], 'procargs': options}})
        except (KeyError, TypeError, ValueError) as error:
            self._notify(str(error), True)
            return
        gain = float(options.split()[1])
        self.fields['gain'].setValue(gain)
        self._notify(f'Copied {gain:g} dB into the unsaved Windows receiver gain. Save Receiver settings to use it.')

    def _apply_pi24_windows_settings(self):
        if self._value('receiver') != 'usb':
            self._notify('Select USB RTL-SDR under Receiver settings before applying Pi24 values.', True)
            return
        pi24_values = {key: control.currentData() if isinstance(control, QComboBox)
                       else control.isChecked() if isinstance(control, QCheckBox)
                       else control.text().strip()
                       for key, control in self.pi24_fields.items()}
        try:
            validate({**self.config, 'pi24': pi24_values})
        except (TypeError, ValueError) as error:
            self._notify(str(error), True)
            return
        enable_raw = pi24_values['raw']
        enable_sbs = pi24_values['bs']
        if (enable_raw or enable_sbs) and self._value('bind_interface') == '0.0.0.0':
            self._notify('Set Data stream access to This PC only before enabling RAW or SBS listeners.', True)
            return
        gain = float(pi24_values['procargs'].split()[1])
        local_logmode = 0 if pi24_values['logmode'] == 0 else 1
        try:
            validate({**self.config, 'pi24': pi24_values, 'gain': gain,
                      'raw': enable_raw, 'bs': enable_sbs, 'logmode': local_logmode,
                      'receiver': self._value('receiver'), 'raw_port': self._value('raw_port'),
                      'bs_port': self._value('bs_port'), 'beast': self._value('beast'),
                      'beast_port': self._value('beast_port'),
                      'bind_interface': self._value('bind_interface')})
        except (TypeError, ValueError) as error:
            self._notify(str(error), True)
            return
        self.fields['gain'].setValue(gain)
        self.fields['raw'].setChecked(enable_raw)
        self.fields['bs'].setChecked(enable_sbs)
        self.fields['logmode'].setCurrentIndex(self.fields['logmode'].findData(local_logmode))
        changed = ('gain', 'raw', 'bs', 'logmode')
        self._dirty.update(changed)
        self._notify('Copied to unsaved controls. Save gain on Receiver and save the output/log switches under Data & settings before restarting.')

    def _keep_sample(self):
        if self._current_sample:
            self.baseline_summary.setText('Baseline: ' + self._current_sample)
            self._baseline_rate = self._sample_rate

    def _select_gain_step(self, index):
        value = self.gain_steps.itemData(index)
        if value is not None and self._gain_options and self._gain_options[0] == self._value('device'):
            self.fields['gain'].setValue(value)
        self.gain_steps.setCurrentIndex(0)

    def _update_quality(self, status, state):
        quality = status.get('quality') or {}
        source = status.get('input', '')
        options = (quality.get('device_index'), tuple(quality.get('gain_steps', ())))
        if self._gain_options != options:
            self._gain_options = options
            self.gain_steps.clear()
            self.gain_steps.addItem('Use a reported tuner gain step…')
            for value in options[1]:
                self.gain_steps.addItem(f'{value:g} dB', value)
        self.gain_steps.setVisible(bool(options[1]) and options[0] == self._value('device'))
        def metric(key, suffix):
            value = quality.get(key)
            return 'unavailable' if value is None else f'{value}{suffix}'
        sample = None
        self._sample_rate = None
        if state == 'receiving' and quality and source != 'sbs-tcp':
            seconds = quality['seconds']
            settings = quality.get('settings', '')
            if quality.get('reported_gain'):
                settings += f", device reports {quality['reported_gain']}"
            sample = (f"{source} {settings} · {seconds}s: {quality['accepted_rate']}/s accepted, "
                      f"{quality['mode_s']-quality['accepted']} rejected by app, {quality['mode_ac']} Mode A/C. "
                      f"Mean level {metric('mean_dbfs',' dBFS')}; strong (≥ −3 dBFS) {metric('strong_pct','%')}; "
                      f"reported corrections {metric('corrected_pct','%')}; "
                      f"accepted without correction {metric('clean_rate','/s')}.")
            current = (source, quality['accepted_rate'])
            comparison = ''
            if seconds >= 60 and self._baseline_rate and source == self._baseline_rate[0]:
                change = current[1] - self._baseline_rate[1]
                comparison = f' Change from baseline: {change:+.2f} accepted/s.'
            if source == 'iq-file':
                comparison += ' Playback wall-clock rate, not a live RF rate.'
            self.quality_summary.setText(sample + comparison)
            if seconds < 60:
                sample = None
            else:
                self._sample_rate = current
        else:
            self.quality_summary.setText('SBS contains decoded text, not RF quality measurements.'
                if source == 'sbs-tcp' else 'No active receiver sample.')
        self._current_sample = sample
        self.baseline_button.setEnabled(sample is not None)
        capture = status.get('capture') or {}
        archive = (f"Archive: {capture.get('written',0)} written · {capture.get('pending',0)} queued · "
                   f"{capture.get('dropped',0)} dropped. {capture.get('error','')}"
                   if capture.get('enabled') else 'Local archive off.')
        self.capture_summary.setText(archive + (' Local profile: message-capture/' if capture.get('enabled') else ''))

    def update_status(self, status):
        if not isinstance(status, dict):
            return
        state = status.get('state', 'stopped')
        self._running = state in ('starting', 'receiving', 'reconnecting')
        text = 'Receiver: ' + str(state)
        if status.get('error'):
            text += ' · ' + str(status['error'])
        self.receiver_state.setText(text)
        self._update_quality(status, state)
        feeder = status.get('feeder') or {}
        monitor = feeder.get('monitor') or {}
        reported_receiver = monitor.get('cfg_receiver') or monitor.get('cfg-receiver')
        self._update_feed_mode_note(reported_receiver)
        feed = monitor.get('feed_status') or monitor.get('feed-status') or 'Not confirmed'
        mlat = monitor.get('mlat_status') or monitor.get('mlat-status') or {
            'YES': 'Active', 'NO': 'Not active', 'NOT-PERMITTED': 'Not permitted by service',
        }.get(monitor.get('mlat-ok')) or monitor.get('mlat_problem') or 'Not confirmed'
        summary = f"Feeder: {feeder.get('state', 'stopped')}   ·   Upload: {feed}   ·   MLAT: {mlat}"
        if feeder.get('error'):
            summary += '\n' + str(feeder['error'])
        self.feeder_summary.setText(summary)
        monitor_text = '\n'.join(f'{key} = {value}' for key, value in sorted(monitor.items())) or 'No monitor values available.'
        if monitor_text != self._last_monitor:
            self.monitor_text.setPlainText(monitor_text)
            self._last_monitor = monitor_text
        self._sync_buttons()

    def _sync_buttons(self):
        if not hasattr(self, 'answer_button'):
            return
        editable = not self._running and not self._signup_running and not self._settings_requests
        for button in self._protected_buttons:
            button.setEnabled(editable and button not in self._busy_buttons)
        self.apply_button.setEnabled(self._import_preview is not None and editable and self.apply_button not in self._busy_buttons)
        self.start_button.setEnabled(not self._running and not self._signup_running and self.start_button not in self._busy_buttons)
        self.stop_button.setEnabled(self._running and self.stop_button not in self._busy_buttons)
        self.key_clear_button.setEnabled(self.has_key and editable and self.key_clear_button not in self._busy_buttons)
        self.cancel_signup_button.setEnabled(self._signup_running and self.cancel_signup_button not in self._busy_buttons)
        self.signup_answer.setEnabled(self._signup_running and self.answer_button not in self._busy_buttons)
        self.answer_button.setEnabled(self._signup_running and self.answer_button not in self._busy_buttons)

    def _notify(self, text, error=False):
        self.banner.setText(str(text))
        self.banner.setProperty('error', error)
        self.banner.setStyleSheet('color: #b42318; padding: 8px;' if error else 'color: #08776a; padding: 8px;')
        self.banner.show()
        self.notice.emit(str(text), error)

    def _request(self, path, data=None, callback=None, button=None):
        if self._closed:
            return
        mutation = data is not None and path in ('/api/settings', '/api/key', '/api/import/apply')
        if mutation:
            self._settings_requests += 1
            self._settings_generation += 1
        if button is not None:
            self._busy_buttons.add(button)
            button.setEnabled(False)
        self._sync_buttons()
        def done(value, error=None):
            if self._closed:
                return
            if mutation:
                self._settings_requests -= 1
            if button is not None:
                self._busy_buttons.discard(button)
                button.setEnabled(True)
            self._sync_buttons()
            if error is not None:
                self._notify(str(error), True)
                return
            if callback is not None:
                try:
                    callback(value)
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    self._notify(str(exc), True)
        try:
            self.invoke(path, data, done)
        except Exception as error:
            done(None, error)

    def _scan(self, button):
        self.device_note.setText('Scanning USB devices…')
        def received(value):
            self.device_list.clear()
            devices = value.get('devices') or []
            for device in devices:
                text = f"{device.get('FriendlyName', 'USB receiver')} · {device.get('Status', '')}\n{device.get('InstanceId', '')}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, device.get('index'))
                self.device_list.addItem(item)
            self.device_note.setText(value.get('error') or ('Select a device with a decoder index to use it.' if devices else
                'No matching USB radio found. Check its connection and USB permissions, then scan again.'))
            self._device_selection()
        self._request('/api/devices', callback=received, button=button)

    def _device_selection(self):
        item = self.device_list.currentItem()
        self.use_device_button.setEnabled(item is not None and item.data(Qt.ItemDataRole.UserRole) is not None)

    def _use_device(self):
        item = self.device_list.currentItem()
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        self.fields['device'].setText(str(item.data(Qt.ItemDataRole.UserRole)))
        self._edited('device')
        self.fields['receiver'].setCurrentIndex(self.fields['receiver'].findData('usb'))
        self._notify('USB decoder index selected. Save receiver settings to use it.')

    def _choose_recording(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose recorded I/Q file', '', 'Unsigned 8-bit I/Q (*.uc8);;All files (*)')
        if filename:
            self.fields['iq_file'].setText(filename)
            self._edited('iq_file')

    def _choose_ini(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose fr24feed.ini', '', 'INI files (*.ini);;All files (*)')
        if not filename:
            return
        try:
            path = Path(filename)
            if path.stat().st_size > 65536:
                raise ValueError('Choose an INI file smaller than 64 KB.')
            text = path.read_bytes()
            if len(text) > 65536:
                raise ValueError('Choose an INI file smaller than 64 KB.')
            self.ini_text.setPlainText(text.decode('utf-8-sig'))
        except (OSError, UnicodeError, ValueError) as error:
            self._notify(str(error), True)

    def _invalidate_import(self):
        self._import_preview = None
        self.import_result.hide()
        self._sync_buttons()

    def _preview_import(self, button):
        text = self.ini_text.toPlainText()
        if len(text.encode('utf-8')) > 65536:
            self._notify('Choose an INI file smaller than 64 KB.', True)
            return
        def previewed(value):
            if self.ini_text.toPlainText() != text:
                self._notify('The INI changed while previewing. Preview it again.', True)
                return
            self._import_preview = text
            warnings = value.get('warnings') or []
            key_status = 'yes · will be stored securely' if value.get('has_imported_key') else 'no'
            self.import_result.setPlainText(f"Input: {value['settings']['receiver']}\nSharing key in file: {key_status}\n\n" +
                                           ('\n'.join(warnings) if warnings else 'All imported options mapped.'))
            self.import_result.show()
            self._sync_buttons()
        self._request('/api/import', {'text': text}, previewed, button)

    def _apply_import(self, button):
        text = self.ini_text.toPlainText()
        if self._import_preview is None or text != self._import_preview:
            self._notify('Preview the changed INI before applying it.', True)
            return
        def applied(value):
            self.ini_text.clear()
            self._dirty.clear()
            self.refresh_settings(force=True)
            self._notify('Imported settings saved. ' + ' '.join(value.get('warnings') or []))
        self._request('/api/import/apply', {'text': text}, applied, button)

    def _save_key(self, button):
        key = self.key_edit.text().strip()
        if not key:
            self._notify('Paste a sharing key first, or use Remove stored key.', True)
            return
        def saved(value):
            self.key_edit.clear()
            self.refresh_settings()
            self._notify(self.key_storage)
        self._request('/api/key', {'key': key}, saved, button)

    def _clear_key(self, button):
        def cleared(value):
            self.key_edit.clear()
            self.refresh_settings()
            self._notify('Stored sharing key removed.')
        self._request('/api/key', {'key': ''}, cleared, button)

    def _start_signup(self, button):
        def started(value):
            self._signup_open = True
            self._signup_running = True
            self._sync_buttons()
            self._poll_signup()
            self._notify('Official registration started. Answer the prompts below.')
        self._request('/api/signup/start', {}, started, button)

    def _stop_signup(self, button):
        def stopped(value):
            self._signup_running = False
            self._signup_open = False
            self.signup_answer.clear()
            self._sync_buttons()
            self._poll_signup()
            self.refresh_settings()
            self._notify('Registration closed.')
        self._request('/api/signup/stop', {}, stopped, button)

    def _answer_signup(self):
        if not self._signup_running or self.answer_button in self._busy_buttons:
            return
        answer = self.signup_answer.text()
        def answered(value):
            self.signup_answer.clear()
            self.signup_answer.setFocus()
            self._poll_signup()
        self._request('/api/signup/answer', {'answer': answer}, answered, self.answer_button)

    def update_signup(self, value):
        text = value.get('text') or ('Waiting for the official wizard…' if value.get('running') else 'Registration is not running.')
        if text != self._last_signup_text:
            self.signup_text.setPlainText(text)
            self.signup_text.verticalScrollBar().setValue(self.signup_text.verticalScrollBar().maximum())
            self._last_signup_text = text
        was_running = self._signup_running
        self._signup_running = bool(value.get('running'))
        if was_running and not self._signup_running:
            self.signup_answer.clear()
            self._signup_open = False
            self.refresh_settings()
        self._sync_buttons()

    def _poll_signup(self):
        if self._signup_pending or self._closed:
            return
        self._signup_pending = True
        def done(value, error=None):
            self._signup_pending = False
            if self._closed:
                return
            if error is not None:
                self._notify(str(error), True)
            else:
                self.update_signup(value)
        try:
            self.invoke('/api/signup', None, done)
        except Exception as error:
            done(None, error)

    def refresh_logs(self, button=None):
        if self._logs_pending or self._closed:
            return
        self._logs_pending = True
        if button is not None:
            button.setEnabled(False)
        def done(value, error=None):
            self._logs_pending = False
            if self._closed:
                return
            if button is not None:
                button.setEnabled(True)
            if error is not None:
                self._notify(str(error), True)
                return
            text = '\n'.join(f"{line.get('time', '')} [{line.get('source', '')}] {line.get('message', '')}" for line in value) or 'No log events yet.'
            if text == self._last_logs:
                return
            scroll = self.log_output.verticalScrollBar()
            previous = scroll.value()
            self.log_output.setPlainText(text)
            scroll.setValue(scroll.maximum() if self.follow_logs.isChecked() else previous)
            self._last_logs = text
        try:
            self.invoke('/api/logs', None, done)
        except Exception as error:
            done(None, error)

    def _export(self, path, title, filename, filters, button):
        target, _ = QFileDialog.getSaveFileName(self, title, filename, filters)
        if not target:
            return
        def received(value):
            content = value.encode('utf-8') if isinstance(value, str) else value
            if not isinstance(content, bytes):
                raise ValueError('The export did not contain a file.')
            Path(target).write_bytes(content)
            self._notify('Saved ' + str(Path(target)))
        self._request(path, callback=received, button=button)

    def set_page(self, name):
        name = {'advanced': 'settings', 'data': 'settings', 'flightradar24': 'sharing'}.get(name, name)
        widget = self.pages.get(name)
        if widget is not None:
            self.tabs.setCurrentWidget(widget)

    def _page_changed(self, index):
        if self.tabs.currentWidget() is self.pages.get('logs'):
            self.refresh_logs()
        elif self.tabs.currentWidget() is self.pages.get('sharing') and self._signup_open:
            self._poll_signup()

    def _poll_auxiliary(self):
        if self._signup_open:
            self._poll_signup()
        if self.isVisible() and self.tabs.currentWidget() is self.pages.get('logs'):
            self.refresh_logs()

    def stop_polling(self):
        self._closed = True
        self._timer.stop()

    def pause_polling(self):
        """Pause new auxiliary polls while pending callbacks remain usable."""
        self._timer.stop()

    def resume_polling(self):
        """Resume after a cancelled or failed application shutdown."""
        if not self._closed:
            self._timer.start()

    def closeEvent(self, event):
        self.stop_polling()
        super().closeEvent(event)


SettingsPages = NativePages
