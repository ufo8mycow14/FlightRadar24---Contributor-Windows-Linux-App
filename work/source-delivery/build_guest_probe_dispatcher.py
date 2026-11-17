"""Generate a tightly scoped Linux guest service-probe dispatcher.

SPDX-License-Identifier: MIT
This is NOT Bash or a general shell. It accepts only two exact, read-only
commands emitted by fr24feed 1.0.57-1 in the isolated Windows-managed guest.
The guest has no Linux fa-mlat-client service or cron updater installation.
Unknown commands/argument shapes fail with a fixed diagnostic and status 2.
An existing cron file also fails visibly rather than inventing its contents.
No shell expansion, subprocess execution, or network access is implemented.
"""
from pathlib import Path
import struct
import sys

UPDATER = r'egrep "^([0-9]+|\*) ([0-9]+|\*) ([0-9]+|\*) ([0-9]+|\*) ([0-9]+|\*) root /usr/lib/fr24/fr24feed_updater\.sh" /etc/cron.d/fr24feed_updater 2>/dev/null'
MLAT = '/usr/bin/pgrep -f fa-mlat-client'
CRON = '/etc/cron.d/fr24feed_updater'
DIAGNOSTIC = b'FlightRadar24: unsupported guest shell probe.\n'

def build():
    code = bytearray()
    labels = {}
    fixups = []
    def emit(h): code.extend(bytes.fromhex(h))
    def label(n): labels[n] = len(code)
    def relative(op, target):
        emit(op); fixups.append((len(code), target)); code.extend(b'\0'*4)
    def compare_arg(stack_offset, literal, target):
        emit('48 8b 74 24'); code.append(stack_offset)
        relative('48 8d 3d', literal)
        relative('e8', 'equal')
        emit('85 c0')
        relative('0f 85', target)

    emit('48 83 3c 24 03')  # argc must be exactly 3: bash -c COMMAND
    relative('0f 85', 'unsupported')
    compare_arg(16, 'dash_c', 'valid_option')
    relative('e9', 'unsupported')
    label('valid_option')
    compare_arg(24, 'updater', 'check_updater')
    compare_arg(24, 'mlat', 'no_process')
    relative('e9', 'unsupported')

    label('check_updater')
    emit('b8 15 00 00 00')  # access(CRON,F_OK)
    relative('48 8d 3d', 'cron')
    emit('31 f6 0f 05 48 85 c0')
    relative('0f 84', 'unsupported')  # Existing cron file requires real grep.
    emit('bf 02 00 00 00')  # egrep cannot read missing file; stderr redirected.
    relative('e9', 'exit')
    label('no_process')
    emit('bf 01 00 00 00')  # pgrep: no match in this isolated Linux guest.
    relative('e9', 'exit')
    label('unsupported')
    emit('b8 01 00 00 00 bf 02 00 00 00')  # write(stderr,...)
    relative('48 8d 35', 'diagnostic')
    emit('ba'); code.extend(struct.pack('<I', len(DIAGNOSTIC)))
    emit('0f 05 bf 02 00 00 00')
    label('exit')
    emit('b8 3c 00 00 00 0f 05 0f 0b')

    label('equal')
    emit('31 c9')
    label('compare')
    emit('8a 04 0e 3a 04 0f')
    relative('0f 85', 'different')
    emit('84 c0')
    relative('0f 84', 'same')
    emit('48 ff c1')
    relative('e9', 'compare')
    label('different'); emit('31 c0 c3')
    label('same'); emit('b8 01 00 00 00 c3')
    for name, value in [('dash_c','-c'),('updater',UPDATER),('mlat',MLAT),('cron',CRON)]:
        label(name); code.extend(value.encode('ascii')+b'\0')
    label('diagnostic'); code.extend(DIAGNOSTIC)
    for offset, target in fixups:
        struct.pack_into('<i',code,offset,labels[target]-(offset+4))
    size=120+len(code); base=0x400000
    eh=struct.pack('<16sHHIQQQIHHHHHH',b'\x7fELF\x02\x01\x01'+b'\0'*9,2,62,1,base+120,64,0,0,64,56,1,0,0,0)
    ph=struct.pack('<IIQQQQQQ',1,5,0,base,base,size,size,4096)
    return eh+ph+code

if __name__ == '__main__':
    destination=Path(sys.argv[1]) if len(sys.argv)>1 else Path('bash')
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_bytes(build())
