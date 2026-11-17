"""Searchable native support centre and an isolated, in-memory practice workspace."""
from __future__ import annotations

import copy
import time
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QVBoxLayout,
    QHBoxLayout, QLabel, QLineEdit, QComboBox, QListWidget, QListWidgetItem,
    QTextBrowser, QSplitter, QWidget, QPushButton, QFileDialog, QTabWidget,
    QFormLayout, QScrollArea, QSpinBox, QPlainTextEdit, QCheckBox)
from . import AUTHOR, PROJECT_URL, SOURCE_LICENSE, __version__
from .config import validate
from .features import FILTER_DEFAULT, validate_features, matches
from .activity import alert_content
from .native_features import FilterEditor, label, button, table, fill_table
from .support import (topics, search_topics, article_html, guide_markdown,
                      support_summary, example_receiver, example_aircraft)


def heading(text):
    result = label(text)
    result.setStyleSheet('font-size: 22pt; font-weight: 600; color: #e8edf4;')
    return result


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('FlightRadar24 · Support centre')
        self.resize(1060, 780)
        self.setMinimumSize(720, 520)
        self.current_topic = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.addWidget(heading('How can we help?'))
        layout.addWidget(label('Offline guides, clear examples and practical answers for your receiver.'))
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setAccessibleName('Search all help topics')
        self.search.setPlaceholderText('Search the entire guide — try gain, email, storage or MLAT')
        self.search.setClearButtonEnabled(True)
        self.search.setMaxLength(200)
        search_row.addWidget(self.search, 1)
        self.category = QComboBox()
        self.category.setAccessibleName('Help category')
        self.category.addItems(['All topics'] + list(dict.fromkeys(t['category'] for t in topics())))
        search_row.addWidget(self.category)
        layout.addLayout(search_row)
        self.result_count = label('')
        layout.addWidget(self.result_count)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.results = QListWidget()
        self.results.setAccessibleName('Help search results')
        self.results.setMinimumWidth(210)
        self.results.setWordWrap(True)
        self.results.setStyleSheet('QListWidget::item { padding: 10px 8px; }')
        self.article = QTextBrowser()
        self.article.setAccessibleName('Help article')
        self.article.setOpenLinks(False)
        self.article.setOpenExternalLinks(False)
        self.article.document().setDefaultStyleSheet('''
            body { color:#e8edf4; font-family: "Segoe UI", sans-serif; }
            h1 { font-size:24px; color:#ffffff; } h2 { font-size:18px; color:#79dbc8; }
            p, li { line-height:145%; } td { border-bottom:1px solid #30485d; }
            a { color:#8dcfff; }''')
        self.article.setStyleSheet('QTextBrowser { background:#101b27; border:1px solid #30485d; padding:16px; }')
        split.addWidget(self.results)
        split.addWidget(self.article)
        split.setSizes([280, 700])
        layout.addWidget(split, 1)
        controls = QHBoxLayout()
        controls.addWidget(button('Practice with dummy data', self.practice))
        controls.addWidget(button('Save full guide…', self.save_guide))
        controls.addWidget(button('Copy article', self.copy_article))
        controls.addStretch()
        controls.addWidget(button('A−', lambda: self.article.zoomOut(1)))
        controls.addWidget(button('A+', lambda: self.article.zoomIn(1)))
        controls.addWidget(button('Close', self.close))
        layout.addLayout(controls)
        self.message = label('F1 opens help · Ctrl+K searches · Tab moves between controls')
        layout.addWidget(self.message)
        self.search.textChanged.connect(self.refresh)
        self.category.currentIndexChanged.connect(self.refresh)
        self.results.currentItemChanged.connect(self.show_article)
        self.article.anchorClicked.connect(lambda url: self.open_topic(url.path()) if url.scheme() == 'help' else None)
        shortcut = QShortcut(QKeySequence('Ctrl+K'), self)
        shortcut.activated.connect(self.focus_search)
        self.refresh()

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def refresh(self, *_):
        selected_id = self.current_topic['id'] if self.current_topic else None
        found = search_topics(self.search.text(), self.category.currentText())
        self.results.blockSignals(True)
        self.results.clear()
        selected_row = 0
        for n, topic in enumerate(found):
            item = QListWidgetItem(topic['title'])
            item.setData(Qt.ItemDataRole.UserRole, topic)
            item.setToolTip(topic['summary'])
            self.results.addItem(item)
            if topic['id'] == selected_id:
                selected_row = n
        self.results.blockSignals(False)
        self.result_count.setText(f'{len(found)} of {len(topics())} guides · Search includes steps, fields and examples')
        if found:
            self.results.setCurrentRow(selected_row)
        else:
            self.current_topic = None
            self.article.setHtml('<h1>No matching guides</h1><p>Try fewer words or choose All topics. Examples: USB, callsign, sound, storage, email.</p>')

    def show_article(self, item, previous=None):
        if item is not None:
            self.current_topic = item.data(Qt.ItemDataRole.UserRole)
            self.article.setHtml(article_html(self.current_topic))

    def open_topic(self, topic_id):
        self.search.clear()
        self.category.setCurrentIndex(0)
        for n in range(self.results.count()):
            if self.results.item(n).data(Qt.ItemDataRole.UserRole)['id'] == topic_id:
                self.results.setCurrentRow(n)
                return

    def practice(self):
        PracticeDialog(self).exec()

    def copy_article(self):
        if self.current_topic:
            QApplication.clipboard().setText(guide_markdown([self.current_topic]))
            self.message.setText('Article copied as text. No information was submitted.')

    def save_guide(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save offline user guide', 'FlightRadar24-user-guide.md', 'Markdown (*.md)')
        if path:
            try:
                Path(path).write_text(guide_markdown(), encoding='utf-8')
                self.message.setText('Full user guide saved.')
            except OSError as error:
                self.message.setText('Could not save guide: ' + str(error))


class PracticeDialog(QDialog):
    """No Controller, Receiver, FeatureModel or settings paths are accepted here."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('FlightRadar24 · Practice with dummy data')
        self.resize(1000, 820)
        self.setMinimumSize(720, 540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(heading('Learn by trying'))
        banner = label('DUMMY DATA · In-memory practice only. Changes stay here; no receiver, uploads, notifications or profile writes.')
        banner.setStyleSheet('background:#25445b; color:#baf2e6; padding:10px; border-radius:5px;')
        layout.addWidget(banner)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._receiver_page()
        self._filter_page()
        self._alert_page()
        self._storage_page()
        self.feedback = label('')
        layout.addWidget(self.feedback)
        controls = QHBoxLayout()
        controls.addWidget(button('Reset examples', self.reset))
        controls.addStretch()
        controls.addWidget(button('Close practice', self.close))
        layout.addLayout(controls)
        self.reset()

    def page(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        scroll.setWidget(body)
        self.tabs.addTab(scroll, title)
        return layout

    def _receiver_page(self):
        layout = self.page('1 · Receiver')
        layout.addWidget(label('Use your own antenna location in the real settings. These coordinates are an arbitrary Atlantic point.'))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.receiver_fields = {}
        for key, title in [('device','USB decoder index'), ('latitude','Latitude (degrees)'),
                           ('longitude','Longitude (degrees)'), ('altitude_ft','Antenna altitude AMSL (feet)')]:
            edit = QLineEdit()
            edit.setAccessibleName('Practice ' + title)
            edit.setMaxLength(80)
            self.receiver_fields[key] = edit
            form.addRow(title, edit)
        layout.addLayout(form)
        layout.addWidget(label('Input: USB RTL-SDR · Sample rate: 2.4 MHz · Gain: automatic (−10) · Uploading: off'))
        layout.addWidget(button('Check example', self.check_receiver))
        layout.addWidget(label('Expected result: a valid sample profile. The button validates values without opening USB or saving a setting.'))
        layout.addStretch()

    def _filter_page(self):
        layout = self.page('2 · Filters')
        layout.addWidget(label('Try DEMO* with maximum altitude 10000 ft. Change it to 20000 ft to include a second dummy aircraft.'))
        self.filter_editor = FilterEditor()
        layout.addWidget(self.filter_editor)
        layout.addWidget(button('Preview matches', self.preview_filter))
        self.filter_result = label('')
        layout.addWidget(self.filter_result)
        self.aircraft_table = table(['Callsign', 'ICAO24', 'Altitude ft', 'Speed kt', 'Matches'])
        self.aircraft_table.setMinimumHeight(160)
        layout.addWidget(self.aircraft_table)

    def _alert_page(self):
        layout = self.page('3 · Alert')
        layout.addWidget(label('Preview an alert for DEMO101. The real Alerts screen adds matching criteria, channels and cooldown behaviour.'))
        form = QFormLayout()
        self.rule_name = QLineEdit()
        self.rule_name.setMaxLength(80)
        self.rule_name.setAccessibleName('Practice rule name')
        self.cooldown = QSpinBox()
        self.cooldown.setRange(30, 86400)
        self.cooldown.setSuffix(' seconds')
        self.cooldown.setAccessibleName('Practice repeat interval')
        form.addRow('Rule name', self.rule_name)
        form.addRow('Minimum repeat interval', self.cooldown)
        form.addRow(label('Example recipient: observer@example.invalid\nExample webhook: https://example.invalid/aircraft-alerts\nThese are deliberately non-deliverable examples.'))
        layout.addLayout(form)
        layout.addWidget(button('Preview alert text', self.preview_alert))
        self.alert_preview = QPlainTextEdit()
        self.alert_preview.setReadOnly(True)
        self.alert_preview.setAccessibleName('Synthetic alert preview')
        self.alert_preview.setMinimumHeight(280)
        layout.addWidget(self.alert_preview, 1)

    def _storage_page(self):
        layout = self.page('4 · Storage')
        layout.addWidget(label('Try a smaller history budget. Real storage settings remove oldest history when age or size limits are reached.'))
        form = QFormLayout()
        self.storage_fields = {}
        for key, title, lo, hi, suffix in [('retention_days','Keep history for',1,3650,' days'),
            ('storage_mb','Database + journal budget',8,4096,' MiB'),
            ('sample_seconds','Sample each aircraft every',5,300,' seconds'),
            ('log_max_mb','Diagnostic log budget',1,1024,' MiB'),
            ('log_days','Keep rotated logs for',1,365,' days')]:
            field = QSpinBox()
            field.setRange(lo,hi)
            field.setSuffix(suffix)
            field.setAccessibleName('Practice ' + title)
            self.storage_fields[key] = field
            form.addRow(title,field)
        layout.addLayout(form)
        layout.addWidget(button('Check storage example', self.check_storage))
        self.storage_result = label('')
        layout.addWidget(self.storage_result)
        layout.addWidget(label('The map cache adds a separate fixed 64 MiB budget. Exports, backups and recordings are outside these budgets.'))
        layout.addStretch()

    def reset(self):
        config = example_receiver()
        for key, field in self.receiver_fields.items():
            field.setText(str(config[key]))
        self.filter_editor.set_value({**FILTER_DEFAULT, 'callsigns':'DEMO*', 'altitude_max':10000})
        self.rule_name.setText('Demo low aircraft')
        self.cooldown.setValue(900)
        for key,value in dict(retention_days=30,storage_mb=256,sample_seconds=15,log_max_mb=20,log_days=3).items():
            self.storage_fields[key].setValue(value)
        self.preview_filter()
        self.preview_alert()
        self.check_storage()
        self.feedback.setText('Examples loaded. Everything in this window is fictional and temporary.')

    def check_receiver(self):
        try:
            values = {key: field.text().strip() if key == 'device' else float(field.text())
                      for key,field in self.receiver_fields.items()}
            validate({**example_receiver(), **values})
        except ValueError as error:
            self.feedback.setText('Example needs attention: ' + str(error))
            return
        self.feedback.setText('Valid receiver example. Nothing saved or connected.')

    def preview_filter(self):
        try:
            criteria = self.filter_editor.value()
        except ValueError as error:
            self.filter_result.setText('Check the filter: ' + str(error))
            self.aircraft_table.setRowCount(0)
            return
        rows = [{**row, 'match':'Yes' if matches(row,criteria,(0,-30)) else 'No'} for row in example_aircraft()]
        fill_table(self.aircraft_table, rows, ['callsign','icao','altitude','groundspeed','match'])
        self.filter_result.setText(f'{sum(row["match"] == "Yes" for row in rows)} of 3 dummy aircraft match. Missing altitude does not match an altitude limit.')

    def preview_alert(self):
        name = self.rule_name.text().strip()
        rule = dict(id='practice-rule',name=name,enabled=True,filter={**FILTER_DEFAULT,'icaos':'A00001'},
                    channels=['in_app'],cooldown_seconds=self.cooldown.value(),include_playback=False)
        try:
            validate_features({'rules':[rule]})
        except ValueError as error:
            self.alert_preview.setPlainText('Check the example rule: ' + str(error))
            return
        title, body = alert_content(rule, example_aircraft()[0], time.time(), 'live')
        self.alert_preview.setPlainText('[PRACTICE · SYNTHETIC · NOT SENT]\n' + title + '\n\n' + body)

    def check_storage(self):
        config = validate_features({key: field.value() for key,field in self.storage_fields.items()})
        total = config['storage_mb'] + config['log_max_mb'] + 64
        self.storage_result.setText(f'Valid example: {config["storage_mb"]} MiB history + {config["log_max_mb"]} MiB logs + 64 MiB map cache = {total} MiB managed budgets. No files were created or pruned.')


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('About FlightRadar24')
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.addWidget(heading('FlightRadar24'))
        layout.addWidget(label(f'Version {__version__} · Native desktop'))
        author = label(AUTHOR)
        author.setStyleSheet('font-size:18pt; color:#79dbc8; padding-top:12px;')
        layout.addWidget(author)
        layout.addWidget(label('Open-source software · ' + SOURCE_LICENSE))
        layout.addWidget(label('Copyright © 2026 ' + AUTHOR))
        layout.addWidget(label('Receive nearby ADS-B / Mode S traffic, explore the map, watch aircraft, and understand your reception with bounded local statistics.'))
        layout.addWidget(label('Project source is open source. Dependencies retain their licences; the optional official feeder is proprietary and excluded from the project licence. No warranty under the applicable licence.'))
        layout.addWidget(label('Independent community project. Not affiliated with or endorsed by Flightradar24 AB.'))
        address = label(PROJECT_URL)
        address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(address)
        layout.addWidget(button('Copy GitHub project address', lambda: QApplication.clipboard().setText(PROJECT_URL)))
        layout.addWidget(button('Licence and third-party guide', self.licence))
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        controls.rejected.connect(self.close)
        layout.addWidget(controls)

    def licence(self):
        help_dialog = HelpDialog(self)
        help_dialog.open_topic('about')
        help_dialog.exec()
