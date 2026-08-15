"""Run the multiload menu in the emulator, against a paged module model.

The machine side is the monitor's module-boot contract, reconstructed
here instruction-for-instruction from the monit3B disassembly
(docs/ROM-module.md) rather than shipped as a ROM dump: the fourteen-byte
read at E02D, and the EC00h block-read routine with its inline arguments,
held strobe and count+1 transfer.  Testing against the reconstruction
keeps the repo self-contained and asserts our understanding of the ABI --
if the disassembly reading were wrong, nothing downstream would boot.

The board side is the Bus's paged-module model: hotspot addresses switch
pages, and every switch and every module read is clock-stamped so the
menu's delay contract is measured, not assumed.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import make_multiload as ml        # noqa: E402
from i8080emu import Bus, CPU      # noqa: E402

SCRATCH = Path("/tmp/claude-0/-home-user-TRS-80-bt-bridge/"
               "5ace1ab9-c2af-5ddd-9aa7-bdba28cb2fc4/scratchpad")
BASIC3 = (SCRATCH / "romzip/PMD85-rom-files/RomModul/Basic3/basic3.rmm")

# The menu's delay loop in bus clocks: 0x7000 iterations of a 4-instruction
# loop, six bus accesses per iteration.  The assertion threshold sits well
# under the computed value but far above anything accidental.
DELAY_MIN_CLOCKS = 150_000


def fake_monitor() -> bytes:
    """An 8 KB image at E000 holding just the module-boot machinery."""
    rom = bytearray(0x2000)

    boot = ml.MAsm(0xE02D)
    boot.call(ml.EC00)
    boot.db(0x00, 0x00, 0x0D, 0x00, ml.STUB_RAM & 0xFF, ml.STUB_RAM >> 8)
    boot.lda(ml.STUB_RAM)
    boot.cpi(0xCC)
    boot.jz(ml.STUB_RAM)
    boot.db(0x76)                                   # HLT: no module to boot
    code = boot.link()
    rom[0x02D:0x02D + len(code)] = code

    # EC00h, byte-faithful to monit3B: mode-set the module 8255, take six
    # inline argument bytes via XTHL, then read with the strobe held,
    # walking the address through the ports themselves.  Transfers
    # count+1 bytes -- the INR B before the loop plus the B-only test.
    ec = ml.MAsm(0xEC00)
    ec.mvi(ml.A, 0x90)
    ec.out(0xFB)
    ec.db(0xE3)                                     # XTHL
    ec.mov(ml.A, ml.M); ec.out(0xF9); ec.inx(2)     # src low
    ec.mov(ml.A, ml.M); ec.out(0xFA); ec.inx(2)     # src high
    ec.mov(ml.C, ml.M); ec.inx(2)                   # count low
    ec.mov(ml.B, ml.M); ec.inr(ml.B); ec.inx(2)     # count high, +1
    ec.mov(ml.E, ml.M); ec.inx(2)                   # dest low
    ec.mov(ml.D, ml.M); ec.inx(2)                   # dest high
    ec.label("rd")
    ec.inp(0xF8)
    ec.stax_d()
    ec.inx(1)
    ec.dcx(0)
    ec.inp(0xF9); ec.inr(ml.A); ec.out(0xF9); ec.jnz("chk")
    ec.inp(0xFA); ec.inr(ml.A); ec.out(0xFA)
    ec.label("chk")
    ec.mov(ml.A, ml.B); ec.ora(ml.A); ec.jnz("rd")
    ec.mvi(ml.A, 0xFF); ec.out(0xFA)                # park
    ec.db(0xE3)                                     # XTHL
    ec.ret()
    code = ec.link()
    rom[0xC00:0xC00 + len(code)] = code
    return bytes(rom)


def shelf():
    entries = [{"type": "rmm", "name": "BASIC-G 3.0", "file": "basic3.rmm"},
               {"type": "demo", "name": "TEST CARD"}]
    root = BASIC3.parent
    ents = [dict(entries[0], file=str(BASIC3)), entries[1]]
    return ml.build_pages([{"type": "rmm", "name": "BASIC-G 3.0",
                            "file": BASIC3.name},
                           {"type": "demo", "name": "TEST CARD"}],
                          root)


def boot(bus: Bus) -> CPU:
    """The state the monitor reaches at E02D: mirror long gone, ROM mapped,
    a stack near the top of RAM."""
    cpu = CPU(bus)
    bus.startup_map = False
    cpu.sp = 0xBFEC
    cpu.pc = 0xE02D
    return cpu


def run_until_pc(cpu: CPU, target: int, max_steps: int = 3_000_000) -> bool:
    for _ in range(max_steps):
        if cpu.pc == target and not cpu.halted:
            return True
        cpu.step()
    return False


def run_steps(cpu: CPU, n: int) -> None:
    for _ in range(n):
        cpu.step()


def strip_on_screen(bus: Bus, line: int, col: int, data: bytes) -> bool:
    w = len(data) // 7
    for r in range(7):
        at = ml.vaddr(line + r, col)
        if bytes(bus.ram[at:at + w]) != data[r * w:(r + 1) * w]:
            return False
    return True


def test_menu_loads_and_renders():
    pages, ents = shelf()
    bus = Bus(fake_monitor(), pages=pages)
    cpu = boot(bus)
    run_steps(cpu, 400_000)

    # The stub delivered the menu where it said it would...
    stub = pages[0][:14]
    menu_len = ((stub[6] << 8) | stub[5]) + 1
    menu = pages[0][ml.PAYLOAD_BASE:ml.PAYLOAD_BASE + menu_len]
    assert bytes(bus.ram[ml.MENU_ORG:ml.MENU_ORG + menu_len]) == menu

    # ...and the shelf is on screen: title and both entries, byte-exact.
    assert strip_on_screen(bus, ml.LN_TITLE, ml.TEXT_COL,
                           ml.strip("ONE ROM MULTILOAD"))
    assert strip_on_screen(bus, ml.LN_ENTRY0, ml.TEXT_COL,
                           ml.strip("1 BASIC-G 3.0"))
    assert strip_on_screen(bus, ml.LN_ENTRY0 + ml.ENTRY_STEP, ml.TEXT_COL,
                           ml.strip("2 TEST CARD"))
    # Nothing switched pages while drawing.
    assert bus.page_events == []


def test_key_boots_basic_with_the_stock_dance():
    pages, ents = shelf()
    basic = BASIC3.read_bytes()
    bus = Bus(fake_monitor(), pages=pages)
    cpu = boot(bus)
    run_steps(cpu, 400_000)

    bus.press(0, 2)                               # key "1"
    assert run_until_pc(cpu, ml.STUB_RAM), "replay never reached C1B2"
    # The replayed stub is BASIC's own, read from the new page.
    assert bytes(bus.ram[ml.STUB_RAM:ml.STUB_RAM + 12]) == basic[:12]

    # BASIC's stub loads its second stage to B800 and jumps in.  1026
    # bytes, not 1024: EC00h transfers count+1 and the stock stub's count
    # is 0x0401, so the last two reads run past the 10 KB image -- compare
    # against the page, which carries the same 0x00s the real machine's
    # undriven bus supplied.
    assert run_until_pc(cpu, 0xB800), "BASIC stage 2 never entered"
    assert bytes(bus.ram[0xB800:0xB800 + 0x402]) == \
        bytes(pages[ents[0]["page"]][0x2400:0x2802])

    # Exactly one page switch, to BASIC's page...
    assert [p for _, p in bus.page_events] == [ents[0]["page"]]
    # ...and the machine held its side of the contract: silence between the
    # touch and the next module read, measured on the bus clock.
    touch = bus.page_events[0][0]
    next_read = next(c for c, _ in bus.mod_reads if c > touch)
    assert next_read - touch >= DELAY_MIN_CLOCKS, \
        f"only {next_read - touch} clocks of post-touch silence"


def test_key_boots_the_demo_cartridge():
    pages, ents = shelf()
    bus = Bus(fake_monitor(), pages=pages)
    cpu = boot(bus)
    run_steps(cpu, 400_000)

    bus.press(1, 2)                               # key "2"
    assert run_until_pc(cpu, ml.DEMO_ORG), "demo cartridge never entered"
    payload, load, _ = ml.build_demo()
    assert bytes(bus.ram[load:load + len(payload)]) == payload
    assert bus.mod_page == ents[1]["page"]

    # Let it draw; its banner is the visible proof of the whole chain.
    run_steps(cpu, 600_000)
    assert strip_on_screen(bus, 40, ml.TEXT_COL,
                           ml.strip("ONE ROM CARTRIDGE OK"))


def test_reset_relaunches_the_mapped_cartridge():
    # Reset while a cartridge's page is mapped boots that cartridge again,
    # exactly as a really-plugged module would -- the documented semantics.
    pages, ents = shelf()
    bus = Bus(fake_monitor(), pages=pages)
    cpu = boot(bus)
    run_steps(cpu, 400_000)
    bus.press(1, 2)
    assert run_until_pc(cpu, ml.DEMO_ORG)

    bus.release_all()
    cpu2 = boot(bus)                              # reset: fresh CPU, same page
    assert run_until_pc(cpu2, ml.DEMO_ORG), "reset did not relaunch"
    assert bus.mod_page == ents[1]["page"]


def test_unbootable_page_falls_back_to_the_menu():
    # Belt-and-braces path: a page with no CC stub must not strand the
    # machine.  The menu re-boots page 0 through the same dance, and its
    # release-wait keeps the still-held key from instantly re-selecting.
    pages, ents = shelf()
    broken = list(pages)
    broken[1] = bytes([0x00]) + pages[1][1:]
    bus = Bus(fake_monitor(), pages=broken)
    cpu = boot(bus)
    run_steps(cpu, 400_000)

    bus.press(0, 2)                               # select the broken page
    assert run_until_pc(cpu, ml.MENU_ORG, 4_000_000), \
        "menu never reloaded after the failed boot"
    assert [p for _, p in bus.page_events] == [1, 0]
    # Key still held: the menu must be parked in its release-wait, page 0
    # still mapped, not looping through the broken page again.
    run_steps(cpu, 200_000)
    assert bus.mod_page == 0
    assert [p for _, p in bus.page_events] == [1, 0]


MONIT3B = SCRATCH / "romzip/PMD85-rom-files/Monitor/PMD85-3/monit3B.rom"
BASIC2A = SCRATCH / "romzip/PMD85-rom-files/RomModul/Basic2A/basic2A.rmm"


def test_v2_entry_boots_through_fff0():
    # A PMD 85-2 module on the shelf.  Selecting it must ride the -3
    # monitor's own switch at FFF0h: relocate the compat monitor to 8000h,
    # let it find the CD stub, and land in BASIC 2A at 0000h.  This test
    # needs the real monit3B -- the fake monitor carries no FFF0 machinery
    # -- so it runs the whole chain against the genuine article, which also
    # re-proves the menu against the real E02D and EC00h.
    import pytest
    if not (MONIT3B.exists() and BASIC2A.exists()):
        pytest.skip("reference ROMs not present")

    b2a = BASIC2A.read_bytes()
    pages, ents = ml.build_pages(
        [{"type": "rmm", "name": "BASIC-G 3.0", "file": BASIC3.name},
         {"type": "rmm2", "name": "BASIC 2A", "file": str(BASIC2A)}],
        BASIC3.parent)
    bus = Bus(MONIT3B.read_bytes(), pages=pages)
    cpu = boot(bus)
    run_steps(cpu, 400_000)
    assert strip_on_screen(bus, ml.LN_ENTRY0 + ml.ENTRY_STEP, ml.TEXT_COL,
                           ml.strip("2 BASIC 2A"))

    bus.press(1, 2)                               # key "2"
    # Through the switch: relocated monitor entry, its module check, the
    # stub, and finally BASIC 2A's own entry at 0000h.
    assert run_until_pc(cpu, 0x8000, 6_000_000), "FFF0 never landed at 8000"
    assert run_until_pc(cpu, 0x802D, 6_000_000), "-2 module check not reached"
    assert run_until_pc(cpu, 0x0000, 6_000_000), "BASIC 2A never entered"
    # The -2 monitor went AllRAM on the way -- its relocated trampoline.
    assert not bus.rom_visible
    # The payload the -2 stub names: 9204 bytes from module 12 to 0000.
    assert bytes(bus.ram[0:9204]) == b2a[12:12 + 9204]
    # One page switch, delay honoured -- same contract as a -3 boot.
    assert [p for _, p in bus.page_events] == [ents[1]["page"]]
    touch = bus.page_events[0][0]
    next_read = next(c for c, _ in bus.mod_reads if c > touch)
    assert next_read - touch >= DELAY_MIN_CLOCKS


def test_v2_reset_lands_at_the_v3_prompt():
    # Reset with a -2 page mapped: the -3 monitor reads CDh, refuses it,
    # and must fall through to its prompt rather than crash or boot.  The
    # fake monitor HLTs where the real one would prompt, which makes the
    # outcome assertable.
    pages, ents = ml.build_pages(
        [{"type": "rmm", "name": "BASIC-G 3.0", "file": BASIC3.name},
         {"type": "rmm2", "name": "BASIC 2A", "file": str(BASIC2A)}],
        BASIC3.parent)
    bus = Bus(fake_monitor(), pages=pages)
    bus.mod_page = ents[1]["page"]
    cpu = boot(bus)
    for _ in range(100_000):
        if cpu.halted:
            break
        cpu.step()
    assert cpu.halted, "the -3 monitor should have refused the CD stub"
    assert bus.ram[ml.STUB_RAM] == 0xCD


def test_multipage_binary_boots_across_page_switches(tmp_path):
    # A payload too big for one page: the stub loads a generated stage-2,
    # which pages chunks in with the same touch-and-wait contract the menu
    # uses, then jumps.  Synthetic 20 KB payload, -3 mode, fake monitor.
    payload = bytes((i * 89 + 31) & 0xFF for i in range(20_000))
    g = tmp_path / "big.bin"
    g.write_bytes(payload)
    pages, ents = ml.build_pages(
        [{"type": "binary", "name": "BIG", "file": "big.bin",
          "load": "0x1000", "exec": "0x1000"}], tmp_path)
    assert len(pages) == 3, "menu + two cartridge pages expected"
    bus = Bus(fake_monitor(), pages=pages)
    cpu = boot(bus)
    run_steps(cpu, 400_000)
    bus.press(0, 2)                               # key "1"
    assert run_until_pc(cpu, 0x1000, 8_000_000), "big binary never entered"
    assert bytes(bus.ram[0x1000:0x1000 + len(payload)]) == payload
    # Page switches: the menu touches page 1, stage-2 re-touches it (cheap,
    # idempotent) and then pages to 2.  Every touch honours the delay.
    assert [p for _, p in bus.page_events] == [1, 1, 2]
    for k, (t, _) in enumerate(bus.page_events):
        later = [c for c, _ in bus.mod_reads if c > t]
        if later:
            assert later[0] - t >= DELAY_MIN_CLOCKS, \
                f"switch {k}: only {later[0] - t} clocks of silence"


def test_willy2_boots_through_the_compat_monitor():
    # The full prize chain, against the real ROM: menu -> key -> hotspot ->
    # JMP FFF0 -> the -3 relocates its own -2 monitor -> CD stub -> stage-2
    # pages 30.7 KB across two pages through the relocated reader at 8C00h
    # -> JMP 0000 -> Jet Set Willy draws its title screen.
    import pytest
    GAMES = SCRATCH / "games/games-4004-482.ptp"
    if not (MONIT3B.exists() and GAMES.exists()):
        pytest.skip("reference ROMs/games not present")
    import ptp_lib
    prog = ptp_lib.find_program(GAMES, "WILLY2")
    payload = bytes(prog['body'])

    pages, ents = ml.build_pages(
        [{"type": "rmm", "name": "BASIC-G 3.0", "file": BASIC3.name},
         {"type": "tape", "name": "JET SET WILLY", "file": str(GAMES),
          "program": "WILLY2", "mode": "v2", "exec": "0x0000"}],
        BASIC3.parent)
    bus = Bus(MONIT3B.read_bytes(), pages=pages)
    cpu = boot(bus)
    run_steps(cpu, 400_000)
    bus.press(1, 2)                               # key "2"
    assert run_until_pc(cpu, 0x8000, 8_000_000), "FFF0 never landed"
    assert run_until_pc(cpu, ml.STAGE2_ORG, 8_000_000), "stage-2 not entered"
    assert run_until_pc(cpu, 0x0000, 30_000_000), "game never entered"
    assert bytes(bus.ram[0:len(payload)]) == payload, \
        "image differs at the moment of entry"
    run_steps(cpu, 2_500_000)                     # let the title draw
    lit = sum(1 for ln in range(256) for c in range(48)
              if bus.ram[0xC000 + ln * 64 + c])
    assert lit > 1500, f"title screen did not draw ({lit} lit bytes)"


def test_pack_tool_refuses_the_refusable(tmp_path):
    import pytest
    # An rmm that cannot boot.
    bad = tmp_path / "bad.rmm"
    bad.write_bytes(bytes([0x00]) * 2048)
    with pytest.raises(SystemExit, match="CCh"):
        ml.build_pages([{"type": "rmm", "name": "X", "file": "bad.rmm"}],
                       tmp_path)
    # A generation mix-up gets pointed at the right type, both ways.
    v2 = tmp_path / "v2.rmm"
    v2.write_bytes(bytes([0xCD]) + bytes(2047))
    with pytest.raises(SystemExit, match="rmm2"):
        ml.build_pages([{"type": "rmm", "name": "X", "file": "v2.rmm"}],
                       tmp_path)
    v3 = tmp_path / "v3.rmm"
    v3.write_bytes(bytes([0xCC]) + bytes(2047))
    with pytest.raises(SystemExit, match='use "rmm"'):
        ml.build_pages([{"type": "rmm2", "name": "X", "file": "v3.rmm"}],
                       tmp_path)
    # Payload in the hotspot region.
    hot = tmp_path / "hot.rmm"
    img = bytearray(16384)
    img[0] = 0xCC
    img[0x3FF0] = 0x42
    hot.write_bytes(bytes(img))
    with pytest.raises(SystemExit, match="hotspot"):
        ml.build_pages([{"type": "rmm", "name": "X", "file": "hot.rmm"}],
                       tmp_path)
    # A binary that would load past RAM -- which also covers the executing
    # stub at C1B2 and the replay stack, both above C000.
    g = tmp_path / "g.bin"
    g.write_bytes(bytes(0x100))
    with pytest.raises(SystemExit, match="BFFF"):
        ml.build_pages([{"type": "binary", "name": "X", "file": "g.bin",
                         "load": "0xC180", "exec": "0xC180"}], tmp_path)
    # Too many entries for the key row.
    many = [{"type": "demo", "name": f"E{i}"} for i in range(17)]
    with pytest.raises(SystemExit, match="16"):
        ml.build_pages(many, tmp_path)


def test_two_touch_paging_and_directories(tmp_path):
    # The omnibus features in miniature: a dir submenu boots from the main
    # menu, its BACK entry returns, and the emulator's two-touch model
    # names pages past 31 (bank latch at 3FD8h+j, commit at 3FE0h+n,
    # single touches still meaning pages 0-31).
    pages, ents = ml.build_pages(
        [{"type": "dir", "name": "GAMES",
          "entries": [{"type": "demo", "name": "CARD A"}]},
         {"type": "demo", "name": "CARD B"}],
        tmp_path)
    assert [e["page"] for e in ents] == [1, 3]
    bus = Bus(fake_monitor(), pages=pages)
    cpu = boot(bus)
    run_steps(cpu, 400_000)
    bus.press(0, 2)                              # key 1: the GAMES dir
    run_steps(cpu, 2_000_000)
    bus.release_all()
    assert bus.mod_page == 1, "submenu page not mapped"
    run_steps(cpu, 100_000)                      # let its release-wait pass
    bus.press(1, 2)                              # key 2: BACK (after CARD A)
    run_steps(cpu, 2_000_000)
    bus.release_all()
    assert bus.mod_page == 0, "BACK did not return to the main menu"

    # the model's arithmetic, straight on the latches
    bus.mod_bank = 0
    bus.mod_b, bus.mod_c = 0xDA, 0x3F            # bank latch: 3FDAh -> 2
    bus._module_hotspot_check()
    bus.mod_b, bus.mod_c = 0xE5, 0x3F            # commit: 3FE5h
    bus._module_hotspot_check()
    assert bus.page_events[-1][1] == 2 * 32 + 5
    assert bus.mod_bank == 0, "commit must reset the latch"
