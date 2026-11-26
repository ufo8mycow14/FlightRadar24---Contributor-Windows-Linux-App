"""Synthetic RF regression checks for the bundled Windows demodulator."""
import math
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

from frw.config import validate
from frw.runtime import decoder_config, HIDDEN
from frw.streams import AVRParser


DECODER = Path(os.environ.get('FRW_TEST_DECODER') or
               Path(__file__).resolve().parents[1] / 'work/runtime/decoder/dump1090.exe')


@unittest.skipUnless(os.name == 'nt' and DECODER.is_file(), 'Windows decoder fixture required')
class DecoderWaveforms(unittest.TestCase):
    def decode(self, rate, data, mode_ac=True, correction=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'test.uc8').write_bytes(data)
            config = validate({'sample_rate': rate, 'mode_ac': mode_ac, 'error_correction': correction})
            (root / 'test.cfg').write_text(decoder_config(config), encoding='ascii')
            run = subprocess.run([str(DECODER.resolve()), '--config', 'test.cfg', '--raw',
                                  '--infile', 'test.uc8'], cwd=root, capture_output=True,
                                 creationflags=HIDDEN, timeout=15, check=True)
            return AVRParser().feed(run.stdout)

    def test_mode_s_payload_clock_and_native_quality(self):
        payload = bytes.fromhex('8D40621D58C382D690C8AC2863A7')
        for rate in (2000000, 2400000):
            with self.subTest(rate=rate):
                samples = bytearray([128, 128]) * 270000
                starts = (14000, 131000, 180000)
                damaged = bytearray(payload)
                damaged[6] ^= 8
                for start, message in zip(starts, (payload, payload, damaged)):
                    bits = ''.join(f'{octet:08b}' for octet in message)
                    for index in range(round(120*rate/1e6)):
                        usec = (index+.5)*1e6/rate
                        high = any(begin <= usec < begin+.5 for begin in (0, 1, 3.5, 4.5))
                        if usec >= 8:
                            high = ((usec % 1) < .5) == (bits[int(usec)-8] == '1')
                        if high:
                            samples[2*(start+index)] = 240
                frames = self.decode(rate, samples)
                self.assertEqual([frame.data for frame in frames], [payload]*2)
                self.assertEqual(frames[1].timestamp-frames[0].timestamp, (starts[1]-starts[0])*(12000000//rate))
                for frame in frames:
                    if rate == 2400000:
                        self.assertGreater(frame.signal, 0)
                        self.assertEqual(frame.corrected, 0)
                    else:
                        self.assertIsNone(frame.signal)
                if rate == 2400000:
                    repaired = self.decode(rate, samples, correction=1)
                    self.assertEqual([frame.data for frame in repaired], [payload]*3)
                    self.assertEqual([frame.corrected for frame in repaired], [0,0,1])

    def test_legacy_replies_across_block_boundary(self):
        for rate in (2000000, 2400000):
            with self.subTest(rate=rate):
                samples = bytearray([128, 128]) * 280000
                starts = [20000, 131056, 220000]
                for start in starts:
                    for slot in (0, 2, 10, 14):
                        begin = (.10 + 1.45 * slot) * rate / 1e6
                        end = begin + .45 * rate / 1e6
                        for index in range(math.floor(begin), math.ceil(end)):
                            overlap = min(end, index+1) - max(begin, index)
                            samples[2*(start+index)] = 128 + round(110 * math.sqrt(overlap))
                frames = self.decode(rate, samples)
                self.assertEqual([frame.data.hex() for frame in frames], ['1200'] * len(starts))
                offsets = [frame.timestamp - start * (12000000 // rate)
                           for frame, start in zip(frames, starts)]
                self.assertEqual(len(set(offsets)), 1)
                self.assertLess(abs(offsets[0] - (244 if rate == 2400000 else 0)), 12)
                self.assertFalse(self.decode(rate, samples, mode_ac=False))
                generator = random.Random(4321)
                quiet = generator.choices(range(126, 131), k=270000)
                self.assertFalse(self.decode(rate, bytes(quiet)))
