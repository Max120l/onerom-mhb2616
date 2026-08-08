// Address decode and data preparation for a 2616 socket in a PMD 85-3.
//
// Everything here is pure arithmetic over the GPIO field: no SDK calls, no
// hardware.  That is deliberate -- the bit scrambles are the part that is easy
// to get subtly wrong and the part worth testing on a host.  See
// test/test_decode.c.
//
// The serving design rests on one fact about the Fire 24 rev E pin map: the
// eight data pins are GPIO 0..7 and every input the loop needs -- A0..A10,
// /CS, PR, and the X pads -- is GPIO 8..23.  So (gpio_in >> 8) is a 16-bit
// value that contains the complete question, and a 64 KB table indexed by it
// contains the complete answer.
//
// Two table shapes, because the machine's wiring (schematic, DOSKA CPU
// 1 PK 280 77) gives the select pins jobs the JEDEC 2716 does not have:
//
//   /CS (socket pin 20) is a pair select: DS4+DS5 share one, DS6+DS7 the
//   other.  PR (socket pin 18) carries A11 -- straight into DS4/DS6,
//   inverted into DS5/DS7.  So "which of the four chips answers" is a
//   function of bits that all live inside our table index, and the drive
//   decision itself can be baked into the table.  The 16-bit-entry table
//   does that: low byte data, bit 8 = drive.  STATIC, PAIR and FULL8K use it.
//
//   HOTSPOT switches whole banks at runtime, which a baked table cannot
//   express; it keeps four 8-bit tables and a mask compare for gating.
//
// MODULE serves a different board entirely: the BASIC ROM module, DOSKA ROM
// PAMATI 1 PK 280 53, whose sockets are plain JEDEC 2716 and whose bank
// select lives on the module's own 7442 rather than on any socket pin.  It
// reuses the 16-bit table unchanged in shape, because there too every input
// to the drive decision fits inside the index.  See docs/ROM-module.md.

#ifndef DECODE_H
#define DECODE_H

#include <stdbool.h>
#include <stdint.h>

#include "board.h"
#include "rom_images.h"

#define MHB_LUT_SIZE  65536u

// Index-bit masks for the non-address inputs (index bit n is GPIO n+8).
#define MHB_IDX_X2     (1u << (GPIO_X2   - 8))   // bit 0
#define MHB_IDX_X1     (1u << (GPIO_X1   - 8))   // bit 1
#define MHB_IDX_nCS    (1u << (GPIO_nCS  - 8))   // bit 2
#define MHB_IDX_PR     (1u << (GPIO_PR   - 8))   // bit 3
#define MHB_IDX_PIN21  (1u << (GPIO_PIN21 - 8))  // bit 4; tied to +5 V, ignored

// The 11-bit chip address encoded in a 16-bit index value.
unsigned mhb_addr_from_index(uint16_t idx);

// The index a host access to addr produces, with every non-address bit zero.
uint16_t mhb_index_of(unsigned addr);

// Index bits that carry address.  Two index values equal under
// (mask | MHB_IDX_X1 | MHB_IDX_X2 | MHB_IDX_PR) can only differ in control
// state, never in the byte they name within one bank.
uint16_t mhb_index_addr_mask(void);

// A logical data byte rearranged into GPIO 0..7 drive order.
uint8_t mhb_scramble_data(uint8_t byte);

// ---------------------------------------------------------------------------
// The 16-bit-entry table: data plus drive decision
// ---------------------------------------------------------------------------

#define MHB_LUT16_DRIVE  0x100u

// Bits 9-10 carry the bank the entry came from.  Free real estate in a
// 16-bit entry, and it means a diagnostic build can record which banks the
// machine has actually asked for without computing anything on the serving
// path -- the answer is already in the word it just looked up.
#define MHB_LUT16_BANK_SHIFT  9
#define MHB_LUT16_BANK_MASK   (3u << MHB_LUT16_BANK_SHIFT)

// Bits 11-15: beacon number plus one, or zero for an ordinary byte.
//
// A beacon is a read of a reserved ROM address by code running on the host
// -- see tools/make_ramtest.py.  The board sees every read it serves, so a
// program with no working RAM, no video and no serial port can still report
// its findings by reading bytes it does not need.  Baking the number into
// the table keeps the serving path free of address comparisons: the answer
// arrives in the same word as the data.
#define MHB_LUT16_BEACON_SHIFT  11
#define MHB_LUT16_BEACON_MASK   (0x1Fu << MHB_LUT16_BEACON_SHIFT)
#define MHB_BEACON_MAX          31

// Where make_ramtest.py puts them: bank 3, offset 0x700 (CPU FF00).
#define MHB_BEACON_BANK  3
#define MHB_BEACON_ADDR  0x700

typedef struct {
    // Which pair this socket's /CS belongs to: 0 = banks 0/1, 1 = banks 2/3.
    // Which pair is which is set by the mainboard's decoder and confirmed by
    // checksum against a reference monitor image -- see docs/PMD85-3.md.
    unsigned socket_pair;

    // PR carries A11 into DS4/DS6 and inverted A11 into DS5/DS7.  Set this
    // for a socket of the inverted kind -- or flip it if the checksum test
    // says the two banks of a pair are swapped, which is the same discovery
    // made empirically.
    bool pr_invert;

    // FULL8K: the *other* pair's /CS arrives on the X1 pad by wire, and an
    // access with it active is served from the other pair's banks.
    bool use_x1;

    // Serve only this bank (0..3), gated so the board answers exactly when
    // the original chip in this socket would have -- the drop-in
    // replacement.  -1 serves the whole pair (and, with use_x1, all four).
    int fixed_bank;

    // With fixed_bank >= 0: ignore PR and answer whenever /CS is active.
    // For reading the board through a 2716 programmer, which parks PR at
    // one level and would otherwise only ever see half the pair's address
    // states match.  Never in a machine: the pair-mate chip answers the
    // other PR state, and this would fight it.
    bool ignore_pr;
} mhb_lut16_cfg_t;

// Fill a 64 K x 16-bit table: low byte the pre-scrambled data, bit 8 the
// drive decision.  Absent banks (per `present`) are never marked driven --
// in a machine that means the real chip for that bank keeps its socket.
void mhb_build_lut16(uint16_t *lut, const uint8_t banks[][MHB_BANK_SIZE],
                     uint8_t present, const mhb_lut16_cfg_t *cfg);

// ---------------------------------------------------------------------------
// MODULE mode: the BASIC ROM module's 16 KB window
// ---------------------------------------------------------------------------

// The module's sockets carry A0-A10 and nothing else of the address; A11-A13
// are the 7442's three inputs, A14 is stolen for the read strobe, and A15
// parks the decoder.  So the window is 16 KB in eight 2 KB banks, and the
// board learns the bank from the decoder's inputs rather than its outputs --
// three leads instead of five, which is what makes it fit.
#define MHB_MODULE_BANKS  8

// Where each of those lands.  Four leads; the fifth input is already in the
// socket.  Full wiring, and why pin 18 must be lifted, in docs/ROM-module.md.
//
//   socket pin 20   /OE    the module's read strobe (PC6)   -- no lead
//   socket pin 21   A11    from IO2 pin 15 (PC3)            -- pin 21 is NC
//   X1 pad          A12    from IO2 pin 14 (PC4)
//   X2 pad          A13    from IO2 pin 13 (PC5)
//   socket pin 18   park   from IO2 pin 12 (PC7)            -- lift pin 18
//
// Every one of these is pulled UP in MODULE builds, which makes a detached
// harness silent rather than wrong: park reads "decoder off", and the three
// address leads read bank 7, which no BASIC image occupies.
#define MHB_IDX_MOD_nOE   MHB_IDX_nCS
#define MHB_IDX_MOD_A11   MHB_IDX_PIN21
#define MHB_IDX_MOD_A12   MHB_IDX_X1
#define MHB_IDX_MOD_A13   MHB_IDX_X2
#define MHB_IDX_MOD_PARK  MHB_IDX_PR

// Eight banks need three bits where the monitor modes needed two, so MODULE
// entries carry the bank in bits 9-11 and never set a beacon -- bit 11 is
// the beacon field's low bit, and the two cannot coexist in one entry.  No
// build uses both: beacons report from code running in the CPU's address
// space, and the module is not in it.  The host test asserts the overlap
// stays theoretical by checking MODULE entries never set bits 12-15.
#define MHB_LUT16_MOD_BANK_SHIFT  9
#define MHB_LUT16_MOD_BANK_MASK   (7u << MHB_LUT16_MOD_BANK_SHIFT)

// Fill the table for MODULE mode.  `banks` must hold MHB_MODULE_BANKS banks;
// `present` marks which of them carry image data.  Absent banks are never
// driven, so the machine reads them as the floating bus does -- 0xFF, which
// is exactly what an unpopulated module socket gives it.
void mhb_build_lut16_module(uint16_t *lut, const uint8_t banks[][MHB_BANK_SIZE],
                            uint8_t present);

// ---------------------------------------------------------------------------
// The 8-bit-entry table: HOTSPOT mode
// ---------------------------------------------------------------------------

// Fill a 64 KB table with one fixed bank's data (absent banks read 0xFF).
void mhb_build_lut8(uint8_t *lut, const uint8_t banks[][MHB_BANK_SIZE],
                    uint8_t present, unsigned bank);

// Gating for the 8-bit path, as a mask compare over the raw 32-bit GPIO
// word: drive only while (gpio_in & mask) == value.  /CS must always be
// low; PR must additionally match a11_level unless ignore_pr.
void mhb_select_masks(bool ignore_pr, bool pr_high,
                      uint32_t *mask, uint32_t *value);

#endif // DECODE_H
