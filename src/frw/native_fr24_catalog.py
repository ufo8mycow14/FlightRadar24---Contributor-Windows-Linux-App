"""On-demand, read-only browser for documented Flightradar24 API endpoints."""
from __future__ import annotations

import json

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .fr24_catalog import BY_KEY, ENDPOINTS, MAX_RESPONSE_CHARS, make_query


class _ResultSignals(QObject):
    done = Signal(object, object)


class _QueryTask(QRunnable):
    def __init__(self, overlay, path, params, signals):
        super().__init__()
        self.overlay, self.path, self.params, self.signals = overlay, path, params, signals

    def run(self):
        try:
            value = self.overlay.request_json(self.path, self.params)
            self.signals.done.emit(value, None)
        except Exception as error:
            self.signals.done.emit(None, error)


class Fr24CatalogDialog(QDialog):
    """Issue one bounded API query at a time; render the complete response locally."""

    def __init__(self, overlay, parent=None):
        super().__init__(parent)
        self.overlay = overlay
        self.fields = {}
        self.busy = False
        self.pool = QThreadPool.globalInstance()
        self.signals = _ResultSignals()
        self.signals.done.connect(self._finished)
        self.setWindowTitle("Flightradar24 API data")
        self.resize(900, 700)
        self.setMinimumSize(650, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Browse data available through your Flightradar24 API subscription."))
        layout.addWidget(QLabel("Queries use API credits. Automatic map requests pause while this window is open."))
        self.endpoint = QComboBox()
        self.endpoint.setAccessibleName("Flightradar24 API endpoint")
        for spec in ENDPOINTS:
            self.endpoint.addItem(spec.title, spec.key)
        self.endpoint.currentIndexChanged.connect(self._fields_changed)
        layout.addWidget(self.endpoint)
        self.description = QLabel()
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.form_widget = QWidget()
        self.form = QFormLayout()
        self.form_widget.setLayout(self.form)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setWidget(self.form_widget)
        layout.addWidget(self.form_scroll, 1)
        row = QHBoxLayout()
        self.run_button = QPushButton("Run query")
        self.run_button.clicked.connect(self.run_query)
        self.status = QLabel("Ready")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        row.addWidget(self.run_button)
        row.addWidget(self.status, 1)
        layout.addLayout(row)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output.setPlaceholderText("Query results will appear here.")
        self.output.setAccessibleName("Flightradar24 API response")
        layout.addWidget(self.output, 2)
        attribution = QLabel("Data provided by Flightradar24 API · API access requires a separate subscription and token.")
        attribution.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(attribution)
        self._fields_changed()

    def _fields_changed(self, *_):
        self.output.clear()
        self.status.setText("Ready")
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields.clear()
        spec = BY_KEY[self.endpoint.currentData()]
        self.description.setText(f"{spec.description}\nCost: {spec.credits}")
        for key, label, required in spec.fields:
            if key == "period":
                edit = QComboBox()
                edit.addItems(("24h", "7d", "30d", "1y"))
            else:
                edit = QLineEdit()
                edit.setMaxLength(200)
                edit.setPlaceholderText("Required" if required else "Optional")
            edit.setAccessibleName(label)
            self.fields[key] = edit
            self.form.addRow(label + (" *" if required else ""), edit)
        try:
            settings = self.overlay.preferences()
        except Exception:
            settings = {}
        if not isinstance(settings, dict):
            settings = {}
        for key, edit in self.fields.items():
            if key == "limit":
                value = settings.get("limit", 20)
                edit.setText(str(value if type(value) is int and 1 <= value <= 5000 else 20))
                edit.setPlaceholderText("Uses API settings result cap")
            elif key == "bounds" and settings.get("bounds"):
                edit.setText(str(settings["bounds"]))
            elif key == "period":
                edit.setCurrentText("24h")

    def run_query(self):
        if self.busy:
            return
        if 'limit' in self.fields and not self.fields['limit'].text().strip():
            self.status.setText('Enter a result cap before running this query.')
            return
        try:
            path, params = make_query(self.endpoint.currentData(),
                                      {key: (widget.currentText() if isinstance(widget, QComboBox) else widget.text())
                                       for key, widget in self.fields.items()})
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.busy = True
        self.endpoint.setEnabled(False)
        self.form_widget.setEnabled(False)
        self.run_button.setEnabled(False)
        self.status.setText("Requesting Flightradar24 data…")
        self.output.clear()
        self.pool.start(_QueryTask(self.overlay, path, params, self.signals))

    def _finished(self, value, error):
        self.busy = False
        self.endpoint.setEnabled(True)
        self.form_widget.setEnabled(True)
        self.run_button.setEnabled(True)
        if error is not None:
            self.status.setText(str(error))
            return
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        if len(text) > MAX_RESPONSE_CHARS:
            self.status.setText(f"Response exceeded the {MAX_RESPONSE_CHARS:,}-character display limit.")
            return
        self.output.setPlainText(text)
        self.status.setText("Response received. Check the endpoint's credit and retention terms.")
