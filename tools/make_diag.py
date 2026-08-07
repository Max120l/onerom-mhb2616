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
    7  I/O          a write to the system 8255, clearing the startup map
    8  RAM          March C- plus an immediate read-back control

Rungs 0-6 are the processor and the ROM path and nothing else: no writes,
no RAM reads, and no I/O. The machine's first OUT is a rung of its own,
because writing to a port is the most hardware-dependent thing the ladder
does and it has no business sitting below the rungs that prove the CPU can
execute at all. An earlier version cleared the mirror map inside rung 0,
which meant a machine that died on the port write reported "the processor
never got past its first instruction" -- true, but pointing at the wrong
part.

Rung 7 writes to port F4h, NOT the F7h the monitor uses. See the note on
the io stage below: `OUT F7h` with bit 7 set takes the ROM out of the
address space, and doing that from ROM ends the program.

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

`--stage io` builds a second nine-rung image for the machine that climbs
0-6 and then stops. It drops the processor rungs, which have by then been
proved, and spends all nine on the thing that is left: one kind of bus
cycle per rung, in order of what each depends on.

    0  alive
    1  a memory WRITE completed        (still mirrored, so a write and
                                        nothing else -- rungs 0-6 above are
                                        all reads, so this is the machine's
                                        first write of any kind)
    2  a memory READ completed         (still mirrored, so served from ROM:
                                        no DRAM cell involved)
    3  the startup map cleared         (OUT F4h -- see below)
    4  RAM actually holds data         (rung 1's byte, read back at last)
    5  RAM, March C-
    6  video RAM                       (C000-DFFF, the one region arbitrated
                                        against the display circuit)
    7  the monitor's paging dance      (the one step that cannot be made safe)
    8  came back from it

The order is not cosmetic. `OUT F7h` with bit 7 set is an 8255 mode set:
it configures port C upper as an output and clears every port C latch on
the way, which drops PC4 -- and PC4 is what puts the ROM at E000-FFFF. So
that single instruction removes the ROM from the machine, and the next
instruction is fetched from RAM. The monitor copies its own next four
bytes into RAM first and lets them execute from there (monit3B E0A3-E0B7);
anything that issues the mode set from ROM without doing so stops
existing on that instruction.

An earlier version of this file did exactly that, in both stages, and the
resulting hang was read as a machine fault twice. Rung 3 therefore clears
the startup map with a write to port A instead, which leaves port C
untouched and the ROM in place, and the paging dance is deferred to rung 7
where everything else has already been measured.

An 8080 cannot fall over on a bad instruction; it can only stall waiting
for READY, or sit in HOLD. So on this image "started rung N and never
finished" -- blue x (N+1) -- names a bus cycle that never completed, and
that is a specific enough fact to put a probe on.

`--stage map` answers a narrower question still: which write, if any,
takes the machine out of the mirrored startup map? Six candidates, least
invasive first.

    0  alive
    1  leaves 5A at 0000 to recognise RAM by
    2  any I/O write at all       OUT 00h, an undecoded port
    3  8255 port A                F4h
    4  8255 port B                F5h
    5  8255 port C                F6h, writing 10h so PC4 stays set
    6  8255 control register      BSR set PC4 -- a control-register write
                                  that does NOT clear the latches
    7  a READ of the 8255         IN F6h
    8  none of them worked

Reporting is inverted in that stage, and only there: the rung that
"fails" is the rung that *worked*. Red x (N+1) names the write that
cleared the mirror, red x9 means nothing did, and blue keeps its ordinary
meaning of a cycle that never completed.
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

VRAM_BASE = 0xC000             # video RAM, shared with the display circuit

N_RUNGS = 9
RUNG_STARTED = 0               # beacons 0..8
RUNG_FAILED = N_RUNGS          # beacons 9..17
ALL_PASSED = 2 * N_RUNGS       # beacon 18
BIT_BASE = 2 * N_RUNGS + 1     # beacons 19..26

# Both stages are nine rungs, so one firmware constant and one lamp serve
# either.  `bit_rungs` are the rungs that go on to name which data bits
# were wrong; the rest just fail.
STAGES = {
    "cpu": {"emit": lambda a, t: emit(a, t), "bit_rungs": (5, 8)},
    "io": {"emit": lambda a, t: emit_io(a, t), "bit_rungs": (5,)},
    "map": {"emit": lambda a, t: emit_map(a, t), "bit_rungs": ()},
}


# The `map` stage sweeps these, in this order, and reports the first one
# that clears the startup mirror.  Ordered least invasive first.
#
# Port C carries the paging bits, so the one write that touches it writes
# 10h and not 00h: PC4 is what holds the ROM in the address space, and
# clearing that latch is the mistake this whole stage exists because of.
MAP_CANDIDATES = [
    ("any I/O write at all", lambda a: (a.mvi(A, 0x00), a.out(0x00))),
    ("8255 port A, F4h", lambda a: (a.mvi(A, 0x00), a.out(0xF4))),
    ("8255 port B, F5h", lambda a: (a.mvi(A, 0x00), a.out(0xF5))),
    ("8255 port C, F6h", lambda a: (a.mvi(A, 0x10), a.out(0xF6))),
    ("8255 control reg, BSR set PC4", lambda a: (a.mvi(A, 0x09), a.out(0xF7))),
    ("a READ of the 8255", lambda a: a.inp(0xF6)),
]


def emit_map(a: Asm, ram_top: int) -> None:
    """The `map` stage: which write, if any, clears the startup mirror?

    The io stage established that `OUT F4h` does not, on this machine,
    though the reference emulator says it should.  Either the real trigger
    is narrower than that model, or the latch is stuck -- and if it is
    stuck, the monitor cannot reach RAM either and that is the whole fault.
    Six candidates, cheapest first, and the answer is a count.

    Reporting is inverted here, deliberately and only here: a rung that
    "fails" is a rung that *worked*.  Red x (N+1) names the write that
    cleared the mirror, red x9 means none of them did, and blue keeps its
    ordinary meaning of a cycle that never completed.  Sharing the lamp
    beats inventing a seventh way to blink at somebody.
    """
    # ---- rung 0: alive ---------------------------------------------------
    a.beacon(RUNG_STARTED + 0)

    # ---- rung 1: leave a byte behind to recognise RAM by ------------------
    # Writes go to RAM even under the startup map, so this lands in a cell
    # that only becomes readable once the mirror is gone.  0000 in ROM is
    # C3, the entry jump, so C3 coming back means the ROM is still
    # answering and anything else means it is not.
    a.beacon(RUNG_STARTED + 1)
    a.lxi(RP_H, 0x0000)
    a.mvi(M, 0x5A)

    # ---- rungs 2..7: one candidate each -----------------------------------
    for i, (_, emit_write) in enumerate(MAP_CANDIDATES):
        n = i + 2
        a.beacon(RUNG_STARTED + n)
        emit_write(a)
        a.lxi(RP_H, 0x0000)
        a.mov(A, M)
        a.cpi(0xC3)
        a.jnz(f"fail{n}")                      # not the ROM: this one won

    # ---- rung 8: none of them cleared it ----------------------------------
    a.beacon(RUNG_STARTED + 8)
    a.jmp("fail8")


def emit_io(a: Asm, ram_top: int) -> None:
    """The `io` stage: one bus cycle per rung, in order of what they need.

    Only worth building once the `cpu` stage has climbed rungs 0-6, because
    it drops them.  What it buys is that every rung is a *single kind of bus
    cycle*, so a machine that freezes on one names which one.  A hang is the
    point here: the 8080 cannot fall over on a bad instruction, it can only
    stall waiting for READY (or sit in HOLD), so "started rung N and never
    finished" is a bus cycle that never completed.
    """
    # ---- rung 0: alive ---------------------------------------------------
    a.beacon(RUNG_STARTED + 0)

    # ---- rung 1: the first memory WRITE ----------------------------------
    # Still in the startup map, where writes go to RAM and reads come from
    # ROM.  So this is a write cycle and nothing else: no read depends on
    # it, and the value cannot be checked yet.  Deliberately first, because
    # a write is the simplest cycle the machine has never yet been asked to
    # do -- rungs 0-6 of the cpu stage are all reads.
    a.beacon(RUNG_STARTED + 1)
    a.lxi(RP_H, 0x0000)
    a.mvi(M, 0x5A)

    # ---- rung 2: a memory READ at a low address --------------------------
    # Still mirrored, so this comes from ROM: it exercises the read cycle
    # and the low half of the address bus without depending on a single
    # DRAM cell.  Offset 0000 of the image is C3, the entry jump.  Anything
    # else means the startup map is not in the state a reset leaves it in.
    a.beacon(RUNG_STARTED + 2)
    a.lxi(RP_H, 0x0000)
    a.mov(A, M)
    a.cpi(0xC3)
    a.jnz("fail2")

    # ---- rung 3: clear the startup map, WITHOUT losing the ROM -----------
    # Any write to the system 8255 clears the mirror.  This one goes to
    # port A, not the control register, and that distinction is the whole
    # rung: a write to the control register with bit 7 set is a mode set,
    # and a mode set clears the port C latches, which drops PC4, which
    # takes the ROM out of the address space entirely.  Port A leaves port
    # C alone, so the machine lands in the ordinary map -- RAM below E000,
    # ROM above it -- and everything below can be measured with the board
    # still being read.
    #
    # Port A is an input until something configures it otherwise, so the
    # value written goes to a latch that drives nothing.  That is the point:
    # it is the most harmless write the machine accepts.
    a.beacon(RUNG_STARTED + 3)
    a.mvi(A, 0x00)
    a.out(0xF4)

    # ---- rung 4: the first proof that a RAM cell holds anything ----------
    # Rung 1 wrote 5A to 0000 and could not check it, because reads were
    # still coming from ROM.  Now they are not.  C3 means the mirror never
    # cleared and this is still the ROM answering; anything else means the
    # map moved, and whether the value is *right* is rung 5's business.
    a.beacon(RUNG_STARTED + 4)
    a.lxi(RP_H, 0x0000)
    a.mov(A, M)
    a.cpi(0xC3)
    a.jz("fail4")

    # ---- rung 5: RAM ------------------------------------------------------
    a.beacon(RUNG_STARTED + 5)
    emit_ram(a, ram_top, "fail5")

    # ---- rung 6: video RAM -----------------------------------------------
    # C000-DFFF is shared with the display, so it is the one region whose
    # access is arbitrated against the video circuit.  If that arbitration
    # is stuck the CPU waits here and nowhere else.  It writes a ramp, which
    # also puts a recognisable pattern on a screen that has sync.
    a.beacon(RUNG_STARTED + 6)
    a.lxi(RP_H, VRAM_BASE)
    a.label("r6loop")
    a.mov(A, L)
    a.mov(M, A)
    a.inx(RP_H)
    a.mov(A, H)
    a.cpi(VRAM_TOP)
    a.jnz("r6loop")
    a.lxi(RP_H, VRAM_BASE)
    a.mov(A, M)
    a.ora(A)
    a.jnz("fail6")                             # L was 0 there, so must be 0

    # ---- rung 7: the monitor's paging dance, done properly ---------------
    # Last, and deliberately so: this is the one step that cannot be made
    # safe, because for four instruction bytes there is no ROM in the
    # machine at all.
    #
    # `OUT F7h` with bit 7 set is an 8255 mode set.  It configures port C
    # upper as an output, and on the way it clears every port C latch --
    # including PC4, which is what puts the ROM at E000-FFFF.  So the
    # instruction after it is fetched from RAM.  The monitor handles this by
    # copying its own next four bytes into RAM at the same addresses before
    # it jumps (monit3B E0A3-E0B7, with LHLD/SHLD); reads come from ROM and
    # writes go to RAM, so copying a byte to where it already is moves it
    # from one to the other.  This does the same thing a byte at a time.
    #
    # Everything above has already run, so if the machine dies here it dies
    # having told us the processor, the bus, the ports and RAM are sound --
    # which makes it a fact about the paging latch and not a mystery.
    a.beacon(RUNG_STARTED + 7)
    a.lxi(RP_H, "tramp")
    a.mvi(C, 4)
    a.label("r7copy")
    a.mov(A, M)                                # from ROM
    a.mov(M, A)                                # ...to RAM, same address
    a.inx(RP_H)
    a.dcr(C)
    a.jnz("r7copy")

    a.mvi(A, 0x82)
    a.out(0xF7)                                # ROM leaves the address space
    a.label("tramp")                           # these four bytes run from RAM
    a.mvi(A, 0x09)
    a.out(0xF7)                                # BSR sets PC4: ROM is back

    # ---- rung 8: we came back --------------------------------------------
    # Reaching this beacon at all is the result: the board is being read
    # again, so the ROM returned to the map and the machine survived the
    # only manoeuvre its own monitor cannot avoid.
    a.beacon(RUNG_STARTED + 8)
    a.lxi(RP_H, BASE)
    a.mov(A, M)
    a.cpi(0xC3)                                # E000 is the entry jump
    a.jnz("fail8")


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
    # Clear the startup mirror map.  Until this happens reads below E000
    # come from ROM and writes go to RAM, so no RAM rung is possible -- but
    # nothing above this point needed it either.
    #
    # Port A of the system 8255, NOT the F7h the monitor writes.  F7h is the
    # control register, and the monitor's 82h is a mode set: it clears the
    # port C output latches, dropping PC4, and PC4 is what puts the ROM at
    # E000-FFFF.  Issuing it from ROM ends the program on that instruction.
    # The monitor gets away with it by executing its next four bytes from a
    # RAM copy; the io stage reproduces that manoeuvre deliberately, but a
    # ladder whose job is to get as far as RAM has no business risking it.
    #
    # The check afterwards is the map itself, not memory: write a byte to
    # 0000 and read it back.  Address 0000 in ROM holds C3, the entry jump.
    # Reading back exactly C3 means the read still came from ROM and the OUT
    # did not take.  Anything else means the map switched; whether the value
    # is right is rung 8's business, so only C3 fails here.
    a.beacon(RUNG_STARTED + 7)
    a.mvi(A, 0x00)
    a.out(0xF4)
    a.lxi(RP_H, 0x0000)
    a.mvi(M, 0x5A)
    a.mov(A, M)
    a.cpi(0xC3)
    a.jz("fail7")

    # ---- rung 8: RAM -----------------------------------------------------
    a.beacon(RUNG_STARTED + 8)
    emit_ram(a, ram_top, "fail8")


def emit_tail(a: Asm, bit_rungs) -> None:
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
        if n in bit_rungs:                     # these ones know which bits
            for bit in range(8):
                a.mov(A, B)
                a.ani(1 << bit)
                a.jz(f"f{n}s{bit}")
                a.beacon(BIT_BASE + bit)
                a.label(f"f{n}s{bit}")
        a.jmp(f"fail{n}")


def emit_ram(a: Asm, ram_top: int, fail: str) -> None:
    """March C- with an immediate read-back control; faults jump to `fail`."""
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
    a.mov(A, B); a.ora(A); a.jnz(fail)

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
        a.ora(B); a.mov(B, A); a.jmp(fail)
        a.label(f"{tag}ok")
        a.mvi(M, write)
        if down:
            a.dcx(RP_H); a.mov(A, H); a.cpi(0xFF); a.jnz(tag)
        else:
            a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz(tag)

    a.lxi(RP_H, 0x0000)
    a.label("m5")
    a.mov(A, M); a.ora(A); a.jz("m5ok")
    a.ora(B); a.mov(B, A); a.jmp(fail)
    a.label("m5ok")
    a.inx(RP_H); a.mov(A, H); a.cpi(ram_top); a.jnz("m5")


def build(ram_top: int = RAM_TOP, stage: str = "cpu") -> bytes:
    rom = bytearray(b"\x00" * ROM_SIZE)

    head = Asm(BASE)
    head.jmp(BASE + ENTRY)
    rom[0:len(head.buf)] = head.link()

    spec = STAGES[stage]
    a = Asm(BASE + ENTRY)
    spec["emit"](a, ram_top)
    emit_tail(a, spec["bit_rungs"])
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
    ap.add_argument("--stage", choices=sorted(STAGES), default="cpu",
                    help="cpu: the processor ladder.  io: one bus cycle per "
                         "rung, for a machine that passes cpu 0-6 and then "
                         "stops.")
    args = ap.parse_args()
    rom = build(args.ram_top, args.stage)
    args.output.write_bytes(rom)
    print(f"wrote {args.output}: {len(rom)} bytes, stage {args.stage}, "
          f"{N_RUNGS} rungs, beacons at {BEACON_ADDR:04X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
