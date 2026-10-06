# 14 — One data byte, and the offset arithmetic that refuted it

*Branch `fix/boot-from-source`. This entry corrects journal 13 item 1 and item 2's
first half. Both corrections are the same mistake.*

## The answer, first

**`$004D` (`mapind`) is written at frame 81 by `initvars`, from the byte at `$C300`
(`stlev`) — and our rebuild's `$C300` is `$01` where Beta 1's is `$00`.**

The chain, every step measured:

| | | |
|---|---|---|
| 1 | `initvars` runs its C=0 path (`X1.PDS:23` `bcs reinit` not taken) | `X1.PDS:27-29` |
| 2 | `lda $C300` — `stlev`, in the fixed window | file offset `$1C300` |
| 3 | `sta hilev` / `sta mapind` — **one byte to two cells, always equal** | `$8553` / `$8556` |
| 4 | that byte is `$01` here, `$00` on Beta 1 | see below |
| 5 | frame 111, `inc mapind` (`X0.PDS:624-625`) → `$02` vs `$01` | `$ECDD` |
| 6 | `curlev` becomes `$20` vs `$10` | `$E03A` |
| 7 | the wedge in `g03` is what a level-2 load looks like | downstream |

And the byte, per dump:

    beta 1   $00     <- the build src/play is verified against
    beta 2   $01     <- what the released source says
    beta     $41     ('A' -- the start of a string; `stlev` has moved)
    beta 3   $41
    beta 4   $41
    release  $41

## What journal 13 got wrong, and how it was able to

Journal 13 refuted `stlev` on evidence, and the refutation was carefully argued:
`initvars` does `lda $C300 / sta hilev / sta mapind`, and "`$C300` is `$7A` filler in
both ROMs — not `$01`. So `lda $C300` returns `$7A` on the cartridge that works,
which means the C=0 branch is never taken there, and no hypothesis that depends on
`$C300` holding `$01` can be true."

`$C300` is at **PRG file offset `$1C300`**, because `X5.PDS:14-21` says
`$C000-$DFFF : from bank $0E` and bank `$0E`'s low 8 KiB is slot 14, which is the
last-but-one 8 KiB slot of a 128 KiB PRG. Journal 13 read file offset `$10300`,
which is **slot 8**. Six slots away, and full of `$7A`.

So the argument was sound and the address was wrong. It is the *third* time this
tree has lost a finding to PRG offset arithmetic — journal 13 lists two, and
records that `cmpbank.py:cpu()` "has it right" while the sibling call site in the
same session did not. **A tool that is right in one place and wrong in another is
worse than one that is wrong everywhere**, because the right place is what you
checked.

The generalisation, which is the actual lesson: **`$C000-$DFFF` is switchable on
MMC3, and the only fixed windows are `$E000-$FFFF`.** Any claim of the form "byte
*X* is at CPU address *Y*" in this project has to name a slot, and until
`tools/dis6502.py` and `tools/nestrace.py` agree on one table of what each slot is,
the question is not answerable by arithmetic at all.

## Why the frame-boundary comparison could not have found it

Six of the eight differing bytes at frame 81 were zero-page scratch. The two
durable ones were `mapind` and `hilev`, and they were equal *to each other* — which
is the signature of one write to two cells, and the reason to look at the writer
rather than at a reader.

But the RAM comparison was never going to localise the frame: it samples at frame
boundaries, and this byte is written and read inside a single frame. The tool that
answers it has to record **writes**, and BizHawk cannot: `tools/bizhawk/writes.lua`
records the settled measurement that `QuickNES.get_MemoryCallbacks()` throws
`NotImplementedException` unconditionally, so there is no memory callback of any
shape on this core. That left `tools/nestrace.py`, which had **no controller at
all** — `$4016` returned 0, so no button was ever pressed and it could not replay a
route.

## The instrument, and how it was validated

`tools/nestrace.py` gained a controller (`$4016` strobe + 8-bit serial shift, the
hardware's own bit order), `--inputs` to replay a `src/play` input log, and
`--watch-ram` to record every write to a named cell with the PC that made it.

**Validated before use, against an independent instrument.** BizHawk's
`src/play/differential.py` had already answered "first differing frame: 81, 8 of
2048 bytes". The tracer reproduced it exactly:

    $004D (mapind)   frame  81   $8543  = $00   beta 1
                     frame  81   $8556  = $01   rebuild
                     frame 111   $ECDD  = $01   beta 1
                     frame 111   $ECDD  = $02   rebuild
    $0719 (hilev)    frame  81   $8540  = $00   beta 1
                     frame  81   $8553  = $01   rebuild

Same frame, same byte, same instruction, from two unrelated emulators. That is
what makes the next step a measurement rather than a guess.

The `$4016` bit order is the hardware's — bit 0 = A, through bit 7 = Right — and
**not** `src/play`'s `BUTTONS = (Up, Down, Left, Right, Select, Start, B, A)`,
which is BizHawk's enumeration and not a bit layout. Assuming they were the same
puts a plausible wrong edge in the replay, which is why
`src/testing/test_write_trace.py` pins the order with all eight buttons separately.

## The fix, and where it lives

`vendor/Magician-NES/` is read-only, so `X5.PDS:8`'s `stlev db $01 ;** tmp` cannot
be edited. It is overlaid instead, by the mechanism `src/magician/README.md`
already establishes for the title screen:

    src/magician/STARTLEV.PDS     org stlev / db $00

with an `expect` entry in `asm/build.py`'s `OUR_MODULES`, so the build **reads the
byte back out of the finished image** and fails if it is not `$00`. "Did it
assemble" and "did it change the ROM" are different questions and only the second
one is worth anything.

The overlay is also what makes this re-syncable: `vendor/` stays pristine, and the
one dump-specific value lives in one place with its evidence in the file.

**After the fix**, both ROMs agree write-for-write through frame 122 on `mapind`,
`hilev`, `curlev` and the phase word:

    $004D   f2=$00 (both), f81=$00 (both), f111=$01 (both)
    $0061   f81=$E2 (both), f112=$10 (both)
    $005F   f81=01, f82=02, f83=03, f92=04, f93=05, f107=06, f108=0A, f111=01,
            f112=02, f122=03                                   (both, in order)

`$10` is level 1.0, which is what `X1.PDS:29`'s own comment says
(`sta mapind ;start game at level 1.0 - 1.0`) and what the released source's `$01`
breaks. `** tmp` is Eurocom telling us which of the comment and the value to
believe.

## The wedge is fixed in the tracer. It is not yet fixed in BizHawk.

`src/play/differential.py` has not been re-run against the rebuilt ROM. The
tracer's agreement through frame 122 is strong evidence and is **not** the same
claim: the tracer is a Python model of a 6502 and an MMC3, `src/play/` is verified
against BizHawk, and this project has been wrong about exactly that gap before
(journal 11: the injector is proven and the game ignores it, because quickerNES
reads the controller at a different point in the frame). **Re-run the milestone
before anything is claimed about entering a level in our rebuild.**

## Where the tracer still cannot follow the game — recorded, not patched

Beta 1 stops in the tracer at frame 131 with `illegal opcode $62 at $A745`. This is
**not** a ROM difference and **not** a decode gap to paper over:

    $DC96  jsr $DC96
    $F846  stx zp / stx abs / sta zpx / sta abs / rts     <- setbank
    $E004  jsr $E004
    $A700  sbc abs / .9F / ldy abs / clv / .1A            <- NOT CODE

`$A700` decodes as nonsense, so the tracer is executing data: after `setbank` and a
`farjsr`-style call it has the wrong 8 KiB slot in the `$A000` window. `$62` is not
in the stable undocumented set either, so adding it to the opcode table would let
the run continue *on a wrong byte* and produce a plausible wrong answer, which is
this project's established failure mode. **Left as a known limit**, with the
suspect named: `Bus._prg_offset`'s `prg_mode` R6/R7 window swap, against
`X5.PDS:329-331`'s `bnk 6,#0 / bnk 7,#1`.

Our rebuild runs the full 307 frames in the tracer, which is why the frame-131 stop
is *not* symmetrical and must not be read as "our ROM is healthier here".

Also fixed along the way, because they stopped the comparison: the six one-byte NOPs
(`$1A $3A $5A $7A $DA $FA`) were missing from **both** `tools/nestrace.py` and
`tools/dis6502.py`, and Beta 1 executed `$1A` at `$A709` on frame 131. A
disassembler that prints `.byte $1A` and a tracer that refuses to execute it are
the same mistake in two places.

## Other instruments that were wrong in the same way

| tool | the bug | what it produced |
|---|---|---|
| `tools/dis6502.py` | `cpu_to_file(a) = a + 0x10000` used below `$C000` | `--from 0x8550` read **slot 12**'s bytes and labelled every line with a `$8000`-window address: a full, plausible, wrong disassembly |
| `tools/dis6502.py` | `--range` averaged a switchable end with a fixed end | now refused |
| `tools/nestrace.py` | `resolve()` used `int(s, 0)`, which rejects `$` | `--where $F5D4` — the spelling `mag.sym` itself uses — always raised `ValueError` |
| `tools/nestrace.py` | `bus.cur_pc` set by the main loop only | under `run_frames()` every write was attributed to PC `$0000`, an address no ROM executes from, and a plausible answer |
| `asm/Makefile` | `prg.bin` did not depend on `src/magician/` | `STARTLEV.PDS` could be written, committed and built into a release note while `make` said "Nothing to be done" |

The last one is the one to keep: the whole architecture of this project is "our
changes go in `src/magician/`", and the build did not watch that directory.

## Tests that failed when first written

Four, all in `src/testing/test_write_trace.py`, all real:

1. `hasattr(nestrace.Bus, "ram_writes")` — `ram_writes` is an instance attribute,
   so the class does not have it. The check was asserting the wrong thing.
2. The `machine()` helper wrote the reset vector at `0xFFFC` of a 32 KiB buffer
   while it was 16 KiB long: `IndexError`.
3. **`bne` was `$F3` where it needed `$F4`.** The branch landed on the operand byte
   of the `lda #$00` at `$0E`, re-decoded as `brk`, and produced **eight identical
   wrong answers** that all looked like a plausible bit reversal in the controller
   model. It was the test that was wrong; the model was right.
4. The expectation `got == 1 << bit` was backwards. `rol` shifts **left**, so the
   carry from the *first* read ends in bit 7 after eight rotations. That reversal
   is not a detail of the test — it is why `joykey` follows `jk0` with an
   `asl a` loop over x = 7..0 (`DISP.SRC:295-305`) to undo it.

And one that was not a failure but should have been caught earlier: the file's
first version printed no `  ok ` lines, so `make check-py` reported
`PASS test_write_trace.py 0.32s exit=0 **0 checks**`. A passing test file that
asserted nothing is indistinguishable, in that output, from a test that was never
written. `check()` now prints the prefix `run_all.py` counts.

Also measured, and worth recording because it corrected me: the iNES battery bit is
**flags6 bit 1**, not bit 6. Beta 1 is `$40` → bit 1 clear → no battery, which is
what the working method says and what my first check contradicted.

## Byte accounting

Recounted, source-derived and manifest-derived kept apart:

    PRG 96414/131072 (73.6%)
      from the source alone : 96414
      from the patch manifest: 0        (asm/patches.manifest has no regions)

+1 from journal 13's 96413: that is `STARTLEV.PDS`'s single byte, which the build
counts as ours. `asm/patches.manifest` is still empty and still has no regions.

## `tools/findstore.py`, and what a linear sweep can and cannot say

New tool: every instruction in a PRG that can store to a named RAM cell. It exists
because "who writes `$004D`" is not a `85 4D` search — a zero-page cell is reachable
by more than one instruction, and a search for the one you already had in mind
cannot tell you that you had the wrong one. `inc mapind` is `E6 4D`, not `8D 4D 00`
with an increment in front of it.

It found that both ROMs have **exactly four** writers of `$004D` (`$8441`, `$8556`,
`$90ED`, `$ECDD`) and **byte-identical** code at all four — which is what moved the
question from "which routine" to "which data did it branch on". It found the same
for `$0719`, where the counts differ: Beta 1 has three writers, our build has four,
the extra one being `reinit`'s `inx / stx $0719` (`X1.PDS:36-38`). That difference
is real and is *not* the cause — it is downstream of `mapind`, and it is why the two
cells move together.

The sweep is linear, so it also decodes data as code. It prints the disassembly
context and the nearest symbol and says so in its own output; it marks nothing as
proven for that reason.

## What the next attempt needs

1. **Re-run `src/play/differential.py` and the `m1_first_town` milestone against the
   rebuilt ROM.** Everything above is tracer evidence; the claim "our rebuild now
   enters a level" needs BizHawk, which is where `src/play/` is verified.
2. **`Bus._prg_offset`'s `prg_mode` swap**, so the tracer can follow Beta 1 past
   frame 131 and the two ROMs can be compared over the whole 307-frame log rather
   than 122 frames of it.
3. **`$01FA-$01FC`** — still no symbol, still differing at frame 81 in the *pre-fix*
   comparison. Re-check them now that `mapind` is right: they may have been the same
   write's side effect. `src/play/ram.py` still has the hole and should record it
   as unknown rather than left blank.
4. Tasks 2-4 of the brief — the input remap, the shared drawing layer, spell slot 27
   — are **not started**. All three are unblocked now. Task 2's watch item:
   `X0.PDS:352` `4 :1=ignore START & SELECT` masks both buttons per object, so
   whatever consumes the remapped START must respect it.