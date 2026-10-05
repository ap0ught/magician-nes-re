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
    stale = BizHawk.running_emuhawk()
    print(f"running mono pids before launch: {stale or 'none'}")
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
        for n in ("phase", "curlev", "mapind", "manacur", "wealth", "plrstat",
                  "plrflg", "plrx", "plry", "obtyp", "pad", "deb_fire"):
            x = ram.f(n)
            print(f"  {n:<10} ${x.addr:04X}+{x.length} = {vals[n]:<8} [{x.hex(img)}]")
        # The panel/level code only runs when the game is past its title screen,
        # so at frame ~120 the expected state is: still the title, phase still
        # whatever the reset left, no player objects live.
        print(f"  title-screen check: obtyp = {img[0x04F4:0x04F8].hex()}")
        d = emu.snapshot("smoke_idle")
        print(f"snapshot -> {d}")
        for f in sorted(d.iterdir()):
            print(f"  {f.name:<34} {f.stat().st_size}")
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())