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

### 2. An intermittent data-path fault killed the served monitor — mechanism known, source unproven

The first symptom ever reported with the board in the socket: *a burst of
activity on power-up, then the CPU halts and /CS sticks high.* Decoded
after the fact, that trace is exact:

- The monitor ran fine from the board — through its checksum, into the
  reset path at E0A3.
- At E0AF it performs the paging dance: `MVI A,82h / OUT F7h` is an 8255
  **mode set**, which drops PC4 and takes the ROM out of the address
  space. The monitor survives this by copying its next four bytes into
  RAM first and executing them from there.
- Those bytes cross the data path **twice** — read out of ROM through the
  socket contacts during the copy, then re-fetched from RAM during the
  ROM-less window. Corruption anywhere on that path means a garbage
  opcode where the second `OUT F7h` should be, and PC4 is never set
  again.
- The machine fell into **AllRAM**: no ROM anywhere in the map, /CS
  permanently deasserted, the CPU wandering uninitialised DRAM until it
  executed a stray 76h and halted.

That mechanism is established. The *source* of the corruption is not,
and the candidates rank like this:

**Socket corrosion, cleaned by DeOxit left to dwell for a day** — the
best fit, though unproven. It explains the observed intermittency (RAM
data lines dead on one scoping, running on the next), it explains a fix
that arrived without any circuit change, and it feeds the death mechanism
directly through the socket contacts the trampoline bytes must cross.

**The MH7474 on D6/D7 — eliminated**, and the elimination is a nice
piece of reasoning worth keeping. Its D6/D7 pins are *inputs*; the
outputs feed only the video circuitry, so a fault there garbles the
screen, not the diagnostics. A dying input could only hurt the bus by
clamping it — and cutting the legs would fix that, but **re-soldering
them would bring it back**, and the machine boots with the IC fully
reconnected. A cracked joint, the other failure mode, merely disconnects
the input and *unloads* the bus. Neither branch survives the reconnect
test.

The ~2.65 Vp-p "clamp" once measured on D6/D7 stays on the books as
unattributed: possibly a marginal input that recovered, possibly an
artifact of probing a halted machine's floating bus.

If the fault ever recurs, the order of operations is: reseat and re-clean
the sockets first, and only then reach for the soldering iron.

### 3. The diagnostic images then hid the repair

The repair was almost certainly in place days before anyone could see
it. DeOxit does its work silently — the machine may have been bootable
from roughly two days before the first green lamp — but every diagnostic
image built in that window performed the same mode set the monitor does,
**without the trampoline**, and so removed itself from the address space
mid-flight. Two days of "the machine is still broken" were the
instruments dying by their own hand on a machine that was already fixed.
The same masking structure as the layer above it: each fault, real or
instrumental, hid the state of the one beneath. See the memory-map
section of PMD85-3.md for the full mechanism, and BOARD-NOTES.md for how
the too-lenient emulator let the same bug pass review twice.

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
