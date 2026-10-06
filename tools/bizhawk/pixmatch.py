#!/usr/bin/env python3
"""Screenshot both ROMs at a frame and report how much of the picture agrees.

    python3 tools/bizhawk/pixmatch.py <outdir> <tag:rom> [<tag:rom> ...] [frame]

One BizHawk session per ROM, serially -- BizHawk allows one instance and a second
launch is diverted into the first. The PNGs are BizHawk's own screenshot of the
core's video buffer, cropped to whole NES pixels, and the comparison is per
region as well as overall: "44% of the picture matches" and "the top 40% matches"
are different claims and only one of them points at the fault.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "tools" / "bizhawk" / "run.sh"
SCRIPT = ROOT / "tools" / "bizhawk" / "frame.lua"

REGIONS = [
    ("rows 0-3", 0, 16), ("rows 4-11", 16, 48), ("rows 12-19", 48, 80),
    ("rows 20-23", 80, 96), ("rows 24-29", 96, 120),
    ("left half", 0, 0), ("right half", 0, 0),
]


def shoot(tag: str, rom: str, outdir: Path, frame: int) -> Path | None:
    report = outdir / f"{tag}.f{frame}.report"
    png = outdir / f"{tag}.f{frame}.png"
    for stale in (report, png):
        if stale.exists():
            stale.unlink()
    env = dict(os.environ)
    env.update(MAGICIAN_LUA_OUT=str(report), MAGICIAN_PNG=str(png),
               MAGICIAN_FRAMES=str(frame), MAGICIAN_EXPECT=str(report),
               MAGICIAN_DONE="final_frame", MAGICIAN_KILL_STALE="1",
               MAGICIAN_SETTLE="90", MAGICIAN_EXPECT_WAIT="600")
    proc = subprocess.run(["bash", str(RUN), str(SCRIPT), str(rom),
                           str(outdir / f"{tag}.f{frame}.log")],
                          env=env, capture_output=True, text=True, timeout=900)
    if not png.exists():
        print(f"  !! {tag}: no png (run.sh rc={proc.returncode})", file=sys.stderr)
        print(proc.stdout[-2000:], file=sys.stderr)
    return png if png.exists() else None


def main() -> int:
    outdir = Path(sys.argv[1])
    specs = [a for a in sys.argv[2:] if ':' in a]
    frame = int(sys.argv[-1]) if sys.argv[-1].isdigit() else 60
    outdir.mkdir(parents=True, exist_ok=True)
    got = {}
    for spec in specs:
        tag, _, rom = spec.partition(':')
        print(f"  shooting {tag} frame {frame} ...", file=sys.stderr, flush=True)
        p = shoot(tag, rom, outdir, frame)
        if p:
            got[tag] = p
    if len(got) < 2:
        print("need two ROMs", file=sys.stderr)
        return 2
    tags = list(got)
    a = Image.open(got[tags[0]]).convert("RGB")
    b = Image.open(got[tags[1]]).convert("RGB")
    print(f"\n{tags[0]} {a.size}   {tags[1]} {b.size}   frame {frame}")
    if a.size != b.size:
        print("SIZE MISMATCH -- cannot compare")
        return 2
    w, h = a.size
    k = min(w // 256, h // 240)
    if k > 1:
        a = a.resize((256 * k, 240 * k), Image.NEAREST).crop((0, 0, 256 * k, 240 * k))
        b = b.resize((256 * k, 240 * k), Image.NEAREST).crop((0, 0, 256 * k, 240 * k))
    pa, pb = list(a.getdata()), list(b.getdata())
    same = sum(1 for x, y in zip(pa, pb) if x == y)
    print(f"\noverall: {same}/{len(pa)} pixels identical ({100.0*same/len(pa):.1f}%)")
    print("\nper region (8 scanline rows each, then left/right half):")
    for name, r0, r1 in REGIONS:
        if name == "left half":
            sl = slice(0, len(pa) // 2)
            n = len(pa) // 2
        elif name == "right half":
            sl = slice(len(pa) // 2, len(pa))
            n = len(pa) - len(pa) // 2
        else:
            sl = slice(r0 * w, r1 * w)
            n = sl.stop - sl.start
        m = sum(1 for i in range(sl.start, sl.stop) if pa[i] == pb[i])
        print('  %-12s %6d/%-6d %5.1f%%' % (name, m, n, 100.0 * m / n))
    for t in tags:
        c = {}
        for px in Image.open(got[t]).convert("RGB").getdata():
            c[px] = c.get(px, 0) + 1
        dom, dn = max(c.items(), key=lambda kv: kv[1])
        print('  %-10s distinct colours %3d  dominant %s %.1f%%'
              % (t, len(c), dom, 100.0 * dn / len(pa)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())