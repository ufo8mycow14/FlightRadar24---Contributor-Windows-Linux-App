"""Native preferences for the Flightradar24 live-data display API."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                              QDoubleSpinBox, QFormLayout, QLabel,
                              QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)
from .config import ConfigError


class Fr24Dialog(QDialog):
    """Edit API display settings without exposing a stored token."""

    def __init__(self, overlay, parent=None):
        super().__init__(parent)
        self.overlay = overlay
        settings = overlay.preferences()
        self.setWindowTitle('Flightradar24 API display')
        self.setMinimumWidth(560)
        self._has_token = bool(settings.get('has_token', False))
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        layout.addWidget(QLabel('Use your API subscription token, not your receiver sharing key.'))

        self.enabled = QCheckBox('Enable Flightradar24 API aircraft on the map')
        self.enabled.setChecked(bool(settings.get('enabled', False)))
        layout.addWidget(self.enabled)

        form = QFormLayout()
        self.token = QLineEdit()
        self.token.setMaxLength(4096)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText('Leave blank to keep the saved API token')
        self.token.setAccessibleName('Flightradar24 API token')
        form.addRow('API token', self.token)
        self.token_status = QLabel('A token is saved securely.' if self._has_token else 'No API token is saved.')
        self.token_status.setWordWrap(True)
        self.token_status.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow('', self.token_status)
        self.remove_token_button = QPushButton('Remove saved token')
        self.remove_token_button.setEnabled(self._has_token)
        form.addRow('', self.remove_token_button)

        self.endpoint = QComboBox()
        self.endpoint.addItem('Light live positions · lower credit use', 'light')
        self.endpoint.addItem('Full live positions · higher credit use', 'full')
        index = self.endpoint.findData(settings.get('endpoint', 'full'))
        self.endpoint.setCurrentIndex(max(0, index))
        form.addRow('Data detail', self.endpoint)

        self.interval = QSpinBox()
        self.interval.setRange(15, 3600)
        self.interval.setValue(int(settings.get('interval_seconds', 60)))
        self.interval.setSuffix(' seconds')
        form.addRow('Refresh interval', self.interval)

        self.use_bounds = QCheckBox('Use a bounded map area for API requests')
        bounds = settings.get('bounds', '')
        has_bounds = bool(bounds)
        if isinstance(bounds, str):
            try:
                north, south, west, east = (float(value) for value in bounds.split(','))
                bounds = dict(north=north, south=south, west=west, east=east)
            except (TypeError, ValueError):
                bounds = {}
        if not bounds:
            bounds = dict(north=1, south=-1, west=-1, east=1)
        self.use_bounds.setChecked(has_bounds)
        form.addRow('', self.use_bounds)
        self.bounds = {}
        for key, title, low, high in (
            ('north', 'North latitude', -90, 90),
            ('south', 'South latitude', -90, 90),
            ('west', 'West longitude', -180, 180),
            ('east', 'East longitude', -180, 180),
        ):
            control = QDoubleSpinBox()
            control.setRange(low, high)
            control.setDecimals(3)
            control.setSingleStep(1.0)
            control.setValue(float(bounds.get(key, {'north': 1, 'south': -1,
                                                    'west': -1, 'east': 1}[key])))
            control.setAccessibleName(title)
            self.bounds[key] = control
            form.addRow(title, control)

        self.limit = QSpinBox()
        self.limit.setRange(1, 5000)
        self.limit.setValue(int(settings.get('limit', 20)))
        self.limit.setSuffix(' aircraft maximum')
        form.addRow('Result cap', self.limit)
        self.request_rate = QLabel()
        form.addRow('Request rate', self.request_rate)
        layout.addLayout(form)

        explanation = QLabel(
            'Choose your own area; the initial box around 0°, 0° is an example. '
            'Matching aircraft appear once. Receiver positions take priority. '
            'API data stays on screen: it is not uploaded, forwarded, recorded or exported.'
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        attribution = QLabel('Data provided by Flightradar24 · Use is subject to your API subscription and Flightradar24 terms.')
        attribution.setWordWrap(True)
        layout.addWidget(attribution)
        self.credit_estimate = QLabel()
        self.credit_estimate.setWordWrap(True)
        layout.addWidget(self.credit_estimate)

        self.remove_token = False
        self.remove_token_button.clicked.connect(self._remove_saved_token)
        self.enabled.toggled.connect(self._set_enabled)
        self.use_bounds.toggled.connect(self._set_bounds_enabled)
        self.interval.valueChanged.connect(self._update_request_rate)
        self.interval.valueChanged.connect(self._update_credit_estimate)
        self.limit.valueChanged.connect(self._update_credit_estimate)
        self.endpoint.currentIndexChanged.connect(self._update_credit_estimate)
        self.enabled.toggled.emit(self.enabled.isChecked())
        self.use_bounds.toggled.emit(self.use_bounds.isChecked())
        self._update_request_rate()
        self._update_credit_estimate()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.resize(620, min(740, self.screen().availableGeometry().height() - 80))

    def _set_enabled(self, enabled):
        for control in (self.endpoint, self.use_bounds):
            control.setEnabled(enabled)
        self._set_bounds_enabled(self.use_bounds.isChecked())

    def _set_bounds_enabled(self, enabled):
        for control in self.bounds.values():
            control.setEnabled(bool(enabled and self.enabled.isChecked()))

    def _update_request_rate(self):
        self.request_rate.setText(f"Up to {3600 / self.interval.value():.1f} requests per hour while enabled")

    def _update_credit_estimate(self):
        credits_per_aircraft = 6 if self.endpoint.currentData() == 'light' else 8
        per_hour = self.limit.value() * credits_per_aircraft * 3600 / self.interval.value()
        self.credit_estimate.setText(
            f'At the {self.limit.value()} aircraft cap: up to {per_hour:,.0f} credits/hour '
            f'({credits_per_aircraft} credits per returned aircraft). Empty responses cost 1 credit. '
            'Indicative estimate; actual usage depends on returned aircraft and current API terms.'
        )

    def accept(self):
        settings = self.preferences()
        current = self.overlay.preferences()
        comparable = {key: value for key, value in current.items() if key != 'has_token'}
        token = self.token_action()
        settings.pop('has_token', None)
        if settings == comparable and token is None:
            super().accept()
            return
        try:
            self.overlay.configure(settings, token)
        except ConfigError as error:
            self.token_status.setText(str(error))
            return
        except OSError:
            self.token_status.setText('Could not save API settings or token. Check profile storage and try again.')
            return
        self.token.clear()
        self.token_status.setText('API preferences saved.')
        super().accept()

    def _remove_saved_token(self):
        self.remove_token = True
        self._has_token = False
        self.enabled.setChecked(False)
        self.token.clear()
        self.token_status.setText('Saved token will be removed and the API overlay disabled when you save.')
        self.remove_token_button.setEnabled(False)

    def preferences(self):
        """Return non-secret settings, suitable for the settings file."""
        return {
            'enabled': self.enabled.isChecked(),
            'endpoint': self.endpoint.currentData(),
            'interval_seconds': self.interval.value(),
            'bounds': (','.join(format(control.value(), '.7g') for control in
                               (self.bounds['north'], self.bounds['south'],
                                self.bounds['west'], self.bounds['east']))
                       if self.use_bounds.isChecked() else ''),
            'limit': self.limit.value(),
            'has_token': self._has_token,
        }

    def token_action(self):
        """Return a token to set, an empty string to remove, or None to keep."""
        value = self.token.text().strip()
        if value:
            return value
        if self.remove_token:
            return ''
        return None

    def reject(self):
        self.token.clear()
        super().reject()
