// Board and chip pin mapping for One ROM Fire 24 rev E emulating a Tesla
// MHB 2616 in a PMD 85-3.
//
// The socket-pin -> GPIO half of this file is taken from the One ROM project's
// own board description (rust/config/json/fire-24-e.json) and is known good --
// it is the same table the onerom-1801re2 project verified on hardware.  The
// chip-pin -> signal half is read off the PMD 85-3 CPU board schematic
// (DOSKA CPU, 1 PK 280 77), which settled the control pins in a way the JEDEC
// 2716 pinout would not have predicted; see below.

#ifndef BOARD_FIRE24E_H
#define BOARD_FIRE24E_H

// ---------------------------------------------------------------------------
// One ROM Fire 24 rev E: 24-pin socket to RP2354A GPIO
// ---------------------------------------------------------------------------
//
// Socket pins 12 and 24 are power (GND and VCC) and are not routed to GPIOs.
// Every other socket pin lands on a GPIO in 0..7 or 10..23.  GPIO 8 and 9 go
// to the X1/X2 jumper pads, NOT to the socket.  For the 1801RE2 that gap was
// a constraint; here it is a gift: the eight data pins land on GPIO 0..7 and
// everything the serve loop must *read* -- eleven address lines, the two
// select pins and the two X pads -- lands on GPIO 8..23.  One 16-bit shift
// of the input register is the whole address decode.

#define SOCKET_PIN_TO_GPIO { \
    /*  1 */ 16, /*  2 */ 17, /*  3 */ 18, /*  4 */ 19, \
    /*  5 */ 20, /*  6 */ 21, /*  7 */ 22, /*  8 */ 23, \
    /*  9 */  7, /* 10 */  6, /* 11 */  5, /* 12 */ 0xFF /* GND */, \
    /* 13 */  0, /* 14 */  1, /* 15 */  2, /* 16 */  3, \
    /* 17 */  4, /* 18 */ 11, /* 19 */ 13, /* 20 */ 10, \
    /* 21 */ 12, /* 22 */ 14, /* 23 */ 15, /* 24 */ 0xFF /* VCC */ }

#define GPIO_X1              9    // jumper pad, not a socket pin
#define GPIO_X2              8    // jumper pad, not a socket pin

#define MHB_BOARD_NAME  "One ROM Fire 24 rev E"
#define MHB_BOARD_HAS_NEOPIXEL  0

// Status LED: +3V3 -> R5 (1K) -> anode, cathode -> this pin.  It lights when
// the pin is driven LOW.
#define GPIO_STATUS_LED     29
#define STATUS_LED_ON        0
#define STATUS_LED_OFF       1
#define GPIO_SEL_JUMPERS  { 25, 24, 26, 27 }

// The jumper block, in the letters printed on the board's underside.
// Verified against the rev E PCB netlist, and the C column's behaviour
// confirmed on hardware -- see docs/BOARD-NOTES.md.
//
//   label  GPIO  closed jumper ties to  also wired to    usable?
//     A     25   GND                    --               yes
//     B     24   GND                    --               yes
//     C     26   BOOT (via R2/QSPI_SS)  SWCLK (MCU p23)  NO
//     D     27   RUN  (10K to +3V3)     SWDIO (MCU p25)  NO
//
// C and D are each hard-wired to a second MCU pin -- the SWD pads -- whose
// own internal pull fights any pull we apply, so jumper_fitted() cannot
// read them.  On hardware, C read as permanently fitted.  Only A and B
// are clean shorts to ground, and A is the recovery jumper, which leaves
// exactly one free jumper.  The bank therefore comes from the build, not
// from jumpers; see MHB_SOCKET_BANK.

// Jumper A, the recovery jumper.  This firmware carries no USB stack, so
// flashing it replaces One ROM's picoboot, and the board exposes no
// BOOTSEL button.  Fit jumper A and power on: the board goes to the
// bootrom's USB mode instead of touching the bus at all.
#define GPIO_RECOVERY_JUMPER  25

// ---------------------------------------------------------------------------
// MHB 2616 chip pinout, as wired in the PMD 85-3
// ---------------------------------------------------------------------------
//
// From the DOSKA CPU schematic (1 PK 280 77, PRE TYP PMD 85-3), sockets
// DS4 (E), DS5 (D), DS6 (C), DS7 (B) -- the schematic prints DS6's letter
// as "0", but the real machine's socket is labelled C; one of several
// typos spotted on the sheet:
//
//   pin  signal          pin  signal          pin  signal
//    1   A7               9   D0              17   D7
//    2   A6              10   D1              18   PR
//    3   A5              11   D2              19   A10
//    4   A4              12   GND             20   /CS
//    5   A3              13   D3              21   +5 V
//    6   A2              14   D4              22   A9
//    7   A1              15   D5              23   A8
//    8   A0              16   D6              24   Vcc
//
// Address and data follow JEDEC 2716; power matches what the Fire 24
// hard-wires, so the board drops in unmodified.  The control pins do NOT
// follow the 2716, and this is read off the schematic, not inferred:
//
//   /CS on pin 20 (2716: /OE), drawn active low, is the PAIR select:
//   DS4+DS5 share one /CS net, DS6+DS7 the other.  The mainboard decoder
//   asserts one per 4 KB half of the 8 KB monitor.
//
//   PR on pin 18 (2716: /CE) carries A11: straight into DS4 and DS6,
//   inverted into DS5 and DS7.  An address bit delivered as a select --
//   which is what lets one board serve a whole pair from one socket with
//   no wires at all: the bank bit is already on pin 18.
//
//   Pin 21 (2716: Vpp) is strapped to +5 V along with pin 24.  It carries
//   no information; the firmware never drives it and never reads it.

// GPIO carrying each address line.  A0..A10, in that order.  The order is
// scrambled relative to the socket, which costs nothing: the serve loop's
// lookup table is built through this map at boot, so the scramble is paid
// once, not per access.
//
// Every entry is in 13..23, i.e. inside the 16-bit field (gpio >> 8) that the
// serve loop uses as its table index.  decode.c turns these GPIO numbers into
// index-bit numbers by subtracting 8.
#define ADDR_GPIO { \
    /* A0  */ 23, /* A1  */ 22, /* A2  */ 21, /* A3  */ 20, \
    /* A4  */ 19, /* A5  */ 18, /* A6  */ 17, /* A7  */ 16, \
    /* A8  */ 15, /* A9  */ 14, /* A10 */ 13 }

// GPIO carrying each data line, D0 first.  All in 0..7, so the drive mask is
// exactly 0xFF and the serve loop writes bytes pre-scrambled through this map.
#define DATA_GPIO { \
    /* D0 */ 7, /* D1 */ 6, /* D2 */ 5, /* D3 */ 0, \
    /* D4 */ 1, /* D5 */ 2, /* D6 */ 3, /* D7 */ 4 }

// Select pins, per the schematic reading above.
#define GPIO_nCS    10    // /CS, socket pin 20: active-low pair select
#define GPIO_PR     11    // PR,  socket pin 18: A11, possibly inverted
#define GPIO_PIN21  12    // socket pin 21: +5 V in this machine; unused

// FULL8K mode: the other pair's /CS, brought to the X1 pad by one flying
// lead from the corresponding pin 20.  X2 is unassigned and kept pulled.
#define GPIO_OTHER_nCS  GPIO_X1

#endif // BOARD_FIRE24E_H
