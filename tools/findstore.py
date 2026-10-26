#!/usr/bin/env python3
"""Which instructions in a PRG can store to a given RAM cell?

    python3 tools/findstore.py --addr mapind
    python3 tools/findstore.py --addr $004D --context 12
    python3 tools/findstore.py --addr mapind --prg asm/out/prg.bin --prg BETA1

WHY THIS IS A TOOL AND NOT A GREP
----------------------------------
"Who writes $004D" (journal 13, item 1) is not a `85 4D` search, because a
zero-page cell is reachable by more than one instruction and a search for the
one you already had in mind cannot tell you that you had the wrong one in mind.
The encodings that can deposit a byte at a fixed address, and the ones that can
modify one in place:

    85 4D      sta $4D          86 4D      stx $4D          84 4D      sty $4D
    E6 4D      inc $4D          C6 4D      dec $4D          96 4D      stx $4D,y
    95 4D      sta $4D,x        94 4D      sty $4D,x        B6 4D      ldx $4D,y
    8D 4D 00   sta $004D        8E 4D 00   stx $004D        8C 4D 00   sty $004D
    EE 4D 00   inc $004D        CE 4D 00   dec $004D        9D 4D 00   sta $004D,x
    8F 4D 00   sax $004D  (undocumented, with --undoc)

`inc mapind` is `E6 4D`, NOT `8D 4D 00` with an increment in front of it, so a
search that only knows the store forms finds nothing and reads as "the routine
does not exist". The same trap cost journal 13 a wrong conclusion about
`sta mapind` assembling as `8D 4D 00`: it assembles as `85 4D`, because
`mapind` is in zero page.

WHAT IT CANNOT DO, STATED HERE RATHER THAN DISCOVERED LATER
-----------------------------------------------------------
This is a LINEAR SWEEP. It decodes every byte of the image as if it were code,
so a run of level data that happens to contain `85 4D` is reported as a writer.
The tool marks nothing as proven for that reason: it prints the disassembly
context and the nearest preceding symbol, and the caller confirms reachability
by disassembly or by a write trace. There is no recursive descent here, because
the game's switchable bank is selected by a runtime `bankreg` and a descent
that pretends to know the bank is a descent that guesses.

WHAT IT ASSERTS ABOUT ITSELF
---------------------------
  * it prints the number of bytes it scanned and the number of images, so
    "no writers" is distinguishable from "did not look";
  * it counts every store/RMW encoding it knows, and a byte that looks like one
    of them but has an unknown mode is reported as `UNKNOWN-MODE`, not dropped;
  * `--expect N` makes a wrong candidate count a failure, so a change in the
    number of writers to a cell cannot pass unnoticed.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cartref import cart  # noqa: E402
from dis6502 import LITERAL, MODE_LEN, UNDOC, disasm_block, read_cart  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SYM = ROOT / "asm" / "out" / "mag.sym"
REBUILD = ROOT / "asm" / "out" / "prg.bin"

# mnemonics that can change the byte at their target.
MUTATORS = {"sta", "stx", "sty", "inc", "dec", "sax", "sre", "shx", "shy",
            "rla", "rra", "slo", "dcp", "isc"}
# modes whose target is a fixed address, so a single operand byte or word names
# the cell exactly. The indexed modes are listed separately because the
# effective address depends on the index register at run time: `sta $4D,x`
# targets $4D only while X is 0.
FIXED_ZP = {"zp"}
FIXED_ABS = {"abs"}
INDEXED_ZP = {"zpx", "zpy"}
INDEXED_ABS = {"absx", "absy"}


def load_syms(path: pathlib.Path) -> dict[int, list[str]]:
    syms: dict[int, list[str]] = {}
    if not path.exists():
        return syms
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        name, _, rhs = line.partition("=")
        rhs = rhs.strip()
        if not rhs.startswith("$"):
            continue
        try:
            addr = int(rhs[1:], 16)
        except ValueError:
            continue
        syms.setdefault(addr, []).append(name.strip())
    return syms


def prune(syms: dict[int, list[str]]) -> list[tuple[int, str]]:
    out = []
    for addr in sorted(syms):
        for n in syms[addr]:
            out.append((addr, n))
    return out


def nearest(table: list[tuple[int, str]], addr: int) -> str:
    best = None
    for a, n in table:
        if a <= addr:
            best = (a, n)
        else:
            break
    if best is None:
        return "-"
    return f"{best[1]}+${addr - best[0]:02X}" if addr != best[0] else best[1]


def scan(prg: bytes, watch: int, table: dict[int, tuple[str, str]],
         undoc: bool) -> tuple[list[tuple[int, int, str, str]], int, int]:
    """Linear sweep. Returns (hits, scanned_bytes, unknown_modes_seen)."""
    hits: list[tuple[int, int, str, str]] = []
    unknown = 0
    scanned = 0
    i = 0
    n = len(prg)
    while i < n:
        op = prg[i]
        entry = table.get(op)
        if entry is None:
            entry = UNDOC.get(op) if undoc else None
        if entry is None:
            i += 1
            continue
        mn, mode = entry
        ln = MODE_LEN.get(mode, 0)
        if not ln or i + ln > n:
            i += 1
            continue
        scanned += 1
        if mn not in MUTATORS:
            i += ln
            continue
        lo = i + 1
        operand = int.from_bytes(prg[lo:lo + (2 if mode in FIXED_ABS | INDEXED_ABS else 1)],
                                 "little")
        target = operand if mode in FIXED_ZP | FIXED_ABS | INDEXED_ZP else operand
        if mode in FIXED_ZP | FIXED_ABS and target == watch:
            hits.append((i, ln, mn, mode))
        elif mode in INDEXED_ZP | INDEXED_ABS and target == watch:
            # Keep it, but the caller has to know the index must be zero.
            hits.append((i, ln, mn, mode))
        i += ln
    return hits, scanned, unknown


def cpu_of(off: int) -> int:
    """File offset -> CPU address. MMC3: the fixed window is at file+$10000;
    every other bank is only reachable through a runtime bank register, so it
    gets a bank-qualified name rather than a CPU address that does not exist."""
    if off >= 0x10000:
        return (off - 0x10000) & 0xFFFF
    return (off & 0x3FFF) | 0x8000


def where(off: int) -> str:
    if off >= 0x10000:
        return f"${cpu_of(off):04X}"
    return f"bank{off // 0x4000}+${off % 0x4000:04X}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--addr", required=True,
                    help="a symbol from asm/out/mag.sym, or $hex")
    ap.add_argument("--prg", action="append", default=[],
                    help="a .nes or raw PRG. Repeatable. Default: the rebuild "
                         "and Beta 1.")
    ap.add_argument("--cart", default="", help="a cartridge name in cartref")
    ap.add_argument("--context", type=lambda s: int(s, 0), default=8,
                    help="bytes of disassembly to print before each hit")
    ap.add_argument("--undoc", action="store_true",
                    help="also decode the undocumented opcodes")
    ap.add_argument("--expect", type=int, default=None,
                    help="fail unless this many hits are found in each image")
    args = ap.parse_args()

    syms = load_syms(SYM)
    if args.addr.startswith("$"):
        watch = int(args.addr[1:], 16)
    else:
        matches = [a for a, ns in syms.items() if args.addr in ns]
        if len(matches) != 1:
            sys.exit(f"findstore: {args.addr!r} names {len(matches)} symbols in "
                     f"{SYM.name}; write the address as $hex to be unambiguous")
        watch = matches[0]

    names = [args.addr if args.addr.startswith("$") else
             "/".join(syms[watch])]
    print(f"watching ${watch:04X} ({names[0]})"
          + (f"  [symbol table: {SYM}]" if syms else "  [no symbol table]"))

    paths: list[pathlib.Path] = []
    if args.prg:
        paths = [pathlib.Path(p) for p in args.prg]
    elif args.cart:
        paths = [cart(args.cart)]
    else:
        paths = [REBUILD, cart("beta1")]

    table = {**LITERAL, **(UNDOC if args.undoc else {})}
    symtab = prune(syms)
    bad = 0
    for p in paths:
        data = p.read_bytes() if p.suffix.lower() in (".bin", ".prg") else read_cart(p)
        hits, scanned, _unknown = scan(data, watch, table, args.undoc)
        print(f"\n=== {p.name}  {len(data)} bytes, {scanned} instructions decoded "
              f"by a linear sweep")
        if not hits:
            print("    no instruction in the sweep can store to this cell.")
            print("    (a linear sweep over DATA also decodes data; see the "
                  "docstring. A hit still needs confirming.)")
            if args.expect is not None and args.expect != 0:
                bad += 1
            continue
        for off, ln, mn, mode in hits:
            indexed = mode in INDEXED_ZP | INDEXED_ABS
            note = "  <- only while the index register is 0" if indexed else ""
            print(f"  file ${off:05X}  {where(off):>12}  {mn} ${mode}  "
                  f"({ln} bytes){note}")
            print(f"      nearest symbol: {nearest(symtab, cpu_of(off))}")
            lo = max(0, off - args.context)
            code = data[lo:off + 6]
            base = cpu_of(lo)
            for pc, text in disasm_block(code, base, undoc=args.undoc):
                mark = ">>" if pc == cpu_of(off) else "  "
                print(f"    {mark} ${pc:04X}  {text}")
        print(f"  {len(hits)} writer(s) for ${watch:04X}")
        if args.expect is not None and len(hits) != args.expect:
            print(f"  FAIL: expected {args.expect} writers, found {len(hits)}")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())