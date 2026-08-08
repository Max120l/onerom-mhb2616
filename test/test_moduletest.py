"""Run the ROM-module scanner in the emulator, against a modelled module.

The emulator's Bus carries the ROM PACK model -- address latched through
ports 89h/8Ah, data read back on 88h, FFh for an empty slot -- copied
from GPMD85Emulator's RomModule.cpp.  These tests plug known content into
that model and read the scanner's verdict off its own screen.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import make_moduletest as mt     # noqa: E402
import make_screentest as st     # noqa: E402
from i8080emu import Bus, CPU    # noqa: E402

LO = 0xFF00


def fake_pack(size=10240, seed=7):
    out = bytearray()
    v = seed
    for _ in range(size):
        v = (v * 33 + 17) & 0xFF
        out.append(v)
    return bytes(out)


def run(rom, module, min_sweeps=1, steps=6_000_000):
    bus = Bus(rom, map_clear_ports=(), module=module)
    cpu = CPU(bus)
    done = 0
    scanned = 0
    sweeps = 0
    while done < steps and not cpu.halted:
        for _ in range(5_000):
            cpu.step()
        done += 5_000
        sweeps += sum(1 for a in bus.rom_reads[scanned:] if a == LO + 2)
        scanned = len(bus.rom_reads)
        if sweeps >= min_sweeps:
            break
    return bus, sweeps


def digits_at(bus, line, col, n):
    """Read back n hex digits by matching glyph columns against the font."""
    tab = {tuple(st.glyph_byte(st.FONT[ch][r]) for r in range(7)): ch
           for ch in "0123456789ABCDEF"}
    out = ""
    for k in range(n):
        got = tuple(bus.ram[mt.vaddr(line + r, col + k)] for r in range(7))
        out += tab.get(got, "?")
    return out


def test_known_module_lights_exactly_its_own_row():
    pack = fake_pack()
    refs = {"B1": mt.REFS["B1"], "B3": mt.module_sums(pack)}
    rom = mt.build(refs=refs)
    bus, sweeps = run(rom, pack)
    assert sweeps >= 1, "no sweep completed"

    # The B3 row (version index 1) fully lit, the B1 row fully dark.
    for j in range(10):
        assert bus.ram[mt.vaddr(mt.vers_line(1), mt.BOX_COL0 + 2 * j)] \
            == 0x3F, f"B3 box {j} dark on a matching module"
    for j in range(9):
        assert bus.ram[mt.vaddr(mt.vers_line(0), mt.BOX_COL0 + 2 * j)] \
            == 0x00, f"B1 box {j} lit on a non-matching module"

    # The on-screen sum of block 0 is the real sum of the pack's first KB.
    want = f"{mt.module_sums(pack)[0]:04X}"
    assert digits_at(bus, mt.block_line(0), mt.SUM_COL, 4) == want


def test_one_corrupt_byte_darkens_exactly_its_block():
    pack = bytearray(fake_pack())
    refs = {"B3": mt.module_sums(bytes(pack))}
    pack[3 * 1024 + 100] ^= 0x40                # corrupt block 3
    rom = mt.build(refs=refs)
    bus, _ = run(rom, bytes(pack))
    for j in range(10):
        want = 0x00 if j == 3 else 0x3F
        got = bus.ram[mt.vaddr(mt.vers_line(0), mt.BOX_COL0 + 2 * j)]
        assert got == want, f"box {j}: {got:02X}, want {want:02X}"


def test_empty_slot_reads_fc00_everywhere():
    rom = mt.build()
    bus, sweeps = run(rom, None)
    assert sweeps >= 1
    for i in (0, 7, 15):
        assert digits_at(bus, mt.block_line(i), mt.SUM_COL, 4) == "FC00", \
            f"block {i} does not read as floating bus"
    for v in range(4):
        for j in range(9):
            assert bus.ram[mt.vaddr(mt.vers_line(v),
                                    mt.BOX_COL0 + 2 * j)] == 0x00


def test_sweeps_tick_and_the_dead_chip_signature_reads_low():
    # A mostly-zeros pack -- the degradation signature of a dying MHB 2616
    # -- must show low sums and match nothing.
    pack = bytes(1 if i % 200 == 0 else 0 for i in range(8192))
    rom = mt.build()
    bus, sweeps = run(rom, pack, min_sweeps=2)
    assert sweeps >= 2, "scanner does not keep sweeping"
    assert bus.ram[mt.vaddr(mt.LN_TICK, mt.TICK_COL0)] == 0x3F
    s = digits_at(bus, mt.block_line(0), mt.SUM_COL, 4)
    assert int(s, 16) < 0x100, f"mostly-zero chip read sum {s}"
    for v in range(4):
        assert bus.ram[mt.vaddr(mt.vers_line(v), mt.BOX_COL0)] == 0x00


def test_reference_tables_have_the_documented_shapes():
    assert [len(s) for s in mt.REFS.values()] == [9, 9, 9, 10]
    assert list(mt.REFS) == ["B1", "B2", "B2A", "B3"]
