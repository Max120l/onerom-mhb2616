// Tesla MHB 2616 emulation on a One ROM Fire 24 (rev E or rev F), sized
// for the PMD 85-3's 8 KB monitor.
//
// A 2616 is the easy shape of chip: address in, data out, selects gating the
// drivers, no handshake anywhere.  The host asserts its selects, waits its
// access time (hundreds of nanoseconds against a 2 MHz 8080), and samples.
// Nothing acknowledges, so unlike the 1801RE2 sibling project serving uses
// no PIO at all -- just a loop that must always be faster than the chip it
// replaces:
//
//     read GPIO -> index a 64 KB table -> write data, gate directions
//
// The Fire 24 pin map (identical on both revisions) makes the middle step
// free.  Data lands on GPIO 0..7; address, selects and the X pads land on
// GPIO 8..23.  So the top 16 bits of one GPIO read are the complete
// question, and a table built at boot holds the answer for every one of
// them, pre-scrambled into drive order.
// The loop is a handful of instructions, comfortably inside a 2616's access
// time at 150 MHz.
//
// The 8 KB monitor lives in four 2616s and the socket carries A0..A10 -- but
// the PMD 85-3 schematic hands us more than the textbook says it should.
// /CS (pin 20) selects a PAIR of chips (DS4+DS5, or DS6+DS7), and PR
// (pin 18) carries A11 -- straight or inverted depending on the socket.  So
// from any one socket the board already sees 12 address bits' worth of
// information, and the bank sources are:
//
//   STATIC   One 2 KB bank, gated exactly as the original chip in this
//            socket was (/CS active AND PR selecting this chip).  A drop-in
//            replacement for one dead 2616; bank fixed at build time (the
//            board has no jumper free to carry it -- see the board header).
//   PAIR     Both banks of this socket's pair, bank bit from PR.  Replaces
//            two chips with zero wires; the pair-mate must come out.
//   FULL8K   All four banks from one socket: one flying lead brings the
//            other pair's /CS to the X1 pad.  A12 is "which select is
//            active", A11 is PR.  All three other chips must come out.
//   HOTSPOT  Reads of magic addresses in the served window switch between
//            four images of this socket's bank.  Zero wires; only software
//            written to touch the hotspots can steer it.
//
// STATIC, PAIR and FULL8K bake the entire drive decision into a 16-bit-entry
// table (bit 8 = drive), because every input to that decision lives inside
// the table index.  HOTSPOT switches whole tables at runtime, which a baked
// bit cannot express, so it keeps 8-bit tables and a mask compare.

#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "pico/bootrom.h"
#include "pico/multicore.h"
#include "hardware/structs/sio.h"

#include "board.h"
#include "decode.h"
#include "rom_images.h"

#if MHB_BOARD_HAS_NEOPIXEL
#include "hardware/pio.h"
#include "ws2812.pio.h"
#endif

// 150 MHz is the RP2350's rated speed, and the loop fits a 2616's access time
// at it several times over.  The knob exists because the sibling project
// learned to keep it, not because it is expected to move.
#ifndef MHB_SYS_CLK_KHZ
#define MHB_SYS_CLK_KHZ  150000
#endif

#if (MHB_BANK_STATIC + MHB_BANK_PAIR + MHB_BANK_FULL8K + MHB_BANK_HOTSPOT) != 1
#error "exactly one bank source; use the CMake MHB_BANK_SOURCE option"
#endif

// First of the four hotspot addresses, as an offset inside the 2 KB window.
// base+0..base+3 select images 0..3.  The default parks them just below the
// top of the window; hotspot-aware software must treat those four bytes as
// control registers, not storage.
#ifndef MHB_HOTSPOT_BASE
#define MHB_HOTSPOT_BASE  0x7F4
#endif
static_assert(MHB_HOTSPOT_BASE + 4 <= MHB_BANK_SIZE, "hotspots beyond window");

// Bumped on every select edge.  Core 1 only writes, core 0 only reads, and a
// torn read costs nothing but a slightly wrong blink, so no synchronisation.
static volatile uint32_t g_served;

#ifndef MHB_DIAG
#define MHB_DIAG 0
#endif

#if MHB_DIAG == 2
// The index of the last access we served.  A machine that halts stops
// changing it, so whatever it holds when the bus goes quiet is the last
// thing the processor ever read -- which, looked up in the monitor image,
// says where it died and what byte killed it.
static volatile uint16_t g_last_idx;
#endif

#if MHB_DIAG == 1
// One bit per bank the machine has actually asked us for since power-on.
// Set-only, so the worst a race can do is delay a bit's appearance by one
// frame.  This is the instrument that turns "it does not boot" into a
// sentence: a machine reading banks 0 and 1 and never 2 or 3 has a
// FULL8K board whose X1 lead is not delivering the other pair's select.
static volatile uint8_t g_bank_seen;
#endif

#if MHB_BANK_HOTSPOT
static uint8_t g_lut8[MHB_BANKS][MHB_LUT_SIZE];
static uint16_t g_hs_idx[MHB_BANKS];
static uint16_t g_hs_mask;
static uint32_t g_sel_mask, g_sel_val;
#else
static uint16_t g_lut16[MHB_LUT_SIZE];
#endif

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
    // Address and select pins: inputs, always.
    for (unsigned gpio = 10; gpio < 24; gpio++) {
        gpio_init(gpio);
        gpio_set_dir(gpio, GPIO_IN);
    }

    // The X pads.  X1 is the FULL8K flying lead: /CS is active low, so the
    // pull-UP makes a detached lead read "other pair not selected" -- the
    // board then falls back to serving its own pair instead of serving
    // wrong data.  In every other mode both pads are unused; pulled so a
    // CMOS input is never left floating, and built into the tables (or
    // masked out) so their state cannot matter.
    gpio_init(GPIO_X1);
    gpio_set_dir(GPIO_X1, GPIO_IN);
    gpio_pull_up(GPIO_X1);
    gpio_init(GPIO_X2);
    gpio_set_dir(GPIO_X2, GPIO_IN);
    gpio_pull_down(GPIO_X2);

#if !MHB_BOARD_HAS_NEOPIXEL
    gpio_init(GPIO_STATUS_LED);
    gpio_set_dir(GPIO_STATUS_LED, GPIO_OUT);
    gpio_put(GPIO_STATUS_LED, STATUS_LED_OFF);
#endif
}

#if MHB_BOARD_HAS_NEOPIXEL
// The rev F status pixel: a WS2812B fed GRB, one word per update, from the
// firmware's only PIO state machine -- serving stays a CPU loop by design,
// so PIO0 is otherwise idle and a cosmetic job is welcome to it.
//
// Colours are the rev E LED semantics with one improvement the plain LED
// could not offer: "powered but nothing selecting us" gets its own colour
// instead of darkness, so a dead machine and a dead board stop looking
// identical.  Kept dim on purpose; these pixels are floodlights at 0xFF.
#define NEO_GRB(g, r, b)  (((uint32_t)(g) << 16) | ((uint32_t)(r) << 8) | (b))
#define NEO_BOOT     NEO_GRB(0x00, 0x00, 0x14)   // blue blip: firmware alive
#define NEO_SERVING  NEO_GRB(0x14, 0x00, 0x00)   // green: selects arriving
#define NEO_IDLE     NEO_GRB(0x00, 0x06, 0x00)   // faint red: powered, silent
#define NEO_OFF      NEO_GRB(0x00, 0x00, 0x00)

static void neo_init(void) {
    PIO pio = pio0;
    uint sm = 0;
    uint off = pio_add_program(pio, &ws2812_program);
    pio_gpio_init(pio, GPIO_NEOPIXEL);
    pio_sm_set_consecutive_pindirs(pio, sm, GPIO_NEOPIXEL, 1, true);
    pio_sm_config c = ws2812_program_get_default_config(off);
    sm_config_set_sideset_pins(&c, GPIO_NEOPIXEL);
    sm_config_set_out_shift(&c, false, true, 24);    // MSB first, autopull 24
    sm_config_set_fifo_join(&c, PIO_FIFO_JOIN_TX);
    // 10 PIO cycles per bit at 800 kHz.
    sm_config_set_clkdiv(&c, (float)clock_get_hz(clk_sys) / (800000.0f * 10));
    pio_sm_init(pio, sm, off, &c);
    pio_sm_set_enabled(pio, sm, true);
}

static void neo_put(uint32_t grb) {
    pio_sm_put_blocking(pio0, 0, grb << 8u);
}
#endif

static void build_tables(void) {
#if MHB_BANK_HOTSPOT
    for (unsigned b = 0; b < MHB_BANKS; b++) {
        mhb_build_lut8(g_lut8[b], mhb_banks, mhb_bank_present, b);
    }
    // The hotspot matcher compares under address plus X-pad bits.  The pads
    // are parked at known levels in this mode (X1 pulled high, X2 low), so
    // they are encoded into the expected values rather than masked out --
    // a pad something has wired up anyway then breaks the match, instead of
    // silently aliasing the hotspots.
    g_hs_mask = mhb_index_addr_mask() | MHB_IDX_X1 | MHB_IDX_X2;
    for (unsigned b = 0; b < MHB_BANKS; b++) {
        g_hs_idx[b] = mhb_index_of(MHB_HOTSPOT_BASE + b) | MHB_IDX_X1;
    }
    // Gate as the original chip in this socket would: /CS active and PR at
    // the level that selects this socket's bank.  bank bit 0 XOR the
    // socket's PR inversion gives the level PR reads when selected.
    unsigned bank = MHB_SOCKET_BANK & 3u;
    bool pr_high = ((bank & 1) ^ (MHB_PR_INVERT ? 1u : 0u)) != 0;
    mhb_select_masks(false, pr_high, &g_sel_mask, &g_sel_val);
#else
    mhb_lut16_cfg_t cfg = {
        .socket_pair = MHB_SOCKET_PAIR,
        .pr_invert   = MHB_PR_INVERT,
        .use_x1      = MHB_BANK_FULL8K,
        .fixed_bank  = -1,
        .ignore_pr   = false,
    };
#if MHB_BANK_STATIC
    cfg.fixed_bank = (int)(MHB_SOCKET_BANK & 3u);
    cfg.socket_pair = ((unsigned)cfg.fixed_bank >> 1) & 1;
    cfg.ignore_pr = MHB_STATIC_IGNORE_PR;
#endif
    mhb_build_lut16(g_lut16, mhb_banks, mhb_bank_present, &cfg);
#endif
}

// ---------------------------------------------------------------------------
// Serving
// ---------------------------------------------------------------------------

#if MHB_BANK_HOTSPOT
static void __not_in_flash_func(serve_forever)(void) {
    const uint8_t *lut = g_lut8[0];
    bool driving = false;

    for (;;) {
        uint32_t in  = sio_hw->gpio_in;
        uint32_t idx = (in >> 8) & 0xFFFFu;
        uint32_t byte = lut[idx];

        if ((in & g_sel_mask) == g_sel_val) {
            // Data before direction: the bus must never see a stale byte
            // driven.  The togl dance writes only GPIO 0..7.
            sio_hw->gpio_togl = (sio_hw->gpio_out ^ byte) & 0xFFu;
            if (!driving) {
                sio_hw->gpio_oe_set = 0xFFu;
                driving = true;
                g_served++;
            }
            // A read of a hotspot switches images for the *next* access;
            // the data for this one came from the old image, which is the
            // convention every hotspot-banked cartridge scheme uses.
            // Re-matching on later loop iterations of the same read is
            // idempotent: each hotspot names a fixed image.
            uint32_t a = idx & g_hs_mask;
            if (a == g_hs_idx[0])      lut = g_lut8[0];
            else if (a == g_hs_idx[1]) lut = g_lut8[1];
            else if (a == g_hs_idx[2]) lut = g_lut8[2];
            else if (a == g_hs_idx[3]) lut = g_lut8[3];
        } else if (driving) {
            sio_hw->gpio_oe_clr = 0xFFu;
            driving = false;
        }
    }
}
#else
static void __not_in_flash_func(serve_forever)(void) {
    bool driving = false;

    for (;;) {
        uint32_t in  = sio_hw->gpio_in;
        uint32_t idx = (in >> 8) & 0xFFFFu;
        uint32_t v   = g_lut16[idx];

        if (v & MHB_LUT16_DRIVE) {
            // Data before direction, as above.  The drive decision -- which
            // select is active, which bank that names, whether that bank is
            // present -- was baked into bit 8 at boot; nothing is computed
            // here.
            sio_hw->gpio_togl = (sio_hw->gpio_out ^ v) & 0xFFu;
            if (!driving) {
                sio_hw->gpio_oe_set = 0xFFu;
                driving = true;
                g_served++;
#if MHB_DIAG == 1
                // The bank is already in the word we looked up; see decode.h.
                g_bank_seen |= (uint8_t)(1u << ((v & MHB_LUT16_BANK_MASK)
                                                >> MHB_LUT16_BANK_SHIFT));
#elif MHB_DIAG == 2
                // One store.  Decoding it into bank+address costs two loops
                // and belongs on core 0, which has nothing else to do.
                g_last_idx = (uint16_t)idx;
#endif
            }
        } else if (driving) {
            sio_hw->gpio_oe_clr = 0xFFu;
            driving = false;
        }
    }
}
#endif

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

#if MHB_DIAG == 2
    // Kill-address frame: the last monitor offset the processor read.
    //
    // Thirteen bits, most significant first: two of bank, then A10 down to
    // A0.  Long green is a 1, short red a 0, with a blue blink every four
    // pulses so a reader never has to hold a running count.  Look the
    // resulting offset up in the monitor image and the byte at it is the
    // last instruction the processor ever fetched.
#if MHB_BOARD_HAS_NEOPIXEL
    neo_init();
#endif
    while (true) {
        uint16_t idx = g_last_idx;
        unsigned addr = mhb_addr_from_index(idx);
        unsigned bank = (g_lut16[idx] & MHB_LUT16_BANK_MASK)
                        >> MHB_LUT16_BANK_SHIFT;
        unsigned off = (bank << 11) | addr;   // 13 bits
#if MHB_BOARD_HAS_NEOPIXEL
        neo_put(NEO_BOOT);
        sleep_ms(1800);
        neo_put(NEO_OFF);
        sleep_ms(700);
        for (int b = 12; b >= 0; b--) {
            bool one = (off >> b) & 1;
            neo_put(one ? NEO_SERVING : NEO_GRB(0x00, 0x14, 0x00));
            sleep_ms(one ? 650 : 150);
            neo_put(NEO_OFF);
            sleep_ms(350);
            if (b % 4 == 0 && b) {          // group separator
                neo_put(NEO_BOOT);
                sleep_ms(120);
                neo_put(NEO_OFF);
                sleep_ms(350);
            }
        }
        sleep_ms(1500);
#else
        gpio_put(GPIO_STATUS_LED, STATUS_LED_ON);
        sleep_ms(1800);
        gpio_put(GPIO_STATUS_LED, STATUS_LED_OFF);
        sleep_ms(700);
        for (int b = 12; b >= 0; b--) {
            gpio_put(GPIO_STATUS_LED, STATUS_LED_ON);
            sleep_ms(((off >> b) & 1) ? 650 : 100);
            gpio_put(GPIO_STATUS_LED, STATUS_LED_OFF);
            sleep_ms((b % 4 == 0 && b) ? 800 : 350);
        }
        sleep_ms(1500);
#endif
    }
#endif // MHB_DIAG == 2

#if MHB_DIAG == 1
    // Coverage frame: which banks has this machine actually read?
    //
    // A board that does not boot a machine gives one bit of information.
    // This gives four, and they are the four that separate the candidate
    // faults: a FULL8K board whose X1 lead is not delivering the other
    // pair's select serves banks 0 and 1 and never 2 or 3, and says so
    // here rather than as a machine that sits there.
    //
    //   blue, long        start of frame
    //   then four pulses, bank 0 first:
    //     green, long     this bank has been read since power-on
    //     red, short      never read
    //
    // Latched since power-on, never cleared: "did this ever happen" is the
    // question, and a frame that changes between repeats is itself worth
    // seeing (the machine got further this time).
#if MHB_BOARD_HAS_NEOPIXEL
    neo_init();
#endif
    while (true) {
        uint8_t seen = g_bank_seen;
#if MHB_BOARD_HAS_NEOPIXEL
        neo_put(NEO_BOOT);
        sleep_ms(1500);
        neo_put(NEO_OFF);
        sleep_ms(600);
        for (unsigned b = 0; b < MHB_BANKS; b++) {
            bool hit = (seen >> b) & 1;
            neo_put(hit ? NEO_SERVING : NEO_GRB(0x00, 0x14, 0x00));
            sleep_ms(hit ? 700 : 150);
            neo_put(NEO_OFF);
            sleep_ms(400);
        }
        sleep_ms(1200);
#else
        // Plain LED: the same frame, long/short, with a lit marker.
        gpio_put(GPIO_STATUS_LED, STATUS_LED_ON);
        sleep_ms(1500);
        gpio_put(GPIO_STATUS_LED, STATUS_LED_OFF);
        sleep_ms(600);
        for (unsigned b = 0; b < MHB_BANKS; b++) {
            gpio_put(GPIO_STATUS_LED, STATUS_LED_ON);
            sleep_ms(((seen >> b) & 1) ? 700 : 100);
            gpio_put(GPIO_STATUS_LED, STATUS_LED_OFF);
            sleep_ms(400);
        }
        sleep_ms(1200);
#endif
    }
#endif // MHB_DIAG == 1

    // Core 0 turns the served-cycle count into something visible.  Installed
    // in a machine that will not boot, the useful question is whether the
    // board is being selected at all.
#if MHB_BOARD_HAS_NEOPIXEL
    //   blue blip at power-on   firmware started
    //   green                   selects arriving, serving normally
    //   faint red               powered but nothing is selecting us
    neo_init();
    neo_put(NEO_BOOT);
    sleep_ms(250);
    uint32_t last_served = 0;
    while (true) {
        uint32_t served = g_served;
        neo_put(served != last_served ? NEO_SERVING : NEO_IDLE);
        last_served = served;
        sleep_ms(100);
    }
#else
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
#endif
}
