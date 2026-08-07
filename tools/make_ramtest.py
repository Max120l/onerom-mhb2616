#!/usr/bin/env python3
"""Build an 8 KB ROM image that tests a PMD 85-3's RAM without using any.

The problem this solves is general: a machine too broken to run its own
monitor cannot run a memory test either, because every ordinary test needs a
stack, and a stack lives in the memory under suspicion. This image needs
nothing but a working processor and the board serving it -- **no stack, no
subroutine calls, no interrupts, not one byte of RAM on any path that
decides anything.** Every branch is a jump; every variable is a register.

It reports through the ROM socket, the one channel guaranteed to work:
reading a reserved ROM address is a *beacon*, the board sees the read and
lights a bit. The machine talks to you through the chip it is reading, with
no video, keyboard or serial port involved.

    ./make_ramtest.py -o ramtest.bin
    ./gen_rom_images.py -o ../firmware/rom_images.c --monitor ramtest.bin
    # build the firmware with -DMHB_DIAG=3

Beacons, in the order the frame blinks them:

     0  running        the processor executed from this image
     1  map cleared    startup mirror gone, RAM is readable
     2  pass complete  a whole March C- sweep finished
     3  RAM FAULT      at least one byte read back wrong
   4-11 D0..D7         which data bits were ever wrong
    12  fault < 4000h
    13  fault 4000-7FFFh
    14  fault 8000-BFFFh
    15  HARD FAULT     cells fail even when read back immediately
 16-23  D0..D7         which bits failed the immediate read-back

A clean machine blinks 0, 1 and 2 and nothing else, over and over.

Beacon 15 is the control that decides what a fault means. If the march
reports faults (3) and the immediate read-back does not (15 dark), the
cells accept and return data but do not hold it: that is a refresh
problem, not a memory problem, and replacing the chips would fix
nothing.
"""

import argparse
import sys
from pathlib import Path

ROM_SIZE = 0x2000
BASE = 0xE000                 # where the machine sees this image
ENTRY = 0x0100                # offset of the real program
BEACON_OFF = 0x1F00           # beacons at offsets 1F00..1F1E
BEACON_ADDR = BASE + BEACON_OFF
RAM_TOP = 0xC0                # RAM is 0000-BFFF; stop when H reaches this
VRAM_TOP = 0xE0               # video RAM #1 is C000-DFFF

B, C, D, E, H, L, M, A = range(8)
RP_B, RP_D, RP_H, RP_SP = range(4)


class Asm:
    """Just enough 8080 assembler: labels, forward references, no expressions."""

    def __init__(self, org: int):
        self.org = org
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []

    @property
    def pc(self) -> int:
        return self.org + len(self.buf)

    def label(self, name: str) -> None:
        assert name not in self.labels, f"duplicate label {name}"
        self.labels[name] = self.pc

    def db(self, *vals: int) -> None:
        self.buf.extend(v & 0xFF for v in vals)

    def a16(self, target) -> None:
        if isinstance(target, str):
            self.fixups.append((len(self.buf), target))
            self.buf.extend(b"\0\0")
        else:
            self.buf.extend((target & 0xFF, (target >> 8) & 0xFF))

    def jmp(self, t): self.db(0xC3); self.a16(t)
    def jnz(self, t): self.db(0xC2); self.a16(t)
    def jz(self, t):  self.db(0xCA); self.a16(t)
    def jc(self, t):  self.db(0xDA); self.a16(t)
    def mvi(self, r, v): self.db(0x06 | (r << 3), v)
    def lxi(self, rp, v): self.db(0x01 | (rp << 4)); self.a16(v)
    def mov(self, d, s): self.db(0x40 | (d << 3) | s)
    def inx(self, rp): self.db(0x03 | (rp << 4))
    def dcx(self, rp): self.db(0x0B | (rp << 4))
    def cma(self): self.db(0x2F)
    def ora(self, s): self.db(0xB0 | s)
    def xra(self, s): self.db(0xA8 | s)
    def ani(self, v): self.db(0xE6, v)
    def xri(self, v): self.db(0xEE, v)
    def ori(self, v): self.db(0xF6, v)
    def cpi(self, v): self.db(0xFE, v)
    def lda(self, a): self.db(0x3A); self.a16(a)
    def out(self, p): self.db(0xD3, p)

    def record_fault(self, tag: str, cont: str) -> None:
        """A holds the failing bits; remember them and which third of RAM.

        Inlined at every read element rather than called, because a CALL
        would need the stack -- which is the whole thing under test.  Six
        copies of twenty bytes is the price of not trusting RAM, and it is
        a bargain.
        """
        self.ora(B)
        self.mov(B, A)
        self.mov(A, H)
        self.cpi(0x40)
        self.jc(f"{tag}_low")
        self.cpi(0x80)
        self.jc(f"{tag}_mid")
        self.mov(A, C); self.ori(0x04); self.mov(C, A); self.jmp(cont)
        self.label(f"{tag}_low")
        self.mov(A, C); self.ori(0x01); self.mov(C, A); self.jmp(cont)
        self.label(f"{tag}_mid")
        self.mov(A, C); self.ori(0x02); self.mov(C, A); self.jmp(cont)

    def beacon(self, n: int) -> None:
        """Announce event n by reading a reserved ROM address."""
        self.lda(BEACON_ADDR + n)

    def link(self) -> bytes:
        for off, name in self.fixups:
            assert name in self.labels, f"undefined label {name}"
            t = self.labels[name]
            self.buf[off] = t & 0xFF
            self.buf[off + 1] = (t >> 8) & 0xFF
        return bytes(self.buf)


def build(ram_top: int = RAM_TOP) -> bytes:
    """March C- over 0000..(ram_top<<8)-1, plus an address-uniqueness pass."""
    rom = bytearray(b"\x00" * ROM_SIZE)

    # At reset the processor starts at 0000, and the startup map mirrors this
    # image everywhere -- so offset 0 is the first thing it ever fetches.
    # Get up into E000-FFFF *before* clearing that map, or the ground vanishes
    # from under the program at the instant it clears.
    head = Asm(BASE)
    head.jmp(BASE + ENTRY)
    rom[0:len(head.buf)] = head.link()

    a = Asm(BASE + ENTRY)
    top = (ram_top << 8) - 1          # last RAM address

    # Beacon 0 before anything else: proves the processor fetched, decoded
    # and executed instructions out of this image.
    a.beacon(0)

    # Clear the startup mirror map.  Until this happens, reads below E000
    # come from ROM, so no memory test is possible at all.  This is the
    # monitor's own first I/O write, chosen because it is known safe here.
    a.mvi(A, 0x82)
    a.out(0xF7)
    # ...and the second write the monitor makes, so this program initialises
    # the machine exactly as its own firmware does.  Skipping it would leave
    # one difference between "the monitor ran" and "the test ran", which is
    # precisely the difference the test is trying to measure.
    a.mvi(A, 0x09)
    a.out(0xF7)
    a.beacon(1)

    # Paint video RAM #1 (C000-DFFF) so a human sees the program is alive
    # without reading the LED, and so writes are known to reach something.
    a.lxi(RP_H, 0xC000)
    a.label("paint")
    a.mvi(M, 0xAA)
    a.inx(RP_H)
    a.mov(A, H)
    a.cpi(VRAM_TOP)
    a.jnz("paint")

    # ---- March C- ---------------------------------------------------------
    # B: every data bit that has ever read back wrong.
    # C: which thirds of RAM the faults were in (bit 0 low, 1 mid, 2 high).
    #
    # March C- is six elements over the whole array:
    #
    #     m0  any order   w0
    #     m1  ascending   r0 w1
    #     m2  ascending   r1 w0
    #     m3  descending  r0 w1
    #     m4  descending  r1 w0
    #     m5  any order   r0
    #
    # Ten operations per cell, and it is worth every one: a single
    # write-everything-then-read-everything pass finds stuck-at faults and
    # nothing else.  Marching finds *transition* faults (a cell that will
    # not go 0->1, or 1->0) because every cell is read in both states after
    # being changed to them, and *coupling* faults (writing one cell
    # disturbing another) because the array is walked in both directions --
    # a coupling that hides going up is exposed coming down.  Address
    # decoder faults fall out too: an aliased cell is found already written
    # when the march reaches it.
    a.label("pass")
    a.mvi(B, 0x00)
    a.mvi(C, 0x00)
    a.mvi(D, 0x00)

    # ---- immediate write-and-read-back -----------------------------------
    # Write a cell and read it straight back, microseconds later, before
    # anything else touches the array.  This is the control for March C-:
    #
    #   both fail      the cells or their data path are genuinely broken
    #   March fails,
    #   this passes    the cells accept and return data but do not HOLD it
    #                  -- dynamic RAM that is not being refreshed, which is
    #                  a fault in the refresh circuit or in this program's
    #                  right to expect refresh, NOT in the memory chips
    #
    # Without this control, a machine whose refresh has stopped is
    # confidently reported as having every chip bad, and someone spends a
    # weekend replacing perfectly good DRAM.
    a.lxi(RP_H, 0x0000)
    a.label("imm")
    a.mvi(M, 0xAA)
    a.mov(A, M)
    a.xri(0xAA)
    a.jz("imm_b")
    a.ora(D)
    a.mov(D, A)
    a.label("imm_b")
    a.mvi(M, 0x55)
    a.mov(A, M)
    a.xri(0x55)
    a.jz("imm_n")
    a.ora(D)
    a.mov(D, A)
    a.label("imm_n")
    a.inx(RP_H)
    a.mov(A, H)
    a.cpi(ram_top)
    a.jnz("imm")

    # m0: write 0 everywhere.
    a.lxi(RP_H, 0x0000)
    a.label("m0")
    a.mvi(M, 0x00)
    a.inx(RP_H)
    a.mov(A, H)
    a.cpi(ram_top)
    a.jnz("m0")

    # m1: ascending, read 0 then write 1.
    a.lxi(RP_H, 0x0000)
    a.label("m1")
    a.mov(A, M)
    a.ora(A)                     # expecting 0: any set bit is a fault
    a.jz("m1ok")
    a.record_fault("m1", "m1ok")
    a.label("m1ok")
    a.mvi(M, 0xFF)
    a.inx(RP_H)
    a.mov(A, H)
    a.cpi(ram_top)
    a.jnz("m1")

    # m2: ascending, read 1 then write 0.
    a.lxi(RP_H, 0x0000)
    a.label("m2")
    a.mov(A, M)
    a.cma()                      # expecting FF: complement, any set bit is a fault
    a.ora(A)                     # CMA leaves the flags alone; this sets them
    a.jz("m2ok")
    a.record_fault("m2", "m2ok")
    a.label("m2ok")
    a.mvi(M, 0x00)
    a.inx(RP_H)
    a.mov(A, H)
    a.cpi(ram_top)
    a.jnz("m2")

    # m3: descending, read 0 then write 1.  The loop ends when DCX wraps
    # 0000 to FFFF, which no RAM address can reach.
    a.lxi(RP_H, top)
    a.label("m3")
    a.mov(A, M)
    a.ora(A)
    a.jz("m3ok")
    a.record_fault("m3", "m3ok")
    a.label("m3ok")
    a.mvi(M, 0xFF)
    a.dcx(RP_H)
    a.mov(A, H)
    a.cpi(0xFF)
    a.jnz("m3")

    # m4: descending, read 1 then write 0.
    a.lxi(RP_H, top)
    a.label("m4")
    a.mov(A, M)
    a.cma()
    a.ora(A)
    a.jz("m4ok")
    a.record_fault("m4", "m4ok")
    a.label("m4ok")
    a.mvi(M, 0x00)
    a.dcx(RP_H)
    a.mov(A, H)
    a.cpi(0xFF)
    a.jnz("m4")

    # m5: read 0 everywhere.
    a.lxi(RP_H, 0x0000)
    a.label("m5")
    a.mov(A, M)
    a.ora(A)
    a.jz("m5ok")
    a.record_fault("m5", "m5ok")
    a.label("m5ok")
    a.inx(RP_H)
    a.mov(A, H)
    a.cpi(ram_top)
    a.jnz("m5")

    # ---- address-uniqueness pass -----------------------------------------
    # March C- works with uniform 0s and 1s, which makes it strong on cells
    # and weaker on wiring: two addresses shorted together still hold the
    # same value it expects.  One pass of an address-dependent pattern
    # closes that -- every byte differs from its neighbours and from the
    # same offset in any other page, so an aliased or stuck address line
    # reads back as a data mismatch.
    a.lxi(RP_H, 0x0000)
    a.label("afill")
    a.mov(A, H)
    a.xra(L)
    a.mov(M, A)
    a.inx(RP_H)
    a.mov(A, H)
    a.cpi(ram_top)
    a.jnz("afill")

    a.lxi(RP_H, 0x0000)
    a.label("averify")
    a.mov(A, H)
    a.xra(L)
    a.xra(M)                     # expected xor actual
    a.jz("aok")
    a.record_fault("av", "aok")
    a.label("aok")
    a.inx(RP_H)
    a.mov(A, H)
    a.cpi(ram_top)
    a.jnz("averify")

    # ---- report ----------------------------------------------------------
    a.beacon(2)                       # a whole pass finished
    a.mov(A, B)
    a.ora(A)
    a.jz("report_imm")                # no march faults; still report the control
    a.beacon(3)                       # RAM FAULT (over a full march)

    for bit in range(8):
        a.mov(A, B)
        a.ani(1 << bit)
        a.jz(f"skipbit{bit}")
        a.beacon(4 + bit)
        a.label(f"skipbit{bit}")

    for third in range(3):
        a.mov(A, C)
        a.ani(1 << third)
        a.jz(f"skipthird{third}")
        a.beacon(12 + third)
        a.label(f"skipthird{third}")

    a.label("report_imm")
    a.mov(A, D)
    a.ora(A)
    a.jz("done")
    a.beacon(15)                      # cells fail even read back immediately
    for bit in range(8):
        a.mov(A, D)
        a.ani(1 << bit)
        a.jz(f"skipimm{bit}")
        a.beacon(16 + bit)
        a.label(f"skipimm{bit}")
    a.label("done")
    a.jmp("pass")

    body = a.link()
    assert ENTRY + len(body) < BEACON_OFF, "program overruns the beacon page"
    rom[ENTRY:ENTRY + len(body)] = body

    # The beacon bytes are ordinary ROM; only the fact of reading them
    # matters, never their value.
    for i in range(32):
        rom[BEACON_OFF + i] = 0xE5
    return bytes(rom)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--ram-top", type=lambda s: int(s, 0), default=RAM_TOP,
                    help="high byte just past the RAM to test (default C0, "
                         "i.e. 0000-BFFF)")
    args = ap.parse_args()
    rom = build(args.ram_top)
    args.output.write_bytes(rom)
    print(f"wrote {args.output}: {len(rom)} bytes, "
          f"beacons at {BEACON_ADDR:04X}-{BEACON_ADDR + 30:04X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
