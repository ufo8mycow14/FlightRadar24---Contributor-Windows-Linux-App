"""Render documentation screenshots from isolated native widgets, never a live profile."""
from pathlib import Path
import os
import sys

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root/'src'), str(root/'work/desktoplibs'), str(root/'work/applibs')]
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from frw.desktop import apply_desktop_style
from frw.native_support import HelpDialog, PracticeDialog

app = QApplication([])
if os.name == 'nt':
    # The offscreen plugin does not enumerate Windows fonts itself.
    for name in ('segoeui.ttf','segoeuib.ttf'):
        font_path = Path(os.environ['WINDIR'])/'Fonts'/name
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Segoe UI',10))
apply_desktop_style(app)
output = root/'docs/images'
output.mkdir(parents=True,exist_ok=True)
help_dialog = HelpDialog()
help_dialog.show()
app.processEvents()
assert help_dialog.grab().save(str(output/'support-centre.png'))
help_dialog.close()
practice = PracticeDialog()
practice.tabs.setCurrentIndex(2)
practice.show()
app.processEvents()
assert practice.grab().save(str(output/'practice-alert.png'))
practice.close()
print('Rendered two native documentation images from offline dummy data.')
