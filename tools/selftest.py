#!/usr/bin/env python3
"""The self-test pattern: every byte names its own address and bank.

The point is to catch wiring and firmware faults before a real monitor image
goes anywhere near a machine.  A dump of a bank read back through a ROM
reader (or a logic probe walking the socket by hand) can be checked with
check_selftest.py, which then says *which* address or data line is wrong
rather than merely that something is.

The encoding packs a 12-bit token -- word index in the bank (10 bits) plus
bank number (2 bits) -- into byte pairs:

    even address:  token bits 0..7
    odd  address:  0x50 | token bits 8..11

The 0x5 marker nibble makes odd bytes distinguishable from even ones on
sight, so a dump that is shifted by one byte, or a data bus wired backwards,
announces itself immediately.
"""

BANK_SIZE = 2048
MARKER = 0x50


def token(bank: int, addr: int) -> int:
    return ((bank & 3) << 10) | ((addr >> 1) & 0x3FF)


def byte_at(bank: int, addr: int) -> int:
    t = token(bank, addr)
    if addr & 1:
        return MARKER | (t >> 8)
    return t & 0xFF


def image(bank: int) -> bytes:
    return bytes(byte_at(bank, a) for a in range(BANK_SIZE))
