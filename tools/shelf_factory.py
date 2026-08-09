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
SENTINEL = 0xAA


class TapeBus(Bus):
    """The extraction rig: a byte-level USART at 1Ch/1Dh (mask FDh, so the
    1E/1F aliases too), and 8000-8FFFh write-protected -- it is ROM on the
    machines these loaders were written for, and at least one of them
    writes there and reads the result back as its machine sniff."""

    def __init__(self, tape=b'', **kw):
        super().__init__(**kw)
        self.tape = list(tape)

    def write(self, a, v):
        if 0x8000 <= (a & 0xFFFF) <= 0x8FFF:
            self.clock += 1
            return
        super().write(a, v)

    def inp(self, port):
        if (port & 0xFD) == 0x1C:
            self.clock += 1
            return self.tape.pop(0) if self.tape else 0
        if (port & 0xFD) == 0x1D:
            self.clock += 1
            return 0x05 | (0x02 if self.tape else 0)
        return super().inp(port)


def rip_turbo(prog: dict, monitors: list):
    """Run a turbo loader against real monitor images until one carries it
    to handoff.  -> (image, load, exec, regs, monitor_name) or (None, why).

    The loaders sniff their machine (LDA 8000h / CPI C3h -- monit1 begins
    with C3h, the -2 monitors with 31h) and then read raw blocks through
    that monitor's tape routines, so the monitor must be the one the
    loader was written against; trying them in order lets the loader
    itself tell us which.
    """
    start, body = prog['start'], bytes(prog['body'])
    plain = b''.join(prog['raws'])
    # Some loaders read raw byte streams; others call the monitor's
    # header-hunting reader and need each block's 48-byte pilot leader,
    # which the ptp container strips.  Some show "press key" mid-load.
    # Variants ordered cheapest-assumption-first; the first to reach
    # handoff wins, so a rip that already worked keeps working.
    leadered = b''.join(ptp_lib.LEADER + r for r in prog['raws'])
    variants = [(plain, False), (plain, True),
                (leadered, False), (leadered, True)]
    last = 'never ran'
    for mon_name, mon in monitors:
        for stream, nudge in variants:
            for dreg in (0xFF, 0x00):
                bus = TapeBus(tape=stream, rom=bytes(0x2000))
                bus.startup_map = False
                bus.rom_visible = False
                for a in range(0x8000):
                    bus.ram[a] = SENTINEL
                bus.ram[0x8000:0x8000 + len(mon)] = mon
                cpu = CPU(bus)
                bus.ram[start:start + len(body)] = body
                cpu.sp = 0xBFF0
                cpu.pc = start
                cpu.r['D'] = dreg
                cpu.z, cpu.cy = True, False
                lo, hi = start, start + len(body)
                for step in range(12_000_000):
                    pc = cpu.pc
                    if not bus.tape and pc < 0x8000 \
                            and not (lo <= pc < hi):
                        snap = bytes(bus.ram[:0x8000])
                        marks = [i for i, v in enumerate(snap)
                                 if v != SENTINEL]
                        a0, b0 = marks[0], marks[-1] + 1
                        img = bytes(0 if snap[i] == SENTINEL else snap[i]
                                    for i in range(a0, b0))
                        regs = {k: cpu.r[k] for k in 'ABCDEHL'}
                        regs['SP'] = cpu.sp
                        return (img, a0, pc, regs, mon_name), None
                    if cpu.halted:
                        last = f'{mon_name}: HALT at {pc:04X}'
                        break
                    if nudge and step % 1_500_000 == 0 and step:
                        bus.press(0, 16)       # SPACE
                        bus.press(14, 16)      # EOL
                    elif nudge and step % 1_500_000 == 750_000:
                        bus.release_all()
                    cpu.step()
                else:
                    last = (f'{mon_name}: cap at {cpu.pc:04X}, '
                            f'{len(bus.tape)} tape bytes left')
    return None, last


def verify_cold(image: bytes, load: int, exec_: int, regs: dict,
                monit3: bytes):
    """Boot the extracted image the way the SHELF will: fresh -2
    environment, stage-2-style register init, cold jump.  A PASS here is a
    game the stage-2 loader can genuinely start."""
    bus, cpu = compat_machine(monit3)
    bus.ram[load:load + len(image)] = image
    for k in 'ABCDEHL':
        cpu.r[k] = regs[k]
    cpu.sp = regs['SP']
    cpu.pc = exec_
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
    return ('PASS' if lit >= LIT_PASS else f'no draw ({lit} lit)'), lit, bus


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


_POOL = {}


def _pool_init(monit3, monitors, out):
    _POOL['monit3'] = monit3
    _POOL['monitors'] = monitors
    _POOL['out'] = Path(out)


def _audition_one(job):
    """One program, start to verdict.  Runs in a worker; writes its own
    screenshot and extracted binary, returns the report row."""
    name, src, prog = job
    monit3, monitors = _POOL['monit3'], _POOL['monitors']
    out = _POOL['out']
    tag = ''.join(ch if ch.isalnum() else '_' for ch in name)
    if prog['raws']:
        if not monitors:
            return name, src, 'turbo loader (no --monitors-dir)', None
        got, why = rip_turbo(prog, monitors)
        if got is None:
            return name, src, f'turbo rip failed: {why}', None
        image, load, exec_, regs, mon_name = got
        verdict, lit, bus = verify_cold(image, load, exec_, regs, monit3)
        if bus is not None:
            screenshot(bus, out / "shots" / f"{tag}.png")
        if verdict != 'PASS':
            return name, src, (f'turbo rip ok via {mon_name} but cold '
                               f'boot: {verdict}'), None
        (out / "verified" / f"{tag}.bin").write_bytes(image)
        ent = {"type": "binary", "name": name[:20],
               "file": f"verified/{tag}.bin",
               "load": f"0x{load:04X}", "exec": f"0x{exec_:04X}",
               "mode": "v2",
               "regs": {k.lower(): f"0x{regs[k]:02X}" for k in 'ABCDEHL'}
               | {"sp": f"0x{regs['SP']:04X}"}}
        return name, src, (f'PASS (turbo via {mon_name}, {lit} lit, '
                           f'{len(image)} B at {load:04X}, '
                           f'exec {exec_:04X})'), ent
    if not prog['body']:
        return name, src, 'no body block', None
    verdict = '?'
    for env in ('v2', 'v3'):
        verdict, lit, bus = audition(prog, monit3, env)
        if verdict == 'PASS':
            if bus is not None:
                screenshot(bus, out / "shots" / f"{tag}.png")
            ent = {"type": "tape", "name": name[:20],
                   "file": src.split(':')[-1], "program": prog['name'],
                   "exec": f"0x{prog['start']:04X}"}
            if env == 'v2':
                ent["mode"] = "v2"
            return name, src, f'PASS ({env}, {lit} lit)', ent
    return name, src, f'FAIL both envs: {verdict}', None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tapes", nargs="+",
                    help=".ptp files, zips of them, or directories of either")
    ap.add_argument("--monitor3", type=Path, required=True,
                    help="monit3B.rom (8 KB), for both environments")
    ap.add_argument("--monitors-dir", type=Path,
                    help="directory with monit2A/monit2/monit2B/monit1 "
                         ".rom files; enables turbo-loader extraction")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    monit3 = args.monitor3.read_bytes()
    monitors = []
    if args.monitors_dir:
        for n in ("monit1", "monit2A", "monit2", "monit2B"):
            hits = list(args.monitors_dir.rglob(f"{n}.rom"))
            if hits:
                monitors.append((n, hits[0].read_bytes()))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "shots").mkdir(exist_ok=True)
    (args.out / "verified").mkdir(exist_ok=True)
    report, manifest = [], []
    seen = set()

    import tempfile, os
    jobs = []
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
            jobs.append((name, src, prog))

    import multiprocessing as mp
    with mp.Pool(min(mp.cpu_count(), 8), _pool_init,
                 (monit3, monitors, str(args.out))) as pool:
        for name, src, verdict, ent in pool.imap_unordered(_audition_one,
                                                           jobs):
            print(f"  {name:10s} {verdict}", flush=True)
            report.append((name, src, verdict))
            if ent:
                manifest.append(ent)

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
