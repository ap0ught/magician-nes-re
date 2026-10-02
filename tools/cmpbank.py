#!/usr/bin/env python3
"""Compare the rebuilt PRG against the cartridge (and the Beta), per bank.

Three questions, three subcommands:

    bank   per 16 KiB bank and per 4 KiB page: match counts, non-zero counts,
           and the *contiguous runs in the cartridge that our build does not
           account for* -- the long unexplained runs are the candidate missing
           code, so they are what this prints.
    diff   the release against the Beta, per bank and per page, so we can tell
           which dump the source is closer to.
    runs   the same run finder, restricted to a range, with disassembly.

A "run" here means: the cartridge has non-zero bytes where our build has zero,
or where it has a *different* non-zero byte, and that condition is held
contiguously. Zero-versus-zero is not a run -- both being empty is agreement.
Different-versus-different is reported too but flagged, because a moved label
looks exactly like missing code byte-wise and only disassembly tells them apart.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from dis6502 import disasm_block, read_cart  # noqa: E402

ROMDIR = pathlib.Path("/extdrive/backups/SHARE/roms/nes")
RELEASE = ROMDIR / "Magician (USA).nes"
BETA = ROMDIR / "Magician (USA) (Beta).nes"
REBUILD = pathlib.Path(__file__).resolve().parents[1] / "asm" / "out" / "prg.bin"

PRG_SIZE = 128 * 1024


def nz(data: bytes) -> int:
    return sum(1 for b in data if b)


def match(a: bytes, b: bytes) -> int:
    return sum(1 for x, y in zip(a, b) if x == y)


def kind(cart: bytes, ours: bytes, i: int) -> str:
    c, o = cart[i], ours[i]
    if c == o:
        return ""
    if o == 0 and c != 0:
        return "missing"          # cart has code, we emit nothing
    if c == 0 and o != 0:
        return "extra"            # we emit where the cart is empty
    return "differ"               # both non-zero, different


def runs(cart: bytes, ours: bytes, lo: int, hi: int,
         wanted: tuple[str, ...] = ("missing",)) -> list[tuple[int, int, str]]:
    """Contiguous [start, end) spans of one condition, within [lo, hi)."""
    out: list[tuple[int, int, str]] = []
    i = lo
    while i < hi:
        k = kind(cart, ours, i)
        if k not in wanted:
            i += 1
            continue
        j = i + 1
        while j < hi and kind(cart, ours, j) == k:
            j += 1
        out.append((i, j, k))
        i = j
    return out


def cpu(off: int) -> int:
    """File offset -> CPU address, for the fixed window. Meaningless elsewhere."""
    return (off - 0x10000) & 0xFFFF


def label(off: int) -> str:
    c = cpu(off)
    if 0xC000 <= c < 0x10000:
        return f"${c:04X}/file ${off:05X}"
    if 0x8000 <= c < 0xC000:
        return f"bank{off // 0x4000} slot{off // 0x2000} +${off % 0x2000:04X}"
    return f"file ${off:05X}"


# --------------------------------------------------------------- subcommands

def cmd_bank(args):
    cart = read_cart(args.cart)
    ours = args.prg.read_bytes() if args.prg else bytes(PRG_SIZE)
    print(f"cart      {args.cart}")
    print(f"non-zero  cart {nz(cart)}   rebuild {nz(ours)}   "
          f"delta {nz(cart) - nz(ours)}")
    print(f"identical {match(cart, ours)} / {PRG_SIZE} "
          f"({100.0 * match(cart, ours) / PRG_SIZE:.2f}%)\n")

    print("per 16 KiB bank")
    print(f"{'bank':>4} {'file':>8} {'match%':>7} {'cartNZ':>7} {'ourNZ':>7} "
          f"{'missing':>8} {'differ':>7} {'extra':>6} {'maxRun':>7} {'@':>9}")
    tot = {"m": 0, "d": 0, "e": 0}
    for b in range(8):
        lo, hi = b * 0x4000, (b + 1) * 0x4000
        c, o = cart[lo:hi], ours[lo:hi]
        m = match(c, o)
        rs = runs(cart, ours, lo, hi, ("missing",))
        ds = runs(cart, ours, lo, hi, ("differ",))
        es = runs(cart, ours, lo, hi, ("extra",))
        big = max(rs, key=lambda r: r[1] - r[0], default=(0, 0, ""))
        tot["m"] += sum(hi2 - lo2 for lo2, hi2, _ in rs)
        tot["d"] += sum(hi2 - lo2 for lo2, hi2, _ in ds)
        tot["e"] += sum(hi2 - lo2 for lo2, hi2, _ in es)
        print(f"{b:>4} {lo:>#8x} {100.0 * m / 0x4000:>6.2f}% {nz(c):>7} {nz(o):>7} "
              f"{sum(y - x for x, y, _ in rs):>8} "
              f"{sum(y - x for x, y, _ in ds):>7} "
              f"{sum(y - x for x, y, _ in es):>6} "
              f"{big[1] - big[0]:>7} {label(big[0]) if big[2] else '-':>9}")
    print(f"{'':>4} {'':>8} {'':>7} {'':>7} {'':>7} {tot['m']:>8} {tot['d']:>7} "
          f"{tot['e']:>6}")

    print("\nper 4 KiB page")
    print(f"{'page':>4} {'file':>8} {'match%':>7} {'cartNZ':>7} {'ourNZ':>7} "
          f"{'missing':>8} {'maxRun':>7}")
    for p in range(32):
        lo, hi = p * 0x1000, (p + 1) * 0x1000
        c, o = cart[lo:hi], ours[lo:hi]
        rs = runs(cart, ours, lo, hi, ("missing",))
        big = max(rs, key=lambda r: r[1] - r[0], default=(0, 0, ""))
        print(f"{p:>4} {lo:>#8x} {100.0 * match(c, o) / 0x1000:>6.2f}% {nz(c):>7} "
              f"{nz(o):>7} {sum(y - x for x, y, _ in rs):>8} "
              f"{big[1] - big[0]:>7}")
    return 0


def cmd_diff(args):
    rel = read_cart(RELEASE)
    beta = read_cart(BETA)
    print(f"release body sha1 bd806d7f...  beta body sha1 2a0a444d...")
    print(f"differing bytes: {sum(1 for a, b in zip(rel, beta) if a != b)} "
          f"/ {PRG_SIZE}")
    print(f"non-zero: release {nz(rel)}  beta {nz(beta)}\n")

    ours = args.prg.read_bytes() if args.prg else bytes(PRG_SIZE)

    print("per 16 KiB bank: does the rebuild match release better than beta?")
    print(f"{'bank':>4} {'file':>8} {'rel~beta':>10} {'our~rel':>8} {'our~beta':>9} "
          f"{'winner':>8} {'relNZ':>7} {'betaNZ':>7} {'ourNZ':>7}")
    for b in range(8):
        lo, hi = b * 0x4000, (b + 1) * 0x4000
        r, e, o = rel[lo:hi], beta[lo:hi], ours[lo:hi]
        rb = match(r, e)
        orl = match(o, r)
        obl = match(o, e)
        win = "rel" if orl > obl else ("beta" if obl > orl else "tie")
        print(f"{b:>4} {lo:>#8x} {rb:>10} {orl:>8} {obl:>9} {win:>8} "
              f"{nz(r):>7} {nz(e):>7} {nz(o):>7}")

    print("\nper 4 KiB page (only pages that differ between the dumps)")
    print(f"{'page':>4} {'file':>8} {'rel~beta':>10} {'our~rel':>8} {'our~beta':>9} "
          f"{'winner':>8}")
    for p in range(32):
        lo, hi = p * 0x1000, (p + 1) * 0x1000
        r, e, o = rel[lo:hi], beta[lo:hi], ours[lo:hi]
        rb = match(r, e)
        if rb == 0x1000:
            continue
        orl, obl = match(o, r), match(o, e)
        win = "rel" if orl > obl else ("beta" if obl > orl else "tie")
        print(f"{p:>4} {lo:>#8x} {rb:>10} {orl:>8} {obl:>9} {win:>8}")

    print("\nlargest contiguous regions where release and beta differ")
    rs = runs(rel, beta, 0, PRG_SIZE, ("differ", "missing", "extra"))
    for lo, hi, k in sorted(rs, key=lambda r: -(r[1] - r[0]))[:25]:
        print(f"  {hi - lo:>6} bytes  {lo:>#8x}  ({k})")
    return 0


def cmd_runs(args):
    cart = read_cart(args.cart)
    ours = args.prg.read_bytes() if args.prg else bytes(PRG_SIZE)
    # `--cpu` says the bounds are CPU addresses and need +$10000. Without it they
    # are raw PRG file offsets. Guessing is how `--lo 0 --hi 0x20000` silently
    # became the range [0x10000, 0x30000) and reported the wrong bank.
    if args.cpu:
        lo = args.lo + 0x10000
        hi = args.hi + 0x10000
    else:
        lo, hi = args.lo, args.hi
    for kindname in args.kind:
        rs = runs(cart, ours, lo, hi, (kindname,))
        rs.sort(key=lambda r: -(r[1] - r[0]))
        print(f"== {kindname} runs in [{lo:#x},{hi:#x}) -- top {args.top}")
        for a, b, k in rs[:args.top]:
            print(f"  {b - a:>6} bytes  {label(a)}  ..  {label(b - 1)}")
            if args.dis:
                n = min(args.dis, b - a)
                start = cpu(a) if 0xC000 <= cpu(a) < 0x10000 else a
                for addr, text in disasm_block(cart[a:a + n], start & 0xFFFF):
                    print(f"      {addr:04X}: {text}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("bank", "diff"):
        p = sub.add_parser(name)
        p.add_argument("--cart", type=pathlib.Path, default=RELEASE)
        p.add_argument("--prg", type=pathlib.Path, default=REBUILD)
    p = sub.add_parser("runs")
    p.add_argument("--cart", type=pathlib.Path, default=RELEASE)
    p.add_argument("--prg", type=pathlib.Path, default=REBUILD)
    p.add_argument("--lo", type=lambda s: int(s, 0), default=0)
    p.add_argument("--hi", type=lambda s: int(s, 0), default=PRG_SIZE)
    p.add_argument("--cpu", action="store_true",
                   help="treat --lo/--hi as CPU addresses (+$10000)")
    p.add_argument("--kind", action="append",
                   default=["missing", "differ"],
                   choices=("missing", "differ", "extra"))
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--dis", type=int, default=0,
                   help="disassemble this many bytes of each run")
    args = ap.parse_args()
    return {"bank": cmd_bank, "diff": cmd_diff, "runs": cmd_runs}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())