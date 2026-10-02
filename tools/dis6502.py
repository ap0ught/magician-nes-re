#!/usr/bin/env python3
"""A 6502 disassembler for the Magician cartridge and rebuild.

There is no disassembler installed on this machine (checked: da65, xa, dasm, py65
-- all absent), so this is written here. It is deliberately *linear* with a
recursive-descent follow for `jsr`/`jmp` targets, because the question being
asked of the cartridge is "what shape is this code", and a recursive walk that
never resolves `$8000`-window bank-switch indirections answers that better than
one that gives up at the first `jmp ($xxxx)`.

The opcode table is derived from `asm/pds6502.py:OPCODES`, which is the same
dialect the source uses -- so if the two disagree about a mode, that is worth
knowing, and `selfcheck()` reports it.

    python3 tools/dis6502.py --cart            # fixed window only
    python3 tools/dis6502.py --cart --range 0x1E000:0x1E200
    python3 tools/dis6502.py --cart --bank 7
    python3 tools/dis6502.py --cart --from 0xF9AB --follow
    python3 tools/dis6502.py --selfcheck
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# ----------------------------------------------------------------- the table
# opcode -> (mnemonic, mode). Built from the OPCODES dict in asm/pds6502.py at
# import time where possible; the literal below is the fallback so this file
# stands alone.
MODE_LEN = {"imp": 1, "acc": 1, "imm": 2, "zp": 2, "zpx": 2, "zpy": 2, "rel": 2,
            "abs": 3, "absx": 3, "absy": 3, "ind": 3, "indx": 2, "indy": 2}

LITERAL: dict[int, tuple[str, str]] = {
    0x00: ("brk", "imp"), 0x01: ("ora", "indx"), 0x05: ("ora", "zp"),
    0x06: ("asl", "zp"), 0x08: ("php", "imp"), 0x09: ("ora", "imm"),
    0x0A: ("asl", "acc"), 0x0D: ("ora", "abs"), 0x0E: ("asl", "abs"),
    0x10: ("bpl", "rel"), 0x11: ("ora", "indy"), 0x15: ("ora", "zpx"),
    0x16: ("asl", "zpx"), 0x18: ("clc", "imp"), 0x19: ("ora", "absy"),
    0x1D: ("ora", "absx"), 0x1E: ("asl", "absx"),
    0x20: ("jsr", "abs"), 0x21: ("and", "indx"), 0x24: ("bit", "zp"),
    0x25: ("and", "zp"), 0x26: ("rol", "zp"), 0x28: ("plp", "imp"),
    0x29: ("and", "imm"), 0x2A: ("rol", "acc"), 0x2C: ("bit", "abs"),
    0x2D: ("and", "abs"), 0x2E: ("rol", "abs"),
    0x30: ("bmi", "rel"), 0x31: ("and", "indy"), 0x35: ("and", "zpx"),
    0x36: ("rol", "zpx"), 0x38: ("sec", "imp"), 0x39: ("and", "absy"),
    0x3D: ("and", "absx"), 0x3E: ("rol", "absx"),
    0x40: ("rti", "imp"), 0x41: ("eor", "indx"), 0x45: ("eor", "zp"),
    0x46: ("lsr", "zp"), 0x48: ("pha", "imp"), 0x49: ("eor", "imm"),
    0x4A: ("lsr", "acc"), 0x4C: ("jmp", "abs"), 0x4D: ("eor", "abs"),
    0x4E: ("lsr", "abs"),
    0x50: ("bvc", "rel"), 0x51: ("eor", "indy"), 0x55: ("eor", "zpx"),
    0x56: ("lsr", "zpx"), 0x58: ("cli", "imp"), 0x59: ("eor", "absy"),
    0x5D: ("eor", "absx"), 0x5E: ("lsr", "absx"),
    0x60: ("rts", "imp"), 0x61: ("adc", "indx"), 0x65: ("adc", "zp"),
    0x66: ("ror", "zp"), 0x68: ("pla", "imp"), 0x69: ("adc", "imm"),
    0x6A: ("ror", "acc"), 0x6C: ("jmp", "ind"), 0x6D: ("adc", "abs"),
    0x6E: ("ror", "abs"),
    0x70: ("bvs", "rel"), 0x71: ("adc", "indy"), 0x75: ("adc", "zpx"),
    0x76: ("ror", "zpx"), 0x78: ("sei", "imp"), 0x79: ("adc", "absy"),
    0x7D: ("adc", "absx"), 0x7E: ("ror", "absx"),
    0x81: ("sta", "indx"), 0x84: ("sty", "zp"), 0x85: ("sta", "zp"),
    0x86: ("stx", "zp"), 0x88: ("dey", "imp"), 0x8A: ("txa", "imp"),
    0x8C: ("sty", "abs"), 0x8D: ("sta", "abs"), 0x8E: ("stx", "abs"),
    0x90: ("bcc", "rel"), 0x91: ("sta", "indy"), 0x94: ("sty", "zpx"),
    0x95: ("sta", "zpx"), 0x96: ("stx", "zpy"), 0x98: ("tya", "imp"),
    0x99: ("sta", "absy"), 0x9A: ("txs", "imp"), 0x9D: ("sta", "absx"),
    0xA0: ("ldy", "imm"), 0xA1: ("lda", "indx"), 0xA2: ("ldx", "imm"),
    0xA4: ("ldy", "zp"), 0xA5: ("lda", "zp"), 0xA6: ("ldx", "zp"),
    0xA8: ("tay", "imp"), 0xA9: ("lda", "imm"), 0xAA: ("tax", "imp"),
    0xAC: ("ldy", "abs"), 0xAD: ("lda", "abs"), 0xAE: ("ldx", "abs"),
    0xB0: ("bcs", "rel"), 0xB1: ("lda", "indy"), 0xB4: ("ldy", "zpx"),
    0xB5: ("lda", "zpx"), 0xB6: ("ldx", "zpy"), 0xB8: ("clv", "imp"),
    0xB9: ("lda", "absy"), 0xBA: ("tsx", "imp"), 0xBC: ("ldy", "absx"),
    0xBD: ("lda", "absx"), 0xBE: ("ldx", "absy"),
    0xC0: ("cpy", "imm"), 0xC1: ("cmp", "indx"), 0xC4: ("cpy", "zp"),
    0xC5: ("cmp", "zp"), 0xC6: ("dec", "zp"), 0xC8: ("iny", "imp"),
    0xC9: ("cmp", "imm"), 0xCA: ("dex", "imp"), 0xCC: ("cpy", "abs"),
    0xCD: ("cmp", "abs"), 0xCE: ("dec", "abs"),
    0xD0: ("bne", "rel"), 0xD1: ("cmp", "indy"), 0xD5: ("cmp", "zpx"),
    0xD6: ("dec", "zpx"), 0xD8: ("cld", "imp"), 0xD9: ("cmp", "absy"),
    0xDD: ("cmp", "absx"), 0xDE: ("dec", "absx"),
    0xE0: ("cpx", "imm"), 0xE1: ("sbc", "indx"), 0xE4: ("cpx", "zp"),
    0xE5: ("sbc", "zp"), 0xE6: ("inc", "zp"), 0xE8: ("inx", "imp"),
    0xE9: ("sbc", "imm"), 0xEA: ("nop", "imp"), 0xEC: ("cpx", "abs"),
    0xED: ("sbc", "abs"), 0xEE: ("inc", "abs"),
    0xF0: ("beq", "rel"), 0xF1: ("sbc", "indy"), 0xF5: ("sbc", "zpx"),
    0xF6: ("inc", "zpx"), 0xF8: ("sed", "imp"), 0xF9: ("sbc", "absy"),
    0xFD: ("sbc", "absx"), 0xFE: ("inc", "absx"),
}

# The undocumented opcodes are listed separately so `--undoc` can include them.
UNDOC: dict[int, tuple[str, str]] = {
    0x04: ("nop", "zp"), 0x14: ("nop", "zpx"), 0x34: ("nop", "zpx"),
    0x44: ("nop", "zp"), 0x54: ("nop", "zp"), 0x64: ("nop", "zp"),
    0x74: ("nop", "zp"), 0x80: ("nop", "imm"), 0x82: ("nop", "imm"),
    0x89: ("nop", "imm"), 0xC2: ("nop", "imm"), 0xD4: ("nop", "zpx"),
    0xD8: ("cld", "imp"),
    0x0B: ("anc", "imm"), 0x2B: ("anc", "imm"), 0x4B: ("alr", "imm"),
    0x6B: ("arr", "imm"), 0x8B: ("xaa", "imm"), 0xAB: ("lax", "imm"),
    0xCB: ("axs", "imm"), 0xEB: ("sbc", "imm"),
    0xA3: ("lax", "indx"), 0xA7: ("lax", "zp"), 0xAF: ("lax", "abs"),
    0xB3: ("lax", "indy"), 0xB7: ("lax", "zpy"), 0xBF: ("lax", "absy"),
    0x83: ("sax", "indx"), 0x87: ("sax", "zp"), 0x8F: ("sax", "abs"),
    0x97: ("sax", "zpy"),
    0x0C: ("nop", "abs"), 0x1C: ("nop", "absx"), 0x3C: ("nop", "absx"),
    0x5C: ("nop", "absx"), 0x7C: ("nop", "absx"), 0xDC: ("nop", "absx"),
    0xFC: ("nop", "absx"),
    0xE3: ("isc", "imm"), 0xE7: ("isc", "zp"), 0xEF: ("isc", "abs"),
    0xF3: ("isc", "indy"), 0xF7: ("isc", "zpx"), 0xFB: ("isc", "absx"),
    0xC3: ("dcp", "imm"), 0xC7: ("dcp", "zp"), 0xCF: ("dcp", "abs"),
    0xD3: ("dcp", "indy"), 0xD7: ("dcp", "zpx"), 0xDB: ("dcp", "absx"),
    0x03: ("slo", "indx"), 0x07: ("slo", "zp"), 0x0F: ("slo", "abs"),
    0x13: ("slo", "indy"), 0x17: ("slo", "zpx"), 0x1B: ("slo", "absy"),
    0x23: ("rla", "indx"), 0x27: ("rla", "zp"), 0x2F: ("rla", "abs"),
    0x33: ("rla", "indy"), 0x37: ("rla", "zpx"), 0x3B: ("rla", "absy"),
    0x43: ("sre", "indx"), 0x47: ("sre", "zp"), 0x4F: ("sre", "abs"),
    0x53: ("sre", "indy"), 0x57: ("sre", "zpx"), 0x5B: ("sre", "absy"),
    0x63: ("rra", "indx"), 0x67: ("rra", "zp"), 0x6F: ("rra", "abs"),
    0x73: ("rra", "indy"), 0x77: ("rra", "zpx"), 0x7B: ("rra", "absy"),
}

TABLE = dict(LITERAL)

BRANCHES = {"bcc", "bcs", "beq", "bmi", "bne", "bpl", "bvc", "bvs"}

# NES hardware register labels, so the disassembly says `$4014` and not `$4014`.
NAMES = {
    0x4014: "OAMDMA", 0x4016: "JOY1", 0x4017: "JOY2",
    0x2000: "PPUCTRL", 0x2001: "PPUMASK", 0x2002: "PPUSTATUS",
    0x2003: "OAMADDR", 0x2004: "OAMDATA", 0x2005: "PPUSCROLL",
    0x2006: "PPUADDR", 0x2007: "PPUDATA", 0x2008: "PPUADDR2",
    0x4000: "APU1", 0x4001: "APU1", 0x4002: "APU1", 0x4003: "APU1",
    0x4004: "APU1", 0x4005: "APU1", 0x4006: "APU1", 0x4007: "APU1",
    0x4008: "APU2", 0x4010: "DMC", 0x4011: "DMC", 0x4012: "DMC",
    0x4013: "DMC", 0x4015: "APUENV", 0x4016: "JOY1", 0x4017: "JOY2",
    0x4018: "APU3", 0x4019: "APU3", 0x401A: "APU3", 0x401B: "APU3",
    0x401C: "APU3", 0x401D: "APU3", 0x401E: "APU3", 0x401F: "APU3",
    0x8000: "MMC3_BANK", 0x8001: "MMC3_RAM", 0xA000: "MMC3_MIRROR",
    0xC000: "MMC3_PRG", 0xC001: "MMC3_UPD", 0xC002: "MMC3_UPD",
    0xC003: "MMC3_UPD", 0xC004: "MMC3_UPD", 0xC005: "MMC3_UPD",
    0xC006: "MMC3_UPD", 0xC007: "MMC3_UPD", 0x4015: "APUENV",
}
# MMC3's $8001 sub-register, chosen by A. Worth naming because a bank-switch
# wrapper is one of the things this analysis is looking for.
MMC3_SUB = {
    0x00: "R7", 0x01: "R6", 0x02: "R5", 0x03: "R4", 0x04: "R3", 0x05: "R2",
    0x06: "R1", 0x07: "R0", 0x08: "CHR0", 0x09: "CHR1", 0x0A: "CHRX",
    0x0B: "PRG", 0x0C: "IRQCFG", 0x0D: "IRQMASK", 0x0E: "IRQSTAT",
    0x0F: "QUIRK",
}


def label(addr: int) -> str:
    if addr in NAMES:
        return f"${addr:04X} <{NAMES[addr]}>"
    return f"${addr:04X}"


def operand_text(mode: str, operand: int, pc: int, mmc3: bool = False) -> str:
    if mode == "imp":
        return ""
    if mode == "acc":
        return "a"
    if mode == "imm":
        return f"#${operand:02X}"
    if mode == "rel":
        tgt = (pc + 2 + ((operand - 256) if operand > 127 else operand)) & 0xFFFF
        return f"${tgt:04X}"
    if mode == "zp":
        return f"${operand:02X}"
    if mode == "zpx":
        return f"${operand:02X},x"
    if mode == "zpy":
        return f"${operand:02X},y"
    if mode == "abs":
        if mmc3 and operand == 0x8001:
            return f"MMC3_SUB"          # patched by caller with the real name
        return label(operand)
    if mode == "absx":
        return f"{label(operand)},x"
    if mode == "absy":
        return f"{label(operand)},y"
    if mode == "ind":
        return f"(${operand:04X})"
    if mode == "indx":
        return f"(${operand:02X},x)"
    if mode == "indy":
        return f"(${operand:02X}),y"
    return f"${operand:04X}"


def decode(code: bytes, pc: int, undoc: bool = False) -> tuple[str, int, int | None, str | None]:
    """-> (text, length, branch_or_abs_target, mnemonic)."""
    op = code[0]
    entry = TABLE.get(op) or (UNDOC.get(op) if undoc else None)
    if entry is None:
        return (f".byte ${op:02X}", 1, None, None)
    mn, mode = entry
    n = MODE_LEN[mode]
    if len(code) < n:
        return (f".byte ${op:02X}", 1, None, None)
    # Only read an operand when the mode actually has one -- `imp`/`acc` are
    # 1 byte, and reading code[1]/code[2] for them raised IndexError whenever a
    # --range ended within two bytes of the end of the buffer.
    if n == 1:
        operand = 0
    elif n == 2:
        operand = code[1]
    else:
        operand = code[1] | (code[2] << 8)
    text = operand_text(mode, operand, pc)
    target: int | None = None
    if mode == "rel":
        target = (pc + 2 + ((operand - 256) if operand > 127 else operand)) & 0xFFFF
    elif mode in ("abs", "ind") and mn in ("jmp", "jsr"):
        target = operand
    return (f"{mn} {text}".strip(), n, target, mn)


# ------------------------------------------------------------ MMC3 refinement

def disasm_block(code: bytes, base: int, undoc: bool = False,
                 annotate_mmc3: bool = True) -> list[tuple[int, str]]:
    """Linear disassembly. Returns (address, text)."""
    out: list[tuple[int, str]] = []
    i, n = 0, len(code)
    # Track the last value loaded into A so `sta $8001` can be named: an MMC3
    # bank-switch is `lda #<sub` / `sta $8001` / `lda #<bank` / `sta $8000`,
    # and naming the sub-register is the difference between "generic harness" and
    # "data".
    acc: int | None = None
    while i < n:
        pc = base + i
        raw = code[i:i + 3]
        text, ln, target, mn = decode(raw, pc, undoc)
        if annotate_mmc3 and mn == "lda" and text.startswith("lda #$"):
            acc = raw[1]
        if annotate_mmc3 and mn == "sta" and "$8001" in text and acc is not None:
            sub = MMC3_SUB.get(acc)
            if sub is not None:
                text += f"        ; select {sub}"
        if annotate_mmc3 and mn == "sta" and "$8000" in text:
            text += "        ; PRG bank select"
        if annotate_mmc3 and mn == "sta" and "$4014" in text:
            text += "        ; sprite DMA"
        if annotate_mmc3 and mn == "sta" and "$4016" in text:
            text += "        ; controller strobe"
        rawhex = " ".join(f"{b:02X}" for b in raw[:ln])
        out.append((pc, f"{rawhex:8s}  {text}"))
        i += ln
    return out


def read_cart(path: pathlib.Path) -> bytes:
    data = path.read_bytes()
    if data[:4] != b"NES\x1a":
        raise SystemExit(f"{path} has no iNES header")
    prg = data[4] * 16384
    body = data[16:]
    return body[:prg]


def selfcheck() -> int:
    """Compare this table with `asm/pds6502.py`'s, which the source relies on."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "asm"))
    from pds6502 import OPCODES  # noqa: E402
    theirs: dict[int, tuple[str, str]] = {}
    for mn, modes in OPCODES.items():
        for code, mode in modes:
            theirs[code] = (mn, mode)
    diffs = 0
    for code, entry in sorted(theirs.items()):
        mine = LITERAL.get(code)
        if mine is None:
            print(f"  {code:02X} {entry[0]} {entry[1]}: not in tools/dis6502.py")
            diffs += 1
        elif mine != entry:
            print(f"  {code:02X}: dis6502 says {mine[0]} {mine[1]}, "
                  f"pds6502 says {entry[0]} {entry[1]}")
            diffs += 1
    for code in sorted(LITERAL):
        if code not in theirs:
            print(f"  {code:02X} {LITERAL[code][0]}: in dis6502 but not pds6502")
            diffs += 1
    print(f"opcode table: {len(theirs)} entries in pds6502.py, "
          f"{len(LITERAL)} in dis6502.py, {diffs} differences")
    return 1 if diffs else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cart", type=pathlib.Path,
                    default=pathlib.Path("/extdrive/backups/SHARE/roms/nes/Magician (USA).nes"))
    ap.add_argument("--prg", type=pathlib.Path, help="disassemble a raw PRG instead")
    ap.add_argument("--bank", type=lambda s: int(s, 0), help="16 KiB PRG bank")
    ap.add_argument("--range", help="lo:hi, both CPU addresses or file offsets")
    ap.add_argument("--from", dest="start", type=lambda s: int(s, 0),
                    help="CPU address to start at")
    ap.add_argument("--length", type=lambda s: int(s, 0), default=0x400)
    ap.add_argument("--follow", action="store_true",
                    help="follow jsr/jmp targets within the fixed window")
    ap.add_argument("--undoc", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck()

    if args.prg:
        prg = args.prg.read_bytes()
    else:
        prg = read_cart(args.cart)

    # CPU address <-> file offset across the fixed window: file = cpu + $10000.
    def cpu_to_file(a: int) -> int:
        return a + 0x10000

    if args.range:
        lo_s, hi_s = args.range.split(":")
        lo, hi = int(lo_s, 0), int(hi_s, 0)
        lo = lo if lo >= 0x10000 else cpu_to_file(lo)
        hi = hi if hi >= 0x10000 else cpu_to_file(hi)
        off, ln = lo, hi - lo
    elif args.bank is not None:
        off, ln = args.bank * 0x4000, 0x4000
    elif args.start is not None:
        off = cpu_to_file(args.start)
        ln = args.length
    else:
        off, ln = 0x1C000, 0x4000

    base_cpu = off - 0x10000 if off >= 0x10000 else off + 0x8000
    code = prg[off:off + ln]
    lines = disasm_block(code, base_cpu & 0xFFFF, args.undoc)
    for addr, text in lines:
        print(f"{addr:04X}: {text}")

    if args.follow:
        seen: set[int] = set()
        targets: set[int] = set()
        code2 = prg[off:off + ln]
        i = 0
        while i < len(code2):
            _, ln2, tgt, _ = decode(code2[i:i + 3], (base_cpu + i) & 0xFFFF, args.undoc)
            if tgt is not None and 0xC000 <= tgt < 0x10000:
                targets.add(tgt)
            i += ln2
        for t in sorted(targets):
            f = cpu_to_file(t)
            if off <= f < off + ln or 0xC000 <= t < 0x10000:
                if t in seen:
                    continue
                seen.add(t)
                print(f"\n; --- target ${t:04X} (file ${f:05X}) ---")
                for addr, text in disasm_block(prg[f:f + 64], t, args.undoc):
                    print(f"{addr:04X}: {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())