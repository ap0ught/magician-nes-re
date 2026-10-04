#!/usr/bin/env python3
"""Find the first frame at which the rebuild and the cartridge disagree.

    python3 tools/bizhawk/bisect.py <cartdir> <rebuildir>

Both directories are what tools/bizhawk/frames.lua wrote: one <region>_<frame>.bin per
frame per memory domain. The answer is the first frame whose bytes differ, per region,
not a count at some frame late enough to be already broken.

Regions are compared over their full dumped length, so the 131072-byte CHR domain is
compared whole and not truncated to the 8 KiB the mapper happens to use.

Two things this deliberately reports that a naive diff hides:

  * an all-one-value region compares equal no matter what it should contain, so the
    distinct-value count of each region at the divergence frame is printed next to the
    diff. "0 differ" on a region that is 8192 bytes of $FF is not agreement.
  * the first frame at which a region differs is not necessarily the first frame at
    which anything differs, so the earliest across all regions is reported too.
"""
from __future__ import annotations

import sys
from pathlib import Path

REGIONS = ("chr", "ciram", "wram", "ram", "oam", "pal")


def frames_in(d: Path) -> list[int]:
    out = []
    for f in d.glob("*_*.bin"):
        try:
            out.append(int(f.stem.rsplit("_", 1)[1]))
        except (IndexError, ValueError):
            continue
    return sorted(out)


def distinct(b: bytes) -> int:
    return len(set(b))


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    cart, reb = Path(sys.argv[1]), Path(sys.argv[2])
    common = sorted(set(frames_in(cart)) & set(frames_in(reb)))
    if not common:
        print("no frames in common")
        return 2
    lo, hi = common[0], common[-1]
    print(f"frames {lo}..{hi} in both ({len(common)} snapshots)")

    first: dict[str, int | None] = {r: None for r in REGIONS}
    rows: dict[int, dict[str, tuple[int, int]]] = {}
    for f in common:
        rows[f] = {}
        for r in REGIONS:
            a = (cart / f"{r}_{f:04d}.bin").read_bytes()
            b = (reb / f"{r}_{f:04d}.bin").read_bytes()
            if len(a) != len(b):
                print(f"  !! {r} frame {f}: length differs {len(a)} vs {len(b)} -- skipped")
                rows[f][r] = (-1, -1)
                continue
            n = sum(1 for x, y in zip(a, b) if x != y)
            rows[f][r] = (n, len(a))
            if n and first[r] is None:
                first[r] = f

    print()
    print(f"{'region':7s} {'len':>8s} {'first differing frame':>22s} {'bytes there':>12s}")
    for r in REGIONS:
        if first[r] is None:
            print(f"{r:7s} {rows[common[0]][r][1]:8d} {'never':>22s}")
        else:
            f = first[r]
            n, ln = rows[f][r]
            print(f"{r:7s} {ln:8d} {f:22d} {n:6d}/{ln:<6d}")
    known = [v for v in first.values() if v is not None]
    earliest = min(known) if known else common[0]
    print(f"\nearliest divergence in any region: frame {earliest}")

    # Content sanity at the divergence frame: an all-constant region that compares
    # equal proves nothing, so say what each region is made of.
    f = earliest
    print(f"\n--- what each region actually holds at frame {f} ---")
    print(f"{'region':7s} {'len':>8s} {'cart distinct':>14s} {'reb distinct':>13s} {'cart first16':>34s}")
    for r in REGIONS:
        a = (cart / f"{r}_{f:04d}.bin").read_bytes()
        b = (reb / f"{r}_{f:04d}.bin").read_bytes()
        print(f"{r:7s} {len(a):8d} {distinct(a):14d} {distinct(b):13d} {a[:16].hex():>34s}")

    print(f"\n--- per-frame differing-byte counts ---")
    hdr = "frame | " + " | ".join(f"{r:>9s}" for r in REGIONS)
    print(hdr)
    for f in common:
        cells = []
        for r in REGIONS:
            n, ln = rows[f][r]
            cells.append(f"{n:5d}/{ln:<5d}" if ln else "      n/a")
        print(f"{f:5d} | " + " | ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())