#!/usr/bin/env python3
"""Align the cartridge's code with the rebuild's, instruction by instruction.

The question this answers is the one a byte-match percentage cannot: *where* do
the two images stop agreeing, and is the disagreement an insertion, a deletion,
or different data? Three sessions of percentage-watching produced no fix,
because 30.4% and 30.6% look identical when the real cause is three bytes in a
trampoline.

Decoding is `tools/dis6502.py`'s, which cross-checks its opcode table against
`asm/pds6502.py:OPCODES` -- one disassembler, not two.

Both images are disassembled *linearly* from a chosen anchor. That is
deliberate: the code being compared is reached by `jmp ($0009)` and `jsr`, not
by fall-through, and a recursive walk would need to resolve MMC3 bank-switch
indirections to know where it is going. A linear sweep from a hand-picked
anchor is enough to align two versions of the same routine.

    # the nmi/irq/reset trampolines, anchored on the cartridge's nmi vector
    python3 tools/align6502.py --from 0xF94F --length 0x80

    # nmi0 in the $C000 window
    python3 tools/align6502.py --from 0xDD01 --length 0x180

    # one image aligned against itself, as a control
    python3 tools/align6502.py --from 0xF94F --length 0x80 --anchor cart

## Reading the output

    ==  identical bytes
    ~~  same opcode and mode, different operand -- almost always a branch or
        absolute address that moved because something before it changed length
    -   present only in the cartridge  (an INSERTION the release has)
    +   present only in the rebuild    (a DELETION the source has that the
                                       release dropped)
    ?   opcode differs: the two really are different code

The summary at the end prints every insertion and deletion with its byte count,
which is the number to argue about. A three-byte insertion that shifts every
branch target after it explains a scrambled nametable and a match percentage
that moves by a third of a point.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dis6502  # noqa: E402


# --------------------------------------------------------------- image input

def cpu_to_file(addr: int) -> int:
    """CPU address -> PRG file offset, across the fixed window.

    A 128 KiB PRG (8 x 16 KiB slots, or 4 x 32 KiB on MMC3) is presented with
    $E000-$FFFF fixed to the last 8 KiB, so CPU $F9AB is file $1F9AB and *not*
    $F9AB. Getting this wrong is how an address resolves against the wrong
    image.
    """
    if not 0x8000 <= addr <= 0xFFFF:
        raise SystemExit(f"{addr:#06x} is not a CPU address this mapper presents")
    return addr + 0x10000


def load(path: pathlib.Path) -> bytes:
    if path.suffix.lower() == ".bin":
        return path.read_bytes()
    return dis6502.read_cart(path)


def sweep(prg: bytes, start_cpu: int, length: int, undoc: bool):
    """-> [(cpu_addr, raw_bytes, text, mnemonic, mode)]"""
    off = cpu_to_file(start_cpu)
    code = prg[off:off + length]
    out = []
    for addr, text in dis6502.disasm_block(code, start_cpu, undoc):
        _, ln, _tgt, mn = dis6502.decode(code[addr - start_cpu:addr - start_cpu + 3],
                                         addr, undoc)
        mode = (dis6502.TABLE.get(code[addr - start_cpu])
                or (dis6502.UNDOC.get(code[addr - start_cpu]) if undoc else None))
        raw = code[addr - start_cpu:addr - start_cpu + ln]
        out.append((addr, raw, text, mn, mode[1] if mode else None))
    return out


# ------------------------------------------------------------ the scoring

def score(a, b) -> int:
    """Similarity of two decoded instructions. Higher is more alike.

    Byte identity dominates, because that is the only comparison that cannot be
    fooled: `sta $2000` at the same address in both images is real agreement.
    Same opcode with a different operand is weak agreement -- a branch target
    or an absolute pointer that moved. Everything else is disagreement.
    """
    if a[1] == b[1]:
        return 6
    if a[3] is None or b[3] is None:
        return -3 if a[3] != b[3] else 0
    if a[3] == b[3]:
        if a[4] == b[4]:
            return 2           # same mnemonic and mode, different operand
        return -1              # same mnemonic, different mode
    return -4


# --------------------------------------------------------------- the aligner

def align(A, B, gap: int = -3):
    """Needleman-Wunsch over instruction indices. -> [(ia|None, ib|None)]"""
    n, m = len(A), len(B)
    # score matrix, row 0 = A consumed, col 0 = B consumed
    S = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        S[i][0] = S[i - 1][0] + gap
    for j in range(1, m + 1):
        S[0][j] = S[0][j - 1] + gap
    for i in range(1, n + 1):
        Ai = A[i - 1]
        Si, Sp = S[i], S[i - 1]
        for j in range(1, m + 1):
            d = Sp[j - 1] + score(Ai, B[j - 1])
            u = Sp[j] + gap
            l = Si[j - 1] + gap
            Si[j] = d if d >= u and d >= l else (u if u >= l else l)
    # traceback
    path = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and S[i][j] == S[i - 1][j - 1] + score(A[i - 1], B[j - 1]):
            path.append((i - 1, j - 1)); i -= 1; j -= 1
        elif i > 0 and S[i][j] == S[i - 1][j] + gap:
            path.append((i - 1, None)); i -= 1
        else:
            path.append((None, j - 1)); j -= 1
    path.reverse()
    return path


def fmt(ins, width: int = 30) -> str:
    if ins is None:
        return " " * width
    return f"{ins[2]:<{width}}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cart", type=pathlib.Path,
                    default=pathlib.Path("/extdrive/backups/SHARE/roms/nes/Magician (USA).nes"),
                    help="the released cartridge (read-only)")
    ap.add_argument("--prg", type=pathlib.Path,
                    default=pathlib.Path("asm/out/magician-rebuilt.nes"),
                    help="the rebuilt image")
    ap.add_argument("--from", dest="start", type=lambda s: int(s, 0),
                    required=True, help="CPU address both images are swept from")
    ap.add_argument("--length", type=lambda s: int(s, 0), default=0x100)
    ap.add_argument("--anchor", choices=("cart", "prg"), default="cart",
                    help="which image's address the sweep starts at in both. "
                         "'cart' is the default because the cartridge's vector "
                         "table is ground truth; 'prg' aligns the rebuild's own "
                         "labels.")
    ap.add_argument("--undoc", action="store_true")
    ap.add_argument("--sym", type=pathlib.Path, default=pathlib.Path("asm/out/mag.sym"),
                    help="label file, used to annotate the rebuild's addresses")
    args = ap.parse_args()

    cart, prg = load(args.cart), load(args.prg)

    start = args.start
    A = sweep(cart, start, args.length, args.undoc)   # cartridge
    B = sweep(prg, start, args.length, args.undoc)    # rebuild

    sym = {}
    if args.sym.exists():
        for line in args.sym.read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                try:
                    sym[int(v.strip().lstrip("$"), 16)] = k.strip()
                except ValueError:
                    pass

    def tag(addr):
        n = sym.get(addr)
        return f" <{n}>" if n else ""

    print(f"# cartridge vs rebuild, swept from ${start:04X}, "
          f"{args.length:#x} bytes each\n")
    print(f"{'cart':<38} {'prg':<38}")
    print(f"{'-'*38} {'-'*38}")

    path = align(A, B)
    for ia, ib in path:
        a = A[ia] if ia is not None else None
        b = B[ib] if ib is not None else None
        if a is not None and b is not None:
            if a[1] == b[1]:
                mark, why = "==", "identical"
            elif a[3] == b[3] and a[4] == b[4]:
                mark, why = "~~", "operand moved"
            else:
                mark, why = "??", "different code"
            row = f"{mark} {a[0]:04X}: {fmt(a)} | {b[0]:04X}: {fmt(b)}{tag(b[0])}"
            print(f"{row}   ; {why}")
        elif a is not None:
            print(f"-  {a[0]:04X}: {fmt(a)}{tag(a[0])}"
                  f"   ; INSERTION, cartridge only")
        else:
            assert b is not None
            print(f"+  {'':30} | {b[0]:04X}: {fmt(b)}{tag(b[0])}"
                  f"   ; DELETION, source only")

    # ------------------------------------------------------ byte accounting
    print()
    same = sum(1 for ia, ib in path
               if ia is not None and ib is not None and A[ia][1] == B[ib][1])
    moved = sum(1 for ia, ib in path
                if ia is not None and ib is not None and A[ia][1] != B[ib][1])
    ins_only = [x for x in path if x[0] is not None and x[1] is None]
    del_only = [x for x in path if x[0] is None and x[1] is not None]
    ins_bytes = sum(len(A[ia][1]) for ia, _ in ins_only)
    del_bytes = sum(len(B[ib][1]) for _, ib in del_only)

    print(f"instructions aligned   : {sum(1 for p in path if p[0] is not None and p[1] is not None)}")
    print(f"  byte-identical       : {same}")
    print(f"  operand moved only   : {moved}")
    print(f"insertions (cart only) : {len(ins_only)} instructions, {ins_bytes} bytes")
    print(f"deletions  (src  only) : {len(del_only)} instructions, {del_bytes} bytes")
    print(f"net size difference    : {ins_bytes - del_bytes:+d} bytes "
          f"(release {'longer' if ins_bytes > del_bytes else 'shorter'})")

    if ins_only:
        print("\ninsertions, cartridge only:")
        for ia, _ in ins_only:
            print(f"  ${A[ia][0]:04X}  {A[ia][1].hex(' '):<9} {A[ia][2]}")
    if del_only:
        print("\ndeletions, source only:")
        for _, ib in del_only:
            print(f"  ${B[ib][0]:04X}  {B[ib][1].hex(' '):<9} {B[ib][2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())