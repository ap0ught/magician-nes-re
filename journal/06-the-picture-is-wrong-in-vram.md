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

---

# Corrections to this entry, made later the same day

Two claims above were wrong, or would have been wrong. Both are corrected here in
place rather than edited out, because the second one is exactly the kind of lead
that costs a session if it is left standing.

## The 36-byte insertion is inert. It is not the cause.

The entry above ends with "the routine that writes the nametable is not the
release's routine", and that part stands. What does not stand is the hope attached
to it. The insertion is **dead code on a cold boot**, and this is measurable rather
than argued.

The insertion is gated on `bit $06D8` / `bmi` -- three times in the whole
cartridge, all in bank 3, at `$DB09`, `$DB15` and `$DB42`, and **nowhere else**:

    $DB05  A0 20     ldy #$20
    $DB07  84 12     sty $12          ; $11/$12 = the tracked VRAM address
    $DB09  2C D8 06  bit  $06D8
    $DB0C  30 03     bmi  $DB11       ; skip the sta $2006 ...
    $DB0E  8C 06 20  sty $2006
    $DB11  A0 00     ldy #$00
    $DB13  84 11     sty $11
    $DB15  2C D8 06  bit  $06D8
    $DB18  30 03     bmi  $DB1D       ; ... and here
    $DB1A  8C 06 20  sty $2006

A scan of all 262 144 bytes of the cartridge's PRG finds **zero** `sta $06D8`, zero
`inc $06D8`, zero `dec $06D8`, and no RMW of any kind. The only way that byte gets
its value is a zero-page clearing loop -- which is consistent with what the
Cartridge's RAM actually contains. Read out of BizHawk at frame 60:

    cartridge  $06D8 = $00      rebuild  $06D8 = $FF

So in the cartridge `bmi` is never taken: it writes `$2006` and streams `$2007`,
which is **exactly what the source does and exactly what the rebuild does**. The
deferred-write path is a feature for a state this ROM never enters. Chasing it
would have been a wasted session.

(The `$FF` in the rebuild is not a fault either: the two builds allocate zero page
differently -- the cartridge has `t0` at `$21` and `curchrpal` at `$0180`, we have
`t0` at `$13` and the palette buffer at `$04B0` -- so untouched stretches of the
page are still at BizHawk's power-on fill in one image and not the other. 396 of
2048 zero-page bytes differ, essentially all of them explained by that.)

## `$9D6C` is not an `rts`, in either image

`journal/03` and the README both carry a claim that at frame 14, cycle 439 272,
"the `rts` at `$9D6C` pops a destroyed return address". There is no `rts` at
`$9D6C`. Read straight out of the assembled ROM:

    rebuild $9D6C = $31

and the surrounding bytes are

    $9D40  0e 11 18 19 0e 11 1a 1b 12 13 1c 1d 14 ff 1e ff 1f 20
    $9D50  2a 24 21 21 2b 2b 22 23 21 21 24 21 2c 2b 25 26 2d
    $9D60  2e 27 28 ff 2f 29 ff 30 ff 31 32 38 39 2b 2b 3a 3a
    $9D70  33 34 3b 3c 35 36 3d 3e 37 ff 3f ff 81 82 84 85 83 ff

That is a **character-code table** -- the game's strings stored as tile indices,
`$FF` as the terminator, `31 32 38 39` spelling "1289". No symbol exists anywhere
in `$9D00`-`$9E00`. `$9D6C` is a data byte in a font table, not an instruction, and
not an `rts` in the rebuild.

BizHawk's own disassembler agrees in spirit and disagrees in detail:
`emu.disassemble(0x9D6C)` returns `BRK` for **both** images, because it reads
through the `System Bus` domain with whatever bank is currently mapped -- not
because the byte is zero. Neither reading supports an `rts`.

So the whole `$9D6C` -> `$0000` -> `brk` -> `$F9B3` IRQ chain rests on an address
that the tracer resolved differently from the ROM it was given. Whatever is true
about the tracer's execution, its address accounting for this claim does not match
the assembled image, and the claim cannot be pursued as written.

## Where that leaves the search

The corruption is still exactly where this entry says it is: on the PPU output
side. WRAM and OAM byte-identical, `PALRAM` identical at frame 60, `CIRAM` 969 of
4096 different, CHR-RAM 480 of 8192 different in tiles `$045`-`$0FE`.

The 480 CHR bytes are **not** a misaligned copy. Testing every constant source
offset, the best is +320 and it explains 63 of 480 -- chance. The rebuild's bytes
at those positions are genuinely different tile data (72 of them zero, against 148
in the cartridge), and whole 16-byte tiles differ, with runs such as

    cart $04C0  ff 50 ea 3f 00 40 54 01 00 af 15 00 00 00 00 54
    reb  $04C0  00 00 00 00 00 00 00 00 ff ff ff ff ff ff ff ff

So it is not a shifted stream and not a wrong source pointer. Both the nametable
writes and the CHR writes go through `$2007`, and both are wrong, which points at
the `$2006` address setup or the MMC3 CHR bank select rather than at the data.

**Measurement 3 in the list above -- diff the two images' `$2006` writes -- is now
the one to do first.** It is also the one that needs the write hook, so the hook
question has to be settled first: `event.onmemorywrite(cb)` installs but does not
fire, and the address-filtered forms are rejected outright. Either the callback
needs to be a callback *object* rather than a bare function, or quickerNES does
not implement `IMemoryCallbackSystem` at all, in which case the fallback is
BizHawk's own trace logger -- and that needs synthetic input, which python-xlib's
`XTEST` extension can provide even though there is no xdotool on this machine.
