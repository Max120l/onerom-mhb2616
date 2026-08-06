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
"""

import argparse
import sys
import zlib
from pathlib import Path

BANK_SIZE = 2048


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--compare", action="store_true",
                    help="byte-compare exactly two files instead of summing")
    args = ap.parse_args()

    if args.compare:
        if len(args.files) != 2:
            print("error: --compare takes exactly two files", file=sys.stderr)
            return 1
        return compare(args.files[0], args.files[1])

    for path in args.files:
        report(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
