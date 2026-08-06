# Roadmap

In order, each step gated by the question it answers. Nothing below a gate
is worth building until the gate opens — the sibling project wrote this rule
down after learning it the slow way.

## 1. Pin 21 and the gating arrangement — *measurement, no code*

**Question: what does the PMD 85-3 put on socket pins 18, 20 and 21?**

Meter and scope session per docs/MHB2616.md and docs/PMD85-3.md question 1.
Everything else keys off this: the default build is correct only under the
textbook arrangement, and EXT's gate choice is this measurement verbatim.
Update the docs from measured fact; delete the inference language.

## 2. Bench selftest through a ROM reader

**Question: do the scrambles on real silicon match the ones the host tests
prove?**

STATIC mode, selftest image, all four jumper settings, read as a 2716 in any
programmer. `check_selftest.py` clean × 4 opens the gate. A failure here is
a mapping bug and cheap to find; the same failure discovered in-circuit
would be expensive.

## 3. STATIC replacement in a running machine

**Question: does the machine accept the board as one of its four chips?**

The first hardware milestone with user value: a 2616 replacement, dead chip
out, board in, jumpers set. The -3's own startup ROM test is the acceptance
test.

## 4. EXT: the full 8 KB from one socket

**Question: do two flying leads and the measured gate serve the stock
monitor?**

The project's headline. Selftest in-circuit first, then the monitor image.
ROM test pass + boot to BASIC closes it.

## 5. HOTSPOT: banked software beyond 8 KB — *after the above*

The hotspot machinery is in the firmware and host-tested, but it has no
consumer until there is software written for it. Candidates, in rising
ambition: a test/diagnostic ROM for this machine in the make_ramtest.py
tradition of the sibling project (the startup mirror map means code at the
reset vector is ours in any bank); a menu ROM that hotspot-switches between
four monitor variants (-3 stock, patched, diagnostic) without reflashing.
Design constraint recorded now: hotspots default to 0x7F4–0x7F7 of the
window, so hotspot-aware images must keep those four bytes free.

## Parked

- **Upstreaming to One ROM.** A "271x with external bank bits" type is a
  plausible upstream feature request once EXT is proven on hardware;
  hotspot banking of a 2716 likewise. Parked until there is a measured
  success to point at.
- **Serving the earlier PMD 85 models** (-1/-2/-2A: 4 KB in four 1 KB
  chips at 8000h, mirrored at A000h). Same firmware shape, different bank
  count and socket pinout (2708s: three rails). Only worth it if a machine
  shows up.
- **A CPU-side test ROM** (8080 assembler tooling in tools/). The sibling
  project's most valuable instrument was its test ROM; this project will
  want one the first time rung 3 fails for a non-obvious reason. Not
  before.
