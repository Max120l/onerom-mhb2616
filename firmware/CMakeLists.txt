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

pico_generate_pio_header(mhb2616 ${CMAKE_CURRENT_LIST_DIR}/ws2812.pio)

# Which Fire 24 revision.  The socket map is identical; what differs is the
# status indicator (rev F: WS2812B pixel on GPIO 29) and the jumper
# positions -- including the RECOVERY jumper, which is jumper 1 on both
# boards but a different GPIO (rev E: 25, rev F: 26).  See board.h.
set(MHB_BOARD "FIRE24E" CACHE STRING "Board: FIRE24E or FIRE24F")
set_property(CACHE MHB_BOARD PROPERTY STRINGS FIRE24E FIRE24F)
if(NOT MHB_BOARD MATCHES "^(FIRE24E|FIRE24F)$")
    message(FATAL_ERROR "MHB_BOARD must be FIRE24E or FIRE24F")
endif()
if(MHB_BOARD STREQUAL "FIRE24F")
    target_compile_definitions(mhb2616 PRIVATE MHB_BOARD_FIRE24F=1)
else()
    target_compile_definitions(mhb2616 PRIVATE MHB_BOARD_FIRE24F=0)
endif()

# Where the bank bits come from.  See main.c for what each means; the short
# version, keyed to the PMD 85-3 schematic:
#   STATIC   one 2 KB bank, gated like the original chip -- drop-in
#            replacement, zero wires (the default: it is the only mode that
#            is safe whatever else is still in its socket)
#   PAIR     both banks of this socket's pair, bank bit from PR -- replaces
#            two chips, zero wires
#   FULL8K   all four banks -- one flying lead (other pair's /CS to X1)
#   HOTSPOT  four images of one bank, switched by magic reads -- for
#            hotspot-aware software only
set(MHB_BANK_SOURCE "STATIC" CACHE STRING
    "Bank source: STATIC, PAIR, FULL8K or HOTSPOT")
set_property(CACHE MHB_BANK_SOURCE PROPERTY STRINGS STATIC PAIR FULL8K HOTSPOT)
if(NOT MHB_BANK_SOURCE MATCHES "^(STATIC|PAIR|FULL8K|HOTSPOT)$")
    message(FATAL_ERROR "MHB_BANK_SOURCE must be STATIC, PAIR, FULL8K or HOTSPOT")
endif()
foreach(src STATIC PAIR FULL8K HOTSPOT)
    if(MHB_BANK_SOURCE STREQUAL src)
        target_compile_definitions(mhb2616 PRIVATE MHB_BANK_${src}=1)
    else()
        target_compile_definitions(mhb2616 PRIVATE MHB_BANK_${src}=0)
    endif()
endforeach()

# Which pair this socket's /CS covers: 0 = banks 0/1, 1 = banks 2/3.  Used by
# PAIR and FULL8K; STATIC and HOTSPOT derive it from the bank number.  Which
# physical pair (DS4+DS5 or DS6+DS7) is which half of the monitor is settled
# by the checksum test in docs/PMD85-3.md, not assumed.
set(MHB_SOCKET_PAIR "0" CACHE STRING "This socket's pair: 0 or 1")
target_compile_definitions(mhb2616 PRIVATE MHB_SOCKET_PAIR=${MHB_SOCKET_PAIR})

# PR (pin 18) carries A11 straight into two sockets (DS4, DS6) and inverted
# into the other two (DS5, DS7).  Set ON for a socket of the inverted kind.
# If the machine boots with the two banks of a pair swapped, this is the
# switch that was wrong -- flip it, do not reorder the image.
option(MHB_PR_INVERT "PR carries inverted A11 in this socket (DS5/DS7)" OFF)
target_compile_definitions(mhb2616 PRIVATE MHB_PR_INVERT=$<BOOL:${MHB_PR_INVERT}>)

# STATIC/HOTSPOT: which bank this socket holds, 0-3.  Build-time only: of
# the board's four jumpers, two are hard-wired to the SWD pads and cannot
# be read (confirmed on hardware, see docs/BOARD-NOTES.md) and one of the
# remaining two is the recovery jumper.  Note bank bit 1 must agree with
# the socket's pair and bit 0 with its PR sense -- the socket table in
# docs/PMD85-3.md hands you all three together.
set(MHB_SOCKET_BANK "0" CACHE STRING "Bank this socket holds, 0-3")
if(NOT MHB_SOCKET_BANK MATCHES "^[0-3]$")
    message(FATAL_ERROR "MHB_SOCKET_BANK must be 0, 1, 2 or 3 "
        "(jumper-selected banks are not available: see docs/BOARD-NOTES.md)")
endif()
target_compile_definitions(mhb2616 PRIVATE MHB_SOCKET_BANK=${MHB_SOCKET_BANK})

# STATIC only: ignore PR and answer whenever /CS is active.  For reading the
# board through a 2716 programmer, which parks pin 18 at one level.  Never in
# a machine -- the pair-mate chip answers the other PR state, and this would
# fight it.
option(MHB_STATIC_IGNORE_PR "Serve on /CS alone (programmer reads only)" OFF)
target_compile_definitions(mhb2616 PRIVATE
    MHB_STATIC_IGNORE_PR=$<BOOL:${MHB_STATIC_IGNORE_PR}>)
if(MHB_STATIC_IGNORE_PR AND NOT MHB_BANK_SOURCE STREQUAL "STATIC")
    message(WARNING "MHB_STATIC_IGNORE_PR only applies to STATIC; ignored")
endif()

# HOTSPOT: offset of the first hotspot inside the 2 KB window; base+0..3
# select images 0..3.
set(MHB_HOTSPOT_BASE "" CACHE STRING "First hotspot offset (default 0x7F4)")
if(MHB_HOTSPOT_BASE)
    target_compile_definitions(mhb2616 PRIVATE MHB_HOTSPOT_BASE=${MHB_HOTSPOT_BASE})
endif()

# Diagnostic build: instead of the ordinary status light, blink a frame
# saying which of the four banks the machine has actually read since
# power-on.  Off by default -- a board left in a machine should be a ROM
# and nothing else.  See main.c for how to read the frame.
option(MHB_DIAG "Blink per-bank coverage instead of the status light" OFF)
target_compile_definitions(mhb2616 PRIVATE MHB_DIAG=$<BOOL:${MHB_DIAG}>)

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
    hardware_pio
)

pico_add_extra_outputs(mhb2616)
