#!/usr/bin/env python3
"""Where in the cartridge does each module's assembled code actually sit?

The naive slide -- compare every byte at every (slot, offset) pair -- is
quadratic and, for a module that spans slots (X7 does, because its data section
`bank`s its way round six banks), it does not even mean what it looks like it
means. Invert it instead: take the module's own bytes, cut them into windows,
find each window in the cartridge, and histogram the *implied* placements.

For a module assembled at logical address L placed in 8 KiB slot q, its bytes
land at file offset ``q * 0x2000 + ((L - 0x8000) & 0x1FFF)``. So a window found
at file offset F implies one (q, shift) pair per candidate q, and a *cluster* of
windows implying the same (q, shift) is evidence, while isolated hits are noise.

    python3 tools/findcode.py
    python3 tools/findcode.py --module X3.PDS --window 12 --best 4
    python3 tools/findcode.py --min-cluster 4
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

import build as B  # noqa: E402
from dis6502 import read_cart, disasm_block  # noqa: E402

CART = pathlib.Path("/extdrive/backups/SHARE/roms/nes/Magician (USA).nes")
PRG_SIZE = B.PRG_SIZE
SLOT = 0x2000


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module")
    ap.add_argument("--window", type=int, default=12,
                    help="bytes per probe window (12 keeps false hits low)")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--best", type=int, default=4)
    ap.add_argument("--min-cluster", type=int, default=3,
                    help="ignore placements supported by fewer windows")
    ap.add_argument("--dis", type=int, default=0,
                    help="disassemble this many bytes at the best placement")
    args = ap.parse_args()

    cart = read_cart(CART)
    import slotscore
    ref = dict(B.ASSUMED_SLOTS)
    ref["X5.PDS"], ref["X7.PDS"] = 14, 15
    _, asm = slotscore.build(ref)

    mods = [args.module] if args.module else B.MODULES
    W = args.window

    for m in mods:
        d = asm.foot.get(m, {})
        if len(d) < W:
            print(f"{m}: only {len(d)} bytes, skipping")
            continue
        off = sorted(d)
        # Probe windows over the module's *logical* address range: consecutive
        # entries in `off` that are also consecutive addresses.
        windows = []
        for i in range(0, len(off) - W + 1, args.stride):
            chunk = off[i:i + W]
            if chunk[-1] - chunk[0] != W - 1:
                continue
            windows.append((chunk[0], bytes(d[k] for k in chunk)))
        # A module's slot is q; the window at logical $8000+w sits at
        # q*SLOT + w. So a hit at F implies (q, w0) with q = (F - w0) // SLOT.
        votes: collections.Counter = collections.Counter()
        where: dict[tuple[int, int], list[int]] = collections.defaultdict(list)
        for w0, sig in windows:
            i = cart.find(sig)
            while i != -1:
                q, rem = divmod(i - w0, SLOT)
                if 0 <= q < 16 and 0 <= rem < SLOT:
                    votes[(q, rem)] += 1
                    where[(q, rem)].append(i)
                i = cart.find(sig, i + 1)
        print(f"{m}: {len(off)} bytes, {len(windows)} probe windows of {W}, "
              f"{sum(votes.values())} raw hits, {len(votes)} distinct placements")
        if not votes:
            print("  no hits at all")
            print()
            continue
        top = [(v, k) for k, v in votes.items() if v >= args.min_cluster]
        top.sort(reverse=True)
        if not top:
            top = sorted(((v, k) for k, v in votes.items()), reverse=True)
            print(f"  (nothing reached --min-cluster {args.min_cluster}; "
                  f"best is {top[0][0]} windows, chance is ~"
                  f"{100.0 / 256:.2f}% of windows)")
        for v, (q, rem) in top[:args.best]:
            print(f"  slot {q:>2} +${rem:04X}  supported by {v:4d}/{len(windows)} "
                  f"windows ({100.0 * v / len(windows):5.2f}%)  "
                  f"file base ${q * SLOT + rem:05X}")
        if args.dis and top:
            v, (q, rem) = top[0]
            base = q * SLOT + rem
            print(f"  cartridge at file ${base:05X} "
                  f"(cpu ${(base - 0x10000) & 0xFFFF:04X}), {args.dis} bytes:")
            for addr, text in disasm_block(cart[base:base + args.dis],
                                           (base - 0x10000) & 0xFFFF):
                print(f"    {addr:04X}: {text}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())