// Tesla MHB 2616 emulation on a One ROM Fire 24 rev E, sized for the
// PMD 85-3's 8 KB monitor.
//
// A 2616 is the easy shape of chip: address in, data out, selects gating the
// drivers, no handshake anywhere.  The host asserts its selects, waits its
// mask-programmed access time (hundreds of nanoseconds against a 2 MHz 8080),
// and samples.  Nothing acknowledges, so unlike the 1801RE2 sibling project
// there is no PIO program here at all -- just a loop that must always be
// faster than the chip it replaces:
//
//     read GPIO -> index a 64 KB table -> write data, set directions
//
// The Fire 24 rev E pin map makes the middle step free.  Data lands on GPIO
// 0..7; address, controls and the X pads land on GPIO 8..23.  So the top 16
// bits of one GPIO read are the complete question -- eleven address bits, two
// bank bits, three control bits -- and a 64 KB table built at boot holds the
// answer for every one of them, pre-scrambled into drive order.  The loop is
// a handful of instructions, comfortably inside a 2616's ~450 ns access time
// at 150 MHz.
//
// The 8 KB question is the interesting one.  The monitor lives in four 2616s
// and the socket only carries A0..A10; which chip answers is decided by
// per-socket selects decoded on the mainboard from A11/A12.  Where the
// missing two bits come from is the bank source, chosen at build time:
//
//   EXT      A11/A12 arrive on the X pads by wire.  The real thing: one
//            board, one socket, all 8 KB, live.  Needs two flying leads and
//            the other three chips out of their sockets.
//   HOTSPOT  Reads of magic addresses in the served window switch banks.
//            Zero wires, but only software that knows about the hotspot can
//            steer it -- the stock monitor does not.
//   STATIC   One 2 KB bank, chosen by jumpers at boot.  A drop-in single-chip
//            replacement; four spare chips in one.

#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "pico/bootrom.h"
#include "pico/multicore.h"
#include "hardware/structs/sio.h"

#include "board_fire24e.h"
#include "decode.h"
#include "rom_images.h"

// 150 MHz is the RP2350's rated speed, and the loop fits a 2616's access time
// at it several times over.  The knob exists because the sibling project
// learned to keep it, not because it is expected to move.
#ifndef MHB_SYS_CLK_KHZ
#define MHB_SYS_CLK_KHZ  150000
#endif

#if (MHB_BANK_EXT + MHB_BANK_HOTSPOT + MHB_BANK_STATIC) != 1
#error "exactly one bank source; use the CMake MHB_BANK_SOURCE option"
#endif

// First of the four hotspot addresses, as an offset inside the 2 KB window.
// base+0..base+3 select banks 0..3.  The default parks them on the last four
// bytes of the window, Atari style; software served in HOTSPOT mode must
// treat those four bytes as control registers, not storage.
#ifndef MHB_HOTSPOT_BASE
#define MHB_HOTSPOT_BASE  0x7F4
#endif
static_assert(MHB_HOTSPOT_BASE + 4 <= MHB_BANK_SIZE, "hotspots beyond window");

// Bumped on every select edge.  Core 1 only writes, core 0 only reads, and a
// torn read costs nothing but a slightly wrong blink, so no synchronisation.
static volatile uint32_t g_served;

#if MHB_BANK_HOTSPOT
static uint8_t g_lut[MHB_BANKS][MHB_LUT_SIZE];
static uint16_t g_hs_idx[MHB_BANKS];
static uint16_t g_hs_mask;
#else
static uint8_t g_lut[1][MHB_LUT_SIZE];
#endif

// Drive permission per bank-bit combination of the index (idx & 3).  In EXT
// mode an absent bank means the real chip for that window is still in its
// socket, and two drivers on one data bus is a fight the host cannot
// referee -- so absent banks are never driven, not served as 0xFF.  In the
// other modes the bank bits are pulled to a constant and this is all-ones.
static uint8_t g_drive_ok[4];

static uint32_t g_sel_mask, g_sel_val;

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

// Is a jumper fitted, whichever rail it ties to?
//
// A floating pin follows whichever internal pull is applied; a pin something
// else is driving does not.  Comparing the two reads therefore detects a
// fitted jumper without needing to know its sense.  Taken verbatim from the
// 1801RE2 firmware, where the consequence of getting it backwards -- a board
// that either never runs or cannot be recovered -- is the same as here.
static bool jumper_fitted(unsigned gpio) {
    gpio_init(gpio);
    gpio_set_dir(gpio, GPIO_IN);
    gpio_pull_down(gpio);
    busy_wait_us(50);
    bool with_pulldown = gpio_get(gpio);
    gpio_pull_up(gpio);
    busy_wait_us(50);
    bool with_pullup = gpio_get(gpio);
    gpio_disable_pulls(gpio);
    return with_pulldown == with_pullup;
}

static void setup_gpio(void) {
    // Data pins: SIO, input until a select says otherwise, 8 mA when driving
    // -- the drive One ROM itself uses into 5 V hosts.
    for (unsigned gpio = 0; gpio < 8; gpio++) {
        gpio_init(gpio);
        gpio_set_dir(gpio, GPIO_IN);
        gpio_set_drive_strength(gpio, GPIO_DRIVE_STRENGTH_8MA);
    }
    // Address and control pins: inputs, always.
    for (unsigned gpio = 10; gpio < 24; gpio++) {
        gpio_init(gpio);
        gpio_set_dir(gpio, GPIO_IN);
    }

    // The X pads.  In EXT mode they are the two missing address bits and the
    // pull-down only matters with the wires absent, where it reads bank 0 --
    // a board wired for 8 KB but missing a lead serves wrong banks, and the
    // selftest image exists to catch exactly that before a monitor image
    // goes in.  In the other modes they are pulled low and the tables are
    // built so their state cannot matter anyway.
    gpio_init(GPIO_X1);
    gpio_set_dir(GPIO_X1, GPIO_IN);
    gpio_pull_down(GPIO_X1);
    gpio_init(GPIO_X2);
    gpio_set_dir(GPIO_X2, GPIO_IN);
    gpio_pull_down(GPIO_X2);

    gpio_init(GPIO_STATUS_LED);
    gpio_set_dir(GPIO_STATUS_LED, GPIO_OUT);
    gpio_put(GPIO_STATUS_LED, STATUS_LED_OFF);
}

static void build_tables(void) {
#if MHB_BANK_EXT
    mhb_build_lut(g_lut[0], mhb_banks, mhb_bank_present,
                  MHB_LUT_BANK_FROM_INDEX, 0);
    for (unsigned i = 0; i < 4; i++) {
        unsigned bank = mhb_bank_from_index((uint16_t)i);
        g_drive_ok[i] = (mhb_bank_present >> bank) & 1;
    }
#elif MHB_BANK_HOTSPOT
    for (unsigned b = 0; b < MHB_BANKS; b++) {
        mhb_build_lut(g_lut[b], mhb_banks, mhb_bank_present,
                      MHB_LUT_BANK_FIXED, b);
    }
    // The hotspot matcher compares the index under the address+bank mask.
    // The bank bits are in the mask on purpose: the pads are pulled low in
    // this mode, mhb_index_of(0, ...) encodes them low, so a pad something
    // has wired up anyway breaks the match rather than silently aliasing
    // four hotspots onto sixteen.
    g_hs_mask = mhb_index_addr_mask();
    for (unsigned b = 0; b < MHB_BANKS; b++) {
        g_hs_idx[b] = mhb_index_of(0, MHB_HOTSPOT_BASE + b);
    }
    memset(g_drive_ok, 1, sizeof g_drive_ok);
#else // MHB_BANK_STATIC
    unsigned bank =
#if defined(MHB_STATIC_BANK) && MHB_STATIC_BANK >= 0
        MHB_STATIC_BANK & 3;
#else
        (jumper_fitted(GPIO_BANK_JUMPER_0) ? 1 : 0) |
        (jumper_fitted(GPIO_BANK_JUMPER_1) ? 2 : 0);
#endif
    mhb_build_lut(g_lut[0], mhb_banks, mhb_bank_present,
                  MHB_LUT_BANK_FIXED, bank);
    memset(g_drive_ok, 1, sizeof g_drive_ok);
#endif

    mhb_select_masks(MHB_GATE_CE, MHB_GATE_OE, &g_sel_mask, &g_sel_val);
}

// ---------------------------------------------------------------------------
// Serving
// ---------------------------------------------------------------------------

static void __not_in_flash_func(serve_forever)(void) {
    const uint8_t *lut = g_lut[0];
    bool driving = false;

    for (;;) {
        uint32_t in  = sio_hw->gpio_in;
        uint32_t idx = (in >> 8) & 0xFFFFu;
        uint32_t byte = lut[idx];

        if ((in & g_sel_mask) == g_sel_val && g_drive_ok[idx & 3]) {
            // Data before direction: the bus must never see a stale byte
            // driven, and on later iterations of a held select this is just
            // the address changing under us, which is what a ROM does.
            // The togl dance writes only GPIO 0..7 and leaves the LED alone.
            sio_hw->gpio_togl = (sio_hw->gpio_out ^ byte) & 0xFFu;
            if (!driving) {
                sio_hw->gpio_oe_set = 0xFFu;
                driving = true;
                g_served++;
            }
#if MHB_BANK_HOTSPOT
            // A read of a hotspot switches banks for the *next* access; the
            // data for this one came from the old bank, which is the
            // convention every hotspot-banked cartridge scheme uses.
            // Re-matching on later loop iterations of the same read is
            // idempotent: each hotspot names a fixed bank.
            uint32_t a = idx & g_hs_mask;
            if (a == g_hs_idx[0])      lut = g_lut[0];
            else if (a == g_hs_idx[1]) lut = g_lut[1];
            else if (a == g_hs_idx[2]) lut = g_lut[2];
            else if (a == g_hs_idx[3]) lut = g_lut[3];
#endif
        } else if (driving) {
            sio_hw->gpio_oe_clr = 0xFFu;
            driving = false;
        }
    }
}

int main(void) {
    // Before anything else, and before a single socket pin is touched: if the
    // recovery jumper is fitted, hand straight back to the bootrom.  This is
    // the only way back to a flashable board, so it must work even when the
    // rest of this firmware does not.
    if (jumper_fitted(GPIO_RECOVERY_JUMPER)) {
        reset_usb_boot(0, 0);
    }

    set_sys_clock_khz(MHB_SYS_CLK_KHZ, true);
    setup_gpio();
    build_tables();

    multicore_launch_core1(serve_forever);

    // Core 0 turns the served-cycle count into something visible.  Installed
    // in a machine that will not boot, the useful question is whether the
    // board is being selected at all:
    //
    //   dark          nothing is selecting us -- no strobes, or no power
    //   fast flicker  serving normally
    //   solid then dark   a burst at boot and then silence, i.e. the machine
    //                     read us and gave up somewhere else
    uint32_t last_served = 0;
    while (true) {
        uint32_t served = g_served;
        gpio_put(GPIO_STATUS_LED,
                 served != last_served ? STATUS_LED_ON : STATUS_LED_OFF);
        last_served = served;
        sleep_ms(100);
    }
}
