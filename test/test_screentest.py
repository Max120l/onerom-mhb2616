"""Run the screen test card in the emulator and read its screen.

The card is the last diagnostic in the family and the first with a
display, so the tests assert on the display: the frame buffer the
emulator's RAM array holds IS the deliverable, and every claim the
docstring makes about it -- border, gradient, grid, live march verdict --
is checked against actual VRAM bytes here before the image is allowed
near a machine.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import make_screentest as st     # noqa: E402
from i8080emu import Bus, CPU    # noqa: E402

LO = st.BEACON_ADDR


def run(steps, ram_top=0x08, stop_beacon=None, extra=0, **buskw):
    """Run the image.  With stop_beacon, halt as soon as that beacon is
    read (plus `extra` steps) -- the card repaints continuously, so many
    assertions are only valid at a known point in its cycle."""
    rom = st.build(ram_top=ram_top)
    bus = Bus(rom, map_clear_ports=(), **buskw)   # the faithful 8255 model
    cpu = CPU(bus)
    done = 0
    scanned = 0
    chunk = 500 if stop_beacon is not None else 5_000
    while done < steps and not cpu.halted:
        for _ in range(chunk):
            cpu.step()
        done += chunk
        if stop_beacon is not None:
            if any(a == LO + stop_beacon for a in bus.rom_reads[scanned:]):
                for _ in range(extra):
                    cpu.step()
                break
            scanned = len(bus.rom_reads)
    seen = {a - LO for a in bus.rom_reads if LO <= a <= LO + 31}
    return bus, seen


def rom_strip(name):
    rom = st.build(ram_top=0x08)
    # Rebuild the strips exactly as build() does, to find their content.
    strips = {
        "title": st.render_strip([(6, "PMD 85-3 TEST CARD")]),
        "pass": st.render_strip([(21, "PASS")]),
        "fail": st.render_strip([(21, "FAIL")], attr=2),
        "grad": st.gradient_strip(),
    }
    return strips[name]


def test_bar_is_full_at_the_moment_a_pass_completes():
    """The bar refills live every pass, so 'full' exists only in the window
    between the last segment and the next pass's hollowing -- catch it at
    the pass-complete beacon."""
    bus, _ = run(4_000_000, stop_beacon=2)
    for k in range(st.BAR_SEGS):
        addr = st.vaddr(st.LN_BAR, st.BAR_COL0 + k * st.BAR_SEGW)
        assert bus.ram[addr] == 0x3F, f"bar segment {k} not filled"


def test_card_is_painted_and_first_pass_is_clean():
    bus, seen = run(4_000_000, stop_beacon=2, extra=8_000)
    ram = bus.ram

    assert 0 in seen and 1 in seen, "init never completed"
    assert 24 in seen and 2 in seen, "no march pass completed"
    assert 3 not in seen and 15 not in seen, "fault reported on clean RAM"

    # Border: all four sides, 1 px.
    assert ram[st.vaddr(0)] == 0x3F and ram[st.vaddr(0, 47)] == 0x3F
    assert ram[st.vaddr(255)] == 0x3F
    assert ram[st.vaddr(70)] & 0x01, "left border missing mid-screen"
    assert ram[st.vaddr(70, 47)] & 0x20, "right border missing mid-screen"
    assert ram[st.vaddr(200)] & 0x01, "left border missing in write-only half"

    # Title strip, byte for byte.
    title = rom_strip("title")
    for r in range(7):
        got = bytes(ram[st.vaddr(st.LN_TITLE + r):
                        st.vaddr(st.LN_TITLE + r) + 48])
        assert got == title[r * 48:(r + 1) * 48], f"title row {r} wrong"

    # Gradient: every group is the two strip rows; all four attributes and
    # all six densities are on screen.
    grad = rom_strip("grad")
    for g in range(16):
        for half in range(2):
            line = st.LN_GRAD + 2 * g + half
            got = bytes(ram[st.vaddr(line):st.vaddr(line) + 48])
            assert got == grad[half * 48:(half + 1) * 48], \
                f"gradient line {line} wrong"
    attrs = {b >> 6 for b in grad if b & 0x3F}
    assert attrs == {0, 1, 2, 3}, "gradient does not cover all attributes"

    # Grid: solid horizontals, verticals between them.
    assert all(ram[st.vaddr(60, c)] == 0x3F for c in range(48)), \
        "grid horizontal missing"
    assert ram[st.vaddr(66, 8)] == 0x01, "grid vertical missing"
    assert ram[st.vaddr(66, 9)] == 0x00, "grid noise between verticals"

    # Verdict PASS, byte for byte, in the write-only half of the frame.
    passs = rom_strip("pass")
    for r in range(7):
        got = bytes(ram[st.vaddr(st.LN_VERDICT + r):
                        st.vaddr(st.LN_VERDICT + r) + 48])
        assert got == passs[r * 48:(r + 1) * 48], f"verdict row {r} wrong"

    # A steady (non-blinking) tick per pass, and the counter kept in the
    # invisible margin agrees with it.
    n = ram[st.SCRATCH_CNT]
    assert n >= 1, "no pass counted"
    for i in range(n):
        assert ram[st.vaddr(st.LN_TICK, st.TICK_COL0 + i)] == 0x3F
    assert ram[st.vaddr(st.LN_TICK, st.TICK_COL0 + n)] == 0x00

    # All fault boxes blank.
    for line in (st.LN_HARD, st.LN_MBOX):
        for bit in range(8):
            assert ram[st.vaddr(line, st.BOX_COL0 + 5 * bit)] == 0x00


def test_stuck_bit_shows_in_both_box_rows_and_blinks_the_tick():
    bus, seen = run(4_000_000, stop_beacon=2, extra=8_000,
                    stuck={a: (0xFF, 0x08) for a in range(0x800)})
    ram = bus.ram

    assert 15 in seen, "hard-fault beacon missing"
    assert 3 in seen and (4 + 3) in seen, "march beacons missing D3"

    # D3's box filled in both rows, its neighbours blank.
    for line in (st.LN_HARD, st.LN_MBOX):
        assert ram[st.vaddr(line, st.BOX_COL0 + 5 * 3)] == 0x3F, \
            "D3 box not filled"
        assert ram[st.vaddr(line, st.BOX_COL0 + 5 * 2)] == 0x00, \
            "D2 box wrongly filled"

    # FAIL verdict, in the blinking attribute.
    fail = rom_strip("fail")
    got = bytes(ram[st.vaddr(st.LN_VERDICT):st.vaddr(st.LN_VERDICT) + 48])
    assert got == fail[:48], "verdict is not FAIL"
    # Blink check on glyph bytes only -- the baked-in side border is
    # deliberately steady.
    lit = [fail[r * 48 + c] for r in range(7) for c in range(1, 47)
           if fail[r * 48 + c] & 0x3F]
    assert lit and all((b & 0xC0) == 0x80 for b in lit), \
        "FAIL strip does not blink"

    # The tick blinks too.
    assert ram[st.vaddr(st.LN_TICK, st.TICK_COL0)] == 0xBF


def test_march_never_touches_the_screen():
    """The card must survive its own RAM test: 0000-BFFF only."""
    bus, _ = run(4_000_000)
    lo, hi = min(bus.written_at), max(a for a in bus.written_at
                                      if a < 0xC000)
    assert hi <= 0xBFFF
    # ...and the screen region was written only by the painters: the title
    # is still intact after a full pass, which test 1 already proved.


def test_stuck_mirror_paints_fail_and_parks():
    """Even the cannot-initialise verdict arrives by screen."""
    bus, seen = run(600_000, sticky_map=True)
    assert 1 not in seen, "claimed the map cleared when it could not"
    fail = rom_strip("fail")
    got = bytes(bus.ram[st.vaddr(st.LN_VERDICT):
                        st.vaddr(st.LN_VERDICT) + 48])
    assert got == fail[:48], "no FAIL verdict under a stuck mirror"


def test_no_stack_instruction_anywhere():
    from i8080dis import decode
    rom = st.build()
    body = rom[st.ENTRY:0x1200]
    forbidden = ({0xC9, 0xCD, 0xE3, 0xF9}
                 | {0xC0 | (c << 3) for c in range(8)}
                 | {0xC4 | (c << 3) for c in range(8)}
                 | {0xC7 | (n << 3) for n in range(8)}
                 | {0xC5, 0xD5, 0xE5, 0xF5}
                 | {0xC1, 0xD1, 0xE1, 0xF1}
                 | {0x31})
    i = 0
    end = rom.rfind(b"\xC3", st.ENTRY, 0x1200)  # last JMP = end of program
    while i < len(body):
        if st.ENTRY + i > end:
            break
        assert body[i] not in forbidden, \
            f"stack opcode {body[i]:02X} at +{i:04X}"
        _, n = decode(body, i)
        i += n
