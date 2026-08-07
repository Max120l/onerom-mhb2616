#!/usr/bin/env python3
"""A small 8080 emulator, for testing ROM images before they meet a machine.

Not a general-purpose emulator: it exists so that `make_ramtest.py`'s output
can be run against a *simulated* PMD 85-3 -- including one with deliberately
broken RAM -- and its beacons checked, before it is trusted to diagnose a
real machine. The sibling project's hardest-won lesson was that the
instrument is wrong more often than the machine is surprising; this is the
cheapest available defence against that.

The memory model mirrors the PMD 85-3: a startup map where reads come from
ROM everywhere and writes go to RAM, cleared by the first I/O write.
"""

import sys

PARITY = [bin(i).count("1") % 2 == 0 for i in range(256)]


class Bus:
    """PMD 85-3 memory behaviour, with optional injected RAM faults."""

    def __init__(self, rom: bytes, stuck: dict | None = None,
                 decay_after: int | None = None):
        assert len(rom) == 0x2000
        self.rom = rom
        self.ram = bytearray(0x10000)
        self.startup_map = True
        # {address: (and_mask, or_mask)} applied on read-back: a dead bit
        # reads 0 (and_mask clears it) or 1 (or_mask sets it).
        self.stuck = stuck or {}
        # DRAM that is not being refreshed: a cell read more than this many
        # bus cycles after it was written comes back as zero.  This models
        # the one fault that a write-then-read-much-later test blames on the
        # cells when the real culprit is refresh.
        self.decay_after = decay_after
        self.written_at = {}
        self.clock = 0
        self.rom_reads = []

    def read(self, a: int) -> int:
        a &= 0xFFFF
        self.clock += 1
        if self.startup_map or a >= 0xE000:
            self.rom_reads.append(a)
            return self.rom[a & 0x1FFF]
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

    def out(self, port: int, v: int) -> None:
        self.startup_map = False        # any I/O write clears it


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
                self.fetch()
                self.r["A"] = 0xFF
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
