# One ROM Fire 24: what the board itself does

Findings about the carrier board rather than the chip or the machine. All
of it applies to both revisions unless stated; where they differ, both are
given.

## The jumper block is only half usable

**Measured on hardware (rev F), and explained by both boards' netlists.**

The 2x4 header carries four jumper columns, labelled A B C D on the
underside, with 5V/GND beside them and X1/X2 below. Fitting a jumper
shorts the two pads of one column. What those pads are is *not* uniform:

| label | rev E GPIO | rev F GPIO | closed jumper ties to | also hard-wired to | usable |
|-------|-----------|-----------|----------------------|--------------------|--------|
| A | 25 | 26 | GND | — | **yes** |
| B | 24 | 27 | GND | — | **yes** |
| C | 26 | 25 | BOOT (→ R2 → QSPI_SS) | SWCLK (MCU pad 23) | **no** |
| D | 27 | 24 | RUN (10K to +3V3) | SWDIO (MCU pad 25) | **no** |

Columns C and D are the board's debug/recovery header doing double duty:
their top pads are BOOT and RUN, and their bottom pads are the select
GPIOs *and* the SWD pads. In the PCB netlist each of those two GPIOs
appears on **two** MCU pins — the GPIO itself and an SWD pin — which is
what makes them unreadable as jumpers: SWD pins carry their own internal
pulls (SWCLK down, SWDIO up), and those fight any pull the firmware
applies, so the "does this pin follow our pull?" test cannot see the
jumper.

How it showed up: a rev F bench run of the selftest image, dumping all
four jumper settings through a 2716 programmer, produced only **two**
distinct images. Jumper B moved bank bit 0 correctly; jumper C never
moved bit 1, which read as permanently fitted — banks 2 and 3 only.
Jumper C's own GPIO sits on SWCLK's pull-down, so both halves of the test
read the same and "fitted" is the answer it always gives.

**Consequence for this firmware.** Only A and B are clean shorts to
ground, and A is the recovery jumper — so exactly one jumper is free,
which is not enough to carry a two-bit bank number. The bank is therefore
a build-time constant (`MHB_SOCKET_BANK`, 0–3) and the jumper path is
gone. In service this costs nothing: a board in a socket serves that
socket's bank, and that is known when the firmware is built.

**Do not** try to reclaim C or D by driving them, or by pulling harder.
D is the RUN (reset) net and C reaches QSPI_SS through R2; both are load
bearing at boot, and neither is worth a bank bit.

### If a jumper-selected bank is ever wanted again

The X1/X2 pads are a 2-pin header of their own (nets CX1/CX2, GPIO 9 and
8), and both are ordinary GPIOs with nothing else on them. A jumper
across them can be detected *actively* rather than by pulls: drive X1
low, read X2; drive X1 high, read X2; X2 follows only if the jumper is
fitted. That is robust in a way the pull test is not — but X1 is the
FULL8K flying-lead pad, so it is only free in STATIC and HOTSPOT modes.
Untested; recorded here so the idea is not rediscovered from scratch.

## The rev F schematic's net names are rotated

On rev F, the schematic's `SEL_A`…`SEL_D` nets do **not** correspond to
the silkscreen letters: net `SEL_A` lands on the pad labelled **D**,
`SEL_B` on **C**, `SEL_C` on **A**, `SEL_D` on **B**. (Rev E's net names
line up in a different order again.) Identify jumpers from the
silkscreen and the PCB netlist; never from the net names.

## The socket-to-GPIO map is identical on rev E and rev F

Every signal pin — the eleven address lines, eight data lines, both
select pins, socket pin 21, and the X1/X2 pads — lands on the same GPIO
on both revisions. Only the indicator and the jumper GPIOs move. This is
why one firmware serves both and `MHB_BOARD` changes nothing on the
serving path.

## The rev F indicator is a WS2812B

GPIO 29, a single XL-1010RGBC-WS2812B (rev E has a plain LED on the same
GPIO). It needs the 800 kHz serial protocol, so a `gpio_put` does
nothing to it. This firmware drives it from a small PIO program on core 0
— the only PIO in the build, and cosmetic; serving remains a CPU loop on
core 1.

Colours, chosen so that a board with nothing to do and a board that is
dead stop looking alike:

| colour | meaning |
|--------|---------|
| blue blip at power-on | firmware started |
| green | selects arriving, serving |
| faint red | powered, but nothing is selecting us |

Some WS2812 clones want RGB order rather than GRB; if the colours come
out permuted, that is the first thing to check (`NEO_GRB` in `main.c`).

## Serving latency, counted from the disassembly

Worth having as numbers rather than adjectives, because "the board is too
fast for the machine" is a tempting theory and these are what refute it.
Counted off the compiled serve loop at 150 MHz (6.67 ns/cycle), including
the RP2350's two-flop GPIO input synchroniser:

| event | best | worst |
|-------|------|-------|
| data valid after a select falls | 60 ns | 113 ns |
| bus released after a select rises | 47 ns | 120 ns |

Against the part being replaced: a 2616/2716 answers a select in up to
~450 ns and floats its outputs 0–100 ns after deselect. So the board is
three to seven times quicker to present data — the margin that makes this
project viable — and **releases the bus no faster than the original
chip's own specified window**. A host that latches read data correctly
from a real 2616 cannot be losing it to an early release here; the
release is, if anything, on the slow side of what the chip could legally
do.

That kills the data-hold-time hypothesis for this design without needing
a scope, and it also says a deliberate hold delay would be treating a
symptom that cannot exist. If a future host ever does need one, the place
to add it is the `else if (driving)` arm of the serve loop, and the note
to write down first is what measurement justified it.
