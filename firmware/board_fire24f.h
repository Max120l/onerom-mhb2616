// One ROM Fire 24 rev F, for the same Tesla MHB 2616 / PMD 85-3 service as
// board_fire24e.h -- read that header first; the chip-side story is there
// and is identical.
//
// The rev F board definition (one-rom rust/config/json/fire-24-f.json) has a
// socket-to-GPIO map identical to rev E in every signal pin, including the
// X pads.  What moved is the furniture around the socket:
//
//  - The status indicator is a WS2812B RGB pixel (BOM: XL-1010RGBC-WS2812B)
//    on GPIO 29, in place of rev E's plain LED on the same GPIO.  It speaks
//    the 800 kHz serial protocol; gpio_put means nothing to it.
//  - The image-select jumpers rotated: the sel array is GPIO 26, 27, 25, 24
//    (rev E: 25, 24, 26, 27), and the SWD pads moved to GPIO 25/24
//    accordingly.  Jumpers 1-3 short their pin to ground when closed,
//    jumper 4 (GPIO 24) to +3V3 -- jumper_fitted() in main.c detects either
//    without caring which.

#ifndef BOARD_FIRE24F_H
#define BOARD_FIRE24F_H

#define MHB_BOARD_NAME  "One ROM Fire 24 rev F"

// ---------------------------------------------------------------------------
// Socket to GPIO: identical to rev E
// ---------------------------------------------------------------------------

#define SOCKET_PIN_TO_GPIO { \
    /*  1 */ 16, /*  2 */ 17, /*  3 */ 18, /*  4 */ 19, \
    /*  5 */ 20, /*  6 */ 21, /*  7 */ 22, /*  8 */ 23, \
    /*  9 */  7, /* 10 */  6, /* 11 */  5, /* 12 */ 0xFF /* GND */, \
    /* 13 */  0, /* 14 */  1, /* 15 */  2, /* 16 */  3, \
    /* 17 */  4, /* 18 */ 11, /* 19 */ 13, /* 20 */ 10, \
    /* 21 */ 12, /* 22 */ 14, /* 23 */ 15, /* 24 */ 0xFF /* VCC */ }

#define GPIO_X1              9    // jumper pad, not a socket pin
#define GPIO_X2              8    // jumper pad, not a socket pin

// The WS2812B status pixel.  Driven by a small PIO program from core 0;
// see the neopixel section in main.c for the colour code.
#define MHB_BOARD_HAS_NEOPIXEL  1
#define GPIO_NEOPIXEL       29

// Image-select jumper 1, reused as the recovery jumper exactly as on rev E
// -- fit it and power on to reach the bootrom's USB mode.  Note it is a
// DIFFERENT physical position than rev E's GPIO 25: on rev F, jumper 1 is
// GPIO 26.
#define GPIO_RECOVERY_JUMPER  26

// Select jumpers in positions 3 and 4 ("4" and "8"), read once at boot as a
// bank number in STATIC/HOTSPOT: GPIO 25 is bit 0, GPIO 24 is bit 1,
// fitted = 1.  These pads double as SWD on rev F; with a debug probe
// attached SWDIO may be driven and read as a fitted jumper, so detach the
// probe (or use -DMHB_SOCKET_BANK) when the bank comes from jumpers.
#define GPIO_BANK_JUMPER_0   25
#define GPIO_BANK_JUMPER_1   24

// ---------------------------------------------------------------------------
// MHB 2616 signal assignment: identical to rev E -- see board_fire24e.h for
// the full pinout table and the schematic reading behind it.
// ---------------------------------------------------------------------------

#define ADDR_GPIO { \
    /* A0  */ 23, /* A1  */ 22, /* A2  */ 21, /* A3  */ 20, \
    /* A4  */ 19, /* A5  */ 18, /* A6  */ 17, /* A7  */ 16, \
    /* A8  */ 15, /* A9  */ 14, /* A10 */ 13 }

#define DATA_GPIO { \
    /* D0 */ 7, /* D1 */ 6, /* D2 */ 5, /* D3 */ 0, \
    /* D4 */ 1, /* D5 */ 2, /* D6 */ 3, /* D7 */ 4 }

#define GPIO_nCS    10    // /CS, socket pin 20: active-low pair select
#define GPIO_PR     11    // PR,  socket pin 18: A11, possibly inverted
#define GPIO_PIN21  12    // socket pin 21: +5 V in this machine; unused

#define GPIO_OTHER_nCS  GPIO_X1

#endif // BOARD_FIRE24F_H
