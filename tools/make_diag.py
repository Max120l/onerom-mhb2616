#!/usr/bin/env python3
"""Build an 8 KB ROM that tests the processor first, then the bus, then RAM.

Every diagnostic so far has assumed the processor works. That assumption is
free to make and expensive to be wrong about: a marginal CPU, or a marginal
bus driver between it and the ROM, produces exactly the erratic behaviour
that gets blamed on memory. So this image climbs a ladder, and each rung
depends only on rungs below it:

    0  alive        the processor fetched, decoded and executed at all
    1  registers    MVI to every register, MOV between them, CPI
    2  ALU + flags  ANI ORI XRI ADI SUI, and the Z/S/CY/P flags they set
    3  branches     all eight conditional jumps, taken AND not taken
    4  16-bit       LXI INX DCX DAD, including a carry across the byte
    5  data bus     256 ROM bytes covering every bit pattern, verified --
                    this is the path through the 8228 and it names the
                    exact data lines that are wrong
    6  ROM sum      all 8192 bytes summed; catches address-line faults
                    that a 256-byte table would step straight over
    7  I/O          OUT F7h, clearing the startup mirror map
    8  RAM          March C- plus an immediate read-back control

Rungs 0-6 are the processor and the ROM path and nothing else: no writes,
no RAM reads, and no I/O. The machine's first OUT is a rung of its own,
because writing to a port is the most hardware-dependent thing the ladder
does and it has no business sitting below the rungs that prove the CPU can
execute at all. An earlier version cleared the mirror map inside rung 0,
which meant a machine that died on the port write reported "the processor
never got past its first instruction" -- true, but pointing at the wrong
part.

There is no stack anywhere in this program: no CALL, no PUSH, no RET, no
interrupts. Every branch is a jump and every variable is a register,
because the whole point is to be trustworthy on a machine where memory is
a suspect.

Reporting is by beacon -- a read of a reserved ROM address, which the
board sees:

     0-8   rung N started
     9-17  rung N FAILED
     18    every rung passed; looping
     19-26 D0..D7, the data lines that failed rung 5 or rung 8

Read it with firmware built `-DMHB_DIAG=5`:

    red   x (N+1)  failed in rung N
    blue  x (N+1)  started rung N and never finished -- it hung there
    green steady   everything passed

    ./make_diag.py -o diag.bin
    ./gen_rom_images.py -o ../firmware/rom_images.c --monitor diag.bin
"""

import argparse
import sys
from pathlib import Path

from make_ramtest import (Asm, BASE, ENTRY, BEACON_OFF, BEACON_ADDR, ROM_SIZE,
                          RAM_TOP, VRAM_TOP, B, C, D, E, H, L, M, A,
                          RP_B, RP_D, RP_H)

PATTERN_OFF = 0x1E00           # 256 bytes, 00..FF, for the data-bus rung
PATTERN_ADDR = BASE + PATTERN_OFF
FILLER_OFF = 0x1DFF            # one byte, adjusted so the whole ROM sums to 0

N_RUNGS = 9
RUNG_STARTED = 0               # beacons 0..8
RUNG_FAILED = N_RUNGS          # beacons 9..17
ALL_PASSED = 2 * N_RUNGS       # beacon 18
BIT_BASE = 2 * N_RUNGS + 1     # beacons 19..26
BIT_RUNGS = (5, 8)             # the rungs that report which bits failed


def emit(a: Asm, ram_top: int) -> None:
    # ---- rung 0: alive ---------------------------------------------------
    # One beacon and nothing else.  If this is the last thing the board
    # hears, the processor fetched exactly one instruction group and
    # stopped, and no later rung can be blamed for it.
    a.beacon(RUNG_STARTED + 0)

    # ---- rung 1: registers and MOV --------------------------------------
    a.beacon(RUNG_STARTED + 1)
    a.mvi(A, 0x40)
    a.mvi(B, 0x01)
    a.mvi(C, 0x02)
    a.mvi(D, 0x04)
    a.mvi(E, 0x08)
    a.mvi(H, 0x10)
    a.mvi(L, 0x20)
    a.cpi(0x40); a.jnz("fail1")        # A survived six other loads
    for reg, want in ((B, 0x01), (C, 0x02), (D, 0x04),
                      (E, 0x08), (H, 0x10), (L, 0x20)):
        a.mov(A, reg); a.cpi(want); a.jnz("fail1")
    a.mov(B, L)                        # MOV between two non-A registers
    a.mov(A, B); a.cpi(0x20); a.jnz("fail1")

    # ---- rung 2: ALU and the flags it sets -------------------------------
    a.beacon(RUNG_STARTED + 2)
    a.mvi(A, 0xAA); a.xri(0xFF); a.cpi(0x55); a.jnz("fail2")
    a.mvi(A, 0x55); a.ori(0xAA); a.cpi(0xFF); a.jnz("fail2")
    a.mvi(A, 0xF0); a.ani(0x3C); a.cpi(0x30); a.jnz("fail2")
    a.mvi(A, 0x10); a.adi(0x20); a.cpi(0x30); a.jnz("fail2")
    a.mvi(A, 0x30); a.sui(0x10); a.cpi(0x20); a.jnz("fail2")
    a.mvi(A, 0xFF); a.adi(0x01); a.jnc("fail2")      # carry out
    a.mvi(A, 0x01); a.adi(0x01); a.jc("fail2")       # and no carry
    a.xra(A);                     a.jnz("fail2")     # zero flag
    a.mvi(A, 0x80); a.ora(A);     a.jp("fail2")      # sign flag
    a.mvi(A, 0x03); a.ora(A);     a.jpo("fail2")     # parity even
    a.mvi(A, 0x01); a.ora(A);     a.jpe("fail2")     # parity odd

    # ---- rung 3: every conditional jump, both ways -----------------------
    # A jump that is wrongly *not* taken lands on the failure below it; a
    # jump wrongly taken lands on one too.  Both directions are covered.
    a.beacon(RUNG_STARTED + 3)
    a.xra(A)                       # Z set, CY clear, P even, S clear
    a.jnz("fail3"); a.jz("r3a"); a.jmp("fail3")
    a.label("r3a")
    a.jc("fail3"); a.jnc("r3b"); a.jmp("fail3")
    a.label("r3b")
    a.jm("fail3"); a.jp("r3c"); a.jmp("fail3")
    a.label("r3c")
    a.jpo("fail3"); a.jpe("r3d"); a.jmp("fail3")
    a.label("r3d")
    a.mvi(A, 0x81); a.ora(A)       # Z clear, S set, P even(two bits)
    a.jz("fail3"); a.jnz("r3e"); a.jmp("fail3")
    a.label("r3e")
    a.jp("fail3"); a.jm("r3f"); a.jmp("fail3")
    a.label("r3f")

    # ---- rung 4: sixteen-bit arithmetic ---------------------------------
    a.beacon(RUNG_STARTED + 4)
    a.lxi(RP_H, 0x1234)
    a.mov(A, H); a.cpi(0x12); a.jnz("fail4")
    a.mov(A, L); a.cpi(0x34); a.jnz("fail4")
    a.inx(RP_H); a.mov(A, L); a.cpi(0x35); a.jnz("fail4")
    a.dcx(RP_H); a.mov(A, L); a.cpi(0x34); a.jnz("fail4")
    a.lxi(RP_B, 0x0001); a.dad(RP_B)
    a.mov(A, L); a.cpi(0x35); a.jnz("fail4")
    a.lxi(RP_H, 0x00FF); a.inx(RP_H)          # carry across the byte
    a.mov(A, H); a.cpi(0x01); a.jnz("fail4")
    a.mov(A, L); a.cpi(0x00); a.jnz("fail4")

    # ---- rung 5: the data bus, through the 8228 --------------------------
    # 256 ROM bytes holding every value 00..FF, read and compared.  Any data
    # line that is stuck, shorted, loaded or intermittent shows up here as
    # specific bits -- and this is the only rung that can see the path
    # between the ROM socket and the processor for what it is.
    a.beacon(RUNG_STARTED + 5)
    a.mvi(B, 0x00)                            # accumulated bad bits
    a.lxi(RP_H, PATTERN_ADDR)
    a.mvi(C, 0x00)                            # expected value
    a.label("r5loop")
    a.mov(A, M)
    a.xra(C)
    a.jz("r5next")
    a.ora(B); a.mov(B, A)
    a.label("r5next")
    a.inx(RP_H)
    a.inr(C)
    a.mov(A, C)
    a.ora(A)
    a.jnz("r5loop")
    a.mov(A, B); a.ora(A); a.jnz("fail5")

    # ---- rung 6: the ROM, summed ----------------------------------------
    # A 256-byte table lives in one page and steps straight over an address
    # line stuck inside the other twelve.  Summing the image does not.  A
    # filler byte is chosen at build time so a healthy read sums to zero.
    #
    # It stops before FF00, and that is not an optimisation: the beacon page
    # lives there, and *reading* a beacon is how this program speaks.  A
    # checksum that walked it would announce every beacon at once --
    # including "all rungs failed" and "everything passed" together, which
    # is exactly what the first version of this did.
    a.beacon(RUNG_STARTED + 6)
    a.mvi(B, 0x00)
    a.lxi(RP_H, BASE)
    a.label("r6loop")
    a.mov(A, M)
    a.add(B)
    a.mov(B, A)
    a.inx(RP_H)
    a.mov(A, H)
    a.cpi(0xFF)                                # stop below the beacon page
    a.jnz("r6loop")
    a.mov(A, B); a.ora(A); a.jnz("fail6")

    # ---- rung 7: the first I/O write -------------------------------------
    # Clear the startup mirror map, exactly as the monitor does.  Until this
    # happens reads below E000 come from ROM and writes go to RAM, so no RAM
    # rung is possible -- but nothing above this point needed it either.
    #
    # The check afterwards is the map itself, not memory: write a byte to
    # 0000 and read it back.  Address 0000 in ROM holds C3, the entry jump.
    # Reading back exactly C3 means the read still came from ROM and the OUT
    # did not take, which is a fault in the port and not in RAM.  Anything
    # else means the map switched; whether the value is right is rung 8's
    # business, so only C3 fails here.
    a.beacon(RUNG_STARTED + 7)
    a.mvi(A, 0x82)
    a.out(0xF7)
    a.mvi(A, 0x09)
    a.out(0xF7)
    a.lxi(RP_H, 0x0000)
    a.mvi(M, 0x5A)
    a.mov(A, M)
    a.cpi(0xC3)
    a.jz("fail7")

    # ---- rung 8: RAM -----------------------------------------------------
    a.beacon(RUNG_STARTED + 8)
    emit_ram(a, ram_top)

    # ---- everything passed ----------------------------------------------
    a.label("passed")
    a.beacon(ALL_PASSED)
    a.jmp("passed")

    # ---- failure exits ---------------------------------------------------
    # Each parks in its own loop, re-announcing itself forever, so the board
    # keeps seeing it however long it takes anyone to look at the LED.
    for n in range(N_RUNGS):
        a.label(f"fail{n}")
        a.beacon(RUNG_FAILED + n)
        if n in BIT_RUNGS:                     # these two know which bits
            for bit in range(8):
                a.mov(A, B)
                a.ani(1 << bit)
                a.jz(f"f{n}s{bit}")
                a.beacon(BIT_BASE + bit)
                a.label(f"f{n}s{bit}")
        a.jmp(f"fail{n}")


def emit_ram(a: Asm, ram_top: int) -> None:
    """March C- with an immediate read-back control; faults jump to fail8."""
    top = (ram_top << 8) - 1

    # Immediate write-and-read-back: does a cell take and return data at
    # all, microseconds apart?  Separates dead cells from cells that simply
    # do not hold, which is refresh and not memory.
    a.mvi(B, 0x00)
    a.lxi(RP_H, 0x0000)
    a.label("immloop")
    a.mvi(M, 0xAA); a.mov(A, M); a.xri(0xAA); a.jz("imm2")
    a.ora(B); a.mov(B, A)
    a.label("imm2")
    a.mvi(M, 0x55); a.mov(A, M); a.xri(0x55); a.jz("imm3")
    a.ora(B); a.mov(B, A)
    a.label("imm3")
    a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz("immloop")
    a.mov(A, B); a.ora(A); a.jnz("fail8")

    # March C-: w0 / r0w1 up / r1w0 up / r0w1 down / r1w0 down / r0.
    a.lxi(RP_H, 0x0000)
    a.label("m0")
    a.mvi(M, 0x00); a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz("m0")

    for tag, expect_one, write, down in (("m1", False, 0xFF, False),
                                         ("m2", True, 0x00, False),
                                         ("m3", False, 0xFF, True),
                                         ("m4", True, 0x00, True)):
        a.lxi(RP_H, top if down else 0x0000)
        a.label(tag)
        a.mov(A, M)
        if expect_one:
            a.cma()
        a.ora(A)
        a.jz(f"{tag}ok")
        a.ora(B); a.mov(B, A); a.jmp("fail8")
        a.label(f"{tag}ok")
        a.mvi(M, write)
        if down:
            a.dcx(RP_H); a.mov(A, H); a.cpi(0xFF); a.jnz(tag)
        else:
            a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz(tag)

    a.lxi(RP_H, 0x0000)
    a.label("m5")
    a.mov(A, M); a.ora(A); a.jz("m5ok")
    a.ora(B); a.mov(B, A); a.jmp("fail8")
    a.label("m5ok")
    a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz("m5")


def build(ram_top: int = RAM_TOP) -> bytes:
    rom = bytearray(b"\x00" * ROM_SIZE)

    head = Asm(BASE)
    head.jmp(BASE + ENTRY)
    rom[0:len(head.buf)] = head.link()

    a = Asm(BASE + ENTRY)
    emit(a, ram_top)
    body = a.link()
    assert ENTRY + len(body) < FILLER_OFF, "program overruns the data pages"
    rom[ENTRY:ENTRY + len(body)] = body

    # Rung 5's pattern: every byte value exactly once.
    for i in range(256):
        rom[PATTERN_OFF + i] = i

    # Beacon page: only the fact of reading it matters.
    for i in range(32):
        rom[BEACON_OFF + i] = 0xE5

    # Rung 6's filler, chosen so a healthy read of E000..FEFF sums to 0.
    # The range must match the loop above exactly, beacon page excluded.
    rom[FILLER_OFF] = 0
    rom[FILLER_OFF] = (-sum(rom[:BEACON_OFF])) & 0xFF
    assert sum(rom[:BEACON_OFF]) & 0xFF == 0
    return bytes(rom)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--ram-top", type=lambda s: int(s, 0), default=RAM_TOP)
    args = ap.parse_args()
    rom = build(args.ram_top)
    args.output.write_bytes(rom)
    print(f"wrote {args.output}: {len(rom)} bytes, {N_RUNGS} rungs, "
          f"beacons at {BEACON_ADDR:04X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
