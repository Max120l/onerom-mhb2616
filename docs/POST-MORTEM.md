# Post-mortem: a year-dead PMD 85-3, booted

2026-08-08. The machine boots monit3B from a One ROM Fire 24 rev F in
socket DS4 (FULL8K, one flying lead to the other pair's /CS). This file
records what was actually wrong, because three separate faults were true
at once and each one masked the next — and because most of the week's
"machine faults" were instrument faults, in roughly equal parts mine and
history's.

## The three faults

### 1. The original ROMs are dead

Established before anything was served: the dumps of the machine's own
MHB 2616s are corrupt, all of them. This alone accounts for the year of
failed boots. It is the fault One ROM fixes, and the only one of the
three that was ever going to need it.

### 2. A bad joint on the video circuit's MH7474 killed the served monitor

The first symptom ever reported with the board in the socket: *a burst of
activity on power-up, then the CPU halts and /CS sticks high.* Decoded
after the fact, that trace is exact:

- The monitor ran fine from the board — through its checksum, into the
  reset path at E0A3.
- At E0AF it performs the paging dance: `MVI A,82h / OUT F7h` is an 8255
  **mode set**, which drops PC4 and takes the ROM out of the address
  space. The monitor survives this by copying its next four bytes into
  RAM first and executing them from there.
- Those two RAM-fetched instructions crossed a data bus whose D6/D7 ran
  through an MH7474 with (in hindsight) a marginal joint — observed at
  the time as a ~2.65 Vp-p clamp. Corrupted fetch, garbage opcode, PC4
  never set again.
- The machine fell into **AllRAM**: no ROM anywhere in the map, /CS
  permanently deasserted, the CPU wandering uninitialised DRAM until it
  executed a stray 76h and halted.

Cutting the IC's two legs removed the fault; re-soldering them did not
bring it back. The IC was never the problem — the joints were, and
cut-plus-resolder left two fresh ones. The board's report of "/CS went
away and stayed away" was correct both times and was filed as a symptom
for days.

### 3. The diagnostic images then hid the repair

From the moment the legs were cut, the machine was probably bootable.
Nobody could tell, because every diagnostic image built after that point
performed the same mode set the monitor does — **without the trampoline**
— and so removed itself from the address space mid-flight. Two days of
"the machine is still broken" were the instruments dying by their own
hand on a repaired machine. See the memory-map section of PMD85-3.md for
the full mechanism, and BOARD-NOTES.md for how the too-lenient emulator
let the same bug pass review twice.

## What each instrument eventually established

| instrument | verdict |
|------------|---------|
| cpu ladder, rungs 0–6 | processor, 8228 read path, all 13 address lines: sound |
| strobe stages, on the scope | /MEMR, /MEMW, /IOW all generated: 8228 fully exonerated |
| map sweep | red ×9 — correctly reported that nothing short of a mode set ends the startup mirror, misread at the time as a stuck latch; it is the 8255's power-on state |
| paging stage | trampoline survives, mirror ends, 48 KB March C− clean, VRAM clean |
| monit3B, served | **boots** |

## The residue

The machine has an intermittent history, and a warm Friday success is
evidence, not acquittal. The soak build of the RAM tester (verdict lamp,
trampoline init) exists for exactly this: run it cold, more than once,
days apart. Long green with a green blip closes the case; a red or blue
blip on a green long colour is the intermittent caught in the act.

## The lessons, in one place

Each of these has a fuller writeup in BOARD-NOTES.md or PMD85-3.md:

- **An edge is not an event** — a held select is one edge and many reads.
- **A count needs an out-of-band marker**, not a longer gap.
- **Copying a sequence out of working firmware copies its preconditions
  too** — the monitor's mode set is safe only because of the four
  instructions above it.
- **Model the machine, not the memory** — an emulator that cannot express
  a failure will pass every image that has it. The faithful model
  (`map_clear_ports=()`) now exists precisely because the lenient one
  approved two fatal inits.
- **When the instrument and the machine disagree, suspect the
  instrument** — the sibling project's founding lesson, re-learned here
  approximately five times.
