# Provenance

Where every fact and every file in this repository came from, and which measurements back them.

## 1. The cartridge dumps on this machine

Two NES files named Magician exist under `/extdrive/backups/SHARE/roms/nes/`:

| file | size | body SHA1 | body CRC32 |
|---|---|---|---|
| `Magician (USA).nes` | 262160 | `bd806d7f7c318b8012433250ca10aa8387a962bb` | `91E2E863` |
| `Magician (USA) (Beta).nes` | 262160 | `2a0a444dae8b5b02f4e5f1b789e16356f3ab08f0` | `2D1FEE70` |

"Body" = the file with the 16-byte iNES header stripped. Both headers are **byte-identical**:

```
4e 45 53 1a  08 10  42 00  00 00 00 00  00 00 00 00
'NES'  \x1a  PRG=8 CHR=16  f6=0x42 f7=0x00
```

Decoded: 8 × 16 KiB PRG = 128 KiB, 16 × 8 KiB CHR = 128 KiB (CHR-ROM, not CHR-RAM — the count
is non-zero), mapper `(f7 & 0xF0) | (f6 >> 4)` = **4** (MMC3), battery-backed PRG RAM
(`f6 & 2`), no trainer, horizontal mirroring (`f6 & 1` = 0).

The two bodies differ by **23 545 bytes**. 16 + 131072 = 131088, so both files are header plus
body with nothing extra.

Both are the release-lineage title; `(Beta)` is a pre-release build. The three NESMagician dumps
that appear as *box art* under `roms/nes/boxarts/` (`Taxan (US) (proto)`, three betas) have no
ROM file on this machine.

**Decision: develop against `Magician (USA).nes`.** It is the retail cartridge, it is the one
that boots to the release title screen, and every address, source label and input file in this
repository describes it. The `(Beta)` dump is a separate revision: same header, different code.
The header alone cannot distinguish them, which is why the digest is pinned in source next to the
addresses instead of being inferred from the mapper.

## 2. The source

`vendor/Magician-NES` — git submodule, `https://github.com/ap0ught/Magician-NES`, pinned at
`bf653a4` ("Added source code"), which is itself a fork of
`https://github.com/tkcn568/Magician-NES`. Two commits, no tags, no releases.

Contents: eight `X?.PDS` (25–32 KB each), seven `.SRC`, `DAT/` (level data, palettes, CHR
artwork, sprites, music), `TXT/` (message text), `SENDG` (the CHR download script for the
development system).

The `.PDS` files are binary PDS 1.26 project containers, not text. Decoded by
`tools/pds_extract.py`; see that file for the container layout. The decode is mechanical:
the source text starts at offset `0x200` in every one of the eight files and runs to a short
binary footer, lines are terminated `CR NUL` rather than `CRLF`, and a lone `CR` is the editor's
soft wrap inside one logical source line. Decoded total: **13 104 logical lines** across the
eight banks.

## 3. Does the source build this cartridge? (measured, and no)

Eurocom's source is dated. Each `X?.PDS` header carries "Last update 14:12 on 02/03/90" and a
project version in the form `24.33`. The cartridge was released in February 1991.

Two measurements put the source and the retail cartridge apart, before any code is assembled:

- **The version string differs.** `x7.pds` ends its bank with

  ```
  org $fff0
  db "MAGIC1+"
  hex 25 02 90
  ```

  The retail cartridge has `4d 41 47 49 43 2d 2a 20` = **`"MAGIC-* "`** at `$FFF0`
  (PRG file offset `0x1FFF0`, the last 16 bytes of the last bank). `"MAGIC1+"` does not occur
  anywhere in the retail PRG.

- **X5's page-aligned tables land exactly where the source says.** `x5.pds` begins
  `org $c000` followed by `sql`/`sqh`/`cos` tables beginning `00 01 04 09 10 19 24 31 …`. That
  sequence is at PRG file offset **0x1C000**, which is the start of the 128 KiB image's final
  16 KiB bank. It also appears at `0x1C080`, because the source repeats the table.

So the bank layout, the assembly dialect and the data files are the real ones, and the retail
cartridge is a **later build of the same program**. The rebuild therefore produces a ROM that
differs from the retail cartridge in the changed code, and the difference is the interesting
artifact: it is a diff between a development build and the shipped one. Exact agreement is not a
target; a *characterised* difference is.

**Consequence for addresses:** label addresses are correct for code the source contains. Where
the retail cartridge differs, an address from this repository's label map must be checked against
the cartridge bytes before use. `crates/mag-core/src/rom.rs` pins the digest so a mismatch is
detected, never silently applied to another revision.

## 4. Address translation: CPU address is not file offset

With a 128 KiB PRG image the mapper's fixed bank is at PRG file offset `+$10000`.

MMC3 fixes `$C000-$FFFF` to 8 KiB slots 14 and 15. In a 128 KiB image those are PRG file offsets
`+$1C000` and `+$1D000` — so the whole `$C000-$FFFF` window is PRG file offset `0x1C000`, i.e.
**file offset = CPU address + `$8000`** for the fixed window, and the bank-selectable window at
`$8000-$BFFF` is `+$0`–`+$FFFF`. Measured: X5's `org $c000` table is at file `0x1C000`, not
`0xC000` and not `0x0`.

X7's bank constants are 8 KiB slot numbers (`b = $6` … `b = $d`, "samples always to bank $F"),
which is 16 KiB banks 3–7. Do not read them as 16 KiB bank numbers.

## 5. Files written here

| file | origin |
|---|---|
| `tools/pds_extract.py` | new, this repository |
| `asm/` (PDS-compatible assembler, cartridge build) | new, this repository |
| `crates/` | new, this repository; the CPU/PPU models start from the user's own MIT-licensed `~/code/z2rs` crates, adapted and recorded here when that happens |

Nothing else is copied. `roms/`, `pds-text/`, `pds-text/`, build output and captured frames are
generated and git-ignored.