#!/usr/bin/env python3
"""Build a multiload flash image from an audition and a curation file.

    build_images.py games --work WORK     # shelf/games.json
    build_images.py apps  --work WORK     # shelf/apps.json
    build_images.py games --work WORK --curation picks.json --name preview

WORK is the directory fetch_corpus.sh and audition.sh fill:

    WORK/roms/       monit3B.rom monit1.rom basic3.rmm basic2A.rmm
    WORK/corpus/     the unpacked Infoserver packages
    WORK/audition/   shelf_factory.py output (report.md, verified/)

Both images carry the same SYSTEM directory (BASICs, test cards, module
scanner, banner, FLASH CHECK with this image's own sums).  Each image is
verified in the emulator from a true cold boot -- every directory opens
and BACKs, BASIC-G boots, FLASH CHECK sweeps every page clean -- before
the firmware is built.  Results land in WORK/out/.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / 'tools'))
import make_multiload as ml                     # noqa: E402
import make_screentest as st                    # noqa: E402
import make_moduletest as mt                    # noqa: E402
import make_flashcheck as fc                    # noqa: E402
from i8080emu import Bus, CPU                   # noqa: E402
from shelf_factory import screenshot, lit_bytes  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('image', choices=('games', 'apps'),
                help="which layout: alphabetical game dirs, or app dirs")
ap.add_argument('--work', type=Path, required=True)
ap.add_argument('--curation', type=Path,
                help="curation file (default shelf/<image>.json); a games "
                     "layout from any file makes a preview image")
ap.add_argument('--name', help="output name (default <image>): "
                               "mhb2616-24F-MULTILOAD-<name>.uf2")
args = ap.parse_args()

WORK = args.work.resolve()
IMAGE = args.image
CURATION = args.curation or (HERE / f'{IMAGE}.json')
NAME = args.name or IMAGE
ROMS = WORK / 'roms'
CORPUS = WORK / 'corpus'
AUDITION = WORK / 'audition'
STAGE = WORK / f'stage-{NAME}'
OUT = WORK / 'out'
SHOTS = OUT / 'shots'
MONIT3B = ROMS / 'monit3B.rom'
BUILD = REPO / 'firmware' / 'build-ml'

rep = (AUDITION / 'report.md').read_text()
by_name = {}
for e in json.loads(rep.split('```json')[1].split('```')[0]):
    by_name.setdefault(e['name'].strip(), e)   # first PASS wins on dupes

shutil.rmtree(STAGE, ignore_errors=True)
(STAGE / 'verified').mkdir(parents=True)
SHOTS.mkdir(parents=True, exist_ok=True)
for rom in ('basic3.rmm', 'basic2A.rmm', 'monit1.rom'):
    shutil.copy(ROMS / rom, STAGE)
(STAGE / 'cardlow.bin').write_bytes(st.build_shelf(0x0000, 0x20, 0xC0))
(STAGE / 'cardhigh.bin').write_bytes(st.build_shelf(0x9000, 0x00, 0x90))
(STAGE / 'modscan.bin').write_bytes(mt.build_shelf(0x4000, basic_page=2))
(STAGE / 'flashcheck.bin').write_bytes(fc.build_shelf(0x4000, [0], 0))


def stage_entry(pick):
    """A curation pick -> a manifest entry, its files copied into STAGE."""
    name = pick['audition']
    if name not in by_name:
        raise SystemExit(f"error: {name!r} is not a PASS in {AUDITION}")
    e = dict(by_name[name])
    if 'name' in pick:
        e['name'] = pick['name']
    for k, v in pick.get('override', {}).items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    if e['type'] == 'tape':
        src = next(c for c in CORPUS.rglob(e['file']) if c.is_file())
        shutil.copy(src, STAGE / e['file'])
    else:
        shutil.copy(AUDITION / e['file'], STAGE / e['file'])
    for seg in ('screen', 'high'):
        if seg in e:
            shutil.copy(AUDITION / e[seg]['file'], STAGE / e[seg]['file'])
    return e


if IMAGE == 'games':
    cur = json.loads(CURATION.read_text())
    games = sorted((stage_entry(p) for p in cur['games']),
                   key=lambda e: e['name'])
    CHUNK = 11
    dirs = []
    for i in range(0, len(games), CHUNK):
        part = games[i:i + CHUNK]
        dirs.append({"type": "dir",
                     "name": f"{part[0]['name'][0]} - {part[-1]['name'][0]}",
                     "entries": part})
else:
    cur = json.loads(CURATION.read_text())
    shutil.copy(next(CORPUS.rglob('mrs2.rmm')), STAGE / 'mrs2.rmm')
    dirs = []
    for d in cur['dirs']:
        ents = [stage_entry(p) for p in d['picks']]
        if d['name'] == 'DEVELOP':
            ents.append({"type": "rmm2", "name": "MRS2", "file": "mrs2.rmm"})
        dirs.append({"type": "dir", "name": d['name'], "entries": ents})

manifest = {"name": NAME, "entries": [
    {"type": "dir", "name": "SYSTEM", "entries": [
        {"type": "rmm",  "name": "BASIC-G 3.0", "file": "basic3.rmm"},
        {"type": "rmm2", "name": "BASIC 2A",    "file": "basic2A.rmm"},
        {"type": "binary", "name": "TEST CARD 2000-BFFF",
         "file": "cardlow.bin", "load": "0x0000", "exec": "0x0000"},
        {"type": "binary", "name": "TEST CARD 0000-8FFF",
         "file": "cardhigh.bin", "load": "0x9000", "exec": "0x9000"},
        {"type": "binary", "name": "MODULE SCAN",
         "file": "modscan.bin", "load": "0x4000", "exec": "0x4000"},
        {"type": "demo", "name": "CARTRIDGE OK"},
        {"type": "binary", "name": "FLASH CHECK",
         "file": "flashcheck.bin", "load": "0x4000", "exec": "0x4000"},
    ]}] + dirs}
(STAGE / 'manifest.json').write_text(json.dumps(manifest, indent=1))

# FLASH CHECK carries a sum per page, including pages after its own, so
# the image is built twice: once to learn the sums, once with them baked
# in.  Its own page is found by which page moved.
pages, menu = ml.build_pages(manifest['entries'], STAGE)
sums = fc.page_sums(pages)
(STAGE / 'flashcheck.bin').write_bytes(fc.build_shelf(0x4000, sums, 0))
pages2, _ = ml.build_pages(manifest['entries'], STAGE)
changed = [i for i, (a, b) in enumerate(zip(pages, pages2)) if a != b]
assert len(changed) == 1, f"two-pass drift: {changed}"
own = changed[0]
(STAGE / 'flashcheck.bin').write_bytes(fc.build_shelf(0x4000, sums, own))
pages, menu = ml.build_pages(manifest['entries'], STAGE)
print(f"{NAME}: {len(pages)} pages = {len(pages) * 16} KB, "
      f"flash check on page {own}")
if len(pages) > 128:
    raise SystemExit(f"error: {len(pages)} pages; FLASH CHECK's grid "
                     f"holds 128 -- drop something from the curation")
for d in menu:
    print(f"  main menu: {d['name']} -> page {d['page']}")


def boot():
    bus = Bus(MONIT3B.read_bytes(), pages=pages)
    cpu = CPU(bus)
    for _ in range(2_500_000):
        cpu.step()
    assert bus.mod_page == 0 and lit_bytes(bus) > 200, "menu did not boot"
    return bus, cpu


def key(bus, cpu, idx, steps):
    _, col, mask = ml.KEYS[idx]
    bus.press(col, mask)
    for _ in range(600_000):
        cpu.step()
        if cpu.halted:
            raise SystemExit(f"halted after key {idx}")
    bus.release_all()
    for _ in range(steps):
        cpu.step()
        if cpu.halted:
            raise SystemExit(f"halted, pc={cpu.pc:04X}")


bus, cpu = boot()
screenshot(bus, SHOTS / f'{NAME}-main.png')

for di in range(1, len(menu)):
    bus, cpu = boot()
    key(bus, cpu, di, 2_000_000)
    assert bus.mod_page == menu[di]['page'], f"dir {di} did not open"
    n = len(dirs[di - 1]['entries']) + 1
    for _ in range(100_000):
        cpu.step()
    key(bus, cpu, n - 1, 2_000_000)
    assert bus.mod_page == 0, f"BACK from dir {di} failed"
print("all directories navigate")

bus, cpu = boot()
key(bus, cpu, 0, 2_000_000)
key(bus, cpu, 0, 6_000_000)
assert bus.mod_page == 2 and lit_bytes(bus) > 60, "BASIC-G did not boot"
print("BASIC-G boots")

bus, cpu = boot()
key(bus, cpu, 0, 2_000_000)
key(bus, cpu, 6, 170_000_000)
done, bad = bus.ram[0x3E00], bus.ram[0x3E01]
print(f"FLASH CHECK: swept {done} pages, bad={bad}")
screenshot(bus, SHOTS / f'{NAME}-flashcheck.png')
assert done == len(pages) and bad == 0 and bus.mod_page == 0
print("emulator verification PASSED")

# ---- firmware ---------------------------------------------------------------
sdk = os.environ.get('PICO_SDK_PATH')
if not sdk:
    raise SystemExit("error: set PICO_SDK_PATH to build the firmware")
env = dict(os.environ, PICO_SDK_PATH=sdk)
subprocess.run([sys.executable, str(REPO / 'tools/make_multiload.py'),
                '-o', str(REPO / 'firmware/rom_images.c'),
                str(STAGE / 'manifest.json')], check=True)
# Configure every time: a tree left configured for a single module
# refuses a multiload rom_images.c, and cmake re-runs cheaply.
subprocess.run(['cmake', '-S', str(REPO / 'firmware'), '-B', str(BUILD),
                '-G', 'Ninja', '-DMHB_BOARD=FIRE24F',
                '-DMHB_BANK_SOURCE=MODULE', '-DMHB_MODULE_MULTILOAD=ON'],
               check=True, env=env, capture_output=True)
subprocess.run(['ninja'], cwd=BUILD, check=True, env=env)
uf2 = OUT / f'mhb2616-24F-MULTILOAD-{NAME}.uf2'
binf = OUT / f'mhb2616-24F-MULTILOAD-{NAME}.bin'
shutil.copy(BUILD / 'mhb2616.uf2', uf2)
subprocess.run(['arm-none-eabi-objcopy', '-O', 'binary',
                str(BUILD / 'mhb2616.elf'), str(binf)], check=True)
size = binf.stat().st_size
assert size <= 2 * 1024 * 1024, "over the RP2354A's 2 MB"
print(f"{NAME} built: {size // 1024} KB of 2048 KB -> {uf2}")
