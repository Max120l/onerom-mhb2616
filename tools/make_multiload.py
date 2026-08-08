#!/usr/bin/env python3
"""Build a multiload image: a boot menu plus a shelf of module cartridges.

The PMD 85-3 monitor hands a ROM module the machine at every reset: it
copies the module's first fourteen bytes to C1B2 and jumps into them if
the first is CCh, with its own block-read routine at EC00h on call for
whatever else the stub wants (docs/ROM-module.md).  A multiload image
uses that hook twice over.  Page 0 of the board's flash is a menu -- its
stub loads a small program to B000h that draws the shelf and scans the
keyboard.  Every other page is a cartridge: either a verbatim module
image (a .rmm, booted exactly as the machine would boot the real module)
or a raw program wrapped in a stub of its own.  Picking an entry touches
a hotspot -- a live read of the window's top 32 bytes names a page --
waits out the board's table rebuild, and then replays the monitor's own
module boot against the freshly mapped page.

Reset semantics fall out rather than being designed: the monitor always
boots whatever page is mapped, so reset relaunches the current cartridge
(exactly what a real plugged module does) and a power cycle returns the
board to page 0, the menu.

    ./make_multiload.py -o ../firmware/rom_images.c manifest.json
    # then: cmake -DMHB_BANK_SOURCE=MODULE -DMHB_MODULE_MULTILOAD=ON

The manifest:

    {"name": "shelf",
     "entries": [
       {"type": "rmm",    "name": "BASIC-G 3.0", "file": "basic3.rmm"},
       {"type": "rmm2",   "name": "BASIC 2A",    "file": "basic2A.rmm"},
       {"type": "binary", "name": "SOME GAME",   "file": "game.bin",
        "load": "0x2000", "exec": "0x2000"},
       {"type": "demo",   "name": "TEST CARD"}
     ]}

An "rmm2" entry is a PMD 85-2 module (stub starts CDh).  Selecting one
jumps the -3 monitor's documented switch at FFF0h: it relocates its own
first 4 KB into RAM at 8000h as a monit2B-lineage compatibility monitor,
which then boots the mapped page by the -2 convention.  Reset from a -2
cartridge lands at the -3 monitor prompt (byte 0 is CDh, not the CCh the
-3 wants); JUMP FFF0 there relaunches it, a power cycle returns to the
menu.

Constraints the tool enforces rather than documents: at most 16 entries
(the menu's key row), payload clear of the hotspot region in every page,
a .rmm must start with CCh or it cannot boot, and a binary must land
inside 0000-BFFF without touching the C1B2 stub the monitor is about to
run.
"""

import argparse
import json
import sys
from pathlib import Path

from make_ramtest import Asm, B, C, D, E, H, L, M, A, RP_B, RP_D, RP_H, RP_SP
from make_screentest import FONT, glyph_byte

PAGE = 16384
HOTSPOT_MODULE_ADDR = 0x3FE0     # module address of hotspot 0; +n names page n
MAX_PAGES = 32
MAX_ENTRIES = 16
PAYLOAD_BASE = 0x0040            # payload starts here in a stub-led page

MENU_ORG = 0xB000                # where the menu runs
MENU_MAX = 0x0F00                # ...and how big it may grow
MENU_SP = 0xAF00                 # menu stack, below the menu

EC00 = 0xEC00                    # the monitor's block-read routine
STUB_RAM = 0xC1B2                # where the monitor executes a module stub

# Stack for the boot replay: top of the last readable VRAM line's invisible
# margin.  Two or three pushes deep, all landing in bytes the screen never
# shows and no sane cartridge loads over -- the monitor's own C070-C07E
# variables use the same trick.
REPLAY_SP = 0xE000

# EC00h transfers count+1 bytes: the loop pre-increments the count's high
# byte and tests only that byte (docs/ROM-module.md).
def ec_count(n: int) -> int:
    assert 1 <= n <= 0x3FA0
    return n - 1

# The delay between touching a hotspot and trusting the window again.
# The board detects within 5 ms and rebuilds in ~65 ms; this loop runs
# 24 states per iteration at 2.048 MHz, so 0x7000 iterations is ~336 ms
# -- four times the board's worst case, invisible in a menu.
DELAY_ITERS = 0x7000

# Selection keys, in entry order: caption character, keyboard column, row
# mask.  Digits sit in one row of the matrix (column d-1 for digit d, 0 in
# column 9); the letters fill out to sixteen.  Mapping measured from
# GPMD85Emulator SystemPIO::KeyMap.
KEYS = [("1", 0, 2), ("2", 1, 2), ("3", 2, 2), ("4", 3, 2), ("5", 4, 2),
        ("6", 5, 2), ("7", 6, 2), ("8", 7, 2), ("9", 8, 2), ("0", 9, 2),
        ("A", 0, 8), ("B", 5, 16), ("C", 3, 16), ("D", 2, 8), ("E", 2, 4),
        ("F", 3, 8)]

# Screen layout, in lines.
LN_TITLE = 6
LN_ENTRY0, ENTRY_STEP = 22, 9
TEXT_COL = 4

VRAM, STRIDE = 0xC000, 64


def vaddr(line: int, col: int = 0) -> int:
    return VRAM + line * STRIDE + col


def strip(text: str) -> bytes:
    """text as 7 rows x len(text) glyph bytes, row-major."""
    out = bytearray()
    for r in range(7):
        for ch in text:
            if ch not in FONT:
                raise SystemExit(f"error: no glyph for {ch!r} in {text!r}")
            out.append(glyph_byte(FONT[ch][r]))
    return bytes(out)


class MAsm(Asm):
    """The base assembler plus the stack-era opcodes the menu is allowed."""

    def call(self, t): self.db(0xCD); self.a16(t)
    def ret(self): self.db(0xC9)
    def push_b(self): self.db(0xC5)
    def pop_b(self): self.db(0xC1)
    def push_d(self): self.db(0xD5)
    def pop_d(self): self.db(0xD1)
    def stax_d(self): self.db(0x12)


# ---------------------------------------------------------------------------
# Shared 8080 pieces: clear the screen, blit a strip
# ---------------------------------------------------------------------------
def emit_clear(a: MAsm) -> None:
    """Clear the visible 48 bytes of all 256 lines.  The invisible margins
    are deliberately left alone: the monitor keeps state there, and so does
    the boot stub this program arrived through."""
    a.label("clear")
    a.lxi(RP_H, VRAM)
    a.lxi(RP_D, STRIDE - 48)
    a.mvi(B, 0)                       # 256 lines: DCR wraps first use
    a.label("clr_line")
    a.mvi(C, 48)
    a.label("clr_byte")
    a.mvi(M, 0)
    a.inx(RP_H)
    a.dcr(C)
    a.jnz("clr_byte")
    a.dad(RP_D)
    a.dcr(B)
    a.jnz("clr_line")
    a.ret()


def emit_blit7(a: MAsm) -> None:
    """Blit a 7-row strip: HL = source, DE = screen, C = width in bytes."""
    a.label("blit7")
    a.mvi(B, 7)
    a.label("b7_row")
    a.push_d()
    a.push_b()
    a.mov(B, C)
    a.label("b7_col")
    a.mov(A, M)
    a.stax_d()
    a.inx(RP_H)
    a.inx(RP_D)
    a.dcr(B)
    a.jnz("b7_col")
    a.pop_b()
    a.pop_d()
    a.mov(A, E)                       # DE += one screen line
    a.adi(STRIDE)
    a.mov(E, A)
    a.mov(A, D)
    a.aci(0)
    a.mov(D, A)
    a.dcr(B)
    a.jnz("b7_row")
    a.ret()


def emit_strip_calls(a: MAsm, placed: list) -> None:
    """placed: (label, line, col, width) per strip already given a label."""
    for label, line, col, width in placed:
        a.lxi(RP_H, label)
        a.lxi(RP_D, vaddr(line, col))
        a.mvi(C, width)
        a.call("blit7")


# ---------------------------------------------------------------------------
# The menu program
# ---------------------------------------------------------------------------
def build_menu(entries: list) -> bytes:
    """entries: dicts with name (str) and page (int), in menu order."""
    a = MAsm(MENU_ORG)
    strips = []                       # (label, line, col, width, data)

    def add_strip(tag, line, col, text):
        data = strip(text)
        strips.append((f"s_{tag}", line, col, len(text), data))

    add_strip("title", LN_TITLE, TEXT_COL, "ONE ROM MULTILOAD")
    for i, ent in enumerate(entries):
        cap = f"{KEYS[i][0]} {ent['name']}"
        add_strip(f"e{i}", LN_ENTRY0 + ENTRY_STEP * i, TEXT_COL, cap)
    ln_foot = LN_ENTRY0 + ENTRY_STEP * len(entries) + 6
    add_strip("foot", ln_foot, TEXT_COL, "PRESS A KEY TO LOAD")

    a.lxi(RP_SP, MENU_SP)
    a.call("clear")
    emit_strip_calls(a, [(lb, ln, co, w) for lb, ln, co, w, _ in strips])

    # Wait until no selection key is down, so the keystroke that picked the
    # previous cartridge (or a bouncing switch) cannot pick this one's.
    a.label("release")
    for i in range(len(entries)):
        _, col, mask = KEYS[i]
        a.mvi(A, col)
        a.out(0xF4)
        a.inp(0xF5)
        a.ani(mask)
        a.jz("release")               # active low: zero means held

    a.label("scan")
    for i, ent in enumerate(entries):
        _, col, mask = KEYS[i]
        a.mvi(A, col)
        a.out(0xF4)
        a.inp(0xF5)
        a.ani(mask)
        a.jz(f"sel_{i}")
    a.jmp("scan")

    # D carries the generation: 0 boots the page with the -3's own module
    # dance, 1 hands the whole job to JMP FFF0h -- the -3 monitor's
    # documented switch into PMD 85-2 mode, which relocates its first 4 KB
    # to RAM at 8000h as a monit2B and lets THAT monitor boot the page by
    # the -2 convention (CALL 8C00h stub, CPI CDh).  Measured end to end
    # in the emulator; see docs/ROM-module.md.
    for i, ent in enumerate(entries):
        a.label(f"sel_{i}")
        a.mvi(D, 1 if ent.get("v2") else 0)
        a.mvi(A, ent["page"])
        a.jmp("boot")

    # boot: A = page, D = generation.  Touch the hotspot, sit out the
    # board's rebuild, then boot the new page the way its machine expects.
    a.label("boot")
    a.lxi(RP_SP, REPLAY_SP)           # stack out of every cartridge's way
    a.adi(0xE0)                       # hotspot low byte; page <= 31, no carry
    a.mov(E, A)
    a.mvi(A, 0x90)                    # module 8255: A in, B/C out.  Clears
    a.out(0xFB)                       # the latches -- a glitch read of module
    a.mov(A, E)                       # 0x0000, which is nobody's hotspot.
    a.out(0xF9)
    a.mvi(A, HOTSPOT_MODULE_ADDR >> 8)
    a.out(0xFA)                       # strobe live on the hotspot address
    a.mvi(A, 0xFF)
    a.out(0xFA)                       # park
    a.lxi(RP_B, DELAY_ITERS)
    a.label("dly")
    a.dcx(RP_B)
    a.mov(A, B)
    a.ora(C)
    a.jnz("dly")
    a.mov(A, D)
    a.ora(A)
    a.jnz("boot_v2")
    # The monitor's E02D sequence, inlined: read the new page's first
    # fourteen bytes to C1B2 and run them if they announce themselves.
    a.call(EC00)
    a.db(0x00, 0x00, 0x0D, 0x00, STUB_RAM & 0xFF, STUB_RAM >> 8)
    a.lda(STUB_RAM)
    a.cpi(0xCC)
    a.jz(STUB_RAM)
    # Not bootable.  Should be unreachable -- the tool refuses such entries
    # -- but a wrong page must not strand the machine: go home to the menu
    # by booting page 0 through the very same path.
    a.mvi(D, 0)
    a.mvi(A, 0)
    a.jmp("boot")
    a.label("boot_v2")
    a.jmp(0xFFF0)

    emit_clear(a)
    emit_blit7(a)
    for label, _, _, _, data in strips:
        a.label(label)
        a.db(*data)

    menu = a.link()
    if len(menu) > MENU_MAX:
        raise SystemExit(f"error: menu is {len(menu)} bytes, "
                         f"limit {MENU_MAX} (fewer or shorter names)")
    return menu


# ---------------------------------------------------------------------------
# The demo cartridge: proof the shelf works, generated rather than shipped
# ---------------------------------------------------------------------------
DEMO_ORG = 0x8000


def build_demo() -> tuple:
    a = MAsm(DEMO_ORG)
    texts = [("t0", 40, "ONE ROM CARTRIDGE OK"),
             ("t1", 60, "RESET RELOADS THIS CARTRIDGE"),
             ("t2", 80, "POWER CYCLE FOR THE MENU")]
    strips = [(f"s_{tag}", ln, TEXT_COL, len(tx), strip(tx))
              for tag, ln, tx in texts]
    a.lxi(RP_SP, MENU_SP)
    a.call("clear")
    emit_strip_calls(a, [(lb, ln, co, w) for lb, ln, co, w, _ in strips])
    a.label("spin")
    a.jmp("spin")
    emit_clear(a)
    emit_blit7(a)
    for label, _, _, _, data in strips:
        a.label(label)
        a.db(*data)
    return a.link(), DEMO_ORG, DEMO_ORG


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def boot_stub(src: int, count: int, dest: int, exec_: int) -> bytes:
    """The monitor's fourteen-byte contract: CC doubles as CZ EC00h, the
    six inline bytes are EC00h's arguments, and the jump is ours."""
    return bytes([0xCC, EC00 & 0xFF, EC00 >> 8,
                  src & 0xFF, src >> 8,
                  count & 0xFF, count >> 8,
                  dest & 0xFF, dest >> 8,
                  0xC3, exec_ & 0xFF, exec_ >> 8,
                  0xFF, 0xFF])


def pad_page(data: bytes) -> bytes:
    assert len(data) <= PAGE
    return data + bytes([0xFF]) * (PAGE - len(data))


def check_hotspot_clear(data: bytes, what: str) -> None:
    tail = data[HOTSPOT_MODULE_ADDR:]
    if tail and any(x not in (0x00, 0xFF) for x in tail):
        raise SystemExit(f"error: {what} carries data in the hotspot region "
                         f"(module 0x3FE0-0x3FFF); those 32 bytes are page-"
                         f"select registers on every page and cannot hold "
                         f"payload")


def page_from_rmm(data: bytes, name: str, v2: bool = False) -> bytes:
    if len(data) > PAGE:
        raise SystemExit(f"error: {name} is {len(data)} bytes; the module "
                         f"window is {PAGE} and multi-page cartridges are "
                         f"not supported yet")
    want, mach = (0xCD, "PMD 85-2") if v2 else (0xCC, "PMD 85-3")
    if data[0] != want:
        hint = ""
        if not v2 and data[0] == 0xCD:
            hint = ' -- it starts with CDh, a PMD 85-2 module: use "rmm2"'
        elif v2 and data[0] == 0xCC:
            hint = ' -- it starts with CCh, a PMD 85-3 module: use "rmm"'
        raise SystemExit(f"error: {name} does not start with "
                         f"{want:02X}h, so the {mach} monitor would never "
                         f"boot it{hint}")
    check_hotspot_clear(data, name)
    # Pad with 0x00, not 0xFF: a real module image can be over-read (the
    # stock BASIC stub reads 1026 bytes of a block that ends two short) and
    # on the measured machine an undriven module bus reads 0x00.  Padding
    # the same keeps a multiload boot byte-identical to a real cartridge's.
    return data + bytes(PAGE - len(data))


def page_from_binary(payload: bytes, load: int, exec_: int, name: str) -> bytes:
    if PAYLOAD_BASE + len(payload) > HOTSPOT_MODULE_ADDR:
        raise SystemExit(f"error: {name} is {len(payload)} bytes; at most "
                         f"{HOTSPOT_MODULE_ADDR - PAYLOAD_BASE} fit a page")
    if load + len(payload) > 0xC000:
        # This also keeps the load clear of the executing stub at C1B2 and
        # of the replay stack in the VRAM margin -- both live above C000.
        raise SystemExit(f"error: {name} would load over "
                         f"{load + len(payload) - 1:04X}; RAM ends at BFFF")
    if not (load <= exec_ < load + len(payload)):
        raise SystemExit(f"error: {name}: exec {exec_:04X} outside the "
                         f"loaded image")
    stub = boot_stub(PAYLOAD_BASE, ec_count(len(payload)), load, exec_)
    return pad_page(stub + bytes(PAYLOAD_BASE - len(stub)) + payload)


def build_pages(entries: list, root: Path) -> tuple:
    """-> (pages, menu_entries).  Page 0 is filled in by the caller once the
    menu exists; a placeholder holds its slot."""
    if not entries:
        raise SystemExit("error: empty manifest")
    if len(entries) > MAX_ENTRIES:
        raise SystemExit(f"error: {len(entries)} entries; the menu's key row "
                         f"holds {MAX_ENTRIES}")
    pages = [None]
    menu_entries = []
    for ent in entries:
        name = ent["name"].upper()
        page_no = len(pages)
        kind = ent.get("type", "rmm")
        if kind in ("rmm", "rmm2"):
            data = (root / ent["file"]).read_bytes()
            pages.append(page_from_rmm(data, ent["file"], v2=(kind == "rmm2")))
        elif kind == "binary":
            payload = (root / ent["file"]).read_bytes()
            load = int(ent["load"], 0)
            exec_ = int(ent.get("exec", ent["load"]), 0)
            pages.append(page_from_binary(payload, load, exec_, ent["file"]))
        elif kind == "demo":
            payload, load, exec_ = build_demo()
            pages.append(page_from_binary(payload, load, exec_, "demo"))
        else:
            raise SystemExit(f"error: unknown entry type {kind!r}")
        menu_entries.append({"name": name, "page": page_no,
                             "v2": kind == "rmm2"})
    if len(pages) > MAX_PAGES:
        raise SystemExit(f"error: {len(pages)} pages; hotspot addressing "
                         f"reaches {MAX_PAGES}")

    menu = build_menu(menu_entries)
    stub = boot_stub(PAYLOAD_BASE, ec_count(len(menu)), MENU_ORG, MENU_ORG)
    pages[0] = pad_page(stub + bytes(PAYLOAD_BASE - len(stub)) + menu)
    return pages, menu_entries


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------
def emit_c(path: Path, name: str, pages: list, menu_entries: list) -> None:
    out = ["// Generated by tools/make_multiload.py -- do not edit.",
           f"// {len(pages)} pages: menu + "
           + ", ".join(e['name'] for e in menu_entries), "",
           '#include "rom_images.h"', "",
           "#if !MHB_MULTILOAD",
           '#error "this image is a multiload set; build with '
           '-DMHB_MODULE_MULTILOAD=ON (and MHB_BANK_SOURCE=MODULE)"',
           "#endif", "",
           "_Static_assert(MHB_BANKS == 8,",
           '               "a multiload image is module-shaped: eight banks");',
           "",
           f'const char *mhb_image_name = "{name}";',
           "const uint8_t mhb_bank_present = 0xFF;",
           f"const unsigned mhb_page_count = {len(pages)};", "",
           f"const uint8_t mhb_pages[{len(pages)}][MHB_BANKS][MHB_BANK_SIZE]"
           " = {"]
    for p, page in enumerate(pages):
        label = "menu" if p == 0 else menu_entries[p - 1]["name"]
        out.append(f"    // page {p}: {label}")
        out.append("    {")
        for b in range(8):
            out.append(f"        {{ // bank {b}")
            bank = page[b * 2048:(b + 1) * 2048]
            for j in range(0, 2048, 16):
                row = ", ".join(f"0x{x:02X}" for x in bank[j:j + 16])
                out.append(f"            {row},")
            out.append("        },")
        out.append("    },")
    out.append("};")
    out.append("")
    path.write_text("\n".join(out))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--pages-out", type=Path,
                    help="also dump each page as a raw 16 KB file here")
    args = ap.parse_args()

    m = json.loads(args.manifest.read_text())
    pages, menu_entries = build_pages(m["entries"], args.manifest.parent)
    emit_c(args.output, m.get("name", args.manifest.stem), pages, menu_entries)
    if args.pages_out:
        args.pages_out.mkdir(parents=True, exist_ok=True)
        for p, page in enumerate(pages):
            (args.pages_out / f"page{p:02d}.bin").write_bytes(page)
    print(f"wrote {args.output}: {len(pages)} pages "
          f"({len(pages) * 16} KB), menu + "
          + ", ".join(f"{KEYS[i][0]}:{e['name']}"
                      for i, e in enumerate(menu_entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
