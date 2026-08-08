# The BASIC ROM module: the board, its sockets, and its dead chips

The PMD 85-3's BASIC lives on a separate card — the ROM PACK — that plugs
into the machine's module connector rather than into the memory map. This
file covers the **card**: how its sockets are wired, what its logic does,
and what its chips turned out to hold. The **host** side — the ports the
machine reads it through, and the on-screen scanner that grades it — is in
[PMD85-3.md](PMD85-3.md#the-rom-module-and-its-scanner).

Read this one *after* [MHB2616.md](MHB2616.md), and read it as a
correction: the monitor sockets on the CPU board repurpose the 2616's two
select pins in a way that is specific to that board, and it is natural to
carry that model across. It does not apply here. This card uses the same
chip conventionally.

## The board

**DOSKA ROM PAMÄTÍ, 1 PK 280 53, pre typ PMD 85-3.** Sixteen 24-pin socket
positions in two rows of eight, an 8255 (IO1), a 7442 BCD decoder (IO2),
and — on the schematic — a hex inverter (IO19) and a transistor switching
the sockets' Vcc. The sheet carries the note *"osadiť iba pozície uvedené v
rozpiske"*: populate only the positions named in the parts list. It is a
universal layout, stuffed per variant — sixteen positions and eight selects
because the 1 KB builds (BASIC-G 1.0 and 2.0, nine chips) and the 2 KB
build (3.0, five chips) share one PCB.

## Socket wiring

Identical across all sixteen positions. Read off the schematic, with pin 20
confirmed by continuity on the board in hand:

| socket pin | function | driven by |
|------------|----------|-----------|
| 1–8 | A7…A0 | PB7…PB0 |
| 23, 22, 19 | A8, A9, A10 | PC0, PC1, PC2 |
| 9, 10, 11, 13–17 | D0–D7 | PA0–PA7 |
| 18 | **/CE** | /CSn from the 7442 |
| 20 | **/OE** | PC6 |
| 21 | — | not connected |
| 12, 24 | GND, Vcc | ground and the +5 V rail |

That is plain JEDEC 2716: both selects active low and both required, pin 21
unused. Nothing like the CPU board's PR-as-A11 and pair-/CS arrangement.

That is also an independent confirmation of the 2616's select sense.
MHB2616.md settles PR as active low from the archive's per-chip socket
assignment; this card reaches the same answer from the other end, since a
part that works *here* must have pin 18 and pin 20 as active-low selects
that AND together. Two boards, two arguments, one conclusion: the 2616 is
a conventional two-select ROM, used unconventionally on the CPU board
rather than an odd part used naturally.

## As built, against as drawn

**Measured on one board — the schematic is the superset and other builds
may differ.**

- **IO19 is not fitted.** Its footprint is jumpered, so PC6 reaches every
  socket's pin 20 directly, active low. On the schematic PC6 passes through
  an IO19 section with R1 pulling its output up.
- **The Vcc-switching transistor and its gate chain are not fitted.** The
  sockets sit on the +5 V rail permanently; there is no power gating and no
  settling delay on select.

One consequence worth checking on any given board: with the inverter
bypassed, **R1 now sits on the PC6 net itself**, where it is the only thing
holding `/OE` deasserted between reset and the first time firmware
programs the 8255 — at reset the 8255's ports are inputs, so PC6 is
high-Z. If R1 was pulled along with IO19, `/OE` floats in that window.
Benign as far as anything here can tell (port A is an input then too, so
there is nothing to contend with), but it is the kind of detail that
explains an intermittent later.

## The decode

The 7442 takes PC3, PC4, PC5 on A, B, C and PC7 on D, and drives
`/CS0`…`/CS7`. So the low three bits of the bank number select a socket,
and PC7 high parks all eight outputs — the module's own enable.

The module image begins at offset 0 (`basic3.txt`: *"umiestnenie v ROM
module od adresy 0000h"*), which makes the socket order fall straight out:

| select | module offset | BASIC-G 3.0 block |
|--------|---------------|-------------------|
| /CS0 | 0x0000–0x07FF | basic3-1 |
| /CS1 | 0x0800–0x0FFF | basic3-2 |
| /CS2 | 0x1000–0x17FF | basic3-3 |
| /CS3 | 0x1800–0x1FFF | basic3-4 |
| /CS4 | 0x2000–0x27FF | basic3-5 |

Worth stating plainly because it is the thing dumping the chips was
supposed to establish: **the schematic gives you the socket order, so a
module can be rebuilt without reading a single original chip.**

## The chips, identified from wreckage

This machine's module carries four chips where BASIC-G 3.0 wants five, and
all four are dead the way every MHB 2616 in this machine has died: mostly
zeros, a scatter of surviving set bits. Dumped through a programmer, none
of them matches anything — against the reference blocks each differs in
about 950 of 2048 bytes, which is the distance between two unrelated
things.

They are all identifiable anyway, because **a chip dying this way loses set
bits and never gains them.** A dump of block B can therefore hold no bit
that B does not also hold, and the count of such impossible bits is zero
for exactly one block and hundreds for every other. `rom_checksum.py
--identify` runs this automatically once the byte count has given up:

| dump | block | surviving bits | of the block's | next-best block |
|------|-------|----------------|----------------|-----------------|
| rom1 | basic3-1 | 1295 | 7664 (16.9%) | 663 impossible |
| rom2 | basic3-2 | 244 | 7895 (3.1%) | 118 impossible |
| rom3 | basic3-3 | 181 | 7528 (2.4%) | 92 impossible |
| rom4 | basic3-4 | 32 | 7481 (0.4%) | 17 impossible |

The fourth chip is named from 32 bits out of 16384 — four hundredths of the
die — and the identification is not close. The dumps also came off in
socket order, which the method did not know and which corroborates it.
`basic3-5` is the chip the module is missing.

Two things this is worth remembering for:

- **Byte counts and bit counts fail at very different depths.** Comparing
  bytes stops discriminating at about a quarter of them wrong; comparing
  bits in the direction the decay runs keeps working to within a fraction of
  a percent of total loss. The test that fails is not evidence the chip is
  unidentifiable.
- **An asymmetric failure is more informative than a symmetric one.** The
  whole method rests on knowing decay has a direction. Symmetric noise —
  a floating bus, a bad contact — would carry no such structure, and
  distinguishing the two is exactly what tells a dead chip from a bad read.

## The boot contract

**Read off `monit3B.rom` by disassembly; not yet confirmed on hardware.**

Two things had to be established before anything could be served here, and
both are in the monitor rather than on the card.

**The ports are aliased.** The module's 8255 answers at `88h–8Bh` in every
description of the machine, and the monitor never touches those addresses.
It uses **`F8h–FBh`**, which the module's own decode (`port & 8Ch == 88h`)
maps to the same chip: `F8h` port A (data in), `F9h` port B (address low),
`FAh` port C (address high), `FBh` control. The system 8255 at `F4h–F7h`
sits just below and does *not* match that decode. Searching a monitor for
`88h` finds nothing and proves nothing.

**The block-read routine is at `EC00h`**, and it takes its arguments inline
after the `CALL`: source address (2), byte count (2), destination (2). It
programs the 8255 with `90h` — port A input, B and C output — writes the
address, then reads bytes with the strobe *held*, incrementing the address
through the ports themselves. It ends with `MVI A,FFh / OUT FAh`, which
parks the card: that single store raises PC7 (decoder off) and PC6 (`/OE`
off) together, and it is why the firmware treats both as gates.

That the strobe is held rather than pulsed per byte is the detail the serve
loop depends on. The board gates on a level, exactly as the chip did.

### The module gets to run its own code at boot

At `E02D`, in the reset path, the monitor does this:

```
E02D  CD 00 EC   CALL EC00h        ; block read...
      00 00                        ;   from module 0x0000
      0D 00                        ;   count field 0x000D
      B2 C1                        ;   to RAM C1B2
E036  3A B2 C1   LDA C1B2h
E039  FE CC      CPI CCh           ; signature?
E03B  CA B2 C1   JZ  C1B2h         ; ...then execute it
```

**The module's first bytes are copied into RAM and jumped to if the first is
`CCh`.** Fourteen of them, not thirteen: the loop pre-increments the count's
high byte (`INR B`) and then tests only that byte, so it transfers
`count + 1`. Worth pinning down rather than reading off the field, because
it is the size of the budget a module gets to bootstrap itself in.

BASIC-G 3.0 spends twelve of the fourteen: 

```
CC 00 EC        CZ EC00h      ; signature AND instruction
00 24 01 04 00 B8             ;   module 0x2400, 1 KB, to B800
C3 00 B8        JMP B800h     ; run what was just loaded
```

The signature byte is doing double duty and it is worth admiring: `CCh` is
`CZ`, call-if-zero, and the Z flag is guaranteed set because the very `CPI
CCh` that validated the signature set it. So the byte that identifies the
module is also the instruction that bootstraps it. The stub copies its
second stage to `B800h` — the 1 KB at `B800h` that `basic3.txt` documents as
BASIC's second destination — and jumps in.

The consequence is larger than BASIC: **a ROM module chooses what the
machine runs, in thirteen bytes, with the monitor's own loader available at
`EC00h` to pull in as much more as it wants.** Anything that fits the window
can be booted this way. See ROADMAP.md for what that opens up.

## Substituting 2732s

**Untested on hardware; the reasoning is from the wiring above.**

The 2732 differs from the 2716 in exactly one pin: 21 is A11 rather than
Vpp. Here pin 21 is unconnected, so a 2732's A11 would float — the only
thing needing attention. Tie it to Vcc and burn the 2 KB block at 0x800, or
to GND and burn it at 0x000. Simpler still, burn the block into **both**
halves and the pin stops mattering, which also means one image works
whether a given board straps pin 21 or leaves it open.

Everything else drops in: `/CE` from the 7442 and `/OE` from PC6 are both
active low and both required, which is 2732 read behaviour unchanged.

- **Speed grade is irrelevant.** Reads here are software-mediated through
  the module's 8255 — write the address to ports B and C, read port A back
  — so a byte takes microseconds. The slowest NMOS part will do.
- **Standby current is the real change.** `/CE` per socket keeps four of
  five chips idle, but NMOS 2732 standby is tens of milliamps each against
  a mask ROM's far less, and this board has no Vcc gating to help. CMOS
  27C32 if there is a choice.

## Serving the whole module from one board

`MHB_BANK_SOURCE=MODULE`. **Built and host-tested; not yet run on
hardware.** A board in any one socket already sees A0–A10, `/OE` on pin 20
and the data bus. What it cannot see is which *other* socket an access is
for — and the cheap way to learn that is to read the 7442's three address
inputs rather than its five outputs.

| board input | socket pin | carries | lead from |
|-------------|-----------|---------|-----------|
| `GPIO_nCS` | 20 | `/OE`, the read strobe | — already in the socket |
| `GPIO_PIN21` | 21 | module A11 | IO2 pin 15 (PC3) |
| `GPIO_X1` | — | module A12 | IO2 pin 14 (PC4) |
| `GPIO_X2` | — | module A13 | IO2 pin 13 (PC5) |
| `GPIO_PR` | 18 | park (A15) | IO2 pin 12 (PC7) |

Four leads. Socket pin 21 is unconnected on this card, so PC3's lead can be
soldered to that dead pad and reach the board through the socket with no
board-side work at all. **Pin 18 is the one modification**: it carries
`/CS0`, which MODULE mode does not use, so the board's pin 18 must be kept
out of the socket and the PC7 lead landed on it directly.

Park matters as much as the address bits. The monitor ends every block read
by writing `FFh` to port C, and a board that ignored PC7 would answer reads
the machine expects to come back `FFh` — including whatever probes the
`A15` convention exists for.

All four leads are **pulled up** in MODULE builds, which is what makes a
harness that has fallen off safe rather than wrong: park reads "decoder
off" and the address leads read bank 7, which no BASIC image occupies. A
broken wire produces a silent board and a red pixel, not a wrong byte.
`test_module_detached_harness` asserts exactly that.

One board therefore answers for all five chips, including the one this
module is missing — and the module's microsecond access times leave the
serve loop nothing to worry about.

### Building it

```
cd tools
./gen_rom_images.py -o ../firmware/rom_images.c --module basic3.rmm
cd ../firmware && mkdir -p build && cd build
cmake .. -DMHB_BOARD=FIRE24F -DMHB_BANK_SOURCE=MODULE
make
```

The generated image carries a `_Static_assert` on its own bank count, so a
module image built into a monitor firmware (or the reverse) is a compile
error naming both numbers rather than a board that serves a quarter of the
window.

## Sources

- PMD 85-3 ROM module schematic, DOSKA ROM PAMÄTÍ 1 PK 280 53 (sheet in
  project correspondence; socket wiring, the 7442 decode, and the IO19 and
  Vcc-switch positions are read directly off it). Deviations as built were
  reported from the board in hand.
- [PMD 85 Infoserver, ROM page](https://pmd85.borik.net/wiki/PMD_85_ROM) and
  the RM-TEAM ROM archive — `RomModul/Basic3`, five 2 KB blocks with the
  module's own PRIZNAK values in `basic3.txt`.
