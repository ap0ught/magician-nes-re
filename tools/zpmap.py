#!/usr/bin/env python3
"""Compare the RAM map of two PRG images by histogramming every data operand.

The zero-page and `$0200`-`$07FF` map is not written down anywhere except as a
run of `zp name,size` macros, and a wrong `equ` in that run makes every module
that touches RAM compare badly against the cartridge for reasons that have
nothing to do with the code. This walks both images, decodes every instruction
it can, and counts each zero-page and `$0100`-`$07FF` absolute operand. Two
images built from the same source have near-identical histograms when the map
agrees; where the map has been moved, the histogram moves with it, and *by how
much* -- which is the number that names the fault.

    python3 tools/zpmap.py cart                      # one image
    python3 tools/zpmap.py cart rebuild --diff       # the diff, by address
    python3 tools/zpmap.py cart rebuild --diff --lo 0x0100 --hi 0x0800

Data bytes decode as nonsense instructions, so the counts include noise, but the
noise is the same *kind* of noise in both images and mostly cancels. The signal
is a whole block of addresses whose counts move together.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from dis6502 import LITERAL  # noqa: E402

ZP_MODES = {"zp", "zpx", "zpy"}
ABS_MODES = {"abs", "absx", "absy", "ind"}
LEN = {"imp": 1, "acc": 1, "imm": 2, "zp": 2, "zpx": 2, "zpy": 2, "rel": 2,
       "abs": 3, "absx": 3, "absy": 3, "ind": 3, "indx": 2, "indy": 2}


def hist(prg: bytes, lo: int, hi: int) -> collections.Counter:
    """Count RAM operands. `prg` is a flat 128 KiB image; bank boundaries are
    irrelevant because every operand is recorded as an address, not an offset."""
    out: collections.Counter = collections.Counter()
    for base in range(0, len(prg), 0x2000):
        bank = prg[base:base + 0x2000]
        i = 0
        n = len(bank)
        while i < n:
            ent = LITERAL.get(bank[i])
            if ent is None:
                i += 1
                continue
            mn, md = ent
            ln = LEN[md]
            if i + ln > n:
                i += 1
                continue
            if ln == 2:
                v = bank[i + 1]
            else:
                v = bank[i + 1] | (bank[i + 2] << 8)
            # `lda $2002`-style register writes are hardware, not RAM.
            if lo <= v < hi and not (0x2000 <= v < 0x4020):
                out[v] += 1
            i += ln
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", type=pathlib.Path)
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=0x0000)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=0x0800)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--min", type=int, default=0)
    args = ap.parse_args()

    hists = []
    for p in args.images:
        b = p.read_bytes()
        prg = b[16:16 + 131072] if b[:4] == b"NES\x1a" else b
        h = hist(prg, args.lo, args.hi)
        hists.append(h)

    if len(hists) == 1 or not args.diff:
        h = hists[0]
        print(f"{'addr':>6s} {'n':>5s}")
        for a, c in sorted(h.items()):
            if c >= args.min:
                print(f"${a:04X} {c:5d}")
        return 0

    names = [p.name for p in args.images]
    rows = []
    for a in range(args.lo, args.hi):
        cs = [h.get(a, 0) for h in hists]
        rows.append((a, cs))
    print(f"{'addr':>6s} " + " ".join(f"{n[:14]:>14s}" for n in names))
    # A block that moved shows as: every address in it high in one image and
    # near zero in the other. Print the addresses whose counts differ most.
    moved = [(a, cs) for a, cs in rows
             if max(cs) - min(cs) >= max(4, 0.5 * max(cs))]
    for a, cs in moved[: args.top]:
        print(f"${a:04X} " + " ".join(f"{c:14d}" for c in cs))
    print(f"\n{len(moved)} addresses in ${args.lo:04X}-${args.hi:04X} differ by "
          f"more than half and by at least 4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())