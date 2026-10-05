# The title screen — what this is, and where it came from

`nametable.txt` and `attributes.txt` are the screen. `asm/build.py` packs them
with `tools/datcodec.py` into `asm/out/ours/TIT.DAT`, and `src/magician/TITLE.SRC`
incbins that over `titdat`. Nothing else is involved. Read this file with
`tools/scenerender.py ours out.png`, which draws the grid.

## The screen renders, and it is ours

Verified end to end on the built ROM, at frame 60:

| check | result |
|---|---|
| our packed scene is in the ROM, exactly once | at `prg.bin $EBA7`, 281 bytes |
| decoding the ROM's copy gives back this grid | 1024 bytes identical |
| **the live PPU nametable at frame 60 vs this grid** | **960/960 tile bytes, 64/64 attribute bytes** |
| BizHawk screenshot of our build at frame 60 | `/tmp/opencode/shots/ours-f60.png` — logo, brick, figures, moon |
| pixel match against the cartridge, frame 60 | 40.8% overall; per region below |

That third row is the one that matters. It is exact bytes from the emulator's own
CIRAM domain, not a pixel comparison, so it says the screen on the hardware is
byte-for-byte the grid in this directory — the grid, through our packer, through
our overlay, through Eurocom's own `dotitle` and `unrun`, into the PPU.

Per-region pixel match against the cartridge (`tools/bizhawk/pixmatch.py`, frame 60):

| region | match |
|---|---|
| rows 0-3 | 88.2% |
| rows 4-11 | 44.8% |
| rows 12-19 | 58.1% |
| rows 20-23 | 44.8% |
| rows 24-29 | 29.5% |
| left half | 52.8% |
| right half | 28.8% |
| overall | **40.8%** (23394/57344 pixels) |

The top rows match well because both screens open with the same blank band. The
bottom matches worst because that is where the cartridge's two copyright lines
are, and our CHR bank has no tiles for them.

## What we changed, and why

Eurocom's own `TIT.DAT` decodes to 1024 bytes: a 128-byte run of tile `$08` —
four whole blank rows — then an ascending ramp of tile indices through about
`$F2` down to row 28. We changed **96 bytes**: rows 0 and 27, 28, 29 are now the
blank tile, so the picture is framed.

That is derived by observation. A BizHawk run of the cartridge at frame 60 has
row 0 and rows 27-29 entirely blank and the picture in rows 1-26; Eurocom's data
runs art from row 0 to row 28, so its picture bleeds off the top and bottom edges
of the screen.

We did **not** slide the artwork up three rows to start it at row 1 like the
cartridge's. The attribute table is a fixed grid of 4x4 blocks keyed to *screen*
position, not to the picture, so moving the artwork three rows up moves it off its
own colour regions and recolours the logo. The blank band at the top is the price
of keeping the two aligned.

## Two things that were wrong before this was measured

* **The title screen was never missing from the source.** An earlier session
  measured `TIT.DAT` as "a solid fill of tile `$08`" and concluded the screen had
  to be written from scratch. That was a bug in that session's transcription of
  `unrun`, not a property of the data: it modelled the pointer advance as
  `t0 += y` when the epilogue's `sec / tya / adc t0` computes `t0 += y + 1`, and
  it re-tested the token on every byte of a run when `dex / bne` branches back to
  the `sta` and not to the `lda`. Both are fixed in `tools/datcodec.py`, which
  round-trips Eurocom's three `.DAT` files byte-identically. The data was there.
* **Tile `$08` is blank, and tile `$00` is not.** TIT0.CHR has exactly one
  all-zero tile and it is `$08`; `$00` is part of the logo. An earlier census in
  this session used an 8-byte stride instead of 16 and reported 25 blank tiles
  and "brick" where there is a blank band. `tools/titletest.py` now asserts the
  blank tile is unambiguous, and caught this.

## Why it is not the cartridge's screen

The two are different revisions of the artwork, and no amount of authoring closes
that. Measured:

* `TIT0.CHR` / `TIT1.CHR` / `TIT2.CHR` share 87% / 78% / 54% of their 24-byte
  slices with the cartridge's.
* The cartridge's packed title descriptor has no 24-byte slice in common with
  Eurocom's anywhere in PRG or CHR, so it is not a re-pack of the same grid.
* The two live nametables at frame 60 agree in 8 of 960 bytes.

So the composition here is the one our art supports — the MAGICIAN logo, the brick
field, the figures, the moon — and the cartridge additionally has
"- EUROCOM ENTERTAINMENT SOFTWARE PRESENTS -" across the top and
"LICENSED BY Nintendo / (C)COPYRIGHT TAXAN USA CORP. 1990" along the bottom.
Those tiles are not in our CHR bank. Naming them anyway is the silent failure:
the PPU fetches pattern-table address `$index * 16` and draws whatever is there,
so the screen comes up and the logo is subtly wrong.

## The budget

314 bytes, exactly, between `titdat` and `pwdat` (`X7.PDS:997-999` lays the three
scene blobs end to end). Ours packs to 281, so there are 33 spare. The build
refuses a scene that does not fit and reads the finished image back to confirm;
`tools/titletest.py` checks it independently.

## Rules for editing

* Tile indices are 8-bit and address `TIT0.CHR`, the first 4 KiB bank of the title
  set. `$00`–`$FF` only.
* `$08` is the blank tile. `$00` is not.
* The token byte (`token.txt`, `$E0`) must never appear as a literal tile index;
  `datcodec.encode()` refuses if it does, because there is no escape for it.
* Do not slide the artwork vertically without redoing the attributes.
* Rows 28-29 sit at `$2380-$23BF` and are ordinary tile bytes, so they are
  displayed.
* Run `python3 tools/titletest.py` after any edit. It is the gate for the failure
  that produces no error at all.