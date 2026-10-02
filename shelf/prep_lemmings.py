#!/usr/bin/env python3
"""LEMMINGS from the module: its sixty level sectors served off ROM pages.

The game streams its levels from tape: a 32 KB engine, then one
standard-format block per level ("SEKTOR01".."SEKTOR60", plus a
"CHECKSUM" block, numbers 1-61) read on demand by the game's own
byte-level tape reader at 0732h -- IN 1Fh for RxRDY, IN 1Eh for the
byte -- through its own header search (0742h) and data loop.  On a
monitor whose 8B6Ch starts with PUSH B the game adopts the monitor's
reader instead (071Ch), which on a -3 is a bit-level routine no byte
stream can feed.

This prep keeps every line of the game's loader and changes only where
the bytes come from.  build_images calls prepare(entry, corpus, stage):

- lemmings.stream: the tape's sector blocks, header and body, verbatim
  and back to back -- shipped as raw data pages after the program
  (make_multiload's "data" entry), never loaded into RAM.
- lemmings-rom.hi: a high segment at BC00h-BFFFh carrying the fetch
  routine, a 61-entry table of (page, address, length) per sector, the
  reader's pointer, and at BFFEh the byte the builder pokes with the
  first data page's number.
- three patches: 071Ch returns at once (keep the game's reader);
  0732h becomes "next byte from the staged block"; the CALL 0742h at
  07CFh (header search, after the cache check) calls the fetch first.

The fetch copies the wanted sector's block from the module into a
staging buffer at A800h -- two-touch paging exactly as the menu does
it, byte reads through the module 8255 at F8h-FAh, following the block
across a page boundary where the stream crosses one -- then parks the
module and falls into the game's header search, which now reads the
same bytes the tape would have delivered.  Verified in the emulator
from a cold menu boot: S loads sector 1, SPACE starts the level.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tools'))
import ptp_lib                                           # noqa: E402
from make_multiload import (MAsm, PAGE_CHUNK, HOTSPOT_MODULE_ADDR,  # noqa
                            BANK_DELAY_ITERS, DELAY_ITERS,
                            A, B, C, D, E, H, L, M, RP_B, RP_D, RP_H)

TAPE = 'game-lemmings.ptp'
FIRST_SECTOR_BLOCK = 4          # blocks 0-3: LEMMINGS header, body, 2 raws
STAGING = 0xA800                # the staged block; sectors load to 9000h-A6xxh
CODE = 0xBC00                   # fetch + table, in the high segment
PTR = 0xBFF8                    # the reader's pointer into the staged block
PAGE = 0xBFFA                   # the page being read
BASE = 0xBFFE                   # first data page -- poked by the builder
HIGH_END = 0xC000
WANTED = 0x0F81                 # the game's "sector wanted" variable
HEADER_SEARCH = 0x0742


def sectors(tape: Path) -> list:
    """-> [(number, header_block + body_block)] in tape order."""
    blocks = ptp_lib.blocks_of(tape)[FIRST_SECTOR_BLOCK:]
    out = []
    for i in range(0, len(blocks), 2):
        hdr = blocks[i]
        assert len(hdr) == 63 and hdr[:48] == ptp_lib.LEADER, "sector header"
        out.append((hdr[48], hdr + blocks[i + 1]))
    return out


def fetch_routine(table: bytes) -> bytes:
    a = MAsm(CODE)
    a.label("fetch")
    a.lda(WANTED)                   # sector 1..61 -> table entry
    a.dcr(A)
    a.mov(L, A)
    a.mvi(H, 0)
    a.mov(D, H)
    a.mov(E, L)
    a.dad(RP_H)
    a.dad(RP_H)
    a.dad(RP_D)                     # HL = 5 * index
    a.lxi(RP_D, "table")
    a.dad(RP_D)
    a.mov(A, M)
    a.sta(PAGE)
    a.db(0xE5)                      # PUSH H
    a.call("select")
    a.db(0xE1)                      # POP H
    a.inx(RP_H)
    a.mov(E, M)
    a.inx(RP_H)
    a.mov(D, M)                     # DE = module address
    a.inx(RP_H)
    a.mov(C, M)
    a.inx(RP_H)
    a.mov(B, M)                     # BC = block length
    a.lxi(RP_H, STAGING)
    a.db(0x22, PTR & 0xFF, PTR >> 8)        # SHLD PTR
    a.label("loop")
    a.mov(A, E)
    a.out(0xF9)
    a.mov(A, D)
    a.out(0xFA)                     # strobe live: high byte < 40h
    a.inp(0xF8)
    a.mov(M, A)
    a.inx(RP_H)
    a.inx(RP_D)
    a.mov(A, D)
    a.cpi(PAGE_CHUNK >> 8)
    a.jnz("next")
    a.mov(A, E)
    a.cpi(PAGE_CHUNK & 0xFF)
    a.jnz("next")
    a.lda(PAGE)                     # the block crosses into the next page
    a.inr(A)
    a.sta(PAGE)
    a.push_b()
    a.db(0xE5)
    a.call("select")
    a.db(0xE1)
    a.pop_b()
    a.lxi(RP_D, 0)
    a.label("next")
    a.dcx(RP_B)
    a.mov(A, B)
    a.ora(C)
    a.jnz("loop")
    a.mvi(A, 0xFF)
    a.out(0xFA)                     # park
    a.jmp(HEADER_SEARCH)

    # Two-touch to page BASE + PAGE and sit out the rebuild -- the menu's
    # hs_touch dance, with the page number split here.
    a.label("select")
    a.lda(PAGE)
    a.lxi(RP_H, BASE)
    a.add(M)
    a.mov(B, A)
    a.ani(0x1F)
    a.ori(0xE0)
    a.mov(E, A)                     # commit hotspot low byte
    a.mov(A, B)
    for _ in range(5):
        a.db(0x0F)                  # RRC
    a.ani(0x07)
    a.ori(0xD8)
    a.mov(D, A)                     # bank latch low byte
    a.mvi(A, 0x90)
    a.out(0xFB)
    a.mov(A, D)
    a.out(0xF9)
    a.mvi(A, HOTSPOT_MODULE_ADDR >> 8)
    a.out(0xFA)
    a.mvi(A, 0xFF)
    a.out(0xFA)
    a.lxi(RP_B, BANK_DELAY_ITERS)
    a.label("bdly")
    a.dcx(RP_B)
    a.mov(A, B)
    a.ora(C)
    a.jnz("bdly")
    a.mov(A, E)
    a.out(0xF9)
    a.mvi(A, HOTSPOT_MODULE_ADDR >> 8)
    a.out(0xFA)
    a.mvi(A, 0xFF)
    a.out(0xFA)
    a.lxi(RP_B, DELAY_ITERS)
    a.label("dly")
    a.dcx(RP_B)
    a.mov(A, B)
    a.ora(C)
    a.jnz("dly")
    a.ret()
    a.label("table")
    a.db(*table)
    return a.link()


PATCHES = [
    # 071Ch: LXI H,8B6Ch / MOV A,M / CPI C5h / RNZ -> RET: never adopt
    # the host monitor's reader.
    {"find": "216C8B7EFEC5C0", "replace": "C9000000000000", "count": 1},
    # 0732h: the byte reader.  Was: IN F5h / ANI 40h / STC / RZ (stop
    # key aborts) / IN 1Fh / ANI 02h / JZ 0732h / IN 1Eh / RET.  Now:
    # PUSH H / LHLD PTR / MOV A,M / INX H / SHLD PTR / POP H / ORA A
    # (carry clear: no abort) / RET.
    {"find": "DBF5E64037C8DB1FE602CA3207DB1EC9",
     "replace": "E52A" + f"{PTR & 0xFF:02X}{PTR >> 8:02X}" + "7E2322"
     + f"{PTR & 0xFF:02X}{PTR >> 8:02X}" + "E1B7C900000000", "count": 1},
    # 07CFh: CALL 0742h / JC 09A2h -> CALL fetch / JC 09A2h.
    {"find": "CD4207DAA209",
     "replace": "CD" + f"{CODE & 0xFF:02X}{CODE >> 8:02X}" + "DAA209",
     "count": 1},
]


def prepare(entry: dict, corpus: Path, stage: Path) -> dict:
    """Write the stream and the high segment into `stage`; -> the entry
    with its high segment, data pages and patches set."""
    tape = next(c for c in corpus.rglob(TAPE) if c.is_file())
    secs = sectors(tape)
    assert [n for n, _ in secs] == list(range(1, len(secs) + 1)), \
        "sector numbers are not 1..N in tape order"
    stream = bytearray()
    table = bytearray()
    for _, blob in secs:
        off = len(stream)
        page, addr = divmod(off, PAGE_CHUNK)
        table += bytes([page, addr & 0xFF, addr >> 8,
                        len(blob) & 0xFF, len(blob) >> 8])
        stream += blob
    (stage / 'lemmings.stream').write_bytes(bytes(stream))
    code = fetch_routine(bytes(table))
    assert CODE + len(code) <= PTR, f"fetch + table is {len(code)} bytes"
    high = bytearray(HIGH_END - CODE)
    high[:len(code)] = code
    (stage / 'lemmings-rom.hi').write_bytes(bytes(high))
    e = dict(entry)
    e['high'] = {"file": "lemmings-rom.hi", "load": f"0x{CODE:04X}"}
    e['data'] = {"file": "lemmings.stream", "page_at": f"0x{BASE:04X}"}
    e['patch'] = list(entry.get('patch', [])) + PATCHES
    return e


if __name__ == "__main__":
    corpus, stage = Path(sys.argv[1]), Path(sys.argv[2])
    stage.mkdir(parents=True, exist_ok=True)
    e = prepare({"name": "LEMMINGS"}, corpus, stage)
    print(e)
    print(f"stream {(stage / 'lemmings.stream').stat().st_size} bytes, "
          f"{len(fetch_routine(b''))} bytes of code")
