// Host tests for decode.c: the bit scrambles, both table builders, the
// gating masks.  No SDK, no hardware -- see the Makefile.
//
// The properties tested are the ones a wiring mistake would break silently:
// index_of and addr_from_index must be inverses; every address GPIO must sit
// inside the 16-bit index field and outside the data field; the tables must
// return the right pre-scrambled byte -- and, for the 16-bit tables, the
// right *drive decision* -- for every combination of /CS, PR and X1 that the
// PMD 85-3 wiring can produce.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "decode.h"

static unsigned g_failures;

#define CHECK(cond, ...) do { \
    if (!(cond)) { \
        g_failures++; \
        printf("FAIL %s:%d: ", __FILE__, __LINE__); \
        printf(__VA_ARGS__); \
        printf("\n"); \
    } \
} while (0)

// A recognisable, bank-dependent filling.
static uint8_t fill(unsigned bank, unsigned addr) {
    return (uint8_t)(addr * 7 + bank * 61 + 3);
}

static uint8_t banks[MHB_BANKS][MHB_BANK_SIZE];

// The index a host access produces: address bits plus explicit select
// states.  cs/x1 are the *electrical* levels (true = high = deasserted for
// these active-low selects); pr is the electrical level of pin 18.
static uint16_t make_idx(unsigned addr, bool cs_high, bool pr_high, bool x1_high) {
    uint16_t idx = mhb_index_of(addr);
    if (cs_high) idx |= MHB_IDX_nCS;
    if (pr_high) idx |= MHB_IDX_PR;
    if (x1_high) idx |= MHB_IDX_X1;
    return idx;
}

static void test_pin_map_sanity(void) {
    static const uint8_t addr_gpio[11] = ADDR_GPIO;
    static const uint8_t data_gpio[8]  = DATA_GPIO;
    for (unsigned i = 0; i < 11; i++) {
        CHECK(addr_gpio[i] >= 13 && addr_gpio[i] <= 23,
              "A%u on GPIO %u, outside 13..23", i, addr_gpio[i]);
        for (unsigned j = i + 1; j < 11; j++) {
            CHECK(addr_gpio[i] != addr_gpio[j], "A%u and A%u share a GPIO", i, j);
        }
    }
    uint8_t seen = 0;
    for (unsigned i = 0; i < 8; i++) {
        CHECK(data_gpio[i] <= 7, "D%u on GPIO %u, outside the drive mask",
              i, data_gpio[i]);
        seen |= 1u << data_gpio[i];
    }
    CHECK(seen == 0xFF, "data GPIOs are not a permutation of 0..7");
    // The select bits must be where the schematic reading put them, and
    // distinct from every address bit.
    CHECK(GPIO_nCS == 10 && GPIO_PR == 11 && GPIO_PIN21 == 12,
          "select GPIOs moved without the tests noticing");
    CHECK((mhb_index_addr_mask()
           & (MHB_IDX_nCS | MHB_IDX_PR | MHB_IDX_X1 | MHB_IDX_X2 | MHB_IDX_PIN21)) == 0,
          "select bits leak into the address mask");
}

static void test_index_roundtrip(void) {
    for (unsigned addr = 0; addr < 2048; addr++) {
        uint16_t idx = mhb_index_of(addr);
        CHECK(mhb_addr_from_index(idx) == addr,
              "addr 0x%03X came back 0x%03X", addr, mhb_addr_from_index(idx));
    }
    // Select bits must not disturb the address.
    uint16_t ctl = MHB_IDX_nCS | MHB_IDX_PR | MHB_IDX_X1 | MHB_IDX_X2 | MHB_IDX_PIN21;
    CHECK(mhb_addr_from_index(mhb_index_of(0x555) | ctl) == 0x555,
          "select bits leak into decoded address");
}

static void test_scramble(void) {
    uint8_t hit[256] = {0};
    for (unsigned b = 0; b < 256; b++) {
        uint8_t s = mhb_scramble_data((uint8_t)b);
        CHECK(!hit[s], "scramble collides at 0x%02X", b);
        hit[s] = 1;
        CHECK(__builtin_popcount(s) == __builtin_popcount(b),
              "scramble gains or loses bits at 0x%02X", b);
    }
}

static uint16_t *lut16;

// PAIR mode, socket in pair 0, PR straight (a DS4-like socket).
static void test_lut16_pair(void) {
    mhb_lut16_cfg_t cfg = { .socket_pair = 0, .pr_invert = false,
                            .use_x1 = false, .fixed_bank = -1 };
    mhb_build_lut16(lut16, banks, 0x0F, &cfg);

    for (unsigned addr = 0; addr < 2048; addr += 19) {
        // /CS low, PR low -> A11 = 0 -> bank 0.
        uint16_t v = lut16[make_idx(addr, false, false, true)];
        CHECK((v & MHB_LUT16_DRIVE) && (v & 0xFF) == mhb_scramble_data(fill(0, addr)),
              "pair: bank 0 wrong at 0x%03X", addr);
        // /CS low, PR high -> A11 = 1 -> bank 1.
        v = lut16[make_idx(addr, false, true, true)];
        CHECK((v & MHB_LUT16_DRIVE) && (v & 0xFF) == mhb_scramble_data(fill(1, addr)),
              "pair: bank 1 wrong at 0x%03X", addr);
        // /CS high -> no drive, whatever PR and X1 do.
        CHECK(!(lut16[make_idx(addr, true, false, true)] & MHB_LUT16_DRIVE),
              "pair: drives with /CS high at 0x%03X", addr);
        CHECK(!(lut16[make_idx(addr, true, true, false)] & MHB_LUT16_DRIVE),
              "pair: X1 must be ignored without use_x1 at 0x%03X", addr);
    }

    // The inverted socket (DS5-like): PR low now means A11 = 1.
    cfg.pr_invert = true;
    mhb_build_lut16(lut16, banks, 0x0F, &cfg);
    uint16_t v = lut16[make_idx(0x111, false, false, true)];
    CHECK((v & 0xFF) == mhb_scramble_data(fill(1, 0x111)),
          "pair: pr_invert did not flip the bank");

    // Pair 1 serves banks 2/3.
    cfg = (mhb_lut16_cfg_t){ .socket_pair = 1, .pr_invert = false,
                             .use_x1 = false, .fixed_bank = -1 };
    mhb_build_lut16(lut16, banks, 0x0F, &cfg);
    v = lut16[make_idx(0x222, false, true, true)];
    CHECK((v & 0xFF) == mhb_scramble_data(fill(3, 0x222)),
          "pair 1: bank 3 wrong");
}

static void test_lut16_full8k(void) {
    mhb_lut16_cfg_t cfg = { .socket_pair = 0, .pr_invert = false,
                            .use_x1 = true, .fixed_bank = -1 };
    mhb_build_lut16(lut16, banks, 0x0F, &cfg);

    unsigned addr = 0x3A5;
    // Own /CS active: banks 0/1 by PR.
    uint16_t v = lut16[make_idx(addr, false, true, true)];
    CHECK((v & MHB_LUT16_DRIVE) && (v & 0xFF) == mhb_scramble_data(fill(1, addr)),
          "full8k: own pair bank 1 wrong");
    // Other pair's /CS (X1) active: banks 2/3 by PR.
    v = lut16[make_idx(addr, true, false, false)];
    CHECK((v & MHB_LUT16_DRIVE) && (v & 0xFF) == mhb_scramble_data(fill(2, addr)),
          "full8k: other pair bank 2 wrong");
    v = lut16[make_idx(addr, true, true, false)];
    CHECK((v & 0xFF) == mhb_scramble_data(fill(3, addr)),
          "full8k: other pair bank 3 wrong");
    // Neither select active: silence.
    CHECK(!(lut16[make_idx(addr, true, true, true)] & MHB_LUT16_DRIVE),
          "full8k: drives with no select active");
    // Both active should not happen; own pair wins, but it must still drive
    // *something* deterministic rather than glitch.
    v = lut16[make_idx(addr, false, false, false)];
    CHECK((v & MHB_LUT16_DRIVE) && (v & 0xFF) == mhb_scramble_data(fill(0, addr)),
          "full8k: both-selects case not deterministic");

    // An absent bank is never driven -- the real chip keeps that window.
    mhb_build_lut16(lut16, banks, 0x0B, &cfg);   // bank 2 absent
    CHECK(!(lut16[make_idx(addr, true, false, false)] & MHB_LUT16_DRIVE),
          "full8k: drives an absent bank");
    CHECK(lut16[make_idx(addr, false, false, true)] & MHB_LUT16_DRIVE,
          "full8k: absent bank silenced a present one");
}

// The whole FULL8K story in one sweep: for every address of the 8 KB
// monitor, does a socket-eye view of that access -- 11 address lines,
// PR = A11, one of the two pair selects low -- return the right byte?
//
// The spot checks above test the table; this tests the *mapping onto the
// machine*, which is where a wrong socket_pair or pr_invert would show up
// as a monitor served in the wrong order.  Modelled on a DS4 board:
// pair 0, PR straight, X1 carrying the other pair's select.
static void test_full8k_sweep(void) {
    mhb_lut16_cfg_t cfg = { .socket_pair = 0, .pr_invert = false,
                            .use_x1 = true, .fixed_bank = -1 };
    mhb_build_lut16(lut16, banks, 0x0F, &cfg);

    unsigned wrong = 0, silent = 0;
    for (unsigned off = 0; off < 4 * MHB_BANK_SIZE; off++) {
        unsigned a   = off & 0x7FF;
        unsigned a11 = (off >> 11) & 1;
        unsigned a12 = (off >> 12) & 1;
        // Active low: the selected pair's line is low, the other idle high.
        uint16_t idx = mhb_index_of(a) | (a11 ? MHB_IDX_PR : 0)
                     | (a12 ? MHB_IDX_nCS : MHB_IDX_X1);
        uint16_t v = lut16[idx];
        if (!(v & MHB_LUT16_DRIVE)) {
            silent++;
        } else if ((v & 0xFF) != mhb_scramble_data(fill(off / MHB_BANK_SIZE, a))) {
            wrong++;
        }
    }
    CHECK(silent == 0, "full8k sweep: %u monitor addresses not served", silent);
    CHECK(wrong == 0, "full8k sweep: %u monitor addresses served wrong", wrong);

    // ...and nothing at all when neither pair is selected.
    unsigned drove = 0;
    for (unsigned off = 0; off < 4 * MHB_BANK_SIZE; off++) {
        uint16_t idx = mhb_index_of(off & 0x7FF) | MHB_IDX_nCS | MHB_IDX_X1
                     | ((off & 0x800) ? MHB_IDX_PR : 0);
        if (lut16[idx] & MHB_LUT16_DRIVE) drove++;
    }
    CHECK(drove == 0, "full8k sweep: drove %u addresses with no select", drove);
}

static void test_lut16_static(void) {
    // The drop-in replacement for a DS5-like socket (pair 0, PR inverted)
    // holding bank 0: PR high selects it (inverted), PR low belongs to the
    // pair-mate and must not be driven.
    mhb_lut16_cfg_t cfg = { .socket_pair = 0, .pr_invert = true,
                            .use_x1 = false, .fixed_bank = 0 };
    mhb_build_lut16(lut16, banks, 0x0F, &cfg);
    unsigned addr = 0x0FE;
    uint16_t v = lut16[make_idx(addr, false, true, true)];
    CHECK((v & MHB_LUT16_DRIVE) && (v & 0xFF) == mhb_scramble_data(fill(0, addr)),
          "static: not serving its bank");
    CHECK(!(lut16[make_idx(addr, false, false, true)] & MHB_LUT16_DRIVE),
          "static: drives the pair-mate's PR state");
    CHECK(!(lut16[make_idx(addr, true, true, true)] & MHB_LUT16_DRIVE),
          "static: drives with /CS high");

    // Programmer mode: /CS alone gates, PR ignored, same bank both states.
    cfg.ignore_pr = true;
    mhb_build_lut16(lut16, banks, 0x0F, &cfg);
    CHECK((lut16[make_idx(addr, false, false, true)] & 0x1FF)
          == (mhb_scramble_data(fill(0, addr)) | MHB_LUT16_DRIVE),
          "static ignore_pr: PR low not served");
    CHECK((lut16[make_idx(addr, false, true, true)] & 0x1FF)
          == (mhb_scramble_data(fill(0, addr)) | MHB_LUT16_DRIVE),
          "static ignore_pr: PR high not served");
}

static void test_lut8_and_masks(void) {
    uint8_t *lut8 = malloc(MHB_LUT_SIZE);
    if (!lut8) { g_failures++; return; }
    mhb_build_lut8(lut8, banks, 0x0F, 2);
    for (unsigned addr = 0; addr < 2048; addr += 23) {
        // Select and X bits must not affect an 8-bit table.
        CHECK(lut8[make_idx(addr, false, false, true)]
              == mhb_scramble_data(fill(2, addr)),
              "lut8 wrong at 0x%03X", addr);
        CHECK(lut8[make_idx(addr, true, true, false)]
              == lut8[make_idx(addr, false, false, true)],
              "lut8 heeds select bits at 0x%03X", addr);
    }
    mhb_build_lut8(lut8, banks, 0x0B, 2);       // bank absent
    CHECK(lut8[mhb_index_of(0x10)] == mhb_scramble_data(0xFF),
          "lut8 absent bank not 0xFF");
    free(lut8);

    uint32_t mask, val;
    mhb_select_masks(false, false, &mask, &val);
    CHECK(mask == ((1u << GPIO_nCS) | (1u << GPIO_PR)) && val == 0,
          "mask: /CS low + PR low wrong");
    mhb_select_masks(false, true, &mask, &val);
    CHECK(mask == ((1u << GPIO_nCS) | (1u << GPIO_PR)) && val == (1u << GPIO_PR),
          "mask: /CS low + PR high wrong");
    mhb_select_masks(true, false, &mask, &val);
    CHECK(mask == (1u << GPIO_nCS) && val == 0, "mask: ignore_pr wrong");
}

int main(void) {
    for (unsigned b = 0; b < MHB_BANKS; b++) {
        for (unsigned a = 0; a < MHB_BANK_SIZE; a++) {
            banks[b][a] = fill(b, a);
        }
    }
    lut16 = malloc(MHB_LUT_SIZE * sizeof(uint16_t));
    if (!lut16) return 2;

    test_pin_map_sanity();
    test_index_roundtrip();
    test_scramble();
    test_lut16_pair();
    test_lut16_full8k();
    test_full8k_sweep();
    test_lut16_static();
    test_lut8_and_masks();

    if (g_failures) {
        printf("%u failure(s)\n", g_failures);
        return 1;
    }
    printf("all decode tests passed\n");
    return 0;
}
