#!/usr/bin/env python3
"""A small 8080 emulator, for testing ROM images before they meet a machine.

Not a general-purpose emulator: it exists so that `make_ramtest.py`'s output
can be run against a *simulated* PMD 85-3 -- including one with deliberately
broken RAM -- and its beacons checked, before it is trusted to diagnose a
real machine. The sibling project's hardest-won lesson was that the
instrument is wrong more often than the machine is surprising; this is the
cheapest available defence against that.

The memory model mirrors the PMD 85-3, and the part that matters most is
the part that is easy to get wrong. There are three read maps, not two:

    startup     reads come from ROM everywhere, writes go to RAM.  This is
                what a reset leaves behind, and it is cleared by the first
                write to the system 8255 at F4-F7.
    ROM/RAM     RAM 0000-DFFF, ROM E000-FFFF.  Selected by PC4 of that 8255.
    AllRAM      RAM across all 64K.  **There is no ROM in the machine.**
                Selected when PC4 is low.

The third one is a trap. `OUT F7h` with bit 7 set is an 8255 *mode set*,
and a mode set clears the port C output latches -- so it drops PC4 and
takes the ROM out of the address space as a side effect. Any program that
issues one while executing from ROM stops existing on that instruction.
The machine's own monitor copies its next four instruction bytes into RAM
first and executes them from there (E0A3-E0B7 of monit3B); this emulator
models the drop-out so an image that forgets to do the same fails here
rather than on the bench.

See GPMD85Emulator src/SystemPIO.cpp WritePaging() and src/ChipMemory3.cpp
FindPointer() for the behaviour being copied.
"""

import sys

PARITY = [bin(i).count("1") % 2 == 0 for i in range(256)]

SYSTEM_PIO = range(0xF4, 0xF8)   # the 8255 whose PC4 decides where ROM is
SYSTEM_CWR = 0xF7                # ...and its control register

# The ROM module (ROM PACK) is not memory-mapped at all: it hangs behind
# its own 8255 at ports 88-8B.  The CPU writes the target address low
# byte to port B (89h) and high byte to port C (8Ah), then reads the data
# byte from port A (88h).  A15 set, or no module, reads FFh.  Modelled on
# GPMD85Emulator src/RomModule.cpp.
MODULE_A, MODULE_B, MODULE_C, MODULE_CWR = 0x88, 0x89, 0x8A, 0x8B


class Bus:
    """PMD 85-3 memory behaviour, with optional injected RAM faults."""

    def __init__(self, rom: bytes, stuck: dict | None = None,
                 decay_after: int | None = None,
                 rom_stuck: tuple | None = None,
                 rom_stuck_range: tuple | None = None,
                 sticky_map: bool = False,
                 map_clear_ports=None,
                 map_clear_on_in=(),
                 module: bytes | None = None):
        assert len(rom) == 0x2000
        self.rom = rom
        self.ram = bytearray(0x10000)
        self.startup_map = True
        # PC4 of the system 8255: ROM at E000-FFFF when set, AllRAM when
        # clear.  It comes up set, because until the first mode set nothing
        # has driven port C low.
        self.rom_visible = True
        # {address: (and_mask, or_mask)} applied on read-back: a dead bit
        # reads 0 (and_mask clears it) or 1 (or_mask sets it).
        self.stuck = stuck or {}
        # DRAM that is not being refreshed: a cell read more than this many
        # bus cycles after it was written comes back as zero.  This models
        # the one fault that a write-then-read-much-later test blames on the
        # cells when the real culprit is refresh.
        self.decay_after = decay_after
        # (and_mask, or_mask) applied to every ROM read: a data line broken
        # between the socket and the processor, which is what the bus-driver
        # rung of the diagnostic exists to catch.
        self.rom_stuck = rom_stuck
        # Limit the ROM fault to an address range, so a *marginal* line can
        # be modelled.  A line that is broken for every fetch stops the
        # processor executing this program at all -- which is realistic, and
        # which no ROM-resident diagnostic can ever report.
        self.rom_stuck_range = rom_stuck_range
        # A port write that goes nowhere: the startup map never clears, so
        # every read keeps coming from ROM and RAM is unreachable however
        # healthy it is.  This is a fault in the machine's I/O decoding, and
        # it looks exactly like dead memory unless something tests for it.
        self.sticky_map = sticky_map
        # Which port writes clear the startup mirror.  The default is what
        # GPMD85Emulator models -- any write to the system 8255 -- but the
        # real machine is the authority and it has already disagreed once,
        # so this is a knob rather than a constant.  Narrow it to model a
        # machine that only responds to the control register, or empty it
        # to model a latch that never clears at all.
        self.map_clear_ports = (SYSTEM_PIO if map_clear_ports is None
                                else map_clear_ports)
        self.map_clear_on_in = map_clear_on_in
        # The plugged ROM PACK, up to 32 KB, or None for an empty slot.
        self.module = module
        self.mod_b = 0            # port B latch: module address, low byte
        self.mod_c = 0            # port C latch: module address, high byte
        self.written_at = {}
        self.clock = 0
        self.rom_reads = []

    def read(self, a: int) -> int:
        a &= 0xFFFF
        self.clock += 1
        if self.startup_map or (self.rom_visible and a >= 0xE000):
            self.rom_reads.append(a)
            v = self.rom[a & 0x1FFF]
            if self.rom_stuck is not None:
                lo, hi = self.rom_stuck_range or (0, 0xFFFF)
                if lo <= a <= hi:
                    and_m, or_m = self.rom_stuck
                    v = (v & and_m) | or_m
            return v
        v = self.ram[a]
        if self.decay_after is not None:
            written = self.written_at.get(a)
            if written is None or self.clock - written > self.decay_after:
                v = 0x00
        if a in self.stuck:
            and_m, or_m = self.stuck[a]
            v = (v & and_m) | or_m
        return v

    def write(self, a: int, v: int) -> None:
        a &= 0xFFFF
        self.clock += 1
        self.ram[a] = v & 0xFF
        self.written_at[a] = self.clock

    def inp(self, port: int) -> int:
        self.clock += 1
        if port in self.map_clear_on_in:
            self.startup_map = False
        if port == MODULE_A:
            addr = (self.mod_c << 8) | self.mod_b
            if self.module is None or (addr & 0x8000):
                return 0xFF
            if addr >= len(self.module):
                return 0xFF
            return self.module[addr]
        return 0xFF

    def out(self, port: int, v: int) -> None:
        self.clock += 1
        if port == MODULE_B:
            self.mod_b = v & 0xFF
            return
        if port == MODULE_C:
            self.mod_c = v & 0xFF
            return
        if port == MODULE_CWR:
            if v & 0x80:
                self.mod_b = self.mod_c = 0    # mode set clears the latches
            return
        if self.sticky_map:
            return
        if port in self.map_clear_ports:
            self.startup_map = False    # a write to this 8255 clears it
        if (port & 0x8F) != (SYSTEM_CWR & 0x8F) or port not in SYSTEM_PIO:
            return                      # only the control register pages
        if v & 0x80:
            # Mode set.  It configures port C upper as an output *and*
            # clears every port C latch on the way.  Two things follow, and
            # the machine's whole boot depends on both:
            #
            #   PC4 goes low  -> the ROM leaves the address space.  This is
            #                    why the monitor executes the next few bytes
            #                    from a RAM trampoline.
            #   PC5 goes low  -> the startup mirror ends.  SystemPIO's
            #                    WritePaging reads PC5 as "ROM only", and at
            #                    reset port C is an *input*, so nothing has
            #                    driven that line low before now.
            #
            # That second effect is not gated by map_clear_ports, because it
            # is not a port write clearing a latch -- it is the 8255 finally
            # driving the line that selects the map.  A machine where only
            # this clears the mirror is modelled by map_clear_ports=().
            self.rom_visible = False
            self.startup_map = False
        elif (v >> 1) & 0x07 == 4:
            self.rom_visible = bool(v & 0x01)      # BSR on PC4
        elif (v >> 1) & 0x07 == 5:
            self.startup_map = bool(v & 0x01)      # BSR on PC5: mirror back


class CPU:
    R = ["B", "C", "D", "E", "H", "L", "M", "A"]

    def __init__(self, bus: Bus):
        self.bus = bus
        self.r = dict(A=0, B=0, C=0, D=0, E=0, H=0, L=0)
        self.pc = 0
        self.sp = 0
        self.z = self.s = self.p = self.cy = self.ac = False
        self.halted = False

    # -- register helpers -------------------------------------------------
    def get(self, i: int) -> int:
        if i == 6:
            return self.bus.read(self.hl())
        return self.r[self.R[i]]

    def put(self, i: int, v: int) -> None:
        v &= 0xFF
        if i == 6:
            self.bus.write(self.hl(), v)
        else:
            self.r[self.R[i]] = v

    def hl(self) -> int:
        return (self.r["H"] << 8) | self.r["L"]

    def set_rp(self, i: int, v: int) -> None:
        v &= 0xFFFF
        if i == 0:
            self.r["B"], self.r["C"] = v >> 8, v & 0xFF
        elif i == 1:
            self.r["D"], self.r["E"] = v >> 8, v & 0xFF
        elif i == 2:
            self.r["H"], self.r["L"] = v >> 8, v & 0xFF
        else:
            self.sp = v

    def get_rp(self, i: int) -> int:
        if i == 0:
            return (self.r["B"] << 8) | self.r["C"]
        if i == 1:
            return (self.r["D"] << 8) | self.r["E"]
        if i == 2:
            return self.hl()
        return self.sp

    # -- flags ------------------------------------------------------------
    def szp(self, v: int) -> None:
        self.z = v == 0
        self.s = bool(v & 0x80)
        self.p = PARITY[v]

    # -- execution --------------------------------------------------------
    def fetch(self) -> int:
        v = self.bus.read(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return v

    def fetch16(self) -> int:
        lo, hi = self.fetch(), self.fetch()
        return (hi << 8) | lo

    def cond(self, i: int) -> bool:
        return [not self.z, self.z, not self.cy, self.cy,
                not self.p, self.p, not self.s, self.s][i]

    def alu(self, op: int, v: int) -> None:
        a = self.r["A"]
        if op == 0:                          # ADD
            r = a + v
            self.cy = r > 0xFF
        elif op == 1:                        # ADC
            r = a + v + self.cy
            self.cy = r > 0xFF
        elif op in (2, 3, 7):                # SUB / SBB / CMP
            sub = v + (self.cy if op == 3 else 0)
            r = a - sub
            self.cy = r < 0
        elif op == 4:                        # ANA
            r = a & v
            self.cy = False
        elif op == 5:                        # XRA
            r = a ^ v
            self.cy = False
        else:                                # ORA
            r = a | v
            self.cy = False
        r &= 0xFF
        self.szp(r)
        if op != 7:                          # CMP discards the result
            self.r["A"] = r

    def step(self) -> None:
        op = self.fetch()
        hi, mid, lo = op >> 6, (op >> 3) & 7, op & 7

        if op == 0x76:
            self.halted = True
            return
        if hi == 1:                                     # MOV
            self.put(mid, self.get(lo))
            return
        if hi == 2:                                     # ALU r
            self.alu(mid, self.get(lo))
            return
        if hi == 0:
            if lo == 0:
                return                                  # NOP (and undocumented)
            if lo == 1:
                if op & 8:                              # DAD
                    r = self.hl() + self.get_rp(mid >> 1)
                    self.cy = r > 0xFFFF
                    self.set_rp(2, r)
                else:
                    self.set_rp(mid >> 1, self.fetch16())
                return
            if lo == 2:
                rp = {0: 0, 1: 1}.get(mid >> 1)
                if op in (0x02, 0x12):
                    self.bus.write(self.get_rp(op >> 4), self.r["A"])
                elif op in (0x0A, 0x1A):
                    self.r["A"] = self.bus.read(self.get_rp(op >> 4))
                elif op == 0x32:
                    self.bus.write(self.fetch16(), self.r["A"])
                elif op == 0x3A:
                    self.r["A"] = self.bus.read(self.fetch16())
                elif op == 0x22:
                    a = self.fetch16()
                    self.bus.write(a, self.r["L"])
                    self.bus.write(a + 1, self.r["H"])
                elif op == 0x2A:
                    a = self.fetch16()
                    self.r["L"] = self.bus.read(a)
                    self.r["H"] = self.bus.read(a + 1)
                else:
                    raise NotImplementedError(f"opcode {op:02X}")
                return
            if lo == 3:
                self.set_rp(mid >> 1,
                            self.get_rp(mid >> 1) + (-1 if op & 8 else 1))
                return
            if lo == 4:                                 # INR
                v = (self.get(mid) + 1) & 0xFF
                self.put(mid, v)
                self.szp(v)
                return
            if lo == 5:                                 # DCR
                v = (self.get(mid) - 1) & 0xFF
                self.put(mid, v)
                self.szp(v)
                return
            if lo == 6:                                 # MVI
                self.put(mid, self.fetch())
                return
            if lo == 7:
                a = self.r["A"]
                if op == 0x07:                          # RLC
                    self.cy = bool(a & 0x80)
                    self.r["A"] = ((a << 1) | self.cy) & 0xFF
                elif op == 0x0F:                        # RRC
                    self.cy = bool(a & 1)
                    self.r["A"] = ((a >> 1) | (self.cy << 7)) & 0xFF
                elif op == 0x2F:                        # CMA
                    self.r["A"] = a ^ 0xFF
                elif op == 0x37:
                    self.cy = True
                elif op == 0x3F:
                    self.cy = not self.cy
                else:
                    raise NotImplementedError(f"opcode {op:02X}")
                return
        if hi == 3:
            # Stack operations.  This program never uses them, but a
            # processor fed corrupted bytes executes whatever it is given,
            # and an emulator that throws on the first RST cannot model
            # that at all.
            if lo == 5 and not (op & 8):                # PUSH
                v = self.get_rp(mid >> 1) if (mid >> 1) != 3 else 0
                if (mid >> 1) == 3:
                    v = (self.r["A"] << 8) | (0x02 | (self.cy)
                                              | (self.p << 2) | (self.z << 6)
                                              | (self.s << 7))
                self.sp = (self.sp - 2) & 0xFFFF
                self.bus.write(self.sp, v & 0xFF)
                self.bus.write(self.sp + 1, v >> 8)
                return
            if lo == 1 and not (op & 8):                # POP
                v = self.bus.read(self.sp) | (self.bus.read(self.sp + 1) << 8)
                self.sp = (self.sp + 2) & 0xFFFF
                if (mid >> 1) == 3:
                    self.r["A"] = v >> 8
                    f = v & 0xFF
                    self.cy, self.p = bool(f & 1), bool(f & 4)
                    self.z, self.s = bool(f & 0x40), bool(f & 0x80)
                else:
                    self.set_rp(mid >> 1, v)
                return
            if op == 0xCD or (lo == 5 and (op & 8)):    # CALL
                t = self.fetch16()
                self.sp = (self.sp - 2) & 0xFFFF
                self.bus.write(self.sp, self.pc & 0xFF)
                self.bus.write(self.sp + 1, self.pc >> 8)
                self.pc = t
                return
            if op == 0xC9 or (lo == 1 and (op & 8)):    # RET
                self.pc = self.bus.read(self.sp) | (self.bus.read(self.sp + 1) << 8)
                self.sp = (self.sp + 2) & 0xFFFF
                return
            if lo == 7:                                 # RST
                self.sp = (self.sp - 2) & 0xFFFF
                self.bus.write(self.sp, self.pc & 0xFF)
                self.bus.write(self.sp + 1, self.pc >> 8)
                self.pc = mid * 8
                return
            if lo == 4:                                 # Ccc
                t = self.fetch16()
                if self.cond(mid):
                    self.sp = (self.sp - 2) & 0xFFFF
                    self.bus.write(self.sp, self.pc & 0xFF)
                    self.bus.write(self.sp + 1, self.pc >> 8)
                    self.pc = t
                return
            if lo == 0:                                 # Rcc
                if self.cond(mid):
                    self.pc = (self.bus.read(self.sp)
                               | (self.bus.read(self.sp + 1) << 8))
                    self.sp = (self.sp + 2) & 0xFFFF
                return
            if op == 0xC3:
                self.pc = self.fetch16()
                return
            if lo == 2:                                 # Jcc
                a = self.fetch16()
                if self.cond(mid):
                    self.pc = a
                return
            if lo == 6:                                 # ALU immediate
                self.alu(mid, self.fetch())
                return
            if op == 0xD3:
                self.bus.out(self.fetch(), self.r["A"])
                return
            if op == 0xDB:
                self.r["A"] = self.bus.inp(self.fetch())
                return
            if op == 0xEB:
                self.r["H"], self.r["D"] = self.r["D"], self.r["H"]
                self.r["L"], self.r["E"] = self.r["E"], self.r["L"]
                return
            if op in (0xF3, 0xFB):
                return
            if op == 0xE9:
                self.pc = self.hl()
                return
        raise NotImplementedError(f"opcode {op:02X} at {self.pc - 1:04X}")

    def run(self, max_steps: int = 20_000_000) -> int:
        n = 0
        while not self.halted and n < max_steps:
            self.step()
            n += 1
        return n
