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

**A sketch, not a built thing.** A One ROM board in one socket already sees
A0–A10, `/OE` on pin 18's neighbour, its own `/CSn`, and the data bus. What
it cannot see is which *other* socket is being addressed — but it does not
need five more wires to find out, because the 7442's three address inputs
carry the same information: PC3, PC4, PC5 on IO2 pins 15, 14, 13.

Three flying leads, and the board knows the full bank number. Better, this
card leaves socket pin 21 unconnected, so one of the three can land on the
board's existing pin-21 input by soldering to an otherwise dead socket pad;
X1 and X2 take the other two. One board would then answer for all five
chips including the one that is missing, with the module's microsecond
access times leaving the serve loop nothing to worry about.

What this needs that does not exist yet is a mode: gate on `/OE` low, take
the bank from the three decode inputs rather than from PR, and ignore the
socket's own `/CSn` entirely. Recorded so the idea is not rediscovered from
scratch.

## Sources

- PMD 85-3 ROM module schematic, DOSKA ROM PAMÄTÍ 1 PK 280 53 (sheet in
  project correspondence; socket wiring, the 7442 decode, and the IO19 and
  Vcc-switch positions are read directly off it). Deviations as built were
  reported from the board in hand.
- [PMD 85 Infoserver, ROM page](https://pmd85.borik.net/wiki/PMD_85_ROM) and
  the RM-TEAM ROM archive — `RomModul/Basic3`, five 2 KB blocks with the
  module's own PRIZNAK values in `basic3.txt`.
