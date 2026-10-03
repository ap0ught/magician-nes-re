# 06 - The picture is wrong in VRAM, and the release's `unrun` is not the source's `unrun`

Dated 2026-10-03. Branch `fix/boot-from-source`. Follows
`journal/05-bizhawk-is-the-instrument.md`.

## What is on screen, for the record

BizHawk 2.11.1, quickerNES, mapper 4, `NES/SaveRAM/` cleared, unattended, no input
injected. `client.screenshot()` from inside the emulator. Cartridge left
untouched (mtime still 2026-01-19 17:23:37.890000000 -0600, 262160 bytes).

| frame | cartridge | rebuild | pixel difference |
|---|---|---|---|
| 60 | nz 20 807, max lum 216, 10 colours | nz 21 580, max lum 207, 10 colours | - |
| 200 | nz 20 807, max lum 216, 10 colours | nz 19 127, max lum 207, 10 colours | 57.6% |
| 400 | nz 20 807, max lum 216, 10 colours | nz 19 127, max lum 207, 10 colours | 57.6% |

Both images are 256x224: BizHawk crops to `nes.gettopscanline()`=8 ..
`nes.getbottomscanline()`=231.

**The cartridge is static from frame 60 to frame 400.** **The rebuild is static
from frame 200 to frame 400, and it is a different picture.** So the rebuild is
not "behind" -- it finishes, and what it finishes on is wrong. At frame 60 the top
~40% still resembles the title screen; by frame 400 it has scribbled over that
too. This is a progressive corruption, not a slow draw.

## What is in memory, both images, same frame, same core

Dumped through BizHawk's own memory domains, with the domain name verified after
selection rather than assumed:

| region | domain | frame 60 | frame 400 |
|---|---|---|---|
| CHR-RAM $0000-$1FFF | `CHR` | 480 / 8192 differ (5.9%) | 480 / 8192 (5.9%) |
| nametables, 4 KiB | `CIRAM (nametables)` | 969 / 4096 differ | 1006 / 4096 differ |
| palette, 32 bytes | `PALRAM` | **0 differ** | 2 differ |
| OAM, 256 bytes | `OAM` | **0 differ** | **0 differ** |
| work RAM, 8 KiB | `WRAM` | **0 differ** | **0 differ** |

**Work RAM is byte-identical, 8192 of 8192, at both frames.** Whatever buffer the
game builds its screen in, the rebuild builds exactly the same bytes. OAM is
identical. The palette is identical at frame 60. The damage is entirely on the
output side: the nametable, and 480 bytes of CHR-RAM.

That kills a whole family of explanations at once. It is not the decompressor
producing wrong data, not the palette load, not sprite placement, and not a wrong
CHR source pointer. **The data is right and the transfer to the PPU is wrong.**

## A retraction from earlier in this same session

The first pass used `memory.readbyterange` with a fall-back that pads, on a domain
name I had guessed. It reported four 1024-byte nametables "0 bytes differ" and I
wrote that down as a finding. It was an artifact: the real domain is called
`CIRAM (nametables)`, its size is 4096, and the block I had been reading was 1024
bytes of the constant `$F2` in both images. Two harness faults produced a clean,
confident, completely false result in the same direction:

1. `memory.usememorydomain` with an **unknown name does not fall back to the
   default domain -- it leaves the previous selection in place.** In the census,
   `pal`, `prg` and `chr` all reported "OAM", because OAM had been selected three
   lines earlier and nothing had changed it. Every one of those reads was OAM.
2. `getmemorydomainlist()` returns plain name **strings**, not objects. Reading
   `.Name`/`.Size` off them gives nil for all nine, which looks like "the core
   does not expose its domains" and is why the first dump script could not
   discover the names at all.

The nine real domains are `WRAM`, `CHR`, `CIRAM (nametables)`, `PRG ROM`,
`CHR VROM`, `PALRAM`, `OAM`, `System Bus`, `CPU registers`.

## The routine, and the difference

`unrun`/`unrunscn`, `pds-text/x5.pds:106-121`, is the RLE decompressor that writes
the nametable through `$2007`. It assembles correctly: `unrun` is at `$DC77` in
the rebuild and every `lda (t0),y` in it is `B1 13` -- `($13),y`, the right mode.
The `(zp),y` fault from two sessions ago is genuinely fixed here.

The cartridge's copy is at `$DB1D`, and it is not the same routine. It is the
source's routine **plus one contiguous 36-byte insertion, and nothing else** --
verified by disassembling both and comparing the mnemonic stream, which is
identical, 71 instructions to 71 instructions, once the block is removed:

```
$DB41  B1 21     lda ($21),y
$DB43  2C D8 06  bit  $06D8      ; <-- inserted
$DB46  30 05     bmi  $DB4D      ; <-- inserted
$DB48  8D 07 20  sta  $2007      ; the source's only instruction here
$DB4B  10 1A     bpl  $DB67      ; <-- inserted
$DB4D  08        php             ; <-- inserted: defer instead of writing
$DB4E  85 13     sta  $13        ;     save A
$DB50  86 23     stx  $23        ;     save X
$DB52  84 24     sty  $24        ;     save Y
$DB54  20 8E F3  jsr  $F38E      ;     queue the byte on the DMA stack
$DB57  20 46 F9  jsr  $F946      ;     then flush the queue
$DB5A  E6 11     inc  $11        ;     bump the 16-bit VRAM address
$DB5C  D0 02     bne  $DB60
$DB5E  E6 12     inc  $12
$DB60  A5 13     lda  $13        ;     restore A
$DB62  A6 23     ldx  $23
$DB64  A4 24     ldy  $24
$DB66  28        plp
$DB67  08        php             ; the source's continuation
```

Both callees are identifiable in the cartridge:

* **`$F38E`** is a one-byte DMA queuer, and it is exactly `initdma`'s token format
  from `x7.pds:238` (`tok3 equ $60` = "DMA hi,lo,count,<data>"):

  ```
  $F38E  BA        tsx
  $F38F  86 28     stx  $28        ; save main stack pointer
  $F391  A6 29     ldx  $29        ; $29 is the DMA stack pointer (stk1)
  $F393  9A        txs             ; switch to the DMA stack
  $F394  A5 13     lda  $13        ; the byte
  $F396  48        pha             ; push data
  $F397  A9 01     lda #$01
  $F399  48        pha             ; push count = 1
  $F39A  A5 11     lda  $11
  $F39C  48        pha             ; push destination lo
  $F39D  A5 12     lda  $12
  $F39F  48        pha             ; push destination hi
  $F3A0  A9 60     lda #$60
  $F3A2  48        pha             ; push token $60
  $F3A3  BA        tsx
  $F3A4  86 29     stx  $29
  $F3A6  A6 28     ldx  $28
  $F3A8  9A        txs             ; back to the main stack
  ```

* **`$F946`** is `jsr $E2D0 / jsr $F94F / jmp $E2CD`, and **`$F94F` is
  `emptydma`** -- byte for byte the shape of `x7.pds:871`, `swapstk 0,1` and
  `!a pla`. And **`$F93C` is `initdma`**, matching `x7.pds:865` exactly
  (`lda #$00 / sta $017F / lda #$7E / sta stk1 / rts`), with the cartridge's
  `stk1` at `$29`.

So the release defers the nametable write: when bit 7 of `$06D8` is set it pushes
each decompressed byte onto the DMA queue at a software-tracked VRAM address
(`$11/$12`), flushes during the NMI the queue exists for, and advances the
address. The February 1990 source has only the synchronous `sta $2007`. Nothing in
the source corresponds to `$F38E`: in our build that address is `CA / dex`, the
tail of an unrelated routine, and `$F946` is the middle of `popbnk`.

This is the **same shape of difference already recorded for `reset`** -- the
release is this source plus small contiguous insertions -- but here it is not
cosmetic. It is inside the one routine that writes the region that is corrupt.

## How far this goes, stated honestly

**Not yet proven to be the cause.** What is proven:

* the rebuild draws a picture, and the picture is wrong;
* the corruption is on the PPU output side, not in the data (WRAM and OAM
  byte-identical);
* it is progressive, not a slow draw (static from frame 200 to 400);
* the routine that writes the nametable differs from the release by exactly one
  36-byte insertion that implements a deferred write the source does not have.

What is **not** proven: that adding those 36 bytes would fix the picture. The
mechanism is attractive -- a 1 KiB `$2007` burst with no protection against `nmi0`
firing in the middle of it, `nmi0` writing `$2006` three times and `$2007` 33
times (`x5.pds:174-190`) -- but it does not survive one obvious check: if the tail
of the burst were landing in `$3F00`, then `PALRAM` would differ substantially,
and it differs by **0 bytes at frame 60 and 2 at frame 400**. So plain NMI
interleaving is not the mechanism, or at least not the whole of it.

The rebuild's nametable at frame 400 has `$08` filling rows 0-3 and real,
ascending data from row 4, against the cartridge's ascending data from row 1. The
damage is at the *start* of the region, not the tail. That is a different
signature from "an NMI stole the end of the stream", and it is where the next
measurement should go.

## The next three measurements, in order

1. **Find `$06D8`'s counterpart in the source.** It is a flag whose bit 7 switches
   the PPU write path. `nmiflag = $002C` and `abflg = $072C` in our build; the
   cartridge's zero page is allocated differently (`t0=$21` there against `$13`
   here), so this needs a role-based match, not an address match. If the source has
   no such flag, the deferred-write feature was added after the source snapshot
   and the region is class (b): absent from source, present in the cartridge.
2. **Count `$2007` writes per frame in both images.** This needs the write hook.
   `emu.registerafter`/`emu.registerbefore` -- what the notes assumed -- **do not
   exist in 2.11.1**; the surface is `event.onmemorywrite(cb)`,
   `event.onmemoryexecuteany(cb)`, `event.on_bus_write(cb)`, and only in the
   single-callback form, with no address filter. The installed form did not fire
   in the probe, so the callback-object convention still has to be pinned down.
   `event.onframeend` does fire, `emu.getregister` and `emu.disassemble` work, and
   `emu.getregisters()` returns a table.
3. **Diff the two images' `$2006` writes.** If the rebuild is aiming `$2006` at the
   wrong place even once, that is the whole bug and it is one number.

## Also corrected: `emu.registerafter` does not exist

`journal/05` said `emu.registerafter`/`emu.registerbefore` would give a
core-native trace with `client.cpu` resolving inside the callback. On 2.11.1
`client.cpu` is nil inside a plain Lua callback too, and the hook functions are on
`event`, not `emu`, and only in a one-callback form that rejects an address
filter. `client.cpu` being nil at script top level is real, but it is not specific
to the top level.

And a caution about a mistake I made while probing that: `pcall(f)` returns
`true` when `f` merely *ran*, including when it returned `nil`. A probe written as
`pcall(function() return emu[n] end)` reports success for names that do not exist.
Every name in this project is now read off `pairs()` or off a real callback
firing, never off a `pcall` result.
