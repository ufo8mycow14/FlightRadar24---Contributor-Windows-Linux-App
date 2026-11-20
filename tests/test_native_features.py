"""Native feature integration, reflow and persisted receiver-independent controls."""
from __future__ import annotations
import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'work/applibs'),str(ROOT/'work/desktoplibs')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QCoreApplication,QEvent,Qt
from PySide6.QtWidgets import QApplication,QDialog
from frw.activity import AlertEngine
from frw.config import Settings
from frw.desktop import FlightRadarWindow
from frw.features import FEATURE_DEFAULT,FILTER_DEFAULT
from frw.native_features import AirportDialog,FilterEditor,RuleDialog
from frw.native_map import _MapCanvas
from frw.runtime import Receiver

class NativeFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def wait(self,predicate,timeout=5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.app.processEvents()
            if predicate():return
            time.sleep(.005)
        self.fail('Native feature operation timed out')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='frw-native-features-')
        self.receiver=Receiver(Settings(Path(self.temp.name)),ROOT/'work/runtime')
        self.network=patch.object(_MapCanvas,'_pump_tiles');self.network.start()
        self.window=FlightRadarWindow(self.receiver)
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.show()
        self.wait(lambda:self.window.feature_model.loaded and bool(self.window.last_status))
        self.window.timer.stop();self.window.activation_timer.stop()

    def tearDown(self):
        self.window.close();self.wait(lambda:self.window._can_close)
        self.window.deleteLater();self.app.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.network.stop();self.temp.cleanup()

    def test_filter_updates_all_airspace_views_and_persists_without_radio_mutation(self):
        self.window._set_list_detached(True)
        self.window.rows=[{'icao':'ABC123','callsign':'ONE','altitude':1000,'latitude':0,'longitude':0},
                          {'icao':'ABC124','callsign':'TWO','altitude':2000,'latitude':1,'longitude':1}]
        before=copy.deepcopy(self.receiver.settings.value)
        self.window.feature_model.save({'filter_enabled':True,'filter':{**FILTER_DEFAULT,'altitude_max':1500}})
        self.wait(lambda:not self.window.feature_model.pending)
        self.assertEqual(self.window.table.rowCount(),1)
        self.assertEqual([r['icao'] for r in self.window.map.canvas.rows],['ABC123'])
        self.assertEqual([r['icao'] for r in self.window.map.canvas.rows],['ABC123'])
        self.assertEqual(self.receiver.settings.value,before)
        self.assertTrue((Path(self.temp.name)/'features.json').exists())
        self.window.feature_model.save({'filter_enabled':False})
        self.wait(lambda:not self.window.feature_model.pending)
        self.assertEqual(self.window.table.rowCount(),2)
        self.window._set_list_detached(False)

    def test_statistics_received_counts_airport_reference_and_empty_recovery(self):
        now=time.time()
        config=self.receiver.activity.settings.value
        airport={'code':'TEST','name':'Test reference','latitude':0,'longitude':0,'radius_nm':5}
        config={**config,'airports':[airport]}
        self.receiver.activity.save(config)
        self.receiver.activity.store.write([(now,'live',{'icao':'ABC123','callsign':'TEST','altitude':0,'groundspeed':0,'latitude':0,'longitude':0,'data_source':'ADS-B'})],[],config,(0,0))
        page=self.window.statistics_page
        self.window.tabs.setCurrentWidget(page)
        self.wait(lambda:bool(page.report) and not page.pending)
        self.assertEqual(page.report['summary']['messages'],1)
        self.assertEqual(page.encounters.rowCount(),1)
        self.assertEqual(page.airports.rowCount(),1)
        self.assertTrue(page.traffic.points)
        page.mode.setCurrentIndex(1)
        self.wait(lambda:not page.pending and page.report.get('mode')=='playback')
        self.assertEqual(page.report['summary']['messages'],0)
        self.assertEqual(page.encounters.rowCount(),0)

    def test_rule_dialog_preserves_zero_limits_and_requires_a_condition(self):
        dialog=RuleDialog(parent=self.window)
        dialog.save()
        self.assertNotEqual(dialog.result(),QDialog.DialogCode.Accepted)
        dialog.editor.controls['altitude_max'].setText('0')
        dialog.save()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.rule['filter']['altitude_max'],0)
        dialog.deleteLater()

    def test_local_notifications_deduplicate_and_quiet_hours_suppress_sound(self):
        event={'id':'example-event','title':'TEST','body':'Fixture','channels':['sound','desktop'],'quiet':False,'time':time.time(),'rule':'TEST','callsign':'','icao':'ABC123','mode':'playback','delivery':'Saved in app'}
        with patch.object(self.window,'_notify') as notify:
            self.window._process_alerts({'activity':{'events':[event]}})
            self.window._process_alerts({'activity':{'events':[event]}})
            notify.assert_called_once_with(event)
        self.assertEqual(self.window.alerts_page.recent.item(0,2).text(),'ABC123')
        self.assertEqual(self.window.alerts_page.recent.item(0,3).text(),'Recorded playback')
        with patch('frw.desktop.QApplication.beep') as beep:
            self.window._notify({**event,'quiet':True})
            beep.assert_not_called()

    def test_airport_catalog_worldwide_search_and_manual_coordinate_validation(self):
        dialog=AirportDialog(parent=self.window)
        self.assertGreater(len(dialog.catalog),5000)
        for code in ('YPAD','KJFK','EGLL','RJTT'):
            dialog.search.setText(code)
            self.assertTrue(any(a['code']==code for a in dialog.found),code)
        for key,value in {'code':'NULL','name':'Zero coordinate reference','latitude':'0','longitude':'0','radius_nm':'10'}.items():
            dialog.fields[key].setText(value)
        dialog.save()
        self.assertEqual(dialog.airport['latitude'],0)
        dialog.deleteLater()

    def test_new_screens_fit_minimum_window_and_secrets_remain_password_fields(self):
        self.window.resize(920,650)
        for page in (self.window.statistics_page,self.window.alerts_page,self.window.pages):
            self.window.tabs.setCurrentWidget(page);self.app.processEvents()
            self.assertLessEqual(page.width(),self.window.width())
        prefs=self.window.feature_preferences
        self.window.show_page('features');self.app.processEvents()
        self.assertGreater(prefs.verticalScrollBar().maximum(),0)
        for control in prefs.secrets.values():
            self.assertEqual(control.echoMode(),control.EchoMode.Password)

if __name__=='__main__':unittest.main()
