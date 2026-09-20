#!/usr/bin/env python3
"""Build the FLASH CHECK cartridge: sum every multiload page on the real
board, against sums baked in at build time.

The bench found corruption the emulator could not reproduce -- games
loading with wrong bytes from an omnibus image that verifies perfectly in
emulation.  The suspects (a partially written 2 MB flash, a serving-path
fault at high offsets, a page-switch protocol failure) all leave
different fingerprints in per-page integrity, and this cartridge shows
the whole fingerprint on one screen: it two-touches to every page in
turn, sums module 0000-3FD7h (the last 40 bytes are the live page-select
rows and are excluded from both the sum and the walk), and lights a grid
box per page -- solid for a match, a dot for a mismatch.  Its own page
cannot contain its own sum and is skipped (drawn solid immediately).

A full sweep is a couple of minutes; boxes light as it goes, then DONE
and the mismatch count.  The final touch returns the window to page 0,
so reset lands in the menu.
"""

import argparse
import sys
from pathlib import Path

from make_ramtest import (Asm, ENTRY, B, C, D, E, H, L, M, A,
                          RP_B, RP_D, RP_H, RP_SP)
from make_screentest import (FONT, glyph_byte, render_strip, vaddr, COLS,
                             emit_copy_loop, emit_fill, emit_column)

PAGE_SUM_TOP = 0x3FD8            # sum module 0000..this-1: control rows out

LN_TITLE = 3
LN_GRID0 = 20                    # box row r at LN_GRID0 + 10*r
LN_VERDICT = 120
BOX_COL0 = 4                     # box for page p at col BOX_COL0 + 2*(p%16)
GRID_COLS = 16

SP_TOP = 0x3F00                  # below the program; clean-room RAM

VAR_PAGE = 0x3E00                # current page
VAR_BAD = 0x3E01                 # mismatch count


def page_sums(pages: list) -> list:
    return [sum(pg[:PAGE_SUM_TOP]) & 0xFFFF for pg in pages]


def build_shelf(org: int, sums: list, own_page: int) -> bytes:
    """The cartridge, org'd for the shelf.  `sums` covers every page of
    the image this will ride; `own_page` is skipped."""
    n_pages = len(sums)
    assert n_pages <= 128, "the grid draws 8 rows of 16"
    sums = list(sums) + [0] * (128 - n_pages)   # fixed-size table: the
    # cartridge's size must not depend on the page count, so a two-pass
    # build (placeholder sums, then real ones) keeps the layout identical
    DATA_OFF = 0x0A00

    blobs = {
        "title": render_strip([(4, "FLASH CHECK")]),
        "done": render_strip([(4, "DONE. BAD PAGES:")]),
        "hextab": bytes(b for ch in "0123456789ABCDEF"
                        for b in (glyph_byte(FONT[ch][r]) for r in range(7))),
        "sums": b''.join(s.to_bytes(2, 'little') for s in sums),
    }
    img = bytearray(DATA_OFF)
    data = {}
    off = DATA_OFF
    for name, blob in blobs.items():
        img.extend(blob)
        data[name] = org + off
        off += len(blob)

    a = Asm(org + ENTRY)
    a.di()
    a.lxi(RP_SP, SP_TOP)
    a.mvi(A, 0x90)                    # module 8255: A in, B/C out
    a.out(0x8B)

    # title
    for r in range(7):
        emit_copy_loop(a, f"st{r}", vaddr(LN_TITLE + r),
                       data["title"] + r * COLS, COLS)

    a.mvi(A, 0)
    a.sta(VAR_PAGE)
    a.sta(VAR_BAD)

    # ---- per-page loop ---------------------------------------------------
    a.label("pageloop")
    a.lda(VAR_PAGE)
    a.cpi(n_pages)
    a.jz("alldone")
    a.cpi(own_page)
    a.jz("ownpage")

    # two-touch to the page: bank latch, grace, commit, rebuild delay
    a.lda(VAR_PAGE)                   # bank byte = D8h | page>>5
    for _ in range(3):
        a.db(0x07)                    # RLC
    a.ani(0x07)
    a.ori(0xD8)
    a.out(0x89)
    a.mvi(A, 0x3F)
    a.out(0x8A)                       # live: the touch
    a.mvi(A, 0xFF)
    a.out(0x8A)                       # park
    a.lxi(RP_B, 0x0800)               # ~24 ms: outlive the board's poll
    a.label("bdly")
    a.dcx(RP_B)
    a.mov(A, B)
    a.ora(C)
    a.jnz("bdly")
    a.lda(VAR_PAGE)                   # commit byte = E0h | page&1Fh
    a.ani(0x1F)
    a.ori(0xE0)
    a.out(0x89)
    a.mvi(A, 0x3F)
    a.out(0x8A)
    a.mvi(A, 0xFF)
    a.out(0x8A)
    a.lxi(RP_B, 0x7000)               # ~336 ms: the rebuild contract
    a.label("cdly")
    a.dcx(RP_B)
    a.mov(A, B)
    a.ora(C)
    a.jnz("cdly")

    # sum module 0000-3FD7h into DE
    a.lxi(RP_D, 0x0000)
    a.lxi(RP_H, 0x0000)
    a.label("sumb")
    a.mov(A, L)
    a.out(0x89)
    a.mov(A, H)
    a.out(0x8A)
    a.db(0xDB, 0x88)                  # IN 88h
    a.add(E)
    a.mov(E, A)
    a.mov(A, D)
    a.aci(0)
    a.mov(D, A)
    a.inx(RP_H)
    a.mov(A, L)
    a.cpi(PAGE_SUM_TOP & 0xFF)
    a.jnz("sumb")
    a.mov(A, H)
    a.cpi(PAGE_SUM_TOP >> 8)
    a.jnz("sumb")
    a.mvi(A, 0xFF)
    a.out(0x8A)                       # park the window

    # compare with sums[page]
    a.lda(VAR_PAGE)
    a.mov(L, A)
    a.mvi(H, 0)
    a.dad(RP_H)                       # HL = page*2
    a.db(0x01)                        # LXI B, sums
    a.a16(data["sums"])
    a.dad(RP_B)
    a.mov(A, M)
    a.db(0xBB)                        # CMP E
    a.jnz("bad")
    a.inx(RP_H)
    a.mov(A, M)
    a.db(0xBA)                        # CMP D
    a.jnz("bad")
    a.mvi(C, 0x3F)                    # match: solid box
    a.jmp("draw")
    a.label("bad")
    a.lda(VAR_BAD)
    a.db(0x3C)                        # INR A
    a.sta(VAR_BAD)
    a.mvi(C, 0x0C)                    # mismatch: a dot
    a.jmp("draw")
    a.label("ownpage")
    a.mvi(C, 0x3F)                    # our own page: trusted by execution

    # draw box for VAR_PAGE with fill C at (page/16, page%16)
    a.label("draw")
    a.lda(VAR_PAGE)
    a.ani(0x0F)
    a.add(A)                          # col offset = 2*(page%16)
    a.adi(BOX_COL0)
    a.mov(E, A)
    # line = LN_GRID0 + 10*row
    a.lda(VAR_PAGE)
    for _ in range(4):
        a.db(0x0F)
    a.ani(0x07)
    a.mov(L, A)
    a.add(A)                          # 2r
    a.add(A)                          # 4r
    a.add(L)                          # 5r
    a.add(A)                          # 10r
    a.adi(LN_GRID0)
    # screen address = C000h + line*64 + E  -> HL = line*64
    a.mov(L, A)
    a.mvi(H, 0)
    for _ in range(6):                # HL <<= 6
        a.dad(RP_H)
    a.mov(A, H)
    a.adi(0xC0)
    a.mov(H, A)
    a.mov(A, L)
    a.add(E)
    a.mov(L, A)
    a.mov(A, H)
    a.aci(0)
    a.mov(H, A)
    a.mvi(B, 6)                       # 6-line, 1-byte box
    a.label("boxrow")
    a.mov(M, C)
    a.mov(A, L)
    a.adi(64)
    a.mov(L, A)
    a.mov(A, H)
    a.aci(0)
    a.mov(H, A)
    a.db(0x05)                        # DCR B
    a.jnz("boxrow")

    a.lda(VAR_PAGE)
    a.db(0x3C)                        # INR A
    a.sta(VAR_PAGE)
    a.jmp("pageloop")

    # ---- done: back to page 0, verdict, park -----------------------------
    a.label("alldone")
    a.mvi(A, 0xD8)                    # bank 0
    a.out(0x89)
    a.mvi(A, 0x3F)
    a.out(0x8A)
    a.mvi(A, 0xFF)
    a.out(0x8A)
    a.lxi(RP_B, 0x0800)
    a.label("fdly1")
    a.dcx(RP_B)
    a.mov(A, B)
    a.ora(C)
    a.jnz("fdly1")
    a.mvi(A, 0xE0)                    # commit page 0: reset -> menu
    a.out(0x89)
    a.mvi(A, 0x3F)
    a.out(0x8A)
    a.mvi(A, 0xFF)
    a.out(0x8A)
    a.lxi(RP_B, 0x7000)
    a.label("fdly2")
    a.dcx(RP_B)
    a.mov(A, B)
    a.ora(C)
    a.jnz("fdly2")
    for r in range(7):
        emit_copy_loop(a, f"sd{r}", vaddr(LN_VERDICT + r),
                       data["done"] + r * COLS, COLS)
    # bad count as two hex nibbles
    for pos, high in ((40, True), (41, False)):
        a.lda(VAR_BAD)
        if high:
            for _ in range(4):
                a.db(0x0F)
        a.ani(0x0F)
        # glyph = hextab + 7*nibble, drawn at (LN_VERDICT, pos)
        a.mov(C, A)
        a.add(A)
        a.add(A)
        a.add(A)                      # x8
        a.db(0x91)                    # SUB C -> x7
        a.mov(L, A)
        a.mvi(H, 0)
        a.db(0x01)
        a.a16(data["hextab"])
        a.dad(RP_B)
        a.db(0xEB)                    # XCHG: DE = glyph
        a.lxi(RP_H, vaddr(LN_VERDICT, pos))
        a.mvi(B, 7)
        a.label(f"vg{pos}")
        a.db(0x1A)                    # LDAX D
        a.mov(M, A)
        a.inx(RP_D)
        a.mov(A, L)
        a.adi(64)
        a.mov(L, A)
        a.mov(A, H)
        a.aci(0)
        a.mov(H, A)
        a.db(0x05)                    # DCR B
        a.jnz(f"vg{pos}")
    a.label("spin")
    a.jmp("spin")

    body = a.link()
    assert ENTRY + len(body) <= DATA_OFF, \
        f"program overruns the data region: {len(body)} bytes"
    img[ENTRY:ENTRY + len(body)] = body

    head = Asm(org)
    head.jmp(org + ENTRY)
    img[0:len(head.buf)] = head.link()
    return bytes(img)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()
    demo = build_shelf(0x4000, [0] * 4, 0)
    args.output.write_bytes(demo)
    print(f"wrote {args.output}: {len(demo)} bytes (demo table)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
