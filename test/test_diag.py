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
N = make_diag.N_RUNGS
IO_RUNG = N - 2
RAM_RUNG = N - 1


def run(ram_top=0x08, steps=3_000_000, stage="cpu", **kw):
    rom = make_diag.build(ram_top=ram_top, stage=stage)
    bus = Bus(rom, **kw)
    cpu = CPU(bus)
    for _ in range(steps):
        cpu.step()
        if cpu.halted:
            break
    seen = {a - LO for a in bus.rom_reads if LO <= a <= LO + 31}
    bit = make_diag.BIT_BASE
    return {
        "started": {n for n in range(N) if n in seen},
        "failed": {n for n in range(N) if make_diag.RUNG_FAILED + n in seen},
        "passed": make_diag.ALL_PASSED in seen,
        "bits": {n - bit for n in range(bit, bit + 8) if n in seen},
    }


def test_healthy_machine_climbs_every_rung():
    r = run()
    assert r["started"] == set(range(N)), "did not reach every rung"
    assert not r["failed"], f"failed rungs on a good machine: {r['failed']}"
    assert r["passed"]
    assert not r["bits"]


def test_no_stack_instruction_anywhere():
    """The whole point: this must run on a machine whose RAM is suspect."""
    from i8080dis import decode
    forbidden = ({0xC9, 0xCD, 0xE3, 0xF9}
                 | {0xC0 | (c << 3) for c in range(8)}
                 | {0xC4 | (c << 3) for c in range(8)}
                 | {0xC7 | (n << 3) for n in range(8)}
                 | {0xC5, 0xD5, 0xE5, 0xF5}
                 | {0xC1, 0xD1, 0xE1, 0xF1}
                 | {0x31})                       # LXI SP
    for stage in make_diag.STAGES:
        rom = make_diag.build(stage=stage)
        body = rom[make_diag.ENTRY:make_diag.FILLER_OFF]
        i = 0
        while i < len(body):
            assert body[i] not in forbidden, \
                f"{stage}: stack opcode {body[i]:02X} at +{i:04X}"
            _, n = decode(body, i)
            i += n


def test_data_bus_rung_names_the_broken_line():
    """A marginal data line must stop the ladder at rung 5 and name itself."""
    pat = make_diag.PATTERN_ADDR
    for bit in (6, 7, 0):
        r = run(rom_stuck=(0xFF, 1 << bit), rom_stuck_range=(pat, pat + 0xFF))
        assert r["failed"] == {5}, f"D{bit}: failed {r['failed']}, want rung 5"
        assert r["bits"] == {bit}, f"D{bit}: named {r['bits']}"
        assert RAM_RUNG not in r["started"], \
            "went on to test RAM after a bus fault"


def test_rom_sum_rung_catches_a_single_wrong_byte():
    # In space that is summed but never executed, so the fault shows up as a
    # checksum failure rather than as a processor executing rubbish.
    addr = make_diag.BASE + 0x1D00
    r = run(rom_stuck=(0xFF, 0x01), rom_stuck_range=(addr, addr))
    assert r["failed"] == {6}, f"failed {r['failed']}, want rung 6"


def test_ram_rung_is_last_and_names_its_bits():
    r = run(stuck={a: (0xFF, 0x08) for a in range(0x800)})
    assert r["started"] == set(range(N)), "did not get as far as RAM"
    assert r["failed"] == {RAM_RUNG}
    assert r["bits"] == {3}


def test_unrefreshed_ram_reaches_the_ram_rung_too():
    r = run(decay_after=400)
    assert r["failed"] == {RAM_RUNG}, "blamed something other than RAM"


def test_processor_rungs_run_before_any_write_or_port_access():
    """Rungs 0-6 must stand on nothing but the CPU and the ROM path.

    A machine whose port write goes nowhere still has a working processor,
    and the ladder has to say so instead of stopping at the bottom.  This is
    the fault an earlier version mislabelled: it cleared the startup map
    inside rung 0, so a dead port read as "never executed an instruction".
    """
    r = run(sticky_map=True)
    assert r["started"] == set(range(N - 1)), \
        f"processor rungs did not run without I/O: {r['started']}"
    assert r["failed"] == {IO_RUNG}, f"blamed {r['failed']}, want the I/O rung"
    assert RAM_RUNG not in r["started"], "tested RAM it could not reach"
    assert not r["passed"]


def test_io_stage_climbs_every_rung_on_a_good_machine():
    r = run(stage="io")
    assert r["started"] == set(range(N)), "did not reach every rung"
    assert not r["failed"], f"failed rungs on a good machine: {r['failed']}"
    assert r["passed"]


def test_io_stage_separates_a_dead_port_from_dead_memory():
    """A port write that goes nowhere leaves the startup map in place.

    Every read then keeps coming from ROM and RAM is unreachable however
    healthy it is.  The cycles below must all still complete -- the write,
    the read, the OUT itself -- and only the read-back check may fail.
    """
    r = run(stage="io", sticky_map=True)
    assert r["started"] == set(range(5)), \
        f"stopped before the read-back: {r['started']}"
    assert r["failed"] == {4}, f"blamed {r['failed']}, want the map rung"
    assert not r["passed"]


def test_io_stage_still_finds_a_real_ram_fault():
    r = run(stage="io", stuck={a: (0xFF, 0x08) for a in range(0x800)})
    assert r["failed"] == {5}, f"blamed {r['failed']}, want the march"
    assert r["bits"] == {3}


def test_map_stage_names_whichever_write_clears_the_mirror():
    """Every candidate must be found, not just the one the model prefers.

    The stage reports inverted: the rung that "fails" is the rung that
    worked.  Each case here makes exactly one candidate effective and
    asserts the sweep lands on it -- so a machine that responds only to the
    control register is distinguished from one that responds to port A,
    which is the distinction the whole stage exists to draw.
    """
    cases = {
        2: dict(map_clear_ports=(0x00,)),          # any I/O write at all
        3: dict(map_clear_ports=(0xF4,)),          # port A
        4: dict(map_clear_ports=(0xF5,)),          # port B
        5: dict(map_clear_ports=(0xF6,)),          # port C
        6: dict(map_clear_ports=(0xF7,)),          # control register
        7: dict(map_clear_ports=(), map_clear_on_in=(0xF6,)),   # a read
    }
    for rung, kw in cases.items():
        r = run(stage="map", **kw)
        assert r["failed"] == {rung}, \
            f"candidate {rung}: named {r['failed']}"
        assert r["started"] == set(range(rung + 1)), \
            f"candidate {rung}: went past the one that worked"


def test_map_stage_says_so_when_nothing_clears_the_mirror():
    """A latch that never clears is a finding, not a silent green."""
    r = run(stage="map", map_clear_ports=())
    assert r["started"] == set(range(N)), "gave up before trying them all"
    assert r["failed"] == {8}, f"named {r['failed']}, want the none-of-them rung"
    assert not r["passed"]


def test_a_mode_set_takes_the_rom_out_of_the_machine():
    """The lesson that cost three firmware builds, as an assertion.

    `OUT F7h` with bit 7 set is an 8255 mode set.  It clears the port C
    latches, which drops PC4, which is what puts the ROM at E000-FFFF.  A
    program that issues it while executing from ROM does not reach its next
    instruction.  The monitor survives only by copying that instruction
    into RAM first (monit3B E0A3-E0B7).
    """
    from make_ramtest import Asm, BASE, ENTRY, A

    def reached_the_end(trampoline: bool) -> bool:
        a = Asm(BASE + ENTRY)
        a.mvi(A, 0x00)
        a.out(0xF4)                       # clear the startup map, keep ROM
        if trampoline:
            a.lxi(2, "tramp")             # RP_H
            a.mvi(1, 4)                   # C = 4 bytes
            a.label("copy")
            a.mov(7, 6)                   # MOV A,M  -- from ROM
            a.mov(6, 7)                   # MOV M,A  -- to RAM, same address
            a.inx(2)
            a.dcr(1)
            a.jnz("copy")
        a.mvi(A, 0x82)
        a.out(0xF7)                       # <-- the ROM leaves here
        a.label("tramp")
        a.mvi(A, 0x09)
        a.out(0xF7)                       # <-- ...and comes back here
        a.mvi(A, 0xEE)
        a.label("done")
        a.jmp("done")

        rom = bytearray(make_diag.build())
        body = a.link()
        rom[ENTRY:ENTRY + len(body)] = body
        bus = Bus(bytes(rom))
        cpu = CPU(bus)
        for _ in range(200_000):
            cpu.step()
            if cpu.halted or cpu.r["A"] == 0xEE:
                break
        return cpu.r["A"] == 0xEE and bus.rom_visible

    assert not reached_the_end(trampoline=False), \
        "the emulator let a bare mode set survive -- it models the wrong machine"
    assert reached_the_end(trampoline=True), \
        "the RAM trampoline did not carry execution across the ROM drop-out"


def test_rom_checksum_excludes_the_beacon_page():
    """Rung 6 must not read the beacons -- reading one *is* speaking.

    The first version summed the whole image and announced every beacon at
    once, including 'all rungs failed' and 'everything passed' together.
    """
    rom = make_diag.build()
    assert sum(rom[:make_diag.BEACON_OFF]) & 0xFF == 0
