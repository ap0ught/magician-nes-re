#!/usr/bin/env python3
"""Slide a module's assembled bytes through the cartridge and look for a peak.

`tools/slotscore.py` scores a module's bytes at each candidate *slot*, with the
bytes landing at the same offset within it. It finds nothing: every module scores
0.3-2.1% of its non-zero bytes at every slot, which is at or below the 0.39% you
get by chance. The obvious explanation is that the cartridge is not the same
*build* as the source -- the source's headers say 02/03/90 and the cartridge went
out in February 1991 -- and that the year of work in between shifted every
routine.

That is testable, and this is the test: if the year of edits was mostly
*insertion*, then the 1990 bytes are still present in the cartridge, just
displaced. Slide each module's bytes through every slot at every offset and look
for a peak above chance. A peak is evidence the module's code is right and only
its address moved; a flat line is evidence the module's code is genuinely
different.

    python3 tools/shiftscan.py
    python3 tools/shiftscan.py --module X7.PDS --best 3
    python3 tools/shiftscan.py --window 24      # longest match to report
"""

from __future__ import annotations

import argparse
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


def load(reference_slots: dict[str, int]):
    import slotscore
    _, asm = slotscore.build(reference_slots)
    return asm


def ref_layout() -> dict[str, int]:
    r = dict(B.ASSUMED_SLOTS)
    r["X5.PDS"], r["X7.PDS"] = 14, 15
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", help="only this module")
    ap.add_argument("--best", type=int, default=3, help="how many peaks per module")
    ap.add_argument("--window", type=int, default=0,
                    help="report the longest identical run at the best shift")
    ap.add_argument("--slot", type=int, default=None,
                    help="restrict to one 8 KiB slot")
    args = ap.parse_args()

    cart = read_cart(CART)
    asm = load(ref_layout())
    mods = [args.module] if args.module else B.MODULES

    for m in mods:
        d = asm.foot.get(m, {})
        if not d:
            print(f"{m}: not assembled")
            continue
        # Collapse to a dense array over the slot window; gaps become None so a
        # padding hole cannot be slid over silently.
        n = SLOT
        arr = [d.get(i) for i in range(n)]
        nz = [i for i, v in enumerate(arr) if v]
        if not nz:
            print(f"{m}: no non-zero bytes")
            continue
        # Longest contiguous stretch with no gap, so the slide is over real code.
        best_run = 0
        run_start = 0
        cur = 0
        cur_start = 0
        for i in nz:
            if i == 0 or arr[i - 1] is None:
                cur, cur_start = 1, i
            else:
                cur += 1
            if cur > best_run:
                best_run, run_start = cur, cur_start
        lo, hi = run_start, run_start + best_run
        seg = arr[lo:hi]
        nseg = sum(1 for v in seg if v)
        print(f"{m}: {nseg} non-zero bytes, longest gapless run "
              f"{best_run} at slot offset ${lo:04X}-${hi - 1:04X} "
              f"({100.0 * nseg / n:.1f}% of the slot)")

        slots = [args.slot] if args.slot is not None else range(16)
        peaks = []
        for q in slots:
            base = q * SLOT
            for shift in range(-lo, SLOT - hi + 1):
                hit = tot = 0
                for k, v in enumerate(seg):
                    if v is None:
                        continue
                    off = base + lo + shift + k
                    if 0 <= off < PRG_SIZE:
                        tot += 1
                        if cart[off] == v:
                            hit += 1
                if tot:
                    peaks.append((hit, tot, q, shift))
        peaks.sort(key=lambda t: -(t[0] / t[1]))
        chance = 1.0 / 256
        print(f"  chance level {100 * chance:.2f}% of non-zero bytes")
        for hit, tot, q, shift in peaks[:args.best]:
            pct = 100.0 * hit / tot
            print(f"  slot {q:>2} shift {shift:>+6}  {hit:5d}/{tot:<5d} = {pct:5.2f}%"
                  f"  ({pct / (100 * chance):5.1f}x chance)  "
                  f"landed at file {(q * SLOT + lo + shift) & 0x1FFFF:05X}")
            if args.window:
                base = q * SLOT + lo + shift
                run = best = 0
                bstart = 0
                for k, v in enumerate(seg):
                    off = base + k
                    if v is not None and 0 <= off < PRG_SIZE and cart[off] == v:
                        run += 1
                        if run > best:
                            best, bstart = run, k - run + 1
                    else:
                        run = 0
                print(f"      longest identical run {best} bytes at slot offset "
                      f"${bstart:04X}")
                if best:
                    a = base + bstart
                    print(f"      cartridge at file {a:05X} "
                          f"(cpu ${(a - 0x10000) & 0xFFFF:04X}):")
                    for addr, text in disasm_block(cart[a:a + best], 0):
                        print(f"        {addr:04X}: {text}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())