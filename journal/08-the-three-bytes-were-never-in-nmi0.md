# 08 -- the three bytes were never in `nmi0`

Dated 2026-10-03. This session was supposed to be spent fixing. It was not, and
the honest reason is at the bottom. What it did produce is a correct answer to
the question it was sent on, three measurements that were wrong, and a harness
that can no longer report a run it did not measure.

## The lead, and what it actually was

The session was sent with: *the release's `nmi0` is exactly 3 bytes longer than
the source's; `nmi0` writes `$2006` four times and `$2007` 33 times per frame;
three bytes are unaccounted for; a mis-addressed rather than wrong-data stream is
what a displaced nametable looks like.*

**The three bytes are `lda $2002` at `X7.PDS:908`, in the NMI *trampoline*, and
they are in the source, not in the release.** The source says, verbatim:

    nmi	pha
	txa
	pha
	tya
	pha
	lda $2002	;clear NMI flag
	jmp (nvec)

The release has no `lda $2002` there. That is the whole of it: three bytes,
`AD 02 20`, deleted by the release after February 1990. It has nothing to do with
`nmi0`, which is in a different module and a different slot.

### The alignment that shows it

`tools/align6502.py` (new this session) sweeps both images linearly from a
hand-picked anchor and aligns them instruction by instruction, scoring byte
identity above opcode-and-mode above opcode, so an insertion is reported as an
insertion instead of as a movement in a match percentage. Anchored on `tsx`,
which is byte-identical in both, and held across 42 byte-identical instructions
that straddle the insertion:

    python3 tools/align6502.py --from 0xF94F --length 0x82

    ...
    == F9AB: 48        pha                  | F9A8: 48        pha       <nmi>      ; identical
    == F9AC: 8A        txa                  | F9A9: 8A        txa                 ; identical
    == F9AD: 48        pha                  | F9AA: 48        pha                 ; identical
    == F9AE: 98        tya                  | F9AB: 98        tya                 ; identical
    == F9AF: 48        pha                  | F9AC: 48        pha                 ; identical
    +                                 | F9AD: AD 02 20  lda $2002   ; DELETION, source only
    == F9B0: 6C 09 00  jmp ($0009)          | F9B0: 6C 09 00  jmp ($0009)         ; identical
    == F9B3: 48        pha                  | F9B3: 48        pha       <irq>      ; identical
    ...

The alignment then continues through `irq` and `reset` with no further
disagreement until the release's `reset` insertions, so the anchor is not a
coincidence of one shared opcode.

**Verdict: class (b), a genuine source-versus-release difference.** The February
1990 source contains the instruction; the assembler emitted it correctly; the
February 1991 release removed it. There is no assembler bug here. Nothing was
fixed in `asm/pds6502.py` because nothing was wrong with it.

### And it is not the cause of the scrambled nametable

Two independent reasons, both measured:

1. The cartridge's `nmi0` is at `$DBEF` and ours is at `$DD01`. Aligned properly
   across that difference, the routine is **63 instructions byte-identical and 63
   with only an operand moved** -- the 96-byte palette write, the OAM DMA, the
   four `$2006` writes, the scroll writes, `lda #$A7 / sta $C000 / sta $C001 /
   sta $E001 / cli` are all identical. The release's is 10 bytes longer, made of
   a 43-byte insertion at the top and a 5-byte insertion at `$DC3F`, against 7
   bytes the source has that it dropped. The `$2006` dance the session was sent
   after is *inside* the release's 43-byte insertion -- it is code the source
   does not have, not three bytes missing from code it does.

2. **`$2002` cannot move the write latch here in a way that matters.** Reading
   `$2002` resets the PPU address latch, and the source's `nmi0` relies on that
   (it does `sta $2006 / sty $2006` straight after). But the release has the same
   `sta $2006 / sty $2006` pair with no reset before it and it works, because the
   latch state on entry is set by the caller. Removing the read would, if
   anything, make the source *less* deterministic, not more.

A scratch ROM was built to check the third possibility -- transplant the
release's `emptydma`/`setnmi`/`nmi` block over ours at the addresses the release
uses -- and measured. It is not the fix: nametable differences improve from 661
to 568 bytes at frame 2 and the ROM then **dies at frame 32 instead of reaching
frame 90**. Recorded here so nobody repeats it.

## What was wrong with the measurements

Three claims from the previous sessions did not survive the guarded harness.

**RAM differs by 396 of 2048 bytes at frame 60, not 1767.** Re-taken with
`tools/bizhawk/run.sh` asserting the core and the ROM, `regionbisect.py` gives 396, and
it has been between 282 and 409 at every frame from 2 to 90. There is no frame at
which 1767 is the answer.

**WRAM and OAM never differ.** The journal recorded "every domain changes at once
at frame 46 -- CIRAM 969, OAM 0->256, WRAM 0->8192". Re-measured over frames
0..90: `wram never` and `oam first differing frame 4, 0/256`. There is no frame-46
event. Whatever was seen at frame 46 was not a memory transition.

**`PPUCTRL` is `$88` in both images at the end of `nmi0`.** The previous claim was
that "the rebuild leaves the pattern table at `$1000` where the cartridge has
`$0000`", which is bit 3 of `$2000`, and it was offered as the cause of a
displaced background. It is wrong, and the alignment shows why:

    cart  DC65: A9 88     lda #$88        | prg  DD3F: 25 08     and $08
    ==    DC67: 05 88     ora $88         | ==    DD41: 05 88     ora $88
    -                                    | +     DD43: 85 08     sta $08
    ==    DC69: 8D 00 20  sta $2000      | ==    DD45: 8D 00 20  sta $2000

Ours computes `$F8 & p0 | $88`; with the source's `p0 = $08` that is `$88`. The
release loads `$88` outright. Both store `$88` to `$2000`. The only difference is
that ours also writes `$88` back into the `p0` shadow. This is also why patching
`reset`'s `sta p0` to `stx p0` changed nothing -- it was never on the path.

What *did* reproduce exactly, which is worth saying: CHR 71 558 of 131 072
differing from frame 0, CIRAM first differing at frame 2 with 661 bytes, and RAM
first differing at frame 1 with 1 byte. Those were real.

## What the nametable damage actually looks like

All 969 differing bytes at frame 60 are in nametable 0. `$2400`, `$2800` and
`$2C00` are identical -- they are mirrors, and both images write the same thing
to them. 918 of the 969 are tile bytes, 51 are attribute bytes.

The rebuild's nametable is not the cartridge's displaced. It contains long
consecutive tile-index runs that the cartridge does not have:

    cartridge row 1   00 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f ...
    rebuild   row 4   00 01 02 03 04 05 06 07 08 08 09 0a 0b 08 0c 08 0d ...

    cartridge row 2   1d 1e 1f 20 21 22 23 24 00 00 25 26 27 00 28 00 29 2a ...
    rebuild   row 5   1d 1e 1f 20 21 22 23 24 25 26 27 28 29 2a 2b 2c 2d 2e ...

One unbroken counter runs from `$00` to `$E5` across the whole nametable, with
`$08` (158 occurrences) standing in for the cartridge's `$00` (210 occurrences).
No constant shift of the cartridge's bytes matches: the best is +64 bytes at
14.9%, allowing `$00`<->`$08`, which is not a displacement, it is a coincidence.

A monotonic counter streamed into the nametable is what a `$2006` stream aimed at
the wrong place looks like, or a DMA loop pointed at VRAM instead of CHR. It is
**not** what a background-pattern-table mistake looks like, which retires the
pattern-table theory outright.

## The harness

Done, and it is the part of this session that will still be worth something.
`run.sh` no longer reports success for a run it did not measure. Written up in
full in the commit message for `520a88f`; the short version:

* The core and the ROM are both asserted. `emu.getsystemid()` must be `NES`,
  `emu.getboardname()` must be the expected board, and 48 bytes at CPU `$F9A8`
  read back out of the loaded cartridge through the core's `System Bus` domain
  must equal the bytes of the file that was pointed at. The last one cannot be
  satisfied by a NullHawk fallback, a wrong path, or a different ROM sharing a
  filename. Both images pass with their own windows, and the windows differ --
  which is also what makes the check discriminating.
* Three facts about this build were wrong in the notes and are now measured:
  `client.getsystemid()`/`getromname()`/`getromhash()` do not exist (it is `emu.*`,
  and NLua returns methods as `userdata` so `type(v) == "function"` is the wrong
  test); `usememorydomain` and `getmemorydomainsize` **never fail**, so a name that
  does not exist silently leaves the previous domain selected and the next read
  returns another region's bytes; and the real domains are CHR, CHR VROM,
  CIRAM (nametables), CPU registers, OAM, PALRAM, PRG ROM, System Bus, WRAM --
  **there is no `VRAM` domain**, so any script naming one was reading something
  else.
* `MAGICIAN_EXPECT_SHA1` pins which image is meant, `MAGICIAN_EXPECT` requires
  non-empty outputs, and an already-running EmuHawk is a hard failure rather than
  a silently diverted launch.

## Where this actually got to

The rebuild still does not produce a correct picture. It reaches the title screen
and the nametable is wrong; that is unchanged. What changed is that the three
leads this session was given are now closed -- one answered (the 3 bytes are
`lda $2002` in the NMI trampoline, class b, not in `nmi0`, not an assembler bug,
not causal), two retracted (the pattern table and the frame-46 memory event), and
the measurements re-taken on an instrument that cannot lie about what it ran.

Next step, in order:

1. **Find what writes the counter.** A monotonic byte sequence in `$2000` means
   something is streaming to VRAM. `tools/bizhawk/zp.lua` (written this session,
   not yet run to completion) dumps zero page and the PPU registers at chosen
   frames from `System Bus` for exactly this. The game's software VRAM pointer is
   `t0`/`t1` with the increment in `t2`, so a cell-by-cell diff of `$00-$FF`
   between the two images will show whether the pointer or the increment differs.
   That is one emulator run each and it is the next thing to do.
2. `emptydma` and `nmi0` in the release are genuinely different code and are
   still unaccounted for, but transplanting them destabilises the boot. They have
   to be fixed in the source's terms, not overwritten.
3. The CHR ceiling is unchanged and was not re-investigated: 71 558 of 131 072
   bytes are a redrawn revision, and the picture cannot be finished until the
   CPU side is right.