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

When a chip has decayed far enough that counting *bytes* stops working --
past about a quarter of them wrong, everything looks equally unlike every
bank -- identification falls back to counting **bits**, which keeps working
almost to the end.  An MHB 2616 in this machine dies by losing set bits and
never by gaining them, so a dump of bank B can hold no bit that B does not
also hold.  One bank leaves zero such impossible bits and every other bank
leaves hundreds, which names the chip from a few dozen survivors: the
module's fourth chip was identified from 32 surviving bits out of 16384,
0.4% of the die, and still unambiguously.
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

# Bit-decay identification needs enough surviving bits that agreement cannot
# be luck.  A wrong bank sets roughly half the bits the right one does, so a
# coincidental clean pass runs at about 2**-k for k survivors; 32 puts that
# near one in four billion per bank, which is decisive against the handful of
# banks any reference image has.  Chips below this are reported, not named.
DECAY_MIN_EVIDENCE = 32


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


def decay_scores(raw: bytes, bank: bytes):
    """Bits in `raw` that no amount of decay could explain, both directions.

    Returns (falling, rising).  `falling` counts bits the dump has set that
    the bank does not -- impossible if the chip only ever loses set bits.
    `rising` counts the mirror case, for a part that rots towards FF instead.
    Whichever direction a chip is dying in, the right bank scores zero on it.
    """
    falling = sum(bin(x & ~y & 0xFF).count("1") for x, y in zip(raw, bank))
    rising = sum(bin(~x & y & 0xFF).count("1") for x, y in zip(raw, bank))
    return falling, rising


def identify_by_decay(raw: bytes, ref_banks: list) -> bool:
    """Name a bank from surviving bits alone.  True if it succeeded."""
    # Evidence is whatever the decay did not take: survivors under a falling
    # model, bits still clear under a rising one.  A dump decayed to all-00
    # (or all-FF) matches every bank trivially and must not be named, which
    # is exactly what having no evidence means.
    scored = [decay_scores(raw, bank) for bank in ref_banks]
    for direction, index, evidence in (
            ("losing set bits", 0, sum(bin(x).count("1") for x in raw)),
            ("gaining set bits", 1, sum(8 - bin(x).count("1") for x in raw))):
        impossible = [s[index] for s in scored]
        clean = [b for b, n in enumerate(impossible) if n == 0]
        if len(clean) != 1:
            continue
        best = clean[0]
        counts = "  ".join(f"bank {b}: {n}" for b, n in enumerate(impossible))
        print(f"  bits inconsistent with a chip {direction} -- {counts}")
        if evidence < DECAY_MIN_EVIDENCE:
            print(f"  -> bank {best} is the only bank consistent with this "
                  f"dump, but only {evidence} bits survive to say so "
                  f"(under {DECAY_MIN_EVIDENCE}); too little to name it")
            return False
        runner_up = min(n for b, n in enumerate(impossible) if b != best) \
            if len(impossible) > 1 else None
        gap = "" if runner_up is None else \
            f", against {runner_up} for the next-best bank"
        print(f"  -> bank {best}, from {evidence} surviving bits alone"
              f"{gap} -- too far gone to match by bytes, but not by bits")
        return True
    return False


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
        elif not identify_by_decay(raw, ref_banks):
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
