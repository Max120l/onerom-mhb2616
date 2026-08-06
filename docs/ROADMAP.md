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

A rev F board, selftest image, read back through a 2716 programmer, all
four banks: `check_selftest.py` clean on every one — every address line,
every data line, both bit scrambles and the /CS gating confirmed against
real hardware. Nothing on the serving path is now unverified outside a
machine.

The run also found a board-level fault that had nothing to do with
serving: two of the four jumpers cannot be read at all, because they are
the BOOT/RUN + SWD pads doing double duty and the SWD pins' own internal
pulls defeat the detection. The bank is a build-time constant now; see
docs/BOARD-NOTES.md for the netlist evidence and the hardware symptom.

## 4. STATIC replacement in a running machine

**Question: does the machine accept the board as one of its four chips?**

The first hardware milestone with user value: a 2616 replacement, dead chip
out, board in, bank per step 2. The -3's own startup ROM test is the
acceptance test.

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
