#!/usr/bin/env python3
"""Build an 8 KB ROM that reads the BASIC-G ROM module and grades it live.

The ROM module (ROM PACK) is not memory-mapped: it hangs behind its own
8255 at ports 88-8B, and the CPU reads it one byte per transaction --
address low to port B (89h), address high to port C (8Ah), data back
from port A (88h).  A programmer tests the EPROMs; this tests the whole
path the machine actually uses -- connector, module 8255, decode, chips
-- and it does it continuously, so a flaky contact shows up as sums that
flicker while you wiggle the module.

The screen:

    16 rows ......... one per 1 KB of the first 16 KB of the module
                      window: block index and a 16-bit sum of its 1024
                      bytes, live every sweep (~2 per second)
    version rows .... B1, B2, B2A, B3 -- one box per 1 KB block of that
                      version; a box lights when the block's sum matches
                      the reference.  A full row = that BASIC, intact
    tick line ....... one tick per completed sweep

Reference sums are baked in from the RM-TEAM archive images
(`PMD85-rom-files`, 2017): BASIC-G 1.0 / 2.0 / 2.A are 9 KB, 3.0 is
10 KB.  `--print-refs` prints the table for comparing against programmer
dumps of individual chips (a 2 KB chip is two consecutive blocks).

Readings worth knowing in advance:

    FC00 everywhere   nothing answered: no module, or its 8255/decode is
                      dead (the bus floats high, 1024 x FF)
    ~0000, stable     chips answer but hold (mostly) zeros -- the same
                      degradation signature this machine's monitor 2616s
                      died with
    flickering sums   the path is intermittent: clean the connector

This is the first tool in the family that uses a stack.  Every earlier
image refused one because RAM was the suspect; this one runs on a
machine whose RAM the march has certified, so SP goes to proven memory
(7F00h) and the code gets subroutines.  The ladder of trust pays out.

    ./make_moduletest.py -o moduletest.bin
    ./gen_rom_images.py -o ../firmware/rom_images.c --monitor moduletest.bin
    # build the firmware with -DMHB_DIAG=3
"""

import argparse
import sys
from pathlib import Path

from make_ramtest import (Asm, BASE, ENTRY, BEACON_OFF, ROM_SIZE,
                          B, C, D, E, H, L, M, A, RP_B, RP_D, RP_H, RP_SP)
from make_screentest import (FONT, glyph_byte, render_strip, vaddr, COLS,
                             emit_copy_loop, emit_fill, emit_column)

# Per-1KB-block 16-bit byte sums of the known BASIC-G images, computed
# from the RM-TEAM archive (PMD85-rom-files, 2017).
REFS = {
    "B1": [0xB622, 0xBD6C, 0xD351, 0xB44E, 0xC831, 0xA414, 0x85AE, 0xD539,
           0xE29A],
    "B2": [0xAC52, 0xE0A5, 0xE05A, 0xC51F, 0xC454, 0x9CB2, 0x9C69, 0xEA12,
           0xDAEA],
    "B2A": [0xBA06, 0xEEE5, 0xF07A, 0xD2FF, 0xCBD4, 0xA072, 0x9CDA, 0xEB32,
            0xDCCA],
    "B3": [0xC2E1, 0xEF51, 0xEE09, 0xD0AF, 0xCA94, 0xA092, 0x94AB, 0xF05F,
           0xE1A1, 0xE13D],
}

N_BLOCKS = 16                  # 16 KB of the 32 KB window, one row per KB

LN_TITLE = 3
LN_BLOCK0 = 14                 # row i at LN_BLOCK0 + 8*i
LN_VERS0 = 150                 # version row v at LN_VERS0 + 9*v
LN_TICK = 190
TICK_COL0, TICK_MAX = 4, 40

IDX_COL = 2                    # block index nibble
SUM_COL = 5                    # four sum digits
BOX_COL0 = 7                   # version boxes at BOX_COL0 + 2*j

SP_TOP = 0x7F00                # certified by the march before this runs
VAR_SWEEP = 0x7E00

STACK_OPS_ALLOWED = True       # the one family member with the privilege


def block_line(i: int) -> int:
    return LN_BLOCK0 + 8 * i


def vers_line(v: int) -> int:
    return LN_VERS0 + 9 * v


def module_sums(data: bytes) -> list[int]:
    return [sum(data[i:i + 1024]) & 0xFFFF
            for i in range(0, len(data), 1024)]


def hex_table() -> bytes:
    out = bytearray()
    for ch in "0123456789ABCDEF":
        for r in range(7):
            out.append(glyph_byte(FONT[ch][r]))
    return bytes(out)


def emit_subroutines(a: Asm, hextab: int) -> None:
    # -- nib: draw the nibble in A at screen address HL.  Preserves DE. --
    a.label("nib")
    a.db(0xC5)                                  # PUSH B
    a.db(0xD5)                                  # PUSH D
    a.mov(C, A)
    a.add(A); a.add(A); a.add(A)                # x8...
    a.db(0x91)                                  # SUB C -> x7
    a.mov(C, A)
    a.lxi(RP_D, hextab)
    a.mov(A, E); a.add(C); a.mov(E, A)
    a.mov(A, D); a.aci(0); a.mov(D, A)
    a.mvi(B, 7)
    a.label("nibrow")
    a.db(0x1A)                                  # LDAX D
    a.mov(M, A)
    a.inx(RP_D)
    a.mov(A, L); a.adi(64); a.mov(L, A)
    a.mov(A, H); a.aci(0); a.mov(H, A)
    a.dcr(B)
    a.jnz("nibrow")
    a.db(0xD1)                                  # POP D
    a.db(0xC1)                                  # POP B
    a.db(0xC9)                                  # RET

    # -- box: fill the 1-byte-wide, 6-line box at HL with C. Preserves DE. --
    a.label("box")
    a.mvi(B, 6)
    a.label("boxrow")
    a.mov(M, C)
    a.mov(A, L); a.adi(64); a.mov(L, A)
    a.mov(A, H); a.aci(0); a.mov(H, A)
    a.dcr(B)
    a.jnz("boxrow")
    a.db(0xC9)                                  # RET

    # -- scan: sum the 1 KB module block starting at HL into DE. ----------
    # One port transaction per byte: address out on B and C, data in on A.
    a.label("scan")
    a.lxi(RP_D, 0x0000)
    a.label("scanb")
    a.mov(A, L); a.out(0x89)
    a.mov(A, H); a.out(0x8A)
    a.db(0xDB, 0x88)                            # IN 88h
    a.mov(C, A)
    a.mov(A, E); a.add(C); a.mov(E, A)
    a.mov(A, D); a.aci(0); a.mov(D, A)
    a.inx(RP_H)
    a.mov(A, L); a.ora(A); a.jnz("scanb")
    a.mov(A, H); a.ani(0x03); a.jnz("scanb")    # 1 KB boundary
    a.db(0xC9)                                  # RET


def emit(a: Asm, refs: dict, data: dict) -> None:
    a.di()
    a.beacon(0)

    # ---- the proven paging trampoline ------------------------------------
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
    a.out(0xF7)
    a.label("tramp")
    a.mov(A, B)
    a.out(0xF7)

    a.lxi(RP_H, 0x0000)
    a.mov(A, M)
    a.cpi(0xC3)
    a.label("stuck")
    a.jz("stuck")                               # mirror never cleared: park
    a.beacon(1)

    a.lxi(RP_SP, SP_TOP)                        # RAM is certified: a stack

    # Module 8255: port A input, ports B and C output, mode 0.
    a.mvi(A, 0x90)
    a.out(0x8B)

    a.jmp("main")
    emit_subroutines(a, data["hextab"])
    a.label("main")

    # ---- static card ------------------------------------------------------
    a.lxi(RP_H, 0xC000)
    a.label("clr")
    a.mvi(M, 0x00)
    a.inx(RP_H)
    a.mov(A, H)
    a.ora(A)
    a.jnz("clr")

    emit_column(a, "bdl", 0, 255, 0, 0x01)
    emit_column(a, "bdr", 0, 255, COLS - 1, 0x20)
    emit_fill(a, "bdt", vaddr(0), 0x3F, COLS)
    emit_fill(a, "bdb", vaddr(255), 0x3F, COLS)

    for r in range(7):
        emit_copy_loop(a, f"st{r}", vaddr(LN_TITLE + r),
                       data["title"] + r * COLS, COLS)
    for v, name in enumerate(refs):
        for r in range(7):
            emit_copy_loop(a, f"sv{v}_{r}", vaddr(vers_line(v) + r),
                           data[f"lbl{v}"] + r * COLS, COLS)

    for i in range(N_BLOCKS):                   # block indices, 0..F
        a.mvi(A, i)
        a.lxi(RP_H, vaddr(block_line(i), IDX_COL))
        a.db(0xCD); a.a16("nib")                # CALL nib

    a.mvi(A, 0x00)
    a.sta(VAR_SWEEP)

    # ---- the sweep, forever ----------------------------------------------
    a.label("sweep")
    a.lxi(RP_H, 0x0000)                         # module address
    for i in range(N_BLOCKS):
        a.db(0xCD); a.a16("scan")               # DE = sum, HL = next block
        a.db(0xE5)                              # PUSH H

        for pos, (reg, high) in enumerate(((D, True), (D, False),
                                           (E, True), (E, False))):
            a.mov(A, reg)
            if high:
                for _ in range(4):
                    a.db(0x0F)                  # RRC
            a.ani(0x0F)
            a.lxi(RP_H, vaddr(block_line(i), SUM_COL + pos))
            a.db(0xCD); a.a16("nib")

        for v, (name, sums) in enumerate(refs.items()):
            if i >= len(sums):
                continue
            ref = sums[i]
            a.mvi(C, 0x00)
            a.mov(A, E); a.cpi(ref & 0xFF); a.jnz(f"n{v}_{i}")
            a.mov(A, D); a.cpi(ref >> 8); a.jnz(f"n{v}_{i}")
            a.mvi(C, 0x3F)
            a.label(f"n{v}_{i}")
            a.lxi(RP_H, vaddr(vers_line(v), BOX_COL0 + 2 * i))
            a.db(0xCD); a.a16("box")

        a.db(0xE1)                              # POP H
    a.beacon(2)

    # Tick per sweep.
    tick = vaddr(LN_TICK)
    assert (tick & 0xFF) + TICK_COL0 + TICK_MAX < 0x100
    a.lda(VAR_SWEEP)
    a.adi((tick & 0xFF) + TICK_COL0)
    a.mov(L, A)
    a.mvi(H, tick >> 8)
    a.mvi(M, 0x3F)
    a.lda(VAR_SWEEP)
    a.inr(A)
    a.cpi(TICK_MAX)
    a.jnz("tkeep")
    emit_fill(a, "tclr", vaddr(LN_TICK, TICK_COL0), 0x00, TICK_MAX)
    a.mvi(A, 0x00)
    a.label("tkeep")
    a.sta(VAR_SWEEP)
    a.jmp("sweep")


def build(refs: dict | None = None) -> bytes:
    refs = REFS if refs is None else refs
    assert len(refs) <= 4, "four version rows fit on the card"
    rom = bytearray(b"\x00" * ROM_SIZE)

    head = Asm(BASE)
    head.jmp(BASE + ENTRY)
    rom[0:len(head.buf)] = head.link()

    DATA_OFF = 0x1600
    blobs = {"title": render_strip([(5, "BASIC-G MODULE TEST")]),
             "hextab": hex_table()}
    for v, name in enumerate(refs):
        blobs[f"lbl{v}"] = render_strip([(IDX_COL, name)])

    data = {}
    off = DATA_OFF
    for name, blob in blobs.items():
        assert off + len(blob) <= BEACON_OFF, "data overruns the beacon page"
        rom[off:off + len(blob)] = blob
        data[name] = BASE + off
        off += len(blob)

    a = Asm(BASE + ENTRY)
    emit(a, refs, data)
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
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--print-refs", action="store_true",
                    help="print the reference block sums and exit")
    args = ap.parse_args()
    if args.print_refs:
        for name, sums in REFS.items():
            print(f"{name}: " + " ".join(f"{s:04X}" for s in sums))
        return 0
    if not args.output:
        print("either -o or --print-refs is required", file=sys.stderr)
        return 2
    rom = build()
    args.output.write_bytes(rom)
    print(f"wrote {args.output}: {len(rom)} bytes, module scanner, "
          f"{len(REFS)} reference versions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
