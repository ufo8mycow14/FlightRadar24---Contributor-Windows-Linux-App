"""Local waypoint source selection; importing never uploads the chosen file."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                              QLabel, QLineEdit, QPushButton, QVBoxLayout)


class WaypointDialog(QDialog):
    catalogChanged = Signal(object)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle('Aviation waypoint data')
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        text = QLabel('Use WPT on the map or View → Aviation waypoints to show or hide the layer. '
                      'Zoom in for named markers; hover for coordinates and source.\n\n'
                      'The bundled Australia-region fixes are a historical October 2013 reference, '
                      'not current AIRAC data. Import a newer file you are entitled to use if needed. '
                      'Imports stay in this profile and are never uploaded. This layer is not for navigation.')
        text.setWordWrap(True)
        layout.addWidget(text)
        self.cycle = QLineEdit()
        self.cycle.setMaxLength(80)
        self.cycle.setPlaceholderText('Source cycle/date for the next import (optional, user supplied)')
        self.cycle.setAccessibleName('Imported waypoint source cycle or date')
        layout.addWidget(self.cycle)
        format_text = QLabel('CSV columns: ident,latitude,longitude (decimal degrees). '
                             'X-Plane / FlightGear fix.dat and .dat.gz are also supported. '
                             'Imports replace the selected layer; 12 MB / 100,000 records maximum.')
        format_text.setWordWrap(True)
        layout.addWidget(format_text)
        row = QHBoxLayout()
        for title, callback in [('Import waypoint file…', self.import_file),
                                ('Use historical reference', lambda: self.change(self.store.use_bundled)),
                                ('Use last import', lambda: self.change(self.store.use_imported))]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.feedback = QLabel(store.error)
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.feedback)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def refresh(self):
        catalog = self.store.catalog()
        self.summary.setText(f'{catalog.title}\n{len(catalog.points):,} waypoints · {catalog.caption}\nSource: {catalog.source}')

    def change(self, operation):
        try:
            catalog = operation()
            self.catalogChanged.emit(catalog)
            self.feedback.setText('Waypoint data selected. The map visibility preference is unchanged.')
            self.refresh()
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.feedback.setText('Could not load waypoints: ' + str(error))

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import waypoint file', '',
                    'Waypoint files (*.csv *.dat *.gz);;All files (*)')
        if path:
            self.change(lambda: self.store.import_file(path, self.cycle.text()))
