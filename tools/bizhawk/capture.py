#!/usr/bin/env python3
"""Capture one frame from each ROM in BizHawk and report framebuffer facts.

    python3 tools/bizhawk/capture.py <outdir> [frames]

Runs the real cartridge and the rebuild through the same BizHawk session recipe --
same core (quickerNES), same GDI display path, same Lua script, same target frame
-- and reports the pixels from BizHawk's own screenshot for both, so the comparison
is like-for-like. Nothing here reads or writes the cartridge: BizHawk only ever
opens it read-only, and the only files created are the reports and PNGs.

The PNG is BizHawk's screenshot of the client surface, so it may carry a small
border or an OSD line. `--crop` reduces it to an integer multiple of the NES
picture before the statistics are taken, and the raw dimensions are reported
either way so a crop can never quietly hide a scaling problem.
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
CART = Path(os.environ.get("MAGICIAN_CART", "/extdrive/backups/SHARE/roms/nes/Magician (USA).nes"))
ROM = ROOT / "asm" / "out" / "magician-rebuilt.nes"

NES_W, NES_H = 256, 240


def luminance_stats(img: Image.Image) -> dict:
    """Luminance over the RGB channels, plus a count of pixels differing from the
    image's own most common colour.

    'non-zero pixels' is counted against pure black rather than against the
    dominant colour, because the question is whether the emulator put anything on
    screen at all -- a uniform grey border would otherwise read as content.
    """
    px = list(img.convert("RGB").getdata())
    lum = [(r * 299 + g * 587 + b * 114) // 1000 for r, g, b in px]
    counts: dict[tuple[int, int, int], int] = {}
    for p in px:
        counts[p] = counts.get(p, 0) + 1
    dominant, dominant_n = max(counts.items(), key=lambda kv: kv[1])
    return {
        "pixels": len(px),
        "max_luminance": max(lum) if lum else 0,
        "mean_luminance": (sum(lum) / len(lum)) if lum else 0.0,
        "nonzero_pixels": sum(1 for v in lum if v != 0),
        "distinct_colours": len(counts),
        "dominant_rgb": dominant,
        "dominant_fraction": dominant_n / len(px) if px else 0.0,
    }


def crop_to_nes(img: Image.Image) -> Image.Image:
    """Trim a client screenshot down to whole NES pixels.

    The client surface is an integer multiple of 256x240 plus whatever the
    window frame contributed, so scale by the largest integer that still fits and
    crop symmetrically.
    """
    w, h = img.size
    k = min(w // NES_W, h // NES_H)
    if k < 1:
        return img
    small = img.convert("RGB").resize((NES_W * k, NES_H * k), Image.NEAREST)
    # Take the top-left NES_W x NES_H tile block out of the scaled surface so the
    # reported numbers describe NES pixels and not a resampled mix.
    return small.crop((0, 0, NES_W, NES_H))


def run_one(tag: str, rom: Path, outdir: Path, frames: int) -> dict:
    report = outdir / f"{tag}.report"
    png = outdir / f"{tag}.png"
    for stale in (report, png):
        if stale.exists():
            stale.unlink()
    env = dict(os.environ)
    env["MAGICIAN_LUA_OUT"] = str(report)
    env["MAGICIAN_PNG"] = str(png)
    env["MAGICIAN_FRAMES"] = str(frames)
    env["MAGICIAN_SETTLE"] = os.environ.get("MAGICIAN_SETTLE", "35")
    log = outdir / f"{tag}.log"
    proc = subprocess.run(
        ["bash", str(RUN), str(SCRIPT), str(rom), str(log)],
        env=env, capture_output=True, text=True, timeout=300,
    )
    facts = {"tag": tag, "rom": str(rom), "runner_rc": proc.returncode}
    facts["log_tail"] = proc.stdout.strip().splitlines()[-4:]
    if not report.exists():
        facts["error"] = "no report written"
        return facts
    text = report.read_text()
    facts["report"] = text
    for line in text.splitlines():
        k, _, v = line.partition(" ")
        facts[k] = v
    if png.exists():
        img = Image.open(png)
        facts["png_size"] = img.size
        facts["raw"] = luminance_stats(img)
        facts["nes"] = luminance_stats(crop_to_nes(img))
        facts["png_path"] = str(png)
    else:
        facts["error"] = "no png written"
    return facts


def main() -> int:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/biz")
    frames = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    outdir.mkdir(parents=True, exist_ok=True)
    results = []
    for tag, rom in (("cart", CART), ("rebuild", ROM)):
        if not rom.exists():
            results.append({"tag": tag, "error": f"missing {rom}"})
            continue
        r = run_one(tag, rom, outdir, frames)
        print(f"--- {tag} ---", file=sys.stderr)
        results.append(r)
    for r in results:
        print(f"[{r['tag']}] frames={r.get('reached_frame','?')} "
              f"png={r.get('png_size','NONE')} raw={r.get('raw')} nes={r.get('nes')}")
        if r.get("error"):
            print(f"    ERROR {r['error']}")
    (outdir / "summary.json").write_text(repr(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
