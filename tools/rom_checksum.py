#!/usr/bin/env python3
"""Per-bank checksums of a monitor image or chip dump.

Prints, for each 2 KB bank present in the file: the 8-bit sum, the 16-bit
sum, and CRC32.  Two uses:

  - comparing a dump of an original chip against a reference image before
    trusting either;
  - after a board-served dump, confirming the machine is being fed
    byte-identical data (the PMD 85-3 monitor runs its own ROM integrity
    test at startup; if that fails while these sums match the reference,
    the fault is in serving, not contents -- the exact split the 1801RE2
    project spent weeks on).

    ./rom_checksum.py pmd85-3.bin
    ./rom_checksum.py --compare pmd85-3.bin dump.bin

--identify matches chip dumps against a reference image's banks, and it
tolerates degraded chips: a checksum can only say "not identical", but a
dump that is 98% one bank and noise elsewhere IS that bank, and naming it
is the whole point when the chip is dying.  Mismatch counts against every
bank are printed, so a marginal verdict is visible as marginal:

    ./rom_checksum.py --identify pmd85-3.bin ds4_dump.bin ds6_dump.bin
"""

import argparse
import sys
import zlib
from pathlib import Path

BANK_SIZE = 2048

# A clear identification: this close to one bank, at least this far from
# every other.  The monitor's banks differ in thousands of bytes, so the gap
# between "right bank through a failing chip" and "wrong bank" is enormous;
# these thresholds only exist to keep a truly destroyed dump from getting a
# confident label.
IDENT_MAX_BAD = BANK_SIZE // 4          # <= 25% bytes wrong of the best bank
IDENT_MIN_GAP = BANK_SIZE // 8          # ...and the runner-up clearly worse


def banks_of(raw: bytes):
    for off in range(0, len(raw), BANK_SIZE):
        yield off // BANK_SIZE, raw[off:off + BANK_SIZE]


def report(path: Path) -> None:
    raw = path.read_bytes()
    print(f"{path.name}: {len(raw)} bytes")
    for b, data in banks_of(raw):
        s8 = sum(data) & 0xFF
        s16 = sum(data) & 0xFFFF
        crc = zlib.crc32(data)
        print(f"  bank {b}: sum8 0x{s8:02X}  sum16 0x{s16:04X}  crc32 0x{crc:08X}")


def compare(ref: Path, dump: Path) -> int:
    a, b = ref.read_bytes(), dump.read_bytes()
    n = min(len(a), len(b))
    if len(a) != len(b):
        print(f"note: sizes differ ({len(a)} vs {len(b)}); comparing first {n}")
    diffs = [i for i in range(n) if a[i] != b[i]]
    if not diffs:
        print(f"identical over {n} bytes")
        return 0
    print(f"{len(diffs)} bytes differ; first ten:")
    for i in diffs[:10]:
        print(f"  0x{i:04X} (bank {i // BANK_SIZE}): "
              f"{ref.name} 0x{a[i]:02X}, {dump.name} 0x{b[i]:02X}")
    return 1


def identify(ref_path: Path, dumps: list) -> int:
    ref = ref_path.read_bytes()
    if len(ref) % BANK_SIZE:
        print(f"error: {ref_path.name} is {len(ref)} bytes, "
              f"not a whole number of {BANK_SIZE}-byte banks", file=sys.stderr)
        return 1
    ref_banks = [data for _, data in banks_of(ref)]

    failed = False
    for path in dumps:
        raw = path.read_bytes()
        if len(raw) != BANK_SIZE:
            print(f"{path.name}: {len(raw)} bytes, expected {BANK_SIZE} -- skipped")
            failed = True
            continue
        scores = [sum(x != y for x, y in zip(raw, bank)) for bank in ref_banks]
        order = sorted(range(len(scores)), key=lambda b: scores[b])
        best, second = order[0], (order[1] if len(order) > 1 else None)
        counts = "  ".join(f"bank {b}: {scores[b]}" for b in range(len(scores)))
        print(f"{path.name}: mismatched bytes vs {ref_path.name} -- {counts}")
        if scores[best] == 0:
            print(f"  -> bank {best}, exact")
        elif (scores[best] <= IDENT_MAX_BAD
              and (second is None or scores[second] - scores[best] >= IDENT_MIN_GAP)):
            print(f"  -> bank {best}, with {scores[best]} bad bytes "
                  f"({100 * scores[best] // BANK_SIZE}% of the chip) -- a "
                  f"degraded chip, but unambiguously this bank")
        else:
            print("  -> no clear match; this dump does not resemble any bank "
                  "of the reference")
            failed = True
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--compare", action="store_true",
                    help="byte-compare exactly two files instead of summing")
    ap.add_argument("--identify", action="store_true",
                    help="first file is the reference image; name the bank "
                         "each further file (a 2 KB chip dump) best matches")
    args = ap.parse_args()

    if args.compare and args.identify:
        print("error: --compare and --identify are exclusive", file=sys.stderr)
        return 1
    if args.compare:
        if len(args.files) != 2:
            print("error: --compare takes exactly two files", file=sys.stderr)
            return 1
        return compare(args.files[0], args.files[1])
    if args.identify:
        if len(args.files) < 2:
            print("error: --identify takes a reference and at least one dump",
                  file=sys.stderr)
            return 1
        return identify(args.files[0], args.files[1:])

    for path in args.files:
        report(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
