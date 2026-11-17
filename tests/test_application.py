from pathlib import Path
import copy, io, json, os, socket, ssl, subprocess, sys, tempfile, threading, time, unittest, urllib.request, urllib.error, zipfile
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'work/applibs')]
from frw.config import DEFAULT, Settings, ConfigError, validate, parse_ini
from frw.migration import import_ini, export_ini
from frw.streams import Frame, AVRParser, BeastParser, Broadcast
from frw.aircraft import Aircraft
from frw.runtime import Receiver, decoder_config, cygpath, HIDDEN, free_port
from frw.server import ControlServer

MESSAGES=['8D40621D58C382D690C8AC2863A7','8D40621D58C386435CC412692AD6','8D4840D6202CC371C32CE0576098','8D485020994409940838175B284F']

class ProtocolTests(unittest.TestCase):
    def test_beast_split_and_escaping(self):
        f=Frame(bytes.fromhex(MESSAGES[0]),0x1a1234561a12,0x1a);wire=f.beast()
        p=BeastParser();out=[]
        for byte in wire:out+=p.feed(bytes([byte]))
        self.assertEqual(out,[f])
        self.assertEqual(p.feed(b'junk\x1a\x33bad'+wire),[f])
    def test_avr_timestamp_and_bound(self):
        p=AVRParser();f=Frame(bytes.fromhex(MESSAGES[0]),123456)
        self.assertEqual(p.feed(b'log output\n'+f.avr()[:7]),[])
        self.assertEqual(p.feed(f.avr()[7:]),[f])
        self.assertIsNone(Frame(f.data).beast())
        p.feed(b'x'*100000);self.assertLessEqual(len(p.buffer),8192)
    def test_aircraft_crc_position_identity(self):
        a=Aircraft(validate({}))
        for i in range(32):a.frame(Frame(bytes.fromhex(MESSAGES[i%4])),time.time()+i*.01)
        rows={r['icao']:r for r in a.snapshot()['aircraft']}
        self.assertEqual(len(rows),3)
        self.assertEqual(rows['4840D6']['callsign'].strip(),'KLM1023')
        # The odd CPR message is most recent in each pair.
        self.assertAlmostEqual(rows['40621D']['latitude'],52.26578,places=4)
        self.assertAlmostEqual(rows['40621D']['longitude'],3.93891,places=4)
        before=a.total;a.frame(Frame(bytes.fromhex(MESSAGES[0][:-2]+'00')))
        self.assertEqual(a.total,before);self.assertGreater(a.rejected,0)
    def test_broadcast_real_socket(self):
        b=Broadcast('127.0.0.1',0)
        try:
            with socket.create_connection(('127.0.0.1',b.port),timeout=2) as c:
                deadline=time.time()+2
                while not b.clients and time.time()<deadline:time.sleep(.01)
                b.publish(b'frame\r\n');self.assertEqual(c.recv(20),b'frame\r\n')
        finally:b.close()

class SettingsTests(unittest.TestCase):
    def test_cygwin_paths(self):
        self.assertEqual(cygpath(Path('C:/Users/Example/Flight Radar')), '/cygdrive/c/Users/Example/Flight Radar')
        self.assertEqual(cygpath(Path('//server/share/Flight Radar')), '//server/share/Flight Radar')
    def test_persistence_and_dpapi(self):
        with tempfile.TemporaryDirectory() as d:
            s=Settings(Path(d));s.save({**DEFAULT,'gain':28.0});s.save_key('a123456789abcdef')
            self.assertNotIn(b'a123456789abcdef',s.key_path.read_bytes())
            t=Settings(Path(d));self.assertEqual(t.key(),'a123456789abcdef');self.assertEqual(t.value['gain'],28)
            self.assertNotIn('a123456789abcdef',json.dumps(t.public()));t.save_key('');self.assertFalse(t.key_path.exists())
    def test_reject_conflicts_and_playback_upload(self):
        for values in ({'raw':True,'bs':True,'bs_port':30002},{'receiver':'iq-file','feed_enabled':True},{'gain':float('nan')},{'latitude':10},{'host':'http://example.com'},{'original':{'fr24key':'abcd'}}):
            with self.subTest(values=values),self.assertRaises(ConfigError):validate(values)
    def test_import_known_original(self):
        path=ROOT/'evidence/rootfs/etc/fr24feed.ini'
        c,key,warnings=import_ini(path.read_text(),validate({}))
        self.assertEqual(c['receiver'],'usb');self.assertEqual(c['gain'],-10);self.assertTrue(c['mlat']);self.assertTrue(warnings)
        self.assertNotIn('fr24key=',export_ini(c))
        with self.assertRaises(ConfigError):import_ini('receiver="uat-fr24"',c)
        with self.assertRaises(ConfigError):parse_ini('a="x"\na="y"')

class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.settings=Settings(Path(self.temp.name))
        self.receiver=Receiver(self.settings,ROOT/'work/runtime')
        self.server=ControlServer(('127.0.0.1',0),self.receiver,ROOT/'src/frw/web')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.receiver.feeder.signup_stop();self.receiver.stop();self.server.shutdown();self.thread.join(3);self.server.server_close();self.receiver.log.close();self.temp.cleanup()
    def request(self,path,data=None,auth=True,origin=None,host=None):
        headers={}
        if auth:headers['Authorization']='Bearer '+self.server.token
        if origin:headers['Origin']=origin
        if host:headers['Host']=host
        body=None
        if data is not None:body=json.dumps(data).encode();headers['Content-Type']='application/json'
        request=urllib.request.Request(self.server.url+path,data=body,headers=headers)
        return urllib.request.urlopen(request,timeout=15)
    def test_auth_and_origin(self):
        for kwargs in ({'auth':False},{'origin':'https://evil.example'},{'host':'evil.example'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(urllib.error.HTTPError) as caught:self.request('api/status',**kwargs)
            self.assertIn(caught.exception.code,(401,403))
        self.assertEqual(json.load(self.request('api/status'))['state'],'stopped')
        self.assertIn(b'FlightRadar24',self.request('').read())
    def test_api_persistence_and_diagnostics(self):
        values=validate({'gain':32,'latitude':-34.9,'longitude':138.6})
        self.request('api/settings',{'settings':values}).close()
        self.assertEqual(json.load(self.request('api/settings'))['gain'],32)
        self.request('api/key',{'key':'a123456789abcdef'}).close()
        archive=self.request('api/diagnostics').read()
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            combined=b''.join(z.read(n) for n in z.namelist())
            self.assertNotIn(b'a123456789abcdef',combined);self.assertNotIn(b'-34.9',combined)
    def test_selected_aircraft_endpoint(self):
        # Repeat the known capture sequence until CPR validation establishes a track.
        for index in range(32):
            self.receiver.aircraft.frame(Frame(bytes.fromhex(MESSAGES[index%4])),time.time()+index*.01)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('api/aircraft/40621D',auth=False)
        self.assertEqual(caught.exception.code,401)
        detail=json.load(self.request('api/aircraft/40621d'))
        self.assertEqual(detail['icao'],'40621D')
        self.assertEqual(detail['messages'],16)
        self.assertAlmostEqual(detail['latitude'],52.26578,places=4)
        self.assertTrue(detail['history'])
        self.assertNotIn('history',json.load(self.request('api/status'))['aircraft'][0])
        for address,expected in [('not-hex',400),('ABCDEF',404)]:
            with self.subTest(address=address),self.assertRaises(urllib.error.HTTPError) as caught:
                self.request('api/aircraft/'+address)
            self.assertEqual(caught.exception.code,expected)
        self.receiver.aircraft=Aircraft(self.settings.value)
        for payload in MESSAGES[:2]:
            self.receiver.aircraft.frame(Frame(bytes.fromhex(payload)),time.time()-120)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('api/aircraft/40621D')
        self.assertEqual(caught.exception.code,404)
    def test_native_recording_end_to_end(self):
        c=validate({'receiver':'iq-file','iq_file':str(ROOT/'work/decoder-test/synthetic.uc8'),'sample_rate':2000000,'error_correction':0,'raw':True,'raw_port':free_port()})
        self.settings.save(c);self.receiver.start()
        deadline=time.time()+12
        while self.receiver.thread.is_alive() and time.time()<deadline:time.sleep(.05)
        value=self.receiver.snapshot()
        self.assertEqual(value['state'],'playback complete',self.receiver.log.snapshot())
        self.assertEqual(value['frames'],48)
        self.assertEqual(len(value['aircraft']),3)
        self.assertEqual(value['feeder']['state'],'stopped')
        self.assertEqual(self.receiver.outputs,{})
        self.receiver.start();self.receiver.thread.join(12)
        self.assertEqual(self.receiver.snapshot()['frames'],48)
        self.assertEqual(self.receiver.outputs,{})
    def test_timestamped_tcp_reception_and_stop(self):
        source=Broadcast('127.0.0.1',0)
        try:
            self.settings.save(validate({'receiver':'beast-tcp','input_port':source.port}))
            self.receiver.start();deadline=time.time()+3
            while not source.clients and time.time()<deadline:time.sleep(.02)
            for i in range(20):source.publish(Frame(bytes.fromhex(MESSAGES[i%4]),100000+i*12000).beast())
            deadline=time.time()+3
            while self.receiver.frames<20 and time.time()<deadline:time.sleep(.02)
            self.assertEqual(self.receiver.frames,20);self.receiver.stop();self.assertFalse(self.receiver.thread.is_alive())
        finally:source.close()
    def test_official_feeder_version_and_registration_prompt(self):
        r=self.receiver;f=r.feeder;r.feed_format='beast'
        monitor=f.root/'dev/shm/decoder.txt'
        monitor.parent.mkdir(parents=True,exist_ok=True)
        monitor.write_text('feed_status=connected\nmlat-ok=YES\n')
        argv,env=f.prepare(self.settings.value,True)
        self.assertFalse(monitor.exists(), 'Old vendor status must not survive a new feeder session')
        trust=f.root/'etc/ssl/certs/ca-bundle.crt'
        self.assertTrue(ssl.create_default_context(cafile=str(trust)).get_ca_certs())
        self.assertEqual(env['SSL_CERT_FILE'],'/etc/ssl/certs/ca-certificates.crt')
        prepared=parse_ini((f.root/'etc/fr24feed.ini').read_text())
        self.assertNotIn('host',prepared)
        self.assertEqual(prepared['httpd'],'no','The unauthenticated vendor HTTP listener must stay disabled')
        shell=subprocess.run(argv[:-2]+['/bin/bash','-c','/usr/bin/pgrep -f fa-mlat-client'],cwd=f.root,env=env,capture_output=True,timeout=10,creationflags=HIDDEN)
        self.assertEqual(shell.returncode,1)
        self.assertEqual(shell.stdout+shell.stderr,b'')
        for name,expected in [('dump1090',1),('fr24feed',2)]:
            discovery=subprocess.run(argv[:-2]+['/usr/bin/pgrep',name],cwd=f.root,env=env,capture_output=True,timeout=10,creationflags=HIDDEN)
            self.assertEqual(discovery.returncode,expected)
            self.assertEqual(discovery.stdout,b'')
        p=subprocess.run(argv[:-1]+['--version'],cwd=f.root,env=env,capture_output=True,timeout=10,creationflags=HIDDEN)
        self.assertEqual(p.returncode,0,p.stderr.decode(errors='replace'));self.assertIn(b'1.0.57-1',p.stdout)
        f.signup_start();deadline=time.time()+10
        while 'email' not in f.signup_snapshot()['text'].lower() and time.time()<deadline:time.sleep(.1)
        output=f.signup_snapshot()['text']
        self.assertIn('email',output.lower(),output)
        time.sleep(.4)
        self.assertNotIn('Invalid email',f.signup_snapshot()['text'])
        f.signup_answer('not-an-email')
        deadline=time.time()+4
        while 'Invalid email' not in f.signup_snapshot()['text'] and time.time()<deadline:time.sleep(.05)
        self.assertEqual(f.signup_snapshot()['text'].count('Invalid email'),1)
        time.sleep(.4)
        self.assertEqual(f.signup_snapshot()['text'].count('Invalid email'),1,'Registration must wait for an answer')
        f.signup_stop()
        self.assertFalse(f.signup_thread.is_alive(),'Registration reader must stop with its child')
    def test_completed_signup_stores_key_and_closes_terminal(self):
        f=self.receiver.feeder
        script=('import pathlib,sys; '
                'p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); '
                'p.write_text(\'fr24key="a123456789abcdef"\\n\'); '
                'print("Saving settings to /etc/fr24feed.ini...OK",flush=True); '
                'sys.stdin.read()')
        child=subprocess.Popen([sys.executable,'-u','-c',script,str(f.root/'etc/fr24feed.ini')],
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=HIDDEN)
        try:
            with patch.object(f,'prepare',return_value=([str(ROOT/'work/runtime/feeder/blink.exe')],{})),patch('frw.runtime.subprocess.Popen',return_value=child):
                f.signup_start()
            f.signup_thread.join(5)
            self.assertFalse(f.signup_thread.is_alive(),'Completed signup must close without user cancellation')
            self.assertEqual(self.settings.key(),'a123456789abcdef')
            self.assertFalse((f.root/'etc/fr24feed.ini').exists())
            self.assertFalse(f.signup_snapshot()['running'])
        finally:
            if child.poll() is None: child.terminate();child.wait(3)

if __name__=='__main__':unittest.main(verbosity=2)
