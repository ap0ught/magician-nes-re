# The title screen — what this is, and where it came from

`nametable.txt` and `attributes.txt` are the screen. `asm/build.py` packs them
into `asm/out/ours/TIT.DAT` with `tools/datcodec.py`, and `src/magician/TITLE.SRC`
incbins that over `titdat`. Nothing else is involved.

## Current state: Eurocom's own screen, re-derived by us

The grid here is what `tools/datcodec.py` decodes out of Eurocom's `TIT.DAT`, and
the build packs it back into Eurocom's 314 bytes exactly. With the grid in this
state the PRG is byte-identical to a build made before `src/magician/` existed.

That is deliberate, and it is how the wiring was proved inert rather than assumed
to be: a live module, assembled by the real assembler, overwriting a real label,
and no change in the output. See `../README.md`.

## What the decoded screen turned out to be

Decoding it says something the earlier sessions got wrong, so it is worth writing
down. `TIT.DAT` is **not** missing, and it is **not** a screen of brick:

* The first record is `E0 7C 08` — a run of 128 bytes of tile `$08`, which is
  exactly four whole 32-tile rows. The top of the screen is brick.
* Rows 4-12 and 13-28 are an **ascending tile counter**: row 4 is
  `00 01 02 03 …`, row 13 starts `00 01 02 03` again, and both climb to about
  `$DF`. The title screen's art is laid out as two 13-row ramps of consecutive
  tile indices. The logo is not drawn from a picture; the tile indices *are* the
  picture, and the CHR bank supplies the shapes in index order.

The "solid fill of tile `$08`" that an earlier session measured was a bug in that
session's transcription of `unrun`, not a property of the data. See the header of
`tools/datcodec.py` for the two mistakes it made — the pointer advance dropped the
carry `sec` supplies, and the inner loop re-tested the token the 6502 skips.

## The budget

314 bytes, exactly, between `titdat` and `pwdat`. Not a suggestion — the build
fails if the packed scene is longer, and reads the finished image back to confirm
the bytes there are ours.

## Rules for editing

* Tile indices are 8-bit and address `TIT0.CHR` (the three title CHR banks are
  placed contiguously at 4 KiB banks 14/15/16). `$00`–`$FF` only.
* The token byte (`token.txt`, `$E0`) must never appear as a literal tile index;
  `datcodec.encode()` refuses if it does, because there is no escape for it.
* Rows 29 and 0 are the vertical mirror of rows 28 and 1. The screen does not
  scroll, so this only matters for the border.
* 25 of `TIT0.CHR`'s 256 tiles are blank. A grid may legitimately use them; a grid
  that uses one *where it meant to draw art* is the silent failure, and
  `tools/titletest.py` is the gate for that.