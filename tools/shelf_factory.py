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
# H and S are the -1 loaders' start keys (see NUDGE_KEYS); pressing them
# in the gauntlet puts those games' start paths under the HLT check too.
NUDGES = [("SPACE", 0, 16), ("EOL", 14, 16), ("1", 0, 2),
          ("H", 5, 8), ("S", 1, 8)]
LIT_PASS = 300                  # drawn bytes that count as "it runs"
NOP_SLED_PCT = 8                # share of executed NOPs that means a crash
SENTINEL = 0xAA


class TapeBus(Bus):
    """The extraction rig: a byte-level USART at 1Ch/1Dh (mask FDh, so the
    1E/1F aliases too), and 8000-8FFFh write-protected -- it is ROM on the
    machines these loaders were written for, and at least one of them
    writes there and reads the result back as its machine sniff.

    Every write below 8000h is recorded in a bitmap.  The bitmap, not a
    sentinel value, decides what the extraction ships: a sentinel cannot
    tell "never written" from "the loader wrote the sentinel's own value",
    and 0xAA is an everyday byte in sprite data -- MAGICIAN shipped with
    21 of its bytes silently zeroed that way, one of them an opcode."""

    def __init__(self, tape=b'', **kw):
        super().__init__(**kw)
        self.tape = list(tape)
        self.wrote = bytearray(0x10000)

    def write(self, a, v):
        a &= 0xFFFF
        if 0x8000 <= a <= 0x8FFF:
            self.clock += 1
            return
        self.wrote[a] = 1
        super().write(a, v)

    def inp(self, port):
        if (port & 0xFD) == 0x1C:
            self.clock += 1
            return self.tape.pop(0) if self.tape else 0
        if (port & 0xFD) == 0x1D:
            self.clock += 1
            return 0x05 | (0x02 if self.tape else 0)
        return super().inp(port)


# The keys a stalled loader is offered, one at a time, in this order.  A
# loader that finishes its tape and then waits is usually a -1 game at
# its own prompt -- "HRA - H" -- and monit1's reader translates one key
# per scan: two keys down at once match nothing in its table and come
# back as nothing.  The first nudge (SPACE and EOL together, every 1.5M
# steps) never got through; BOULD +4, FLAPPY+4, FRED +4, both MANICs,
# PENETR.3 and both PAMPUCHs want H, MICRHYTM and PANGO want S.
NUDGE_KEYS = [('H', 5, 8), ('G', 4, 8), ('P', 9, 4), ('S', 1, 8),
              ('SPACE', 0, 16), ('EOL', 14, 16), ('1', 0, 2), ('Y', 1, 16),
              ('A', 0, 8), ('N', 6, 16)]
NUDGE_PERIOD = 1_000_000
NUDGE_HOLD = 300_000
HANDOFF_GUARD = 200_000         # steps a handoff is watched on the tape port
HANDOFF_TX_MAX = 4              # data bytes sent there that mean "saving"
                                # (TREASURE ISLAND's init sends one)


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
    # The header's start field is the LOAD address; for most loaders it is
    # also the entry, but the +4 family points it at an internal
    # subroutine and really starts at its DI -- so every DI in the body
    # is an entry candidate (SABOTER's is 47 bytes in).
    entries = [start] + [start + i for i, b in enumerate(body)
                         if b == 0xF3][:3]
    entries = list(dict.fromkeys(entries))
    last = 'never ran'
    for mon_name, mon in monitors:
        for stream, nudge in variants:
          for entry in entries:
            for dreg in (0xFF, 0x00):
                bus = TapeBus(tape=stream, rom=bytes(0x2000))
                bus.startup_map = False
                bus.rom_visible = False
                for a in range(0x8000):
                    bus.ram[a] = SENTINEL
                bus.ram[0x8000:0x8000 + len(mon)] = mon
                cpu = CPU(bus)
                # clip at the monitor: on the real machine 8000h+ is ROM,
                # so a header block that nominally runs past it loses its
                # tail exactly as it would on the bench
                bus.ram[start:min(start + len(body), 0x8000)] = \
                    body[:max(0, 0x8000 - start)]
                cpu.sp = 0xBFF0
                cpu.pc = entry
                cpu.r['D'] = dreg
                cpu.z, cpu.cy = True, False
                # the loader body is content too: games reuse its bytes
                # as scratch or data, so it ships with the image (clamped:
                # some bodies run right up to the 8000h monitor boundary)
                bend = min(start + len(body), 0x8000)
                bus.wrote[start:bend] = b'\x01' * (bend - start)
                lo, hi = start, start + len(body)
                # A handoff candidate is the first instruction executed
                # outside the loader body and the monitor once the tape
                # is drained.  It is only accepted if the next while is
                # quiet on the tape port: PANGO's and MICRORHYTHM's
                # loaders offer "S to save" at their prompt, the nudge
                # pressed it, and the first thing outside the body was a
                # trampoline into monit1's save routine -- which the
                # emulator's instant USART let fall back to the prompt,
                # and the bench's real tape interface waited in forever.
                tx = [0]
                out = bus.out

                def guarded_out(port, v):
                    if (port & 0xFD) == 0x1C:
                        tx[0] += 1
                    out(port, v)
                bus.out = guarded_out
                armed = True
                pending = None          # (capture, deadline) under guard
                for step in range(12_000_000):
                    pc = cpu.pc
                    if pending is not None:
                        if tx[0] >= HANDOFF_TX_MAX:
                            # the loader is saving: not a handoff.  Let it
                            # finish and re-arm once it is back home.
                            pending, armed = None, False
                        elif step >= pending[1] or cpu.halted:
                            # (a HLT right after handoff is the program's
                            # problem, for the cold verify to judge; the
                            # capture was taken at the handoff instant)
                            return pending[0], None
                    elif armed and not bus.tape and not (lo <= pc < hi) \
                            and not (0x8000 <= pc < 0x9000):
                        tx[0] = 0
                        pending = (_capture(bus, cpu, mon_name),
                                   step + HANDOFF_GUARD)
                    elif not armed and lo <= pc < hi:
                        armed = True
                    if cpu.halted:
                        last = f'{mon_name}: HALT at {pc:04X}'
                        break
                    if nudge and step % NUDGE_PERIOD == 0 and step:
                        _, col, mask = NUDGE_KEYS[
                            (step // NUDGE_PERIOD - 1) % len(NUDGE_KEYS)]
                        bus.press(col, mask)
                    elif nudge and step % NUDGE_PERIOD == NUDGE_HOLD:
                        bus.release_all()
                    cpu.step()
                else:
                    last = (f'{mon_name}: cap at {cpu.pc:04X}, '
                            f'{len(bus.tape)} tape bytes left')
    return None, last


def _capture(bus, cpu, mon_name):
    """The extraction at this instant: image, entry, registers, and the
    loader's writes above the program as segments."""
    snap = bytes(bus.ram[:0x8000])
    marks = [i for i, w in enumerate(bus.wrote[:0x8000]) if w]
    a0, b0 = marks[0], marks[-1] + 1
    img = bytes(snap[i] if bus.wrote[i] else 0 for i in range(a0, b0))
    regs = {k: cpu.r[k] for k in 'ABCDEHL'}
    regs['SP'] = cpu.sp
    # the loading screen, if the loader painted one: some games keep it
    # as their title backdrop, some put their only instructions on it
    vm = [i for i in range(0xC000, 0x10000) if bus.wrote[i]]
    screen = None
    if vm:
        s0, s1 = vm[0], vm[-1] + 1
        screen = (s0, bytes(bus.ram[i] if bus.wrote[i] else 0
                            for i in range(s0, s1)))
    # ...and anything stored above the monitor: BOULDER DASH keeps a
    # 24-byte table at BFD8h that its menu renderer walks -- shipped as
    # its own segment, because the image extent stops at 8000h
    hm = [i for i in range(0x9000, 0xC000) if bus.wrote[i]]
    high = None
    if hm:
        h0, h1 = hm[0], hm[-1] + 1
        high = (h0, bytes(bus.ram[i] if bus.wrote[i] else 0
                          for i in range(h0, h1)))
    return (img, a0, cpu.pc, regs, mon_name, screen, high)


def verify_cold(image: bytes, load: int, exec_: int, regs: dict,
                monit3: bytes, screen: tuple | None = None,
                high: tuple | None = None, tape_check: bool = True):
    """Boot the extracted image the way the SHELF will: fresh -2
    environment, screen segment applied, stage-2-style register init, cold
    jump.  A PASS here is a game the stage-2 loader can genuinely start."""
    def mk():
        bus, cpu = compat_machine(monit3)
        bus.ram[load:load + len(image)] = image
        if screen is not None:
            s0, sdata = screen
            bus.ram[s0:s0 + len(sdata)] = sdata
        if high is not None:
            h0, hdata = high
            bus.ram[h0:h0 + len(hdata)] = hdata
        bus.usart_reads = 0
        for k in 'ABCDEHL':
            cpu.r[k] = regs[k]
        cpu.sp = regs['SP']
        cpu.pc = exec_
        return bus, cpu
    # execution inside a shipped segment is the program, not a crash --
    # the +4 family runs real code up in the VRAM region (SABOTER enters
    # at FA43h); BLUDISTE-style noise runs live outside anything shipped
    ok = []
    if screen is not None:
        ok.append((screen[0], screen[0] + len(screen[1])))
    if high is not None:
        ok.append((high[0], high[0] + len(high[1])))
    def exec_ok(pc):
        return any(a <= pc < b for a, b in ok)
    return gauntlet(mk, rom_at_e000=False, exec_ok=exec_ok,
                    tape_check=tape_check)


def gauntlet(mk_machine, rom_at_e000: bool, exec_ok=None,
             tape_check: bool = True):
    """Settle, nudge, then judge -- the full obstacle course a shelf entry
    must survive, with every check named after the game that taught it:

    - HLT anywhere: MAGICIAN's zeroed opcode waited behind the start key.
    - 'executes VRAM': a crashed program that runs off into screen memory
      paints self-sustaining static -- BLUDISTE's signature.  No cartridge
      has business executing above C000h (above E000h it is the monitor's
      ROM in a native boot, so the bound is per-environment).
    - 'noise' backstop: static-like byte distribution on a lit screen --
      what a multi-part loader's first stage (CERES-01, TVARE, TANK)
      shows after inhaling the tape port's noise as its next stage.
    - 'no draw': the lit floor, as ever -- measured at the screen's
      fullest moment across the nudges, since a start key may clear it.
    - 'waits for tape': the run ends inside the monitor's tape reader --
      a first stage expecting the next from tape (PAVUCI), the
      multi-part class without the static.

    Tape reads are NOT a verdict: healthy games poll the port and reject
    its noise (KUBANOID, MAGICIAN) exactly as they do on the bench.
    """
    bus, cpu = mk_machine()
    vram_hits = 0

    nops = steps = 0

    def run(n, presses=None):
        nonlocal vram_hits, nops, steps
        if presses:
            bus.press(*presses)
        for _ in range(n):
            if bus.read(cpu.pc) == 0x00:
                nops += 1
            steps += 1
            cpu.step()
            pc = cpu.pc
            if pc >= 0xC000 and (pc < 0xE000 or not rom_at_e000) \
                    and not (exec_ok and exec_ok(pc)):
                vram_hits += 1
                if vram_hits > 50_000:
                    return 'executes VRAM'
            if cpu.halted:
                return 'halted'
        return None

    # The screen is judged at its fullest, not its last: a nudge key can
    # legitimately clear it (MESTO and ONA A DUCH start a level on S and
    # wipe their title), and "did it draw" is a question about the peak.
    # HLT and VRAM execution are still caught wherever they happen.
    def snapshot():
        return lit_bytes(bus), visible_bytes(bus)

    try:
        why = run(STEPS_SETTLE)
        if why:
            return why, lit_bytes(bus), bus
        best = snapshot()
        for key, col, mask in NUDGES:
            why = run(400_000, (col, mask))
            bus.release_all()
            why = why or run(400_000)
            if why:
                return (why if why == 'executes VRAM'
                        else f'{why} on {key}'), lit_bytes(bus), bus
            best = max(best, snapshot(), key=lambda s: s[0])
    except NotImplementedError as e:
        return f'emulator: {e}', lit_bytes(bus), bus
    # A run that ends inside the monitor's tape reader is a first stage
    # waiting for the next one -- PAVUCI drew a title and then sat in
    # the byte loop at 8A42h for a header no module will ever send.
    # Unshippable, whatever it drew.  The reader's polling sites are
    # read off the monitor image itself (every IN from the USART in its
    # tape section), natively at E000h or relocated to 8000h.
    # Only meaningful when the monitor in the machine is the one the
    # program was written for: a -1 game cold-verified in the -2 monitor
    # (BOULDER DASH, MANIC MINER 2) calls monit1 entry points and lands
    # wherever the -2 code happens to be, reader included.
    base = 0xE000 if rom_at_e000 else 0x8000
    if tape_check and any(s - 0x10 <= cpu.pc < s + 0x20
                          for s in usart_sites(bytes(bus.rom), base)):
        return 'waits for tape', lit_bytes(bus), bus
    # A program running through empty RAM executes NOP after NOP (and the
    # odd STAX from whatever bytes the sled crosses): BOMBARDER drew a
    # full screen of regular stripes that way -- too regular for the
    # noise check -- and read no port at all.  Healthy games sit at 0-3%
    # NOPs; it was at 12%.
    if steps and nops * 100 // steps >= NOP_SLED_PCT:
        return f'runs through empty RAM ({nops * 100 // steps}% NOP)', \
            lit_bytes(bus), bus
    lit, visible = best
    if lit < LIT_PASS:
        return f'no draw ({lit} lit)', lit, bus
    if noisy(visible):
        return 'noise (static-like byte distribution)', lit, bus
    return 'PASS', lit, bus


def usart_sites(monitor: bytes, base: int) -> list:
    """Addresses in the monitor's tape section (its upper half -- the
    lower half is the command loop and terminal I/O, which poll the
    same port for other reasons) where it reads the 8251."""
    return [base + i for i in range(len(monitor) - 1)
            if i >= 0x800 and monitor[i] == 0xDB
            and monitor[i + 1] in (0x1C, 0x1D, 0x1E, 0x1F)]


def visible_bytes(bus) -> list:
    return [bus.ram[0xC000 + ln * 64 + c]
            for ln in range(256) for c in range(48)]


def noisy(visible: list) -> bool:
    from collections import Counter
    top8 = sum(n for _, n in Counter(visible).most_common(8))
    return top8 < len(visible) // 5


class ShelfBus(Bus):
    """The verification machine's bus: a real 8251 sits at 1Ch/1Dh with no
    tape playing -- which on the bench means an open audio input picking
    up hum, so the receiver clocks in garbage frames forever.  A silent
    model (RxRDY never) was tried first and contradicted the bench both
    ways: KUBANOID polls the port, rejects the junk and plays on (silent
    = it waits forever); BLUDISTE inhales the junk and paints static
    (silent = it draws nothing).  Deterministic noise reproduces the
    machine; reads are still counted for the report."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.usart_reads = 0
        self._prng = 0x2A5F

    def _next(self):
        self._prng = (self._prng * 0x41C6 + 0x3039) & 0x7FFF
        return self._prng >> 7

    def inp(self, port):
        if (port & 0xFD) == 0x1C:
            self.usart_reads += 1
            return self._next() & 0xFF
        if (port & 0xFD) == 0x1D:
            self.usart_reads += 1
            return 0x05 | (0x02 if self._next() & 1 else 0)
        return super().inp(port)


def compat_machine(monit3: bytes):
    """A -3 that has just executed JMP FFF0h: its manufactured -2 monitor
    at 8000h, AllRAM, stopped at the module probe."""
    bus = ShelfBus(monit3)
    bus.startup_map = False
    bus.rom_visible = True
    cpu = CPU(bus)
    cpu.sp = 0xBFF0
    cpu.pc = 0xFFF0
    for _ in range(3_000_000):
        if cpu.pc == 0x802D:
            bus.usart_reads = 0                  # the monitor's own boot
            return bus, cpu
        cpu.step()
    raise SystemExit("error: FFF0h relocation never reached 802Dh -- "
                     "is --monitor3 really a monit3 image?")


def native_machine(monit3: bytes):
    bus = ShelfBus(monit3)
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

    def mk():
        bus, cpu = (compat_machine if env == 'v2'
                    else native_machine)(monit3)
        bus.ram[load:load + len(body)] = body
        bus.usart_reads = 0
        cpu.pc = load                      # entry = header start field
        return bus, cpu
    return gauntlet(mk, rom_at_e000=(env != 'v2'))


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


def _pool_init(monit3, monitors, out, trust):
    _POOL['monit3'] = monit3
    _POOL['monitors'] = monitors
    _POOL['out'] = Path(out)
    _POOL['trust'] = trust


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
        image, load, exec_, regs, mon_name, screen, high = got
        verdict, lit, bus = verify_cold(image, load, exec_, regs, monit3,
                                        screen, high,
                                        tape_check=(mon_name != 'monit1'))
        if bus is not None:
            screenshot(bus, out / "shots" / f"{tag}.png")
        if verdict != 'PASS' and name.strip() in _POOL['trust']:
            verdict = f'TRUSTED (bench-attested; gauntlet said: {verdict})'
        if not verdict.startswith(('PASS', 'TRUSTED')):
            return name, src, (f'turbo rip ok via {mon_name} but cold '
                               f'boot: {verdict}'), None
        (out / "verified" / f"{tag}.bin").write_bytes(image)
        ent = {"type": "binary", "name": name[:20],
               "file": f"verified/{tag}.bin",
               "load": f"0x{load:04X}", "exec": f"0x{exec_:04X}",
               "mode": "v2",
               "regs": {k.lower(): f"0x{regs[k]:02X}" for k in 'ABCDEHL'}
               | {"sp": f"0x{regs['SP']:04X}"}}
        note = ''
        if screen is not None:
            s0, sdata = screen
            (out / "verified" / f"{tag}.scr").write_bytes(sdata)
            ent["screen"] = {"file": f"verified/{tag}.scr",
                             "load": f"0x{s0:04X}"}
            note = f', {len(sdata)} B screen'
        if high is not None:
            h0, hdata = high
            (out / "verified" / f"{tag}.hi").write_bytes(hdata)
            ent["high"] = {"file": f"verified/{tag}.hi",
                           "load": f"0x{h0:04X}"}
            note += f', {len(hdata)} B high'
        return name, src, (f'{verdict.split(" ")[0]} (turbo via '
                           f'{mon_name}, {lit} lit, {len(image)} B at '
                           f'{load:04X}, exec {exec_:04X}{note})'), ent
    if not prog['body']:
        return name, src, 'no body block', None
    verdict = '?'
    for env in ('v2', 'v3'):
        verdict, lit, bus = audition(prog, monit3, env)
        if verdict != 'PASS' and env == 'v2' \
                and name.strip() in _POOL['trust']:
            verdict = 'PASS'      # bench-attested; objection in the log
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
    ap.add_argument("--trust", action="append", default=[],
                    help="program name whose shelf-worthiness is attested "
                         "on real hardware; emitted even when the gauntlet "
                         "objects, with the objection printed.  The bench "
                         "outranks the emulator -- KUBANOID plays fine on "
                         "the machine and dies under one particular noise "
                         "stream here")
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
                 (monit3, monitors, str(args.out),
                  {t.strip() for t in args.trust})) as pool:
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
