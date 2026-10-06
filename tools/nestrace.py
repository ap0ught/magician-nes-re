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
import bisect
import collections
import hashlib
import json
import pathlib
import sys

# One NTSC frame in PPU dots, and the dots within it at which the PPU's own tile
# fetches drive address bit 12 up: once on every rendering scanline (dot 260) and
# once on the pre-render line. 241 clocks per frame, which is the number
# mmc3_irq_tests/2.Details checks.
FRAME_DOTS = 341 * 262
SCANLINE_CLOCK = tuple(sorted(341 * sl + 260 for sl in (*range(240), 261)))
CPU_CYCLES_PER_FRAME = FRAME_DOTS / 3.0

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
    # The six one-byte NOPs. Real 6502 code from 1990 does use them, and this
    # cartridge does: Beta 1 executed `$1A` at $A709 on frame 131 and the tracer
    # stopped the run there with "illegal opcode", which is indistinguishable
    # from a crash. It is not a crash -- it is a two-byte-per-cycle NOP, and a
    # tracer that cannot decode it cannot follow the game past frame 131, so it
    # cannot answer any question about the rest of a 307-frame route.
    0x1A: 1, 0x3A: 1, 0x5A: 1, 0x7A: 1, 0xDA: 1, 0xFA: 1,
}

MODE_LEN = {"imp": 1, "acc": 1, "imm": 2, "zp": 2, "zpx": 2, "zpy": 2,
            "rel": 2, "abs": 3, "absx": 3, "absy": 3, "ind": 3,
            "indx": 2, "indy": 2}


class Halt(Exception):
    pass


# Mnemonics that read the addressed byte, and therefore pay a cycle when the
# indexed address crosses a page. Stores and read-modify-writes do not: on the
# 6502 their address is already latched, so the crossing is free. Everything not
# listed here that has an indexed mode is treated as a reader, which is the
# conservative direction -- a cycle too many is visible, a cycle too few is not.
PAGE_CROSS_READS = frozenset((
    "lda", "ldx", "ldy", "cmp", "sbc", "adc", "and", "ora", "eor", "bit",
))


def _pays_page_cross(mnemonic: str) -> bool:
    return mnemonic in PAGE_CROSS_READS


class UnsupportedMapper(Exception):
    """Raised rather than run: a ROM on a mapper we do not model would be
    executed through the wrong bank map and produce a plausible, confident,
    wrong answer. `holy_diver_batman` alone carries fourteen mappers."""


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
        # Which instruction is executing, for `Bus.ram_writes`. Set here rather
        # than in the main loop: `run_frames()` -- the other driver in this
        # file, used by the nes-testsuite runner -- does not set it, so a store
        # made under that driver was attributed to PC $0000. $0000 is not an
        # address any of these ROMs executes from, and it read as a plausible
        # answer: it is the same shape of bug as the $10300 offset in journal 13.
        bus.cur_pc = self.pc
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
            # The +1 is for a page crossing, and a page crossing costs a cycle
            # ONLY when the instruction actually reads. The 6502 does not pay it
            # for a store or for a read-modify-write, because the address is
            # already on the bus. Paying it unconditionally gave every
            # `sta $8001` / `sta $2007` in this cartridge one cycle too many,
            # and those are the instructions its bank-switch loops are made of --
            # so the drift accumulates across a scanline rather than showing up
            # as a one-off. Found by src/testing/test_nestrace_cpu.py, which
            # measured `sta $0200,x` at 6 cycles where the opcode table says 5.
            if _pays_page_cross(name):
                cyc += 1
            val = bus.read((base & 0xFF00) | (addr & 0xFF))
        elif mode == "absy":
            base = lo | (hi << 8)
            addr = (base + self.y) & 0xFFFF
            if _pays_page_cross(name):
                cyc += 1
            val = bus.read((base & 0xFF00) | (addr & 0xFF))
        elif mode == "indx":
            z = (lo + self.x) & 0xFF
            p = bus.read(z) | (bus.read((z + 1) & 0xFF) << 8)
            addr = val = p
        elif mode == "indy":
            z = lo
            p = bus.read(z) | (bus.read((z + 1) & 0xFF) << 8)
            addr = (p + self.y) & 0xFFFF
            if _pays_page_cross(name):
                cyc += 1
            val = bus.read((p & 0xFF00) | (addr & 0xFF))
        elif mode == "ind":
            p = lo | (hi << 8)
            addr = val = bus.read(p) | (bus.read((p & 0x1000) | ((p + 1) & 0xFF)) << 8)
        else:
            addr = val = 0

        self.pc = (self.pc + ln) & 0xFFFF
        self.cycles += cyc
        R = self._exec
        if name not in ("nop",):
            R(name, addr, val, target, mode)
        self.log.append((self.cycles, self.pc, op, self.a, self.x, self.y, self.sp, 0))

    # ---- the operations. `addr` is the effective address (for stores too),
    # `val` the operand value (for stores the low byte read). `mode` is the
    # addressing mode of THIS instruction -- it is passed in rather than
    # recovered from self.log, because self.log is only appended to after
    # _exec returns, so anything read out of it during _exec is the previous
    # instruction's state.
    def _exec(self, name, addr, val, target, mode="imp"):
        bus = self.bus
        n = name
        # Accumulator-mode shifts ($0A/$2A/$4A/$6A) are the only ones with no
        # operand byte. The mode is a property of the opcode, so it is
        # available here; the previous instruction's opcode is not.
        acc = mode == "acc"
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
            # INC and DEC set N and Z from the *memory* result and leave C
            # alone. The old code wrote the byte and touched no flag at all,
            # which is invisible in a straight-line test and lethal in a loop:
            # `ldy #0 / ... / iny / dec $F2 / bne loop` leaves Z=0 for 32
            # iterations and then inherits Z=1 from the `tya`, the branch falls
            # through, and the loop exits early with no error anywhere. It is
            # how branch_timing_tests/1 uploaded 512 bytes of its 944-byte font
            # and then rendered a screen of blanks.
            v = (bus.read(addr) + 1) & 0xFF
            bus.write(addr, v)
            self.setzn(v)
        elif n == "dec":
            v = (bus.read(addr) - 1) & 0xFF
            bus.write(addr, v)
            self.setzn(v)
        elif n == "inx":
            self.x = self.setzn(self.x + 1)
        elif n == "iny":
            self.y = self.setzn(self.y + 1)
        elif n == "dex":
            self.x = self.setzn(self.x - 1)
        elif n == "dey":
            self.y = self.setzn(self.y - 1)
        elif n == "asl":
            v = self.a if acc else bus.read(addr)
            self.c = (v >> 7) & 1
            v = self.setzn(v << 1)
            if acc:
                self.a = v
            else:
                bus.write(addr, v)
        elif n == "lsr":
            v = self.a if acc else bus.read(addr)
            self.c = v & 1
            v = self.setzn(v >> 1)
            if acc:
                self.a = v
            else:
                bus.write(addr, v)
        elif n == "rol":
            v = self.a if acc else bus.read(addr)
            c = self.c
            self.c = (v >> 7) & 1
            v = self.setzn((v << 1) | c)
            if acc:
                self.a = v
            else:
                bus.write(addr, v)
        elif n == "ror":
            v = self.a if acc else bus.read(addr)
            c = self.c
            self.c = v & 1
            v = self.setzn((v >> 1) | (c << 7))
            if acc:
                self.a = v
            else:
                bus.write(addr, v)
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
    cpu: "CPU"

    def __init__(self, prg: bytes, chr_, mapper: int, vertical: bool,
                 chr_ram: bool = False):
        self.ram = bytearray(0x800)
        self.prg = prg
        self.chr_rom = not chr_ram
        self.chr = bytearray(chr_) if len(chr_) else bytearray(0x2000)
        self.mapper = mapper
        self.prgram = bytearray(0x2000)
        self.cycles = 0
        self.vertical = vertical
        if mapper not in (0, 4):
            raise UnsupportedMapper(mapper)
        # NROM
        self.nrom16 = len(prg) == 0x4000
        # MMC3
        # MMC3: R0-R7 select four 1 KiB CHR banks / two 1 KiB PRG banks / one
        # 2 KiB PRG bank; R8 is the 2 KiB CHR bank at $1000-$1FFF and is chosen
        # by writing >= $40 to $8001. Nine entries, not eight.
        self.bankreg = 6
        self.regs = [0, 0, 0, 0, 0, 0, 0x0E, 0x0F, 0]
        self.prg_mode = 0
        self.chr_a12_inv = False
        # Where PRG mode was last changed, and by what. PRG mode swaps the R6 and
        # R7 windows, so knowing *who* set it is the difference between reading a
        # disassembly of the right module and reading the wrong one.
        self.prg_mode_from: tuple[int, int, int] | None = None
        self.irq_latch = 0
        self.irq_reload = False
        self.irq_pending = False
        self.irq_enabled = False
        self.scanline = 0
        self.irq_cycles = 0
        self.a12 = 0
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
        self.abs_dot = 0
        self.sl = 0
        self.ppu_writes: collections.Counter = collections.Counter()
        self.ppu_write_log: list[tuple[int, int, int]] = []
        self.ppu7: list[tuple[int, int, int]] = []
        self.ppu7_targets: collections.Counter = collections.Counter()
        # Which instruction performed each PPU register write. `w:2007` is how
        # you find out who is responsible for the palette being all $0F: the
        # register write alone cannot tell you whether the value came from a
        # palette table in ROM, from RAM the fade routine owns, or from the
        # black fill in `initcols`.
        self.reg_writes: dict[int, list[tuple[int, int, int, int]]] = {}
        self.cur_pc = 0
        self.nmi_count = 0
        self.irq_count = 0
        self.sprite0_x = -1
        self.sprite0_y = -1
        self.dma_pending = 0
        # Controller. `$4016` read 0. A game that polls the pad and this returns a
        # constant 0 sees "nothing ever pressed, forever" -- and this project's
        # whole milestone route IS button presses, so a tracer without this is
        # not a diagnostic, it is a different program. `buttons` is the current
        # frame's state in the hardware's own bit order (bit 0 = A ... bit 7 =
        # Right); `pad_latch`/`pad_i` are the standard 8-deep serial shift.
        self.buttons = 0
        self.pad_latch = 0
        self.pad_i = 8
        self.pad_reads = 0
        # Every write to a watched RAM cell, with the instruction that made it.
        # BizHawk has no memory callback on this core -- `QuickNES.
        # get_MemoryCallbacks()` throws unconditionally, measured in
        # tools/bizhawk/writes.lua -- so this is the only way to answer "who
        # writes $004D", and a RAM snapshot cannot: the byte that decides the
        # level index is written and read inside a single frame and is gone by
        # the next frame boundary.
        self.ram_writes: dict[int, list[tuple[int, int, int, int]]] = {}
        self.halted = ""

    # -- PRG mapping, by mapper.
    #
    # NROM: 16 KiB images are mirrored at both $8000 and $C000; 32 KiB images
    # are one flat 32 KiB window. There is no banking at all, so a NROM test
    # ROM run through the MMC3 map below reads the wrong bytes at $A000 and
    # $E000 and fails for reasons that have nothing to do with the thing being
    # tested.
    #
    # MMC3 8 KiB slots: 16 of them in 128 KiB. PRG mode 0 is $8000=R6,
    # $A000=R7, $C000=slot 14, $E000=slot 15; mode 1 swaps R6 and R7 in the
    # first two windows and leaves the fixed pair alone. The fixed pair staying
    # fixed in both modes is the load-bearing part: in mode 1 the cartridge's own
    # reset does `lda #$40 / sta $A001`, which is a *data* write to whichever
    # register is selected -- not a mode write -- and a model that takes the PRG
    # mode bit from there sends $E000-$FFFF to R7. The reset and IRQ vectors live
    # in the fixed window, so they then read as whatever the banked window holds,
    # BRK vectors through garbage, and the cartridge spins on BRK at $1000 instead
    # of booting.
    def _prg_offset(self, cpu: int) -> int:
        if cpu < 0x8000:
            return None
        if self.mapper == 0:
            if self.nrom16:
                return (cpu & 0x3FFF)
            return (cpu & 0x7FFF)
        n = len(self.prg) // 0x2000          # number of 8 KiB slots
        if cpu < 0xA000:
            slot = self.regs[7] & 0x0F if self.prg_mode else self.regs[6] & 0x0F
        elif cpu < 0xC000:
            slot = self.regs[6] & 0x0F if self.prg_mode else self.regs[7] & 0x0F
        elif cpu < 0xE000:
            slot = n - 2
        else:
            slot = n - 1
        return (slot % n) * 0x2000 + (cpu & 0x1FFF)

    # -- CHR mapping. NROM has none: one flat 8 KiB (CHR-ROM or CHR-RAM).
    # MMC3's eight 1 KiB windows are the load-bearing part for this game --
    # Magician is MMC3 with 32 KiB of CHR-RAM, and with the banks left alone
    # every tile fetch returns the same 8 KiB and the screen is noise.
    #
    # Mode 0 ($8000 bit 1 clear):  $0000 R0, $0400 R1, $0800 R4, $0C00 R5,
    #                             $1000 R2, $1400 R3, $1800 R6, $1C00 R7
    # Mode 1:                      $0000 R2, $0400 R3, $0800 R4, $0C00 R5,
    #                             $1000 R6, $1400 R7, $1800 R0, $1C00 R1
    # If the value last written to R0 has bit 6 set, R8 covers the whole
    # $1000-$1FFF as one 2 KiB bank and R0/R1 move up to $0000-$07FF.
    def _chr_offset(self, ppu: int) -> int:
        ppu &= 0x1FFF
        if self.mapper == 0:
            return ppu
        r = self.regs
        mask = (len(self.chr) // 0x400) - 1
        inv = self.chr_a12_inv
        if r[0] & 0x40:
            if ppu >= 0x1000:
                return (((r[8] & ~1) + (1 if ppu >= 0x1400 else 0)) & mask) * 0x400 \
                    + (ppu & 0x3FF)
            tab = (0, 1, 4, 5) if not inv else (2, 3, 4, 5)
        elif inv:
            tab = (2, 3, 4, 5, 6, 7, 0, 1)
        else:
            tab = (0, 1, 4, 5, 2, 3, 6, 7)
        return (r[tab[ppu >> 10]] & mask) * 0x400 + (ppu & 0x3FF)

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
                self._a12(self.v & 0x3FFF)
                self._inc_v()
                v = self.ppu_read(self.v & 0x3FFF)
                self.readbuf = self.ppu_read((self.v & 0x3FFF) - 1 if self.v >= 0x3F00 else (self.v & 0x3FFF))
                self._inc_v()
                return v
            return self.ppu_read(self.v & 0x3FFF) if (a & 7) == 7 else 0
        if a < 0x4020:
            if a == 0x4016:
                # Standard NES serial read: bit 0 of the shift register walks A,
                # B, Select, Start, Up, Down, Left, Right; after eight reads the
                # hardware returns 1s, which is what terminates the game's
                # `cmp #$01 / bcc` loop (DISP.SRC:340-344, `jk0`). Returning 0
                # forever would hang it.
                self.pad_reads += 1
                if self.pad_i >= 8:
                    return 1
                v = (self.pad_latch >> self.pad_i) & 1
                self.pad_i += 1
                return v
            if a == 0x4017:
                # $4017 write-only on the NES (APU frame counter); the game never
                # reads it, and the 2-bit-per-button read it does not do is
                # folded into $4016 above.
                return 1
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
        # MMC3: reading the R6 data port returns the scanline counter and
        # acknowledges a pending IRQ. Without this a poll of $8006 sees PRG
        # bytes instead, and the cartridge -- which does poll it -- never
        # reaches its main loop.
        #
        # Mapper 4 ONLY. `bankreg` starts at 6 and is never changed on a board
        # with no bank registers, so without the `mapper != 0` this read is a
        # no-op path that shadows $8006 on NROM as well -- and $8006 is a
        # perfectly ordinary address in a 16 KiB program. Found by
        # src/testing/test_nestrace_cpu.py, which put a `sta $0200,x` operand
        # byte at $8006 and watched it come back as $00.
        if self.mapper != 0 and self.bankreg == 6 and (a & 0x1FFF) in (0x0006, 0x2006):
            self.irq_pending = False
            self.scanline = self.irq_cycles
            return self.irq_cycles
        off = self._prg_offset(a)
        if off is None:
            return 0xFF
        return self.prg[off]

    def write(self, a: int, v: int) -> None:
        a &= 0xFFFF
        v &= 0xFF
        if a < 0x2000:
            addr = a & 0x7FF
            self.ram[addr] = v
            if addr in self.ram_writes:
                # (frame, cycle, PC, value). The PC is `cur_pc`, which the main
                # loop sets to the address of the instruction being executed --
                # set BEFORE the step, so a store that happens on an instruction's
                # last cycle is attributed to that instruction and not the next.
                self.ram_writes[addr].append(
                    (self.frame, self.cycles, self.cur_pc, v))
            return
        if a == 0x4016:
            # Strobe. On hardware any write reloads the shift register; the game
            # writes 1 then 0 immediately before its eight reads (DISP.SRC:336-340),
            # so both writes reload and the second one is the one that matters.
            self.pad_latch = self.buttons
            self.pad_i = 0
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
                # Both halves of a $2006 write put the address on the PPU bus,
                # so both can clock the MMC3 counter. This is how the
                # mmc3_irq_tests ROMs drive the counter with rendering off.
                self._a12(self.t)
            elif r == 7:
                self.reg_writes.setdefault(r, []).append(
                    (self.cycles, self.cur_pc, self.v & 0x3FFF, v))
                if self.ppu_writes[7] <= 64:
                    self.ppu7.append((self.cycles, self.v & 0x3FFF, v))
                self.ppu7_targets[self.v & 0x3FFF] += 1
                self._a12(self.v & 0x3FFF)
                self.ppu_write(self.v & 0x3FFF, v)
                self._inc_v()
            self.ppu_writes[r] += 1
            if self.ppu_writes[r] <= 4000:
                self.ppu_write_log.append((self.cycles, a, v))
            return
        if a < 0x4020:
            if a == 0x4014:
                self._oam_dma(v)
                # 513 cycles, plus one more if the DMA starts on an odd CPU
                # cycle. oam_read and dmc_dma_during_read4 both measure the
                # stealing; without the cost the instruction after the write
                # lands a cycle early and the measured total is short.
                self.cpu.cycles += 513 + (self.cpu.cycles & 1)
            return
        if a < 0x6000:
            return
        if a < 0x8000:
            self.prgram[a - 0x6000] = v
            return
        if self.mapper == 0:
            return                            # NROM: the writes go nowhere
        if a < 0xC000:
            # $8000/$A000 even: bank select (bits 0-2), CHR A12 inversion
            # (bit 1 of the same port), PRG mode (bit 6), IRQ latch reload
            # (bit 7). $8001/$A001 odd: data for the selected register. Both
            # windows select the same register -- that is the chip. PRG mode
            # does NOT come from the odd port.
            if (a & 1) == 0:
                self.bankreg = v & 7
                inv = (v >> 1) & 1
                if inv != self.chr_a12_inv:
                    self.chr_a12_inv = inv
                if ((v >> 6) & 1) != self.prg_mode:
                    self.prg_mode = (v >> 6) & 1
                    self.prg_mode_from = (a, self.cur_pc, v)
                self.irq_reload = self.irq_reload or bool(v & 0x80)
            else:
                self.regs[self.bankreg] = v
            return
        if (a & 0xFFF0) == 0xC000:
            if (a & 1) == 0:
                self.irq_latch = v
            else:
                # $C001 acknowledges the IRQ, zeroes the counter and arms a
                # reload on the *next* clock. It does not reload now, and it
                # does not raise an IRQ by itself (mmc3_irq_tests 2.Details 6).
                self.irq_pending = False
                self.irq_cycles = 0
                self.scanline = 0
                self.irq_reload = True
            return
        # $E000 even / $E001 odd: the MMC3 interrupt controls. $E001 bit 7
        # disables the IRQ.
        #
        # PRG mode is deliberately NOT taken from here. NESdev lists bit 6 of
        # $E001 as PRG mode on MMC3, and honouring it made *both* ROMs swap R6
        # and R7 part way through: this game's IRQ handler is
        # `sta $e000 / sta $e001` with A = Y ("clear MMC3 IRQ"), so the mode
        # would flip on Y alone, and `reset` -- which sets up `bnk 6,#$0` and
        # `bnk 7,#$1` and then `jmp start` -- needs mode 0 to keep its own main
        # loop at $8000. The source therefore settles it: the only place this
        # game ever writes a mode bit is `stx $8000` with a value of 0-7, inside
        # the `bnk` macro (`x0.pds:188`). Mode is read from there alone.
        if (a & 1) == 1:
            self.irq_enabled = not (v & 0x80)
            if v & 0x80:
                self.irq_pending = False
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
            return self.chr[self._chr_offset(a)]
        if a < 0x3F00:
            return self.vram[self._nt_index(a)]
        return self.pal[a & 0x1F] if (a & 0x13) != 0x10 else self.vram[self._nt_index(0x2000 + (a & 0x0FFF))]

    def ppu_write(self, a: int, v: int) -> None:
        a &= 0x3FFF
        if a < 0x2000:
            if self.chr_rom:
                return                       # CHR-ROM: the write goes nowhere
            self.chr[self._chr_offset(a)] = v
        elif a < 0x3F00:
            self.vram[self._nt_index(a)] = v
        elif (a & 0x13) == 0x10:
            self.vram[self._nt_index(0x2000 + (a & 0x0FFF))] = v
        else:
            self.pal[a & 0x1F] = v

    def _inc_v(self):
        # $2000 bit 3 is "increment VRAM address by 32". Bit 2 is unused; the
        # old code stepped by 1 or 32 on bit 2, so every game that used the
        # increment bit -- which is all of them -- filled its nametable one byte
        # at a time down a column and left the rest of it as $00.
        self.v = (self.v + ((32 if self.ctrl & 8 else 1))) & 0x7FFF

    def _maybe_nmi(self):
        if self.vblank and (self.ctrl & 0x80):
            if not self.cpu.nmi_pending:
                self.nmi_count += 1
            self.cpu.nmi_pending = True

    # ---- the MMC3 scanline counter.
    #
    # This is the part that was missing entirely, and it is the reason the
    # cartridge as well as the rebuild never reached its main loop: `irq_pending`
    # was set by nothing and delivered to nothing, so `cpu.irq_pending` stayed
    # false forever and the IRQ vector was never taken.
    #
    # The counter is clocked by the *rising edge of bit 12* of the PPU address
    # bus. Two things drive that bus: the PPU's own tile fetches (one rise per
    # rendering scanline, at dot 260) and CPU accesses to $2006/$2007, which
    # clock it even when rendering is off -- that is how mmc3_irq_tests clocks
    # the counter by hand. A fall does not clock it, and no change does not
    # clock it.
    #
    # Per-clock behaviour, as measured on real cartridges by Shay Green and
    # written up in roms/mmc3_irq_tests/readme.txt:
    #   reload pending  -> counter = latch, no decrement
    #   counter == 0    -> counter = latch
    #   otherwise       -> counter -= 1
    # and after that, if the counter is zero and IRQs are enabled, raise.
    # Writing $C001 acknowledges, zeroes and arms the reload; it does not raise
    # an IRQ itself, and it does not reload until the next clock.
    def _irq_clock(self) -> None:
        if self.mapper != 4:
            return
        if self.irq_reload:
            self.irq_reload = False
            self.irq_cycles = self.irq_latch
        elif self.irq_cycles == 0:
            self.irq_cycles = self.irq_latch
        else:
            self.irq_cycles = (self.irq_cycles - 1) & 0xFF
        if self.irq_cycles == 0 and self.irq_enabled:
            self.irq_pending = True

    def _a12(self, addr: int) -> None:
        """A CPU-side PPU address access. Clocks the counter on a 0->1 edge of
        bit 12, whatever the access was."""
        hi = (addr >> 12) & 1
        if hi and not self.a12:
            self._irq_clock()
        self.a12 = hi

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
        prev = self.abs_dot
        self.abs_dot += d * 3
        # 241 counter clocks per frame: one on each of scanlines 0-239, where
        # the PPU's fetches drive A12 up, and one on the pre-render line.
        if self.mask & 0x18:
            for f in range(prev // FRAME_DOTS, self.abs_dot // FRAME_DOTS + 1):
                base = FRAME_DOTS * f
                lo = max(prev, base)
                for i in range(bisect.bisect_right(SCANLINE_CLOCK, lo - base),
                               bisect.bisect_right(SCANLINE_CLOCK, self.abs_dot - base)):
                    self._irq_clock()
        self.dot += d * 3
        while self.dot >= FRAME_DOTS:
            self.dot -= FRAME_DOTS
            self.frame += 1
        new_sl = self.dot // 341
        if self.sl < 241 <= new_sl and not self.vblank:
            self.vblank = True
            self.status |= 0x80
            self._maybe_nmi()
        elif new_sl < 241 and self.vblank:
            # Vblank flag is cleared at the pre-render line without a read of
            # $2002. Without this a ROM that polls the flag instead of reading
            # the register -- and vbl_nmi_timing/3 does exactly that -- waits
            # forever.
            self.vblank = False
            self.status &= 0x7F
        self.sl = new_sl
        # Hand the MMC3 IRQ line to the CPU. It stays asserted until the handler
        # acknowledges it by reading $8006 or writing $E001, so if the handler
        # does not, the CPU takes it again -- which is what the hardware does.
        if self.irq_pending and self.irq_enabled:
            if not self.cpu.irq_pending:
                self.irq_count += 1
            self.cpu.irq_pending = True

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

    def _inc_y(self, v: int) -> int:
        """The PPU's vertical scroll increment, once per rendering scanline."""
        if (v & 0x7000) != 0x7000:
            return v + 0x1000
        v &= ~0x7000
        y = (v & 0x03E0) >> 5
        if y == 29:
            y = 0
            v ^= 0x0800                 # coarse Y 29 wraps and flips nametable Y
        elif y == 31:
            y = 0                        # 31 is skipped entirely, no flip
        else:
            y += 1
        return (v & ~0x03E0) | (y << 5)

    # $2000 bit 4 is the *background* pattern table and bit 5 selects the bank
    # for 8x16 sprites. The old code used bit 5 for the background and took its
    # complement, so every background tile was fetched from the opposite table
    # and then used to build the nametable address as well. Two wrong answers
    # from one wrong bit.
    def _bg_base(self) -> int:
        return 0x1000 if (self.ctrl & 0x10) else 0x0000

    def _render_scanline(self, y: int, v: int) -> list[int]:
        """`v` is the address latch for this scanline. Returns 256 palette
        indices -- the 6-bit palette RAM entries, which is what pinky's
        `FramebufferPixel::base_color_index` hands the test suite's md5."""
        backdrop = self.pal_idx(0)
        out = [backdrop] * 256
        if y >= 240:
            return out
        vram = self.vram
        chr_ = self.chr
        mapper4 = self.mapper == 4
        show_bg = bool(self.mask & 0x08)
        show_sp = bool(self.mask & 0x04)
        left8 = bool(self.mask & 0x02)
        grey = bool(self.mask & 0x01)
        bgopaque = bytearray(256)

        def pat(a: int) -> int:
            if mapper4:
                return chr_[self._chr_offset(a)]
            return chr_[a & 0x1FFF]

        bgbase = self._bg_base()
        if show_bg:
            coarse_x = v & 0x1F
            nt_x = (v >> 10) & 1
            nt_y = (v >> 11) & 1
            coarse_y = (v >> 5) & 0x1F
            fine_y = (v >> 12) & 7
            nt = ((nt_y << 1) | nt_x) * 0x400
            att = 0x3C0 + (coarse_y >> 2) * 8
            # 33 tiles: 32 to cover the line, plus one for the fine-X shift.
            shift = coarse_x * 8 + self.x
            for i in range(33):
                cx = (coarse_x + i) & 0x1F
                ntx = nt ^ (((coarse_x + i) >> 5) & 1) * 0x400
                tile = vram[(ntx + coarse_y * 32 + cx) & 0x7FF]
                base = bgbase + tile * 16 + fine_y
                lo = pat(base)
                hi = pat(base + 8)
                if not (lo | hi):
                    continue
                a = vram[(ntx + att + (cx >> 2)) & 0x7FF]
                q = ((coarse_y & 2) << 1) | (cx & 2)
                p = ((a >> q) & 3) * 4
                for k in range(8):
                    px = i * 8 + k - shift
                    if not 0 <= px < 256 or (px < 8 and not left8):
                        continue
                    bit = 7 - k
                    b = ((lo >> bit) & 1) | (((hi >> bit) & 1) << 1)
                    if b:
                        bgopaque[px] = 1
                        out[px] = backdrop if grey else self.pal_idx(p + b)
        if not show_sp:
            return out
        oam = self.oam
        h16mode = bool(self.ctrl & 0x20)
        spbase = bgbase if h16mode else (0x1000 - bgbase)
        drawn = bytearray(256)          # OAM order: the first sprite wins
        nvis = 0
        for i in range(64):
            sy = oam[i * 4] - 1
            row_in = y - sy
            h = 16 if (oam[i * 4 + 2] & 0x80) else 8
            if not 0 <= row_in < h:
                continue
            nvis += 1
            if nvis > 8:
                self.status |= 0x20      # sprite overflow
            tile = oam[i * 4 + 1]
            attr = oam[i * 4 + 2]
            xpos = oam[i * 4 + 3]
            if attr & 0x40:
                row_in = h - 1 - row_in
            if h == 16:
                t = (tile & 0xFE) + (1 if row_in >= 8 else 0)
                r = row_in & 7
            else:
                t = tile
                r = row_in
            addr = spbase + t * 16 + r
            lo = pat(addr)
            hi = pat(addr + 8)
            if not (lo | hi):
                continue
            for k in range(8):
                px = xpos + k
                if not 0 <= px < 256 or (px < 8 and not left8):
                    continue
                bit = k if (attr & 0x40) else 7 - k
                b = ((lo >> bit) & 1) | (((hi >> bit) & 1) << 1)
                if not b:
                    continue
                # Sprite 0 hit: opaque sprite 0 over opaque background, with both
                # halves of rendering on and the leftmost 8 pixels open.
                if i == 0 and show_bg and bgopaque[px] and not (self.status & 0x40):
                    self.status |= 0x40
                    self.sprite0_x = px
                    self.sprite0_y = y
                if drawn[px]:
                    continue
                drawn[px] = 1
                if (attr & 0x20) and bgopaque[px]:
                    continue            # behind an opaque background pixel
                out[px] = backdrop if grey else self.pal_idx(0x10 + (attr & 3) * 4 + b)
        return out

    def pal_idx(self, i: int) -> int:
        i &= 0x1F
        if (i & 0x13) == 0x10:
            i &= 0x0F
        return self.pal[i] & 0x3F

    def render_frame(self) -> list[list[int]]:
        """Render one whole frame from the current PPU state.

        `v` and `t` are snapshotted and the vertical increment is simulated, so
        the frame is internally consistent even though the CPU keeps writing
        to the latches mid-frame. The previous code read the vertical scroll out
        of `t` -- the write-only register -- so a game that scrolls by writing
        $2005 and never $2006 got no vertical scroll at all, and one that writes
        $2006 mid-frame got the wrong one.
        """
        if not (self.mask & 0x18):
            return [[self.pal_idx(0)] * 256 for _ in range(240)]
        self.status &= ~0x60
        v = self.v
        t = self.t
        v = (v & ~0x7BE0) | (t & 0x7BE0)   # pre-render: vertical bits from t
        rows = []
        for y in range(240):
            v = (v & ~0x041F) | (t & 0x041F)   # each line: horizontal bits from t
            rows.append(self._render_scanline(y, v))
            v = self._inc_y(v)
        return rows


def load_rom(path: pathlib.Path):
    b = path.read_bytes()
    if b[:4] != b"NES\x1a":
        raise SystemExit(f"{path}: not an iNES file")
    nprg, nchr, f6, f7 = b[4], b[5], b[6], b[7]
    prg = b[16:16 + nprg * 16384]
    chr_ram = nchr == 0
    chr_ = b[16 + nprg * 16384: 16 + nprg * 16384 + nchr * 8192] if nchr else bytearray(0x2000)
    mapper = (f6 >> 4) | (f7 & 0xF0)
    vertical = bool(f6 & 1)
    # `chr_ram` has to come from the header, not from `len(chr_)`: a zero CHR
    # count means 8 KiB of *RAM*, and load_rom substitutes that 8 KiB so the
    # array exists. Deriving ROM-vs-RAM from the length therefore called every
    # CHR-RAM cartridge CHR-ROM, `ppu_write` returned before storing, and every
    # tile a game uploaded to CHR-RAM vanished -- a black screen with a
    # nametable full of tile numbers pointing at nothing. Four of the test
    # suites are CHR-RAM cartridges and all four rendered one flat colour.
    return prg, chr_, mapper, vertical, chr_ram


def load_syms(rom: pathlib.Path) -> dict[str, int]:
    """Symbols the assembler wrote next to the ROM, for `--where label`.

    A missing or unreadable mag.sym is not an error: numeric addresses still
    work, and the tracer's job is to work on a ROM with no build tree beside
    it at all.
    """
    out: dict[str, int] = {}
    path = rom.parent / "mag.sym"
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        name, _, val = line.partition("=")
        val = val.strip()
        if val.startswith("$"):
            try:
                out[name.strip()] = int(val[1:], 16)
            except ValueError:
                pass
    return out


def resolve(s: str, syms: dict[str, int]) -> int:
    """`$F5D4`, `0xF5D4`, `62932` or `movepal` -> a PC.

    The `$` form is the one this project's own symbol table uses (mag.sym writes
    `mapind = $004D`), and it was the one that did not work: `int(s, 0)` rejects
    a leading `$`, so `--where $F5D4` raised ValueError and every other spelling
    was fine. A documented spelling that raises is worse than an undocumented
    one, because it is the one people try.
    """
    t = s.strip()
    if t in syms:
        return syms[t]
    if "|" in t and t.split("|", 1)[0] in syms:
        return syms[t.split("|", 1)[0]]
    if t.startswith("$"):
        return int(t[1:], 16)
    return int(t, 0)


# --------------------------------------------------- recorded input replay
#
# src/play records one line per frame: "-" for nothing pressed, otherwise a
# comma-separated list of button names, under a `# frames=N valid_from_poweron=`
# header (src/play/emu.py:save_inputs). Reading that format HERE, rather than
# inventing a second one, is the whole point: the input log that verified
# Beta 1 is the input log this replays, so a difference found here is a
# difference between two ROMs under identical input, not between two harnesses.
#
# The button NAMES are mapped to the hardware's own bit order for $4016 --
# bit 0 A, 1 B, 2 Select, 3 Start, 4 Up, 5 Down, 6 Left, 7 Right -- which is
# the order `jk0` collects with `rol` (DISP.SRC:336-344). Note that this is NOT
# the order src/play lists them in (`BUTTONS = Up,Down,Left,Right,Select,Start,
# B,A`), which is BizHawk's button enumeration and not a bit layout.
HARDWARE_BITS = {"A": 0, "B": 1, "Select": 2, "Start": 3,
                 "Up": 4, "Down": 5, "Left": 6, "Right": 7}


def load_input_log(path: pathlib.Path) -> tuple[list[int], dict[str, str]]:
    """Return (per-frame button masks, header key=value pairs).

    Refuses to be quiet about the two ways a log can be wrong: a header that
    claims N frames over a body of a different length, and a line naming a
    button this machine has never heard of. Both read as "the replay ran" when
    they are not.
    """
    head: dict[str, str] = {}
    masks: list[int] = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if line.startswith("#"):
            for tok in line[1:].split():
                k, _, v = tok.partition("=")
                head[k] = v
            continue
        if not line.strip():
            # The writer emits "-" for an idle frame precisely because an empty
            # line is skipped by the reader. If one turns up anyway, say so
            # rather than silently spending one frame fewer than the log claims.
            raise SystemExit(
                f"{path}:{lineno}: blank line in an input log. The format writes "
                f"'-' for an idle frame; a blank line here would replay one "
                f"frame fewer than the log claims and still look like a run.")
        m = 0
        if line.strip() != "-":
            for name in (b for b in line.split(",") if b):
                if name not in HARDWARE_BITS:
                    raise SystemExit(
                        f"{path}:{lineno}: button {name!r} is not one of "
                        f"{sorted(HARDWARE_BITS)}. Guessing a bit for it would put "
                        f"a plausible wrong edge into the replay.")
                m |= 1 << HARDWARE_BITS[name]
        masks.append(m)
    claimed = head.get("frames")
    if claimed is not None and claimed.isdigit() and int(claimed) != len(masks):
        raise SystemExit(
            f"{path}: header says frames={claimed} but the body holds "
            f"{len(masks)} frames. Replaying the body would run the machine a "
            f"different length than the run that recorded it.")
    return masks, head


def make_machine(rom: pathlib.Path):
    """`(.nes path) -> (Bus, CPU)`, or raise UnsupportedMapper."""
    prg, chr_, mapper, vertical, chr_ram = load_rom(rom)
    bus = Bus(prg, chr_, mapper, vertical, chr_ram)
    cpu = CPU(bus)
    bus.cpu = cpu
    cpu.pc = bus.read(0xFFFC) | (bus.read(0xFFFD) << 8)
    return bus, cpu


# ------------------------------------------------------------------ test suite
#
# pinky's nes-testsuite: a JSON file per testcase holding the digest of the ROM
# it is written against, how many frames to run, and the md5 of the 256x240
# framebuffer it expects at the end. The framebuffer is one byte per pixel and
# that byte is the 6-bit palette RAM entry -- `FramebufferPixel::base_color_index`
# -- so the expected values depend on palette RAM, the nametables, the attribute
# table and CHR, and on nothing else. No master palette, no emphasis.
#
# The harness runs `elapsed_frames * 2` frames and reads the framebuffer at the
# frame boundary, which is where this renderer's `bus.frame` increments too.
#
# The digest check is not optional. A testcase names the ROM it was calibrated
# against; running some *other* ROM under the same expectations produces a
# confident wrong answer, which is the exact failure mode this whole tool
# exists to eliminate.

BLARGG_FAILURE_MD5 = "0941a56e4c62c6026264952a9bfaea35"
"""The framebuffer md5 of blargg's own "Failed" screen. Ten of the 46 JSON
testcases expect it: the seven blargg_apu ones and the three blargg_ppu ones.
pinky recorded those as failures too, so for them a PASS from us would mean we
disagree with the reference, and a match on this digest means the ROM printed
its failure screen -- which is the *expected* outcome, not our bug."""


def framebuffer_md5(bus: Bus) -> str:
    h = hashlib.md5()
    for row in bus.render_frame():
        h.update(bytes(row))
    return h.hexdigest()


def run_frames(bus: Bus, cpu: CPU, frames: int, cycle_cap: int) -> str:
    """Run until `frames` complete PPU frames have been generated. Returns '' on
    success, or the reason it stopped early."""
    while bus.frame < frames:
        if cpu.cycles >= cycle_cap:
            return f"cycle cap at frame {bus.frame}/{frames}"
        try:
            cpu.step()
        except Halt as h:
            return f"halted: {h}"
        bus.tick(cpu.cycles)
    return ""


def index_roms(romdir: pathlib.Path) -> dict[str, pathlib.Path]:
    out: dict[str, pathlib.Path] = {}
    for p in sorted(romdir.rglob("*.nes")):
        out[hashlib.md5(p.read_bytes()).hexdigest()] = p
    return out


def run_json_suite(root: pathlib.Path, roms: dict[str, pathlib.Path],
                   wanted: set[str], cycle_cap: int, rows: list) -> None:
    for js in sorted((root / "testcases").rglob("*.json")):
        suite = js.parent.name
        if wanted and suite not in wanted:
            continue
        spec = json.loads(js.read_text())
        want_md5 = spec["test"]["expected_framebuffer_md5sum"]
        frames = spec["test"]["elapsed_frames"] * 2
        name = f"{suite}/{js.stem}"
        rom = roms.get(spec["romfile_md5sum"])
        if rom is None:
            rows.append((suite, name, "NO-ROM", "no ROM with that digest under roms/"))
            continue
        got = hashlib.md5(rom.read_bytes()).hexdigest()
        if got != spec["romfile_md5sum"]:
            rows.append((suite, name, "MD5-MISMATCH",
                         f"rom/{rom.name} is {got}, testcase wants {spec['romfile_md5sum']}"))
            continue
        try:
            bus, cpu = make_machine(rom)
        except UnsupportedMapper as m:
            rows.append((suite, name, "UNSUPPORTED", f"mapper {m.args[0]}"))
            continue
        why = run_frames(bus, cpu, frames, cycle_cap)
        if why:
            rows.append((suite, name, "ERROR", f"{why} (PC=${cpu.pc:04X})"))
            continue
        md5 = framebuffer_md5(bus)
        if md5 == want_md5:
            rows.append((suite, name, "PASS", f"{frames} frames"))
        elif want_md5 == BLARGG_FAILURE_MD5:
            rows.append((suite, name, "KNOWN-FAIL",
                         "printed blargg's failure screen, which is what the testcase expects"))
        else:
            rows.append((suite, name, "FAIL", f"got {md5[:12]}, want {want_md5[:12]}"))


def run_mmc3_suite(roms: dict[str, pathlib.Path], wanted: set[str],
                   frames: int, rows: list) -> None:
    """mmc3_irq_tests has no JSON testcases, so read its own result byte.

    `source/validation.asm` puts it at zero-page $F8: 1 means passed, anything
    else is the failure code listed in the ROM's readme, and the ROM then spins
    in `forever`. Nine codes are enumerated there; 10 would mean it never got
    as far as reporting."""
    for rom in sorted(roms.values()):
        if rom.parent.name != "mmc3_irq_tests":
            continue
        suite = "mmc3_irq_tests"
        if wanted and suite not in wanted:
            continue
        name = f"{suite}/{rom.stem}"
        bus, cpu = make_machine(rom)
        why = run_frames(bus, cpu, frames, 40_000_000)
        code = bus.ram[0xF8]
        if code == 1:
            rows.append((suite, name, "PASS", f"$F8=1 after {bus.frame} frames"))
        elif code == 0:
            rows.append((suite, name, "ERROR",
                         f"$F8 still 0 after {frames} frames ({why or 'no halt'})"))
        else:
            rows.append((suite, name, "FAIL", f"$F8={code} (code {code} in the readme)"))


def run_instr_suite(roms: dict[str, pathlib.Path], wanted: set[str],
                    frames: int, rows: list) -> None:
    """instr_test-v5 has no JSON testcases either; read its result byte.

    From its readme: the status byte lives at $6000 ($80 = still running,
    $00-$7F = finished with that code) and $DE $B0 $47 is written to $6001-$6003
    while it runs, so a $6000 that goes back to zero without the signature ever
    appearing is a ROM that never started. Code 0 is a pass.

    These ROMs take thousands of frames of real time, which a Python tracer
    cannot afford for all sixteen, so `frames` is a budget and a ROM that is
    still $80 when the budget runs out is reported as INCOMPLETE, not as a
    failure."""
    for rom in sorted(roms.values()):
        if rom.parent.name != "instr_test-v5":
            continue
        suite = "instr_test-v5"
        if wanted and suite not in wanted:
            continue
        name = f"{suite}/{rom.stem}"
        bus, cpu = make_machine(rom)
        why = run_frames(bus, cpu, frames, 400_000_000)
        code = bus.prgram[0]
        sig = bytes(bus.prgram[1:4])
        if sig != b"\xde\xb0\x47":
            rows.append((suite, name, "ERROR",
                         f"no $DE $B0 $47 signature at $6001 ({why or 'ran out of frames'})"))
        elif code == 0x80:
            rows.append((suite, name, "INCOMPLETE",
                         f"still running ($6000=$80) after {bus.frame} frames"))
        elif code == 0:
            rows.append((suite, name, "PASS", f"$6000=0 after {bus.frame} frames"))
        else:
            rows.append((suite, name, "FAIL", f"$6000=${code:02X} after {bus.frame} frames"))


def run_testsuite(args) -> int:
    """The driver: build the ROM index, run every suite, print the table."""
    root: pathlib.Path = args.testsuite
    romdir = root / "roms"
    if not romdir.is_dir() or not (root / "testcases").is_dir():
        print(f"{root}: expected testcases/ and roms/ under it "
              f"(a pinky nes-testsuite checkout)", file=sys.stderr)
        return 2
    roms = index_roms(romdir)
    wanted = set(args.suite)
    rows: list = []
    run_json_suite(root, roms, wanted, args.ts_max_cycles, rows)
    run_mmc3_suite(roms, wanted, args.ts_frames, rows)
    run_instr_suite(roms, wanted, args.ts_frames, rows)

    order = {"PASS": 0, "KNOWN-FAIL": 1, "FAIL": 2, "INCOMPLETE": 3,
             "ERROR": 4, "MD5-MISMATCH": 5, "NO-ROM": 6, "UNSUPPORTED": 7}
    tally: collections.Counter = collections.Counter(r[2] for r in rows)
    bysuite: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for suite, _, verdict, _ in rows:
        bysuite[suite][verdict] += 1
    width = max((len(r[1]) for r in rows), default=10)
    cur = None
    for suite, name, verdict, note in sorted(
            rows, key=lambda r: (list(bysuite).index(r[0]) if r[0] in bysuite else 0, r[1])):
        if suite != cur:
            c = bysuite[suite]
            print(f"\n{suite}  ({', '.join(f'{k} {v}' for k, v in sorted(c.items()))})")
            cur = suite
        print(f"  {verdict:<12} {name:<{width}}  {note}")
    print(f"\n{len(rows)} testcases: "
          + ", ".join(f"{k} {tally[k]}" for k in sorted(tally, key=lambda k: order.get(k, 9))))
    # The number that decides whether the tracer can be trusted: everything that
    # can pass and does, against everything that can pass and does not.
    judged = [r for r in rows if r[2] in ("PASS", "FAIL")]
    if judged:
        print(f"{sum(1 for r in judged if r[2] == 'PASS')}/{len(judged)} "
              f"of the testcases that have a real expected picture pass")
    return 0 if not tally["FAIL"] and not tally["ERROR"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=pathlib.Path, default=None)
    ap.add_argument("--frames", type=float, default=10.0, help="frames of CPU to run")
    ap.add_argument("--trace", type=int, default=0, help="print the first N instructions")
    ap.add_argument("--trace-from", default=None,
                    help="only trace once PC reaches this address or label")
    ap.add_argument("--where", action="append", default=[],
                    help="report when execution reaches PC=LABEL, or w:REG for a PPU write")
    ap.add_argument("--png", type=pathlib.Path)
    ap.add_argument("--png-frame", type=int, default=0,
                    help="capture this frame number instead of the first one that "
                         "has been drawn")
    ap.add_argument("--max-cycles", type=int, default=60_000_000)
    ap.add_argument("--halt-at", action="append", default=[],
                    help="stop when PC reaches this address or label. Combine with "
                         "--last: a program that has fallen into a loop will "
                         "otherwise overwrite the ring buffer with the loop")
    ap.add_argument("--halt-below", type=lambda s: int(s, 0), default=None,
                    help="stop the first time PC drops below this address. RAM is "
                         "$0000-$1FFF, so --halt-below 0x2000 is 'first time it "
                         "left ROM', which is the question a bad return address "
                         "on the stack actually asks")
    ap.add_argument("--ram", type=lambda s: int(s, 0), nargs=2, metavar=("LO", "HI"),
                    help="hex-dump CPU RAM over [lo,hi) at the end")
    ap.add_argument("--inputs", type=pathlib.Path, default=None,
                    help="replay a src/play input log (.inputs.txt): one line per "
                         "frame, '-' for idle, comma-separated button names. The "
                         "buttons are the frame's state from power-on; the log's "
                         "own header is checked against its body length.")
    ap.add_argument("--watch-ram", action="append", default=[],
                    metavar="NAME",
                    help="record every write to a RAM cell, with the PC that made "
                         "it. NAME is a mag.sym symbol or $hex. Repeatable. This is "
                         "the only way to answer 'who wrote this byte': a frame "
                         "snapshot cannot, because the byte can be written and "
                         "read inside one frame, and BizHawk's core here has no "
                         "memory callback at all.")
    ap.add_argument("--ram-watch-window", type=lambda s: int(s, 0), default=0,
                    help="only report writes in frames >= this. A RAM cell that is "
                         "written every frame forever buries the one write you are "
                         "looking for, so the window is usually the honest filter.")
    ap.add_argument("--vram", type=lambda s: int(s, 0), nargs=2, metavar=("LO", "HI"),
                    help="hex-dump PPU nametaps over [lo,hi) at the end")
    ap.add_argument("--last", type=int, default=0,
                    help="print the last N instructions executed (a ring buffer), "
                         "which is where a crash actually happened")
    ap.add_argument("--testsuite", type=pathlib.Path, default=None,
                    metavar="DIR",
                    help="run a pinky nes-testsuite checkout instead of a single "
                         "ROM: DIR is the directory holding testcases/ and roms/")
    ap.add_argument("--suite", action="append", default=[],
                    help="only these suites (repeatable); default is all of them")
    ap.add_argument("--ts-frames", type=int, default=600,
                    help="frame budget for mmc3_irq_tests and instr_test-v5, which "
                         "have no JSON testcases and report through a result byte")
    ap.add_argument("--ts-max-cycles", type=int, default=40_000_000,
                    help="cycle cap per JSON testcase")
    args = ap.parse_args()

    if args.testsuite:
        return run_testsuite(args)
    if args.rom is None:
        ap.error("one of --rom or --testsuite is required")

    prg, chr_, mapper, vertical, chr_ram = load_rom(args.rom)
    syms = load_syms(args.rom)
    print(f"mapper {mapper}  {'vertical' if vertical else 'horizontal'} mirroring  "
          f"PRG {len(prg)} bytes ({len(prg)//8192} x 8 KiB)  "
          f"CHR {len(chr_)} bytes {'RAM' if chr_ram else 'ROM'}")
    if syms:
        print(f"symbols: {len(syms)} from {args.rom.parent / 'mag.sym'}")
    bus = Bus(prg, chr_, mapper, vertical, chr_ram)
    cpu = CPU(bus)
    bus.cpu = cpu
    cpu.pc = bus.read(0xFFFC) | (bus.read(0xFFFD) << 8)
    print(f"entry: nmi=${bus.read(0xFFFA)|(bus.read(0xFFFB)<<8):04X} "
          f"reset=${cpu.pc:04X} irq=${bus.read(0xFFFE)|(bus.read(0xFFFF)<<8):04X}")

    # Input replay. Armed for the whole run, because a button state that starts
    # partway through is a different program and reads like a different ROM.
    log_masks: list[int] = []
    if args.inputs:
        log_masks, head = load_input_log(args.inputs)
        want_frames = len(log_masks)
        if not head.get("valid_from_poweron", "").startswith("True"):
            print(f"WARNING: {args.inputs.name} says valid_from_poweron="
                  f"{head.get('valid_from_poweron')!r}. Replaying a log that does "
                  f"not start at power-on measures whatever state the log assumes.")
        if args.frames and int(args.frames) < want_frames:
            print(f"input log holds {want_frames} frames; --frames asked for "
                  f"{args.frames}. The tail is not replayed.")
        want_frames = min(want_frames, int(args.frames) or want_frames)
        print(f"input: {args.inputs.name}, {want_frames} frames, "
              f"{sum(1 for m in log_masks if m)} with a button held, "
              f"{len(head)} header field(s)")
    else:
        want_frames = int(args.frames)
    total = int((want_frames or args.frames) * 29780.5)
    traced = 0
    seen: collections.Counter = collections.Counter()
    watch: dict = {}
    for w in args.where:
        if w.startswith("w:"):
            # `w:2007` names a CPU-visible PPU register; key on its
            # index within the $2000 window, which is what reg_writes uses.
            watch.setdefault(("w", int(w[2:], 0) & 7), 0)
        else:
            watch.setdefault(("p", resolve(w, syms)), 0)
    ram_watch: dict[int, str] = {}
    for name in args.watch_ram:
        addr = resolve(name, syms)
        if not 0 <= addr < 0x800:
            raise SystemExit(
                f"nestrace: --watch-ram {name} resolved to ${addr:04X}, which is "
                f"not CPU RAM. Zero page and $0100-$07FF only; the PPU and the "
                f"mapper have their own counters.")
        ram_watch[addr & 0x7FF] = name
    if ram_watch:
        for a in ram_watch:
            bus.ram_writes[a] = []
        print(f"watching RAM: " + ", ".join(
            f"{n}=${a:04X}" for a, n in sorted(ram_watch.items()))
            + f"  (frames >= {args.ram_watch_window})")
    halt = ""
    ring: collections.deque = collections.deque(maxlen=args.last or 1)
    halt_at = {resolve(h, syms) for h in args.halt_at}
    trace_from = None if args.trace_from is None else resolve(args.trace_from, syms)
    armed = trace_from is None
    frames_wanted = {}
    if args.png:
        want = args.png_frame or int(args.frames)
        for n in range(1, want + 1):
            frames_wanted[n] = []
    captures: list[tuple[int, list]] = []
    try:
        while cpu.cycles < total and cpu.cycles < args.max_cycles:
            # The pad's state for the frame about to run. Applied at the TOP of
            # the frame, before any instruction of it executes: a game polls
            # $4016 early in vblank and late in the main loop, and "the state
            # during frame N" is only well defined if it does not change inside
            # the frame. That is also what a real controller does -- the buttons
            # are held for the whole frame the player held them.
            if log_masks and bus.frame < len(log_masks):
                bus.buttons = log_masks[bus.frame]
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
            bus.cur_pc = step_pc
            if args.last:
                op = bus.read(step_pc)
                ent = OPS.get(op)
                ring.append((cpu.cycles, step_pc, op, ent[0] if ent else f".{op:02X}",
                             ent[1] if ent else "?", cpu.a, cpu.x, cpu.y, cpu.sp))
            if args.halt_below is not None and step_pc < args.halt_below:
                halt = f"PC fell to ${step_pc:04X}, below ${args.halt_below:04X}"
                break
            if step_pc in halt_at:
                halt = f"reached ${step_pc:04X} (--halt-at)"
                break
            if not armed and step_pc == trace_from:
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
    # The bank map, because "which bytes were at $8008" is unanswerable without
    # it: eight modules are all nominally at $8000 and only R6/R7 say which one
    # is on screen. Printed on every run -- it is three lines.
    print(f"MMC3 bankreg={bus.bankreg} R6=${bus.regs[6]:02X} R7=${bus.regs[7]:02X}"
          f" prg_mode={bus.prg_mode} irq_latch=${bus.irq_latch:02X}"
          f" irq_en={int(bus.irq_enabled)} scanline={bus.scanline}"
          f"  => $8000 window = slot {bus.regs[7] if bus.prg_mode else bus.regs[6]}"
          f", $A000 window = slot {bus.regs[6] if bus.prg_mode else bus.regs[7]}"
          + (f"; mode last set by ${bus.prg_mode_from[1]:04X} writing "
             f"${bus.prg_mode_from[2]:02X} to ${bus.prg_mode_from[0]:04X}"
             if bus.prg_mode_from else "; mode never changed"))
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

    # ------------------------------------------------------------- RAM writes
    #
    # The report groups by PC, because "which instruction writes this cell" is
    # the question and a list of every write is 400 lines of the same address.
    # It prints the frame of the FIRST and LAST write as well as the count, and
    # it counts writes outside the window too -- a filter that hides how much it
    # hid is how you end up believing a cell was written once when it was
    # written four thousand times.
    for addr in sorted(bus.ram_writes):
        rows = bus.ram_writes[addr]
        name = ram_watch.get(addr, f"${addr:04X}")
        bypc: collections.Counter = collections.Counter()
        for _f, _c, pc, _v in rows:
            bypc[pc] += 1
        inwin = [r for r in rows if r[0] >= args.ram_watch_window]
        print(f"\nRAM ${addr:04X} ({name}): {len(rows)} write(s) over "
              f"{bus.frame} frames, {len(inwin)} at frame >= "
              f"{args.ram_watch_window}")
        for pc, n in bypc.most_common(12):
            ex = [(f, v) for f, _c, p2, v in rows if p2 == pc][:6]
            vals = ", ".join(f"f{f}=${v:02X}" for f, v in ex)
            sym = next((s for s, a in syms.items() if a == pc), "")
            print(f"  ${pc:04X}{' ' + sym if sym else '':<12} x{n:<7} {vals}")
        if not rows:
            print("  never written -- check the address and whether this cell is "
                  "reached at all before concluding anything about it")
    if log_masks and bus.frame < len(log_masks):
        print(f"\nWARNING: the input log holds {len(log_masks)} frames but the run "
              f"ended after {bus.frame}. {len(log_masks) - bus.frame} frames were "
              f"not replayed, so this is not the whole logged route.")
    if log_masks:
        print(f"controller: {bus.pad_reads} reads of $4016 across {bus.frame} "
              f"frames ({bus.pad_reads / max(bus.frame, 1):.1f} per frame)")
    for k in sorted(watch):
        if k[0] != "w":
            continue
        rows = bus.reg_writes.get(k[1], [])
        print(f"\nPPU register $200{k[1]} writes: {len(rows)}")
        bypc: collections.Counter = collections.Counter()
        for _, pc, _, _ in rows:
            bypc[pc] += 1
        for pc, n in bypc.most_common(8):
            ex = [f"${va:04X}=${v:02X}" for _, p2, va, v in rows if p2 == pc][:6]
            print(f"  ${pc:04X} x{n:<7} e.g. " + ", ".join(ex))
        pal = [(pc, va, v) for _, pc, va, v in rows if 0x3F00 <= va < 0x3F20 and v != 0x0F]
        print(f"  non-$0F writes into $3F00-$3F1F: {len(pal)}"
              + ("  e.g. " + ", ".join(f"${pc:04X}->${va:04X}=${v:02X}"
                                       for pc, va, v in pal[:8]) if pal else ""))
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
    # Printed whenever --last was asked for, not only on a Halt: a run that
    # simply runs out of cycles is exactly the case where the last few
    # instructions are the interesting ones.
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
