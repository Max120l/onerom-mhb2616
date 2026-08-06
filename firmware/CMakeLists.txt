cmake_minimum_required(VERSION 3.13)

# One ROM Fire 24 rev E carries an RP2354A: an RP2350A with 2 MB of stacked
# flash, so there is no external flash chip to configure.
set(PICO_PLATFORM rp2350)
set(PICO_BOARD pico2)

include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(mhb2616 C CXX ASM)
set(CMAKE_C_STANDARD 11)
pico_sdk_init()

add_executable(mhb2616
    main.c
    decode.c
    rom_images.c        # generated: tools/gen_rom_images.py
)

# Where the two bank bits come from.  See main.c for what each means; the
# short version is EXT for the stock monitor with two flying leads, HOTSPOT
# for software that knows about the hotspot, STATIC for a one-for-one 2 KB
# chip replacement.
set(MHB_BANK_SOURCE "HOTSPOT" CACHE STRING "Bank source: EXT, HOTSPOT or STATIC")
set_property(CACHE MHB_BANK_SOURCE PROPERTY STRINGS EXT HOTSPOT STATIC)
if(NOT MHB_BANK_SOURCE MATCHES "^(EXT|HOTSPOT|STATIC)$")
    message(FATAL_ERROR "MHB_BANK_SOURCE must be EXT, HOTSPOT or STATIC")
endif()
foreach(src EXT HOTSPOT STATIC)
    if(MHB_BANK_SOURCE STREQUAL src)
        target_compile_definitions(mhb2616 PRIVATE MHB_BANK_${src}=1)
    else()
        target_compile_definitions(mhb2616 PRIVATE MHB_BANK_${src}=0)
    endif()
endforeach()

# Which control pins must be active (low) for the board to drive the bus.
#
# The default honours both, which is right for a chip alone in its socket.
# It is NOT right for EXT mode: /CE on pin 18 is the per-socket decoded
# select, active only for that socket's own 2 KB -- gating 8 KB of serving on
# it would answer one window and starve three.  What pin 20 carries on the
# PMD 85-3 board decides what EXT should gate on instead, and that is an open
# hardware question (docs/PMD85-3.md).  Hence a warning rather than a silent
# per-mode default: the person flashing must decide, knowingly.
option(MHB_GATE_CE "Drive only while /CE (pin 18) is low" ON)
option(MHB_GATE_OE "Drive only while /OE (pin 20) is low" ON)
if(MHB_BANK_SOURCE STREQUAL "EXT" AND MHB_GATE_CE)
    message(WARNING "EXT bank source with MHB_GATE_CE=ON: /CE is the "
        "per-socket select and only ever covers one of the four 2 KB banks. "
        "Unless this socket's /CE turns out to cover the whole 8 KB region, "
        "build with -DMHB_GATE_CE=OFF and gate on /OE.")
endif()
if(NOT MHB_GATE_CE AND NOT MHB_GATE_OE)
    message(WARNING "Neither /CE nor /OE gates the drivers: the board will "
        "answer every address it sees.  Sane on a bench rig, a bus fight "
        "anywhere else.")
endif()
target_compile_definitions(mhb2616 PRIVATE
    MHB_GATE_CE=$<BOOL:${MHB_GATE_CE}>
    MHB_GATE_OE=$<BOOL:${MHB_GATE_OE}>)

# What socket pin 21 must read for the board to serve: IGNORE, LOW or HIGH.
# Vpp on a 2716, so IGNORE by default; see docs/MHB2616.md before changing.
set(MHB_PIN21 "IGNORE" CACHE STRING "Pin 21 gating: IGNORE, LOW or HIGH")
set_property(CACHE MHB_PIN21 PROPERTY STRINGS IGNORE LOW HIGH)
if(NOT MHB_PIN21 MATCHES "^(IGNORE|LOW|HIGH)$")
    message(FATAL_ERROR "MHB_PIN21 must be IGNORE, LOW or HIGH")
endif()
target_compile_definitions(mhb2616 PRIVATE MHB_PIN21_ROLE=MHB_PIN21_${MHB_PIN21})

# STATIC mode: fix the bank at build time instead of reading the jumpers.
# -1 (the default) means "read jumpers 2 and 3 at boot".
set(MHB_STATIC_BANK "-1" CACHE STRING "STATIC bank number 0-3, or -1 for jumpers")
target_compile_definitions(mhb2616 PRIVATE MHB_STATIC_BANK=${MHB_STATIC_BANK})

# HOTSPOT mode: offset of the first hotspot inside the 2 KB window; base+0..3
# select banks 0..3.
set(MHB_HOTSPOT_BASE "" CACHE STRING "First hotspot offset (default 0x7F4)")
if(MHB_HOTSPOT_BASE)
    target_compile_definitions(mhb2616 PRIVATE MHB_HOTSPOT_BASE=${MHB_HOTSPOT_BASE})
endif()

# System clock in kHz.  150000 is the RP2350's rated speed and the default;
# the serve loop fits a 2616's access time at it several times over.
set(MHB_SYS_CLK_KHZ "" CACHE STRING "System clock in kHz (default 150000)")
if(MHB_SYS_CLK_KHZ)
    target_compile_definitions(mhb2616 PRIVATE MHB_SYS_CLK_KHZ=${MHB_SYS_CLK_KHZ})
endif()

target_include_directories(mhb2616 PRIVATE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(mhb2616
    pico_stdlib
    pico_multicore
)

pico_add_extra_outputs(mhb2616)
