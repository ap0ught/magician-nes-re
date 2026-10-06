#!/usr/bin/env python3
"""Screenshot both ROMs at several frames, in one BizHawk session per (rom, frame).

    python3 tools/bizhawk/series.py <outdir> <tag:frames> [<tag:frames> ...]
    python3 tools/bizhawk/series.py /tmp/opencode/biz/series cart:60,150,300,600 reb:60,150,300,600

A single frame at 60 cannot distinguish "the rebuild draws the wrong picture" from
"the rebuild has not finished drawing it yet". The title screen is written
progressively, so the honest measurement is a time series: run both to the same
frames and compare at each one. If the rebuild converges, there is no corruption
to chase; if it does not, the frame where it stops converging is the frame worth
looking at.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "tools" / "bizhawk" / "run.sh"
SCRIPT = ROOT / "tools" / "bizhawk" / "frame.lua"
sys.path.insert(0, str(ROOT / "tools"))
from cartref import env_or_default  # noqa: E402

# $MAGICIAN_CART wins; otherwise the registry's target (Beta 1).
CART = env_or_default()
ROM = ROOT / "asm" / "out" / "magician-rebuilt.nes"

ROMS = {"cart": CART, "reb": ROM}


def stats(img: Image.Image) -> dict:
    px = list(img.convert("RGB").getdata())
    lum = [(r * 299 + g * 587 + b * 114) // 1000 for r, g, b in px]
    return {
        "size": img.size,
        "max_lum": max(lum) if lum else 0,
        "mean_lum": round(sum(lum) / len(lum), 2) if lum else 0.0,
        "nonzero": sum(1 for v in lum if v),
        "distinct": len(set(px)),
    }


def shoot(tag: str, frame: int, outdir: Path) -> Path | None:
    report = outdir / f"{tag}.f{frame}.report"
    png = outdir / f"{tag}.f{frame}.png"
    for stale in (report, png):
        if stale.exists():
            stale.unlink()
    env = dict(os.environ)
    env["MAGICIAN_LUA_OUT"] = str(report)
    env["MAGICIAN_PNG"] = str(png)
    env["MAGICIAN_FRAMES"] = str(frame)
    env["MAGICIAN_SETTLE"] = os.environ.get("MAGICIAN_SETTLE", "35")
    log = outdir / f"{tag}.f{frame}.log"
    subprocess.run(["bash", str(RUN), str(SCRIPT), str(ROMS[tag]), str(log)],
                   env=env, capture_output=True, text=True, timeout=600)
    return png if png.exists() else None


def main() -> int:
    outdir = Path(sys.argv[1])
    outdir.mkdir(parents=True, exist_ok=True)
    jobs: dict[str, list[int]] = {}
    for spec in sys.argv[2:]:
        tag, _, fr = spec.partition(":")
        jobs[tag] = [int(x) for x in fr.split(",")]

    got: dict[tuple[str, int], Path] = {}
    for tag, frames in jobs.items():
        for fr in frames:
            print(f"  shooting {tag} frame {fr} ...", file=sys.stderr, flush=True)
            p = shoot(tag, fr, outdir)
            if p:
                got[(tag, fr)] = p
            else:
                print(f"  !! no png for {tag} frame {fr}", file=sys.stderr)

    common = sorted(set(fr for _, fr in got if _ == "cart") &
                    set(fr for _, fr in got if _ == "reb"))
    print("\nframe | cartridge                          | rebuild")
    print("------+------------------------------------+------------------------------------")
    for fr in common:
        a = Image.open(got[("cart", fr)]).convert("RGB")
        b = Image.open(got[("reb", fr)]).convert("RGB")
        sa, sb = stats(a), stats(b)
        if a.size == b.size:
            diff = ImageChops.difference(a, b)
            bbox = diff.getbbox()
            dpix = sum(1 for p in diff.get_flattened_data() if p != (0, 0, 0)) \
                if hasattr(diff, "get_flattened_data") else \
                sum(1 for p in list(diff.getdata()) if p != (0, 0, 0))
            pct = 100.0 * dpix / (a.size[0] * a.size[1])
        else:
            bbox, pct = "SIZE MISMATCH", -1.0
        print(f"{fr:5d} | nz {sa['nonzero']:6d} max {sa['max_lum']:3d} "
              f"col {sa['distinct']:3d} | nz {sb['nonzero']:6d} max {sb['max_lum']:3d} "
              f"col {sb['distinct']:3d} | differ {pct:5.1f}% bbox={bbox}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
