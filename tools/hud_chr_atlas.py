#!/usr/bin/env python3
"""Render original NES 2bpp CHR assets as dependency-free PPM tile atlases.

This is an asset discovery tool for Issue #2, NOT an in-ROM HUD renderer.
Usage: python3 tools/hud_chr_atlas.py --out /tmp/magician-hud-chr
Reads vendor/Magician-NES/DAT/*.CHR; does not modify vendor or the ROM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAT = ROOT / "vendor" / "Magician-NES" / "DAT"
# Deliberately grayscale: these are pixel indices, not the game's live palettes.
COLORS = ((16, 16, 16), (100, 100, 100), (185, 185, 185), (255, 255, 255))


def decode_tile(data: bytes) -> list[list[int]]:
    """Decode exactly one NES planar 8x8, 2-bit tile (16 bytes)."""
    if len(data) != 16:
        raise ValueError(f"expected 16 bytes, got {len(data)}")
    return [
        [((data[y] >> (7 - x)) & 1) | (((data[y + 8] >> (7 - x)) & 1) << 1)
         for x in range(8)]
        for y in range(8)
    ]


def atlas_ppm(data: bytes, columns: int = 16, scale: int = 2) -> bytes:
    if not data or len(data) % 16:
        raise ValueError("CHR data must be nonempty and a multiple of 16 bytes")
    if columns < 1 or scale < 1:
        raise ValueError("columns and scale must be positive")
    tiles = [decode_tile(data[i:i + 16]) for i in range(0, len(data), 16)]
    rows = (len(tiles) + columns - 1) // columns
    width, height = columns * 8 * scale, rows * 8 * scale
    out = bytearray(f"P6\n{width} {height}\n255\n".encode("ascii"))
    for y in range(height):
        tile_row, tile_y = divmod(y // scale, 8)
        for x in range(width):
            tile_col, tile_x = divmod(x // scale, 8)
            index = tile_row * columns + tile_col
            out.extend(COLORS[tiles[index][tile_y][tile_x]] if index < len(tiles)
                       else COLORS[0])
    return bytes(out)


def scan(dat: Path, out: Path, columns: int = 16, scale: int = 2) -> dict:
    if not dat.is_dir():
        raise FileNotFoundError(f"missing DAT directory: {dat}")
    files = sorted(p for p in dat.iterdir() if p.suffix.upper() == ".CHR")
    if not files:
        raise FileNotFoundError(f"no .CHR assets in {dat}")
    out.mkdir(parents=True, exist_ok=True)
    inventory = {}
    for path in files:
        raw = path.read_bytes()
        if not raw or len(raw) % 16:
            raise ValueError(f"{path}: invalid NES 2bpp tile data length {len(raw)}")
        tiles = len(raw) // 16
        atlas = out / f"{path.name}.ppm"
        atlas.write_bytes(atlas_ppm(raw, columns, scale))
        inventory[path.name] = {
            "bytes": len(raw), "tiles": tiles,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "atlas": atlas.name,
            "tile_indexing": "zero-based within this CHR file, NOT runtime PPU tile ID",
        }
    result = {
        "purpose": "candidate filigree discovery; visual verification required",
        "warning": "CHR RAM is loaded dynamically; file-local indices do not establish live banks or palettes",
        "files": inventory,
    }
    (out / "inventory.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dat", type=Path, default=DAT)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--columns", type=int, default=16)
    ap.add_argument("--scale", type=int, default=2)
    args = ap.parse_args()
    result = scan(args.dat, args.out, args.columns, args.scale)
    print(f"Wrote {len(result['files'])} atlases and inventory.json to {args.out}")
    print("Inspect candidate border tiles visually, then trace runtime CHR RAM loads.")


if __name__ == "__main__":
    main()
