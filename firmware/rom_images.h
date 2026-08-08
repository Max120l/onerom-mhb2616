#ifndef ROM_IMAGES_H
#define ROM_IMAGES_H

#include <stdint.h>

// A 2616 is 2 KB.  The PMD 85-3 monitor is 8 KB, held as four of them, one
// per 2 KB "bank" -- banks 0..3 are the monitor's 0x0000, 0x0800, 0x1000 and
// 0x1800, i.e. A12:A11 = 00, 01, 10, 11.
//
// MODULE builds serve the BASIC ROM module instead, whose window is 16 KB in
// eight banks -- see docs/ROM-module.md.  The generated rom_images.c
// static-asserts its own bank count against this, so a module image built
// into a monitor firmware fails to compile rather than serving a quarter of
// itself.
#if MHB_BANK_MODULE
#define MHB_BANKS      8
#else
#define MHB_BANKS      4
#endif
#define MHB_BANK_SIZE  2048

// Which banks this build actually answers for, as a bitmask.  A bank that is
// absent is served as 0xFF in the modes where the board is the only chip on
// the bus, and *never driven* in EXT mode -- where an absent bank means the
// real chip for that window is still in its socket and must not be fought.
extern const uint8_t mhb_bank_present;

extern const uint8_t mhb_banks[MHB_BANKS][MHB_BANK_SIZE];
extern const char   *mhb_image_name;

#endif // ROM_IMAGES_H
