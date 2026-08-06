// Host tests for decode.c: the bit scrambles, the LUT builder, the gating
// masks.  No SDK, no hardware -- see the Makefile.
//
// The properties tested are the ones a wiring mistake would break silently:
// index_of and addr_from_index must be inverses; every address GPIO must sit
// inside the 16-bit index field and outside the data field; the LUT must
// return the right pre-scrambled byte for every (bank, addr); absent banks
// must read 0xFF; the fixed-bank tables must not care about the X-pad bits.

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

// A recognisable, bank-dependent filling: no two (bank, addr) pairs collide
// within the range the tests probe against each other.
static uint8_t fill(unsigned bank, unsigned addr) {
    return (uint8_t)(addr * 7 + bank * 61 + 3);
}

static uint8_t banks[MHB_BANKS][MHB_BANK_SIZE];

static void test_pin_map_sanity(void) {
    static const uint8_t addr_gpio[11] = ADDR_GPIO;
    static const uint8_t data_gpio[8]  = DATA_GPIO;
    for (unsigned i = 0; i < 11; i++) {
        CHECK(addr_gpio[i] >= 10 && addr_gpio[i] <= 23,
              "A%u on GPIO %u, outside the index field", i, addr_gpio[i]);
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
    CHECK(GPIO_BANK_A11 == 9 && GPIO_BANK_A12 == 8, "bank bits off the X pads");
}

static void test_index_roundtrip(void) {
    for (unsigned bank = 0; bank < 4; bank++) {
        for (unsigned addr = 0; addr < 2048; addr++) {
            uint16_t idx = mhb_index_of(bank, addr);
            CHECK(mhb_addr_from_index(idx) == addr,
                  "addr 0x%03X came back 0x%03X", addr,
                  mhb_addr_from_index(idx));
            CHECK(mhb_bank_from_index(idx) == bank,
                  "bank %u came back %u", bank, mhb_bank_from_index(idx));
        }
    }
    // Control bits must not disturb the address.
    uint16_t idx = mhb_index_of(2, 0x555);
    uint16_t ctl = (uint16_t)((1u << (GPIO_nCE - 8)) | (1u << (GPIO_nOE - 8))
                              | (1u << (GPIO_PIN21 - 8)));
    CHECK(mhb_addr_from_index(idx | ctl) == 0x555, "control bits leak into addr");
    CHECK((mhb_index_addr_mask() & ctl) == 0, "control bits in the addr mask");
}

static void test_scramble(void) {
    // A permutation: every byte distinct, popcount preserved.
    uint8_t hit[256] = {0};
    for (unsigned b = 0; b < 256; b++) {
        uint8_t s = mhb_scramble_data((uint8_t)b);
        CHECK(!hit[s], "scramble collides at 0x%02X", b);
        hit[s] = 1;
        CHECK(__builtin_popcount(s) == __builtin_popcount(b),
              "scramble gains or loses bits at 0x%02X", b);
    }
    CHECK(mhb_scramble_data(0x00) == 0x00, "zero must scramble to zero");
    CHECK(mhb_scramble_data(0xFF) == 0xFF, "ones must scramble to ones");
}

static uint8_t *lut;

static void test_lut_ext(void) {
    mhb_build_lut(lut, banks, 0x0F, MHB_LUT_BANK_FROM_INDEX, 0);
    for (unsigned bank = 0; bank < 4; bank++) {
        for (unsigned addr = 0; addr < 2048; addr += 17) {
            uint16_t idx = mhb_index_of(bank, addr);
            CHECK(lut[idx] == mhb_scramble_data(fill(bank, addr)),
                  "EXT lut wrong at bank %u addr 0x%03X", bank, addr);
        }
    }
    // Absent banks are 0xFF -- the drive-permission mask is what actually
    // keeps them off the bus in EXT mode, but the contents should not lie.
    mhb_build_lut(lut, banks, 0x0B, MHB_LUT_BANK_FROM_INDEX, 0);
    uint16_t idx = mhb_index_of(2, 0x123);
    CHECK(lut[idx] == 0xFF, "absent bank served real data");
}

static void test_lut_fixed(void) {
    mhb_build_lut(lut, banks, 0x0F, MHB_LUT_BANK_FIXED, 1);
    for (unsigned addr = 0; addr < 2048; addr += 13) {
        // Whatever the X pads read, a fixed-bank table answers from its bank.
        for (unsigned x = 0; x < 4; x++) {
            uint16_t idx = mhb_index_of(x, addr);
            CHECK(lut[idx] == mhb_scramble_data(fill(1, addr)),
                  "fixed lut heeds the X pads at addr 0x%03X x=%u", addr, x);
        }
    }
}

static void test_select_masks(void) {
    uint32_t mask, val;
    mhb_select_masks(true, true, &mask, &val);
    CHECK(mask == ((1u << GPIO_nCE) | (1u << GPIO_nOE)), "CE+OE mask wrong");
    CHECK(val == 0, "CE+OE are active low; required value must be 0");
    mhb_select_masks(false, true, &mask, &val);
    CHECK(mask == (1u << GPIO_nOE), "OE-only mask wrong");
    mhb_select_masks(false, false, &mask, &val);
    CHECK(mask == 0 && val == 0, "no-gating must select always");
    // (in & 0) == 0 for all in: always selected, as documented.
}

int main(void) {
    for (unsigned b = 0; b < MHB_BANKS; b++) {
        for (unsigned a = 0; a < MHB_BANK_SIZE; a++) {
            banks[b][a] = fill(b, a);
        }
    }
    lut = malloc(MHB_LUT_SIZE);
    if (!lut) return 2;

    test_pin_map_sanity();
    test_index_roundtrip();
    test_scramble();
    test_lut_ext();
    test_lut_fixed();
    test_select_masks();

    if (g_failures) {
        printf("%u failure(s)\n", g_failures);
        return 1;
    }
    printf("all decode tests passed\n");
    return 0;
}
