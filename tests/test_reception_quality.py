import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from frw.aircraft import Aircraft
from frw.capture import MessageCapture
from frw.config import validate
from frw.runtime import Receiver, ReceptionQuality
from frw.streams import AVRParser, BeastParser, Frame, legacy_interpretations


class ReceiverCoverageTests(unittest.TestCase):
    def test_anonymous_alternatives_are_not_resolved_to_a_flight(self):
        self.assertEqual(legacy_interpretations(bytes.fromhex('0020')), {
            'possible_mode_a':'0020','spi':False,'possible_mode_c_ft':-1000})
        self.assertNotIn('possible_mode_c_ft',legacy_interpretations(bytes.fromhex('00A0')))
        self.assertEqual(legacy_interpretations(bytes.fromhex('FFFF')), {})

    def test_quality_transport_is_optional_and_private(self):
        parser = AVRParser()
        self.assertEqual(parser.feed(b'~B5'), [])
        legacy, short = parser.feed(b'1@0000000000001A00;*00000000000000;')
        self.assertEqual(legacy, Frame(bytes.fromhex('1a00'), 0, 181, 1))
        self.assertIsNone(short.signal)
        self.assertFalse(legacy.avr().startswith(b'~'))
        beast = BeastParser()
        self.assertEqual(beast.feed(legacy.beast()[:7]), [])
        self.assertEqual(beast.feed(legacy.beast()[7:]), [Frame(legacy.data,0,181)])

    def test_quality_window_expires_and_missing_is_not_zero(self):
        with patch('frw.runtime.time.monotonic', return_value=20) as clock:
            quality = ReceptionQuality()
            quality.observe(Frame(bytes(14),0,181,0), True)
            quality.observe(Frame(bytes(7),1,None,1), False)
            quality.observe(Frame(bytes(2)), False)
            clock.return_value = 79
            report = quality.snapshot()
            self.assertEqual((report['mode_s'],report['accepted'],report['mode_ac']), (2,1,1))
            self.assertEqual((report['mean_dbfs'],report['strong_pct'],report['corrected_pct']), (-3,100,50))
            self.assertEqual(report['clean_rate'],0.02)
            clock.return_value = 81
            report = quality.snapshot()
            self.assertEqual(report['seconds'],60)
            self.assertIsNone(report['strong_pct'])
            self.assertIsNone(report['corrected_pct'])
            self.assertIsNone(report['clean_rate'])
            self.assertEqual(report['accepted_rate'],0)

    def test_capture_rejected_and_anonymous_without_tracking(self):
        with TemporaryDirectory() as directory:
            receiver = Receiver.__new__(Receiver)
            receiver.aircraft = Aircraft(validate({}))
            receiver.quality = ReceptionQuality()
            receiver.capture = MessageCapture(directory, 1, 'beast-tcp')
            receiver.frames = 0
            receiver.outputs = {}
            receiver.feed_stream = Mock()
            receiver.feed_format = 'beast'
            for frame in (Frame(bytes.fromhex('1200'),0), Frame(bytes.fromhex('8D'+'00'*13),42,20)):
                receiver._frame(frame)
            receiver.capture.close()
            self.assertEqual(receiver.aircraft.snapshot()['aircraft'], [])
            rows = [json.loads(line) for line in next(receiver.capture.directory.glob('frames-*')).read_text().splitlines()]
            self.assertEqual([row[2:] for row in rows], [['mode-ac','1200',0,None,None,{'possible_mode_a':'1200','spi':False}],
                             ['mode-s','8d'+'00'*13,42,20,None]])
            self.assertEqual(receiver.capture.status()['dropped'],0)
            self.assertEqual(receiver.feed_stream.publish.call_count,1)
            self.assertEqual(receiver.feed_stream.publish.call_args.args[0][:2],b'\x1a3')

    def test_capture_rotation_disk_failure_and_disabled(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = MessageCapture(root, 1, 'sbs-tcp')
            recorder.directory.mkdir()
            (recorder.directory/'frames-note.jsonl').write_text('not owned')
            line = b'x'*4000 + b'\r\n'
            for count in range(180):
                recorder.submit(count,'sbs',line)
            recorder.close()
            files = sorted(recorder.directory.glob('frames-[0-9]*'))
            self.assertLessEqual(sum(path.stat().st_size for path in files),1048576)
            self.assertEqual(recorder.written,180)
            self.assertGreater(json.loads(files[0].read_text().splitlines()[0])[0],0)
            self.assertEqual(bytes.fromhex(json.loads(files[-1].read_text().splitlines()[-1])[3]),line)
            self.assertEqual((recorder.directory/'frames-note.jsonl').read_text(),'not owned')
            failed = MessageCapture(root/'frames-note.jsonl',1,'usb')
            (root/'frames-note.jsonl').touch()
            failed.submit(1,'mode-s',bytes(7))
            failed.close()
            self.assertTrue(failed.status()['error'])
            self.assertEqual(failed.dropped,1)
            disabled = MessageCapture(root/'disabled',0,'usb')
            disabled.submit(1,'mode-s',bytes(7))
            disabled.close()
            self.assertFalse(disabled.directory.exists())

    def test_queue_overflow_is_reported_without_blocking_receiver(self):
        gate = threading.Event()
        writer = MessageCapture._write
        def delayed(recorder):
            gate.wait(5)
            writer(recorder)
        with TemporaryDirectory() as directory, patch.object(MessageCapture,'_write',delayed):
            recorder = MessageCapture(directory,1,'usb')
            try:
                for stamp in range(4100):
                    recorder.submit(stamp,'mode-s',bytes(7))
                self.assertEqual(recorder.status()['pending'],4096)
                self.assertEqual(recorder.dropped,4)
            finally:
                gate.set()
                recorder.close()
            self.assertEqual(recorder.written,4096)

    def test_sbs_archive_precedes_validation_and_retains_endings(self):
        with TemporaryDirectory() as directory:
            receiver = Receiver.__new__(Receiver)
            receiver.stop_event = threading.Event()
            receiver.capture = MessageCapture(directory,1,'sbs-tcp')
            receiver.aircraft = Aircraft(validate({}))
            receiver.outputs = {}
            receiver.frames = 0
            source = Mock()
            source.recv.side_effect = [b'invalid\r',b'\nunsupported\n',b'']
            with patch('frw.runtime.socket.create_connection',return_value=source):
                with self.assertRaisesRegex(OSError,'closed'):
                    receiver._stream(dict(receiver='sbs-tcp',host='localhost',input_port=5000,global_timeout=0))
            receiver.capture.close()
            rows = next(receiver.capture.directory.glob('frames-*')).read_text().splitlines()
            self.assertEqual([bytes.fromhex(json.loads(line)[3]) for line in rows],[b'invalid\r\n',b'unsupported\n'])
            self.assertFalse(receiver.aircraft.snapshot()['aircraft'])
