#!/usr/bin/env python3
"""An 8080 disassembler, for reading the PMD 85 monitor.

The kill-address frame gives a monitor offset; this says what is there.
Offsets are file offsets into an 8 KB monitor image by default, and
`--base` sets the address they are labelled with -- 0xE000 for a PMD 85-3
monitor, which is where the machine runs it from once the startup mirror
map has been cleared.

    ./i8080dis.py monit3B.rom --at 0x0000 --count 24
    ./i8080dis.py monit3B.rom --at 0x1A3C --base 0xE000 --around
    ./i8080dis.py monit3B.rom --find-halt

The sibling project wanted the same thing for a PDP-11 and wrote
pdp11dis.py; this is that idea for an 8080, and it exists because reading
a monitor by eye out of a hex dump is how wrong conclusions get made.
"""

import argparse
import sys
from pathlib import Path

R = ["B", "C", "D", "E", "H", "L", "M", "A"]
RP = ["B", "D", "H", "SP"]
RP_PUSH = ["B", "D", "H", "PSW"]
CC = ["NZ", "Z", "NC", "C", "PO", "PE", "P", "M"]
ALU = ["ADD", "ADC", "SUB", "SBB", "ANA", "XRA", "ORA", "CMP"]
ALU_I = ["ADI", "ACI", "SUI", "SBI", "ANI", "XRI", "ORI", "CPI"]

# Opcodes that do not fit a pattern, by exact value.
ODD = {
    0x00: ("NOP", 1), 0x07: ("RLC", 1), 0x0F: ("RRC", 1),
    0x17: ("RAL", 1), 0x1F: ("RAR", 1), 0x27: ("DAA", 1),
    0x2F: ("CMA", 1), 0x37: ("STC", 1), 0x3F: ("CMC", 1),
    0x76: ("HLT", 1), 0xC9: ("RET", 1), 0xC3: ("JMP a16", 3),
    0xCD: ("CALL a16", 3), 0xD3: ("OUT d8", 2), 0xDB: ("IN d8", 2),
    0xE3: ("XTHL", 1), 0xE9: ("PCHL", 1), 0xEB: ("XCHG", 1),
    0xF3: ("DI", 1), 0xFB: ("EI", 1), 0xF9: ("SPHL", 1),
    0x22: ("SHLD a16", 3), 0x2A: ("LHLD a16", 3),
    0x32: ("STA a16", 3), 0x3A: ("LDA a16", 3),
    0x02: ("STAX B", 1), 0x12: ("STAX D", 1),
    0x0A: ("LDAX B", 1), 0x1A: ("LDAX D", 1),
}


def decode(mem: bytes, i: int):
    """(text, length) for the instruction at offset i."""
    op = mem[i]

    def imm16():
        return mem[i + 1] | (mem[i + 2] << 8) if i + 2 < len(mem) else 0

    def imm8():
        return mem[i + 1] if i + 1 < len(mem) else 0

    if op in ODD:
        text, n = ODD[op]
        if "a16" in text:
            return text.replace("a16", f"{imm16():04X}h"), 3
        if "d8" in text:
            return text.replace("d8", f"{imm8():02X}h"), 2
        return text, n

    hi, lo = op >> 6, op & 7
    mid = (op >> 3) & 7

    if hi == 1:                                   # 0x40-0x7F: MOV (0x76 handled)
        return f"MOV {R[mid]},{R[lo]}", 1
    if hi == 2:                                   # 0x80-0xBF: ALU A,r
        return f"{ALU[mid]} {R[lo]}", 1
    if hi == 0:
        if lo == 0:
            return "NOP*", 1                      # undocumented NOPs
        if lo == 1:
            if op & 8:
                return f"DAD {RP[mid >> 1]}", 1
            return f"LXI {RP[mid >> 1]},{imm16():04X}h", 3
        if lo == 3:
            return (f"DCX {RP[mid >> 1]}" if op & 8 else f"INX {RP[mid >> 1]}"), 1
        if lo == 4:
            return f"INR {R[mid]}", 1
        if lo == 5:
            return f"DCR {R[mid]}", 1
        if lo == 6:
            return f"MVI {R[mid]},{imm8():02X}h", 2
    if hi == 3:
        if lo == 0:
            return f"R{CC[mid]}", 1
        if lo == 1:
            return f"POP {RP_PUSH[mid >> 1]}", 1 if not (op & 8) else 1
        if lo == 2:
            return f"J{CC[mid]} {imm16():04X}h", 3
        if lo == 4:
            return f"C{CC[mid]} {imm16():04X}h", 3
        if lo == 5:
            if not (op & 8):
                return f"PUSH {RP_PUSH[mid >> 1]}", 1
            return f"CALL* {imm16():04X}h", 3
        if lo == 6:
            return f"{ALU_I[mid]} {imm8():02X}h", 2
        if lo == 7:
            return f"RST {mid}", 1
    return f"DB {op:02X}h", 1


def listing(mem: bytes, start: int, count: int, base: int) -> None:
    i = start
    for _ in range(count):
        if i >= len(mem):
            break
        text, n = decode(mem, i)
        raw = " ".join(f"{b:02X}" for b in mem[i:i + n])
        print(f"  {base + i:04X}  (+{i:04X})  {raw:<9}  {text}")
        i += n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path)
    ap.add_argument("--at", type=lambda s: int(s, 0), default=0,
                    help="offset into the image (not the CPU address)")
    ap.add_argument("--count", type=int, default=16)
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0xE000,
                    help="CPU address the image is based at (default E000)")
    ap.add_argument("--around", action="store_true",
                    help="also list the 16 bytes before --at, unaligned")
    ap.add_argument("--find-halt", action="store_true",
                    help="list every 0x76 byte in the image, with context")
    args = ap.parse_args()

    mem = args.image.read_bytes()

    if args.find_halt:
        hits = [i for i, b in enumerate(mem) if b == 0x76]
        print(f"{len(hits)} bytes of 0x76 in {args.image.name} "
              f"(most will be MOV M,M operands or data, not reachable HLTs):")
        for i in hits:
            print(f"  +{i:04X}  (CPU {args.base + i:04X})")
        return 0

    if args.around and args.at >= 16:
        print("  -- preceding bytes, alignment unknown --")
        listing(mem, args.at - 16, 8, args.base)
        print("  -- from the requested offset --")
    listing(mem, args.at, args.count, args.base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
