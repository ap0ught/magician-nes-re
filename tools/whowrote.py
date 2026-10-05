#!/usr/bin/env python3
"""Which module emitted which byte of the rebuilt PRG, and how much of each bank.

`Assembler.emitted` is cleared at the start of every `run_file`, so the build has
no record of which bank each module's bytes belong to once it has run. This wraps
`run_file` and keeps a per-module set of PRG offsets, which answers the question
the per-bank table cannot: bank 5 looked empty under `ASSUMED_SLOTS`, and the
question is whether that is a placement problem or a module that was never
reaching it.

    python3 tools/whowrote.py                 # table
    python3 tools/whowrote.py --offset 0x16297   # who wrote this byte
    python3 tools/whowrote.py --conflicts    # bytes two modules both claim
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))
from cartref import DEFAULT_CART  # noqa: E402

import build as B  # noqa: E402
import pds6502  # noqa: E402


class Traced(pds6502.Assembler):
    owner: dict = {}                  # prg offset -> module that last wrote it
    bymodule: dict = {}               # module -> set of prg offsets
    order: list = []                  # modules in assembly order
    tracing = False

    def run_file(self, path, slot=None, origin=None, window_slots=None,
                 addr_ceiling=None):
        super().run_file(path, slot=slot, origin=origin,
                         window_slots=window_slots, addr_ceiling=addr_ceiling)
        if not self.tracing:
            # Pass 1 assembles every module at all sixteen slots as a search, so
            # recording there unions 16 attempts and reports a module as having
            # written the whole image. Only the project pass -- which assembles
            # each module once at its chosen slot -- is a real footprint.
            return None
        name = pathlib.Path(path).name
        # `run_file` clears `emitted` at the start of every attempt and leaves the
        # last attempt's map behind, which is the module's actual footprint.
        offs = set(self.emitted)
        self.bymodule.setdefault(name, set()).update(offs)
        for o in offs:
            self.owner[o] = name
        if name not in self.order:
            self.order.append(name)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offset", type=lambda s: int(s, 0))
    ap.add_argument("--conflicts", action="store_true")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    cart_prg, _cart_chr = B.read_cart(
        DEFAULT_CART)

    # Assemble the project pass by hand rather than reaching into build.py, so
    # the tracer can be switched on for exactly that pass.
    image = bytearray(B.PRG_SIZE)
    Traced.owner, Traced.bymodule, Traced.order = {}, {}, []
    asm = Traced(image, B.SRC, [B.SRC])
    asm.force_conditions = {"0=1": True}
    asm.prebank_symbols = frozenset({"b"})
    asm.prebank_split = True
    asm.demo_errors = B.X7_DEMO_ERRORS
    asm.prescan(B.ALL_SOURCES)
    asm.collect_macros(B.ALL_SOURCES)
    # The same placement the build uses: `CHAINED` modules continue from the
    # previous one, `SEQ.SRC` is appended at its own origin and window map, and
    # X7 is anchored so `reset` lands on the cartridge's reset vector -- which is
    # what `build.py` computes in two passes, so it is measured the same way.
    chained = set(B.CHAINED)
    _slots = B.all_slots()   # PINNED over ASSUMED; X4/X6/X7 are pinned
    placed = [(B.SRC / m, None if m in chained else _slots[m])
              for m in B.MODULES]
    placed += [(B.SRC / m, s) for m, s in B.SEQ_MODULES]
    Traced.tracing = True
    x7_base = None
    try:
        for attempt in range(2):
            asm.run_all([p for p, _ in placed], [s for _, s in placed],
                        {"SEQ.SRC": B.SEQ_ORIGIN,
                         **({"X7.PDS": x7_base} if x7_base else {})},
                        {"SEQ.SRC": B.SEQ_WINDOW_SLOTS})
            reset = asm.sym.get("reset")
            if reset is None or x7_base is not None:
                break
            x7_base = (cart_prg[0x1FFFC] | (cart_prg[0x1FFFD] << 8)) - (
                reset - pds6502.Assembler.slot_origin(B.PINNED_SLOTS["X7.PDS"]))
    finally:
        Traced.tracing = False
    prg = image
    bm = Traced.bymodule
    if not bm:
        print("no per-module footprint captured")
        return 1

    if args.offset is not None:
        o = args.offset
        print(f"file ${o:05X}: written by {Traced.owner.get(o, 'nobody')}")
        return 0

    print(f"{'module':<10} {'bytes':>7}  {'slots touched (8 KiB)':<52}  "
          f"{'span':>18}")
    for name in Traced.order:
        offs = bm.get(name, set())
        slots = sorted({o // 0x2000 for o in offs})
        s = ",".join(str(x) for x in slots)
        print(f"{name:<10} {len(offs):>7}  {s:<52}  "
              f"${min(offs):05X}-${max(offs):05X}")

    print(f"\n{'8K slot':>8} {'bank':>5} {'oursNZ':>7} {'cartNZ':>7}  owners")
    cart = B.read_cart(
        DEFAULT_CART)[0]
    for slot in range(16):
        lo, hi = slot * 0x2000, (slot + 1) * 0x2000
        owners: dict[str, int] = {}
        for o in range(lo, hi):
            w = Traced.owner.get(o)
            if w:
                owners[w] = owners.get(w, 0) + 1
        ours = sum(1 for v in prg[lo:hi] if v)
        cnz = sum(1 for v in cart[lo:hi] if v)
        who = ", ".join(f"{k}:{v}" for k, v in sorted(owners.items(),
                                                       key=lambda kv: -kv[1]))
        print(f"{slot:>8} {slot // 2:>5} {ours:>7} {cnz:>7}  {who or '-'}")

    if args.conflicts:
        seen: dict[int, str] = {}
        clashes: dict[int, list[str]] = {}
        for name, offs in bm.items():
            for o in offs:
                clashes.setdefault(o, []).append(name)
        hot = [(o, v) for o, v in clashes.items() if len(v) > 1]
        hot.sort()
        print(f"\nbytes claimed by more than one module: {len(hot)}")
        for o, v in hot[:args.limit * 64]:
            print(f"  file ${o:05X}: {', '.join(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())