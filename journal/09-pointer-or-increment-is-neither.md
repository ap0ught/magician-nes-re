# 09 — "pointer or increment?" is neither, and the $08 field is the source working

2026-10-04. Branch `fix/boot-from-source`. The question this session was asked
was narrow: the rebuild's nametable 0 holds an unbroken tile counter, and the
game's software VRAM pointer is `t0`/`t1` with the increment in `t2`, so is the
pointer wrong or is the increment wrong?

**Neither.** And the reason is more useful than either answer would have been.

## The instrument was not measuring zero page

`tools/bizhawk/zp.lua` had never measured zero page. Its row loop was

```lua
for base, name in ipairs({ {0x00, "zp00"}, {0x20, "zp20"}, ... }) do
```

A generic `for` over `ipairs` assigns the **index** to the first control variable
and the **value** to the second. So `base` was 1..5, `name` was a Lua table (it
printed as `table: 0x55b51ca82ef0`), and the five rows read `$01-$20`,
`$02-$21`, `$03-$22`, `$04-$23`, `$05-$24` — 36 addresses, with `$25-$FF` never
touched once. It looked like a working dump: five rows of plausible hex, with a
range label that was also wrong.

What gave it away was the rows coming out shifted by one: row `$02-21` repeated
row `$01-20` minus its first byte. A row that is the previous row shifted by one
byte is a loop that is reading the wrong addresses, not a machine that is
misbehaving.

Fixed, plus two guards so it cannot recur quietly: the loop indexes rows
explicitly, every address read is recorded, and the run ends by **asserting
256/256** zero-page bytes were covered. A dump that reads 36 bytes now says
`coverage 36/256 FATAL` at the end instead of printing something plausible.

`run.sh` grew `MAGICIAN_DONE`, a sentinel the expected output must reach. Without
it the post-verdict wait returned on the first non-empty byte, so a 96-frame dump
was compared one frame in. That is not hypothetical: the first version of
`sweep.sh` omitted `MAGICIAN_EXPECT`, so `run.sh` returned immediately and the
*next* job's `pkill` killed an EmuHawk that was two frames from finishing. The
first sweep produced two frames per ROM and said nothing about it.

## The real zero-page diff, frames 0-95, all 256 bytes, named

95 of 96 frames differ. The first difference is frame 1, and it is one byte.

| addr | symbol | rebuild | cartridge | frames |
|---|---|---|---|---|
| `$08` | `p0` — copy of PPU register 0 | `08`, then `88` from f3 | `00` | 1-95 |
| `$09/$0A` | `nvec` — NMI vector | `$AD5E` | `$83AD` | 2-95 |
| `$0B/$0C` | `ivec` — IRQ vector | `$D2DD` | `$FFFF` | 2-10 |
| `$0D-$12` | `rn` — random seed | `A6 17 9B F2 FF FF` | `FF FF 00 FB 00 20` | 2-95 |
| `$13/$14` | `t0`/`t1` | `E1 AC` | `3F 00` | 2-95 |
| `$15` | **`t2`** | `9B` | `FE` | **2 only** |
| `$16/$17` | `t3`/`t4` | `00 E0` | `FE FF` | 3-95 |
| `$21/$22` | `te`/`tf` | `FF FF` | `F5 AF` | 2-95 |
| `$25/$26` | `t12`/`t13` | `FF FF` | `00 80` | 2-95 |
| `$27-$29` | `ma`/`mb`/`mc` — multiply vars | `00 80 FF` | `FF 01 7E` | 2-95 |
| `$2A/$2B` | `stk0`/`stk1` — DMA stack pointers | `01 7E` | `FF 07` | 2-95 |
| `$2C` | `nmiflag` | `FF` | `00` | 2-95 |
| `$2D` | `bnksel` | `07` | `00` | 2-95 |
| `$40/$41` | `twin` | `00 00` | `01 FF` | 4-95 |
| `$43/$44` | `fadevec`/`fadedel` | `01 FF` | `00 00` | 11-95 |
| `$56` | `second` | `08` | `00` | 3-95 |
| `$5A` | `food` | `00` | `08` | 3-95 |
| `$67` | `mapbnk0` | `38` | `3F` | 3-95 |

**`t2` — the increment the question named — differs at exactly one frame out of
96 and never again.** It is not the fault. `t0`/`t1` differ from frame 2 onward,
but they are `general-purpose temporary storage` ("general-purpose" is the
source's own comment): they are scratch at whatever moment the snapshot is taken,
and a snapshot of scratch is not a pointer.

## `p0` is the first difference, and it is not the cause either

`p0` is documented in `X0.PDS:370` as `copy of PPU register 0 ($2000)`. Ours
ends up `$88`, the cartridge's stays `$00`. The source's `reset` (`X7.PDS:926`)
does `sta p0` with A=`$08`; the release's `reset` at the same `$F9D1` does
`stx p0` with X=`$00` (`X7.PDS` never grew that change). Both do
`lda #$08 / sta $2000` at `$F9C1`, so `$2000` is `$08` either way and only the
*shadow* differs.

That looked like the increment, because `$2000` bit 3 is the VRAM address
increment and `$88` has it set while `$00` does not — and the game's DMA bursts
write `count` bytes through `$2007` with no re-aiming, so the increment decides
whether a burst is contiguous.

**Measured, not argued.** Patched the built ROM's `$F9CD` from `85 08` (`sta p0`)
to `86 08` (`stx p0`), so `p0` becomes `$00` exactly as the cartridge has it, and
ran both sides to frame 60 through BizHawk with `NES/SaveRAM/` cleared:

| | baseline rebuild | with `stx p0` |
|---|---|---|
| ciram first differing frame | 2 | 2 |
| ciram differing bytes there | 661 | 661 |
| ciram at frame 3 onward | 969 | 969 |
| ram first differing frame | 1 | 2 |

Identical. `p0` is real, it is the first byte to differ, and it makes no
difference to the picture. The one thing it changed is the `ram` domain's
first-differing frame moving 1 → 2, which is battery-backed PRG RAM and not the
nametable.

## What the `$08` field actually is

The rebuild's nt0 holds **158 bytes of `$08`** and the cartridge's holds **3**.
`$08` is the value `reset` leaves in `A`, and it is also what the game's own
title data asks for.

`tools/unrun.py` transcribes `unrun` (`X5.PDS:111-127`) literally and runs it
offline over the source's own data:

    TIT.DAT   314 compressed bytes -> 1024 screen bytes   distinct 1   08 x 1024
    PW.DAT    292 compressed bytes -> 1024 screen bytes   distinct 1   20 x 1024
    PAN.DAT    96 compressed bytes ->  256 screen bytes   distinct 1   20 x 256

The length fields decode to 1024, 1024 and 256 — 960 tiles plus 64 attributes for
a nametable, and 256 for a panel — which is too exact to be a modelling mistake.
**The source's title data is a solid fill of tile `$08`.** So the field of `$08`
in the rebuild's nametable is `unrunscn` doing exactly what the source says, and
four sessions have been reading a correct behaviour as a corruption.

The ascending run that follows it is not that either. It is the *level* build
drawing over the fill, and it is present on **both** sides: the cartridge's nt0
rows 1-8 are `00 00 01 02 03 ... 1C 00 00 1D 1E 1F ...` — the same unbroken
ascending run. Both images have a nametable full of an ascending counter. The
difference is 128 bytes of `$08` at the front and `$00` holes where the
cartridge has them, not the counter.

## The data is not in the cartridge

| file | source bytes | in the cartridge |
|---|---|---|
| `DAT/TIT.DAT` | 314 | **not present anywhere in the 131 072-byte PRG** |
| `DAT/PW.DAT` | 292 | **not present** |
| `DAT/MUS/MUS.MUS` | 10 156 | **not present** |
| `DAT/PAN.DAT` | 96 | present at file `$0F1E6`, identical for 16 bytes then different |

This is the finding that changes the framing. The release's title scene, password
scene and music are **not in the source at all** and are not recoverable from it
by any amount of correct assembly. The February 1990 tree carries an earlier
revision of the data; the 1991 cartridge carries a later one. So:

* the picture cannot be made to match the cartridge from this source's data;
* this is **not** a class-(b) manifest fill. Class (b) is "the release has bytes
  the source lacks and the same address". Here the bytes are not at the address
  in any form — a fill would have to invent them.

## The release's `unrun` is a different routine

Located by opcode signature (`tools/findsig.py`), not by guessing an address.

| | rebuild | cartridge |
|---|---|---|
| routine | `$DC77` | `$DB08` |
| pointer | `sta $13 / stx $14` | `sta $21 / stx $22` |
| token / length | `$15/$16/$17` | `$14/$15/$16` |
| `$2007` write | plain | gated on `bit $06D8` |
| extra calls | none | `jsr $F38E`, `jsr $F946` (`emptydma`) |

Same shape, later revision, different zero page. The release's pushes through
the DMA stack; the source's writes `$2007` directly. Two dumps of the same
algorithm cannot be expected to produce the same screen.

## Instrument fixes

* `zp.lua`: the `ipairs` bug, plus a 256/256 coverage assertion.
* `run.sh`: `MAGICIAN_DONE`, so a dumping script is not compared truncated.
* `sweep.sh`: runs ROMs serially against one core and waits for the sentinel.
* `tools/bizhawk/bisect.py` → `regionbisect.py`. A file called `bisect.py` in
  that directory shadows the standard library for anything run from it, and
  `from PIL import Image` died with an ImportError about `bisect` that had
  nothing to do with images.
* `ntdiff.py` / `ntgrid.py` / `ntanalyse.py`: the nametable difference as
  arithmetic. `ntdiff` separates the 960 tile bytes from the 64 attribute bytes,
  because "all 969 differing bytes are in nt0" is a claim about *where*.
* `dataincart.py` / `whereis.py`: is a symbol's data actually where the symbol
  says, and is that data in the cartridge.
* `unrun.py`: the decompressor, offline.
* `pixmatch.py`: both pictures, one BizHawk session each, serially, with the
  overall match and per-region rows.

## Where this leaves the picture

Unchanged, and now measured rather than assumed. **44.3 %** of pixels identical
at frame 60 (25 408 / 57 344, BizHawk's own screenshots, `NES/SaveRAM/` cleared
both sides). Per region: rows 0-3 88.2 %, rows 4-11 44.8 %, rows 12-19 58.1 %,
rows 20-23 44.8 %, rows 24-29 29.5 %; left half 52.8 %, right half 35.8 %.

Next step, in order:

1. **Stop treating the ascending counter as the bug.** It is on both sides.
2. Find what draws over the `$08` fill. It is the level build, it reads a block
   map, and both sides read an ascending table from slot 5 — ours at offset
   `$1471`, the cartridge's at `$14CE`. The placement of X5 within slot 5 is
   therefore still wrong, and that is where the remaining CPU-side divergence
   lives.
3. `pntslot`/`bnk` for the level data groups (`b = $8`, `$9`, …) has never been
   checked against the cartridge's `$8000` writes.