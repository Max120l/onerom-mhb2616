// Board selection.  The two Fire 24 revisions this firmware supports carry
// an identical socket-to-GPIO map -- the serving design is untouched by the
// choice -- and differ only in where the human-facing pins went:
//
//              rev E                       rev F
//   status     plain LED on GPIO 29        WS2812B neopixel on GPIO 29
//   jumper 1   GPIO 25                     GPIO 26     (recovery)
//   jumpers 4,8  GPIO 26, 27               GPIO 25, 24 (bank select)
//   SWD pads   GPIO 26, 27                 GPIO 25, 24
//
// Select with -DMHB_BOARD=FIRE24F from the build; rev E is the default.

#ifndef BOARD_H
#define BOARD_H

#ifndef MHB_BOARD_FIRE24F
#define MHB_BOARD_FIRE24F 0
#endif

#if MHB_BOARD_FIRE24F
#include "board_fire24f.h"
#else
#include "board_fire24e.h"
#endif

#endif // BOARD_H
