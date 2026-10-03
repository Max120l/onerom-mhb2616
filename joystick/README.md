# Bluetooth joystick adapter

An ESP32 that turns a Bluetooth gamepad into a joystick for a shelf of
machines, from one firmware: the machine is picked from a menu on a
small OLED, with a rotary encoder or two buttons, and remembered across
power cycles.  Bluepad32 brings the gamepads in (most Bluetooth pads,
Classic and BLE); a table of profiles says how each machine wants its
switch lines driven.  Adding a machine is one line in that table.

## Profiles

| Profile | Lines | Idle | Buttons |
|:--|:--|:--|:--|
| PMD 85 4004/482 | up down left right fire | driven high | A B L1 R1 X Y: fire |
| C64 / VIC-20 / C128 | up down left right fire | let go | A B L1 R1: fire; X Y: up (jump) |
| Amiga (2 buttons) | + pin 9 | let go | A B L1 R1: fire; X Y: button 2 |
| Atari 2600 / 8-bit / ST | up down left right fire | let go | A B L1 R1 X Y: fire |
| ZX Spectrum Kempston | up down left right fire | let go | A B L1 R1 X Y: fire |
| Sega Master System | + pin 9 | let go | A B L1 R1: button 1; X Y: button 2 |
| MSX | + pin 7 | let go | A B L1 R1: A; X Y: B |

Every machine here reads a joystick as a handful of switch lines; the
differences are whether an idle line must be driven high or merely let
go, which connector pins carry which switch, and what a second button
is.

- **PMD 85**: the club's JOYDEMO (`dam+2.ptp`) is the spec.  The game
  puts the GPIO 8255 (4Ch-4Fh) in mode 0 with port A as input, raises
  PC4 as the stick's supply, then polls `IN 4Ch`: bit 0 down, 1 up,
  2 right, 3 left, 4 fire, a pressed switch reading 0.  With nothing on
  the connector the port reads **low** -- every bit "pressed" (MANIC
  MINER 2's miner walks off by himself) -- so this profile drives the
  lines high when idle, push-pull.
- **Atari-style DE-9** (everything else): a pressed switch shorts its
  pin to ground; the machine's pull-ups hold it high otherwise.  On
  the C64 and the Amiga those lines are the keyboard matrix, which the
  machine itself drives on every scan, so these profiles run the lines
  **open-drain**: pulled low when pressed, let go otherwise, never
  driven high.  Pin 6 is fire; a second button is pin 9 (Amiga, Master
  System) or pin 7 (MSX).  Lines a profile does not use are left
  floating, so MSX's pin-7 button never meets Atari's pin-7 +5 V.

The profile shown on the OLED is the one driving the lines.  Picking
"PMD 85" while plugged into a C64 would drive the CIA's keyboard lines
high, which is the one thing to not do; the display is there so you
can see.

## Wiring

All ESP32 pins are chosen clear of the strapping pins and the I2C
pair.  Put a **470 Ω resistor in series with every stick line** on
every machine: the 8255 and the CIA read 3.3 V as a solid high, the
pulled-low level stays well under their thresholds, and the resistor
is what keeps both sides safe if a line is ever driven from both ends.

### Stick lines

| Line | DE-9 pin | Stick 1 | Stick 2 |
|:--|:--|:--|:--|
| up | 1 | GPIO 16 | GPIO 26 |
| down | 2 | GPIO 17 | GPIO 27 |
| left | 3 | GPIO 18 | GPIO 13 |
| right | 4 | GPIO 19 | GPIO 14 |
| fire | 6 | GPIO 23 | GPIO 33 |
| button 2, pin 9 | 9 | GPIO 25 | -- |
| button 2, pin 7 (MSX) | 7 | GPIO 32 | -- |
| ground | 8 | GND | GND |

Stick 2 has the five lines every one-button machine needs.  On the
C64 and Amiga, stick 1 is **control port 2** (the one most games read)
and stick 2 is port 1.

**DE-9 pin 7 is +5 V** on Atari-style machines and a button on the MSX.
Leave the GPIO 32 wire off unless MSX is on the menu for that cable,
and never power the ESP32 from pin 7 on a cable that carries GPIO 32.
Powering the ESP32 from pin 7 (C64, Amiga, Atari) works like the
keyboard bridge's own 5 V feed does, but those supplies are not
generous and Bluetooth draws in bursts: if the adapter resets when a
pad connects, power it from USB and leave pin 7 unconnected.  Never
both at once.

### PMD 85 cable

GPIO/0 is K3, GPIO/1 is K4 (`pmd85.borik.net`, *Konektory na PMD 85*),
20-pin connectors with the same layout: K3 carries port A, K4 port B.
**Neither carries +5 V**: power the ESP32 from USB.

| K3 / K4 pin | Signal | Stick 1 (K3) | Stick 2 (K4) |
|:--|:--|:--|:--|
| 1 | GND | GND | GND |
| 8 | DIR | GND | GND |
| 13 | PA1 / PB1 up | GPIO 16 | GPIO 26 |
| 14 | PA0 / PB0 down | GPIO 17 | GPIO 27 |
| 15 | PA3 / PB3 left | GPIO 18 | GPIO 13 |
| 16 | PA2 / PB2 right | GPIO 19 | GPIO 14 |
| 18 | PA4 / PB4 fire | GPIO 23 | GPIO 33 |

The connector's port lines pass through a bidirectional bus driver
whose direction pin (DIR, pin 8) floats to *output*; grounding it is
what turns the connector into an input, and without that strap
nothing the ESP32 does reaches the 8255.

### Display and controls

| Part | ESP32 |
|:--|:--|
| OLED SDA | GPIO 21 |
| OLED SCL | GPIO 22 |
| OLED VCC, GND | 3V3, GND |
| encoder A / next button | GPIO 4 |
| encoder B / previous button | GPIO 5 |
| encoder push / forget button | GPIO 15 |

The OLED is a 0.96" SSD1306 128x64 on I2C; for the 1.3" SH1106 kind,
swap the commented constructor line at the top of the sketch.  The
encoder and buttons go to ground; the internal pull-ups are on.  The
firmware builds in two flavours, `UI_ENCODER=1` (rotary encoder with
push button) and `UI_ENCODER=0` (three buttons: next, previous,
forget); the merged images carry the flavour in their name.

## Using it

- The display shows the profile, which pads are connected, their live
  stick as glyphs (`U D L R F`, `2` for a second button), and whether
  the lines are open-drain or push-pull.
- Turn the encoder (or press next / previous) to change the profile.
  It takes effect at once and is saved a second later ("saving" on the
  last line while it waits).
- Pairing: put the gamepad in pairing mode (the explicit pairing
  combination, not just power on).  It reconnects on its own after
  that.  Gamepad 1 is stick 1, gamepad 2 is stick 2; the player LEDs
  show which is which on pads that have them.  The on-board LED (GPIO
  2) lights while a pad is connected.
- Forget every pairing: hold the encoder button (or the forget button)
  for two seconds, or press BOOT within the first three seconds after
  a reset -- not held through the reset, which is the chip's own
  flashing-mode strap.
- Serial monitor at 115200 baud: the profile, connections, and a line
  per stick change.

## Flashing a ready-made image

`btjoy-encoder-esp32-devkit.bin` and `btjoy-buttons-esp32-devkit.bin`
(built with the commands below and merged with esptool) flash to
offset 0 of a plain ESP32 DevKit:

```
esptool.py --chip esp32 --port /dev/ttyUSB0 write_flash 0x0 btjoy-encoder-esp32-devkit.bin
```

(on Windows, `python -m esptool ... --port COM3 ...` needs no PATH).
The offset is **0x0**: the merged image begins with 4 KB of padding so
the bootloader lands at 0x1000 by itself.  Written at 0x1000 instead,
everything sits one sector high and the chip prints `invalid header:
0xffffffff` forever.  A browser flasher of the ESP Web Tools kind wants
the parts instead -- `bootloader.bin` at 0x1000, `partitions.bin` at
0x8000, `boot_app0.bin` at 0xE000, the app at 0x10000 -- which the
build directory provides.

## Building

The sketch needs the "ESP32 + Bluepad32" board package (Bluepad32
replaces the stock Bluetooth stack, so it ships its own copy of the
ESP32 core) and the U8g2 library.

Arduino IDE: add
`https://raw.githubusercontent.com/ricardoquesada/esp32-arduino-lib-builder/master/bluepad32_files/package_esp32_bluepad32_index.json`
under *Additional boards manager URLs*, install *ESP32 + Bluepad32*
from the Boards Manager and *U8g2* from the Library Manager, pick your
board under *ESP32 + Bluepad32 Arduino*, open `btjoy/btjoy.ino`.  For
the three-button flavour add `#define UI_ENCODER 0` above the
`#include` lines.  Upload.

arduino-cli:

```
export ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS=https://raw.githubusercontent.com/ricardoquesada/esp32-arduino-lib-builder/master/bluepad32_files/package_esp32_bluepad32_index.json
arduino-cli core update-index
arduino-cli core install esp32-bluepad32:esp32
arduino-cli lib install U8g2
arduino-cli compile --fqbn esp32-bluepad32:esp32:esp32 --build-property "compiler.cpp.extra_flags=-DUI_ENCODER=1" joystick/btjoy
arduino-cli compile --fqbn esp32-bluepad32:esp32:esp32 --build-property "compiler.cpp.extra_flags=-DUI_ENCODER=0" joystick/btjoy
arduino-cli upload  --fqbn esp32-bluepad32:esp32:esp32 -p /dev/ttyUSB0 joystick/btjoy
```

A plain ESP32 DevKit (ESP32-WROOM-32) is the target; the ESP32-S3 and
C3 have no Bluetooth Classic, which most gamepads need.

## Adding a machine

One line in `PROFILES[]`: a name for the display, whether idle lines
are let go (open-drain) or driven high, which lines the machine reads,
which line a second button lands on (or -1), and whether X/Y should
mean "up".  Machines whose joystick is read against a strobed common
line rather than ground (the Amstrad CPC's COM1, the MSX's pin 8 when
a game uses it) are not this kind of profile: a line pulled to ground
there presses every key in that column.  They need the common line
sensed, which is a future addition.

## Checking it

- Every profile: with no pad connected, nothing on the machine may
  move, and its keyboard must behave (a stuck line shows up as a
  phantom key on the C64).
- PMD 85: MANIC MINER 2 (the joystick edition in the preview image)
  must stand still with no pad connected; BOULDER DASH 4 reads the
  stick at its menu and in play.
- C64 / Amiga: X or Y jumps where the game jumps on up.
