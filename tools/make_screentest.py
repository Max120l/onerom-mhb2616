#!/usr/bin/env python3
"""Build an 8 KB ROM: a screen test card with a live March C- RAM test.

The last diagnostic in the family, and the first one written for a
machine known to work: everything earlier reported through an LED because
the screen could not be trusted, and this one reports through the screen
because now it can. It still keeps the family discipline -- no stack, no
interrupts, every branch a jump -- because a diagnostic that needs the
thing it is testing is not a diagnostic.

The screen (288x256, six pixels per VRAM byte, bit 0 leftmost, bits 6-7
the attribute):

    top border ................ 1-px frame, all four sides: overscan and
                                centering
    title ..................... PMD 85-3 TEST CARD
    gradient band ............. 6 dither densities x all 4 attributes.
                                On a stock -3 the attributes are bright /
                                dim / blinking bright / blinking dim, so
                                this is a brightness gradient AND a blink
                                test; on a colour-modified machine it is
                                four colour bars
    grid ...................... 24 px x 12 line crosshatch: geometry,
                                linearity, centering
    MARCH C- RAM TEST ......... the RAM test, live:
      progress bar ............ 8 segments, one per march element
      HARD row ................ 8 boxes, D0..D7: bits that failed the
                                immediate write/read-back control
      MARCH row ............... 8 boxes, D0..D7: bits that ever failed
                                the march proper
      PASS / FAIL ............. verdict of the last pass; FAIL is drawn
                                in the blinking attribute
      tick line ............... one tick per completed pass: steady tick
                                clean, blinking tick a pass that failed.
                                40 passes to a line, then it clears

The march covers 0000-BFFF -- everything except the video RAM it is
drawing on. Video RAM's own test is the picture: the card IS the VRAM
test, every byte of it, including the write-only half at E000-FFFF.

It reports on the LED too: the beacons are `make_ramtest.py`-compatible
(0 running, 1 map cleared, 24 pass start, 2 pass done, 3 march fault,
4-11 bits, 15 hard fault), so firmware built `-DMHB_DIAG=3` gives the
verdict lamp alongside the screen.

Init is the proven paging trampoline (see docs/PMD85-3.md): the mode set
that ends the startup mirror also removes the ROM from the address space
for three bytes, which are executed from a RAM copy made moments before.
If the mirror never clears, the card paints FAIL and parks -- the screen
works under the mirror, so even that verdict is visible.

    ./make_screentest.py -o screentest.bin
    ./gen_rom_images.py -o ../firmware/rom_images.c --monitor screentest.bin
    # build the firmware with -DMHB_DIAG=3

Drawing rules the code lives by:

- The frame's lower half (lines 128-255) sits at E000-FFFF, which the
  CPU can only WRITE -- reads there return this ROM.  So every paint is
  a blind whole-byte store; nothing ever reads the screen back.
- Two bytes of state survive between passes (pass counter, hard bits)
  and they live in VRAM's invisible margin -- each line is 64 bytes but
  only 48 are shown, and the margin of the readable half (C000-DFFF) is
  real RAM the march never touches.
"""

import argparse
import sys
from pathlib import Path

from make_ramtest import (Asm, BASE, ENTRY, BEACON_OFF, BEACON_ADDR, ROM_SIZE,
                          RAM_TOP, B, C, D, E, H, L, M, A, RP_B, RP_D, RP_H)

VRAM = 0xC000
STRIDE = 64
COLS = 48                      # visible bytes per line


def vaddr(line: int, col: int = 0) -> int:
    return VRAM + line * STRIDE + col


# ---------------------------------------------------------------------------
# Layout, in screen lines.  The readable half ends at line 127.
# ---------------------------------------------------------------------------
LN_TITLE = 3                  # 7 rows
LN_GRAD = 13                  # 32 lines: 16 groups of 2
LN_GRID0, LN_GRID1 = 48, 119  # crosshatch area, inclusive
LN_MARCH = 124                # 7 rows, "MARCH C- RAM TEST"
LN_BAR = 135                  # 3 rows
LN_HARD = 143                 # 6 rows of boxes, label beside
LN_MBOX = 152                 # 6 rows of boxes, label beside
LN_DIGIT = 161                # 7 rows, 0..7 under the box columns
LN_VERDICT = 176              # 7 rows, PASS / FAIL
LN_TICK = 190                 # 1 row

BOX_COL0 = 8                  # box i occupies cols BOX_COL0+5i .. +1
BOX_W = 2
BAR_COL0, BAR_SEGW, BAR_SEGS = 4, 5, 8
TICK_COL0, TICK_MAX = 4, 40

# Two bytes of cross-pass state in the invisible margin of a readable line.
SCRATCH_CNT = vaddr(1, 60)
SCRATCH_HARD = vaddr(1, 61)

# ---------------------------------------------------------------------------
# A 5x7 font, bit 0 leftmost to match the hardware.
# ---------------------------------------------------------------------------
FONT = {
    "0": ["#####", "#...#", "#..##", "#.#.#", "##..#", "#...#", "#####"],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": ["#####", "....#", "....#", "#####", "#....", "#....", "#####"],
    "3": ["#####", "....#", "....#", ".####", "....#", "....#", "#####"],
    "4": ["#...#", "#...#", "#...#", "#####", "....#", "....#", "....#"],
    "5": ["#####", "#....", "#....", "#####", "....#", "....#", "#####"],
    "6": ["#####", "#....", "#....", "#####", "#...#", "#...#", "#####"],
    "7": ["#####", "....#", "...#.", "..#..", "..#..", "..#..", "..#.."],
    "8": ["#####", "#...#", "#...#", "#####", "#...#", "#...#", "#####"],
    "9": ["#####", "#...#", "#...#", "#####", "....#", "....#", "#####"],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "G": [".####", "#....", "#....", "#.###", "#...#", "#...#", ".###."],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "-": [".....", ".....", ".....", "#####", ".....", ".....", "....."],
    " ": [".....", ".....", ".....", ".....", ".....", ".....", "....."],
}


def glyph_byte(row: str) -> int:
    v = 0
    for i, ch in enumerate(row):               # index 0 -> bit 0 -> leftmost
        if ch == "#":
            v |= 1 << i
    return v


def render_strip(placed: list[tuple[int, str]], attr: int = 0,
                 border: bool = True) -> bytes:
    """7 rows x 48 cols; text at byte columns; side border baked in."""
    rows = [[0] * COLS for _ in range(7)]
    for col0, text in placed:
        for k, ch in enumerate(text):
            g = FONT[ch]
            for r in range(7):
                rows[r][col0 + k] = glyph_byte(g[r])
    a = (attr & 3) << 6
    out = bytearray()
    for r in range(7):
        for c in range(COLS):
            v = rows[r][c]
            if v:
                v |= a
            if border:
                if c == 0:
                    v |= 0x01
                if c == COLS - 1:
                    v |= 0x20
            out.append(v)
    return bytes(out)


def gradient_strip() -> bytes:
    """2 lines x 48 cols: 4 attribute bands x 6 dither densities."""
    even = [0x00, 0x09, 0x15, 0x1B, 0x2F, 0x3F]
    odd = [0x00, 0x12, 0x2A, 0x36, 0x3D, 0x3F]
    out = bytearray()
    for pats in (even, odd):
        row = []
        for attr in range(4):
            for level in range(6):
                v = pats[level]
                if v:
                    v |= attr << 6
                row.extend([v, v])
        row[0] |= 0x01
        row[47] |= 0x20
        out.extend(row)
    return bytes(out)


# ---------------------------------------------------------------------------
# Code emitters.  Register discipline: init-time paints may use anything;
# per-pass paints must preserve B (the fault bits of the moment).
# ---------------------------------------------------------------------------
def emit_copy_loop(a: Asm, tag: str, dst: int, src: int, n: int) -> None:
    """C-counted copy of n bytes from ROM src to VRAM dst."""
    a.lxi(RP_H, dst)
    a.lxi(RP_D, src)
    a.mvi(C, n)
    a.label(tag)
    a.db(0x1A)                                  # LDAX D
    a.mov(M, A)
    a.inx(RP_D)
    a.inx(RP_H)
    a.dcr(C)
    a.jnz(tag)


def emit_strip(a: Asm, tag: str, line: int, src: int) -> None:
    for r in range(7):
        emit_copy_loop(a, f"{tag}{r}", vaddr(line + r), src + r * COLS, COLS)


def emit_fill(a: Asm, tag: str, dst: int, val: int, n: int) -> None:
    a.lxi(RP_H, dst)
    a.mvi(C, n)
    a.label(tag)
    a.mvi(M, val)
    a.inx(RP_H)
    a.dcr(C)
    a.jnz(tag)


def emit_column(a: Asm, tag: str, line0: int, line1: int, col: int,
                val: int) -> None:
    """Write val down one byte column, lines line0..line1 inclusive."""
    a.lxi(RP_H, vaddr(line0, col))
    a.lxi(RP_D, STRIDE)
    a.mvi(C, line1 - line0 + 1)
    a.label(tag)
    a.mvi(M, val)
    a.dad(RP_D)
    a.dcr(C)
    a.jnz(tag)


def emit_bar_segment(a: Asm, k: int) -> None:
    """Fill progress segment k.  A, H, L only -- B and C stay whole."""
    for r in range(3):
        a.lxi(RP_H, vaddr(LN_BAR + r, BAR_COL0 + k * BAR_SEGW))
        for _ in range(BAR_SEGW):
            a.mvi(M, 0x3F)
            a.inx(RP_H)


def emit_boxes(a: Asm, tag: str, line: int) -> None:
    """Paint the 8 bit boxes for the bits in B: filled bad, blank good.
    Uses A, E, H, L; preserves B, C, D."""
    for bit in range(8):
        col = BOX_COL0 + 5 * bit
        a.mvi(E, 0x00)
        a.mov(A, B)
        a.ani(1 << bit)
        a.jz(f"{tag}b{bit}")
        a.mvi(E, 0x3F)
        a.label(f"{tag}b{bit}")
        for r in range(6):
            a.lxi(RP_H, vaddr(line + r, col))
            a.mov(M, E)
            a.inx(RP_H)
            a.mov(M, E)


def emit_march(a: Asm, ram_top: int) -> None:
    """March C- over 0000..(ram_top<<8)-1, one pass.  B accumulates the
    failing bits and the pass continues -- the screen wants the whole
    picture, not the first casualty.  A progress segment fills after each
    element.  No branches on faults inside the loops: XRI of the expected
    value IS the fault mask, and OR-ing zero into B is free."""
    top = (ram_top << 8) - 1

    # -- element 0: immediate write/read-back (the hard-fault control) ----
    a.mvi(B, 0x00)
    a.lxi(RP_H, 0x0000)
    a.label("imm")
    a.mvi(M, 0xAA); a.mov(A, M); a.xri(0xAA); a.ora(B); a.mov(B, A)
    a.mvi(M, 0x55); a.mov(A, M); a.xri(0x55); a.ora(B); a.mov(B, A)
    a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz("imm")

    a.mov(A, B)
    a.sta(SCRATCH_HARD)
    a.ora(A)
    a.jz("nohard")
    a.beacon(15)
    a.label("nohard")
    emit_boxes(a, "hx", LN_HARD)
    emit_bar_segment(a, 0)

    # -- element 1: write 0 everywhere ------------------------------------
    a.mvi(B, 0x00)                              # march bits start clean
    a.lxi(RP_H, 0x0000)
    a.label("m0")
    a.mvi(M, 0x00); a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz("m0")
    emit_bar_segment(a, 1)

    # -- elements 2-5: r0w1 up, r1w0 up, r0w1 down, r1w0 down -------------
    for seg, (tag, expect_one, write, down) in enumerate(
            (("m1", False, 0xFF, False), ("m2", True, 0x00, False),
             ("m3", False, 0xFF, True), ("m4", True, 0x00, True)),
            start=2):
        a.lxi(RP_H, top if down else 0x0000)
        a.label(tag)
        a.mov(A, M)
        if expect_one:
            a.cma()
        a.ora(B); a.mov(B, A)
        a.mvi(M, write)
        if down:
            a.dcx(RP_H); a.mov(A, H); a.cpi(0xFF); a.jnz(tag)
        else:
            a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz(tag)
        emit_bar_segment(a, seg)

    # -- element 6: final read of 0 ---------------------------------------
    a.lxi(RP_H, 0x0000)
    a.label("m5")
    a.mov(A, M); a.ora(B); a.mov(B, A)
    a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz("m5")
    emit_bar_segment(a, 6)

    # -- element 7: address uniqueness, H xor L ---------------------------
    a.lxi(RP_H, 0x0000)
    a.label("au0")
    a.mov(A, H); a.xra(L); a.mov(M, A)
    a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz("au0")
    a.lxi(RP_H, 0x0000)
    a.label("au1")
    a.mov(A, H); a.xra(L); a.xra(M); a.ora(B); a.mov(B, A)
    a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz("au1")
    emit_bar_segment(a, 7)


def emit(a: Asm, ram_top: int, data: dict) -> None:
    a.di()
    a.beacon(0)

    # ---- the proven paging trampoline (see docs/PMD85-3.md) --------------
    a.lxi(RP_H, 0x0000)
    a.mvi(M, 0x5A)
    a.lxi(RP_H, "tramp")
    a.mvi(C, 3)
    a.label("tcopy")
    a.mov(A, M)
    a.mov(M, A)
    a.inx(RP_H)
    a.dcr(C)
    a.jnz("tcopy")
    a.mvi(B, 0x09)
    a.mvi(A, 0x82)
    a.out(0xF7)                                 # ROM leaves the address space
    a.label("tramp")
    a.mov(A, B)
    a.out(0xF7)                                 # ...and returns

    a.lxi(RP_H, 0x0000)
    a.mov(A, M)
    a.cpi(0xC3)
    a.jnz("mapok")
    # Mirror never cleared.  Paint the verdict -- the screen works even
    # under the mirror, writes always went to RAM -- and park.
    emit_strip(a, "sf", LN_VERDICT, data["fail"])
    a.label("stuck")
    a.jmp("stuck")
    a.label("mapok")
    a.beacon(1)

    # ---- clear the whole frame, C000-FFFF --------------------------------
    a.lxi(RP_H, VRAM)
    a.label("clr")
    a.mvi(M, 0x00)
    a.inx(RP_H)
    a.mov(A, H)
    a.ora(A)                                    # H wraps to 0 past FFFF
    a.jnz("clr")

    # ---- static card ------------------------------------------------------
    emit_column(a, "bdl", 0, 255, 0, 0x01)      # left border
    emit_column(a, "bdr", 0, 255, COLS - 1, 0x20)   # right border
    emit_fill(a, "bdt", vaddr(0), 0x3F, COLS)   # top
    emit_fill(a, "bdb", vaddr(255), 0x3F, COLS)  # bottom

    emit_strip(a, "st", LN_TITLE, data["title"])
    emit_strip(a, "sm", LN_MARCH, data["march"])
    emit_strip(a, "sh", LN_HARD, data["hard"])
    emit_strip(a, "sx", LN_MBOX, data["mbox"])
    emit_strip(a, "sd", LN_DIGIT, data["digits"])

    for g in range(16):                         # gradient: 16 x 2 lines
        emit_copy_loop(a, f"gr{g}a", vaddr(LN_GRAD + 2 * g),
                       data["grad"], COLS)
        emit_copy_loop(a, f"gr{g}b", vaddr(LN_GRAD + 2 * g + 1),
                       data["grad"] + COLS, COLS)

    for i, col in enumerate(range(4, 45, 4)):   # grid verticals first...
        emit_column(a, f"gv{i}", LN_GRID0, LN_GRID1, col, 0x01)
    for i, ln in enumerate(range(LN_GRID0, LN_GRID1 + 1, 12)):
        emit_fill(a, f"gh{i}", vaddr(ln), 0x3F, COLS)   # ...solid horizontals

    a.mvi(A, 0x00)                              # pass counter
    a.sta(SCRATCH_CNT)

    # ---- the march, forever ----------------------------------------------
    a.label("pass")
    a.beacon(24)
    for r in range(3):                          # hollow the bar
        emit_fill(a, f"bc{r}", vaddr(LN_BAR + r, BAR_COL0), 0x00,
                  BAR_SEGS * BAR_SEGW)

    emit_march(a, ram_top)
    a.beacon(2)

    # LED channel: march fault flag and bits.
    a.mov(A, B)
    a.ora(A)
    a.jz("nofault")
    a.beacon(3)
    for bit in range(8):
        a.mov(A, B)
        a.ani(1 << bit)
        a.jz(f"nb{bit}")
        a.beacon(4 + bit)
        a.label(f"nb{bit}")
    a.label("nofault")

    emit_boxes(a, "mx", LN_MBOX)

    # Verdict strip: FAIL if the march or the control saw anything.  The
    # blits clobber every register except B, so the combined verdict is
    # recomputed from B and the scratch byte wherever it is needed.
    a.lda(SCRATCH_HARD)
    a.ora(B)
    a.jz("vpass")
    emit_strip(a, "vf", LN_VERDICT, data["fail"])
    a.jmp("vdone")
    a.label("vpass")
    emit_strip(a, "vp", LN_VERDICT, data["pass"])
    a.label("vdone")

    # Tick line: steady tick for a clean pass, blinking for a dirty one.
    tick_line = vaddr(LN_TICK)
    assert (tick_line & 0xFF) + TICK_COL0 + TICK_MAX < 0x100, \
        "tick line must not cross a page"
    a.lda(SCRATCH_CNT)
    a.adi((tick_line & 0xFF) + TICK_COL0)
    a.mov(L, A)
    a.mvi(H, tick_line >> 8)
    a.lda(SCRATCH_HARD)
    a.ora(B)
    a.jz("tgood")
    a.mvi(M, 0xBF)                              # lit + blinking attribute
    a.jmp("tdone")
    a.label("tgood")
    a.mvi(M, 0x3F)
    a.label("tdone")
    a.lda(SCRATCH_CNT)
    a.inr(A)
    a.cpi(TICK_MAX)
    a.jnz("tkeep")
    emit_fill(a, "tclr", vaddr(LN_TICK, TICK_COL0), 0x00, TICK_MAX)
    a.mvi(A, 0x00)
    a.label("tkeep")
    a.sta(SCRATCH_CNT)

    a.jmp("pass")


def build(ram_top: int = RAM_TOP) -> bytes:
    rom = bytearray(b"\x00" * ROM_SIZE)

    head = Asm(BASE)
    head.jmp(BASE + ENTRY)
    rom[0:len(head.buf)] = head.link()

    # Data strips live between the program and the beacon page.
    DATA_OFF = 0x1200
    strips = {
        "title": render_strip([(6, "PMD 85-3 TEST CARD")]),
        "march": render_strip([(6, "MARCH C- RAM TEST")]),
        "hard": render_strip([(1, "HARD")]),
        "mbox": render_strip([(1, "MARCH")]),
        "digits": render_strip([(BOX_COL0 + 5 * i, str(i))
                                for i in range(8)]),
        "pass": render_strip([(21, "PASS")]),
        "fail": render_strip([(21, "FAIL")], attr=2),
        "grad": gradient_strip(),
    }
    data_addrs = {}
    off = DATA_OFF
    for name, blob in strips.items():
        assert off + len(blob) <= BEACON_OFF, "data overruns the beacon page"
        rom[off:off + len(blob)] = blob
        data_addrs[name] = BASE + off
        off += len(blob)

    a = Asm(BASE + ENTRY)
    emit(a, ram_top, data_addrs)
    body = a.link()
    assert ENTRY + len(body) <= DATA_OFF, \
        f"program overruns the data region: {len(body)} bytes"
    rom[ENTRY:ENTRY + len(body)] = body

    for i in range(32):
        rom[BEACON_OFF + i] = 0xE5
    return bytes(rom)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--ram-top", type=lambda s: int(s, 0), default=RAM_TOP)
    args = ap.parse_args()
    rom = build(args.ram_top)
    args.output.write_bytes(rom)
    print(f"wrote {args.output}: {len(rom)} bytes, test card + March C- "
          f"over 0000-{(args.ram_top << 8) - 1:04X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
