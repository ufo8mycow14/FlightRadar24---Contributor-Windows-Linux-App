"""Exercise help search, complete references and isolated native practice behaviour."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'work/desktoplibs'), str(ROOT/'work/applibs')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from frw.support import topics, search_topics, article_html, guide_markdown, support_summary
from frw import __version__


class HelpContentTests(unittest.TestCase):
    def test_searches_body_examples_and_multiple_words(self):
        self.assertIn('email', {t['id'] for t in search_topics('SMTP 587')})
        self.assertIn('storage', {t['id'] for t in search_topics('journal')})
        self.assertEqual(search_topics('sdf987noresult'), [])
        self.assertTrue(all(t['category']=='Alerts' for t in search_topics('', 'Alerts')))

    def test_all_topics_and_links_render_with_shared_guide(self):
        data = topics()
        ids = {t['id'] for t in data}
        self.assertEqual(len(ids),len(data))
        self.assertGreaterEqual(len(ids),25)
        for topic in data:
            self.assertTrue(set(topic['related']) <= ids)
            self.assertIn('<h1>',article_html(topic))
        self.assertEqual((ROOT/'docs/USER-GUIDE.md').read_text(encoding='utf-8'),guide_markdown())

    def test_html_escapes_content_and_summary_is_allowlisted(self):
        sample = dict(id='x',category='Help',title='<img src="https://bad.invalid/x">',summary='A & B',sections=[],related=[])
        rendered = article_html(sample)
        self.assertNotIn('<img',rendered)
        self.assertIn('&lt;img',rendered)
        report = support_summary()
        self.assertIn(__version__,report)
        self.assertNotIn(str(Path.home()),report)
        self.assertNotIn('latitude',report.lower())


from PySide6.QtWidgets import QApplication
from frw.native_support import HelpDialog, PracticeDialog, AboutDialog
from frw.desktop import apply_desktop_style


class NativeSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_desktop_style(cls.app)

    def test_help_results_empty_recovery_and_internal_navigation(self):
        dialog = HelpDialog()
        try:
            dialog.search.setText('smtp 587')
            self.assertEqual(dialog.current_topic['id'],'email')
            dialog.search.setText('unknownphrase999')
            self.assertEqual(dialog.results.count(),0)
            self.assertIn('No matching guides',dialog.article.toPlainText())
            dialog.open_topic('filters')
            self.assertEqual(dialog.current_topic['id'],'filters')
            self.assertFalse(dialog.article.openExternalLinks())
            dialog.resize(720,520)
            dialog.show()
            self.app.processEvents()
            self.assertGreater(dialog.article.width(),300)
        finally:
            dialog.close()

    def test_practice_matches_validates_and_never_delivers_or_writes(self):
        with patch('socket.socket.connect',side_effect=AssertionError('Practice network access')), \
             patch('pathlib.Path.write_text',side_effect=AssertionError('Practice profile write')), \
             patch('frw.activity.deliver_event',side_effect=AssertionError('Practice delivery')):
            dialog = PracticeDialog()
            try:
                self.assertIn('1 of 3',dialog.filter_result.text())
                dialog.filter_editor.controls['altitude_max'].setText('20000')
                dialog.preview_filter()
                self.assertIn('2 of 3',dialog.filter_result.text())
                dialog.filter_editor.controls['altitude_max'].setText('invalid')
                dialog.preview_filter()
                self.assertEqual(dialog.aircraft_table.rowCount(),0)
                dialog.receiver_fields['latitude'].setText('91')
                dialog.check_receiver()
                self.assertIn('attention',dialog.feedback.text())
                dialog.receiver_fields['latitude'].setText('nan')
                dialog.check_receiver()
                self.assertIn('attention',dialog.feedback.text())
                dialog.reset()
                dialog.receiver_fields['altitude_ft'].setText('')
                dialog.check_receiver()
                self.assertIn('Valid',dialog.feedback.text())
                dialog.receiver_fields['latitude'].setText('')
                dialog.receiver_fields['longitude'].setText('')
                dialog.check_receiver()
                self.assertIn('Valid',dialog.feedback.text())
                dialog.reset()
                dialog.check_receiver()
                self.assertIn('Valid',dialog.feedback.text())
                self.assertIn('SYNTHETIC',dialog.alert_preview.toPlainText())
                self.assertIn('NOT SENT',dialog.alert_preview.toPlainText())
                self.assertIn('340 MiB',dialog.storage_result.text())
                dialog.resize(720,540)
                dialog.show()
                self.app.processEvents()
            finally:
                dialog.close()

    def test_about_shows_current_identity(self):
        dialog = AboutDialog()
        try:
            from PySide6.QtWidgets import QLabel
            text = '\n'.join(label.text() for label in dialog.findChildren(QLabel))
            self.assertIn(__version__,text)
            self.assertIn("Kieran O'Rourke",text)
            self.assertIn('Open-source',text)
            self.assertIn('proprietary',text)
        finally:
            dialog.close()


if __name__ == '__main__':
    unittest.main()
