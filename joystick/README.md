# Bluetooth joystick adapter: PMD 85 and C64

An ESP32 that turns a Bluetooth gamepad into a joystick, from one
sketch (`btjoy/btjoy.ino`) with two build targets:

- **PMD 85**: a 4004/482 club stick on the GPIO connector -- the stick
  the "+4" games (BOULDER DASH 4, FLAPPY 4, FRED, MANIC MINER 2,
  PENETRATOR, PAMPUCH ...) were written for.
- **C64**: a stick on control port 2 (and a second one on port 1).

No configuration page: pair a gamepad, plug the ESP32 in, play.  The
gamepad side, the pairing and the pins are the same on both; what
differs is how the lines are driven, and that difference matters.

## Gamepad mapping

- D-pad, or the left stick past about 40 % travel: the four directions.
  Opposite directions at once are dropped (a stick cannot do that).
- PMD 85: A, B, X, Y, L1, R1 are fire.  C64: A, B, L1, R1 are fire and
  X, Y are *up* -- C64 games jump on up, and a button spares the thumb.
- Gamepad 1 is stick 1, gamepad 2 is stick 2; the player LEDs show
  which is which on pads that have them.
- The on-board LED (GPIO 2) lights while a gamepad is connected.

Bluepad32 handles the pairing: put the gamepad in pairing mode (the
explicit pairing combination, not just power on) and it connects; it
reconnects on its own afterwards.  To forget every pairing, reset the
ESP32 and press BOOT within the first three seconds -- not held through
the reset, which is the chip's own flashing-mode strap.  The serial
monitor at 115200 baud shows connections and a line per stick change.

The ESP32 pins are the same for both targets, chosen clear of the
strapping pins (0, 2, 5, 12, 15) and all below GPIO 32, so the sketch
sets a whole stick in one register write -- the machine never samples
a half-updated direction.

| Stick 1 | ESP32 | Stick 2 | ESP32 |
|:--|:--|:--|:--|
| up | GPIO 17 | up | GPIO 23 |
| down | GPIO 16 | down | GPIO 22 |
| left | GPIO 19 | left | GPIO 26 |
| right | GPIO 18 | right | GPIO 25 |
| fire | GPIO 21 | fire | GPIO 27 |

Put a **470 Ω resistor in series with each of the ten lines** on either
machine.

## PMD 85

### The interface

The club's own JOYDEMO source (on `dam+2.ptp`) is the spec.  The game
puts the GPIO 8255 (ports 4Ch-4Fh) in mode 0 with port A as input
(`OUT 4Fh,92h`), raises PC4 as the stick's supply (`OUT 4Eh,10h`;
`11h` raises PC0 as well, for a second stick on port B), then polls
`IN 4Ch`.  "Bity jsou negované": bit 0 down, bit 1 up, bit 2 right,
bit 3 left, bit 4 fire, a pressed switch reading 0; a second stick is
`IN 4Dh`.  The club's "482. STICK" program on the same tape draws the
original: five switches, a transistor on the fire button, and the
connector list below -- which is why the adapter follows that list
pin for pin.

With nothing on the connector the port reads **low** on this machine,
every bit "pressed" (MANIC MINER 2's miner walks off by himself).  A
real stick pulls the lines up through PC4; this build drives them
high when idle and low when pressed, push-pull, so an unpaired gamepad
is a stick nobody is touching.

### Wiring

GPIO/0 is K3, GPIO/1 is K4 (`pmd85.borik.net`, *Konektory na PMD 85*).
Both are 20-pin connectors with the same layout; K3 carries port A and
PC4-PC7, K4 carries port B and PC0-PC3.  **Neither carries +5 V**: the
ESP32 is powered from its USB socket (a phone charger or a power
bank).  Share ground.

Stick 1 (gamepad 1), on **K3 / GPIO/0**:

| Signal | K3 pin | ESP32 | Direction |
|:--|:--|:--|:--|
| GND | 1 | GND | -- |
| DIR | 8 | GND | bus driver to *input* -- a must |
| PA0 | 14 | GPIO 16 | down |
| PA1 | 13 | GPIO 17 | up |
| PA2 | 16 | GPIO 18 | right |
| PA3 | 15 | GPIO 19 | left |
| PA4 | 18 | GPIO 21 | fire |
| PC4 | 12 | GPIO 34 | optional, see *Following the enable* |

Stick 2 (gamepad 2), on **K4 / GPIO/1**:

| Signal | K4 pin | ESP32 | Direction |
|:--|:--|:--|:--|
| GND | 1 | GND | -- |
| DIR | 8 | GND | bus driver to *input* -- a must |
| PB0 | 14 | GPIO 22 | down |
| PB1 | 13 | GPIO 23 | up |
| PB2 | 16 | GPIO 25 | right |
| PB3 | 15 | GPIO 26 | left |
| PB4 | 18 | GPIO 27 | fire |
| PC0 | 12 | GPIO 35 | optional, see *Following the enable* |

The 8255 reads 3.3 V as a solid high, and the series resistor is what
keeps both sides safe if a program ever sets the port to output while
the ESP32 is driving it.  The connector's port lines pass through a
bidirectional bus driver whose direction pin (DIR, pin 8) floats to
*output*; grounding it is what turns the connector into an input, and
without that strap nothing the ESP32 does reaches the 8255.  GPIO 34
and 35 are input-only pins, which suits the enable sense.

### Following the enable (optional)

A real stick only pulls its lines up while the program has raised PC4
(or PC0 for stick 2).  With `FOLLOW_ENABLE 1` in the sketch and the
pin-12 wire fitted (through a **10 kΩ series resistor**, since it is a
5 V signal into a 3.3 V input), the adapter drives its lines only
while that bit is high and floats them otherwise, so a printer or
another gadget on the port is never fought.  It is off by default:
not every +4 game is known to raise the bit, and a line that is never
driven is a stick that never works.  Leave the wire off unless you
turn it on.

### Checking it

The test is a game.  BOULDER DASH 4 in the preview image reads the
stick at its menu and in play, and its mask table at 0970h is how the
bit order above was confirmed in the emulator.  MANIC MINER 2 (the
joystick edition, not the keyboard one in the main image) is the
sensitive case: with the adapter on and no gamepad paired the miner
must stand still.

## C64

### The interface

The control ports are DE-9: pin 1 up, 2 down, 3 left, 4 right, 6 fire,
7 +5 V, 8 GND (5 and 9 are the paddle pots, unused).  A pressed switch
shorts its line to ground; the CIA's pull-ups hold it high otherwise.

Those lines are not a dedicated input: port 1 sits on the keyboard
matrix rows and port 2 on its columns, and the machine drives the
columns low itself, line by line, on every keyboard scan.  So on this
target the five lines are **open-drain**: pulled low when pressed, let
go otherwise, never driven high.  An adapter that drove them high
would fight the keyboard scan and stand a good chance of hurting the
CIA.

### Wiring

Stick 1 (gamepad 1) is **control port 2**, the one most games read;
stick 2 is control port 1.  A DE-9 *male* plug on the adapter side for
each.

| DE-9 pin | Signal | Stick 1 (port 2) | Stick 2 (port 1) |
|:--|:--|:--|:--|
| 1 | up | GPIO 17 | GPIO 23 |
| 2 | down | GPIO 16 | GPIO 22 |
| 3 | left | GPIO 19 | GPIO 26 |
| 4 | right | GPIO 18 | GPIO 25 |
| 6 | fire | GPIO 21 | GPIO 27 |
| 7 | +5 V | ESP32 5V / VIN (see below) | -- |
| 8 | GND | GND | GND |

Series resistors as above; the pulled-low level stays well under the
CIA's input threshold through 470 Ω, and the resistor is what the
ESP32 pins see 5 V through when the lines idle high.

**Power**: pin 7 carries the machine's 5 V.  It will run the ESP32
(the keyboard bridge's own 5 V feed is the same idea), but the C64's
supply is not generous and Bluetooth draws in bursts: if the adapter
resets when a pad connects, power it from USB instead and leave pin 7
unconnected.  Never both at once.

### Checking it

Any game that moves on port 2.  With the gamepad off, nothing may
move and the keyboard must behave -- a line stuck low shows up as a
phantom key.  Then pair and play; X or Y jumps where the game jumps on
up.

## Flashing a ready-made image

`pmd85_joy-esp32-devkit.bin` and `c64_joy-esp32-devkit.bin` (built
with the commands below and merged with esptool) flash to offset 0 of
a plain ESP32 DevKit:

```
esptool.py --chip esp32 --port /dev/ttyUSB0 write_flash 0x0 pmd85_joy-esp32-devkit.bin
```

(on Windows, `python -m esptool ... --port COM3 ...` needs no PATH).
The offset is **0x0**: the merged image begins with 4 KB of padding so
the bootloader lands at 0x1000 by itself.  Written at 0x1000 instead,
everything sits one sector high and the chip prints `invalid header:
0xffffffff` forever.  A browser flasher of the ESP Web Tools kind wants
the parts instead -- `bootloader.bin` at 0x1000, `partitions.bin` at
0x8000, `boot_app0.bin` at 0xE000, the app at 0x10000 -- which the
build directory provides.

The serial monitor shows the target and Bluepad32 version a second
after reset, then "gamepad 1 connected" on pairing and a line per
stick change.

## Building

The sketch needs the "ESP32 + Bluepad32" board package -- Bluepad32
replaces the stock Bluetooth stack, so it ships its own copy of the
ESP32 core.  The target is a compile-time define, `TARGET_PMD85` or
`TARGET_C64`; with neither, the PMD 85 is built.

Arduino IDE: add
`https://raw.githubusercontent.com/ricardoquesada/esp32-arduino-lib-builder/master/bluepad32_files/package_esp32_bluepad32_index.json`
under *Additional boards manager URLs*, install *ESP32 + Bluepad32*
from the Boards Manager, pick your board under *ESP32 + Bluepad32
Arduino*, open `btjoy/btjoy.ino`.  For the C64, add `#define
TARGET_C64` above the `#include` line.  Upload.

arduino-cli:

```
export ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS=https://raw.githubusercontent.com/ricardoquesada/esp32-arduino-lib-builder/master/bluepad32_files/package_esp32_bluepad32_index.json
arduino-cli core update-index
arduino-cli core install esp32-bluepad32:esp32
arduino-cli compile --fqbn esp32-bluepad32:esp32:esp32 --build-property "compiler.cpp.extra_flags=-DTARGET_PMD85" joystick/btjoy
arduino-cli compile --fqbn esp32-bluepad32:esp32:esp32 --build-property "compiler.cpp.extra_flags=-DTARGET_C64"   joystick/btjoy
arduino-cli upload  --fqbn esp32-bluepad32:esp32:esp32 -p /dev/ttyUSB0 joystick/btjoy
```

A plain ESP32 DevKit (ESP32-WROOM-32) is the target; the ESP32-S3 and
C3 have no Bluetooth Classic, which most gamepads need.
