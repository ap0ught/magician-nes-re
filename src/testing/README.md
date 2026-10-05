# `src/testing/`

The test suite. One command, one exit code, no emulator and no cartridge.

```
python3 src/testing/run_all.py            # every test_*.py, one process each
python3 src/testing/run_all.py --list     # the files and nothing else
python3 src/testing/run_all.py test_fm2.py test_identity.py
make testsuite-py                         # the same thing through the Makefile
```

## Why this directory exists

There were no tests. `tools/datcodec.py selftest` was 20 checks and
`tools/dis6502.py --selfcheck` was one, and both had to be *remembered*.
Everything else in `tools/` was a script that printed a number.

In that state the project produced these, all plausible, none of them an error:

| instrument | what it reported |
|---|---|
| `tools/bizhawk/run.sh` | **three zero vectors** — `head -c 1 \| tail -c +5` read no byte at all |
| `tools/bizhawk/run.sh` | NMI/RESET/IRQ read in the wrong order; all three numbers looked fine |
| `tools/bizhawk/run.sh` | a "verdict ok" while having read nothing |
| `tools/bizhawk/run.sh` | success with a 0-byte screenshot |
| `tools/bizhawk/zp.lua` | 36 bytes read instead of 256, printed as five tidy hex rows |
| `tools/bizhawk/frame.lua` | `$2006`/`$2007` silently return `$00` on quickerNES |
| `tools/nestrace.py` | a black screen for two different images |
| `tools/nestrace.py` | `asl a` shifting zero-page `$00`, because it read the *previous* opcode |
| `tools/modrange.py` | a 0% match over an all-zero image, from a `TypeError` in a bare `except` |
| `src/play/bridge.lua` | `settimeout(nil)` is not "no timeout", so the loop exited on a healthy connection |
| `src/play/bridge.lua` | a multi-line reply read one line at a time, leaving the rest in the socket |
| `src/play/bridge.lua` | `CPU registers` is 12 bytes of which 8 are readable, so "all nine domains" could not be dumped |
| `src/play/ram.py` | `manacur` read big-endian: 12800 mana for a character with 50 |
| `src/play/ram.py` | the player's position read `obxl[0]` — the slot `initob` leaves empty — so the player was permanently at (65535,65535) |
| `src/play/ram.py` | `jt` read as eight buttons at `$0028-$002F`; `joykey` writes `$002E-$0035` |
| `src/play/emu.py` | the input log wrote "nothing pressed" as a BLANK line, and the loader skipped blanks: 636 frames recorded, 392 replayed |
| `src/play/emu.py` | `run.sh` returns before EmuHawk exits, so the replay emulator was diverted into the run's own |
| `src/play/bridge.lua` | a domain name with spaces read as `dom=CIRAM`, so `domain_read` rejected a correct reply |
| `src/play/recon.py` | `nmiflag` used as a title-screen predicate, but `$002C` is also the UP button |
| `tools/gapmap.py`, `whowrote.py`, `slotscore.py` | the same `TypeError`, from an `Assembler.run_file` signature change |
| a bash guard | `[ x -lt 0x8000 ]`, which does not compare |
| `tools/unrun.py` | `t0 += y` instead of `t0 += y + 1`, so the title screen came out solid brick |
| a hex-byte count | 108 bytes reported where the real figure was several hundred |

None of those is a hard bug. Each is a measurement that cannot be wrong
*loudly*, which is worse, because the number is what gets acted on. The
instruments are the thing that has to be pinned.

## The four rules

`run_all.py`'s docstring says why each of these exists; in short:

1. **A fresh process per file.** These are scripts, not a framework. They
   `os.chdir` at import and several read module-level state, so one interpreter
   would make the result depend on alphabetical order.
2. **An import error is a failure, not a skip.** There is no
   "skip unless the cartridge is present" path: `synthcart.py` builds a
   synthetic image for anything that needed a real one, so no test has a reason
   to ask for one. The single conditional is in `test_fm2.py`, for the movie,
   and it says in its own output whether it ran.
3. **A failing file's output is printed in full**, not the last line.
4. **The counts are counts.** A glob that matched nothing is a failure here, not
   a cheerful "0 tests, all green".

## The rules a test in here has to follow

* **No constant from a previous session's report.** Every expected value is
  derived from a file the test writes (`synthcart.py`), from a file in the
  repository (`asm/pds6502.py`'s signature, parsed with `ast`; the guard's own
  case list; `asm/patches.py`'s digests), or from the format being implemented.
  Where a constant is unavoidable its provenance is a comment saying which
  document or which file produced it.
* **Positive controls.** Where a check could be vacuous, another check shows it
  is not: `test_placement_tools.py` builds a deliberately wrong override and
  shows the check reports it; `test_placement_math.py` asserts the premises of
  the trap it reproduces; `test_nestrace_cpu.py` writes through `$2006`/`$2007`
  so that "VRAM was untouched" is a measurement.
* **A refusal is a result.** A tool that returns a plausible value where an
  error belongs has failed, and `raises()` is used as often as `check()`.

## What is in here

| file | checks | what it pins |
|---|---:|---|
| `test_identity.py` | 34 | `tools/bizhawk/identity.py`: header bytes, the three vectors and **their order**, the 48-byte window and its file offset, and every way the derivation must refuse |
| `test_run_sh.py` | 41 | the real `run.sh`, against a fake BizHawk: missing/wrong ROM, an unidentifiable ROM, a NullHawk verdict, an absent verdict, an expected output missing/empty/truncated, `NES/SaveRAM/` read-only, and the bare-hex bash guard |
| `test_fm2.py` | 41 (+6) | the `.fm2` button order `RLDUTSBA`, the frame *ordinal* rather than the declared index, and `romChecksum` = md5(PRG+CHR) |
| `test_scene_format.py` | 24 | the `.DAT` format against hand-packed streams: `t0 += y + 1`, the run length `(count & $7F) + 4`, and the token test once per *record* |
| `test_datcodec.py` | 20 | `datcodec.py selftest`, invoked by the suite, plus the byte-identical re-encode per file |
| `test_placement_tools.py` | 35 | the four placement tools' `run_file` overrides against the assembler's signature, and that a tool with its tracing flag off records nothing |
| `test_placement_math.py` | 34 | `dis6502`'s opcode table, and slotalign's rule that a **modal displacement is not the origin test** — reproduced, not asserted |
| `test_nestrace_cpu.py` | 28 | the CPU core only. Every check is named `cpu:` and none is about pixels |
| `test_repo_hygiene.py` | 20 | no cartridge, no movie, no save, no archive committed; `guard_staged.sh` exercised against a synthetic index; `vendor/` unmodified |

`synthcart.py` is the shared helper: a synthetic iNES image built from
arithmetic in that file, with the provenance of every constant stated there.

## `test_play_ram.py` and `test_play_actions.py`

The play harness has two files of its own, and both exist because a wrong answer
there is invisible: a predicate on the wrong byte evaluates, returns a plausible
bool, and asserts nothing.

`test_play_ram.py` (28 checks) pins `ram.py` — the single source of truth for
every address `src/play/` uses:

* every field's address is `base + offset` for a name that exists in the
  assembler’s own symbol table, and `field()` refuses a declaration with no
  symbol, no comment, or an impossible length;
* `Field.get` is little-endian, against `manacur`'s documented 50 from
  `x1.pds:48-49`, and `plrx`/`plry` read the PLAYER's object slot (`maxob` is 4,
  so `pi` is 3) rather than slot 0;
* the Python predicate and the bridge's decoding of its own wire format agree on
  all 504 combinations of op × field × value × RAM state;
* the wire encoding is decimal in every field, because the bridge's pattern is
  `%d+` throughout;
* **no module under `src/play` except `ram.py` writes a RAM address as a hex
  literal in code or subscripts one.** Docstrings may quote an address as
  evidence; that is the only exemption, because prose citing `$0036` is how a
  measurement gets recorded.

`test_play_actions.py` (28 checks) pins the action contract against a fake
emulator, because the contract is about the *failure* path and a failure cannot
be produced on demand by a real game. The fake routes through
`BizHawk._pulse_until` — the real implementation — so the pulsing logic under
test is the logic a run gets.

Three live bugs these two files found while being written, recorded because a
suite that passes immediately proves nothing:

* `act()` *noted* a budget that expired instead of raising, so a route would have
  walked on past a failed action and printed a completion line.
* `_pulse_until` stepped before checking, so a predicate that was already true
  cost one frame and pressed one button — and the two paths through
  `step_until` disagreed about the one property a caller assumes they share.
* `_pulse_until` with `pulse == 0` computed `min(0, …) == 0`, never advanced, and
  spun forever. The real `step_until` never reaches it with 0; the fake does.

## What it does not do

It runs no emulator, opens no cartridge, and reads no movie. It does not
validate `nestrace`'s PPU — `make testsuite` is the gate on that, and
`test_nestrace_cpu.py` says so in every one of its check names. It does not prove
a replay stays in sync; that needs BizHawk and the release.

## Live bugs this suite found while being written

Recorded because a suite that passes immediately proves nothing:

* `identity.py`'s save stem used `split(".")[0]`, so `a.b.nes` gave `a` — not the
  name BizHawk uses, which is the only thing the stale-`NES/SaveRAM/` guard has to
  go on.
* `run.sh` accepted a verdict containing **both** `verdict ok` and `verdict FAIL`,
  because `grep -q '^verdict ok$'` matches as soon as one such line is present.
* `run.sh` reported "the emulator is not running what was asked for" for a
  preamble that died before writing any verdict at all — sending the operator
  looking for a wrong core when the cause is a Lua error.
* `tools/nestrace.py` applied the MMC3 IRQ acknowledge to NROM as well, because
  `bankreg` starts at 6 and is never changed on a board with no bank registers.
  A `sta $0200,x` operand byte at `$8006` read back as `$00`.
* `tools/nestrace.py` charged a page-crossing cycle to every indexed store and
  read-modify-write. The 6502 charges it only on reads — and this cartridge's
  bank-switch loops are `sta $8001` / `sta $8000`, so the error accumulates
  across a scanline rather than appearing as a one-off.