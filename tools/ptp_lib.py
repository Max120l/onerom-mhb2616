"""PMD 85 tape archives (.ptp), read for the multiload shelf.

A PTP is a container: each block is a 2-byte little-endian length and the
block's bytes.  A program is a 63-byte header block -- 48-byte pilot
leader (16 x FF, 16 x 00, 16 x 55), then number(1), type(1), start(2),
length(2), name(8), sum-CRC(1) -- followed by a body block of length+1
payload bytes and a CRC.  Simple "AutoRun"/"JUMP xxxx" programs are one
header + one body, loading at the header's start field; the interesting
ones follow the body with headerless "raw" blocks read by a turbo loader
inside the body, whose framing is the loader's own business (see
docs/ROM-module.md on what is and is not extracted yet).

Format cross-checked against GPMD85Emulator src/TapeBrowser.cpp.
"""

import struct
from pathlib import Path

LEADER = bytes([0xFF] * 16 + [0x00] * 16 + [0x55] * 16)


def blocks_of(path: Path):
    d = Path(path).read_bytes()
    pos = 0
    out = []
    while pos + 2 <= len(d):
        n = struct.unpack('<H', d[pos:pos + 2])[0]
        out.append(d[pos + 2:pos + 2 + n])
        pos += 2 + n
    return out

def programs_of(path: Path):
    """-> list of dicts: name, number, type, start, length, body (CRC
    stripped), raws (the turbo blocks that follow, verbatim)."""
    blocks = blocks_of(path)
    progs = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if len(b) == 63 and b[:48] == LEADER:
            start, length = struct.unpack('<HH', b[50:54])
            raws = []
            j = i + 2
            while j < len(blocks) and not (len(blocks[j]) == 63
                                           and blocks[j][:48] == LEADER):
                raws.append(blocks[j])
                j += 1
            progs.append({
                'name': b[54:62].decode('latin1').rstrip(),
                'number': b[48], 'type': chr(b[49]),
                'start': start, 'length': length,
                'body': blocks[i + 1][:-1] if i + 1 < len(blocks) else b'',
                'raws': raws,
            })
            i = j
        else:
            i += 1
    return progs


def find_program(path: Path, name: str):
    for p in programs_of(path):
        if p['name'] == name:
            return p
    names = ', '.join(p['name'] for p in programs_of(path))
    raise SystemExit(f"error: no program {name!r} in {path} (has: {names})")
