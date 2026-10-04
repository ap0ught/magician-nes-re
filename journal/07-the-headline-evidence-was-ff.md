# 07 - The headline evidence of 06 was two arrays of $FF, and the first divergence is at frame 1

Dated 2026-10-03. Branch `fix/boot-from-source`. Follows
`journal/06-the-picture-is-wrong-in-vram.md`.

**The title screen still does not match. This entry does not fix it.** What it
does is throw out the two claims 06 was standing on, find the first frame the two
images disagree, and locate one real source-versus-release difference. Both PNGs
are saved under `shots/` (gitignored -- they contain cartridge-derived imagery).

## The picture, frame 60, BizHawk 2.11.1, quickerNES, unattended, cleared SRAM

| | cartridge | rebuild |
|---|---|---|
| pixels matching the other image | 25 408 / 57 344 (44.3%) | 25 408 / 57 344 (44.3%) |
| top ~40% | correct | correct |
| lower ~60% | correct | scrambled tiles |

Unchanged from 06: brick courses, Japanese text panels and the MAGICIAN logo all
read correctly in the rebuild; the lower 60% is tiles from unrelated artwork.

## RETRACTION 1: "work RAM 0 bytes differ" was two identical arrays of `$FF`

06's table says work RAM is `0 / 8192` different at frame 60 and frame 400, and
draws the conclusion "the game logic is correct; the damage is entirely on the
PPU output side". **That measurement read an unpopulated domain.**

`tools/bizhawk/frames.lua` now dumps six domains per frame. The core's `WRAM`
domain is 8192 bytes and it is **`$FF` for every byte** until frame 45:

    frame  0: non-$FF bytes in WRAM = 0 / 8192
    frame 30: non-$FF bytes in WRAM = 0 / 8192
    frame 45: non-$FF bytes in WRAM = 0 / 8192
    frame 50: non-$FF bytes in WRAM = 8192 / 8192

So "WRAM 0 bytes differ" was `$FF == $FF`. The real work RAM is the **`RAM`
domain, 2048 bytes**, and it is not identical: at frame 60, **1767 of 2048 bytes
differ**. OAM likewise: 06 says `0 / 256` differ at frame 60, and the correct
figure is **256 of 256**.

The conclusion "the CPU side is producing exactly the right results" does not
survive. The CPU side differs from frame 1.

## RETRACTION 2: "480 of 8192 CHR bytes differ" was 6% of the CHR

The `CHR` domain is **131072 bytes**, not 8192. 06 compared the first 8 KiB of it
and reported 480/8192 (5.9%) plus "the best constant offset explains 63 of 480,
which is chance". Both numbers describe a 6% sample.

Over the whole domain: **71 558 of 131 072 bytes differ (54.6%)**. `regionbisect.py` now
compares every region over its full dumped length, and prints the distinct-value
count beside every diff for exactly this reason -- a region that is 8192 bytes of
`$FF` compares equal to another 8192 bytes of `$FF`, so "0 differ" on it means
nothing.

## RETRACTION 3: the two builds do NOT allocate zero page differently

06 says "the cartridge has `t0` at `$21` there against `$13` here, `curchrpal` at
`0180` against `04B0`" and builds on that. **The layouts are identical.** The
cartridge contains our `setmmc3` byte for byte, and that routine names both the
absolute MMC3 shadow and the CHR base:

    ours    $DE23  A0 00     ldy #$00
    ours    $DE25  A6 67     ldx $0067      ; mapbnk0
    ours    $DE39  A6 02     ldx $0002      ; r2
    cartridge has the identical 16-byte run at CPU $DD19

`mapbnk0` is `$0067` in the cartridge and `r2..r5` are `$0002..$0005` in both. The
RAM domain confirms it at runtime: `$02..$07` hold the same values in both images
at every frame from 2 to 45.

This is the same failure as the `$9D6C` lead 06 already retracted: an address
resolved from the wrong image. The pattern is now three for three.

## What the CHR difference actually is, and why it is not fixable from source

Both ROMs are 128 KiB PRG + 128 KiB **CHR-ROM** (iNES header `08 10 42 00`), and
at frame 0 BizHawk's `CHR` domain equals each ROM's own CHR image byte for byte.
The cartridge's is `file[0x20010:]`; the rebuild's is `asm/out/chr.bin`.

The two CHR images have **identical byte multisets** -- 0 bytes present in one and
absent from the other -- but different content at the same offsets:

identical  59 514 / 131 072 (45.4%)
first difference at CHR offset $0454, tile 69: our tile and the cartridge's
differ in 7 of 16 bytes, all in bit 2 of four columns plus the whole second bit
plane -- the cartridge's is the *less* detailed of the two, as a shipped revision
against development artwork usually is. The dump is not quoted here because it is
cartridge-derived data and nothing downstream needs it.

Per file, the agreement degrades exactly where a revision change would sit: the
first eight files (`10.chr` .. `53.chr`, `su.chr`, `map.chr`) are byte-identical or
nearly so, and from `60.chr` at `$7000` onwards every file is partly different,
ending at 8% agreement for the sprite banks.

**This is a different revision of the artwork in the shipped cartridge, not a
packing order this build got wrong.** The earlier "8/32 pages, order is wrong"
reading was the 8 KiB sample again. It cannot be fixed from Eurocom's source, and
it must not be "fixed" by pasting cartridge bytes into `chr.bin`. It is class (d)
and the honest entry is that the CHR artwork is a known, accepted difference.

For the record, the game *does* write CHR at runtime: the cartridge's CHR domain
changes in 89 772 bytes between frame 45 and frame 46, and the rebuild's does not
change at all.

## Bisect: the first frame the two images disagree

`tools/bizhawk/regionbisect.py`, frames 0..90, both images, six domains:

| region | size | first differing frame | bytes there |
|---|---|---|---|
| chr | 131072 | **0** | 71 558 (the CHR-ROM revision, above) |
| ciram | 4096 | **2** | 661 |
| ram | 2048 | **1** | 1 |
| wram | 8192 | 46 | 8192 |
| oam | 256 | 46 | 256 |
| pal | 32 | 15 | 24 |

**The first CPU-visible divergence is at frame 1, one byte: `p0`.**

    $0008  p0    cartridge $00    rebuild $08

`p0` is zero page `$0008` (`x0.pds:371`, "copy of PPU register 0"), and frame 1 is
the first frame the cartridge has written it. From `x7.pds:922`:

    reset  lda #$08       ; NMI off, BG pattern $1000
           sta $2000
           sei
           ldx #$00
           stx $2001
           cld
           sta p0         ; <-- source: A is still $08
           ...

and in the cartridge, whose `reset` vector is also `$F9C1`:

    F9C1: A9 08     lda #$08
    F9C3: 8D 00 20  sta $2000
    F9C6: 78        sei
    F9C7: A9 40     lda #$40          <-- 5-byte insertion, absent from source
    F9C9: 8D 01 A0  sta $A001         <--   "disable MMC3 interval timer IRQ"
    F9CC: A2 00     ldx #$00
    F9CE: 8E 01 20  stx $2001
    F9D1: 86 08     stx $08           <-- stx p0, not sta p0
    F9D3: D8        cld

So the release stores X, not A: `stx p0` (`$86`) where the source has `sta p0`
(`$85`), plus a 5-byte insertion at `$F9C7`. Both images' reset, NMI and IRQ
vectors are `$F9C1` / `$F9AB`-ish / `$F9B3`; only NMI differs by 3 bytes
(`$F9AB` cartridge, `$F9A8` rebuild).

**This is not the cause.** A scratch ROM with that one byte changed to `$86` was
run in BizHawk and its frame-60 screenshot is identical to the unpatched rebuild,
pixel for pixel (25 408/57 344 against the cartridge either way). `p0` is
overwritten anyway: `x0.pds:608` does `lda #$88 / sta p0` on the way into the
game, so whatever `reset` left there does not survive. The divergence at frame 1
is real and is a genuine source-versus-release difference, but it is a symptom.

## A correction to this entry, made while writing it

While chasing `p0` I wrote down that PPUCTRL bit 3 is the `$2007` address
increment, and that the rebuild therefore streamed nametable writes 32 bytes
apart. **That is wrong.** PPUCTRL bit 2 is the increment and bit 3 is the
background pattern table address. `$88` and `$7B` both have bit 2 clear, so the
increment is +1 in both images throughout, and no increment-32 behaviour exists
anywhere. What `p0` actually selects is the background pattern table, and that is
a real difference -- the rebuild leaves it at `$1000` where the cartridge has
`$0000` -- but it is sampled at a single instant at the end of a frame in which
`irq0` and several NMI paths legitimately turn NMI off, so it is a symptom too,
not yet a cause.

## What frame 46 is, and why it matters

Every domain changes at once at frame 46 and then both images are static to frame
90: CIRAM 969 -> 4001 of 4096, OAM 0 -> 256 of 256, WRAM 0 -> 8192, CHR
71 558 -> 86 704. That is a scene or state transition, not a slow draw.

The MMC3 bank shadows say the two are in different code from then on. In the
cartridge at frame 46 `$00..$07` (r0..r7) hold `$0F $28 $38 $30 $0F $2A $3A $30`;
in the rebuild they are still `$FF $FF $3F $40 $41 $42 $00 $01`, i.e. **r0 and r1
have never been written**. `setmmc3` only *reads* r0..r7; they are written by the
`fbnk` fast-bank-select macro (`x0.pds:197`). So at frame 46 the cartridge reaches
an `fbnk` that the rebuild never reaches.

## Also settled: there is no memory-write hook

`tools/bizhawk/writes.lua` registers `event.onmemorywrite` in every callback
shape 06 could think of. Result on quickerNES 2.11.1:

    register onmemorywrite(function)  ok
    register onmemorywrite(table)     NLua: Argument number 1 is invalid
    ran to frame 3 -- total=0 ppu=0 mapper=0 other=0 unparsed=0
    callback shape: NEVER FIRED

So it is not "not yet pinned down": **it does not fire**. A `$2006`/`$2007` write
trace needs BizHawk's own trace logger driven over X11 XTEST. Per-frame snapshots
are the instrument, and they are sufficient for a bisect but cannot separate "wrote
the wrong bytes" from "wrote the right bytes to the wrong address".

## Next three measurements, in order

1. **Diff the cartridge's and the rebuild's `unrun` call site, not the routine.**
   The nametable first differs at frame 2 and the rebuild's rows are displaced and
   full of `$08` where the cartridge has `$00` holes. `$08` is a real tile index
   in the cartridge, which is what a *mis-addressed* stream looks like and not
   what a *wrong-data* stream looks like. Something sets `$2006` differently, and
   the game's software VRAM pointer is `$11/$12` -- both readable in `RAM`.
2. **Find what the release changed in `x5.pds`'s `nmi0`/`irq0` `$2006` dance.**
   `nmi0` writes `$2006` four times per frame and `$2007` 33 times
   (`x5.pds:174-190`), and the release's `nmi0` is 3 bytes longer than the
   source's. Those three bytes are unaccounted for.
3. **Then the CHR write at frame 46.** The cartridge rewrites 89 772 bytes of CHR
   and the rebuild rewrites none. Whatever reaches that write is the same code
   that never reaches `fbnk` at frame 46, so it is one bug, not two.

## Status

| | |
|---|---|
| rebuild reaches the Magician title screen unattended | **yes** |
| rebuild's title screen matches the cartridge | **no**, 44.3% of pixels; lower 60% scrambled |
| first divergent frame | **1**, one byte, `p0` at `$0008` |
| that divergence is causal | **no**, proved by patching it and re-running |
| CHR artwork matches the cartridge | **no**, and not fixable from source |
| `$2006`/`$2007` write hook available | **no**, `onmemorywrite` never fires |
| `make rom` produces a loadable image | **yes** |
| PRG bytes identical to the cartridge | 39 832 / 131 072 (30.4%) -- 38 982 source-derived, plus 864 supplied by the one class-b manifest region of which 850 are byte-different from the source-only image |