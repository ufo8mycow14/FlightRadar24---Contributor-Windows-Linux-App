"""Bounded local recorder for receiver output, independent of aircraft identification."""
import json
import os
import queue
import threading
import time
from pathlib import Path
from .streams import legacy_interpretations


class MessageCapture:
    def __init__(self, directory, budget_mb, source):
        self.directory = Path(directory) / 'message-capture'
        self.budget = budget_mb * 1048576
        self.source = source
        self.written = self.dropped = 0
        self.error = ''
        self.pending = queue.Queue(4096)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.worker = None
        if self.budget:
            self.worker = threading.Thread(target=self._write, name='message-capture', daemon=True)
            self.worker.start()

    def submit(self, stamp, kind, payload, ticks=None, signal=None, corrected=None):
        with self.lock:
            if not self.worker:
                return
            if not self.stopping.is_set():
                try:
                    self.pending.put_nowait((stamp, self.source, kind, payload, ticks, signal, corrected))
                    return
                except queue.Full:
                    pass
            self.dropped += 1

    def discard(self, count=1):
        with self.lock:
            if self.worker:
                self.dropped += count

    def status(self):
        return {'enabled':bool(self.budget), 'written':self.written,
                'dropped':self.dropped, 'pending':self.pending.qsize(), 'error':self.error}

    def close(self):
        with self.lock:
            self.stopping.set()
        if self.worker:
            self.worker.join(8)
            if self.worker.is_alive():
                raise RuntimeError('Message capture is still flushing to disk.')

    def _write(self):
        stream = None
        size = 0
        segment = min(1048576, self.budget // 4)
        retry = 0
        try:
            while not self.stopping.is_set() or not self.pending.empty():
                try:
                    first = self.pending.get(timeout=.2)
                except queue.Empty:
                    continue
                batch = [first]
                while len(batch) < 256:
                    try:
                        batch.append(self.pending.get_nowait())
                    except queue.Empty:
                        break
                completed = 0
                if time.monotonic() < retry:
                    self.discard(len(batch))
                    continue
                try:
                    for stamp, source, kind, payload, ticks, signal, corrected in batch:
                        record = [stamp,source,kind,payload.hex(),ticks,signal,corrected]
                        if kind == 'mode-ac':
                            record.append(legacy_interpretations(payload))
                        line = (json.dumps(record,separators=(',',':'))+'\n').encode('ascii')
                        if stream is None or size + len(line) > segment:
                            if stream:
                                stream.close()
                                stream = None
                            self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
                            if self.directory.is_symlink() or getattr(os.path,'isjunction',lambda _:False)(self.directory):
                                raise OSError('Capture directory must be local to the profile')
                            owned = sorted((entry for entry in self.directory.glob('frames-*.jsonl')
                                            if entry.stem[7:].isascii() and entry.stem[7:].isdigit()
                                            and not entry.is_symlink() and entry.is_file()),
                                           key=lambda entry: int(entry.stem[7:]))
                            used = sum(entry.stat().st_size for entry in owned)
                            for entry in owned:
                                if used + segment <= self.budget:
                                    break
                                length = entry.stat().st_size
                                entry.unlink()
                                used -= length
                            name = max(time.time_ns(), int(owned[-1].stem[7:])+1 if owned else 0)
                            path = self.directory / f'frames-{name}.jsonl'
                            stream = os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb',buffering=0)
                            size = 0
                        if stream.write(line) != len(line):
                            raise OSError('Incomplete capture write')
                        size += len(line)
                        completed += 1
                        self.written += 1
                    self.error = ''
                except OSError:
                    self.discard(len(batch)-completed)
                    self.error = 'Capture storage unavailable; check disk space and permissions.'
                    retry = time.monotonic()+2
                    if stream:
                        stream.close()
                    stream = None
        finally:
            if stream:
                stream.close()
