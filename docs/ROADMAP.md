# Roadmap

In order, each step gated by the question it answers. Nothing below a gate
is worth building until the gate opens — the sibling project wrote this rule
down after learning it the slow way.

## ~~1. The control pins~~ — answered by the schematic

The PMD 85-3 CPU board schematic settled what was going to be a meter-and-
scope session: /CS on pin 20 (pair select, DS4+DS5 and DS6+DS7), PR on
pin 18 (A11, inverted for DS5/DS7), pin 21 strapped to +5 V. The firmware's
modes were rebuilt around the measured wiring; the guessed EXT mode
(two address leads, common-/OE gating) died with the guess it was built on.

## ~~2. Bank ↔ socket assignment~~ — answered by the Infoserver archive

The archive's per-chip files match `monit3B.rom` byte-for-byte:
DS4 "E" = bank 0 … DS7 "B" = bank 3, chips selected at PR low. The full
table with per-socket build switches is in docs/PMD85-3.md, along with
reference CRCs for `monit3.rom` and `monit3B.rom` (they differ in banks
0 and 3 only). No chip dumps were needed — which is fortunate, because
the target machine's four originals are all bad.

## ~~3. Bench selftest through a ROM reader~~ — PASSED on a rev F

**Question: do the scrambles on real silicon match the ones the host tests
prove?  Answer: yes, exactly.**

A rev F board, selftest image, read back through a 2716 programmer:
**all four banks clean, each matching only its own bank.** Every address
line, every data line, both bit scrambles and the /CS gating confirmed
against real hardware. Nothing on the serving path is unverified outside
a machine now.

It took two passes. The first used jumper-selected banks and returned
only two distinct images — which is how the jumper fault below was
found; the second pinned each bank at build time and swept all four.

The run also found a board-level fault that had nothing to do with
serving: two of the four jumpers cannot be read at all, because they are
the BOOT/RUN + SWD pads doing double duty and the SWD pins' own internal
pulls defeat the detection. The bank is a build-time constant now; see
docs/BOARD-NOTES.md for the netlist evidence and the hardware symptom.

## 4. STATIC replacement in a running machine

**Question: does the machine accept the board as one of its four chips?**

The first hardware milestone with user value: a 2616 replacement, dead
chip out, board in, `-DMHB_SOCKET_BANK=<n>` and `-DMHB_PR_INVERT=<...>`
per the socket table in docs/PMD85-3.md. The -3's own startup ROM test is
the acceptance test.

Worth knowing before it is attempted on this machine: **all four of its
originals are bad**, so a STATIC board fixes one quarter of a monitor
that is broken in four places — the machine will still fail its ROM test
on the other three. The rung is therefore diagnostic rather than
restorative here: it proves the board serves correctly in-circuit (levels,
timing, /CS and PR gating live) while only one socket is in play, which
is the variable worth isolating before three more join it. Watch the
status pixel, not the screen: green means the machine is reading us.

If the goal is simply a working machine, skipping to rung 6 (FULL8K,
which replaces all four at once) is legitimate — but a failure there has
four times the surface to search.

## 5. PAIR: two chips from one socket, zero wires

**Question: does PR-as-A11 serving hold up live?**

Pair-mate out, `MHB_SOCKET_PAIR` set. This rung exists because it isolates
the one new serving behaviour (bank switching on PR) from the one new wire
(FULL8K's X1 lead) — when something fails, it says which half to suspect.

## 6. FULL8K: the whole monitor from one socket

**Question: does one flying lead close the last address bit?**

All four originals out, other pair's pin 20 to X1. Boot to BASIC with the
machine's ROM test passing closes the project's headline feature.

## 7. HOTSPOT: banked software beyond 8 KB — *after the above*

The hotspot machinery is in the firmware and host-tested, but it has no
consumer until there is software written for it. Candidates, in rising
ambition: a test/diagnostic ROM for this machine (the startup mirror map
means code at the reset vector is ours); a menu ROM that hotspot-switches
between monitor variants (stock, patched, diagnostic) without reflashing.
Design constraint recorded now: hotspots default to 0x7F4–0x7F7 of the
window, so hotspot-aware images must keep those four bytes free.

## ~~8. MODULE: the BASIC ROM module from one board~~ — PASSED

**Question: does the board serve a card it was never designed for?
Answer: yes, all ten blocks.**

`MHB_BANK_SOURCE=MODULE`, three flying leads to the module's own 7442 and
pin 21 freed from the +5 V rail. The scanner lights the B3 row completely:
all ten 1 KB blocks of BASIC-G 3.0 correct, read the way the machine reads
them — through the connector, the module's 8255 and its decode — from one
board in place of five chips, of which this machine physically has four.
The missing chip is served like any other.

It took two passes. The first crossed the X1 and X2 leads, which is the
fault worth remembering because it does not look like one: every block read
correct and only their *order* was wrong. Both readings from the run are in
docs/ROM-module.md, along with the sum-column table that names each wiring
fault without counting boxes.

Independent of rungs 4–6: a different card, whose faults cannot mask theirs.

## ~~9. A boot menu in the module~~ — PASSED on hardware

**Question: can the module carry a shelf of programs behind a menu?
Answer: yes — 2026-08-08, same day as rung 8.** Menu up at power-on,
BASIC-G 3.0 and the test cartridge both booting from their keys, reset
and power-cycle semantics as designed.

`tools/make_multiload.py` builds it: page 0 a generated menu (keyboard
matrix scan, names rendered from the font), every other page a cartridge —
`basic3.rmm` verbatim, or a raw binary wrapped in a boot stub of its own.
Selection touches a hotspot (a live read of module `0x3FE0+n` names page
*n*), sits out the board's rebuild, and then replays the monitor's own
`E02D` boot against the new page — so BASIC boots through its stock stub,
byte-identical to a cartridge swap. Reset relaunches the mapped cartridge,
exactly as a real module would; power cycle returns to the menu.

The emulator run covers: the menu loading and rendering, a keypress
booting BASIC through its own two-stage dance, a raw-binary cartridge
booting, reset semantics, the unbootable-page fallback, and the delay
contract measured on the bus clock — against a monitor reconstructed
instruction-for-instruction from the disassembly, which is itself a test
of the ABI reading. Details in docs/ROM-module.md.

Since the hardware pass, the shelf also reaches back a generation:
`rmm2` entries boot PMD 85-2 modules through the -3 monitor's own
`JMP FFF0h` compatibility switch — no firmware change, one menu action.
Confirmed on hardware the same day: BASIC 2A boots from its key,
the machine relocating itself into a PMD 85-2 mid-menu.

Both of the next two rungs landed together after a games archive
arrived: **multi-page cartridges** (a generated stage-2 pages chunks in
through the same touch-and-wait contract, `Jet Set Willy` at 30.7 KB
being the customer) and **tape entries** (`.ptp` programs shelved by
name; the -2-environment ones boot through FFF0h). Host-tested through
the real ROM end to end -- menu key to Willy's title screen.

The turbo-loader wall then fell to the PCHL/SPHL emulator fix: the
factory rips loaders against real monitor images, captures the handoff
register file, and cold-verifies each extraction exactly as the shelf
boots it.

The first shipped census (34 of 90) then met real hardware, and two
rounds of bench testing taught the factory most of what it now knows
-- the full list of earned checks lives in docs/ROM-module.md.  Round
one found the sentinel-prefill corruption (479 bytes across 18 games:
MAGICIAN's crashed start key, ARKANOID's mangled sprites, BOULDER
DASH's deaf keyboard).  Round two found the incomplete multi-part
captures (BLUDISTE, CERES-01, TANK, TVARE -- first stages that stream
the rest from tape, unshippable), the dirty-RAM crashes that only
happen after a menu boot (JERRY, ONA A DUCH, MESTO -- fixed by the
stage-2 clean room), the games that index their birth monitor's key
tables (CROSFIRE, COBRA -- fixed by shipping monit1 as cargo), and
the fact that turbo loaders' painted VRAM is content the shelf was
dropping (ARKANOID's mothership, BOULDER DASH's instructions -- now
captured and shipped as a second load segment).

The standing census: **42 shelf-worthy of 90** -- 39 through the
gauntlet plus three bench-attested over its objection -- across five
volumes, including five games (GALAXIA, SPACE RAID, RESCUER, REVERZ,
HLIPA) unlocked by modeling the idle tape port as the noisy open
input it really is.

Then the volumes merged.  The **omnibus** carries all 40 shelved games
plus the diagnostics on one board -- 119 pages, 1.91 MB of the
RP2354A's 2 MB -- behind a two-level menu: alphabetical directories
plus a SYSTEM directory (BASICs, both test cards, the module scanner,
the banner), each submenu just another menu page with a BACK entry.
Pages beyond the hotspot row's 32 are named by a **two-touch
protocol**: a read of module 3FD8h+j latches a bank (no rebuild, ~20 ms
grace for the board's poll), the 3FE0h+n commit selects page j*32+n;
the latch persists across commits, and a stub that never touches the
bank row stays in bank 0 -- so every single-touch stub written before
the extension still means pages 0-31.  Verified end to end from a true
cold boot: reset vector, mirror map, the monitor initializing itself
and booting the menu, a directory, a game four bank-switches deep on
page 106, BACK, BASIC-G through SYSTEM, the scanner grading BASIC on
its relocated page.

A fifth volume carries the diagnostics: shelf editions of the screen
test card (twice, marching complementary ranges 2000-BFFF and
0000-9FFF, since a RAM-resident march cannot cover its own feet) and
the module scanner (hotspot-swaps the window to the BASIC page, grades
it live; block F stops short of the live hotspot bytes).  Confirmed on
hardware the day it was built.  The beacon RAM test and the CPU ladder
stay monitor-socket images by design -- they exist for machines too
broken to reach a menu.

Then a second archive arrived -- the pmd85emu collection's editors,
assemblers, graphics and music software plus more games -- and the
census tripled: **262 programs auditioned, 68 through the gauntlet**
(70 shipped, counting the three bench-attested).  The stubborn turbo
hold-outs fell to two rip generalizations.  *Entry candidates*: the
"+4" family's header start field is not its entry -- the loader
enters at its own first DI instruction -- so the rip tries the header
start plus the first three DI offsets in the body.  *A widened
handoff*: a loader may hand control off above 9000h (SABOTER's second
stage runs at 7189h with code parked beside the VRAM), so the rip
accepts any handoff outside the monitor's 8000h window once the tape
drains.  Together they rescued SABOTER, JETPAC, PSSST, VLAK, TETRIS+4
and LEMMINGS in one stroke.  The bench also taught the rip that
loaders write above the program body (BOULDER DASH's movement table
at BFD8h -- the robot faced every direction and moved in none until
the 9000h-BFFFh writes shipped as a third segment).

Everything no longer fits one flash, so the shelf ships as **two
images** behind the same two-level menu, each with an identical
SYSTEM directory (both BASICs, both test cards, the module scanner,
the banner, and FLASH CHECK with its own baked sum table):
`MULTILOAD-games` -- the verified games shelf under alphabetical
directories, 125 pages, 2009 KB of the 2048 -- and `MULTILOAD-apps`
-- MORE GAMES plus EDITORS, GRAPHICS, DEVELOP (with the MRS2
assembler module) and MUSIC directories, 73 pages, 1177 KB.  Each
image is
emulator-verified from a true cold boot before its firmware builds:
every directory opens and BACKs, BASIC-G boots through SYSTEM, and
FLASH CHECK sweeps every page clean.

Remaining, in rising ambition: the last turbo hold-outs (the MANIC
two-part family caps out with tape still unread; FLAPPY+4 and kin
stall at 8A3xh with the tape already empty -- a trailing-padding
variant might free them cheaply; MUSICA and DAM stop at 8BCEh with
bytes left); composite pages (a BASIC plus the module software it
loads, e.g. `wurmi`/`kli2`, which have no boot stub of their own);
more than 16 entries per menu.

## Parked

- **Upstreaming to One ROM.** A "2616 in the PMD 85-3" chip type — /CS on
  the /OE pin, an address bit on the /CE pin — is a plausible upstream
  contribution once FULL8K is proven on hardware. Parked until there is a
  measured success to point at.
- **Serving the earlier PMD 85 models** (-1/-2/-2A: 4 KB in four 1 KB
  chips, and 2708s need three rails). Only worth it if a machine shows up,
  and their schematic needs the same reading this one got.
- **A CPU-side test ROM** (8080 assembler tooling in tools/). The sibling
  project's most valuable instrument was its test ROM; this project will
  want one the first time rung 4 fails for a non-obvious reason. Not
  before.
