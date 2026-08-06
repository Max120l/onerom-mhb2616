// Address decode and data preparation for a 2616 socket.
//
// Everything here is pure arithmetic over the GPIO field: no SDK calls, no
// hardware.  That is deliberate -- the bit scrambles are the part that is easy
// to get subtly wrong and the part worth testing on a host.  See
// test/test_decode.c.
//
// The whole serving design rests on one fact about the Fire 24 rev E pin map:
// the eight data pins are GPIO 0..7 and every input the loop needs -- A0..A10,
// /CE, /OE, pin 21, and the X pads -- is GPIO 8..23.  So (gpio_in >> 8) is a
// 16-bit value that contains the complete question, and a 64 KB table indexed
// by it contains the complete answer.  Control bits ride along inside the
// index; the table simply repeats each byte across their combinations, and
// gating is done separately against masks so it can be reconfigured without
// rebuilding tables.

#ifndef DECODE_H
#define DECODE_H

#include <stdbool.h>
#include <stdint.h>

#include "board_fire24e.h"
#include "rom_images.h"

#define MHB_LUT_SIZE  65536u

// How a lookup table folds the bank bits in.
typedef enum {
    MHB_LUT_BANK_FROM_INDEX,   // EXT: X pads carry A11/A12, index bits decide
    MHB_LUT_BANK_FIXED,        // HOTSPOT/STATIC: one bank, X bits ignored
} mhb_lut_mode_t;

// The 11-bit chip address encoded in a 16-bit index value.
unsigned mhb_addr_from_index(uint16_t idx);

// The 2-bit bank number encoded in the index's X-pad bits (EXT mode reading).
unsigned mhb_bank_from_index(uint16_t idx);

// A logical data byte rearranged into GPIO 0..7 drive order.
uint8_t mhb_scramble_data(uint8_t byte);

// The index a host access to (bank, addr) produces, with all control and X
// bits zero.  The inverse of the two functions above; tests and the hotspot
// matcher are its users.
uint16_t mhb_index_of(unsigned bank, unsigned addr);

// Index bits that carry address or bank -- i.e. everything except the three
// control-pin bits.  Two index values equal under this mask name the same
// byte.
uint16_t mhb_index_addr_mask(void);

// Fill a 64 KB table: lut[idx] = pre-scrambled data byte for the address (and,
// in BANK_FROM_INDEX mode, the bank) encoded in idx.  fixed_bank names the
// bank in BANK_FIXED mode and is ignored otherwise.  Absent banks read 0xFF.
void mhb_build_lut(uint8_t *lut, const uint8_t banks[][MHB_BANK_SIZE],
                   uint8_t present, mhb_lut_mode_t mode, unsigned fixed_bank);

// Gating masks over the raw 32-bit GPIO word: the board drives the bus only
// while (gpio_in & mask) == value.  Built from the MHB_GATE_* / MHB_PIN21_ROLE
// configuration; a mask of 0 means "always selected", which is only sane on a
// bench rig.
void mhb_select_masks(bool gate_ce, bool gate_oe, uint32_t *mask, uint32_t *value);

#endif // DECODE_H
