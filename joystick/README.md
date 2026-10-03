# PMD 85 Bluetooth joystick adapter

An ESP32 that turns a Bluetooth gamepad into a 4004/482 club joystick
on the PMD 85's GPIO connector -- the stick the "+4" games (BOULDER
DASH 4, FLAPPY 4, FRED, MANIC MINER 2, PENETRATOR, PAMPUCH ...) were
written for.  One sketch, no configuration page: pair a gamepad, plug
the ESP32 into GPIO/0, play.

## The interface

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
real stick pulls the lines up through PC4; this adapter drives them
high when idle and low when pressed, so an unpaired gamepad is a
stick nobody is touching.

## Wiring

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

Put a **470 Ω resistor in series with each of the five stick lines**.
The 8255 reads 3.3 V as a solid high, and the resistor is what keeps
both sides safe if a program ever sets the port to output while the
ESP32 is driving it.  The connector's port lines pass through a
bidirectional bus driver whose direction pin (DIR, pin 8) floats to
*output*; grounding it is what turns the connector into an input, and
without that strap nothing the ESP32 does reaches the 8255.

The ESP32 pins are chosen clear of the strapping pins (0, 2, 5, 12,
15) and all below GPIO 32, so the sketch sets a whole stick in one
register write -- the machine never samples a half-updated direction.
GPIO 34 and 35 are input-only pins, which suits the enable sense.

## Following the enable (optional)

A real stick only pulls its lines up while the program has raised PC4
(or PC0 for stick 2).  With `FOLLOW_ENABLE 1` in the sketch and the
pin-12 wire fitted (through a **10 kΩ series resistor**, since it is a
5 V signal into a 3.3 V input), the adapter drives its lines only
while that bit is high and floats them otherwise, so a printer or
another gadget on the port is never fought.  It is off by default:
not every +4 game is known to raise the bit, and a line that is never
driven is a stick that never works.  Leave the wire off unless you
turn it on.

## Gamepad mapping

- D-pad, or the left stick past about 40 % travel: the four directions.
  Opposite directions at once are dropped (a stick cannot do that).
- A, B, X, Y, L1, R1: fire.
- Gamepad 1 is stick 1, gamepad 2 is stick 2; the player LEDs show
  which is which on pads that have them.
- The on-board LED (GPIO 2) lights while a gamepad is connected.

Bluepad32 handles the pairing: put the gamepad in pairing mode and it
connects; it reconnects on its own afterwards.  To forget every
pairing, reset the ESP32 and press BOOT within the first three seconds
(not held through the reset: that is the chip's flashing-mode strap).  The serial
monitor at 115200 baud shows connections and every stick change.

## Flashing a ready-made image

`pmd85_joy-esp32-devkit.bin` (built with the commands below and merged
with esptool) flashes to offset 0 of a plain ESP32 DevKit:

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

The serial monitor shows the Bluepad32 banner a second after reset,
then "gamepad 1 connected" on pairing and a line per stick change.

## Building

The sketch needs the "ESP32 + Bluepad32" board package -- Bluepad32
replaces the stock Bluetooth stack, so it ships its own copy of the
ESP32 core.

Arduino IDE: add
`https://raw.githubusercontent.com/ricardoquesada/esp32-arduino-lib-builder/master/bluepad32_files/package_esp32_bluepad32_index.json`
under *Additional boards manager URLs*, install *ESP32 + Bluepad32*
from the Boards Manager, pick your board under *ESP32 + Bluepad32
Arduino*, open `pmd85_joy/pmd85_joy.ino`, upload.

arduino-cli:

```
export ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS=https://raw.githubusercontent.com/ricardoquesada/esp32-arduino-lib-builder/master/bluepad32_files/package_esp32_bluepad32_index.json
arduino-cli core update-index
arduino-cli core install esp32-bluepad32:esp32
arduino-cli compile --fqbn esp32-bluepad32:esp32:esp32 joystick/pmd85_joy
arduino-cli upload  --fqbn esp32-bluepad32:esp32:esp32 -p /dev/ttyUSB0 joystick/pmd85_joy
```

A plain ESP32 DevKit (ESP32-WROOM-32) is the target; the ESP32-S3 and
C3 have no Bluetooth Classic, which most gamepads need.

## Checking it

The test is a game.  BOULDER DASH 4 in the preview image reads the
stick at its menu and in play, and its mask table at 0970h is how the
bit order above was confirmed in the emulator.  MANIC MINER 2 (the joystick edition, not the
keyboard one in the main image) is the sensitive case: with the
adapter on and no gamepad paired the miner must stand still.
