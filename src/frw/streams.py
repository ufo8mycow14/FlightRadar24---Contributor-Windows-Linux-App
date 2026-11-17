"""Bounded AVR, Beast and SBS streams. Beast clocks are never fabricated."""
from dataclasses import dataclass
import queue,re,socket,threading

@dataclass(frozen=True)
class Frame:
    data: bytes
    timestamp: int|None = None
    signal: int = 0
    def avr(self):
        prefix='*' if self.timestamp is None else f'@{self.timestamp:012X}'
        return (prefix+self.data.hex().upper()+';\r\n').encode('ascii')
    def beast(self):
        if self.timestamp is None or len(self.data) not in (2,7,14):return None
        code={2:0x31,7:0x32,14:0x33}[len(self.data)]
        body=self.timestamp.to_bytes(6,'big')+bytes([self.signal])+self.data
        return b'\x1a'+bytes([code])+body.replace(b'\x1a',b'\x1a\x1a')

class AVRParser:
    def __init__(self):self.buffer=bytearray()
    def feed(self,data):
        self.buffer.extend(data);result=[]
        while b';' in self.buffer:
            i=self.buffer.index(ord(';'));part=bytes(self.buffer[:i+1]);del self.buffer[:i+1]
            m=re.search(rb'([*@])([A-Fa-f0-9]+);$',part)
            if not m:continue
            value=m[2];timestamp=None
            if m[1]==b'@':
                if len(value) not in (26,40):continue
                timestamp=int(value[:12],16);value=value[12:]
            if len(value) in (14,28):result.append(Frame(bytes.fromhex(value.decode()),timestamp))
        if len(self.buffer)>8192:self.buffer.clear()
        return result

class BeastParser:
    LENGTHS={0x31:9,0x32:14,0x33:21}
    def __init__(self):self.buffer=bytearray()
    def feed(self,data):
        self.buffer.extend(data);frames=[]
        while len(self.buffer)>=2:
            if self.buffer[0]!=0x1a or self.buffer[1] not in self.LENGTHS:del self.buffer[0];continue
            count=self.LENGTHS[self.buffer[1]];body=bytearray();i=2;incomplete=False;malformed=False
            while len(body)<count:
                if i>=len(self.buffer):incomplete=True;break
                b=self.buffer[i];i+=1
                if b==0x1a:
                    if i>=len(self.buffer):incomplete=True;break
                    if self.buffer[i]!=0x1a:malformed=True;break
                    i+=1
                body.append(b)
            if incomplete:break
            if malformed:del self.buffer[:max(1,i-1)];continue
            del self.buffer[:i]
            frames.append(Frame(bytes(body[7:]),int.from_bytes(body[:6],'big'),body[6]))
        if len(self.buffer)>65536:self.buffer.clear()
        return frames

class Broadcast:
    def __init__(self,host,port):
        self.socket=socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET,getattr(socket,'SO_EXCLUSIVEADDRUSE',socket.SO_REUSEADDR),1)
        self.socket.bind((host,port));self.socket.listen(16);self.socket.settimeout(.5)
        self.port=self.socket.getsockname()[1];self.stop=threading.Event();self.lock=threading.Lock();self.clients={}
        threading.Thread(target=self._accept,daemon=True).start()
    def _accept(self):
        while not self.stop.is_set():
            try:client,_=self.socket.accept()
            except socket.timeout:continue
            except OSError:break
            client.settimeout(2);q=queue.Queue(256)
            with self.lock:
                if len(self.clients)>=32:client.close();continue
                self.clients[client]=q
            threading.Thread(target=self._send,args=(client,q),daemon=True).start()
    def _send(self,client,q):
        try:
            while not self.stop.is_set():
                try:data=q.get(timeout=.5)
                except queue.Empty:continue
                if data is None:break
                client.sendall(data)
        except OSError:pass
        finally:
            with self.lock:self.clients.pop(client,None)
            client.close()
    def publish(self,data):
        if not data:return
        with self.lock:
            for client,q in list(self.clients.items()):
                try:q.put_nowait(data)
                except queue.Full:
                    try:client.shutdown(socket.SHUT_RDWR)
                    except OSError:pass
                    self.clients.pop(client,None)
    def close(self):
        self.stop.set();self.socket.close()
        with self.lock:
            for client in self.clients:
                try:client.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            self.clients.clear()
