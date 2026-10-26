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
| `test_repo_hygiene.py` | 24 | no cartridge, no movie, no save, no archive committed; `guard_staged.sh` exercised against a synthetic index; `vendor/` unmodified; **and no *code* in the tree reaches into another project's checkout** (section F) |
| `test_bizpath.py` | 48 | `tools/bizhawk/bizpath.sh`: one name for the emulator directory, one answer, no fallback onto a path that happens to exist; `run.sh` honours it; and the Python and bash halves are compared against each other rather than assumed to agree |

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

`test_play_actions.py` (29 checks) pins the action contract against a fake
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

## `test_play_search.py`

33 checks, and it is the file that pins the *failure modes* rather than the
features, because the search layer is the only part of `src/play/` that has to
survive a policy misbehaving without taking the run with it.

* **`OverBudget` is a `BaseException` and not an `Exception`**, and a policy's
  `except Exception` provably cannot catch it (1a-1e). This is not a style
  choice: every policy in both this project and the one it was ported from is a
  `try: … except Exception: return "gave up"` wrapper, and an `Exception` would
  be swallowed by one of those, leaving `emu.inputs` pointing at the scout's
  list and the next attempt recording into a log nobody reads.
* **The `finally` still runs** (2), and **the budget is enforced through
  `emu.step` as well as `rec.step`** (3), so calling an action instead of
  stepping directly is not a way around the cut.
* **Every attempt is a record**, including the failures (4a-4e), and an attempt
  the budget cut off is a failure even when it had already reached the success
  test (4e — see the bug table below).
* **The runner splices by replaying**, not by appending (7a-7e), re-checks the
  success test on MAIN, snapshots before and after, and writes the ledger with
  the losers in it.
* **`save`/`load`'s wire format**, both directions (12a-12d), after the first
  live run died on it.
* **The route's shape**: names, order, the digest, `tries=0` for a segment with
  no choices, and `walk`'s `prepare` factory (11a-11d).

Four checks in this file were red when written, and three of them were real
defects rather than a wrong expectation:

| check | what it caught |
|---|---|
| 1c | `Recorder.__exit__` returned `True` — the context manager swallowed `OverBudget`, the one thing it must never do |
| 4e | `note != "over budget"` decided success. When the note grew a frame count in it, every cut-off attempt was scored a **success** |
| 7b | the winner's inputs were appended to MAIN's log instead of replayed through `emu.step`: log length right, frame counter wrong, and the log will not replay |
| 8 | the fake's `load_state` restored RAM but not the frame counter, which would have made 7b pass for the wrong reason |

## `test_bizpath.py` and `nodep.py`

`test_bizpath.py` is the file written after a service in this project launched
BizHawk out of a *different* project's install and a `pkill` killed a 136,526-frame
replay belonging to that project. Two names for one thing, in two languages:

| exported | `src/play/emu.py`'s guard checked | `tools/bizhawk/run.sh` launched |
|---|---|---|
| `BIZHAWK=/new` | the other project's directory | `/new` |
| `MAGICIAN_BIZHAWK=/new` | `/new` | the other project's directory |

`emu.py` built the child environment from `dict(os.environ)` and added only
`MAGICIAN_*` keys, so it never handed `BIZHAWK` to `run.sh`; the two could not
agree even in principle, and the second row reports success while measuring
somebody else's copy. That is this suite's own subject matter, which is why it
belongs here rather than in a comment.

48 checks in six groups: precedence and the ambiguity refusal (A); `run.sh`
really launches out of `MAGICIAN_BIZHAWK` with the alias **unset**, which is the
only way to write the regression (B); a directory that exists but is not a
BizHawk (C); the display, `:2`, exported to the emulator (D); the scripts resolve
through `bizpath.sh` and no `$BIZHAWK` default expansion survives (E); and the
Python half — `resolve_bizhawk()`, `child_env()`, and the three environments in
which Python and bash must return the *same path* (F).

**`nodep.py` is the shared half**, and the reason the check is not a grep.
`aibeatszelda` appears in dozens of files here and almost all of them are
correct: credit for the MAIN/SCOUT split ported out of it, two real bugs found by
reading its tests, and a `journal/` that is a record rather than a claim. A grep
that failed on those would get deleted, and then there would be no check at all.
So `nodep.py` strips comments (`tokenize` for Python, exact; `--` for Lua;
line-based for shell) and docstrings (`ast`, which is the only thing that knows
which string is prose) and looks at what is left. It cannot catch a **symlink**
into the other tree, which is why the install is a real copy — and it says so.

Three live bugs it found while being written, recorded because a suite that
passes immediately proves nothing:

* `emu.resolve_bizhawk()` built its default install location at **import** time
  from the real `$HOME`, so a caller whose environment named a different home got
  the real machine's path back. Section F's Python-vs-bash comparison found it; a
  comment saying the two agreed would not have.
* The test's own `launches_with()` helper popped `BIZHAWK` from the environment
  *after* merging the caller's settings, so every "BIZHAWK alone" launch silently
  fell through to the default directory — and the check passed on the exit code
  while measuring nothing. It only showed up because the alias case asserts on
  *which* stub answered.
* `doctor.sh`'s `ldconfig -p | grep -q libgdiplus` under `pipefail`: `grep -q`
  exits at the first match, `ldconfig` dies on SIGPIPE, the pipeline returns 141,
  and the test answers "not installed" for a library that is installed. Same
  family as `[ x -lt 0x8000 ]` — a guard whose own plumbing decides the answer.

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