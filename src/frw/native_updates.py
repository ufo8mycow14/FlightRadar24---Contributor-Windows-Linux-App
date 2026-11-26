"""Background update checks and a native, opt-in installation interface."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox,
                              QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout)

from . import __version__
from .updates import (UpdatePreferences, check_release, download_release,
                      installation_supported, prepare_update)


class UpdateManager(QObject):
    changed = Signal()
    completed = Signal(str, object, object)
    downloadProgress = Signal(int, int)
    installReady = Signal(object)

    def __init__(self, directory, parent=None, *, enabled=None):
        super().__init__(parent)
        self.directory = Path(directory)
        self.preferences = UpdatePreferences(directory)
        self.supported = installation_supported() if enabled is None else enabled
        self.release = self.installer = None
        self.busy = self.closed = False
        self.status = 'Checks GitHub every 12 hours while the app is running.' if self.supported else 'In-app updates require the packaged Windows application.'
        self.progress = 0
        self.countdown = None
        self.can_install = lambda: True
        self.resume_receiver = lambda: False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='updates')
        self.completed.connect(self._complete)
        self.downloadProgress.connect(self._progress)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self._next_due_poll = 0
        # Persisted install result is informational only, never executable data.
        try:
            result = json.loads((self.directory/'updates/install-result.json').read_text(encoding='utf-8-sig'))
            if result.get('success') is False:
                self.status = 'Last update did not complete. Open the update installation log to inspect the failure.'
        except (OSError, ValueError, AttributeError):
            pass
        if self.supported:
            self._next_due_poll = time.monotonic()+5

    def tick(self):
        if self.closed or not self.supported:
            return
        if self.countdown is not None:
            if not self.can_install():
                self.countdown = 60
                self.status = 'Update ready. Automatic restart is deferred while you edit settings or use a dialog.'
            else:
                self.countdown -= 1
                self.status = f'Update {self.release.version} installs in {self.countdown}s. Open Updates to postpone.'
                if self.countdown <= 0:
                    self.countdown = None
                    self.install()
            self.changed.emit()
        now = time.monotonic()
        if now >= self._next_due_poll:
            self._next_due_poll = now + 60
            if not self.busy and self.countdown is None and self.preferences.due():
                self.check()

    def save_preferences(self, checks, automatic):
        self.preferences.value.update(check_automatically=bool(checks), install_automatically=bool(checks and automatic))
        self.preferences.save()
        if not automatic or not checks:
            if self.countdown is not None:
                self.status = 'Automatic installation disabled. The downloaded update remains available to install manually.'
            self.countdown = None
        elif self._automatic_allowed() and not self.busy:
            if self.installer:
                self.countdown = 60
                self.status = f'Update {self.release.version} installs in 60s. Open Updates to postpone.'
            else:
                self.download()
        self.changed.emit()

    def _run(self, operation, function):
        if self.closed or self.busy:
            return
        self.busy = True
        self.changed.emit()
        def work():
            try:
                value, error = function(), None
            except Exception as exc:
                value, error = None, exc
            self.completed.emit(operation, value, error)
        self.pool.submit(work)

    def check(self):
        if not self.supported or self.busy or self.closed:
            return
        self.countdown = None
        try:
            # Record attempts too, so offline/rate-limited hosts do not spin.
            self.preferences.value['last_check'] = time.time()
            self.preferences.save()
        except OSError as error:
            self.status = 'Could not save the update-check schedule: '+str(error)
            self.changed.emit()
            return
        self.status = 'Checking published GitHub releases…'
        self._run('check', lambda: check_release(__version__))

    def download(self):
        if self.release is None or self.busy or self.closed:
            return
        self.status = f'Downloading {self.release.version}… Reception continues.'
        self.progress = 0
        self._run('download', lambda: download_release(self.release, self.directory/'updates', self.downloadProgress.emit))

    def _progress(self, received, total):
        if not self.closed:
            self.progress = int(received*100/max(1,total))
            self.changed.emit()

    def _complete(self, operation, value, error):
        if self.closed:
            return
        self.busy = False
        if error:
            self.status = 'Update '+operation+' failed: '+str(error)
            self.countdown = None
        elif operation == 'check':
            self.release = value
            self.installer = None
            self.status = f'Version {value.version} is available.' if value else f'You are up to date ({__version__}).'
            if value and self._automatic_allowed():
                self.download()
        elif operation == 'download':
            self.installer = value
            self.progress = 100
            self.status = f'Version {self.release.version} downloaded and SHA-256 verified. Ready to install.'
            if self._automatic_allowed():
                self.countdown = 60
        self.changed.emit()

    def _automatic_allowed(self):
        value = self.preferences.value
        return (self.release and value['check_automatically'] and value['install_automatically']
                and value['auto_attempted_version'] != self.release.version)

    def postpone(self):
        self.countdown = None
        if self.release:
            self.preferences.value['auto_attempted_version'] = self.release.version
            try:
                self.preferences.save()
            except OSError as error:
                self.status = 'Postponed for this session; could not save the preference: '+str(error)
                self.changed.emit()
                return
        self.status = 'Automatic installation postponed for this version. You can install it manually here.'
        self.changed.emit()

    def install(self):
        if self.busy or self.closed or not self.release:
            return
        if self.installer is None:
            self.download()
            return
        try:
            self.preferences.value['auto_attempted_version'] = self.release.version
            self.preferences.save()
            plan = prepare_update(self.release,self.installer,self.directory,self.resume_receiver())
        except (OSError, ValueError) as error:
            self.status = 'Could not prepare installation: '+str(error)
            self.changed.emit()
            return
        self.countdown = None
        self.installReady.emit(plan)

    def finish(self):
        self.closed = True
        self.timer.stop()
        self.pool.shutdown(wait=False,cancel_futures=True)


class UpdateDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle('Software updates')
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'FlightRadar24 · installed version {__version__}'))
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.checks = QCheckBox('Check GitHub for updates every 12 hours')
        self.automatic = QCheckBox('Automatically download and install updates')
        self.checks.setChecked(manager.preferences.value['check_automatically'])
        self.automatic.setChecked(manager.preferences.value['install_automatically'])
        self.automatic.setEnabled(self.checks.isChecked())
        self.checks.toggled.connect(self.automatic.setEnabled)
        layout.addWidget(self.checks)
        layout.addWidget(self.automatic)
        note = QLabel('Checks run while the app is open; overdue checks run at startup. '
                      'Automatic installation is optional and gives a 60-second restart countdown. '
                      'Reception pauses during installation and resumes if it was running. '
                      'Saved settings and history are retained. You can postpone any version.')
        note.setWordWrap(True)
        layout.addWidget(note)
        privacy = QLabel('Updates come from this project’s public GitHub releases. Checks send no receiver settings, keys or aircraft data. '
                         'GitHub receives the connection IP address. Installers are verified against GitHub’s SHA-256 digest; Windows prompts still apply.')
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.save = QPushButton('Save preferences')
        self.save.clicked.connect(self._save)
        self.check = QPushButton('Check now')
        self.check.clicked.connect(manager.check)
        self.install_button = QPushButton()
        self.install_button.clicked.connect(self._install)
        self.postpone_button = QPushButton('Postpone this version')
        self.postpone_button.clicked.connect(manager.postpone)
        for button in (self.save,self.check,self.install_button,self.postpone_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        manager.changed.connect(self.refresh)
        self.finished.connect(lambda _: manager.changed.disconnect(self.refresh))
        self.refresh()

    def _save(self):
        try:
            self.manager.save_preferences(self.checks.isChecked(),self.automatic.isChecked())
            if not self.checks.isChecked():
                self.automatic.setChecked(False)
        except OSError as error:
            self.manager.status = 'Could not save update preferences: '+str(error)
        self.refresh()

    def _install(self):
        if self.manager.installer:
            self.accept()
        self.manager.install()

    def refresh(self):
        manager = self.manager
        self.status.setText(manager.status)
        self.progress.setValue(manager.progress)
        self.check.setEnabled(manager.supported and not manager.busy)
        self.install_button.setText('Install and restart' if manager.installer else 'Download update')
        self.install_button.setEnabled(manager.supported and manager.release is not None and not manager.busy)
        self.postpone_button.setEnabled(manager.release is not None)
