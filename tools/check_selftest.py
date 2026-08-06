#!/usr/bin/env python3
"""Check a dump of the self-test pattern and diagnose what is wrong.

Feed it 2048 bytes (one bank) or 8192 bytes (all four, in bank order), as
read back through a ROM reader from a board serving the selftest image:

    ./check_selftest.py dump.bin              # expects bank 0 first
    ./check_selftest.py --bank 2 dump.bin     # a single bank, number 2

A clean dump prints one line per bank and exits 0.  A dirty one is worked
into the two faults that produce structured error patterns:

  data lines    every mismatch XORed against its expectation; a bit set in
                every mismatch is a stuck or miswired data line.
  address lines a byte that is exactly the pattern byte for a *different*
                address differs from it in a consistent set of address bits;
                those bits name the miswired or stuck address lines.

Anything unstructured is listed raw, first ten mismatches, which is usually
enough to see what happened (a shifted dump announces itself in the marker
nibbles immediately).
"""

import argparse
import sys
from pathlib import Path

import selftest

BANK_SIZE = selftest.BANK_SIZE


def decode(byte_even: int, byte_odd: int):
    """Token recovered from a byte pair, or None if the marker is wrong."""
    if byte_odd & 0xF0 != selftest.MARKER:
        return None
    return ((byte_odd & 0x0F) << 8) | byte_even


def check_bank(data: bytes, bank: int) -> list:
    """All mismatches as (addr, got, want)."""
    bad = []
    for a in range(BANK_SIZE):
        want = selftest.byte_at(bank, a)
        if data[a] != want:
            bad.append((a, data[a], want))
    return bad


def diagnose(bad: list, bank: int) -> None:
    data_and = 0xFF
    data_or = 0
    addr_and = 0xFFF
    addr_or = 0
    addr_evidence = 0
    for a, got, want in bad:
        diff = got ^ want
        data_and &= diff
        data_or |= diff
        # Is `got` the right byte for some other address in some bank?  Even
        # and odd addresses encode different token halves, so only compare
        # against same-parity addresses.
        for b in range(4):
            for cand in range(a & 1, BANK_SIZE, 2):
                if selftest.byte_at(b, cand) == got:
                    d = cand ^ a
                    if d:
                        addr_and &= d
                        addr_or |= d
                        addr_evidence += 1
                    break
            else:
                continue
            break

    if data_and:
        bits = [i for i in range(8) if data_and & (1 << i)]
        print(f"  bank {bank}: data line fault -- D{', D'.join(map(str, bits))} "
              f"wrong in every one of {len(bad)} mismatches")
    elif addr_evidence == len(bad) and addr_and:
        bits = [i for i in range(11) if addr_and & (1 << i)]
        print(f"  bank {bank}: address line fault -- A{', A'.join(map(str, bits))} "
              f"consistent across {len(bad)} mismatches")
    else:
        print(f"  bank {bank}: {len(bad)} mismatches, first ten:")
        for a, got, want in bad[:10]:
            print(f"    0x{a:03X}: got 0x{got:02X}, want 0x{want:02X}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", type=Path)
    ap.add_argument("--bank", type=int, default=None,
                    help="the dump is this single bank (default: bank 0, or "
                         "all four if the file is 8192 bytes)")
    args = ap.parse_args()

    raw = args.dump.read_bytes()
    if len(raw) == 4 * BANK_SIZE and args.bank is None:
        pairs = [(raw[b * BANK_SIZE:(b + 1) * BANK_SIZE], b) for b in range(4)]
    elif len(raw) == BANK_SIZE:
        pairs = [(raw, args.bank or 0)]
    else:
        print(f"error: {args.dump.name} is {len(raw)} bytes; "
              f"expected {BANK_SIZE} or {4 * BANK_SIZE}", file=sys.stderr)
        return 1

    failed = False
    for data, bank in pairs:
        bad = check_bank(data, bank)
        if not bad:
            print(f"  bank {bank}: clean")
        else:
            failed = True
            diagnose(bad, bank)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
