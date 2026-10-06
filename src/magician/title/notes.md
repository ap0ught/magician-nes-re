# The title screen — what this is, and where it came from

`nametable.txt` and `attributes.txt` are the screen, and **both are Eurocom's
own bytes**: `vendor/Magician-NES/DAT/TIT.DAT` expanded by `tools/datcodec.py`
into a readable grid, one row per line. `asm/build.py` packs them back with the
same token (`$E0`) on every build and asserts the result is byte-identical to
`DAT/TIT.DAT`, so this directory is a view of the shipped asset rather than a
reconstruction of it. `src/magician/TITLE.SRC` emits the packed form at `titdat`.

Read it with `tools/scenerender.py ours out.png`, which draws the grid.

## What changed on 2026-10-05, and why

This grid used to be hand-authored. The reasoning was recorded and it was
reasonable *given what was being measured*: every "the title data is absent from
the source" measurement had been taken against the release, and the release
really does not contain `TIT.DAT`. So the artwork was reproduced by observing
the release and guessing at the rest.

Against **beta1** -- the build this source came from, dated 1990-03-02, whose
`TIT.DAT` is byte-identical to `vendor/Magician-NES/DAT/TIT.DAT` -- the
hand-authored grid was wrong. In BizHawk at frame 60:

| what | hand-authored | authentic |
|---|---|---|
| nametable `nt0` | 928/1024 (90.6%) | **1024/1024** |
| all of CIRAM | 4000/4096 | **4096/4096** |
| palette | 32/32 | 32/32 |
| attributes | 64/64 | 64/64 |
| pattern table in use | 8192/8192 | 8192/8192 |

The hand-authored version was wrong across the whole of the bottom three tile
rows: Beta 1 has real tiles there (`$B0 $93 $94 $95 $96 $B1 $B2 $B3 $B4 $B5
$B6 $B7 $9A $B8 ...`) and the reconstruction had a solid `$08` backdrop. Every
other row, and every palette and attribute byte, had been right already.

Nothing else needed to change. The packing pipeline, the token, the 314-byte
budget against `pwdat`, the read-back through the assembled image and the
`tools/titletest.py` fail-check gate were all already correct -- the architecture
was sound and only the content was invented.

## What still differs, and it is not the screen

The rendered frame is **64.87%** identical to Beta 1's, even though every byte of
PPU-visible title state matches. The top 60 scanlines are 100% identical; the
first difference is scanline 88, and from there our picture is offset by exactly
**one scanline** upward relative to Beta 1's.

That is a raster-split timing difference, not a picture difference: something
writes a scroll or PPUCTRL value at a slightly different scanline, and everything
below that line moves with it. `tools/bizhawk/title.lua` dumps CIRAM, PALRAM and
CHR but not OAM or the PPU's scroll latches, so this measurement cannot yet say
which write is early. See README.md for what is known and what is not.
