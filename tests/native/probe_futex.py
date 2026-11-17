"""Build small static Linux ELF syscall probes using the supplied Cygwin assembler."""
from pathlib import Path
import argparse, json, os, struct, subprocess, sys, tempfile, time

PROJECT = Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime',type=Path,default=PROJECT/'work/runtime/feeder/blink.exe')
parser.add_argument('--assembler-dir',type=Path,default=PROJECT/'work/cygwin-toolchain/bin')
parser.add_argument('--output-dir',type=Path)
args=parser.parse_args()
STAGE=args.output_dir or Path(tempfile.mkdtemp(prefix='frw-futex-'))
STAGE.mkdir(parents=True,exist_ok=True)
TOOLS = args.assembler_dir
ENV = dict(os.environ)
ENV['PATH'] = str(TOOLS)+';'+ENV.get('PATH','')
ENV['BLINK_LOG_FILENAME'] = '/dev/null'
BIN = args.runtime

def cygpath(p):
    p = Path(p).resolve()
    if p.drive.startswith('\\\\'): return p.as_posix()
    return '/cygdrive/'+p.drive[0].lower()+p.as_posix()[2:]

def build(name, source):
    asm = STAGE/(name+'.s')
    obj = STAGE/(name+'.o')
    raw = STAGE/(name+'.bin')
    elf = STAGE/(name+'.elf')
    asm.write_text('.intel_syntax noprefix\n.text\n'+source,encoding='utf-8')
    for command in ([str(TOOLS/'as.exe'),'-o',str(obj),str(asm)],
                    [str(TOOLS/'objcopy.exe'),'-j','.text','-O','binary',str(obj),str(raw)]):
        subprocess.run(command,env=ENV,check=True,capture_output=True,timeout=20,creationflags=0x08000000)
    code = raw.read_bytes()
    size = 120+len(code)
    eh = struct.pack('<16sHHIQQQIHHHHHH',b'\x7fELF\x02\x01\x01'+b'\0'*9,2,62,1,0x400000+120,64,0,0,64,56,1,0,0,0)
    ph = struct.pack('<IIQQQQQQ',1,7,0,0x400000,0x400000,size,size,4096)
    elf.write_bytes(eh+ph+code)
    return elf

def clock_read(label, clock=1):
    return f'mov eax,228\nmov edi,{clock}\nlea rsi,[rip+{label}]\nsyscall\n'

def futex(op, value=0, mask=0xffffffff, timeout=False):
    return (f'mov eax,202\nlea rdi,[rip+futex_value]\nmov esi,{op}\nmov edx,{value}\n'+
            ('lea r10,[rip+deadline]\n' if timeout else 'xor r10d,r10d\n')+
            f'xor r8d,r8d\nmov r9d,{mask}\nsyscall\n')

def output():
    return ('mov eax,1\nmov edi,1\nlea rsi,[rip+result]\nmov edx,40\nsyscall\n'
            'mov eax,231\nxor edi,edi\nsyscall\n'
            '.balign 8\nresult: .quad 0\nstart: .quad 0,0\nend: .quad 0,0\n'
            'deadline: .quad 0,0\nfutex_value: .long 0\n')

def probe(name, op, expect, value=0, mask=0xffffffff, timeout_kind=None, clock=1):
    source = clock_read('start')
    if timeout_kind == 'future':
        source += clock_read('deadline',clock)
        source += ('add qword ptr [rip+deadline+8],120000000\n'
                   'cmp qword ptr [rip+deadline+8],1000000000\njl ready\n'
                   'sub qword ptr [rip+deadline+8],1000000000\ninc qword ptr [rip+deadline]\nready:\n')
    elif timeout_kind == 'past':
        source += clock_read('deadline',clock)+'dec qword ptr [rip+deadline]\n'
    elif timeout_kind == 'relative':
        source += 'mov qword ptr [rip+deadline+8],120000000\n'
    elif timeout_kind == 'negative':
        source += 'mov qword ptr [rip+deadline],-1\n'
    elif timeout_kind == 'bad_nsec':
        source += 'mov qword ptr [rip+deadline+8],1000000000\n'
    source += futex(op,value,mask,bool(timeout_kind))
    source += 'mov [rip+result],rax\n'+clock_read('end')+output()
    return name,build(name,source),expect,timeout_kind in ('future','relative')

def wake_probe(name, wakeop):
    source = clock_read('start')
    source += ('mov eax,56\nmov edi,0x50f00\nlea rsi,[rip+stacktop]\n'
               'xor edx,edx\nxor r10d,r10d\nxor r8d,r8d\nsyscall\n'
               'test rax,rax\njz child\njs clone_failed\n')
    source += futex(0x189)+ 'mov [rip+result],rax\n'+clock_read('end')
    source += 'jmp report\nclone_failed:\nmov [rip+result],rax\njmp report\nchild:\n'
    source += ('mov eax,35\nlea rdi,[rip+pause]\nxor esi,esi\nsyscall\n')
    source += futex(wakeop,1)
    source += ('cmp eax,1\nje wake_ok\nmov qword ptr [rip+result],-999\nwake_ok:\n'
               'mov eax,60\nxor edi,edi\nsyscall\nreport:\n')
    source += output()+'.balign 8\npause: .quad 0,120000000\n.space 65536\nstacktop:\n'
    return name,build(name,source),0,True

def main():
    tests = [
        probe('mismatch_rt',0x189,-11,value=1),
        probe('mismatch_mono',0x89,-11,value=1),
        probe('zero_mask',0x189,-22,mask=0),
        probe('selective_mask',0x189,-22,mask=1),
        probe('timeout_rt',0x189,-110,timeout_kind='future',clock=0),
        probe('timeout_mono',0x89,-110,timeout_kind='future'),
        probe('past_rt',0x189,-110,timeout_kind='past',clock=0),
        probe('past_mono',0x89,-110,timeout_kind='past'),
        probe('negative_seconds',0x189,-22,timeout_kind='negative'),
        probe('invalid_nsec',0x189,-22,timeout_kind='bad_nsec'),
        probe('wait_relative',0x80,-110,timeout_kind='relative'),
        probe('wake_bitset_unsupported',0x8a,-22,value=1),
        probe('wake_bitset_zero',0x8a,-22,value=1,mask=0),
        probe('wake_bitset_selective',0x8a,-22,value=1,mask=1),
        wake_probe('wait_bitset_plain_wake',0x81),
    ]
    results = []
    for name,path,expect,delayed in tests:
        try:
            p = subprocess.run([str(BIN),'-m',cygpath(path)],env=ENV,capture_output=True,timeout=4,creationflags=0x08000000)
            values = struct.unpack('<qqqqq',p.stdout) if len(p.stdout)==40 else None
            elapsed = values[3]-values[1]+(values[4]-values[2])/1e9 if values else None
            ok = bool(values and values[0]==expect and p.returncode==0 and
                      ((0.10 <= elapsed < 1.5) if delayed else (0 <= elapsed < 0.5)))
            result = dict(name=name,passed=ok,returncode=p.returncode,syscall_result=values[0] if values else None,
                          expected=expect,elapsed_seconds=elapsed,stderr=p.stderr.decode(errors='replace')[:1000])
        except subprocess.TimeoutExpired:
            result = dict(name=name,passed=False,error='process timeout')
        results.append(result)
        print(json.dumps(result),flush=True)
    (STAGE/'futex-results.json').write_text(json.dumps(results,indent=2))
    return 0 if all(r['passed'] for r in results) else 1

if __name__=='__main__':
    sys.exit(main())
