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

uint16_t mhb_index_of(unsigned addr) {
    uint16_t idx = 0;
    for (unsigned bit = 0; bit < 11; bit++) {
        if (addr & (1u << bit)) {
            idx |= 1u << (addr_gpio[bit] - 8);
        }
    }
    return idx;
}

uint16_t mhb_index_addr_mask(void) {
    return mhb_index_of(0x7FF);
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

void mhb_build_lut16(uint16_t *lut, const uint8_t banks[][MHB_BANK_SIZE],
                     uint8_t present, const mhb_lut16_cfg_t *cfg) {
    // 64 K iterations of a few table walks: pennies, paid once at boot.
    for (uint32_t idx = 0; idx < MHB_LUT_SIZE; idx++) {
        unsigned addr = mhb_addr_from_index((uint16_t)idx);
        unsigned a11  = ((idx & MHB_IDX_PR) ? 1u : 0u) ^ (cfg->pr_invert ? 1u : 0u);

        // Which pair, if any, is this access for?  Own /CS wins if the
        // decoder ever asserted both, which it should not.
        int pair = -1;
        if (!(idx & MHB_IDX_nCS)) {
            pair = (int)cfg->socket_pair;
        } else if (cfg->use_x1 && !(idx & MHB_IDX_X1)) {
            pair = (int)!cfg->socket_pair;
        }

        unsigned drive = 0;
        unsigned bank = 0;
        if (pair >= 0) {
            bank = (unsigned)pair * 2 + a11;
            if (cfg->fixed_bank >= 0) {
                // Drop-in replacement: answer exactly when the original in
                // this socket would have.  ignore_pr widens that to the
                // whole pair select, for programmer reads only.
                drive = (pair == (int)cfg->socket_pair)
                        && (cfg->ignore_pr || bank == (unsigned)cfg->fixed_bank);
                bank = (unsigned)cfg->fixed_bank;
            } else {
                drive = 1;
            }
            drive = drive && ((present >> bank) & 1);
        }

        uint8_t byte = ((present >> bank) & 1) ? banks[bank][addr] : 0xFF;
        uint16_t beacon = 0;
        if (bank == MHB_BEACON_BANK && addr >= MHB_BEACON_ADDR
            && addr < MHB_BEACON_ADDR + MHB_BEACON_MAX) {
            beacon = (uint16_t)(addr - MHB_BEACON_ADDR + 1)
                     << MHB_LUT16_BEACON_SHIFT;
        }
        lut[idx] = mhb_scramble_data(byte) | (drive ? MHB_LUT16_DRIVE : 0)
                 | ((bank & 3u) << MHB_LUT16_BANK_SHIFT) | beacon;
    }
}

void mhb_build_lut8(uint8_t *lut, const uint8_t banks[][MHB_BANK_SIZE],
                    uint8_t present, unsigned bank) {
    bank &= 3;
    for (uint32_t idx = 0; idx < MHB_LUT_SIZE; idx++) {
        unsigned addr = mhb_addr_from_index((uint16_t)idx);
        uint8_t byte = ((present >> bank) & 1) ? banks[bank][addr] : 0xFF;
        lut[idx] = mhb_scramble_data(byte);
    }
}

void mhb_select_masks(bool ignore_pr, bool pr_high,
                      uint32_t *mask, uint32_t *value) {
    uint32_t m = 1u << GPIO_nCS;      // /CS low, always: contributes 0
    uint32_t v = 0;
    if (!ignore_pr) {
        m |= 1u << GPIO_PR;
        if (pr_high) {
            v |= 1u << GPIO_PR;
        }
    }
    *mask = m;
    *value = v;
}
