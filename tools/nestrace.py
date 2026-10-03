#!/usr/bin/env python3
"""A small NES CPU+PPU harness, written here because this machine has no NES
emulator (checked: fceux, mesen, nestopia, mednafen, retroarch, py65 -- all
absent) and BizHawk is GUI-only with no Xvfb, so it cannot be driven headless.

What it is for: the rebuild boots to a black screen and a screenshot of a black
screen says nothing about *why*. This traces execution, records every PPU
register write, and renders the nametable + sprites so there is a framebuffer to
measure. It is a diagnostic, not an accuracy claim -- cycle counts are official-
opcode accurate, unofficial opcodes are treated as NOPs of the right length.

    python3 tools/nestrace.py --rom asm/out/magician-rebuilt.nes --frames 120
    python3 tools/nestrace.py --rom X --trace 40000 --png out.png
    python3 tools/nestrace.py --rom X --where ppu-on --where w:2001
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

# ------------------------------------------------------------------ the 6502

# name -> (mode, base cycles, extra cycles)
# modes: imp acc imm zp zpx zpy abs absx absy ind indx indy rel
OPS: dict[int, tuple[str, str, int]] = {
    0x00: ("brk", "imp", 7), 0x01: ("ora", "indx", 6), 0x05: ("ora", "zp", 3),
    0x06: ("asl", "zp", 5), 0x08: ("php", "imp", 3), 0x09: ("ora", "imm", 2),
    0x0A: ("asl", "acc", 2), 0x0D: ("ora", "abs", 4), 0x0E: ("asl", "abs", 6),
    0x10: ("bpl", "rel", 2), 0x11: ("ora", "indy", 5), 0x15: ("ora", "zpx", 4),
    0x16: ("asl", "zpx", 6), 0x18: ("clc", "imp", 2), 0x19: ("ora", "absy", 4),
    0x1D: ("ora", "absx", 4), 0x1E: ("asl", "absx", 7),
    0x20: ("jsr", "abs", 6), 0x21: ("and", "indx", 6), 0x24: ("bit", "zp", 3),
    0x25: ("and", "zp", 3), 0x26: ("rol", "zp", 5), 0x28: ("plp", "imp", 4),
    0x29: ("and", "imm", 2), 0x2A: ("rol", "acc", 2), 0x2C: ("bit", "abs", 4),
    0x2D: ("and", "abs", 4), 0x2E: ("rol", "abs", 6),
    0x30: ("bmi", "rel", 2), 0x31: ("and", "indy", 5), 0x35: ("and", "zpx", 4),
    0x36: ("rol", "zpx", 6), 0x38: ("sec", "imp", 2), 0x39: ("and", "absy", 4),
    0x3D: ("and", "absx", 4), 0x3E: ("rol", "absx", 7),
    0x40: ("rti", "imp", 6), 0x41: ("eor", "indx", 6), 0x45: ("eor", "zp", 3),
    0x46: ("lsr", "zp", 5), 0x48: ("pha", "imp", 3), 0x49: ("eor", "imm", 2),
    0x4A: ("lsr", "acc", 2), 0x4C: ("jmp", "abs", 3), 0x4D: ("eor", "abs", 4),
    0x4E: ("lsr", "abs", 6),
    0x50: ("bvc", "rel", 2), 0x51: ("eor", "indy", 5), 0x55: ("eor", "zpx", 4),
    0x56: ("lsr", "zpx", 6), 0x58: ("cli", "imp", 2), 0x59: ("eor", "absy", 4),
    0x5D: ("eor", "absx", 4), 0x5E: ("lsr", "absx", 7),
    0x60: ("rts", "imp", 6), 0x61: ("adc", "indx", 6), 0x65: ("adc", "zp", 3),
    0x66: ("ror", "zp", 5), 0x68: ("pla", "imp", 4), 0x69: ("adc", "imm", 2),
    0x6A: ("ror", "acc", 2), 0x6C: ("jmp", "ind", 5), 0x6D: ("adc", "abs", 4),
    0x6E: ("ror", "abs", 6),
    0x70: ("bvs", "rel", 2), 0x71: ("adc", "indy", 5), 0x75: ("adc", "zpx", 4),
    0x76: ("ror", "zpx", 6), 0x78: ("sei", "imp", 2), 0x79: ("adc", "absy", 4),
    0x7D: ("adc", "absx", 4), 0x7E: ("ror", "absx", 7),
    0x81: ("sta", "indx", 6), 0x84: ("sty", "zp", 3), 0x85: ("sta", "zp", 3),
    0x86: ("stx", "zp", 3), 0x88: ("dey", "imp", 2), 0x8A: ("txa", "imp", 2),
    0x8C: ("sty", "abs", 4), 0x8D: ("sta", "abs", 4), 0x8E: ("stx", "abs", 4),
    0x90: ("bcc", "rel", 2), 0x91: ("sta", "indy", 6), 0x94: ("sty", "zpx", 4),
    0x95: ("sta", "zpx", 4), 0x96: ("stx", "zpy", 4), 0x98: ("tya", "imp", 2),
    0x99: ("sta", "absy", 5), 0x9A: ("txs", "imp", 2), 0x9D: ("sta", "absx", 5),
    0xA0: ("ldy", "imm", 2), 0xA1: ("lda", "indx", 6), 0xA2: ("ldx", "imm", 2),
    0xA4: ("ldy", "zp", 3), 0xA5: ("lda", "zp", 3), 0xA6: ("ldx", "zp", 3),
    0xA8: ("tay", "imp", 2), 0xA9: ("lda", "imm", 2), 0xAA: ("tax", "imp", 2),
    0xAC: ("ldy", "abs", 4), 0xAD: ("lda", "abs", 4), 0xAE: ("ldx", "abs", 4),
    0xB0: ("bcs", "rel", 2), 0xB1: ("lda", "indy", 5), 0xB4: ("ldy", "zpx", 4),
    0xB5: ("lda", "zpx", 4), 0xB6: ("ldx", "zpy", 4), 0xB8: ("clv", "imp", 2),
    0xB9: ("lda", "absy", 4), 0xBA: ("tsx", "imp", 2), 0xBC: ("ldy", "absx", 4),
    0xBD: ("lda", "absx", 4), 0xBE: ("ldx", "absy", 4),
    0xC0: ("cpy", "imm", 2), 0xC1: ("cmp", "indx", 6), 0xC4: ("cpy", "zp", 3),
    0xC5: ("cmp", "zp", 3), 0xC6: ("dec", "zp", 5), 0xC8: ("iny", "imp", 2),
    0xC9: ("cmp", "imm", 2), 0xCA: ("dex", "imp", 2), 0xCC: ("cpy", "abs", 4),
    0xCD: ("cmp", "abs", 4), 0xCE: ("dec", "abs", 6),
    0xD0: ("bne", "rel", 2), 0xD1: ("cmp", "indy", 5), 0xD5: ("cmp", "zpx", 4),
    0xD6: ("dec", "zpx", 6), 0xD8: ("cld", "imp", 2), 0xD9: ("cmp", "absy", 4),
    0xDD: ("cmp", "absx", 4), 0xDE: ("dec", "absx", 7),
    0xE0: ("cpx", "imm", 2), 0xE1: ("sbc", "indx", 6), 0xE4: ("cpx", "zp", 3),
    0xE5: ("sbc", "zp", 3), 0xE6: ("inc", "zp", 5), 0xE8: ("inx", "imp", 2),
    0xE9: ("sbc", "imm", 2), 0xEA: ("nop", "imp", 2), 0xEC: ("cpx", "abs", 4),
    0xED: ("sbc", "abs", 4), 0xEE: ("inc", "abs", 6),
    0xF0: ("beq", "rel", 2), 0xF1: ("sbc", "indy", 5), 0xF5: ("sbc", "zpx", 4),
    0xF6: ("inc", "zpx", 6), 0xF8: ("sed", "imp", 2), 0xF9: ("sbc", "absy", 4),
    0xFD: ("sbc", "absx", 4), 0xFE: ("inc", "absx", 7),
}

# The unofficial opcodes real 6502 code from 1990 does use. Length only; they
# behave as their official two-operand sibling (or NOP) for timing purposes.
UNOFFICIAL_LEN = {
    0x03: 2, 0x07: 2, 0x0F: 2, 0x13: 2, 0x17: 2, 0x1B: 2, 0x1F: 2,
    0x23: 2, 0x27: 2, 0x2B: 2, 0x2F: 2, 0x33: 2, 0x37: 2, 0x3B: 2, 0x3F: 2,
    0x43: 2, 0x47: 2, 0x4B: 2, 0x4F: 2, 0x53: 2, 0x57: 2, 0x5B: 2, 0x5F: 2,
    0x63: 2, 0x67: 2, 0x6B: 2, 0x6F: 2, 0x73: 2, 0x77: 2, 0x7B: 2, 0x7F: 2,
    0x83: 2, 0x87: 2, 0x8B: 2, 0x8F: 2, 0x97: 2, 0x9B: 2, 0x9F: 2,
    0xA3: 2, 0xA7: 2, 0xAB: 2, 0xAF: 2, 0xB3: 2, 0xB7: 2, 0xBB: 2, 0xBF: 2,
    0xC3: 2, 0xC7: 2, 0xCB: 2, 0xCF: 2, 0xD3: 2, 0xD7: 2, 0xDB: 2, 0xDF: 2,
    0xE3: 2, 0xE7: 2, 0xEB: 2, 0xEF: 2, 0xF3: 2, 0xF7: 2, 0xFB: 2, 0xFF: 2,
    0x04: 2, 0x0C: 2, 0x14: 2, 0x1C: 2, 0x34: 2, 0x3C: 2, 0x44: 2, 0x54: 2,
    0x5C: 2, 0x64: 2, 0x6C: 2, 0x74: 2, 0x7C: 2, 0xD4: 2, 0xDC: 2, 0xFC: 2,
    0x80: 2, 0x82: 2, 0x89: 2, 0xC2: 2, 0xE2: 2,
}

MODE_LEN = {"imp": 1, "acc": 1, "imm": 2, "zp": 2, "zpx": 2, "zpy": 2,
            "rel": 2, "abs": 3, "absx": 3, "absy": 3, "ind": 3,
            "indx": 2, "indy": 2}


class Halt(Exception):
    pass


class CPU:
    def __init__(self, bus):
        self.bus = bus
        self.a = self.x = self.y = 0
        self.sp = 0xFD
        self.pc = 0
        self.c = self.z = self.i = self.d = self.v = self.n = 0
        self.cycles = 0
        self.nmi_pending = False
        self.irq_pending = False
        self.log: list[tuple[int, int, int, int, int, int, int, int]] = []

    # ---- flags
    def _nz(self, v):
        self.z = 1 if (v & 0xFF) == 0 else 0
        self.n = (v >> 7) & 1
        return v & 0xFF

    def setzn(self, v):
        return self._nz(v)

    def push(self, v):
        self.bus.write(0x100 + self.sp, v & 0xFF)
        self.sp = (self.sp - 1) & 0xFF

    def pull(self):
        self.sp = (self.sp + 1) & 0xFF
        return self.bus.read(0x100 + self.sp)

    # ---- interrupts
    def nmi(self):
        self.push(self.pc >> 8)
        self.push(self.pc & 0xFF)
        self.push((self.n << 7) | (self.v << 6) | 0x20 | (self.d << 3) | (self.i << 2) | (self.z << 1) | self.c)
        self.i = 1
        self.pc = self.bus.read(0xFFFA) | (self.bus.read(0xFFFB) << 8)
        self.cycles += 7
        self.log.append((self.cycles, self.pc, 0xEA, self.a, self.x, self.y, self.sp, 0x40))

    def irq(self):
        self.push(self.pc >> 8)
        self.push(self.pc & 0xFF)
        self.push((self.n << 7) | (self.v << 6) | 0x20 | (self.d << 3) | (self.i << 2) | (self.z << 1) | self.c)
        self.i = 1
        self.pc = self.bus.read(0xFFFE) | (self.bus.read(0xFFFF) << 8)
        self.cycles += 7
        self.log.append((self.cycles, self.pc, 0xEA, self.a, self.x, self.y, self.sp, 0x40))

    def step(self):
        bus = self.bus
        if self.nmi_pending:
            self.nmi_pending = False
            self.nmi()
            return
        if self.irq_pending and not self.i:
            self.irq_pending = False
            self.irq()
            return
        op = bus.read(self.pc)
        ent = OPS.get(op)
        if ent is None:
            if op in UNOFFICIAL_LEN:
                # Consume the operands, then act as a NOP.
                n = UNOFFICIAL_LEN[op]
                for i in range(n - 1):
                    bus.read((self.pc + i) & 0xFFFF)
                self.pc = (self.pc + n) & 0xFFFF
                self.cycles += n + 1
                return
            self.illegal = op
            raise Halt(f"illegal opcode ${op:02X} at ${(self.pc):04X}")
        name, mode, cyc = ent
        ln = MODE_LEN[mode]
        addr = val = target = 0
        lo = hi = 0
        if ln == 2:
            lo = bus.read(self.pc + 1)
            if mode == "rel":
                pass
            elif mode == "imm":
                pass
            elif mode in ("indx",):
                pass
            elif mode in ("indy",):
                pass
            else:
                pass
        elif ln == 3:
            lo = bus.read(self.pc + 1)
            hi = bus.read(self.pc + 2)
        if mode == "rel":
            off = lo
            target = (self.pc + 2 + (off - 256 if off > 127 else off)) & 0xFFFF
            val = self.pc + 2
        elif mode == "imm":
            addr = val = self.pc + 1
        elif mode == "zp":
            addr = val = lo
        elif mode == "zpx":
            addr = val = (lo + self.x) & 0xFF
        elif mode == "zpy":
            addr = val = (lo + self.y) & 0xFF
        elif mode == "abs":
            addr = val = lo | (hi << 8)
        elif mode == "absx":
            base = lo | (hi << 8)
            addr = (base + self.x) & 0xFFFF
            val = bus.read((base & 0xFF00) | (addr & 0xFF))
            cyc += 1
        elif mode == "absy":
            base = lo | (hi << 8)
            addr = (base + self.y) & 0xFFFF
            val = bus.read((base & 0xFF00) | (addr & 0xFF))
            cyc += 1
        elif mode == "indx":
            z = (lo + self.x) & 0xFF
            p = bus.read(z) | (bus.read((z + 1) & 0xFF) << 8)
            addr = val = p
        elif mode == "indy":
            z = lo
            p = bus.read(z) | (bus.read((z + 1) & 0xFF) << 8)
            addr = (p + self.y) & 0xFFFF
            val = bus.read((p & 0xFF00) | (addr & 0xFF))
            cyc += 1
        elif mode == "ind":
            p = lo | (hi << 8)
            addr = val = bus.read(p) | (bus.read((p & 0x1000) | ((p + 1) & 0xFF)) << 8)
        else:
            addr = val = 0

        self.pc = (self.pc + ln) & 0xFFFF
        self.cycles += cyc
        R = self._exec
        if name not in ("nop",):
            R(name, addr, val, target)
        self.log.append((self.cycles, self.pc, op, self.a, self.x, self.y, self.sp, 0))

    # ---- the operations. `addr` is the effective address (for stores too),
    # `val` the operand value (for stores the low byte read).
    def _exec(self, name, addr, val, target):
        bus = self.bus
        n = name
        if n == "lda":
            self.a = self.setzn(bus.read(addr))
        elif n == "ldx":
            self.x = self.setzn(bus.read(addr))
        elif n == "ldy":
            self.y = self.setzn(bus.read(addr))
        elif n == "sta":
            bus.write(addr, self.a)
        elif n == "stx":
            bus.write(addr, self.x)
        elif n == "sty":
            bus.write(addr, self.y)
        elif n == "tax":
            self.x = self.setzn(self.a)
        elif n == "tay":
            self.y = self.setzn(self.a)
        elif n == "txa":
            self.a = self.setzn(self.x)
        elif n == "tya":
            self.a = self.setzn(self.y)
        elif n == "tsx":
            self.x = self.setzn(self.sp)
        elif n == "txs":
            self.sp = self.x
        elif n == "pha":
            self.push(self.a)
        elif n == "php":
            self.push((self.n << 7) | (self.v << 6) | 0x30 | (self.d << 3) | (self.i << 2) | (self.z << 1) | self.c)
        elif n == "pla":
            self.a = self.setzn(self.pull())
        elif n == "plp":
            self._setflags(self.pull())
        elif n == "and":
            self.a = self.setzn(self.a & bus.read(addr))
        elif n == "ora":
            self.a = self.setzn(self.a | bus.read(addr))
        elif n == "eor":
            self.a = self.setzn(self.a ^ bus.read(addr))
        elif n == "adc":
            self._adc(bus.read(addr))
        elif n == "sbc":
            self._adc(bus.read(addr) ^ 0xFF)
        elif n == "cmp":
            self._cmp(self.a, bus.read(addr))
        elif n == "cpx":
            self._cmp(self.x, bus.read(addr))
        elif n == "cpy":
            self._cmp(self.y, bus.read(addr))
        elif n == "bit":
            m = bus.read(addr)
            self.z = 1 if (self.a & m) == 0 else 0
            self.n = (m >> 7) & 1
            self.v = (m >> 6) & 1
        elif n == "inc":
            bus.write(addr, (bus.read(addr) + 1) & 0xFF)
        elif n == "dec":
            bus.write(addr, (bus.read(addr) - 1) & 0xFF)
        elif n == "inx":
            self.x = self.setzn(self.x + 1)
        elif n == "iny":
            self.y = self.setzn(self.y + 1)
        elif n == "dex":
            self.x = self.setzn(self.x - 1)
        elif n == "dey":
            self.y = self.setzn(self.y - 1)
        elif n == "asl":
            if addr == self.pc - 1 and n == "asl" and self.log and self.log[-1][2] == 0x0A:
                pass
            v = self.a if self._is_acc() else bus.read(addr)
            self.c = (v >> 7) & 1
            v = self.setzn(v << 1)
            if not self._is_acc():
                bus.write(addr, v)
            else:
                self.a = v
        elif n == "lsr":
            v = self.a if self._is_acc() else bus.read(addr)
            self.c = v & 1
            v = self.setzn(v >> 1)
            if not self._is_acc():
                bus.write(addr, v)
            else:
                self.a = v
        elif n == "rol":
            v = self.a if self._is_acc() else bus.read(addr)
            c = self.c
            self.c = (v >> 7) & 1
            v = self.setzn((v << 1) | c)
            if not self._is_acc():
                bus.write(addr, v)
            else:
                self.a = v
        elif n == "ror":
            v = self.a if self._is_acc() else bus.read(addr)
            c = self.c
            self.c = v & 1
            v = self.setzn((v >> 1) | (c << 7))
            if not self._is_acc():
                bus.write(addr, v)
            else:
                self.a = v
        elif n in ("bne", "beq", "bcc", "bcs", "bmi", "bpl", "bvc", "bvs"):
            cond = {
                "bne": self.z == 0, "beq": self.z == 1,
                "bcc": self.c == 0, "bcs": self.c == 1,
                "bmi": self.n == 1, "bpl": self.n == 0,
                "bvc": self.v == 0, "bvs": self.v == 1,
            }[n]
            if cond:
                self.cycles += 1
                self.cycles += 1 if (self.pc & 0xFF00) != (target & 0xFF00) else 0
                self.pc = target
        elif n == "jmp":
            self.pc = addr
        elif n == "jsr":
            r = (self.pc - 1) & 0xFFFF
            self.push(r >> 8)
            self.push(r & 0xFF)
            self.pc = addr
        elif n == "rts":
            lo = self.pull()
            hi = self.pull()
            self.pc = ((hi << 8) | lo) + 1 & 0xFFFF
        elif n == "rti":
            self._setflags(self.pull())
            lo = self.pull()
            hi = self.pull()
            self.pc = ((hi << 8) | lo) & 0xFFFF
        elif n == "brk":
            r = self.pc
            self.push(r >> 8)
            self.push(r & 0xFF)
            self.push((self.n << 7) | (self.v << 6) | 0x30 | (self.d << 3) | (self.i << 2) | (self.z << 1) | self.c)
            self.i = 1
            self.pc = bus.read(0xFFFE) | (bus.read(0xFFFF) << 8)
        elif n == "clc":
            self.c = 0
        elif n == "sec":
            self.c = 1
        elif n == "cli":
            self.i = 0
        elif n == "sei":
            self.i = 1
        elif n == "cld":
            self.d = 0
        elif n == "sed":
            self.d = 1
        elif n == "clv":
            self.v = 0
        else:
            raise Halt(f"unimplemented {n}")

    def _is_acc(self):
        return self.log and self.log[-1][2] in (0x0A, 0x2A, 0x4A, 0x6A)

    def _setflags(self, p):
        self.n = (p >> 7) & 1
        self.v = (p >> 6) & 1
        self.d = (p >> 3) & 1
        self.i = (p >> 2) & 1
        self.z = (p >> 1) & 1
        self.c = p & 1

    def _adc(self, m):
        if self.d:
            al = (self.a & 0x0F) + (m & 0x0F) + self.c
            if al > 9:
                al += 6
            ah = (self.a >> 4) + (m >> 4) + (1 if al > 0x0F else 0)
            self.z = 1 if ((self.a + m + self.c) & 0xFF) == 0 else 0
            self.n = (ah >> 3) & 1
            self.v = (~(self.a ^ m) & (self.a ^ (ah << 4)) & 0x80) >> 7
            if ah > 9:
                ah += 6
            self.c = 1 if ah > 0x0F else 0
            self.a = ((ah << 4) | (al & 0x0F)) & 0xFF
        else:
            t = self.a + m + self.c
            self.c = 1 if t > 0xFF else 0
            self.v = (~(self.a ^ m) & (self.a ^ t) & 0x80) >> 7
            self.a = self.setzn(t)

    def _cmp(self, reg, m):
        t = reg - m
        self.c = 1 if reg >= m else 0
        self.setzn(t)


# ------------------------------------------------------------------ the bus

class Bus:
    def __init__(self, prg: bytes, chr_: bytes, mapper: int, vertical: bool):
        self.ram = bytearray(0x800)
        self.prg = prg
        self.chr = bytearray(chr_) if len(chr_) else bytearray(0x2000)
        self.mapper = mapper
        self.prgram = bytearray(0x2000)
        self.cycles = 0
        self.vertical = vertical
        # MMC3
        self.bankreg = 6
        self.regs = [0, 0, 0, 0, 0, 0, 0x0E, 0x0F]
        self.prg_mode = 0
        self.irq_latch = 0
        self.irq_reload = False
        self.irq_pending = False
        self.irq_enabled = False
        self.scanline = 0
        self.irq_cycles = 0
        # PPU
        self.ctrl = 0
        self.mask = 0
        self.status = 0
        self.oamaddr = 0
        self.v = 0
        self.t = 0
        self.x = 0
        self.w = 0
        self.readbuf = 0
        self.vram = bytearray(0x800)
        self.pal = bytearray(0x40)
        self.oam = bytearray(0x100)
        self.vblank = False
        self.frame = 0
        self.dot = 0
        self.sl = 0
        self.ppu_writes: collections.Counter = collections.Counter()
        self.ppu_write_log: list[tuple[int, int, int]] = []
        self.ppu7: list[tuple[int, int, int]] = []
        self.ppu7_targets: collections.Counter = collections.Counter()
        self.nmi_count = 0
        self.irq_count = 0
        self.dma_pending = 0
        self.halted = ""

    # -- PRG mapping. MMC3 8 KiB slots: 16 of them in 128 KiB.
    def _prg_offset(self, cpu: int) -> int:
        n = len(self.prg) // 0x2000          # number of 8 KiB slots
        if cpu < 0x8000:
            return None
        if cpu < 0xA000:
            slot = self.regs[6] & 0x0F
        elif cpu < 0xC000:
            slot = self.regs[7] & 0x0F
        elif cpu < 0xE000:
            slot = (n - 2) if not self.prg_mode else self.regs[5] & 0x0F
        else:
            slot = (n - 1) if not self.prg_mode else self.regs[7] & 0x0F
        return (slot % n) * 0x2000 + (cpu & 0x1FFF)

    def read(self, a: int) -> int:
        a &= 0xFFFF
        if a < 0x2000:
            return self.ram[a & 0x7FF]
        if a < 0x4000:
            if a & 7 == 2:
                v = (self.status & 0xE0) | (self.readbuf & 0x1F)
                self.status &= 0x7F
                self.vblank = False
                self.readbuf = self.ppu_read(self.v & 0x3FFF)
                self._maybe_nmi()
                return v
            if a & 7 == 4:
                return self.oam[self.oamaddr]
            if a & 7 == 7:
                self._inc_v()
                v = self.ppu_read(self.v & 0x3FFF)
                self.readbuf = self.ppu_read((self.v & 0x3FFF) - 1 if self.v >= 0x3F00 else (self.v & 0x3FFF))
                self._inc_v()
                return v
            return self.ppu_read(self.v & 0x3FFF) if (a & 7) == 7 else 0
        if a < 0x4020:
            if a == 0x4016:
                return 0
            if a == 0x4014:
                self._oam_dma_pending = True
                return 0
            if a < 0x4008:
                return 0
            return 0
        if a < 0x6000:
            return 0
        if a < 0x8000:
            return self.prgram[a - 0x6000]
        off = self._prg_offset(a)
        if off is None:
            return 0xFF
        return self.prg[off]

    def write(self, a: int, v: int) -> None:
        a &= 0xFFFF
        v &= 0xFF
        if a < 0x2000:
            self.ram[a & 0x7FF] = v
            return
        if a < 0x4000:
            r = a & 7
            if r == 0:
                old = self.ctrl
                self.ctrl = v
                self.t = (self.t & 0xF3FF) | ((v & 3) << 10)
                if not (old & 0x80) and (v & 0x80):
                    self._maybe_nmi()
            elif r == 1:
                self.mask = v
            elif r == 3:
                self.oamaddr = v
            elif r == 4:
                self.oam[self.oamaddr] = v
                self.oamaddr = (self.oamaddr + 1) & 0xFF
            elif r == 5:
                if self.w == 0:
                    self.t = (self.t & 0xFFE0) | (v >> 3)
                    self.x = v & 7
                    self.w = 1
                else:
                    self.t = (self.t & 0x8FFF) | ((v & 7) << 12)
                    self.t = (self.t & 0xFC1F) | ((v & 0xF8) << 2)
                    self.w = 0
            elif r == 6:
                if self.w == 0:
                    self.t = (self.t & 0x00FF) | ((v & 0x3F) << 8)
                    self.w = 1
                else:
                    self.t = (self.t & 0xFF00) | v
                    self.v = self.t
                    self.w = 0
            elif r == 7:
                if self.ppu_writes[7] <= 64:
                    self.ppu7.append((self.cycles, self.v & 0x3FFF, v))
                self.ppu7_targets[self.v & 0x3FFF] += 1
                self.ppu_write(self.v & 0x3FFF, v)
                self._inc_v()
            self.ppu_writes[r] += 1
            if self.ppu_writes[r] <= 4000:
                self.ppu_write_log.append((self.cycles, a, v))
            return
        if a < 0x4020:
            if a == 0x4014:
                self._oam_dma(v)
            return
        if a < 0x6000:
            return
        if a < 0x8000:
            self.prgram[a - 0x6000] = v
            return
        if a < 0xA000:
            k = a & 7
            if k == 0:
                self.bankreg = v & 7
                self.irq_reload = bool(v & 0x04)
                self.irq_enabled = bool(v & 0x40)
                self.irq_pending = False
            elif k == 1:
                self.regs[self.bankreg] = v
            elif k == 6:
                if v & 0x80:
                    self.prgram[(v & 0x1F) * 0x200 + (a & 0x1FF)] ^= 0xFF
            return
        if a < 0xC000:
            if (a & 7) == 0:
                self.bankreg = v & 7
                self.irq_reload = bool(v & 0x04)
                self.irq_enabled = bool(v & 0x40)
                self.irq_pending = False
            elif (a & 7) == 1:
                self.regs[self.bankreg] = v
                if self.bankreg == 6:
                    self.prg_mode = (v >> 6) & 1
            elif (a & 7) == 6:
                self.prgram[(v & 0x1F) * 0x200 + (a & 0x1FF)] ^= 0xFF
            return
        if (a & 0xFFF0) == 0xC000:
            self.irq_latch = v
            return
        if (a & 0xFFF0) == 0xC001:
            self.irq_cycles = 0
            self.scanline = 0
            self.irq_pending = self.irq_enabled
            return

    def _oam_dma(self, page):
        for i in range(256):
            self.oam[i] = self.read((page << 8) | i)

    # ---- PPU address space
    def _nt_index(self, a: int) -> int:
        a &= 0x0FFF
        if self.vertical:
            # $2000/$2400 -> 0/1, $2800/$2C00 -> 1/0
            tbl = (0, 1, 1, 0)
        else:
            # horizontal: $2000/$2400 -> 0/0, $2800/$2C00 -> 1/1
            tbl = (0, 0, 1, 1)
        return (tbl[a >> 10] << 10) | (a & 0x3FF)

    def ppu_read(self, a: int) -> int:
        a &= 0x3FFF
        if a < 0x2000:
            return self.chr[a]
        if a < 0x3F00:
            return self.vram[self._nt_index(a)]
        return self.pal[a & 0x1F] if (a & 0x13) != 0x10 else self.vram[self._nt_index(0x2000 + (a & 0x0FFF))]

    def ppu_write(self, a: int, v: int) -> None:
        a &= 0x3FFF
        if a < 0x2000:
            self.chr[a] = v
        elif a < 0x3F00:
            self.vram[self._nt_index(a)] = v
        elif (a & 0x13) == 0x10:
            self.vram[self._nt_index(0x2000 + (a & 0x0FFF))] = v
        else:
            self.pal[a & 0x1F] = v

    def _inc_v(self):
        self.v = (self.v + ((32 if self.ctrl & 4 else 1))) & 0x7FFF

    def _maybe_nmi(self):
        if self.vblank and (self.ctrl & 0x80):
            if not self.cpu.nmi_pending:
                self.nmi_count += 1
            self.cpu.nmi_pending = True

    # ---- frame timing. The PPU runs at 3 dots per CPU cycle, 341 dots per
    # scanline, 262 scanlines per frame (89342 dots, 29780.5 CPU cycles). The
    # bug this replaces counted one scanline per CPU cycle, so vblank never came
    # and any ROM that waits for vblank at reset (this one does, twice) hangs.
    def tick(self, upto: int):
        """Advance the PPU to CPU cycle `upto`. The CPU owns the cycle counter;
        the PPU runs 3 dots per CPU cycle and wraps a frame every 89342 dots."""
        d = upto - self.cycles
        if d <= 0:
            return
        self.cycles = upto
        self.dot += d * 3
        while self.dot >= 341 * 262:
            self.dot -= 341 * 262
            self.frame += 1
        new_sl = self.dot // 341
        if self.sl < 241 <= new_sl and not self.vblank:
            self.vblank = True
            self.status |= 0x80
            self._maybe_nmi()
        self.sl = new_sl

    def _mmc3_irq_check(self):
        pass

    # ---- rendering. A scanline renderer, not a dot renderer: for each of the
    # 240 visible scanlines it walks the 32 background tiles that cover the
    # line, fetches the two pattern bytes and the attribute byte, then walks the
    # OAM for sprites on that line. Accurate enough to answer "is there a
    # picture and is it black", which is the question being asked.
    NES_PAL = (
        0x666666, 0x002A88, 0x1412A7, 0x3B00A4, 0x5C007E, 0x6E0040, 0x6C0600,
        0x561D00, 0x333500, 0x0B4800, 0x005200, 0x004F08, 0x00404D, 0x000000,
        0x000000, 0x000000, 0xADADAD, 0x155FD9, 0x4240FF, 0x7527FE, 0xA01ACC,
        0xB71E7B, 0xB53120, 0x994E00, 0x6B6D00, 0x388700, 0x0C9300, 0x008F32,
        0x007C8D, 0x000000, 0x000000, 0x000000, 0xFFFEFF, 0x64B0FF, 0x9290FF,
        0xC676FF, 0xF36AFF, 0xFE6ECC, 0xFE8170, 0xEA9E22, 0xBCBE00, 0x88D800,
        0x5CE430, 0x45E082, 0x48CDDE, 0x4F4F4F, 0x000000, 0x000000, 0x000000,
        0xFFFEFF, 0xC0DFFF, 0xD3D2FF, 0xE8C8FF, 0xFBC2FF, 0xFEC4EA, 0xFECCC5,
        0xF7D8A5, 0xE4E594, 0xCFEF96, 0xBDF4AB, 0xB3F3CC, 0xB5EBF2, 0xB8B8B8,
        0x000000, 0x000000, 0x000000,
    )

    def _render_scanline(self, y: int) -> list[int]:
        """y is the visible scanline 0..239. Returns 256 palette indices."""
        out = [self.pal_idx(0)] * 256   # the universal backdrop colour
        if y >= 240:
            return out
        if not (self.mask & 0x18):
            return out
        bgbase = 0x0000 if (self.ctrl & 0x20) else 0x1000
        sprbase = 0x1000 - bgbase
        ntbase = bgbase & 0x1000
        ntsel = (self.t >> 10) & 3
        ntsel_addr = (ntsel << 10) & 0x0C00
        coarsey = (self.t >> 5) & 0x1F
        finey = (self.t >> 12) & 7
        coarsex = self.t & 0x1F
        finex = self.x
        # The vertical scroll wraps every 30 tiles.
        yfine = coarsey * 8 + finey + y
        coarsey_eff = (yfine // 8) % 30
        finey_eff = (yfine // 8) % 8
        if yfine >= 240:
            coarsey_eff = 0
            finey_eff = yfine - 240
        for px in range(256):
            tot = px + finex
            cx = (coarsex + (tot >> 3)) & 0x1F
            fx = tot & 7
            row = (coarsey_eff >> 2) * 32 + cx
            tile = self.ppu_read(ntbase | ntsel_addr | row)
            lo = self.ppu_read(bgbase + tile * 16 + finey_eff)
            hi = self.ppu_read(bgbase + tile * 16 + finey_eff + 8)
            if lo or hi:
                b = ((lo >> (7 - fx)) & 1) | (((hi >> (7 - fx)) & 1) << 1)
            else:
                b = 0
            if b:
                attr = self.ppu_read(ntbase | ntsel_addr | 0x03C0 |
                                     ((coarsey_eff >> 2) & 7) * 8 + (cx >> 2))
                q = ((coarsey_eff & 2) << 1) | ((cx & 2))
                pal = (attr >> q) & 3
                if self.mask & 0x01:
                    out[px] = self.pal_idx(pal * 4 + b)
        if self.mask & 0x14:
            for i in range(64):
                sy = self.oam[i * 4] - 1
                if not (0 <= y - sy < 8 if self.ctrl & 0x20 else 0 <= sy - y < 8):
                    continue
                tile = self.oam[i * 4 + 1]
                attr = self.oam[i * 4 + 2]
                xpos = self.oam[i * 4 + 3]
                h = 8
                if attr & 0x80:
                    h = 16
                    bank = 0x1000 if (self.ctrl & 0x20) else 0x0000
                    tile &= 0xFE
                    if not (self.ctrl & 0x20):
                        tile += 1
                else:
                    bank = sprbase
                row_in = (y - sy) if (self.ctrl & 0x20) else (y - sy)
                if attr & 0x40:
                    row_in = h - 1 - row_in
                addr = bank + tile * 16 + (row_in & 7)
                lo = self.ppu_read(addr)
                hi = self.ppu_read(addr + 8)
                if attr & 0x80:
                    hi = 0
                fx = (0 if (attr & 0x40) else 0)
                for k in range(8):
                    if xpos + k >= 256:
                        break
                    px = xpos + k
                    if px < 0:
                        continue
                    bit = (7 - k) if not (attr & 0x40) else k
                    b = ((lo >> bit) & 1) | (((hi >> bit) & 1) << 1)
                    if b == 0:
                        continue
                    if attr & 0x20:
                        out[px] = 0x20 | out[px]     # behind background
                    else:
                        out[px] = self.pal_idx(0x10 + (attr & 3) * 4 + b)
        return out

    def pal_idx(self, i: int) -> int:
        i &= 0x1F
        if (i & 0x13) == 0x10:
            i &= 0x0F
        return self.pal[i] & 0x3F

    def render_frame(self) -> list[list[int]]:
        return [self._render_scanline(y) for y in range(240)]


def load_rom(path: pathlib.Path):
    b = path.read_bytes()
    if b[:4] != b"NES\x1a":
        raise SystemExit(f"{path}: not an iNES file")
    nprg, nchr, f6, f7 = b[4], b[5], b[6], b[7]
    prg = b[16:16 + nprg * 16384]
    chr_ = b[16 + nprg * 16384: 16 + nprg * 16384 + nchr * 8192] if nchr else bytearray(0x2000)
    mapper = (f6 >> 4) | (f7 & 0xF0)
    vertical = bool(f6 & 1)
    return prg, chr_, mapper, vertical


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=pathlib.Path, required=True)
    ap.add_argument("--frames", type=float, default=10.0, help="frames of CPU to run")
    ap.add_argument("--trace", type=int, default=0, help="print the first N instructions")
    ap.add_argument("--trace-from", type=lambda s: int(s, 0), default=None,
                    help="only trace once PC reaches this address")
    ap.add_argument("--where", action="append", default=[],
                    help="report when execution reaches PC=LABEL, or w:REG for a PPU write")
    ap.add_argument("--png", type=pathlib.Path)
    ap.add_argument("--png-frame", type=int, default=0,
                    help="capture this frame number instead of the first one that "
                         "has been drawn")
    ap.add_argument("--max-cycles", type=int, default=60_000_000)
    ap.add_argument("--ram", type=lambda s: int(s, 0), nargs=2, metavar=("LO", "HI"),
                    help="hex-dump CPU RAM over [lo,hi) at the end")
    ap.add_argument("--vram", type=lambda s: int(s, 0), nargs=2, metavar=("LO", "HI"),
                    help="hex-dump PPU nametaps over [lo,hi) at the end")
    ap.add_argument("--last", type=int, default=0,
                    help="print the last N instructions executed (a ring buffer), "
                         "which is where a crash actually happened")
    args = ap.parse_args()

    prg, chr_, mapper, vertical = load_rom(args.rom)
    print(f"mapper {mapper}  {'vertical' if vertical else 'horizontal'} mirroring  "
          f"PRG {len(prg)} bytes ({len(prg)//8192} x 8 KiB)  CHR {len(chr_)} bytes")
    bus = Bus(prg, chr_, mapper, vertical)
    cpu = CPU(bus)
    bus.cpu = cpu
    cpu.pc = bus.read(0xFFFC) | (bus.read(0xFFFD) << 8)
    print(f"entry: nmi=${bus.read(0xFFFA)|(bus.read(0xFFFB)<<8):04X} "
          f"reset=${cpu.pc:04X} irq=${bus.read(0xFFFE)|(bus.read(0xFFFF)<<8):04X}")
    total = int(args.frames * 29780.5)
    traced = 0
    seen: collections.Counter = collections.Counter()
    watch: dict = {}
    for w in args.where:
        if w.startswith("w:"):
            watch.setdefault(("w", int(w[2:], 0)), 0)
        else:
            watch.setdefault(("p", int(w, 0)), 0)
    halt = ""
    ring: collections.deque = collections.deque(maxlen=args.last or 1)
    armed = args.trace_from is None
    frames_wanted = {}
    if args.png:
        want = args.png_frame or int(args.frames)
        for n in range(1, want + 1):
            frames_wanted[n] = []
    captures: list[tuple[int, list]] = []
    try:
        while cpu.cycles < total and cpu.cycles < args.max_cycles:
            if traced < args.trace and armed:
                op = bus.read(cpu.pc)
                ent = OPS.get(op)
                if ent:
                    nm, md = ent[0], ent[1]
                    ln = MODE_LEN[md]
                    operand = bus.read(cpu.pc + 1) if ln == 2 else (
                        bus.read(cpu.pc + 1) | (bus.read(cpu.pc + 2) << 8))
                else:
                    nm, md, ln = f".{op:02X}", "unofficial", UNOFFICIAL_LEN.get(op, 1)
                    operand = 0
                print(f"{cpu.cycles:>10}  {cpu.pc:04X}: {nm:<4}{md:<5}"
                      f"{operand:04X}  A={cpu.a:02X} X={cpu.x:02X} Y={cpu.y:02X}"
                      f" S={cpu.sp:02X} P={_flags(cpu)}")
                traced += 1
            step_pc = cpu.pc
            if args.last:
                op = bus.read(step_pc)
                ent = OPS.get(op)
                ring.append((cpu.cycles, step_pc, op, ent[0] if ent else f".{op:02X}",
                             ent[1] if ent else "?", cpu.a, cpu.x, cpu.y, cpu.sp))
            if not armed and step_pc == args.trace_from:
                armed = True
            cpu.step()
            bus.tick(cpu.cycles)
            seen[step_pc] += 1
            for k in watch:
                if k[0] == "p" and step_pc == k[1] and watch[k] == 0:
                    watch[k] = 1
                    print(f"*** cycle {cpu.cycles} (frame {bus.frame}) reached ${step_pc:04X}"
                          f"  A={cpu.a:02X} X={cpu.x:02X} Y={cpu.y:02X}")
            if captures is not None or args.png:
                n = bus.frame + 1
                if n in frames_wanted and not frames_wanted[n] and n >= want:
                    if bus.sl > 250:
                        frames_wanted[n].append(bus.render_frame())
    except Halt as h:
        halt = str(h)

    print(f"\nran {cpu.cycles} cycles = {cpu.cycles / 29780.5:.1f} frames;"
          f" final PC=${cpu.pc:04X} A={cpu.a:02X} X={cpu.x:02X} Y={cpu.y:02X}")
    print(f"PPU ctrl=${bus.ctrl:02X} mask=${bus.mask:02X} status=${bus.status:02X}"
          f" frames elapsed={bus.frame}  NMI enabled={bool(bus.ctrl & 0x80)}")
    print("PPU writes: " + " ".join(f"${r:04X}={c}" for r, c in sorted(bus.ppu_writes.items())))
    nz = sum(1 for b in bus.vram if b)
    print(f"VRAM: {nz}/2048 nametable bytes nonzero;  palette nonzero entries:"
          f" {sum(1 for b in bus.pal if b)}/32")
    print(f"NMI raised {bus.nmi_count} times")
    print("first $2007 writes (cycle, vram addr, value): " +
          ", ".join(f"${a:04X}={v:02X}" for _, a, v in bus.ppu7[:24]))
    tg = bus.ppu7_targets
    blocks: collections.Counter = collections.Counter()
    for a, c in tg.items():
        blocks[a & 0x3F00] += c
    print("$2007 targets by 256-byte block: "
          + ", ".join(f"${a:04X}={c}" for a, c in blocks.most_common(10)))
    print("vram nonzero per 256-byte block: " + ", ".join(
        f"${b:04X}={sum(1 for v in bus.vram[b:b+256] if v)}" for b in range(0, 0x800, 256)))
    print("hottest: " + ", ".join(f"${a:04X}x{n}" for a, n in seen.most_common(14)))
    if args.vram:
        lo, hi = args.vram
        print(f"VRAM ${lo:04X}-${hi:04X} (nametable RAM is 2 KiB, $2000 -> [0]):")
        for a in range(lo, hi, 32):
            row = bus.vram[(a & 0x7FF):(a & 0x7FF) + 32]
            txt = "".join(chr(v) if 32 <= v < 127 else "." for v in row)
            print(f"  ${a:04X}  " + " ".join(f"{v:02X}" for v in row[:16])
                  + "  " + " ".join(f"{v:02X}" for v in row[16:]) + "  |" + txt + "|")
    if args.ram:
        lo, hi = args.ram
        print(f"RAM ${lo:04X}-${hi:04X}:")
        for a in range(lo, hi, 16):
            row = bus.ram[a & 0x7FF:(a & 0x7FF) + 16]
            print(f"  ${a:04X}  " + " ".join(f"{v:02X}" for v in row))
    if halt:
        print(f"HALT: {halt}")
        if args.last:
            print(f"last {args.last} instructions:")
            for cy, pc, op, mn, md, a, x, y, sp in ring:
                print(f"  {cy:>9}  {pc:04X}: {mn:<4}{md:<5} A={a:02X} X={x:02X} "
                      f"Y={y:02X} S={sp:02X}")

    if args.png:
        try:
            from PIL import Image
        except ImportError:
            print("no PIL; skipping png", file=sys.stderr)
            return 1
        rows = None
        for n in sorted(frames_wanted):
            if frames_wanted[n]:
                rows = frames_wanted[n][0]
                print(f"captured frame {n}")
                break
        if rows is None:
            rows = bus.render_frame()
            print("no clean frame boundary hit; rendered from final state")
        img = Image.new("RGB", (256, 240))
        pal = bus.NES_PAL
        data = [((pal[i] >> 16) & 0xFF, (pal[i] >> 8) & 0xFF, pal[i] & 0xFF)
                for row in rows for i in row]
        img.putdata(data)
        img.save(args.png)
        lum = [0.299 * r + 0.587 * g + 0.114 * b for (r, g, b) in data]
        print(f"png {args.png}: max luminance over 256x240 = {max(lum):.1f},"
              f" mean = {sum(lum)/len(lum):.2f}, nonzero = {sum(1 for v in lum if v > 1)}")
    return 0


def _flags(cpu: CPU) -> str:
    f = 0
    f |= cpu.n << 7
    f |= cpu.v << 6
    f |= 1 << 5
    f |= cpu.d << 3
    f |= cpu.i << 2
    f |= cpu.z << 1
    f |= cpu.c
    return f"{f:02X}"


if __name__ == "__main__":
    raise SystemExit(main())
