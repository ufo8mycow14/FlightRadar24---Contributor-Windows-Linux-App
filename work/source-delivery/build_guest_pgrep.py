"""Generate the scoped Linux decoder-discovery helper for the isolated guest.

SPDX-License-Identifier: MIT
The Windows controller stops its native decoder before registration. This guest
contains no Linux dump1090/faup1090 processes. The vendor nevertheless invokes
/usr/bin/pgrep to discover those Linux services. Report no match for exactly
those two literal names; reject all other argument forms with exit status 2.
This is deliberately not a general pgrep replacement or a reception test.
"""
from pathlib import Path
import struct
import sys


def build():
    code = bytearray()
    labels = {}
    fixups = []

    def emit(hex_bytes):
        code.extend(bytes.fromhex(hex_bytes))

    def label(name):
        labels[name] = len(code)

    def relative(opcode, target):
        emit(opcode)
        fixups.append((len(code), target))
        code.extend(b'\0' * 4)

    # Linux initial stack: argc, argv[0], argv[1], ...
    emit('48 83 3c 24 02')                 # cmp qword [rsp], 2
    relative('0f 85', 'unsupported')
    for index in range(2):
        emit('48 8b 74 24 10')            # mov rsi, [rsp+16] (argv[1])
        relative('48 8d 3d', f'name{index}')  # lea rdi, [rip+name]
        relative('e8', 'equal')
        emit('85 c0')                    # test eax, eax
        relative('0f 85', 'absent')
    relative('e9', 'unsupported')

    label('absent')
    emit('bf 01 00 00 00')                # pgrep status 1: no matching process
    relative('e9', 'exit')
    label('unsupported')
    emit('bf 02 00 00 00')                # pgrep status 2: unsupported usage
    label('exit')
    emit('b8 3c 00 00 00 0f 05 0f 0b')   # exit(status); unreachable ud2

    label('equal')
    emit('31 c9')                        # xor ecx, ecx
    label('compare')
    emit('8a 04 0e 3a 04 0f')            # compare characters at rsi/rdi+rcx
    relative('0f 85', 'different')
    emit('84 c0')                        # test al, al (terminator)
    relative('0f 84', 'same')
    emit('48 ff c1')                     # inc rcx
    relative('e9', 'compare')
    label('different')
    emit('31 c0 c3')                     # return false
    label('same')
    emit('b8 01 00 00 00 c3')            # return true
    for index, name in enumerate(('dump1090', 'faup1090')):
        label(f'name{index}')
        code.extend(name.encode('ascii') + b'\0')
    for offset, target in fixups:
        struct.pack_into('<i', code, offset, labels[target] - (offset + 4))

    base = 0x400000
    image_size = 120 + len(code)
    header = struct.pack('<16sHHIQQQIHHHHHH',
        b'\x7fELF\x02\x01\x01' + b'\0' * 9,
        2, 62, 1, base + 120, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    load = struct.pack('<IIQQQQQQ', 1, 5, 0, base, base,
                       image_size, image_size, 4096)
    return header + load + code


if __name__ == '__main__':
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('pgrep')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(build())
