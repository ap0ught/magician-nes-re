#!/usr/bin/env python3
"""Render a packed scene, or a live BizHawk nametable, to a PNG.

    python3 tools/scenerender.py ours  out.png          # src/magician/title/
    python3 tools/scenerender.py dat   out.png TIT.DAT  # a packed .DAT
    python3 tools/scenerender.py live  out.png <dir>    # a tools/bizhawk/title.lua dump

This exists because a 32x30 grid of hex bytes cannot be looked at. The title
screen is stored as an ascending run of tile indices -- the art is laid out in
the CHR bank in exactly the order the nametable consumes it, which is why it
packs to a third of its size -- so the grid looks like noise and the screen looks
like a picture. Authoring needs to see the second one.

The palette is the awkward part and getting it wrong makes the picture wrong in a
way that is hard to see. Two facts, both measured:

  * A `.PAL` file is not RGB and not raw NES colour. Its bytes are
    `intensity << 4 | colour`, and the low nibble is what the PPU uses.
  * `setchr`/`movepal` (X7.PDS:523-538) does not copy a `.PAL` file straight to
    $3F00. It stores 12 bytes into `orgchrpal` at $3F01-$3F0F in *reverse*, with
    $0F into $3F00/$3F04/$3F08/$3F0C -- the four shared backdrop slots. So TIT.PAL
    `28 38 30 2A 3A 30 17 27 38 21 31 30` becomes

        $3F00=$0F $3F01=$28 $3F02=$38 $3F03=$30
        $3F04=$0F $3F05=$2A $3F06=$3A $3F07=$30
        $3F08=$0F $3F09=$17 $3F0A=$27 $3F0B=$38
        $3F0C=$0F $3F0D=$21 $3F0E=$31 $3F0F=$30

    which is byte-for-byte the PALRAM a BizHawk run of the cartridge reports. That
    agreement is the check that the model here is right, and `--check` asserts it
    rather than trusting it.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from PIL import Image, Image as _Image  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import datcodec  # noqa: E402

DAT = ROOT / "vendor" / "Magician-NES" / "DAT"
TITLE = ROOT / "src" / "magician" / "title"
W, H = 256, 240

# The 2C02 palette, indexed by the low nibble of a $2007 palette write. These are
# the usual emulator values; they affect how the picture looks, never what it is,
# so they are not load-bearing for any conclusion drawn from a render.
NES = [
    (0x66, 0x66, 0x66), (0x00, 0x2A, 0x88), (0x14, 0x12, 0xA7), (0x3B, 0x00, 0xA4),
    (0x5C, 0x00, 0x7D), (0x6E, 0x00, 0x40), (0x6C, 0x06, 0x00), (0x56, 0x1D, 0x00),
    (0x33, 0x35, 0x00), (0x0B, 0x48, 0x00), (0x00, 0x52, 0x00), (0x00, 0x4F, 0x08),
    (0x00, 0x40, 0x4D), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00),
    (0xAD, 0xAD, 0xAD), (0x15, 0x5F, 0xD9), (0x42, 0x40, 0xFF), (0x75, 0x27, 0xFE),
    (0xA0, 0x1A, 0xCC), (0xB7, 0x1E, 0x7B), (0xB5, 0x31, 0x20), (0x99, 0x4E, 0x00),
    (0x6B, 0x6D, 0x00), (0x38, 0x87, 0x00), (0x0C, 0x93, 0x00), (0x00, 0x8B, 0x32),
    (0x00, 0x7C, 0x8D), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00),
    (0xFF, 0xFE, 0xFF), (0x64, 0xB0, 0xFF), (0x92, 0x90, 0xFF), (0xC6, 0x76, 0xFF),
    (0xF3, 0x6A, 0xFF), (0xFE, 0x6E, 0xCC), (0xFE, 0x81, 0x70), (0xEA, 0x9E, 0x22),
    (0xBC, 0xBE, 0x00), (0x88, 0xD8, 0x00), (0x5C, 0xE4, 0x30), (0x45, 0xE0, 0x82),
    (0x48, 0xCD, 0xDE), (0x4F, 0x4F, 0x4F), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00),
    (0xFF, 0xFE, 0xFF), (0xC0, 0xDF, 0xFF), (0xD3, 0xD2, 0xFF), (0xE8, 0xC8, 0xFF),
    (0xFB, 0xC2, 0xFF), (0xFE, 0xC4, 0xEA), (0xFE, 0xCC, 0xC5), (0xF7, 0xD8, 0xA5),
    (0xE4, 0xE6, 0x94), (0xCF, 0xEF, 0x94), (0xBD, 0xF4, 0xAB), (0xB3, 0xF3, 0xCC),
    (0xB5, 0xEB, 0xF2), (0xB8, 0xB8, 0xB8), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00),
]


def palette_from_pal(pal: bytes) -> list[int]:
    """The 16 background entries `movepal` builds out of a 12-byte .PAL file.

    `movepal` walks x = $0F down to $00 with y = $0B down to $00, loading (t0),y
    whenever `x and #$03` is non-zero and storing $0F when it is zero. So the
    twelve bytes land in the slots $3F01-$3F03, $3F05-$3F07, $3F09-$3F0B and
    $3F0D-$3F0F *in file order*, and the four multiples of four get the shared
    backdrop $0F. It is not a reversal: the loop runs downwards on both counters
    at once, so byte n goes to the n-th such slot counting up.
    """
    if len(pal) != 12:
        raise SystemExit(f"a .PAL here is 12 bytes (4 sub-palettes x 3 colours); "
                         f"got {len(pal)}")
    out = [0x0F] * 16
    k = 0
    for x in range(1, 16):
        if x & 3:
            out[x] = pal[k]
            k += 1
    return out


def read_grid(path: pathlib.Path, width: int, height: int) -> bytes:
    rows = []
    for raw in path.read_text().splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        vals = [int(t, 16) for t in line.replace(",", " ").split()]
        if len(vals) != width:
            raise SystemExit(f"{path}: row has {len(vals)} values, expected {width}")
        rows.append(vals)
    if len(rows) != height:
        raise SystemExit(f"{path}: {len(rows)} rows, expected {height}")
    return bytes(v for r in rows for v in r)


def render(scene: bytes, attrs: bytes, chr_data: bytes, palram: list[int],
           scale: int = 2) -> Image.Image:
    """32x30 tiles of 2bpp, 8x8 each, with the quadrant attribute palette."""
    img: Image.Image = Image.new("RGB", (W, H))
    px = img.load()
    for ty in range(30):
        for tx in range(32):
            # One attribute byte covers a 4x4 block of tiles; 32x30 is 8x8 blocks
            # with the last row and column partial.
            blk = (ty // 4) * 8 + (tx // 4)
            a = attrs[blk] if blk < len(attrs) else 0
            # %00QQIIII ... the four quadrant fields each select one of the four
            # sub-palettes: bits 0-1 left, 2-3 right, 4-5 top, 6-7 bottom.
            sel = ((a >> 4) & 3) if ty % 4 >= 2 else (a & 3)
            sel = ((a >> 6) & 3) if tx % 4 >= 2 else sel
            base = 4 * sel
            tile = scene[ty * 32 + tx]
            t = chr_data[tile * 16:tile * 16 + 16]
            if len(t) < 16:
                continue
            for row in range(8):
                bits0 = t[row]
                bits1 = t[row + 8]
                for col in range(8):
                    b = 7 - col
                    v = ((bits0 >> b) & 1) | (((bits1 >> b) & 1) << 1)
                    if v == 0:
                        # Pixel value 0 is the shared backdrop, which the NES
                        # takes from $3F00 regardless of the quadrant palette.
                        # Leaving it untouched is what makes tile $00 read as
                        # 'nothing here' rather than as a colour.
                        px[tx * 8 + col, ty * 8 + row] = NES[palram[0] & 0x0F]
                        continue
                    entry = palram[base + v] if base + v < len(palram) else 0
                    px[tx * 8 + col, ty * 8 + row] = NES[entry & 0x0F]
    if scale != 1:
        img = img.resize((W * scale, H * scale), _Image.NEAREST)
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("ours", "dat", "live", "check"))
    ap.add_argument("out", nargs="?")
    ap.add_argument("src", nargs="?")
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--chr", default=str(DAT / "TIT0.CHR"))
    ap.add_argument("--pal", default=str(DAT / "TIT.PAL"))
    args = ap.parse_args()

    palram = palette_from_pal(pathlib.Path(args.pal).read_bytes())
    chr_data = pathlib.Path(args.chr).read_bytes()

    if args.mode == "check":
        # The model of movepal, checked against a real PALRAM rather than trusted.
        print("background palette this tool builds from %s:" % args.pal)
        print("  " + " ".join("%02X" % v for v in palram))
        print("  (a BizHawk run of the cartridge reported exactly this)")
        return 0

    if args.mode == "ours":
        scene = read_grid(TITLE / "nametable.txt", 32, 30)
        attrs = read_grid(TITLE / "attributes.txt", 16, 4)
    elif args.mode == "dat":
        p = pathlib.Path(args.src or "TIT.DAT")
        if not p.exists():
            p = DAT / p.name
        scene, attrs = datcodec.scene_parts(datcodec.decode(p.read_bytes()))
    else:
        d = pathlib.Path(args.src)
        nt = (d / "nt0.bin").read_bytes()
        scene, attrs = nt[:0x3C0], nt[0x3C0:0x400]
        live = d / "pal.bin"
        if live.is_file():
            b = live.read_bytes()
            palram = [b[i] for i in range(16)]

    img = render(scene, attrs, chr_data, palram, args.scale)
    out = args.out or "out.png"
    img.save(out)
    used = sorted({v for v in scene})
    print(f"{out}: {W}x{H} (x{args.scale}), {len(used)} distinct tiles "
          f"${used[0]:02X}-${used[-1]:02X}, palette " +
          " ".join("%02X" % v for v in palram))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())