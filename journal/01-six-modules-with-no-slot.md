# 01 - Six modules and no slot

2026-10-02

**Goal.** Place all eight `X?.PDS` modules in the 128 KiB PRG image so the rebuilt
cartridge boots, and settle whether the rebuild can be a usable base for the
moddable core at all.

**Where things stood.** `master`, three commits, HEAD `a817dcf` "Pin X7 to slot 15
from the cartridge's vectors; add the iNES wrapper". The working tree had
uncommitted changes to exactly two files — `asm/pds6502.py` +76/-9 and
`asm/build.py` +38/-17, `git diff --numstat`. Nothing was staged. This session
changed no code; it measured, and corrected the documentation.

The last edit before this session had been written but never run. Running it is
the first thing this entry has to say, because it is not what the session
expected.

## The build crashes; it does not exit 1 with a tidy message

```
$ make -C asm
python3 /home/cmayfield/code/games/magician-nes/asm/build.py
Traceback (most recent call last):
  ...
  File ".../asm/pds6502.py", line 1066, in line
    self.instruction(ln)
  File ".../asm/pds6502.py", line 1271, in instruction
    raise AsmError(f"unknown instruction {mnemonic!r}", ln.where)
pds6502.AsmError: .../vendor/Magician-NES/X7.PDS:184:184: unknown instruction '0,1'
make: *** [Makefile:20: .../asm/out/prg.bin] Error 1
```

`python3 asm/build.py` exits 1, `make -C asm` exits 2. Neither prints
`*** INCOMPLETE BUILD`. That message does exist and is correct — it is just no
longer reachable, because the crash happens first, at `asm/build.py:130`, inside
the `PINNED_SLOTS` branch, which calls `asm.run_file()` with no `try` around it.
Every other `run_file` in that function is guarded; the pinned one is not.

Reverting only the change described below in a scratch copy reproduces the
expected state exactly:

```
*** INCOMPLETE BUILD: 6 of 8 modules have no determined 8 KiB slot and were NOT assembled:
***   X0.PDS, X1.PDS, X2.PDS, X3.PDS, X4.PDS, X5.PDS
***   The PRG above is missing them. Pass --allow-incomplete to exit 0 anyway.
```

exit 1. So the `INCOMPLETE BUILD` behaviour is real and was measured; it is one
uncommitted edit away from being unreachable.

## The last uncommitted edit trades one unfixable failure for another

`asm/pds6502.py:562` decides whether a no-operand macro takes the next token as
its argument, and the new test is `not words[i].col0` — "the next token is not at
column 0". It fixes `X4.PDS:160`:

```
'pca0\thex f7\t;casting frames hi,mid,lo\r\tdone\rpca1\thex 0f\r\tdone\rpca2\thex f6\r\tdone\r'
```

which used to cut `done` + `pca1` together, emitting `db a_done` and leaving
`pca1`/`pca2` undefined. With the new test, all three labels survive.

It breaks `X7.PDS:184`:

```
onechr	swapstk 0,1
```

`Word.col0` is set from the **physical segment**, in `words_of`
(`asm/pds6502.py:447`): `col0 = seg[:1] not in (" ", "\t")`. Every token in a
segment inherits that segment's column. So for a line that *starts* at column 0,
all three tokens — `onechr`, `swapstk`, `0,1` — are `col0=True`, and `0,1` is
correctly, and uselessly, treated as the start of a new statement:

```
$ python3 -c "... words_of + scan_statements on X7.PDS:184 ..."
WORDS: [('onechr', True), ('swapstk', True), ('0,1', True)]
  LINE raw='onechr swapstk' label='onechr' op='swapstk' operands=''
  LINE raw='0,1' label=None op='0,1' operands=''
```

The field that would settle it is not there. `Word.line` carries the **logical**
line number — `logical_lines` passes one `lineno` per logical line
(`asm/pds6502.py:621-630`), and the editor's soft wrap is inside that logical
line. So a token cannot be asked "did you start a new physical line?"; a segment
index would have to be recorded, and it is not.

**This is not a one-line fix, and it is the blocker.** Both failure modes are
live and mutually exclusive:

| state | what happens |
|---|---|
| `col0` test reverted | `pca1`/`pca2` undefined → X1.PDS:738 fails → the whole slot-layout hypothesis cannot be run |
| `col0` test kept | X0–X6 rejected at **all sixteen** slots; X7 raises on the pinned path → nothing assembles at all |

The second row is worse than the crash suggests. With the working tree as it
stands, every unplaced module is rejected for an assembler reason, not for lack
of evidence — and the message the build would print for that is the same
`SLOT NOT DETERMINED` wording used for "no evidence". Measured, one probe over
all sixteen slots per module:

```
== X0.PDS:  slot 0 REJECT AsmError: X0.PDS:188:188: lda needs an operand
== X1.PDS:  slot 0 REJECT AsmError: X1.PDS:379:379: unknown instruction '7'
== X2.PDS:  slot 0 REJECT AsmError: X0.PDS:188:188: lda needs an operand
== X3.PDS:  slot 0 REJECT AsmError: BUL.SRC:59:59: unknown instruction '03'
== X4.PDS:  slot 0 REJECT AsmError: X4.PDS:133:133: expression ended early  [!e popbnk]
== X5.PDS:  slot 0 REJECT AsmError: MISC.SRC:194:194: unexpected token '$' in expression  [unfly sfx]
```

**`X4.PDS:162` is still mis-cut even with the fix in place**, which is the same
class of defect and worth knowing about before anyone tries again:

```
'pde0\ttime 10\t\t;physical death\r\tsdfx 13'
  raw='pde0 time' label='pde0' op='time' operands=''      <- operand dropped
  raw='10 sdfx 13' label=None op='10' operands='sdfx 13' <- bogus statement
```

## Assembler bugs closed this session

Ten were committed in `3355aba`; five are in the working tree. Each failed
silently or as a misleading error, and each cost real time. Recorded as dead
ends, so none of them is re-chased.

Committed (present at HEAD, verified by `git show 3355aba:asm/pds6502.py` and
`git show 3355aba:asm/build.py`):

1. **`\S+` in the tokeniser ate a string's opening quote.** `db hyp,hyp," QUIT
   GAME ",hyp,hyp+$80` (`MISC.SRC:757`) became three statements, shifting every
   address after it. `WORD_RE` (`asm/pds6502.py:423`) makes a quoted string
   atomic first.
2. **`prescan` deleted the soft wrap instead of treating it as whitespace.**
   `text.replace("\r", "")` fused `pti<CR>macro` into `ptimacro`; `pti` went
   unknown and its body assembled as the bare instructions `pti`, `10`.
   Now `text.replace("\r", " ")` (`asm/pds6502.py:873`).
3. **`run_file` reset slot, fixups and conditionals between passes but not the
   address counters.** A run that raised left its address behind, and X7 — which
   has no leading `org` — started there. It surfaced as `error ">$FB80!"`, which
   is why all sixteen of X7's slots looked broken. Base address now comes from
   `Assembler.slot_origin()` (`asm/pds6502.py:784`).
4. **`set` was in `DIRECTIVES`**, so `SHOPDAT.SRC`'s `set equ $07` lost its
   label. It is only ever a symbol in this source (`asm/pds6502.py:107`).
5. **`hex @5`** in X0's `l0` macro: `@5` is not a hex run at definition time and
   matches `LABEL_RE`, so it was cut off as a label. `MACRO_PARAM_RE`
   (`asm/pds6502.py:402`).
6. **`org *&$dfff`** handed `&$dfff` to `int()`. `org_apply`
   (`asm/pds6502.py:1212`) strips leading operators, not just a `$`.
7. **`LABEL_RE` did not allow `@` inside a name**, so `pti`'s `pi@1 equ li`
   assembled as the instruction `pi10` (`asm/pds6502.py:117`).
8. **`substitute()` rewrote `raw`/`label`/`operands` but not `op`**, so a macro
   invoked through a parameter dispatched on the literal `@1`
   (`asm/pds6502.py:1101`).
9. **A label redefined at the *same* address within one pass is now an error**;
   at a different address it is allowed and reported (`asm/pds6502.py:754`).
   Same-address-only is the precise rule available, because `memchk`
   legitimately emits a group into both slots of a 16 KiB bank and X5's `sql`
   table really is emitted twice, at `$C000` and `$C080`, both present in the
   cartridge.
10. **The CHR packer computed every file's page from `len(image)//4096`** — a
    constant, so nothing was placed — and `DAT/` names are upper case on a
    case-sensitive filesystem while `SENDG` and the source write them lower.
    `asm/build.py: dat_file()` resolves case-insensitively.

Uncommitted (working tree only, **not yet verified end to end** — the build
crashes):

11. `snapshot()`/`restore()` did not cover `self.slot` or the address counters,
    so it was not a restore. This is what stacked six modules into slot 15.
12. Macro *bodies* were only registered when the defining module executed, so no
    module could be assembled on its own — which makes per-module slot scoring
    structurally impossible. `collect_macros()` (`asm/pds6502.py:877`). All 40
    macros are in X0.PDS.
13. **An unterminated string did not swallow the rest of the segment.** In
    `memchk`'s `error "** exceeded $@1`, `$@1` was left bare, the scanner read it
    as an instruction and took `endif` as its operand, so `memchk` never closed
    its conditional and **the whole rest of X7 assembled inside a false `if`**.
    That is why `btit`, `bpw`, `bpan`, `bev` were undefined. Now judged per
    segment on an odd quote count (`asm/pds6502.py:452`).
14. **`asl a`** — `a` is the accumulator, not a symbol; it was resolving as a
    reference and reporting `undefined symbol 'a'`, stopping the project pass
    (`asm/pds6502.py:1308`).
15. The macro-operand column test described above. **This is the regression.**

## Ruled out

**Scoring all emitted bytes identifies the slot for X0–X5.** It does not. Best
slot per module, all emitted bytes against the cartridge, measured:

```
== X0.PDS:  slot 12     52/ 1294 ( 4.02%)     == X3.PDS:  slot  9    261/ 3208 ( 8.14%)
== X1.PDS:  slot  9    156/ 2378 ( 6.56%)     == X4.PDS:  slot  0   1704/ 8192 (20.80%)
== X2.PDS:  slot  0   1626/ 8192 (19.85%)     == X5.PDS:  slot 14   1200/ 8192 (14.65%)
```

Flat or contradictory. X2 and X4 both peak at slot 0; X1 and X3 both peak at
slot 9; X0 peaks at slot 12, which is X6's slot. Two modules claiming one slot
is the metric contradicting itself, not evidence. The cause is understood:
unresolved cross-bank forward references make every `lda label` choose zero-page
over absolute, so most bytes are noise regardless of the right slot. Do not
re-chase this without first making the other banks' addresses known.

**X0, X6 and X7 are the only modules that touch data.** The shape is right and
the first number is off by one. Measured over the decoded source — logical lines
(split on the soft wrap), comments stripped, counting `load` as a *token*
rather than only where it starts a line, because most calls are `label<TAB>load
file` — and separating the macro definition from its calls:

```
file     load calls  load macro def  raw "dat\" lines
X0.PDS            0               1                2
X1-X5.PDS         0               0                0
X6.PDS            1               0                0
X7.PDS           79               0                0
```

X0 has **zero calls**. The `load` macro is *defined* at `X0.PDS:75` and its body
is `incbin dat\@1` at `X0.PDS:86`; both of X0's two `dat\` mentions are those two
lines (the body, and the comment at `X0.PDS:72`). X0 *defines* the macro that X6
and X7 *call*. The earlier "(1, 1 and 79 uses)" was right about X6 and X7 and
wrong about X0 by exactly the one definition.

X1–X5 really are pure code, which is the load-bearing half of the claim and it
holds. That is why a byte-scoring slot search has nothing to score for them.

*Corrected in place 2026-10-02: the figure for X7 first recorded here as 51 was
wrong — it missed every `label<TAB>load` call. The token-level count above is
79, which agrees with the 79 in the older wording.*

**`slot & 7` is the MMC3 mapping.** No. 8 KiB slot `n` is presented at
`$8000 + (n & 3) * $2000`; only four slots are visible in the `$8000-$FFFF`
window, and `slot & 7` produces logical addresses above `$FFFF`. Correct in
`Assembler.slot_origin()` (`asm/pds6502.py:795`). Slot 14 is at PRG file
`0x1C000`, slot 15 at `0x1E000` — **not** `0x1D000`, which is what §4 used to
say. The whole fixed window is `file offset = CPU address + $10000`, not
`+$8000`. Verified three ways against the cartridge: x5's `org $c000` `sql`
table at `0x1C000`, `"MAGIC-* "` at `$FFF0` = `0x1FFF0`, vectors at `$FFFA` =
`0x1FFFA`.

**`DAT/MUS/SAM.SAM` should be at `$FB80` in the cartridge.** It is not. The
source says it is loaded there; the cartridge has `38 30 28 38 30 0f 27 37 0f`
and `SAM.SAM` starts `aa 2a 1f e3 c7 18 01 00`. Cartridge slot 15 is non-zero for
6 865 of the 7 040 bytes from `$E000` to `$FB80`. This is the source-versus-
cartridge difference §3 predicts, not an assembler fault.

**`crates/mag-core/src/rom.rs` pins the cartridge digest.** No such file; there
is no `crates/` directory. Nothing in `asm/` or `tools/` compares the
cartridge against `bd806d7f7c318b8012433250ca10aa8387a962bb` — `asm/mkrom.py:65-66`
only *prints* it in a message. The pin is documentation today.

## Open, in dependency order

1. **Give `scan_statements` a segment index, then re-run the build.** Everything
   else is downstream. Until X0–X7 all assemble, no slot hypothesis can be
   tested and no measurement of the rebuild means anything.
2. **Distinguish "no evidence" from "did not assemble"** in the slot search
   output. Right now both print `SLOT NOT DETERMINED`, and step 1's failure mode
   is invisible in the log.
3. **Place X0–X5.** The live hypothesis: X0–X4 fill 16 KiB banks 0–4 (initial
   slots 0/2/4/6/8 or the odd parity 1/3/5/7/9), X6 at slot 12, X5 at slot 14,
   X7 at slot 15 sharing bank 7 as the fixed window. **Untested** —
   `/tmp/opencode/projprobe.py` fails with `undefined symbol(s): pca1, pca2`,
   which is bug 15. Independent support for X5 → 14: its best all-byte score is
   slot 14 at 14.65%, the highest single score in the table above.
4. **Close the `$1126` vector gap.** Rebuilt `nmi=$E882 reset=$E89B irq=$E88D`
   against the cartridge's `nmi=$F9AB reset=$F9C1 irq=$F9B3`. Relative spacing
   is nearly right (`reset`-`nmi` is 25 bytes against 22), so 3 bytes are emitted
   too many in one small span and the rest accumulates earlier in X7. Until this
   closes the CPU is sent to the wrong address and nothing else matters.
5. **Then the CHR packing order.** 8 of 32 4 KiB pages are identical; the
   artwork itself is byte-identical, so only the order is open.
6. **Only then `crates/`.** Nothing about it is started.

## Two reference downloads, deliberately not in the repository

`~/Downloads/Magician-Game-Manual.pdf` is the official Taxan manual: 22 scanned
pages, **no text layer**. OCR'd at 300 dpi with `tesseract --psm 6`; the scratch
output lives in `/tmp/opencode/magman/` and is not durable. It is the oracle for
data-table validation — exact item effect numbers (HEAL +20%, MANA +500, AMULET
OF SHIELD +15% to all four, RING OF ANA sets food/water to 100% once, AMULET OF
MOR +50% once; bread 20%, chicken 30%, ham 25%, vegetables 40%; water flask
5 drinks × 20%), the Magician rating thresholds keyed to max mana (APPRENTICE
0-499 … MASTER 4000-9999, MAGICIAN 10000+), the venom rule ("roughly 1/12th of
this value is periodically subtracted from your health"), and a per-level
item/spell checklist for levels 1-8. **A durable copy belongs in a private,
git-ignored location with the OCR text beside it — not in this repository, and
not in `vendor/`, which is a submodule of someone else's release.**

`~/Downloads/lpwb-magician-nes-walkthrough.txt` is an auto-generated transcript
of a third-party walkthrough,
`https://www.youtube.com/watch?v=Kp3BWWMXApo` (Video Games 101). Useful for
hunger/thirst drain rates, shield rules, spell costs, the per-chest item list and
the four vials → Ultimate Potion craft. **Third-party copyrighted video
transcript: record the URL and the extracted facts, never the transcript.**

## Repository state

```
$ git log --oneline
a817dcf Pin X7 to slot 15 from the cartridge's vectors; add the iNES wrapper
3355aba Assemble the cartridge from Eurocom's source
c3ec9e7 Pin the Magician cartridge revision; decode the PDS source containers

$ git status --short          # after this session's documentation edits
 M LEGAL.md
 M PROVENANCE.md
 M README.md
 M asm/build.py
 M asm/pds6502.py
?? journal/
```

The three ` M` documentation files and `journal/` are this session's work. The two
`asm/` files were already modified when it started and it did not touch them.
Nothing staged, nothing committed by this session.

**`asm/pds6502.py` +76/-9 and `asm/build.py` +38/-17 are uncommitted, and that
diff is the only copy of five assembler fixes (bugs 11–15 above) plus the
`INCOMPLETE BUILD` policy.** Bugs 1–10 are in commit `3355aba`; bugs 11–14 were
never committed and only exist in this working tree. It is one `git checkout` from
gone, it does not build in its current state, and the reason it does not build is
bug 15, which is also only in this working tree.

`asm/out/` holds stale artefacts and they disagree with each other:
`magician-rebuilt.nes` (15:45) carries the vectors BizHawk was tested against,
`nmi=$E882 reset=$E89B irq=$E88D`; `prg.bin` (16:06, sha256 `f91dead1111b74f9`)
is a *later* run whose vectors are `$29E8/$42E8/$34E8` and which is not the PRG
inside that `.nes`. `make -C asm rom` would produce a third ROM. All of it is
git-ignored; none of it is trustworthy.