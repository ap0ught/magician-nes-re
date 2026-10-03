# 03 - The tracer was the instrument, and the instrument was wrong

2026-10-03

Append-only, in the style of `02-a-gap-is-not-one-thing.md`. Where this entry
contradicts an earlier one it says so and gives the measurement.

**Where things stood.** HEAD `66a335c` (the vendored source), branch
`fix/boot-from-source`, PR #1. Baseline for every "before" here is
`39 204 / 131 072 (29.9%)` from `02-...md` §"Where things stood"; after this
session it is **39 832 / 131 072 (30.4%)**, and the 0.5-point move is *not* the
headline -- three of the four defects below moved no byte-match number at all.

---

## 1. The tracer was corrupting the game's RAM, so every conclusion drawn with it was suspect

`tools/nestrace.py` decided accumulator-mode shifts by asking whether the
*previous* instruction was one:

```python
def _is_acc(self):
    return self.log and self.log[-1][2] in (0x0A, 0x2A, 0x4A, 0x6A)
```

`self.log` is appended to *after* `_exec` returns, so during `_exec` it describes
the instruction before the current one. For `asl a` the mode string is `"acc"`,
which matches no branch in the decode switch, so `addr = val = 0`, and the
handler took the zero-page path: it read RAM `$00`, shifted it, and **stored the
result back into RAM `$00`**, never touching A. `x0.pds` uses `asl a` in at
least eight places (`X0.PDS:659` among them).

Fixed by passing the current instruction's mode into `_exec` and reading
accumulator mode off it. The four accumulate-mode opcodes are exactly the four
the old check listed, so the predicate is unchanged in intent. Also deleted a
dead `(addr == self.pc - 1 and log[-1][2] == 0x0A)` branch that only ever
`pass`ed.

**What this changed.** `movepal` reads real palette bytes now. Before the fix,
`X7.PDS:539`'s `lda (t0),y` was reading through RAM `$00`; after it, the trace
shows `A=30`, `A=31`, `A=30`, ... which is `TIT.PAL` read from `$FB48+y`.

---

## 2. `lda (t0),y` was assembled as `lda (t0,x)` -- 54 times, in all eight modules

This is the one that mattered, and it was in the assembler, not the tracer.

```python
close = operand.find(")")
indexed_y = indirect and operand[close:].lower().lstrip() in (",y", ", w", ",w")
```

`close` is the index *of* the `)`, so `operand[close:]` starts at the bracket and
reads `"),y"` -- which is not an index at all. Every `(zp),y` in the source
therefore selected `indx` and emitted `$A1` where the opcode is `$B1`.

Counted with the assembler's own statement parser: **54 statements, in x0, x1,
x3, x4, x5, x6 and x7, and zero `(zp),x` spellings anywhere in the tree** -- so
every one of them was wrong, not some. Among them: `x0.pds`'s `moveb2` level
decompressor (`lda (t2),y` / `sta (t2),y`) and `x7.pds`'s `movepal`.

The cartridge decides it. At file `$1F772` the release has

```
A0 0B 8A 29 03 C9 01 A9 0F 90 03 B1 11 88 9D FC 06 ...
```

and the rebuild had `... 90 03 A1 13 88 9D B4 04 ...`. `$B1`, not `$A1`. The
operand differs too (`$11` vs `$13`) -- that is the ZP map, see §6.

**The match percentage went *down*, 30.6% -> 30.4%, and that is the correct
outcome.** Byte-match against a later release rewards a wrong-but-self-
consistent build; `moveb2` copying through the wrong address produces bytes that
happen to agree with the release more often than correct ones do. The number is
not evidence here.

---

## 3. The MMC3 PRG-mode bit was being read from the data port, so the vectors were garbage

`tools/nestrace.py` took `prg_mode` from bit 6 of a write to the odd port of the
`$8000`/`$A000` window. On the chip the mode comes from `$8000` (even), `$E000`
and `$E001`; `$A001` is a plain data write to whichever register is selected.
The cartridge's own `reset` does `lda #$40 / sta $A001`.

With mode stuck at 1 the tracer's model put R7 in the `$E000` window, so the
reset/IRQ vectors were read from whatever the banked window held. The cartridge
vectored through garbage and spun on `BRK` at `$1000`, 502 211 times. After the
fix it gets as far as filling 432 nametable bytes and then halts on an illegal
opcode at `$841A` -- still short of BizHawk, but no longer stuck at reset.

Two smaller faults in the same model: reading `$8006` returned PRG bytes instead
of the scanline counter, and two write paths XORed `prgram` at a CHR-derived
index, which both `IndexError`s above 8 KiB and has no business corrupting
`$6000`-`$7FFF`.

**This is the honest limit of the tracer: it does not boot the cartridge either.**
So "the rebuild is black" is not yet a statement about the rebuild. What *is*
sound is the tracer-vs-tracer comparison, and the structural findings below.

### 3a. PRG mode must not be read from `$E000`/`$E001` -- and that one was worth both ROMs

With the mode bit read from `$8000` alone (see §3b) both images run 120 frames
without halting. Before it, both halted inside frame 4 with R6 and R7 swapped.

### 3b. PRG mode comes from `$8000` bit 6, and the source proves it

NESdev lists bit 6 of `$E001` as PRG mode on MMC3, and the tracer was honouring
it. This game's IRQ handler is

    irq     pha / txa / pha / tya / pha / sta $e000 / sta $e001   ; "clear MMC3 IRQ"

with A = Y, so under that reading the bank windows swap on Y alone -- and
`reset` needs mode 0, because it sets `bnk 6,#$0` / `bnk 7,#$1` and then
`jmp start`, which puts its own main loop at `$8000`. A game that broke itself
every other interrupt is not what ships.

The source settles it without appeal to a datasheet: the only place this game
ever writes a mode bit is `stx $8000` with a value of 0-7, inside the `bnk`
macro (`x0.pds:188` -> `setbank`, `x7.pds:793`). So the mode bit is read from
`$8000`/`$A000` even ports and nowhere else. `$E000`/`$E001` are the interrupt
controls, and `$E001` bit 7 disables the IRQ.

Measured, 600 frames, `--where w:2007`:

| | before | after |
|---|---|---|
| cartridge | HALT `illegal opcode $12 at $841A`, 3.6 frames | 600 frames, no halt |
| rebuild | HALT `illegal opcode $7A at $010A`, 3.6 frames | 600 frames, no halt |

Both still have an all-`$0F` palette and both still spin in a wait loop, so
neither is fixed -- but "runs 600 frames" and "vectored through garbage at
frame 1" are very different places to be debugging from.

**A trap worth recording.** `tools/dis6502.py --cart --from 0x8378` reported
`AA AA AA 2A AA` at `$8380`, and the cartridge's own code at `$8382` is
`20 25 F8` = `jsr rn`. I spent a step believing the cartridge was executing a
data table. The raw bytes (`slot0 $0380 = CD E2 20 25 F8 A5 40 D0 F9`) are the
authority; do not trust a disassembly whose bank you have not checked.

---

## 4. `resolve()` wrote `self.prg[-1]` -- the IRQ vector's high byte

`prg_offset()` answers `-1` for a byte at or above `addr_ceiling`, which the new
`X6_CEILING` makes reachable in normal operation. `emit()` guards for that;
`resolve()` did not, so a dropped branch's displacement byte landed on
`self.prg[-1]` == file offset `$1FFFF`. Fixups resolve in list order, so which
branch corrupted the vector depended on how many modules ran first.

Guarded the same way `emit()` is, and the dropped branch is now reported in
`overflow` rather than vanishing.

---

## 5. Four measurement tools measured nothing, and one of them was the source of the ceiling numbers

`Assembler.run_all` passes `addr_ceiling=` for *every* module, so the `run_file`
overrides in `gapmap.py`, `whowrote.py`, `slotscore.py` and `modrange.py` raised
`TypeError` on their first call. Three also indexed `ASSUMED_SLOTS[m]` for all
eight modules, and X4/X6/X7 are in `PINNED_SLOTS`, so they died with `KeyError`
first.

`modrange.py` was the worst: its `TypeError` was caught by the bare
`except Exception` around the project pass, which logged one line and `break`ed
out of the retry loop -- leaving `image` as the zero-filled `bytearray` allocated
at the top of that loop and returning it as a build. The tool then printed a
plausible per-module table in which every module wrote 0% of its slot. The
ceiling arithmetic in `build.py` gets its numbers from `modrange.py`, so **the
682-byte justification in the commit message and on the PR was not reproducible
and should not be cited.**

---

## 6. Things measured this session that contradict earlier notes

* **X6's placement.** `MODULE_NOTES["X6.PDS"]` said "slot 3, by elimination"
  while `PINNED_SLOTS["X6.PDS"]` was 15, so every build log contradicted itself.
  Fixed. It is pinned to 15 because `reset`'s `jsr initcols` targets the fixed
  `$E000` window; the *origin* is `$E605` because X6 has no `org` and follows
  X5's ceiling. Two different questions, and `GAPMAP.md` used to conflate them.
* **The ceiling drops are 517 + 150 = 667 bytes, not 394/666/176.** Nothing
  printed them -- `overflow` is cleared per file -- so `Assembler.ceiling_drops`
  is new and the build now prints it. X5 gives up `$E605-$E809` (517 bytes), not
  "its last 176".
* **`initspr` is not dropped.** It assembles at `$F033`, 307 bytes *below*
  `X6_CEILING = $F166`, so `jsr initspr` lands in X6's own code and the tracer
  reaches it (cycle 88082). The claim that the ROM "draws the title screen's
  background and not its sprites" was wrong in its mechanism.
* **The RAM/ZP map is genuinely different from the release, and that is not a
  bug.** The release has `t0 = $11`, `orgchrpal = $06FC`, `curchrpal = $0180`;
  this build has `$13`, `$04B4`, `$04D4`. Both are self-consistent -- every
  module uses the same `zp`/`ram` declarations in `x0.pds:363+` -- and the
  palette path is correct under either: `nmi0` (`x5.pds:176`) pushes
  `curchrpal-$60 .. curchrpal` to `$3F00-$3F60`, and `curchrpal` aliases `$3F00`
  because `$3F60 & $1F == 0`. Chasing the release's addresses would be matching
  the cartridge, not fixing the build.

---

## 7. What the rebuild actually does now, and where it stops

`tools/nestrace.py --where`, 120 frames:

    cycle 57224  initcols    $EFEE
    cycle 60991  dotitle     $802F
    cycle 65504  unrunscn    $DC6D
    cycle 88082  initspr     $F033
    cycle 89167  nmi         $F9A8   -> tnmi, 256-byte palette push
    cycle 98674  addmsg's `rts` at $F1A5 finds an empty stack
    cycle 98680  HALT: illegal opcode $7A at $010A

So: reset, `initdma`, `initcols`, `setchr`/`setspr`/`movepal` (which now read
the right bytes), `unrunscn`, `initspr` and the NMI all run. `im` (`x5.pds:319`,
the `orgchrpal -> curchrpal` copy) and `nmi0` (`x5.pds:167`, the per-frame
palette push) are never reached, because execution leaves the program's own code
first: a **lost stack frame**, at `dobullets|!b`'s `jmp miscmsg` chain. The
palette stays `$0F` because the routine that would copy it never runs, not
because the ceiling dropped it.

Nametable bytes nonzero rose 280 -> 659 across §1 and §2 (block `$0000` went
44 -> 255, i.e. completely full). Max luminance over the 256x240 picture area is
still 0, and the frame is black.

---

## 8. Dead ends, kept so they are not repeated

* **Matching the release's `sfx N,jmp` / `jsr setfx` split by counting.** It
  already matches: 11 `jmp setfx` sites in the image, 11 `sfx N,jmp` uses in the
  sources, and `ifs [@2] []` evaluates correctly. Counting macro *expansions*
  instead of emitted bytes gives 615 and looks alarming; the `else` arm is parsed
  on every expansion and only emitted when the condition is false.
* **Proving the stack imbalance by replaying `jsr`/`rts` pairs.** The game is
  full of `jmp`-tail-called routines (`farjsr67`, `setfx`, `farjsr67`'s `ret`),
  so an unmatched `rts` is normal and a shadow-stack replay finds four, all of
  them legitimate. What localises it is SP, not the call graph: SP is `$FF` with
  nothing above it at `$8012`, so `$F1A5`'s `rts` wraps the stack pointer and
  reads `$0100`/`$0101` -- the DMA buffer at `x7.pds:868`. Next step is to audit
  the `swapstk` pairs (`x0.pds:208`) in the chain that reached `$8008`.
* **Trying to read the cartridge's palette path out of its own code to copy the
  addresses.** `$0120` (where `nmi0` reads from in the release) is written by no
  static store anywhere in the cartridge; it is reached through the generic
  `lda ($11),y / sta ($13),y` block-copy loop. Dead end -- stop looking for a
  writer.