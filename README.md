# Emulating a Tesla MHB 2616 on a One ROM Fire 24 rev E

Firmware that makes an RP2350-based One ROM board behave like a Tesla MHB 2616
— the Czechoslovak 2 KB PROM in the JEDEC 2716 footprint — and, from a single
socket, like all four of them at once: 8 KB instead of 2.

Target machine: **Tesla PMD 85-3**, whose 8 KB monitor lives in four 2616s.

**Status: designed and host-tested, not yet run on hardware.** The bit
scrambles, table builder and tools are covered by tests that run on any PC;
what the PMD 85-3 actually puts on the socket's control pins is an open
question with a measurement plan, not an answer. Read
[Before you plug anything in](#before-you-plug-anything-in) first. This is a
sibling of [onerom-1801re2](https://github.com/Max120l/onerom-1801re2), and it
inherits that project's habit: what has been verified is stated as fact,
everything else is stated as a question.

## Why this is not a One ROM configuration

Half of it could be. A 2616 alone in its socket is a 2716 as far as read mode
is concerned, and stock One ROM serves 2716s. If all you want is to replace
one dead chip with one board, stop reading and use One ROM with `type=2716` —
that path is maintained, tested on dozens of machines, and needs no custom
firmware. That is the [Prior art](#prior-art), and it is prior art in the
strong sense: for the 2 KB job, the purpose-built thing already exists.

The 8 KB job is different. The monitor is four chips, the socket carries
eleven address lines, and which chip answers is decided by per-socket selects
decoded on the mainboard — signals that never enter any one socket. One ROM's
multi-ROM sets solve a version of this (extra CS lines wired to X1/X2), but
they top out at three images and assume each extra line is a chip select,
active one-at-a-time. Serving four banks as one linear 8 KB ROM, with the
missing address bits brought in by wire — or switched by hotspot for software
that knows about it — is not a configuration One ROM offers. So this is
custom firmware that treats the Fire 24 as a well-documented RP2350 carrier
board with a known socket-to-GPIO map. Same conclusion as the 1801RE2
project, for a milder reason.

## The chip

The MHB 2616 is Tesla's 2048 × 8 PROM, the mask/OTP sibling of the MHB 2716
EPROM, both used interchangeably in PMD 85 monitor sockets — surveyed -3
units carry 2616s with paper labels. Read-mode pinout is JEDEC 2716:

| pin | signal | | pin | signal | | pin | signal |
|-----|--------|-|-----|--------|-|-----|--------|
| 1 | A7  | |  9 | D0  | | 17 | D7 |
| 2 | A6  | | 10 | D1  | | 18 | /CE |
| 3 | A5  | | 11 | D2  | | 19 | A10 |
| 4 | A4  | | 12 | GND | | 20 | /OE |
| 5 | A3  | | 13 | D3  | | 21 | **see below** |
| 6 | A2  | | 14 | D4  | | 22 | A9 |
| 7 | A1  | | 15 | D5  | | 23 | A8 |
| 8 | A0  | | 16 | D6  | | 24 | Vcc |

Power follows JEDEC and matches what the Fire 24 hard-wires, so the board
drops in unmodified.

**Pin 21 is the open question on this chip.** On a 2716 it is Vpp, tied to
Vcc for reading. A PROM has no Vpp in read service, and some 26xx-series
parts repurpose the pin as a second select. No primary MHB 2616 datasheet has
been located to settle it. The firmware never drives pin 21 and its role in
gating is a build option (`MHB_PIN21`), defaulting to ignored — the only
arrangement actually documented. [docs/MHB2616.md](docs/MHB2616.md) has what
is known, what is inferred, and how to settle it with a meter in five
minutes.

## What the PMD 85-3 does with them

The -3 monitor is 8 KB — twice the earlier models' — and it is the machine's
whole personality: startup test, keyboard, tape, screen, the boot into BASIC.
From MAME's driver, corroborated by the PMD 85 Infoserver:

- **At power-up the monitor is read-mirrored across the entire 64 KB address
  space**, writes falling through to RAM underneath. The 8080 starts at
  0x0000 and finds the monitor there.
- **The first I/O write clears the startup map.** After that the monitor
  reads at E000–FFFF, sharing that window with video RAM #2 writes; earlier
  models' 8000h placement does not apply.

Two consequences for a board in a socket:

**The four chips are selected, not addressed.** Each socket receives A0–A10
and its own decoded select; A11/A12 exist only on the mainboard, as inputs to
the decoder. A board serving all 8 KB must get those two bits some other way
— that is the whole design problem, and the bank sources below are its three
answers.

**Which pin carries the per-socket select is not yet established.** /CE on
pin 18 driven per-socket with /OE on pin 20 as a common read strobe is the
textbook arrangement, and the default build gates on both — correct for a
chip alone in its socket under any arrangement. But EXT mode must gate on a
signal that covers the whole 8 KB region, and whether pin 20 is that signal
is a thing to measure, not assume. The measurement is one scope session:
[docs/PMD85-3.md](docs/PMD85-3.md).

## Design

### Serving

There is no PIO program in this firmware, and that is the design. A 2616 has
no handshake: address in, data out within the access time, selects gating the
drivers. Against a 2 MHz 8080 the budget is generous — the original parts are
~450 ns chips.

The Fire 24 rev E pin map does the decoding for free. The eight data pins
land on GPIO 0–7; the eleven address lines, all three control pins and the
two X jumper pads land on GPIO 8–23. So the top 16 bits of a single GPIO read
are the complete question, and the serve loop is:

```
read GPIO  ->  lut[(gpio >> 8) & 0xFFFF]  ->  write data, gate directions
```

The 64 KB table is built at boot: address bits gathered through the socket
scramble, data pre-scrambled into drive order, control bits repeated across
their combinations. Selection is a mask compare against the same GPIO word,
and the data drivers are enabled only while it passes. The loop runs from RAM
on core 1 in a handful of instructions — comfortably an order of magnitude
inside the timing of the chip it replaces.

### The bank sources

Where the two missing address bits come from. One firmware, three answers,
chosen at build time:

| mode | wires | what you get |
|------|-------|--------------|
| `EXT` | 2 | The real thing: A11/A12 arrive on the X pads, the board serves the full 8 KB linearly, live — the stock monitor from one socket. The other three chips must come out. |
| `HOTSPOT` | 0 | Reads of four magic addresses in the window switch banks, cartridge style. Zero modification — but only software written (or patched) to touch the hotspots can steer it. **The stock monitor never will.** |
| `STATIC` | 0 | One 2 KB bank, picked by jumpers at boot. A drop-in replacement for any single dead 2616 — four spare chips in one board. |

The honest statement about HOTSPOT, since it is the default: it exists for
custom ROM software, test ROMs and development, where its zero-wire property
is genuinely valuable. It cannot serve the stock monitor, because banks are
switched by reads the stock monitor does not perform. For the stock monitor
the machine itself broadcasts the bank on A11/A12 — it just doesn't reach
the socket, and two flying leads (`EXT`) are the honest price of fetching it.

In `EXT` mode a bank can also be *absent*: the board then never drives the
bus for that bank's select states, so a partial set can coexist with real
chips still in their sockets — replace two dead 2616s of four, keep the
living ones.

### Safety properties

Carried over from the sibling project, because they were earned there:

- **Recovery jumper.** This firmware replaces One ROM's picoboot and the
  board has no BOOTSEL button. Jumper 1 fitted at power-on hands straight to
  the bootrom's USB mode before a single socket pin is touched.
- **Nothing drives until selected.** Data pins power up as inputs and are
  enabled only while the select compare passes, data written before
  direction. Pin 21 and the X pads are never driven under any configuration.
- **Refusing to fight the bus.** Absent banks in EXT mode are never driven;
  the CMake build warns when a gating choice would answer addresses it
  shouldn't (or everything, which is only sane on a bench).

## Before you plug anything in

1. **Establish pin 21.** Board out, meter from socket pin 21 to Vcc and to
   GND. Tied to Vcc: the 2716 arrangement, build with `MHB_PIN21=IGNORE`
   (the default). Anything else: [docs/MHB2616.md](docs/MHB2616.md) before
   going further.
2. **Flash the selftest image first** (`gen_rom_images.py --selftest`), read
   the board back through a ROM reader or dump it in-circuit, and run
   `check_selftest.py` on the result. It names the failing address or data
   line rather than merely failing. Only then flash a monitor image.
3. **Every original the board answers for must come out.** Two devices
   driving the same data lines is a bus fight. For `STATIC` that is one
   chip; for `EXT` it is all four.
4. **For `EXT`, settle the gating question** with the scope session in
   [docs/PMD85-3.md](docs/PMD85-3.md) before trusting the two flying leads.

## Building

```sh
# images: one 8 KB monitor dump, or up to four 2 KB chip dumps
cd tools
./gen_rom_images.py -o ../firmware/rom_images.c --monitor pmd85-3.bin

# firmware (needs the Pico SDK; PICO_SDK_PATH set)
cd ../firmware
cmake -B build -DMHB_BANK_SOURCE=EXT -DMHB_GATE_CE=OFF   # stock monitor, 2 wires
cmake --build build
# -> build/mhb2616.uf2, flashed over USB with the recovery jumper fitted
```

The host tests need no SDK and no hardware:

```sh
cd test && make check
```

## Prior art

- **[One ROM](https://github.com/piersfinlayson/one-rom)** — for a single 2 KB
  replacement, use it instead of this; `type=2716` is a supported
  configuration and this board is its own hardware. This project exists for
  the 8 KB shape One ROM does not offer.
- **[8kB ROM pre PMD 85](https://pmd85.borik.net/wiki/Blog:8kB_ROM_pre_PMD_85)**
  (PMD 85 Infoserver) — the community's own answer to a related question:
  replacing the earlier models' four 1 KB 2708s with one 2764, jumper-placed
  in the address space, with select signals brought over by wire. Different
  target, same lesson: the missing address bits come in on wires, there is
  no way around it for stock software.
- **[onerom-1801re2](https://github.com/Max120l/onerom-1801re2)** — the
  sibling project this one's structure, board mapping and habits are lifted
  from. It fought a much harder bus (multiplexed, handshaken, inverted) and
  its README is the reference for what the hardware bring-up of this one
  should look like.

## Sources

- MAME `src/mame/tesla/pmd85.cpp` — the -3 memory map, startup mirror
  behaviour, 8 KB monitor region.
- [PMD 85 Infoserver](https://pmd85.borik.net/wiki/PMD_85_ROM) — ROM
  arrangements per model; 2616s observed in -3 units.
- [Wikipedia: PMD 85](https://en.wikipedia.org/wiki/PMD_85) — model history,
  chip variants used across the family.
- One ROM `rust/config/json/fire-24-e.json` — the socket-to-GPIO map,
  verified on hardware by the sibling project.

## Layout

```
firmware/   board header, decode (host-testable), serve loop, CMake
tools/      image generation, selftest pattern + checker, checksums
test/       host tests: cc for the C, pytest for the tools
docs/       the chip, the machine, the roadmap
```
