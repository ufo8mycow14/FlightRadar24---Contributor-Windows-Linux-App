#!/usr/bin/env python3
"""Decode the bundled synthetic I/Q fixture through loopback Beast output."""
from __future__ import annotations

import os
import math
import socket
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from frw.config import DEFAULT
from frw.runtime import linux_decoder_argv
from frw.streams import BeastParser

MESSAGES = ('8D40621D58C382D690C8AC2863A7', '8D40621D58C386435CC412692AD6',
            '8D4840D6202CC371C32CE0576098', '8D485020994409940838175B284F')


def make_synthetic(path: Path) -> set[str]:
    """Generate a 2.4 MS/s I/Q fixture to match dump1090-fa's fixed clock."""
    rate = 2_400_000
    samples = 1_400_000
    signal = bytearray([128, 128] * samples)
    edges = (131000, 262080, 524230, 1048500)
    starts = sorted(start for start in (20000 + i * 12000 for i in range(96))
                    if all(abs(start-edge) > 500 for edge in edges))
    starts = sorted((*starts, *edges))
    expected = set()
    pulse_count = math.ceil(120 * rate / 1_000_000)
    for number, start in enumerate(starts):
        message = MESSAGES[number % len(MESSAGES)]
        bits = ''.join(f'{byte:08b}' for byte in bytes.fromhex(message))
        for j in range(pulse_count):
            t = (j + 0.5) * 1_000_000 / rate
            high = any(a <= t < b for a, b in ((0, 0.5), (1, 1.5), (3.5, 4), (4.5, 5)))
            if 8 <= t < 120:
                bit = int(t - 8)
                phase = t - 8 - bit
                high = (phase < 0.5) == (bits[bit] == '1')
            if high:
                signal[(start + j) * 2] = 240
        expected.add(message)
    path.write_bytes(signal)
    return expected


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit('usage: check-linux-decoder.py DECODER OUTPUT_DIRECTORY')
    decoder, output_dir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    recording = output_dir/'synthetic-2.4mhz.uc8'
    expected = make_synthetic(recording)
    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
    probe.close()
    config = dict(DEFAULT)
    config.update(receiver='iq-file', iq_file='-', sample_rate=2400000, error_correction=1)
    argv = linux_decoder_argv(decoder, config, port)
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    frames = []
    input_errors = []
    input_writer = None
    parser = BeastParser()
    connection = None
    deadline = time.monotonic() + 15
    try:
        while connection is None and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'Decoder exited before opening Beast output ({process.returncode}).')
            try:
                connection = socket.create_connection(('127.0.0.1', port), timeout=0.25)
            except OSError:
                time.sleep(0.05)
        if connection is None:
            raise RuntimeError('Decoder did not expose its loopback Beast output.')
        connection.settimeout(1)
        def write_fixture():
            try:
                with recording.open('rb') as stream:
                    shutil.copyfileobj(stream, process.stdin, length=65536)
            except (BrokenPipeError, OSError) as error:
                input_errors.append(error)
            finally:
                try: process.stdin.close()
                except (BrokenPipeError, OSError, ValueError): pass
        input_writer = threading.Thread(target=write_fixture, daemon=True, name='synthetic-input')
        input_writer.start()
        while time.monotonic() < deadline:
            try:
                data = connection.recv(16384)
            except socket.timeout:
                data = None
            if data == b'':
                break
            if data:
                frames.extend(parser.feed(data))
            if process.poll() is not None:
                break
        if input_writer:
            input_writer.join(3)
            if input_writer.is_alive():
                raise RuntimeError('Synthetic I/Q writer did not finish after decoder EOF.')
        if input_errors:
            raise RuntimeError(f'Synthetic I/Q writer failed: {input_errors[0]}')
        received = {frame.data.hex().upper() for frame in frames}
        if len(frames) < 3 or not expected.issubset(received):
            missing = sorted(expected - received)
            raise RuntimeError(f'Synthetic recording produced {len(frames)} Beast frames; expected messages missing: {missing}.')
        try: exit_code = process.wait(timeout=3)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError('Decoder did not finish after the paced I/Q input closed.') from error
        if exit_code != 1:
            raise RuntimeError(f'FlightAware decoder I/Q playback returned {exit_code}; expected its documented finite-input status 1.')
        print(f'Linux decoder synthetic check passed: {len(frames)} Beast frames at 2.4 MS/s.')
        return 0
    finally:
        if connection:
            connection.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, 15)
                process.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try: os.killpg(process.pid, 9)
                except ProcessLookupError: pass
                process.wait(timeout=2)
        if input_writer:
            input_writer.join(2)


if __name__ == '__main__':
    raise SystemExit(main())
