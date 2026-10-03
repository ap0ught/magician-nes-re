#!/usr/bin/env python3
"""Score each module's emitted bytes against the cartridge at every candidate slot.

Why this is not the search `asm/build.py` documents as failed
-------------------------------------------------------------
`journal/01-six-modules-with-no-slot.md` records that "scoring all emitted bytes
identifies the slot for X0-X5. It does not", with the reason: "unresolved
cross-bank forward references make every `lda label` choose zero-page over
absolute, so most bytes are noise regardless of the right slot."

That is a fact about *the search*, not about the bytes. `build.py` assembles a
module **on its own** to score it, so every symbol another module defines is
unknown, every `lda label` picks zero-page over absolute, and the opcode bytes
really are noise at all sixteen slots.

Here every module is assembled **together**, in a full project pass, so all 3120
symbols resolve and the zero-page/absolute choice is right. A module's code bytes
then do not depend on which slot it is placed in, so they can be *re-scored*
against the cartridge at every candidate slot without re-assembling anything:

    file offset = q * 0x2000 + ((logical_addr - 0x8000) & 0x1FFF)

for candidate slot `q`. Bytes that genuinely encode a cross-bank absolute
address are wrong for every `q` alike -- they are a fixed penalty, not a signal --
so the argmax is still the best estimate. That is the whole difference between
this and the search it replaces.

    python3 tools/slotscore.py                 # per-module, all 16 slots
    python3 tools/slotscore.py --slots 0-5     # restrict to a slot range
    python3 tools/slotscore.py --assign        # best-fit assignment (Hungarian)
    python3 tools/slotscore.py --module X3.PDS --why 40
"""

from __future__ import annotations

import argparse
import itertools
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

import build as B  # noqa: E402
import pds6502  # noqa: E402
from dis6502 import read_cart  # noqa: E402

CART = pathlib.Path("/extdrive/backups/SHARE/roms/nes/Magician (USA).nes")


class Footprint(pds6502.Assembler):
    """Records, per module, the (within-slot offset, byte) pairs it emitted."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.trace = False
        self.foot: dict[str, dict[int, int]] = {}
        self.order: list[str] = []
        self.slotof: dict[str, int] = {}

    def run_file(self, path, slot=None, origin=None, window_slots=None,
                 addr_ceiling=None):
        super().run_file(path, slot=slot, origin=origin,
                         window_slots=window_slots, addr_ceiling=addr_ceiling)
        if not self.trace:
            return None
        name = pathlib.Path(path).name
        s = slot if slot is not None else self.slot
        d = self.foot.setdefault(name, {})
        for off, byte in self.emitted.items():
            d[(off - s * 0x2000) & 0x1FFF] = byte
        self.slotof.setdefault(name, s)
        if name not in self.order:
            self.order.append(name)
        return None


def build(reference_slots: dict[str, int], bank_groups: bool = True):
    image = bytearray(B.PRG_SIZE)
    asm = Footprint(image, B.SRC, [B.SRC])
    asm.force_conditions = {"0=1": True}
    asm.prebank_symbols = frozenset({"b"}) if bank_groups else frozenset()
    asm.prescan(B.ALL_SOURCES)
    asm.collect_macros(B.ALL_SOURCES)
    placed = [(B.SRC / m, reference_slots[m]) for m in B.MODULES]
    asm.trace = True
    try:
        asm.run_all([p for p, _ in placed], [s for _, s in placed])
    finally:
        asm.trace = False
    return image, asm


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slots", default="0-15", help="lo-hi slot range")
    ap.add_argument("--module", help="restrict the detail table to one module")
    ap.add_argument("--why", type=int, default=0,
                    help="show the top N matched addresses for --module")
    ap.add_argument("--assign", action="store_true",
                    help="best one-to-one assignment of modules to slots")
    ap.add_argument("--ref", default="", help="k=v,... override the reference layout")
    args = ap.parse_args()

    lo, hi = (int(x, 0) for x in args.slots.split("-"))
    slots = list(range(lo, hi + 1))

    # all_slots() already carries the pinned measurements (X4/X6/X7); the
    # ASSUMED_SLOTS index raises KeyError on exactly those three.
    ref = dict(B.all_slots())
    for kv in filter(None, args.ref.split(",")):
        k, v = kv.split("=")
        ref[k] = int(v, 0)

    cart = read_cart(CART)
    _, asm = build(ref)

    pure = [m for m in B.MODULES if m not in ("X5.PDS", "X7.PDS")]
    print(f"reference layout: {ref}")
    print(f"slot range {lo}-{hi}; scoring modules {pure}\n")

    scores: dict[str, dict[int, int]] = {}
    nzscores: dict[str, dict[int, int]] = {}
    for m in B.MODULES:
        d = asm.foot.get(m, {})
        if not d:
            print(f"{m}: not assembled")
            continue
        row, nrow = {}, {}
        nz = sum(1 for b in d.values() if b) or 1
        for q in range(16):
            hit = nhit = 0
            for w, byte in d.items():
                off = q * 0x2000 + w
                if 0 <= off < len(cart) and cart[off] == byte:
                    hit += 1
                    if byte:
                        nhit += 1
            row[q] = hit
            nrow[q] = nhit
        scores[m] = row
        nzscores[m] = nrow
        if m == "X5.PDS" or m == "X7.PDS":
            continue
        best = max(row[q] for q in slots)
        cells = "".join(
            ("*" if row[q] == best and row[q] else " ") for q in slots)
        print(f"{m}  ({len(d):5d} bytes emitted, {nz:5d} of them non-zero)")
        print(f"  {'slot':<26}" + "".join(f"{q:>7}" for q in slots))
        print(f"  {'all bytes':<26}" + "".join(f"{row[q]:>7}" for q in slots))
        print(f"  {'non-zero only':<26}" + "".join(f"{nrow[q]:>7}" for q in slots))
        print(f"  {'pct non-zero':<26}"
              + "".join(f"{100.0 * nrow[q] / nz:>6.1f}%" for q in slots))
        print(f"  {'best (all)':<26}" + cells)

    if args.assign and len(pure) <= 10:
        print(f"\nbest one-to-one assignment over slots {lo}-{hi}:")
        cands = [s for s in slots if len(slots) >= len(pure)] or slots
        best = None
        for perm in itertools.permutations(cands, len(pure)):
            assign = dict(zip(pure, perm))
            tot = sum(scores[m][assign[m]] for m in pure)
            if best is None or tot > best[0]:
                best = (tot, assign)
        tot, assign = best
        print(f"  total matched bytes {tot} over "
              f"{sum(len(asm.foot[m]) for m in pure)} emitted")
        for m in pure:
            q = assign[m]
            print(f"    {m} -> slot {q:>2}   {scores[m][q]:6d}/{len(asm.foot[m]):<6d} "
                  f"({100.0 * scores[m][q] / len(asm.foot[m]):5.2f}%)")
        ref_tot = sum(scores[m][ref[m]] for m in pure)
        print(f"  reference layout scores {ref_tot} on the same bytes "
              f"({100.0 * ref_tot / sum(len(asm.foot[m]) for m in pure):5.2f}%)")

    if args.module:
        m = args.module
        d = asm.foot[m]
        q = max(slots, key=lambda s: scores[m][s])
        print(f"\n{m}: {len(d)} bytes, best slot {q} "
              f"({scores[m][q]} match, {100.0 * scores[m][q] / len(d):.2f}%)")
        hits = [(w, b) for w, b in sorted(d.items()) if cart[q * 0x2000 + w] == b]
        print(f"  matched offsets within the slot: "
              f"{[hex(0x8000 + w) for w, _ in hits[:args.why]]}")
        if args.why:
            lo2 = hits[0][0] if hits else 0
            from dis6502 import disasm_block
            print("  cartridge, best-matching slot, linear from its first match:")
            for addr, text in disasm_block(cart[q * 0x2000 + lo2:q * 0x2000 + lo2 + args.why],
                                           0x8000 + lo2):
                print(f"    {addr:04X}: {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())