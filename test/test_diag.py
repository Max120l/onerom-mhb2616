"""Run the CPU/bus/RAM diagnostic ladder in the emulator before trusting it.

Each rung must pass on a healthy machine and fail on exactly the fault it
exists to catch -- no earlier rung firing first, and no later rung reached.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import make_diag                 # noqa: E402
from i8080emu import Bus, CPU    # noqa: E402

LO = make_diag.BEACON_ADDR


def run(ram_top=0x08, steps=3_000_000, **kw):
    rom = make_diag.build(ram_top=ram_top)
    bus = Bus(rom, **kw)
    cpu = CPU(bus)
    for _ in range(steps):
        cpu.step()
        if cpu.halted:
            break
    seen = {a - LO for a in bus.rom_reads if LO <= a <= LO + 31}
    return {
        "started": {n for n in range(8) if n in seen},
        "failed": {n - 8 for n in range(8, 16) if n in seen},
        "passed": 16 in seen,
        "bits": {n - 17 for n in range(17, 25) if n in seen},
    }


def test_healthy_machine_climbs_every_rung():
    r = run()
    assert r["started"] == set(range(8)), "did not reach every rung"
    assert not r["failed"], f"failed rungs on a good machine: {r['failed']}"
    assert r["passed"]
    assert not r["bits"]


def test_no_stack_instruction_anywhere():
    """The whole point: this must run on a machine whose RAM is suspect."""
    from i8080dis import decode
    rom = make_diag.build()
    body = rom[make_diag.ENTRY:make_diag.FILLER_OFF]
    forbidden = ({0xC9, 0xCD, 0xE3, 0xF9}
                 | {0xC0 | (c << 3) for c in range(8)}
                 | {0xC4 | (c << 3) for c in range(8)}
                 | {0xC7 | (n << 3) for n in range(8)}
                 | {0xC5, 0xD5, 0xE5, 0xF5}
                 | {0xC1, 0xD1, 0xE1, 0xF1}
                 | {0x31})                       # LXI SP
    i = 0
    while i < len(body):
        assert body[i] not in forbidden, f"stack opcode {body[i]:02X} at +{i:04X}"
        _, n = decode(body, i)
        i += n


def test_data_bus_rung_names_the_broken_line():
    """A marginal data line must stop the ladder at rung 5 and name itself."""
    pat = make_diag.PATTERN_ADDR
    for bit in (6, 7, 0):
        r = run(rom_stuck=(0xFF, 1 << bit), rom_stuck_range=(pat, pat + 0xFF))
        assert r["failed"] == {5}, f"D{bit}: failed {r['failed']}, want rung 5"
        assert r["bits"] == {bit}, f"D{bit}: named {r['bits']}"
        assert 7 not in r["started"], "went on to test RAM after a bus fault"


def test_rom_sum_rung_catches_a_single_wrong_byte():
    # In space that is summed but never executed, so the fault shows up as a
    # checksum failure rather than as a processor executing rubbish.
    addr = make_diag.BASE + 0x1D00
    r = run(rom_stuck=(0xFF, 0x01), rom_stuck_range=(addr, addr))
    assert r["failed"] == {6}, f"failed {r['failed']}, want rung 6"


def test_ram_rung_is_last_and_names_its_bits():
    r = run(stuck={a: (0xFF, 0x08) for a in range(0x800)})
    assert r["started"] == set(range(8)), "did not get as far as RAM"
    assert r["failed"] == {7}
    assert r["bits"] == {3}


def test_unrefreshed_ram_reaches_the_ram_rung_too():
    r = run(decay_after=400)
    assert r["failed"] == {7}, "blamed something other than RAM"


def test_rom_checksum_excludes_the_beacon_page():
    """Rung 6 must not read the beacons -- reading one *is* speaking.

    The first version summed the whole image and announced every beacon at
    once, including 'all rungs failed' and 'everything passed' together.
    """
    rom = make_diag.build()
    assert sum(rom[:make_diag.BEACON_OFF]) & 0xFF == 0
