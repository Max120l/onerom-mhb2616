# Emulating a Tesla MHB 2616 on a One ROM Fire 24

Firmware that makes an RP2350-based One ROM board behave like a Tesla MHB
2616 — the Czechoslovak 2 KB PROM in the 2716 footprint — and, from a single
socket, like up to all four of them at once: the full 8 KB of a PMD 85-3
monitor.

Target machine: **Tesla PMD 85-3**, whose 8 KB monitor lives in four 2616s.

**Status: serving verified on hardware; not yet run in the machine.** A
rev F board flashed with the selftest image and read back through a 2716
programmer returns every bank byte-perfect — all eleven address lines,
all eight data lines, both bit scrambles and the /CS gating confirmed on
real silicon. The socket wiring is read directly off the PMD 85-3 CPU
board schematic (DOSKA CPU, 1 PK 280 77), and it reshaped the firmware in
the machine's favour:
**two chips replaceable with zero wires, all four with one**. The
bank-to-socket assignment is settled byte-for-byte from the Infoserver ROM
archive (DS4 "E" = bank 0 … DS7 "B" = bank 3; full table with build
switches in [docs/PMD85-3.md](docs/PMD85-3.md)). Next stop is the
machine. This is a sibling
of [onerom-1801re2](https://github.com/Max120l/onerom-1801re2) and inherits
its habit: what has been verified is stated as fact, everything else as a
question.

## Why this is not a One ROM configuration

For one dead chip in isolation it nearly could be — stock One ROM serves
2716s, and a 2616 alone in its socket mostly reads like one. But the
PMD 85-3 does not wire its sockets the JEDEC way, and the difference is the
whole project:

- **/CS sits on pin 20** (a 2716's /OE) and selects a *pair* of chips:
  DS4+DS5 share one select net, DS6+DS7 the other.
- **PR sits on pin 18** (a 2716's /CE) and carries **A11** — straight into
  DS4/DS6, inverted into DS5/DS7. An address bit delivered as a select.
- **Pin 21** (a 2716's Vpp) is strapped to +5 V and carries nothing.

So each socket already receives twelve address bits' worth of information:
A0–A10 on the address pins and A11 on PR. One ROM's 2716 type has no notion
of an address bit arriving on the /CE pin, and its multi-ROM sets assume
extra selects are one-chip-at-a-time chip selects. Serving the pair — let
alone all four banks — is custom firmware. Same conclusion as the 1801RE2
project, for a milder reason: the Fire 24 is treated as a well-documented
RP2350 carrier board with a known socket-to-GPIO map.

The chip itself, what the schematic settled about its pins, and the caveats
that remain: [docs/MHB2616.md](docs/MHB2616.md).

## What the PMD 85-3 does with them

The -3 monitor is 8 KB — the machine's whole personality: startup test,
keyboard, tape, screen, boot into BASIC. From MAME's driver: at power-up the
monitor is read-mirrored across the entire 64 KB map (writes fall through to
RAM), and the first write to the system 8255 clears that, leaving the monitor
at E000–FFFF. There is a third map too — **AllRAM**, in which the ROM is not
in the machine at all — and landing in it by accident is easy: `OUT F7h` with
bit 7 set is an 8255 mode set, and a mode set drops the PC4 latch that holds
the ROM in the map. Read the memory-map section before writing an image that
touches that port.
The mirrors are the decoder's business, not the sockets': a board just
answers its selects. Details and the full maps:
[docs/PMD85-3.md](docs/PMD85-3.md).

The four chips split the 8 KB by A12 (the pair /CS nets) and A11 (PR),
and the physical assignment is settled byte-for-byte against the
Infoserver ROM archive's per-chip files:

| bank | monitor offset | socket | letter | pair /CS | A11 |
|------|----------------|--------|--------|----------|-----|
| 0 | 0x0000–0x07FF | DS4 | E | DS4+DS5 | 0 |
| 1 | 0x0800–0x0FFF | DS5 | D | DS4+DS5 | 1 |
| 2 | 0x1000–0x17FF | DS6 | C | DS6+DS7 | 0 |
| 3 | 0x1800–0x1FFF | DS7 | B | DS6+DS7 | 1 |

For a -3B machine the image to serve is `monit3B.rom` (it differs from
plain `monit3.rom` in banks 0 and 3 only); reference CRCs for both live
in [docs/PMD85-3.md](docs/PMD85-3.md).

## Design

### Serving

Serving uses no PIO, and that is the design. A 2616 has no handshake:
address in, data out within the access time, selects gating the drivers.
Against a 2 MHz 8080 the budget is generous — the original parts are
~450 ns chips. (The only PIO in the firmware is cosmetic: rev F's status
pixel speaks WS2812B.)

The Fire 24 pin map — identical on rev E and rev F — does the decoding
for free. The eight data pins land on GPIO 0–7; the eleven address lines,
both select pins and the two X jumper pads land on GPIO 8–23. So the top 16 bits of a single GPIO read are
the complete question, and the serve loop is:

```
read GPIO  ->  table[(gpio >> 8) & 0xFFFF]  ->  write data, gate directions
```

Better still, every input to the *drive decision* — which select is active,
what PR says, whether that bank is even ours to serve — lives inside those
same 16 bits. So for the chip-shaped modes the whole decision is baked into
the table at boot: 16-bit entries, low byte the pre-scrambled data, bit 8
"drive or stay silent". The loop computes nothing; it looks up, writes, and
gates. Runs from RAM on core 1, an order of magnitude inside the timing of
the chip it replaces.

### The bank sources

One firmware, four answers to "where do the missing address bits come
from", chosen at build time:

| mode | wires | replaces | how |
|------|-------|----------|-----|
| `STATIC` (default) | 0 | one chip | Gated exactly as the original: /CS active and PR at this chip's level. Bank set at build time. |
| `PAIR` | 0 | two chips | /CS gates, PR *is* the bank bit. The pair-mate must come out. |
| `FULL8K` | 1 | all four | One flying lead: the other pair's /CS (pin 20 of either empty socket) to the X1 pad. A12 = which select is active, A11 = PR. All three others out. |
| `HOTSPOT` | 0 | one chip, four images | Reads of four magic addresses switch images — for software written to touch them. **The stock monitor never will**; this mode is for custom/diagnostic ROMs. |

`STATIC` is the default because it is the only mode that is safe no matter
what else is still in its socket's neighbourhood — it drives exactly when
the chip it replaced would have. The X1 pad is pulled up, so a FULL8K build
with a detached lead degrades to serving its own pair rather than serving
wrong data.

In `FULL8K` a bank can also be *absent*: the board then never drives the
bus for that bank's select states, so a partial set coexists with real
chips still in their sockets — replace two dead 2616s of four, keep the
living ones.

### Safety properties

Carried over from the sibling project, because they were earned there:

- **Recovery jumper.** This firmware replaces One ROM's picoboot and the
  board has no BOOTSEL button. Jumper A fitted at power-on hands straight
  to the bootrom's USB mode before a single socket pin is touched.
- **Nothing drives until selected.** Data pins power up as inputs and are
  enabled only while the (table-baked or mask-compared) select decision
  passes, data written before direction. Pin 21 and the X pads are never
  driven under any configuration.
- **Refusing to fight the bus.** Absent banks are never driven; the
  programmer-read convenience (`MHB_STATIC_IGNORE_PR`) is a separate
  explicit flag with a warning, because in a machine it would fight the
  pair-mate.

## Before you plug anything in

1. **Take the build switches from the socket table**
   ([docs/PMD85-3.md](docs/PMD85-3.md)): `MHB_SOCKET_PAIR` /
   `MHB_PR_INVERT` / `MHB_SOCKET_BANK` per socket, settled byte-for-byte
   against the Infoserver archive. (For a machine with readable originals,
   `tools/rom_checksum.py --identify` cross-checks a dump against the
   table — worth doing once if any chip will stay in service.)
2. **Flash the selftest image first** (`gen_rom_images.py --selftest`),
   read the board through a 2716 programmer (build with
   `-DMHB_STATIC_IGNORE_PR=ON` and `-DMHB_SOCKET_BANK=<n>`, never in a
   machine), and run `check_selftest.py` on the dump — it names the
   failing address or data line rather than merely failing. Only then
   flash a monitor image. **Done on rev F for all four banks: clean.**
3. **Every original the board answers for must come out.** One chip for
   `STATIC`/`HOTSPOT`, the pair for `PAIR`, all four for `FULL8K`.
4. **Climb the ladder in order** — STATIC before PAIR before FULL8K, per
   the bring-up ladder in [docs/PMD85-3.md](docs/PMD85-3.md). Each rung
   isolates one new behaviour, so a failure names its suspect.

## Building

```sh
# images: one 8 KB monitor dump, or up to four 2 KB chip dumps
cd tools
./gen_rom_images.py -o ../firmware/rom_images.c --monitor pmd85-3.bin

# firmware (needs the Pico SDK; PICO_SDK_PATH set)
cd ../firmware
cmake -B build -DMHB_BOARD=FIRE24F \
      -DMHB_BANK_SOURCE=FULL8K -DMHB_SOCKET_PAIR=0 \
      -DMHB_PR_INVERT=OFF          # per the socket table
cmake --build build
# -> build/mhb2616.uf2, flashed over USB with the recovery jumper fitted
```

`MHB_BOARD` selects the Fire 24 revision: `FIRE24E` (plain LED, the
default) or `FIRE24F` (WS2812B status pixel). The socket-to-GPIO map is
identical between them — the serving design does not change. The jumpers
are addressed by the letters printed on the board's underside, which are
the same on both revisions even though the GPIOs behind them differ
(verified at PCB-netlist level; the board headers carry the full table):

- **Jumper A = recovery**, and it is the only jumper this firmware reads.
  Fit it and power on to reach the bootrom's USB flash mode.
- **B, C, D are unused.** C and D cannot be read at all — they are the
  board's BOOT/RUN + SWD pads doing double duty, and the SWD pins' own
  internal pulls defeat the detection (confirmed on hardware, see
  [docs/BOARD-NOTES.md](docs/BOARD-NOTES.md)). That leaves too few
  jumpers for a bank number, so **the bank is a build-time constant**
  (`-DMHB_SOCKET_BANK=0..3`).
- **X1** is the FULL8K flying-lead pad; X2 is unused.

The rev F pixel speaks in colours: blue blip at power-on (firmware
alive), green (serving), faint red (powered but nothing selecting us —
distinguishable from a dead board, which a plain LED could not offer).

The host tests need no SDK and no hardware:

```sh
cd test && make check
```

## Prior art

- **[One ROM](https://github.com/piersfinlayson/one-rom)** — the board this
  runs on, and the right answer for machines that wire 2716s the JEDEC way.
  This project exists because the PMD 85-3 does not.
- **[8kB ROM pre PMD 85](https://pmd85.borik.net/wiki/Blog:8kB_ROM_pre_PMD_85)**
  (PMD 85 Infoserver) — the community's conversion of the earlier models'
  four 2708s to one 2764, select signals brought over by wire. Same lesson,
  earlier machine.
- **[onerom-1801re2](https://github.com/Max120l/onerom-1801re2)** — the
  sibling project this one's structure, board mapping and habits are lifted
  from. It fought a much harder bus; its README is the reference for what
  this project's hardware bring-up should look like.

## Sources

- **PMD 85-3 CPU board schematic** (DOSKA CPU, 1 PK 280 77) — the socket
  wiring: pair /CS nets, PR ← A11 (± inversion), pin 21 strapping. The
  document that turned this project's hardest question into a table.
- MAME `src/mame/tesla/pmd85.cpp` — the -3 memory map, startup mirror
  behaviour, 8 KB monitor region.
- [PMD 85 Infoserver](https://pmd85.borik.net/wiki/PMD_85_ROM) — ROM
  arrangements per model; 2616s observed in -3 units.
- **Infoserver ROM archive** (`PMD85-rom-files`, RM-TEAM, via the
  [ROM page](https://pmd85.borik.net/wiki/ROM)) — `monit3B.rom` and the
  per-chip B/C/D/E `.bin` files whose byte-for-byte placement settled the
  socket-to-bank assignment.
- One ROM `rust/config/json/fire-24-e.json` — the socket-to-GPIO map,
  verified on hardware by the sibling project.

## Layout

```
firmware/   board header, decode (host-testable), serve loops, CMake
tools/      image generation, selftest pattern + checker, checksums
test/       host tests: cc for the C, pytest for the tools
docs/       the chip, the machine, the roadmap
```
