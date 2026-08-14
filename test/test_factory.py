"""The shelf factory's verdicts, against synthetic tapes.

Three programs: one that draws (must PASS with a manifest entry), one
that spins without drawing (must FAIL, named), one turbo-shaped (must be
listed as out of scope).  The real-archive run is the tool's demo; this
is what keeps its verdict logic honest when it changes.
"""

import json
import struct
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import ptp_lib                     # noqa: E402
import make_multiload as ml        # noqa: E402

SCRATCH = Path("/tmp/claude-0/-home-user-TRS-80-bt-bridge/"
               "5ace1ab9-c2af-5ddd-9aa7-bdba28cb2fc4/scratchpad")
MONIT3B = SCRATCH / "romzip/PMD85-rom-files/Monitor/PMD85-3/monit3B.rom"


def tape_program(name: str, start: int, body: bytes, raws=()) -> bytes:
    hdr = bytearray(ptp_lib.LEADER)
    hdr += bytes([0, ord('?')])
    hdr += struct.pack('<HH', start, len(body) - 1)
    hdr += name.ljust(8)[:8].encode()
    hdr.append(sum(hdr[48:62]) & 0xFF)
    out = struct.pack('<H', len(hdr)) + bytes(hdr)
    body_blk = body + bytes([sum(body) & 0xFF])
    out += struct.pack('<H', len(body_blk)) + body_blk
    for r in raws:
        out += struct.pack('<H', len(r)) + r
    return out


def drawing_program() -> bytes:
    a = ml.MAsm(0x4000)
    a.lxi(ml.RP_H, 0xC000)
    a.lxi(ml.RP_B, 500)
    a.label("f")
    a.mvi(ml.M, 0x3F)
    a.inx(ml.RP_H)
    a.dcx(ml.RP_B)
    a.mov(ml.A, ml.B)
    a.ora(ml.C)
    a.jnz("f")
    a.label("spin")
    a.jmp("spin")
    return a.link()


def spinning_program() -> bytes:
    a = ml.MAsm(0x4000)
    a.label("spin")
    a.jmp("spin")
    return a.link()


def test_turbo_rip_preserves_sentinel_valued_bytes():
    """The extraction must ship a loader-written 0xAA byte as 0xAA.

    The rig prefills RAM with 0xAA to find the image extent; the first
    version also *shipped* every byte still reading 0xAA as 0x00 -- so any
    game whose data legitimately contained 0xAA was silently corrupted
    (MAGICIAN crashed on its start key from a zeroed opcode).  The write
    bitmap decides now; this pins that.
    """
    import shelf_factory as sf

    # a turbo-shaped loader: pull every tape byte to 4000h, jump there
    a = ml.MAsm(0x7F00)
    payload = bytes([0x76, 0xAA, 0x00, 0xAA, 0x55, 0xAA]) + bytes(10) \
        + bytes([0xAA])
    a.lxi(ml.RP_H, 0x4000)
    a.lxi(ml.RP_B, len(payload))
    a.label("rd")
    a.inp(0x1C)
    a.mov(ml.M, ml.A)
    a.inx(ml.RP_H)
    a.dcx(ml.RP_B)
    a.mov(ml.A, ml.B)
    a.ora(ml.C)
    a.jnz("rd")
    a.jmp(0x4000)
    body = a.link()

    prog = {"start": 0x7F00, "body": body, "raws": [payload]}
    got, why = sf.rip_turbo(prog, [("fake", bytes(0x1000))])
    assert got is not None, why
    image, load, exec_, regs, mon = got
    assert load == 0x4000 and exec_ == 0x4000
    assert image[:len(payload)] == payload, \
        "0xAA bytes in loaded data must survive extraction"
    # the unwritten gap between payload and loader body ships as zeros
    assert set(image[len(payload):0x7F00 - load]) == {0}


def test_factory_verdicts(tmp_path):
    import pytest
    if not MONIT3B.exists():
        pytest.skip("monit3B not present")
    tape = tmp_path / "mix.ptp"
    tape.write_bytes(
        tape_program("DRAWS", 0x4000, drawing_program())
        + tape_program("SPINS", 0x4000, spinning_program())
        + tape_program("TURBO", 0x7F00, spinning_program(),
                       raws=[bytes(100)]))
    out = tmp_path / "audition"
    res = subprocess.run(
        [sys.executable, str(TOOLS / "shelf_factory.py"),
         "--monitor3", str(MONIT3B), "--out", str(out), str(tape)],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    report = (out / "report.md").read_text()
    assert "`DRAWS" in report and "PASS" in report
    assert "`SPINS" in report and "no draw" in report
    assert "`TURBO" in report and "turbo loader" in report
    entries = json.loads(report.split("```json")[1].split("```")[0])
    assert len(entries) == 1 and entries[0]["program"] == "DRAWS"
