# 02 - A gap is not one thing

2026-10-03

Append-only, in the style of `01-six-modules-with-no-slot.md`. Where this entry
contradicts an earlier one it says so and gives the measurement, rather than
quietly replacing it.

**Where things stood.** HEAD `cbc505e`, and the working tree already dirty with
eight modified files. The five uncommitted assembler fixes and the whole
slot-pinning scheme existed only as uncommitted changes to `asm/build.py`
(+294/-34) and `asm/pds6502.py` (+182/-19). They are still uncommitted and are
still the only copy. Nothing here was committed.

**The build did not build.** `make -n` printed `Nothing to be done for 'all'`.
`asm/out/prg.bin` was newer than the four prerequisites, and the prerequisites
were only the Python files -- never the `vendor/` sources the assembler reads.
Every number below was measured after fixing that, with the assembler actually
running: `asm/build.py` at HEAD+tree, forced, exit 0, deterministic across three
runs, `sha256 272a1cbd4d42a3d5`, **39 204 / 131 072 (29.9%)**, 8/32 CHR pages,
3622 symbols. That is the baseline every "before" here means.

---

## 1. `make` was the blocker, and it was two bugs

`asm/out/prg.bin: $(SOURCES)` is a file target whose prerequisites are four
Python files. It is not a build; it is a timestamp comparison against the wrong
set of inputs, and it printed "nothing to do" while the assembler was one command
away. Fixed by making `assemble`/`rom` phony and running unconditionally
(`asm/` is the slow part -- ~20 s, almost all of it the sixteen-slot search).

The second bug was worse, because it *reported* itself as something else:

```make
-$(ASM) --cart "$(CART)" > $(OUT)/build.log 2>&1 || true
@if cmp -s $(OUT)/prg.bin $(OUT)/prg.bin.prev; then ...
```

The `-` prefix and `|| true` discard the assembler's exit status. An assembler
crash leaves the *old* `prg.bin` in place, `cmp` sees a difference, and the log
says `FAIL: the rebuilt PRG moved` -- which is a statement about bytes when the
truth is that there were none. Verified by pointing `CART` at a nonexistent file:
now

```
FAIL: the assembler exited 1 -- not a comparison failure.
      tail of asm/out/build.log:
      ... FileNotFoundError: [Errno 2] No such file or directory
```

**Cost of not fixing this first:** every measurement in the project is
interpretable only if the build runs. It was a two-line fix that had to come
first.

## 2. `tools/pds_extract.py --check` was permanently red for a cosmetic reason

`read_text()` opens in universal-newline mode, which turns each of the 742 lone
`\r` soft-wrap characters per file into `\n`. The comparison was against a string
the decoder could never produce. One line, `read_bytes().decode("latin-1")`.
`--check` now exits 0, and the files on disk are byte-identical to a fresh decode.

**Cost:** a permanently failing check is worse than no check. Two sessions can
read it as "the extraction is broken" and go looking for a bug in the decoder.

---

## 3. `exitm` is a no-op label, and the 383 dropped `exitm`s are not the problem

`X0.PDS:34` is `db @3,@2 exitm` inside the `st` macro's true arm. `exitm` is
defined nowhere in the release. The statement scanner cuts the soft-wrapped
segment so that `exitm` becomes its own statement, `Line.__init__` sees a bare
word in column 0, and it is *defined as a label* at the current address -- so it
emits nothing and the `else` arm is still skipped by the conditional. The report
line `383x redefined 'exitm'` is that label being defined 383 times, not 383
dropped instructions.

Measured rather than argued: subclassing the assembler so that `exitm` is a
**total** no-op (never defines the label, never reaches `instruction`) produces a
**byte-identical** PRG:

```
exitm no-op calls: 53
PRG identical with exitm suppressed: True
sha A 272a1cbd4d42a3d5  sha B 272a1cbd4d42a3d5
```

53 calls in the project pass; the 383 is the total across the sixteen-slot search
as well. Each `st` call emits 4 bytes on either branch (`>$@1, $@1&$f8` then two
more), which is what the data format wants -- `pti`'s `li = (*-lo)/$4+li` divides
by 4.

**This closes the question `PROVENANCE.md` and the task brief both flagged as the
prime suspect.** It cost about twenty minutes to settle and it was the wrong
suspect: `exitm` cannot produce a byte difference, because it produces no bytes.

## 4. The real assembler bug: every relative branch is one byte long

`Assembler.instruction`, `mode == "rel"`:

```python
self.emit(code)
self.fixups.append(Fixup(self.prg_offset(), self.log, ...))
self.emit(0)
```

After `emit(code)` the opcode is written and `self.log` is the address of the
**displacement byte**. `Fixup.after` is documented as "the logical address the
displacement is measured from", and `resolve` computes
`delta = (target - f.after)`. A 6502 measures a branch displacement from the
address of the *next* instruction -- displacement byte + 1. So every branch in
the rebuild lands **one byte late**.

It is one byte *late*, not early, which is why it survived: a routine's loop and
its own backward branch are displaced by the same byte and still agree, so the
code runs. It only shows against the cartridge:

```
X6.PDS:791 initcols   ours  ... 8D D3 04 CA D0 F8 8C
                     cart  ... 9D 7F 01 CA D0 F7 8C
```

`bne !a` must return to the `sta $2007`; `$F8` targets the middle of our own
`sta $04D3,x`. Fixed by `self.log + 1`. **+5 matched PRG bytes** (39 204 -> 39 209),
which is small only because the bytes in the wrong-placed modules are wrong
anyway; the *correctness* is not small, and it is now verifiable against the
cartridge byte-for-byte.

The same comparison also showed `sta curchrpal-1,x` assembling `8D D3 04`
(absolute `$04D3`) where the cartridge has `9D 7F 01` (zero page `$017F`). Our
`curchrpal` is `$04D4`; the cartridge's is `$0180`. **The `zp`/RAM allocation map
has drifted by $354 by that point.** `rn` is `$0D` on both sides, so the map is
right at the bottom and wrong by the time it reaches `$0180`. Not diagnosed. This
is the next thing to look at, and it is class (c) or (b) -- undecided.

## 5. X6: `slot_origin` is wrong for two slots out of sixteen, and it was right by luck

`Assembler.slot_origin(slot)` returns `0x8000 + (slot & 3) * 0x2000`. That is a
true statement about MMC3 for **four** slots. With 8 KiB banks, `$C000` and
`$E000` are fixed to slots 14 and 15 whatever the bank registers say; registers 6
and 7 offer `$8000` and `$A000` for everything else. So slot 2 must not be
assembled at `$C000` and slot 3 must not be assembled at `$E000`.

X6 has no `org` of its own and sits in slot 3, so its whole address space came
from `slot_origin(3)` = `$E000`. The obvious move is to force the origin. **That
would have been wrong, and measuring first is the only reason it was not done.**

Both dumps' `reset` call `initcols` inside the `$E000` window:

```
release  reset $F9C1: ... 20 4A F0  jsr $F04A      <- initcols, X6.PDS:791
beta     reset $F984: ... 20 0D F0  jsr $F00D      <- initcols
```

`$F04A` and `$F00D` are only reachable when slot 15 is mapped at `$E000`, and
slot 15 always is. **`initcols` is X6's, so X6 is in slot 15 -- not slot 3.** The
address `slot_origin(3)` produced is the address X6 needs; the *slot* that
produced it is wrong, and it will stop being right the moment either number
changes.

X6 cannot be moved: slot 15 is X7's, X5 already spills into it, and the assembler
cannot express an offset within a slot. So `MODULE_ORIGINS` is left empty on
purpose and `window_faults()` prints the collision on every build instead. Two
wrong-but-working things are worse than one honest fault.

**X6's code is class (b), not (a).** 60 position-independent windows of >= 12
bytes, 1919 bytes in all, and **not one** appears verbatim anywhere in either
dump, at any slot, at any offset. Its content has drifted; its placement is
undecidable by byte evidence. This is the direct answer to the brief's guess that
the six `ASSUMED` modules were all placement bugs. Three of them are not.

## 6. The boot path: the release's `reset` is this `reset` plus ten bytes

Side by side, from `GAPMAP.md` (file offset = CPU + `$10000`, so the fixed window
needs no slot arithmetic and this comparison is real):

| | ours | release |
|---|---|---|
| `$F9C6` | `78 sei` | `78 sei` |
| `$F9C7` | `A2 00 ldx #$00` | **`A9 40 lda #$40` / `8D 01 A0 sta $A001`** |
| | | `A2 00 ldx #$00` |
| `$F9CD` | `85 08 sta $08` | **`86 08 stx $08`**, before `cld` |
| `$F9D3` | `10 FB bpl` | `10 FB bpl` |
| ... | identical | identical ... |
| before `jsr initcols` | -- | **`A2 08 / CA / D0 FD`** (a 5-byte delay) |

Then `stx $4010`, `lda #$40`, `sta $4017`, `lda $2002`, `lda #$10`, `tax`,
`sta $2006` x2 -- **instruction for instruction identical**. The release's
`reset` is this source's `reset` with **ten bytes inserted** and one `sta p0` /
`stx $08` reordering. Class (b), and it explains the whole shape of the X7
difference: the two insertions are `$A9 40`+`8D 01 A0` and `A2 08`+`CA`+`D0 FD`,
so everything after `reset` in the release sits **10 bytes ahead** of where this
source puts it. `nmi` is 3 bytes early for a separate reason: this source's `nmi`
reads `$2002` (`X7.PDS:907`) and the release's does not.

## 7. `SAM.SAM` is at `$FC40`, not `$FB80` -- and that dissolves the X7 overrun

`X7.PDS:977-979` guards with `if last>$fb80 / error ">$FB80!"`, and `X7.PDS:997`
loads the samples at `$fb80`. The build's `last` reaches `$FB90`, 16 bytes past,
so the samples overwrite 16 bytes of X7's own code, and the build has been
reporting that as a fault.

Measured: SAM.SAM's first 32 bytes occur **exactly once** in the whole 131 072
byte PRG, at file `$1FC40` -- CPU `$FC40`. The first 864 bytes of
`DAT/MUS/SAM.SAM` are byte-identical there, `$FD9F`-`$FFEF` is zero, and `$FFF0`
carries the cartridge's own `db "MAGIC1+"`. So the release carries
`SAM.SAM[0:$360]` at `$FC40`, `$C0` bytes above where the February 1990 source
puts it.

**The 16-byte overrun is not a bug in the build.** It is the same stale constant
as the guard: the source's own `SAM.SAM` lands at `$FB80` and eats `$FB90`-`$FFEF`
of X7. Nothing collides once the samples are at `$FC40` -- there are 176 bytes of
room. Class (b), and it is the one region shipped in `asm/patches.manifest`.

## 8. X2's placement is measured, and it contradicts the source's own call graph

The position-independent window probe (`tools/gapmap.py --probe`) searches each
module's windows at every slot and every offset. X2's result is not ambiguous:

```
X2.PDS, 114 windows of >= 8 bytes -> release: 17 hits, 16 of them at delta +0x04AFC
X2.PDS,  67 windows of >=10 bytes -> release: 10 hits, 10 of them at delta +0x04AFC
X2.PDS,  67 windows of >=10 bytes -> beta:    10 hits, 10 of them at delta +0x04AFF
```

and scoring X2's own bytes:

| placement | release | beta |
|---|---|---|
| slot 1 + `$0000` (current) | 26/8192 = **0.32%** | 25/8192 = 0.31% |
| slot 3 + `$0AFC` (measured) | 993/8192 = **12.12%** | 326/8192 = 3.98% |

Six times the best score any module achieves at any other slot. **X2's code
belongs at file `$06AFC`.** And note X6 emits 3063 = `$0BFB` bytes: slot 3's
`$0000`-`$0AFB` is exactly X6-shaped and X2 begins at `$0AFC`. The two fit.

**It is not acted on, and the reason matters.** `X1.PDS:719` is `jmp firespell`,
a direct absolute jump from X1 -- which is chained into slot 0 -- so `firespell`
must be reachable in the `$8000`/`$A000` windows with slot 0 and slot 1 mapped.
File `$06AFC` is slot 3, which `farjsr67` never names: the source contains exactly
three `bnk 6,#N` and two `bnk 7,#N` with literal N, and none of them is 3. So
either the release reorganised X2's placement relative to this source, or
`MODULE_NOTES`' reading of `farjsr67`'s arguments is wrong.

Moving X2 also forces a second decision with no evidence behind it: at origin
`$8AFC` X2 spans two windows, and nothing measured says which slot is in the
second. Getting the first half right and guessing the second is how X5's `sql`
table was destroyed once already (see `01-...md`, `Assembler.prg_offset`).

**Left as the leading measured candidate, not applied.** It is worth more on the
page than in the build until the `jmp firespell` contradiction is resolved.

## 9. `SEQ.SRC`'s overflow: undecidable, and therefore not filled

`SEQ.SRC` is assembled at `org $a000` and spans `$A000`-`$C676`; the `$C000` half
(1716 bytes) addresses bank `$0E`, which `SEQ_WINDOW_SLOTS` declines to guess, so
those bytes are dropped and printed.

The `$A000` half is **strongly confirmed**: 78 position-independent windows, and
29 of them land in slot 5 with a modal delta of `+0`, on top of the 24-byte
literal already in `build.py`'s comment.

The `$C000` half is not. The first 14 bytes of our `$C000` region match the
release at file `$0C000` -- slot 6, not slot 14 -- and then diverge at byte 15
(`81 58 F5 8B` against `81 53 ED 96`). One 14-byte partial hit out of 131 072,
immediately broken, is not a confirmation. MMC3 says `$C000` is slot 14 and the
byte evidence says slot 6, and the region is 1654 bytes of *code* (`lda $800D,y /
sta zp / jmp ($00E4)`), not tables.

Class (a)-suspected, unproven, **not filled**. Copying 1654 bytes of cartridge
over it would erase the only trace of the question.

## 10. Classification, with what each one rests on

| region | class | rests on |
|---|---|---|
| X0.PDS code | **b** | 25 windows >= 12 B, none in either dump at any address |
| X1.PDS placement | **correct** | 5 windows, modal delta **+0** against the release (`9.82%` of its bytes, against `1.97%` for the Beta). The chaining is measured, not argued |
| X2.PDS placement | **a**, measured, not applied | 16/17 windows at delta `+$4AFC`, slot 3; contradicted by `X1.PDS:719 jmp firespell` |
| X3.PDS code | **b** | 45 windows, none found |
| X4.PDS placement | undecided | 4 hits, 4 different deltas, no consensus |
| X5.PDS placement | **correct** | 12 windows at delta +0; plus the `sql` table byte-identical at `$1C000` and `$1C080` |
| X6.PDS content | **b** | 60 windows, none found |
| X6.PDS slot | **a**, unfixable | both dumps' `reset` calls `initcols` at `$F04A`/`$F00D` -> slot 15 |
| X7.PDS placement | **correct** | 317 windows at delta +0 |
| X7 `reset` | **b** | +10 bytes of cartridge code, listed instruction by instruction in §6 |
| X7 `nmi` | **b** | this source reads `$2002`, the release does not |
| SAM.SAM at `$FC40` | **b, filled** | one 32-byte literal hit in 131 072; 864/864 bytes identical |
| `curchrpal` `$04D4` vs `$0180` | **c or b** | one instruction. Not diagnosed |
| SEQ.SRC `$C000` half | **a**-suspected | 14 bytes then divergence. Not filled |
| CHR 8/32 pages | **d** | artwork identical, order wrong. Not attempted |

## 11. Which dump to fill from: measure it

| | release | Beta |
|---|---|---|
| whole-PRG match | 39 209 (29.91%) | 39 387 (30.05%) |
| X1.PDS at its measured placement | **9.82%** | 1.97% |
| X2.PDS at its measured placement | **12.12%** | 3.98% |
| X7.PDS (its level data) | 32 175 (45.3%) | **32 532 (45.8%)** |

Whole-image the Beta is ahead by **177 bytes, 0.14 points**. That is a tie, and it
cuts both ways: for the two modules whose placement is actually *measured*, the
release is three to six times better, and for X7's level data the Beta is
slightly better. Neither dump is close enough to a February 1990 source for the
remaining differences to be individually diagnosable, so "the Beta is nearer the
source" buys nothing at 0.14 points.

**Recommendation: keep `release` pinned.** It is the shipped revision, it is what
README.md records, and it wins decisively on every module whose placement is
known. The pin has **not** been changed. `asm/patches.py` carries both dumps in a
registry with digests and every manifest region names one, so switching is a
one-line change if the user decides otherwise.

## Ruled out

- **`exitm` accounts for a measurable share of the difference.** No. §3: the PRG
  is byte-identical with it suppressed entirely.
- **The six `ASSUMED` modules are all placement bugs.** No. Three of them
  (X0, X3, X6) have code that is in neither dump at any address. Their content
  is class (b) and no placement change will ever make them match.
- **Per-byte slot scoring is at chance because cross-bank references are
  unresolved.** Half right. It is also at chance where the placement is *provably
  correct*: X5 and X7 score 11.8% and 45.3% at their known-good slots, and X2
  scores 0.32% at its assumed slot and 12.12% at its measured one. The metric was
  not broken; the placement was.
- **`SAM.SAM` should be at `$FB80` in the cartridge** (entry 01). Confirmed false,
  and measured this time: `$FC40`. Entry 01's `$0C000` / `$1D000` slot arithmetic
  is also wrong -- the fixed window is file = CPU + `$10000`, so slot 14 is
  `0x1C000` and slot 15 is `0x1E000`.

## Open, in dependency order

1. **The `zp`/RAM allocation drift.** `curchrpal` is `$04D4` here and `$0180` in
   the cartridge; `rn` is `$0D` in both. Every zero-page operand past the drift
   point is wrong, which is a large share of the 61 433 differing bytes and it
   makes code comparison meaningless for any module that uses RAM. Find the
   `zp` allocation that diverges. **This is the biggest single lever left.**
2. **Resolve `jmp firespell`.** Either it moves X2 to slot 3 + `$0AFC` or
   `MODULE_NOTES`' reading of `farjsr67`'s arguments is wrong. One of them is
   certainly wrong and both are load-bearing.
3. **X6 into slot 15**, which needs the assembler to express an offset within a
   slot and needs something to pin that offset down.
4. **SEQ.SRC's `$C000` half.** Slot 6 or slot 14; the two disagree and the region
   is code.
5. **X4's placement.** 172 windows, 4 hits, no consensus -- the module is too
   different for this method.
6. **Then CHR** (class d), then `crates/` (not started, nothing depends on it).

## Repository state

Nothing was committed. The tree started with eight modified files
(`PROVENANCE.md`, `README.md`, `asm/build.py`, `asm/pds6502.py`,
`journal/01-six-modules-with-no-slot.md`, `tools/bizhawk_probe.sh`,
`tools/slotscore.py`, `tools/whowrote.py`) and this session added:

```
 M Makefile                       make really assembles; make check fails loudly
 M README.md                      the numbers and the pin count, corrected
 M asm/build.py                   MODULE_ORIGINS, window_faults, patch accounting,
                                  the placement table, SEQ.SRC's provenance
 M asm/mkrom.py                   applies asm/patches.manifest
 M asm/pds6502.py                 the relative-branch off-by-one (§4)
 M tools/pds_extract.py           --check reads bytes (§2)
?? GAPMAP.md                      generated; `make gaps`
?? asm/patches.py                 the manifest applier
?? asm/patches.manifest           the manifest itself: ranges, classes, digests
?? journal/02-a-gap-is-not-one-thing.md   this file
?? tools/gapmap.py                the comparison and the window probe
```

No cartridge bytes are in any of them. `asm/patches.manifest` is 46 lines of
text; `asm/patches.py` refuses to fill a region unless the named dump's body
SHA1 matches both the manifest and the file on disk, which was verified by
pointing `release` at the Beta file (exit 2, nothing applied).

**The black screen is not fixed.** No emulator was run. `asm/out/magician-rebuilt.nes`
still has `reset` writing `stx $2001` with `$00`, which is the whole of the
observed black frame, and §5 shows `reset` still calls `initcols` at an address
whose slot is known to be wrong.
