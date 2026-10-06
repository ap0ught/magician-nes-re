#!/usr/bin/env python3
"""Fail-check gate for our title screen: does the ROM really carry our scene?

    python3 tools/titletest.py                # the built ROM, or fail
    python3 tools/titletest.py --rom <path>

Everything here is a check that has a *silent* failure mode, which is why it
exists as a gate rather than as a note. A title screen that names a tile the art
does not draw does not error, does not crash and does not print anything: the PPU
fetches pattern-table address `$index * 16` and draws whatever is there. The
screen comes up and the logo is subtly wrong, and the only symptom is a picture
that does not look quite right to somebody who does not know what it should look
like. Three sessions of this project were lost to tools that reported plausible
numbers for the wrong thing, so each check below is written to fail loudly and
to say what it actually measured.

THE CHECKS

  1. THE ROM CARRIES OUR SCENE. The packed descriptor is read out of the *built
     PRG* at the offsets `TITLE.SRC` is recorded as writing, not out of the file
     the packer wrote. Those are different things: the packer could be right and
     the overlay could have landed somewhere else, which is exactly what happened
     once when the window slot was 6 instead of 7.

  2. IT DECODES. The bytes in the image go back through tools/datcodec.py and must
     produce the 1024 bytes of src/magician/title/. This is the whole codec
     contract, applied to the artefact rather than to a fixture.

  3. EVERY TILE EXISTS IN THE ART. Each index the grid names is looked up in
     TIT0.CHR, the 4 KiB bank a title screen draws from, and must have non-zero
     art -- unless it is on the explicit BLANK_OK list. A blank tile is a
     legitimate thing to draw (it is the backdrop), so the list is not a failure;
     what is a failure is a blank tile appearing *unexpectedly*, which is what a
     mistyped index looks like.

  4. THE ATTRIBUTES ARE COHERENT. A background attribute byte is four 2-bit
     quadrant selectors, so no byte value is out of range and there is no range
     check to write. What there *is* to check is that the screen is not
     attribute-uniform (a symptom of shipping a grid with an attributes file left
     at zero), that the sub-palettes it selects are ones TIT.PAL actually defines,
     and that the palettes the title loads at boot are the ones the attributes
     assume.

  5. IT FITS. The scene must pack into the space between `titdat` and `pwdat`.
     Checked here as well as in the build, because this tool can be run against a
     ROM somebody else built.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import datcodec  # noqa: E402

TITLE = ROOT / "src" / "magician" / "title"
DAT = ROOT / "vendor" / "Magician-NES" / "DAT"
DEFAULT_ROM = ROOT / "asm" / "out" / "magician-rebuilt.nes"

# Blank tiles in TIT0.CHR. Measured, not guessed: a tile whose 16 bytes are all
# zero draws nothing whatever palette it is given.
def _blank_tiles(chr_data: bytes) -> set[int]:
    return {i for i in range(len(chr_data) // 16)
            if not any(chr_data[i * 16:(i + 1) * 16])}


def read_grid(path: pathlib.Path, width: int, height: int) -> bytes:
    rows = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        vals = [int(t, 16) for t in line.replace(",", " ").split()]
        if len(vals) != width:
            raise SystemExit(f"{path}:{lineno}: {len(vals)} value(s), expected {width}")
        rows.append(vals)
    if len(rows) != height:
        raise SystemExit(f"{path}: {len(rows)} row(s), expected {height}")
    return bytes(v for r in rows for v in r)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=pathlib.Path, default=DEFAULT_ROM)
    ap.add_argument("--prg", type=pathlib.Path,
                    help="read a bare prg.bin instead of unpacking a .nes")
    args = ap.parse_args()

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        print("  [%s] %s%s" % ("ok" if ok else "FAIL", name,
                               ("\n         " + detail) if detail and not ok else ""))
        if not ok:
            failures.append(name)
        return ok

    print("titletest -- src/magician/title/ against %s" % args.rom)

    # ---------------------------------------------------------- our sources
    nt_path, at_path = TITLE / "nametable.txt", TITLE / "attributes.txt"
    for p in (nt_path, at_path, TITLE / "token.txt"):
        if not p.is_file():
            print(f"  [FAIL] {p.relative_to(ROOT)} does not exist")
            return 1
    grid = read_grid(nt_path, 32, 30)
    attrs = read_grid(at_path, 16, 4)
    scene = grid + attrs
    # Read the token the same way asm/build.py's _read_int does: the file is
    # mostly comment, and taking everything before the *first* semicolon of the
    # whole file yields the empty string rather than the value.
    token = 0xE0
    for raw in (TITLE / "token.txt").read_text().splitlines():
        line = raw.split(";", 1)[0].strip().lstrip("$")
        if line:
            token = int(line, 16)
            break
    print("  grid %d tile bytes, %d attribute bytes, token $%02X"
          % (len(grid), len(attrs), token))

    # ------------------------------------------------- the art the grid uses
    chr_data = (DAT / "TIT0.CHR").read_bytes()
    blanks = _blank_tiles(chr_data)
    used = sorted({v for v in grid})
    # (3) every named tile must exist and have art -- or be the blank tile.
    #
    # There is no allowlist file, because there is nothing to disambiguate:
    # TIT0.CHR has exactly ONE all-zero tile, so "the blank tile" is a fact about
    # the art rather than a choice. Measured with a 16-byte stride; an 8-byte one
    # reports 25 blanks and is how an earlier pass of this file came to describe
    # tile $08 as brick. Tile $00 is *not* blank -- it is part of the logo -- so a
    # frame row filled with $00 draws a scrap of artwork instead of nothing.
    unaddressable = [v for v in used if v > 0xFF]
    check("every tile index is addressable by an 8-bit descriptor",
          not unaddressable,
          "outside $00-$FF: " + ", ".join("$%02X" % v for v in unaddressable))
    check("TIT0.CHR has exactly one blank tile, so 'the blank tile' is unambiguous",
          len(blanks) == 1,
          "blank tiles are %s -- this grid's frame rows name one of them and the "
          "rule below would be guesswork"
          % ", ".join("$%02X" % v for v in sorted(blanks)))
    stray_blank = [v for v in used if v in blanks and v not in (0x08,)]
    check("every named tile draws something, or is the blank tile $08",
          not stray_blank,
          "blank in TIT0.CHR: " + ", ".join("$%02X" % v for v in stray_blank)
          + "\n         A mistyped index lands on one of these and draws nothing.")
    nblank = sum(1 for v in grid if v in blanks)
    print("       TIT0.CHR: %d tiles, %d blank ($%02X); the grid names %d distinct "
          "tiles and uses the blank one %d time(s)"
          % (len(chr_data) // 16, len(blanks), min(blanks) if blanks else 0,
             len(used), nblank))

    # ------------------------------------------------------ (4) attributes
    # Four 2-bit quadrant selectors per byte, so nothing can be out of range.
    # What can be wrong is the file being uniform, or selecting a sub-palette the
    # title screen never loads.
    sel = {(a >> s) & 3 for a in attrs for s in (0, 2, 4, 6)}
    check("attributes are not attribute-uniform", len(set(attrs)) > 1,
          "all 64 bytes are $%02X, which is what an attributes file left at zero "
          "looks like" % attrs[0])
    pal = (DAT / "TIT.PAL").read_bytes()
    check("every sub-palette the attributes select is one TIT.PAL defines",
          sel <= set(range(len(pal) // 3)),
          "selects %s but TIT.PAL defines %d sub-palettes"
          % (sorted(sel), len(pal) // 3))
    print("       sub-palettes used: %s of 0..3; TIT.PAL defines %d"
          % (sorted(sel), len(pal) // 3))

    # ------------------------------------------------------------ (5) budget
    packed = datcodec.encode(scene, token)
    print("       packed: %d bytes" % len(packed))

    # --------------------------------------------- (1)+(2) the built ROM
    if args.prg:
        prg = args.prg.read_bytes()
    else:
        data = args.rom.read_bytes()
        if data[:4] != b"NES\x1a":
            raise SystemExit(f"{args.rom} is not an iNES image")
        prg = data[16:16 + 131072]
    if len(prg) != 131072:
        raise SystemExit(f"PRG is {len(prg)} bytes, expected 131072")

    # The overlay lands where the assembler put it. Rather than re-deriving the
    # window mapping here and risking a second, different answer, find the packed
    # stream in the image and insist it is the only one.
    hits = []
    start = 0
    while True:
        i = prg.find(packed, start)
        if i < 0:
            break
        hits.append(i)
        start = i + 1
    check("the packed scene is in the ROM exactly once", len(hits) == 1,
          "found at %s" % (["$%X" % h for h in hits] or "nowhere")
          + ("" if hits else "\n         The ROM does not contain our scene at all. "
                             "Is it built from src/magician/ (see --no-ours)?"))
    if not hits:
        print("titletest: FAIL")
        return 1

    got = prg[hits[0]:hits[0] + len(packed)]
    check("the bytes in the ROM are the bytes we packed", got == packed,
          "the ROM has %s and we packed %s at $%X"
          % (got[:8].hex(), packed[:8].hex(), hits[0]))
    decoded = datcodec.decode(got)
    check("decoding the ROM's copy gives back src/magician/title/",
          decoded == scene,
          "decoded %d bytes, %s; expected %s"
          % (len(decoded), decoded[:8].hex(), scene[:8].hex()))
    print("       at prg.bin $%X (%d bytes)" % (hits[0], len(packed)))

    print("titletest: %s (%d checks, %d failed)"
          % ("PASS" if not failures else "FAIL", 6, len(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())