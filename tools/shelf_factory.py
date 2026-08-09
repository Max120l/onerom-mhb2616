#!/usr/bin/env python3
"""Audition tape games for the multiload shelf, in bulk.

Point it at a directory of .ptp files (zips are opened and searched for
ptps automatically) and it boots every plain one-block program in the
emulator -- in the PMD 85-2 compatibility environment first, since the
era's tape software calls the 8000h monitor, then natively -- runs it
with a few nudge keypresses, and issues a verdict by looking at the
screen: a program that draws is a candidate, one that does not is
reported with the reason.  Every candidate gets a screenshot and a
ready-to-paste manifest entry; the report says what was skipped and why,
because a shelf that silently drops games reads as "checked everything"
when it did not.

    ./shelf_factory.py --monitor3 monit3B.rom --out audition/ tapes/...

The -2 environment is manufactured the way the machine itself does it:
monit3B's JMP FFF0h relocation is executed, not imitated.  A PASS here
is the same chain the shelf boots, minus the module transport that
test_multiload proves separately.

Turbo-loader programs (body + headerless raw blocks) are out of scope
and listed as such -- docs/ROM-module.md carries the state of that art.
"""

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptp_lib
from i8080emu import Bus, CPU

STEPS_SETTLE = 2_500_000
NUDGES = [("SPACE", 0, 16), ("EOL", 14, 16), ("1", 0, 2)]
LIT_PASS = 300                  # drawn bytes that count as "it runs"


def compat_machine(monit3: bytes):
    """A -3 that has just executed JMP FFF0h: its manufactured -2 monitor
    at 8000h, AllRAM, stopped at the module probe."""
    bus = Bus(monit3)
    bus.startup_map = False
    bus.rom_visible = True
    cpu = CPU(bus)
    cpu.sp = 0xBFF0
    cpu.pc = 0xFFF0
    for _ in range(3_000_000):
        if cpu.pc == 0x802D:
            return bus, cpu
        cpu.step()
    raise SystemExit("error: FFF0h relocation never reached 802Dh -- "
                     "is --monitor3 really a monit3 image?")


def native_machine(monit3: bytes):
    bus = Bus(monit3)
    bus.startup_map = False
    bus.rom_visible = True
    cpu = CPU(bus)
    cpu.sp = 0xBFF0
    return bus, cpu


def lit_bytes(bus) -> int:
    return sum(1 for ln in range(256) for c in range(48)
               if bus.ram[0xC000 + ln * 64 + c])


def screenshot(bus, path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        return
    W, H = 48 * 6, 256
    img = Image.new('RGB', (W, H), (0, 0, 0))
    px = img.load()
    for ln in range(H):
        for c in range(48):
            v = bus.ram[0xC000 + ln * 64 + c]
            for bit in range(6):
                if (v >> bit) & 1:
                    px[c * 6 + bit, ln] = (0, 255, 40)
    img.resize((W * 2, H * 2), Image.NEAREST).save(path)


def audition(prog: dict, monit3: bytes, env: str):
    """Boot one program; -> (verdict, lit, bus)."""
    body = bytes(prog['body'])
    load = prog['start']
    if load + len(body) > 0xC000:
        return 'loads past BFFF', 0, None
    bus, cpu = (compat_machine if env == 'v2' else native_machine)(monit3)
    bus.ram[load:load + len(body)] = body
    cpu.pc = load                          # entry = header start field
    try:
        for _ in range(STEPS_SETTLE):
            cpu.step()
            if cpu.halted:
                return 'halted', lit_bytes(bus), bus
        for _, col, mask in NUDGES:
            bus.press(col, mask)
            for _ in range(400_000):
                cpu.step()
            bus.release_all()
            for _ in range(400_000):
                cpu.step()
    except NotImplementedError as e:
        return f'emulator: {e}', lit_bytes(bus), bus
    lit = lit_bytes(bus)
    if lit >= LIT_PASS:
        return 'PASS', lit, bus
    return f'no draw ({lit} lit)', lit, bus


def gather_ptps(paths):
    out = []
    for p in map(Path, paths):
        candidates = p.rglob('*') if p.is_dir() else [p]
        for f in candidates:
            if not f.is_file():
                continue
            if f.suffix.lower() == '.ptp':
                out.append((f.name, f.read_bytes()))
            elif f.suffix.lower() == '.zip':
                with zipfile.ZipFile(f) as z:
                    for n in z.namelist():
                        if n.lower().endswith('.ptp'):
                            out.append((f"{f.name}:{Path(n).name}",
                                        z.read(n)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tapes", nargs="+",
                    help=".ptp files, zips of them, or directories of either")
    ap.add_argument("--monitor3", type=Path, required=True,
                    help="monit3B.rom (8 KB), for both environments")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    monit3 = args.monitor3.read_bytes()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "shots").mkdir(exist_ok=True)
    report, manifest = [], []
    seen = set()

    import tempfile, os
    for src, data in gather_ptps(args.tapes):
        tmp = Path(tempfile.mkstemp(suffix='.ptp')[1])
        tmp.write_bytes(data)
        try:
            progs = ptp_lib.programs_of(tmp)
        finally:
            os.unlink(tmp)
        for prog in progs:
            name = prog['name'] or '(unnamed)'
            key = (name, len(prog['body']))
            if key in seen:
                continue
            seen.add(key)
            if prog['raws']:
                report.append((name, src, 'turbo loader: not extractable yet'))
                continue
            if not prog['body']:
                report.append((name, src, 'no body block'))
                continue
            for env in ('v2', 'v3'):
                verdict, lit, bus = audition(prog, monit3, env)
                if verdict == 'PASS':
                    tag = ''.join(ch if ch.isalnum() else '_' for ch in name)
                    if bus is not None:
                        screenshot(bus, args.out / "shots" / f"{tag}.png")
                    report.append((name, src, f'PASS ({env}, {lit} lit)'))
                    ent = {"type": "tape", "name": name[:20],
                           "file": src.split(':')[-1], "program": prog['name'],
                           "exec": f"0x{prog['start']:04X}"}
                    if env == 'v2':
                        ent["mode"] = "v2"
                    manifest.append(ent)
                    break
            else:
                report.append((name, src, f'FAIL both envs: {verdict}'))

    lines = ["# Shelf audition", ""]
    for name, src, verdict in report:
        lines.append(f"- `{name:10s}` ({src}): {verdict}")
    lines += ["", "## Manifest entries for the passers", "```json"]
    import json
    lines.append(json.dumps(manifest, indent=2))
    lines.append("```")
    (args.out / "report.md").write_text("\n".join(lines))
    n_pass = sum(1 for _, _, v in report if v.startswith('PASS'))
    print(f"{len(report)} programs auditioned, {n_pass} passed "
          f"-> {args.out}/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
