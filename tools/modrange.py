#!/usr/bin/env python3
"""Report, for each module, the byte range it emits and which PRG file offsets
it writes, under the placement the build actually uses.

The build reports slot placement but not the resulting *address span*, and the
span is what decides whether MMC3 can present a module where the code calls it.
`initcols` lives in X6, `reset` calls it, `reset` is in the fixed `$E000`-`$FFFF`
window -- so X6 has to be in slot 15 or that `jsr` reads a different module. That
is not visible anywhere in the build's own output.

    python3 tools/modrange.py
    python3 tools/modrange.py --slots X6.PDS=15 --origins X6.PDS=0xE000
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

import build as B            # noqa: E402
import pds6502               # noqa: E402


class Traced(pds6502.Assembler):
    bymodule: dict = collections.OrderedDict()
    ranges: dict = {}
    physlog: dict = {}
    lo: dict = {}
    hi: dict = {}

    def emit(self, byte):
        # `phys` is the address the byte is *assembled* at, which is not the same
        # as the file offset `prg_offset` files it at -- and it is the address that
        # decides whether two modules overlap. Recording the left edge here rather
        # than reading `self.phys` before the run is what makes that visible: a run
        # whose base address comes from `slot`/`org` starts there, not wherever
        # the previous module happened to stop.
        self._lo = min(getattr(self, "_lo", 1 << 30), self.phys)
        self._hi = max(getattr(self, "_hi", 0), self.phys)
        super().emit(byte)

    def run_file(self, path, slot=None, origin=None, window_slots=None,
                 addr_ceiling=None):
        self._lo = 1 << 30
        self._hi = 0
        super().run_file(path, slot=slot, origin=origin,
                         window_slots=window_slots, addr_ceiling=addr_ceiling)
        name = pathlib.Path(path).name
        offs = sorted(self.emitted)
        self.bymodule[name] = offs
        self.ranges[name] = (self._lo, self._hi)
        self.physlog[name] = self.phys
        self.lo[name] = self._lo
        self.hi[name] = self._hi


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cart", type=pathlib.Path,
                    default=pathlib.Path("/extdrive/backups/SHARE/roms/nes/Magician (USA).nes"))
    args = ap.parse_args()
    prg = args.cart.read_bytes()[16:16 + 128 * 1024]

    image, asm, log, unplaced = B.assemble_prg(prg, False, asm_cls=Traced)
    t = asm  # assemble_prg returns the Assembler it last used
    print(f"{'module':11s} {'addr span':>17s} {'bytes':>7s} "
          f"{'file offsets':>25s}  cart-match")
    for name, (a, b) in t.ranges.items():
        offs = t.bymodule[name]
        lo, hi = (offs[0], offs[-1]) if offs else (0, 0)
        same = sum(1 for o in offs if prg[o] == image[o])
        print(f"{name:11s} ${a:04X}-${b:04X} {b - a + 1:7d} "
              f"${lo:05X}-${hi:05X} ({len(offs):6d})  {same}/{len(offs)} "
              f"= {100.0 * same / len(offs) if offs else 0:5.1f}%")
    print()
    for line in log:
        if "slot" in line and ("ASSUMED" in line or "PINNED" in line or "match" in line):
            print("  " + line)
    print()
    print("unplaced:", unplaced)
    # Per-slot occupancy, and who owns what.
    owner: dict = {}
    for name, offs in t.bymodule.items():
        for o in offs:
            owner.setdefault(o, []).append(name)
    clashes = {o: v for o, v in owner.items() if len(v) > 1}
    print(f"offsets written by more than one module: {len(clashes)}")
    for slot in range(16):
        base = slot * 0x2000
        cnt = sum(1 for o in range(base, base + 0x2000) if o in owner)
        who = collections.Counter()
        for o in range(base, base + 0x2000):
            for n in owner.get(o, ()):
                who[n] += 1
        parts = ", ".join(f"{k}:{v}" for k, v in who.most_common())
        print(f"  slot {slot:2d} (${0x8000 + (slot & 3) * 0x2000:04X} by slot_origin) "
              f"{cnt:5d}/8192 written   {parts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
