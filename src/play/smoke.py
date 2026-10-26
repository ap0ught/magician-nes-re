#!/usr/bin/env python3
"""Smoke-test the bridge: connect, step, snapshot, close.

    python3 src/play/smoke.py [frames]

Deliberately the smallest thing that can fail loudly. Every instrument in this
project has at some point reported success while measuring nothing, so the
first question about a new bridge is not "does it work" but "does it notice when
it is not working".
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from play.emu import BizHawk  # noqa: E402
from play import ram  # noqa: E402


def main() -> int:
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    ram.load_button_order()
    stale = BizHawk.running_emuhawk()
    print(f"running mono pids before launch: {stale or 'none'}")
    if stale:
        print("REFUSING: BizHawk would divert this launch into the running one")
        return 2
    with BizHawk(log_name="smoke", route="smoke") as emu:
        print(f"connected in {emu.connect_seconds:.1f}s on port {emu.port}")
        print(f"domains ({len(emu.domains)}):")
        for d in sorted(emu.domains, key=lambda x: x.index):
            print(f"  {d.index:2d} {d.name:<22} {d.size}")
        print(f"ram map digest {ram.map_digest()} ({len(ram.all_fields())} fields)")
        emu.step(frames=frames)
        print(f"after {frames} idle frames: frame={emu.frame}")
        img = emu.work_ram()
        print(f"work RAM {len(img)} bytes, fingerprint {emu.fingerprint()}")
        # Every field must be readable from a single $0000-$07FF image, and each
        # declared byte must be one the symbol table says it is.
        vals = ram.decode(img)
        print(f"decoded {len(vals)} fields")
        for n in ("phase", "curlev", "mapind", "manacur", "manatop", "wealth",
                  "plrstat", "plrflg", "plrtype", "pad", "deb_start", "nmiflag"):
            x = ram.f(n)
            print(f"  {n:<10} ${x.addr:04X}+{x.length} = {vals[n]:<8} [{x.hex(img)}]")
        # `plrx`/`plry` are FUNCTIONS, not fields: the two halves of the player's
        # position live in two separate one-byte arrays (obxl[3] and obxh[3]),
        # four bytes apart, so there is no two-byte field that spans them. The
        # first version of this file called `ram.f("plrx")` and would have died
        # with a KeyError the first time that changed.
        print(f"  player position: ({ram.plrx(img)},{ram.plry(img)})  "
              f"[obxl[3]=${img[0x0514 + ram.PLAYER_IDX]:02X} "
              f"obxh[3]=${img[0x0518 + ram.PLAYER_IDX]:02X}]")
        for i in range(4):
            s = ram.object_slot(img, i)
            print(f"  slot {i}: obtyp=${s['obtyp']:02X} {'LIVE' if s['active'] else 'free'} "
                  f"pos=({s['x']},{s['y']}) obchr=${s['obchr']:02X}")
        d = emu.snapshot("smoke_idle")
        print(f"snapshot -> {d}")
        for f in sorted(d.iterdir()):
            print(f"  {f.name:<34} {f.stat().st_size}")
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())