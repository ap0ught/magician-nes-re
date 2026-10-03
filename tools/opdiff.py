#!/usr/bin/env python3
"""Walk two PRG images in lockstep from the same address and report operands
that differ by a constant.

The RAM map is the thing that most needs a measurement rather than an opinion:
`rn` is `$0D` in both the cartridge and the rebuild, `curchrpal` is `$0180` in
the cartridge and `$04D4` in the rebuild, and every operand past the divergence
is wrong in one of them. Comparing *addresses* cannot say which is which -- but
comparing the *stream of instructions* can, because the code is the same code:
wherever a `sta` sits at the same offset in both images and its operands differ
by a fixed amount, that difference is the map, and the cartridge is the one that
has to be right because the cartridge is the one that runs.

    python3 tools/opdiff.py 0xF166 0x200          # fixed window, 512 bytes
    python3 tools/opdiff.py 0xF166 0x2000 --min-run 6
    python3 tools/opdiff.py 0x8000 0x1000 --slot 0
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from dis6502 import LITERAL, MODE_LEN  # noqa: E402


def cpu_to_off(a: int, slot: int) -> int:
    """Where a CPU address lands in the flat image for a module assumed to be in
    `slot`. Slots 14 and 15 are the fixed windows; anything else is presented at
    $8000 (register 6) unless the caller says otherwise."""
    if a >= 0xE000:
        return 15 * 0x2000 + (a - 0xE000)
    if a >= 0xC000:
        return 14 * 0x2000 + (a - 0xC000)
    return slot * 0x2000 + (a - 0x8000)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cart", type=pathlib.Path,
                    default=pathlib.Path("/extdrive/backups/SHARE/roms/nes/Magician (USA).nes"))
    ap.add_argument("--reb", type=pathlib.Path,
                    default=ROOT / "asm" / "out" / "prg.bin")
    ap.add_argument("start", type=lambda s: int(s, 0))
    ap.add_argument("length", type=lambda s: int(s, 0))
    ap.add_argument("--slot-a", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--slot-b", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--min-run", type=int, default=1,
                    help="only report a delta seen at least this many times")
    ap.add_argument("--print", action="store_true", help="list every differing instruction")
    args = ap.parse_args()

    a = args.cart.read_bytes()[16:16 + 131072]
    braw = args.reb.read_bytes()
    b = braw[16:16 + 131072] if braw[:4] == b"NES\x1a" else braw

    pairs: list[tuple[int, int, int, int]] = []   # cpu, cart operand, reb operand
    addr = args.start
    end = min(args.start + args.length, 0x10000)
    while addr < end:
        oa = cpu_to_off(addr, args.slot_a)
        ob = cpu_to_off(addr, args.slot_b)
        ea = LITERAL.get(a[oa])
        eb = LITERAL.get(b[ob])
        if ea is None or eb is None or ea != eb or MODE_LEN[ea[1]] != MODE_LEN[eb[1]]:
            addr += 1
            continue
        ln = MODE_LEN[ea[1]]
        if oa + ln > len(a) or ob + ln > len(b):
            addr += 1
            continue
        va = a[oa + 1] if ln == 2 else (a[oa + 1] | (a[oa + 2] << 8)) if ln == 3 else -1
        vb = b[ob + 1] if ln == 2 else (b[ob + 1] | (b[ob + 2] << 8)) if ln == 3 else -1
        pairs.append((addr, va, vb))
        addr += ln

    deltas = collections.Counter()
    samples: dict[int, list[tuple[int, int, int]]] = collections.defaultdict(list)
    for cpu, va, vb in pairs:
        if va == vb:
            continue
        d = vb - va                     # reb - cart
        deltas[d] += 1
        samples[d].append((cpu, va, vb))

    print(f"{len(pairs)} instructions decoded identically from ${args.start:04X}")
    print(f"{'rebuilt - cartridge':>20s} {'n':>5s}   sample addresses")
    for d, n in deltas.most_common():
        if n < args.min_run:
            continue
        ss = " ".join(f"${c:04X}(cart ${x:04X}/reb ${y:04X})" for c, x, y in samples[d][:4])
        print(f"{d:>+20d} (${d & 0xFFFF:04X}) {n:5d}   {ss}")
    if args.print:
        print("\nevery differing operand:")
        for cpu, va, vb in pairs:
            if va != vb:
                print(f"  ${cpu:04X}: cart ${va:04X}  reb ${vb:04X}  delta ${(vb - va) & 0xFFFF:04X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())