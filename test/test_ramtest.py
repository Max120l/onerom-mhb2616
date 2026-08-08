"""Run the RAM-test ROM in an 8080 emulator before it meets a machine.

The sibling project's hardest lesson was that the instrument is wrong more
often than the machine is surprising. This image is an instrument that will
be trusted to say "your RAM is bad", so it gets tested against a simulated
machine whose RAM is good, and against ones whose RAM is broken in known
ways, and it has to give the right answer to each.
"""

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import make_ramtest              # noqa: E402
from i8080emu import Bus, CPU    # noqa: E402

BEACON_LO = make_ramtest.BEACON_ADDR
BEACON_HI = BEACON_LO + 31


def run(stuck=None, steps=60_000_000, ram_top=0x20, **buskw):
    """Run the image; return the set of beacon numbers it lit.

    A reduced RAM range by default: March C- is ten operations per cell, so
    a full 48 KB sweep is millions of emulated instructions per pass, and
    the logic under test is identical either way.

    Stops as soon as the program has finished a pass *and* reported --
    the image loops forever by design, so without this every test would run
    to its step ceiling instead of to its answer.
    """
    rom = make_ramtest.build(ram_top=ram_top)
    bus = Bus(rom, stuck=stuck, **buskw)
    cpu = CPU(bus)
    seen = set()
    done = 0
    while done < steps and not cpu.halted:
        for _ in range(100_000):
            cpu.step()
        done += 100_000
        seen = {a - BEACON_LO for a in bus.rom_reads if BEACON_LO <= a <= BEACON_HI}
        if 2 in seen:                     # a pass completed
            if 3 not in seen:
                break                     # clean: nothing more to report
            # A fault was found; give the report loop room to blink it all,
            # then stop.  One further pass is plenty.
            for _ in range(300_000):
                cpu.step()
            seen = {a - BEACON_LO for a in bus.rom_reads
                    if BEACON_LO <= a <= BEACON_HI}
            break
    return seen


def test_image_is_the_right_shape():
    rom = make_ramtest.build()
    body_end = rom.index(b"\xE5" * 8) if b"\xE5" * 8 in rom else len(rom)
    assert len(rom) == 0x2000
    assert rom[0] == 0xC3                      # a JMP at the reset entry
    target = rom[1] | (rom[2] << 8)
    assert target == make_ramtest.BASE + make_ramtest.ENTRY


def test_clean_machine_reports_clean():
    seen = run()
    assert 0 in seen, "never reached the program"
    assert 1 in seen, "never cleared the startup map"
    assert 2 in seen, "never completed a pass"
    assert 3 not in seen, "reported a fault on perfect RAM"
    assert not any(b in seen for b in range(4, 15)), "spurious detail beacons"


def test_init_survives_a_faithfully_modelled_machine():
    """The regression that mattered: the init must work when ONLY a mode
    set ends the mirror -- the real 8255's behaviour -- not just under the
    any-write-clears-it shortcut the reference emulator uses.  Both prior
    versions of the init pass the shortcut and fail the machine.
    """
    seen = run(map_clear_ports=())
    assert 1 in seen, "the trampoline did not survive the ROM-less window"
    assert 2 in seen, "never completed a pass on the faithful model"
    assert 3 not in seen, "reported a fault on perfect RAM"


def test_program_never_touches_the_stack():
    # No PUSH, POP, CALL, RET or RST reaches the machine: those are the
    # opcodes that would need the memory under test.  Checked over the
    # program body only -- the beacon page is data.
    rom = make_ramtest.build()
    body = rom[make_ramtest.ENTRY:make_ramtest.BEACON_OFF]
    forbidden = ({0xC9, 0xCD, 0xE3, 0xF9}                    # RET CALL XTHL SPHL
                 | {0xC0 | (c << 3) for c in range(8)}       # Rcc
                 | {0xC4 | (c << 3) for c in range(8)}       # Ccc
                 | {0xC7 | (n << 3) for n in range(8)}       # RST
                 | {0xC5, 0xD5, 0xE5, 0xF5}                  # PUSH
                 | {0xC1, 0xD1, 0xE1, 0xF1})                 # POP
    # Walk instructions properly rather than scanning bytes, so operands are
    # never mistaken for opcodes.
    from i8080dis import decode
    i = 0
    while i < len(body):
        op = body[i]
        assert op not in forbidden, f"stack instruction {op:02X} at +{i:04X}"
        _, n = decode(body, i)
        i += n


def test_stack_pointer_is_never_set():
    rom = make_ramtest.build()
    body = rom[make_ramtest.ENTRY:make_ramtest.BEACON_OFF]
    assert 0x31 not in body[:3], "LXI SP in the first instruction"


@pytest.mark.parametrize("bit", [0, 3, 7])
def test_stuck_data_bit_is_found_and_named(bit):
    # One byte in the low third reads back with a bit forced HIGH.
    #
    # Forcing it low would be the obvious choice and is a trap: it only
    # shows up when the expected byte has that bit set, and the fill
    # pattern at a given address may not.  Forced high always differs
    # somewhere, so the test measures the ROM rather than the pattern.
    stuck = {0x1234: (0xFF, 1 << bit)}   # inside the reduced range
    seen = run(stuck=stuck)
    assert 2 in seen, "never completed a pass"
    assert 3 in seen, "missed the fault"
    assert (4 + bit) in seen, f"did not name D{bit}"
    others = {b for b in range(4, 12)} - {4 + bit}
    assert not (others & seen), "named data bits that were fine"
    assert 12 in seen, "did not place the fault in the low third"
    assert 13 not in seen and 14 not in seen


def force_a_clear_bit_high(addr):
    """A fault mask that is guaranteed to change the byte at `addr`.

    The fill pattern is H xor L, so which bits are already set depends on
    the address.  Picking a fixed bit makes the test silently vacuous
    wherever that bit happens to match -- which it did, twice, while this
    file was being written.  Choose a bit that is clear *there*.
    """
    expected = ((addr >> 8) ^ (addr & 0xFF)) & 0xFF
    for bit in range(8):
        if not (expected >> bit) & 1:
            return (0xFF, 1 << bit)
    raise AssertionError(f"no clear bit at {addr:04X}")


def test_fault_region_is_placed_correctly():
    """Each third of RAM is named correctly when a fault lands in it.

    Sized so each case sweeps only as much array as it must: March C- is
    ten operations per cell, and an emulated 48 KB pass is millions of
    instructions.  The low third is already covered by the stuck-bit tests
    above, which assert beacon 12.
    """
    for addr, want, ram_top in ((0x5000, 13, 0x60), (0x9000, 14, 0xC0)):
        seen = run(stuck={addr: force_a_clear_bit_high(addr)},
                   ram_top=ram_top, steps=60_000_000)
        assert 3 in seen and want in seen, f"{addr:04X} not placed"
        assert not ({12, 13, 14} - {want}) & seen, f"{addr:04X} placed twice"


def test_whole_chip_dead_is_reported():
    # A whole 16K reading back as zeros -- a dead chip, not a dead bit.
    stuck = {a: (0x00, 0x00) for a in range(0x1000, 0x1400)}
    seen = run(stuck=stuck)
    assert 3 in seen
    assert 12 in seen, "did not place the fault"


def test_beacons_live_where_the_firmware_looks():
    # The firmware watches a fixed page; if this moves, both must move.
    assert make_ramtest.BEACON_OFF == 0x1F00
    assert make_ramtest.BEACON_ADDR == 0xFF00


def run_decay(decay_after, ram_top=0x20, steps=60_000_000):
    """Run against DRAM that loses its charge after `decay_after` cycles."""
    rom = make_ramtest.build(ram_top=ram_top)
    bus = Bus(rom, decay_after=decay_after)
    cpu = CPU(bus)
    done = 0
    seen = set()
    while done < steps and not cpu.halted:
        for _ in range(100_000):
            cpu.step()
        done += 100_000
        seen = {a - BEACON_LO for a in bus.rom_reads if BEACON_LO <= a <= BEACON_HI}
        if 2 in seen:
            for _ in range(400_000):
                cpu.step()
            seen = {a - BEACON_LO for a in bus.rom_reads
                    if BEACON_LO <= a <= BEACON_HI}
            break
    return seen


def test_unrefreshed_dram_is_not_blamed_on_the_chips():
    """The control that stops a refresh fault reading as 'every chip bad'.

    Cells that accept and return data immediately, but lose it long before
    the march comes back round, must light the march fault (3) and leave
    the hard-fault beacon (15) dark.  Getting this backwards would send
    someone out to buy DRAM they do not need.
    """
    seen = run_decay(decay_after=500)
    assert 3 in seen, "march did not notice the memory losing its contents"
    assert 15 not in seen, "blamed the cells for what is a refresh fault"
    assert not ({16 + b for b in range(8)} & seen), "named cells as hard-failed"


def test_hard_fault_lights_both():
    """A genuinely dead bit fails immediately as well as over a march."""
    seen = run(stuck={0x1234: (0xFF, 0x01)})
    assert 3 in seen, "march missed it"
    assert 15 in seen, "immediate read-back missed a hard fault"
    assert 16 in seen, "did not name D0 as hard-failed"


def test_clean_machine_lights_no_hard_fault():
    seen = run()
    assert 15 not in seen and not ({16 + b for b in range(8)} & seen)
