"""Behavioural checks for filters, alerts, bounded history and safe delivery."""
from __future__ import annotations
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'work/applibs'),str(ROOT/'work/desktoplibs')]
from frw.features import FEATURE_DEFAULT,FILTER_DEFAULT,FeatureSettings,matches,validate_features,validate_filter
from frw.activity import ActivityService,AlertEngine,HistoryStore,deliver_event,quiet_now
from frw.aircraft import Aircraft
from frw.config import validate,ConfigError,Settings
from frw.control import Controller
from frw.runtime import Receiver,EventLog
from frw.streams import Frame

def rule(**changes):
    value={'id':'test-rule','name':'Low nearby aircraft','enabled':True,'filter':{**FILTER_DEFAULT,'altitude_max':1000},
        'channels':['in_app','sound','desktop'],'cooldown_seconds':300,'include_playback':False}
    value.update(changes)
    return value

def observation(icao='ABC123',**changes):
    return {'icao':icao,'callsign':'TEST123','altitude':800,'groundspeed':100,'latitude':0,'longitude':0,
        'track':0,'vertical_rate':0,'on_ground':False,'squawk':'1200','data_source':'ADS-B',**changes}

class FilterTests(unittest.TestCase):
    def test_exact_prefix_or_and_unknown_values(self):
        criteria=validate_filter({'callsigns':'ABC*, TEST123','icaos':'ABC123,ABC124','altitude_min':0,'altitude_max':1000,'speed_max':100})
        self.assertTrue(matches(observation(),criteria,(0,0)))
        self.assertFalse(matches(observation(callsign='XTEST123'),criteria,(0,0)))
        self.assertFalse(matches(observation(altitude=None),criteria,(0,0)))
        self.assertFalse(matches(observation(icao='ABC125'),criteria,(0,0)))
        self.assertTrue(matches(observation(altitude=0,groundspeed=0),criteria,(0,0)))

    def test_range_zero_coordinates_and_date_line(self):
        criteria=validate_filter({'distance_max':20,'positioned':True})
        self.assertTrue(matches(observation(latitude=0,longitude=-179.9),criteria,(0,179.9)))
        self.assertFalse(matches(observation(latitude=0,longitude=-179.9),criteria,(None,None)))
        self.assertFalse(matches(observation(latitude=None),criteria,(0,0)))

    def test_squawk_emergency_ground_and_source(self):
        criteria=validate_filter({'squawks':'7700,7600','emergency':True,'state':'ground','source':'ADS-B'})
        self.assertTrue(matches(observation(squawk='7700',on_ground=True),criteria))
        self.assertFalse(matches(observation(squawk='7700',on_ground=False),criteria))
        self.assertFalse(matches(observation(squawk='7700',on_ground=None),criteria))
        self.assertFalse(matches(observation(squawk='7700',on_ground=True,data_source='Mode S'),criteria))

    def test_invalid_rules_and_nonfinite_limits_rejected(self):
        for changes in [{'altitude_min':1000,'altitude_max':0},{'speed_max':float('nan')},{'squawks':'8888'}, {'callsigns':'*TEST*'}, {'icaos':'ABC*'}]:
            with self.assertRaises(ConfigError):validate_filter(changes)
        with self.assertRaises(ConfigError):validate_features({'rules':[rule(filter=copy.deepcopy(FILTER_DEFAULT))]})
        with self.assertRaises(ConfigError):validate_features({'quiet_start':'25:99','quiet_end':'06:00'})

    def test_expired_measurements_are_not_given_to_history_or_rules(self):
        captured=[]
        aircraft=Aircraft(validate({}),lambda row,stamp:captured.append(row))
        aircraft._update('ABC123',{'df':17,'typecode':9,'altitude':0},1000)
        aircraft._update('ABC123',{'df':17,'typecode':1,'callsign':'TEST123'},1061)
        self.assertEqual(captured[0]['altitude'],0)
        self.assertNotIn('altitude',captured[-1])

class RuleTests(unittest.TestCase):
    def setUp(self):
        self.config=validate_features({'alerts_enabled':True,'rules':[rule()]})

    def test_entering_cooldown_reentry_and_repeated_reports(self):
        engine=AlertEngine()
        epoch=1700000000
        self.assertEqual(len(engine.process(observation(),epoch,'live',self.config,(0,0))),1)
        self.assertFalse(engine.process(observation(),epoch+1,'live',self.config,(0,0)))
        self.assertFalse(engine.process(observation(altitude=2000),epoch+2,'live',self.config,(0,0)))
        self.assertFalse(engine.process(observation(),epoch+3,'live',self.config,(0,0)))
        engine.process(observation(altitude=2000),epoch+301,'live',self.config,(0,0))
        self.assertEqual(len(engine.process(observation(),epoch+302,'live',self.config,(0,0))),1)
        self.assertEqual(len(engine.process(observation(),epoch+603,'live',self.config,(0,0))),1)

    def test_playback_opt_in_and_alert_message(self):
        engine=AlertEngine()
        self.assertFalse(engine.process(observation(),1700000000,'playback',self.config,(0,0)))
        self.config['rules'][0]['include_playback']=True
        event=engine.process(observation(altitude=0,vertical_rate=0),1700000001,'playback',self.config,(0,0))[0]
        self.assertIn('[PLAYBACK]',event['title'])
        self.assertIn('Altitude: 0 ft',event['body'])
        self.assertIn('Position: 0.000000, 0.000000',event['body'])
        self.assertIn('Vertical rate: 0 ft/min',event['body'])

    def test_quiet_hours_cross_midnight_and_global_burst_limit(self):
        self.config.update(quiet_start='22:00',quiet_end='06:00')
        timestamp=dt.datetime(2026,11,17,23,0).timestamp()
        self.assertTrue(quiet_now(self.config,timestamp))
        engine=AlertEngine()
        for index in range(25):
            events=engine.process(observation(f'{index:06X}'),timestamp,'live',self.config,(0,0))
            if index<20:self.assertTrue(events[0]['quiet'])
            else:self.assertFalse(events)
        self.assertEqual(engine.suppressed,5)

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='frw-features-')
        self.directory=Path(self.temporary.name)
        self.config=validate_features({'storage_mb':8,'airports':[{'code':'TEST','name':'Reference','latitude':0,'longitude':0,'radius_nm':5}]})
        self.store=HistoryStore(self.directory)
        self.now=time.time()

    def tearDown(self):self.temporary.cleanup()

    def test_exact_totals_unique_aircraft_encounters_and_airport_proximity(self):
        events=[(self.now+i,'live',observation()) for i in range(3)]
        events += [(self.now+3,'live',observation('ABC124')),(self.now+1300,'live',observation())]
        events += [(self.now,'playback',observation('ABC125'))]
        self.store.write(events,[],self.config,(0,0))
        report=self.store.report(self.config,24,'live',now=self.now+1400)
        self.assertEqual(report['summary']['messages'],5)
        self.assertEqual(report['summary']['aircraft'],2)
        self.assertEqual(report['summary']['encounters'],3)
        self.assertEqual(report['airports'][0]['encounters'],3)
        self.assertEqual(report['summary']['closest_nm'],0)
        self.assertEqual(sum(p['messages'] for p in report['traffic']),5)
        self.assertEqual(self.store.report(self.config,24,'playback',now=self.now+1400)['summary']['messages'],1)

    def test_retention_size_lowering_and_recording_off(self):
        self.store.write([(self.now-40*86400,'live',observation())],[],self.config,(0,0))
        self.store.write([(self.now,'live',observation('ABC124'))],[],self.config,(0,0))
        report=self.store.report(self.config,24,'live')
        self.assertEqual(report['summary']['messages'],1)
        self.assertLessEqual(report['storage_bytes'],8*1048576)
        self.config['history_enabled']=False
        self.store.write([(self.now,'live',observation('ABC125'))],[],self.config,(0,0))
        self.assertEqual(self.store.report(self.config)['summary']['messages'],1)

    def test_sustained_storage_pressure_stays_bounded_and_accepts_recent_data(self):
        # Populate many identities/samples rather than an artificial file truncate.
        for batch in range(80):
            self.store.write([(self.now+batch*3600+i/100,'live',observation(f'{batch*300+i:06X}')) for i in range(300)],[],self.config,(0,0))
            self.assertLessEqual(self.store.size(),self.config['storage_mb']*1048576)
        report=self.store.report(self.config,87600,now=self.now+81*3600)
        self.assertGreater(report['summary']['messages'],0)
        self.assertLess(report['summary']['messages'],24000)
        self.assertTrue(any(r['icao']==f'{79*300+299:06X}' for r in report['encounters']))
        connection=sqlite3.connect(self.store.path)
        try:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally:
            connection.close()

    def test_log_budget_and_age_are_independent_of_history(self):
        log=EventLog(self.directory/'logs',2);log.set_limits(1,1)
        for _ in range(1100):log.add('receiver','x'*3000)
        log.close()
        files=list((self.directory/'logs').glob('receiver.log*'))
        self.assertLessEqual(sum(p.stat().st_size for p in files),1048576)
        self.assertLessEqual(len(files),4)

    def test_queued_service_flushes_and_filters_do_not_drop_statistics(self):
        service=ActivityService(self.directory)
        service.settings.save({**self.config,'filter_enabled':True,'filter':{**FILTER_DEFAULT,'icaos':'FFFFFF'},'alerts_enabled':True,'rules':[rule()]})
        service.start(validate({}))
        for index in range(4):service.submit(observation(),self.now+index)
        service.stop()
        report=service.store.report(service.settings.value)
        self.assertEqual(report['summary']['messages'],4)
        self.assertEqual(len(service.snapshot()['events']),1)
        self.assertFalse(service.thread.is_alive())
        self.assertFalse(service.delivery_thread.is_alive())

    def test_feature_credentials_are_protected_and_not_returned(self):
        settings=FeatureSettings(self.directory)
        settings.save(self.config,{'smtp_password':'fixture-password','webhook_url':'https://example.org/topic'})
        self.assertEqual(settings.secrets()['smtp_password'],'fixture-password')
        self.assertNotIn('fixture-password',json.dumps(settings.public()))
        if os.name=='nt':self.assertNotIn(b'fixture-password',settings.secret_path.read_bytes())
        self.assertNotIn('fixture-password',settings.path.read_text())

    def test_alert_cooldown_survives_application_restart(self):
        config={**self.config,'alerts_enabled':True,'rules':[rule()]}
        service=ActivityService(self.directory);service.settings.save(config)
        service.start(validate({}));service.submit(observation(),time.time());time.sleep(.6);service.stop()
        self.assertEqual(len(service.snapshot()['events']),1)
        replacement=ActivityService(self.directory)
        replacement.start(validate({}));replacement.submit(observation(),time.time());time.sleep(.6);replacement.stop()
        self.assertFalse(replacement.snapshot()['events'])

    def test_disk_failure_does_not_block_receiver_callback_and_is_reported(self):
        service=ActivityService(self.directory)
        with patch.object(service.store,'write',side_effect=sqlite3.OperationalError('fixture disk failure')):
            service.start(validate({}));service.submit(observation(),self.now);service.stop()
        self.assertGreaterEqual(service.snapshot()['dropped_reports'],1)
        self.assertIn('storage unavailable',service.snapshot()['error'])

    def test_controller_features_save_live_does_not_touch_radio_configuration(self):
        receiver=Receiver(Settings(self.directory),ROOT/'work/runtime')
        controller=Controller(receiver,lambda:None)
        before=copy.deepcopy(receiver.settings.value)
        result=controller.execute('/api/features','POST',{'settings':self.config})
        self.assertEqual(result.status,200)
        self.assertEqual(receiver.settings.value,before)
        report=controller.execute('/api/statistics?hours=1&mode=playback')
        self.assertEqual(report.data['mode'],'playback')
        self.assertEqual(report.data['hours'],1)
        self.assertEqual(controller.execute('/api/statistics?hours=bad').status,400)
        diagnostics=controller.diagnostics()
        self.assertNotIn(b'webhook_url',diagnostics)
        receiver.stop();receiver.log.close()

class DeliveryTests(unittest.TestCase):
    def test_smtp_uses_tls_and_email_content(self):
        config=validate_features({'delivery':{**FEATURE_DEFAULT['delivery'],'email_enabled':True,'smtp_host':'mail.example.org','smtp_user':'fixture','smtp_from':'sender@example.org','email_to':'target@example.org'}})
        event={'title':'Test alert','body':'Fixture observations','channels':['email']}
        with patch('frw.activity.smtplib.SMTP') as factory:
            result=deliver_event(event,config,{'smtp_password':'fixture-password'})
            client=factory.return_value
            client.starttls.assert_called_once()
            client.login.assert_called_once_with('fixture','fixture-password')
            message=client.send_message.call_args.args[0]
            self.assertEqual(message['To'],'target@example.org')
            self.assertIn('accepted',result)

    def test_webhook_format_disable_redirects_and_redact_failure(self):
        config=validate_features({'delivery':{**FEATURE_DEFAULT['delivery'],'webhook_enabled':True,'webhook_kind':'discord'}})
        event={'title':'@everyone TEST','body':'Fixture','channels':['webhook']}
        with patch('frw.activity.urllib.request.build_opener') as factory:
            opener=factory.return_value
            opener.open.return_value.__enter__.return_value.status=204
            result=deliver_event(event,config,{'webhook_url':'https://example.org/secret-fixture'})
            request=opener.open.call_args.args[0]
            self.assertEqual(json.loads(request.data)['allowed_mentions'],{'parse':[]})
            self.assertIn('accepted',result)
            opener.open.side_effect=OSError('URL https://example.org/secret-fixture token fixture-private')
            result=deliver_event(event,config,{'webhook_url':'https://example.org/secret-fixture'})
            self.assertNotIn('fixture-private',result)
            self.assertNotIn('secret-fixture',result)

if __name__=='__main__':unittest.main()
