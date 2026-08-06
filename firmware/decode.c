// See decode.h.  Compiled both into the firmware and into the host test, so
// nothing here may touch the SDK.

#include "decode.h"

static const uint8_t addr_gpio[11] = ADDR_GPIO;
static const uint8_t data_gpio[8]  = DATA_GPIO;

unsigned mhb_addr_from_index(uint16_t idx) {
    unsigned addr = 0;
    for (unsigned bit = 0; bit < 11; bit++) {
        // Index bit n is GPIO n+8; every ADDR_GPIO entry is >= 13, which the
        // host test asserts rather than trusts.
        if (idx & (1u << (addr_gpio[bit] - 8))) {
            addr |= 1u << bit;
        }
    }
    return addr;
}

unsigned mhb_bank_from_index(uint16_t idx) {
    unsigned bank = 0;
    if (idx & (1u << (GPIO_BANK_A11 - 8))) bank |= 1;
    if (idx & (1u << (GPIO_BANK_A12 - 8))) bank |= 2;
    return bank;
}

uint8_t mhb_scramble_data(uint8_t byte) {
    uint8_t out = 0;
    for (unsigned bit = 0; bit < 8; bit++) {
        if (byte & (1u << bit)) {
            out |= 1u << data_gpio[bit];
        }
    }
    return out;
}

uint16_t mhb_index_of(unsigned bank, unsigned addr) {
    uint16_t idx = 0;
    for (unsigned bit = 0; bit < 11; bit++) {
        if (addr & (1u << bit)) {
            idx |= 1u << (addr_gpio[bit] - 8);
        }
    }
    if (bank & 1) idx |= 1u << (GPIO_BANK_A11 - 8);
    if (bank & 2) idx |= 1u << (GPIO_BANK_A12 - 8);
    return idx;
}

uint16_t mhb_index_addr_mask(void) {
    return mhb_index_of(3, 0x7FF);
}

void mhb_build_lut(uint8_t *lut, const uint8_t banks[][MHB_BANK_SIZE],
                   uint8_t present, mhb_lut_mode_t mode, unsigned fixed_bank) {
    // 64 K iterations of a few table walks: pennies, paid once at boot.
    for (uint32_t idx = 0; idx < MHB_LUT_SIZE; idx++) {
        unsigned addr = mhb_addr_from_index((uint16_t)idx);
        unsigned bank = (mode == MHB_LUT_BANK_FROM_INDEX)
                            ? mhb_bank_from_index((uint16_t)idx)
                            : (fixed_bank & 3);
        uint8_t byte = (present & (1u << bank)) ? banks[bank][addr] : 0xFF;
        lut[idx] = mhb_scramble_data(byte);
    }
}

void mhb_select_masks(bool gate_ce, bool gate_oe, uint32_t *mask, uint32_t *value) {
    uint32_t m = 0, v = 0;
    if (gate_ce) {
        m |= 1u << GPIO_nCE;          // required low: contributes 0 to value
    }
    if (gate_oe) {
        m |= 1u << GPIO_nOE;
    }
#if MHB_PIN21_ROLE == MHB_PIN21_LOW
    m |= 1u << GPIO_PIN21;
#elif MHB_PIN21_ROLE == MHB_PIN21_HIGH
    m |= 1u << GPIO_PIN21;
    v |= 1u << GPIO_PIN21;
#endif
    *mask = m;
    *value = v;
}
