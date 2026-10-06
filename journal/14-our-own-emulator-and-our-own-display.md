# 14 — Our own emulator, our own display, and a fallback that was hiding a coupling

Dated 2026-10-06. Branch `fix/boot-from-source`. Base `d0cef20`.

This entry replaces two claims rather than adding to the record, and one of them
was load-bearing for months. Everything below was **measured before it was
written**, including the measurements that turned out to contradict what this
repository already said.

## The incident

A service in this project launched BizHawk out of **a different project's**
emulator directory. A `pkill` in that service killed a **136,526-frame** replay
belonging to that other project — not this one's run, not a stale window, a
37-minute verified replay in a sibling checkout.

Two checkouts shared one BizHawk directory. There is no lock on `config.ini` and
no lock on `NES/SaveRAM/`. Nothing in either project said they were sharing
anything, because in the sharing project's own documentation this was simply
where the emulator was.

Physical evidence was left in the shared directory, and I found it before
changing anything:

```
$ ls -la ../aibeatszelda/BizHawk-2.11.1-win-x64/config.ini*
-rw-r--r-- 1 cmayfield cmayfield 104378 Oct  5 16:37 config.ini
-rw-r--r-- 1 cmayfield cmayfield 104348 Oct  5 16:08 config.ini.runsh.bak   <-- run.sh's, ours

$ diff <(head -c 200000 .../config.ini.runsh.bak) <(head -c 200000 .../config.ini)
>   "LastRomPath": "/home/cmayfield/code/games/aibeatszelda/src/roms"
>   "/home/cmayfield/code/games/aibeatszelda/src/bridge.lua",
>   "/tmp/magician-verify.qDr7G4/preamble.lua",     <-- /tmp/magician-verify.*
```

`/tmp/magician-verify.*` is `MAGICIAN_VERIFY_DIR`, a name only this project uses.
So this project's launches were rewriting **that** project's emulator settings and
recent-ROM history. The `LastRomPath` line had also been pushed from the shared
ROM collection to the sibling's own roms directory.

## THE QUESTION THAT WAS NOT ASKED: do the two names agree?

The brief named three hardcoded paths and one dependency. The more interesting
half is that the *variable names* differed, and nobody had checked whether that
mattered:

| | before |
|---|---|
| `tools/bizhawk/run.sh:57` | `BIZ="${BIZHAWK:-$HOME/code/games/aibeatszelda/BizHawk-2.11.1-win-x64}"` |
| `tools/bizhawk_probe.sh:49` | the same line |
| `src/play/emu.py:65` | `MAGICIAN_BIZHAWK or (home / "code/games/aibeatszelda/...")` |

`emu.py` builds the child environment from `dict(os.environ)` and adds only
`MAGICIAN_*` keys — so it never handed `BIZHAWK` to `run.sh`. **They agreed only
if both variables happened to be unset, in which case both fell back to the same
hardcoded sibling path.** MEASURED, with nothing running and no emulator launched:

| exported | `emu.py`'s guard checked | `run.sh` launched |
|---|---|---|
| `BIZHAWK=/new` | **the other project's directory** | `/new` |
| `MAGICIAN_BIZHAWK=/new` | `/new` | **the other project's directory** |

Both directions disagree. The shell half was proved without launching anything at
all, by scrubbing `HOME` so the default could not be reached:

```
$ env -i PATH=... HOME=/tmp/... MAGICIAN_BIZHAWK=$STUB \
    bash tools/bizhawk/run.sh noop.lua README.md
run.sh: BizHawk not at /tmp/.../code/games/aibeatszelda/BizHawk-2.11.1-win-x64
   (the stub dir DOES exist: yes)
```

**The second row is the dangerous one.** The guard certifies a directory, the
emulator runs out of a different one, and every number the run produces is
somebody else's. That is this repository's standing failure mode — *"a harness
reporting success while measuring nothing"* — appearing in a new place: not a
wrong measurement, but a measurement of the wrong thing, correctly reported.

There was a third name for the same directory. `tools/bizhawk_probe.sh` called the
SaveRAM directory `BIZHAWK_SRAM` where `run.sh` calls it `MAGICIAN_SRAM`, and did
so over three lines of `mkdir -p` and `rm -f` **inside the shared install**, under
a comment that said "*only ever read, never written*". `make probe` was writing to
another project's directory and denying it in the same breath.

## WHICH PATHS DOES BIZHAWK ACTUALLY WRITE?

The brief suggested symlinking the large read-only parts and keeping the mutable
state local, but said to measure rather than assume. So:

```
$ diff -rq <pristine BizHawk-2.11.1-linux-x64> <the sibling's used install>
Only in <used>: config.ini
Only in <used>: config.ini.runsh.bak
Only in <used>: EmuHawkMono_laststderr.txt
Only in <used>: EmuHawkMono_laststdout.txt
Only in <used>/NES: SaveRAM
Only in <used>/NES: State
```

**443 of 449 files byte-identical.** The entire mutable surface is six paths:

* `config.ini` — BizHawk's settings, ~103 KB, written on the first launch. Carries
  the RecentROM list and the Lua-console history, which is how the cross-writing
  above became visible.
* `config.ini.runsh.bak` — `run.sh`'s own copy, made before **every** launch.
* `EmuHawkMono_laststdout.txt`, `EmuHawkMono_laststderr.txt` — written by
  `EmuHawkMono.sh` **itself**, into whatever directory it was launched from. Two
  `tee` processes, in the shipped script.
* `NES/SaveRAM/` — battery saves, by BizHawk.
* `NES/State/` — savestates, by BizHawk.

**Conclusion, and it is the opposite of the brief's suggestion: symlinking is the
wrong answer.** 145 MB of the 150 MB never changes, so symlinks would have saved
that and re-created the dependency — a symlink satisfies a naive path grep while
remaining exactly the coupling being removed. The brief says so explicitly and it
is right. A **real copy** costs disk this machine has (115 GB free) and removes the
coupling entirely.

The same measurement gives a *new* guard that had not been thought of: the install
must be **writable**, because the launcher writes two files into its own directory
on every run. `bizpath.sh` refuses a read-only install up front rather than letting
it fail inside a Mono stack trace.

## THE DEPENDENCY NOBODY HAD LISTED: `Lua/socket/core.so`

`src/play/bridge.lua:50` is `require("socket.core")`. BizHawk ships only the
Windows `Lua/socket/core.dll`:

```
$ ls <used install>/Lua/socket/
core.dll   51712
core.so   103696      <-- built, not shipped
```

A pristine 2.11.1 tarball has **no `core.so` at all**. So the dependency was not
just a shared directory: it was a **locally compiled artifact**, built by the
sibling's `setup_linux.sh`, that this project's bridge needs and that no tarball
provides. Anyone who had merely copied the *path* would still have been one file
short.

Build recipe verified here, and deliberately **without** `-llua54`: NLua embeds
Lua 5.4 and exports the `lua_*` symbols, so they bind to the host at `dlopen`;
linking liblua54 hands the module a second private copy of the runtime. Built here
from `luasocket-3.1.0`, the result is **byte-identical** to the sibling's
(`cb922dde4447f26e000b9ca9f87e20913c18af96592c55d57f444e1d3f859577`), which is a
useful cross-check that the two builds are the same build.

## BIZHAWK ON A NESTED DISPLAY: YES, AND NO WINDOW MANAGER

This is the claim the brief told me to test rather than repeat. `journal/05` and
the `Makefile` both said, in effect, "BizHawk cannot be driven headless here".

**`Xvfb` really is absent and `xorg-server-xvfb` really is not installed** — both
statements were literally true. But **`Xephyr` is installed**
(`xorg-server-xephyr` 21.1.24) and a nested X server is not Xvfb. Measured, this
project's own install, the same ROM, 120 frames:

```
DISPLAY=:0   connected in 2.0s   work RAM fingerprint efa771ed1c1964d152baae62c0ee68abf0a12b16
DISPLAY=:2   connected in 2.2s   work RAM fingerprint efa771ed1c1964d152baae62c0ee68abf0a12b16
```

**Identical RAM.** The display does not change the emulation. And on `:2` with
**no window manager on it at all**:

```
0x200077 "magician-rebuilt [NES] - BizHawk": ()  586x503+22+22
```

a real, correctly-sized, named window. A real Beta 1 session on `:2` with no WM
gave `verdict ok` and all nine memory domains.

**So `i3` is not started, and that is a decision rather than an omission.** This
instrument reads the core through Lua and takes screenshots from
`client.screenshot()`, so window placement cannot affect a number it produces. The
sibling runs five windows as a documentary and does need tiling; we do not. Both
of its load-bearing rules — never resize a BizHawk window, and match on `title=`
never `class=` — are recorded in `tools/bizhawk/display.sh` for the case where
someone does start a WM here, and are marked as not currently in play.

**One measurement that contradicts the sibling's journal, recorded carefully.** The
sibling records that these windows have no `WM_CLASS` at all. On `:2` here:

```
0x400001 "mono": ("mono" "Mono")  10x10+10+10
```

They do. I have not touched that project's journal — it is theirs, it was measured
in their configuration, and a difference in display and Mono path is enough to
produce a difference here. But if an i3 rule ever fails on `class=` in *this*
project, this is the number to check first.

## A THIRD FALLBACK, IN A PLACE NOBODY LOOKED

`EmuHawkMono.sh` contains its own:

```sh
if (ps -C "mono" -o "cmd" --no-headers | grep -Fq "EmuHawk.exe"); then
    printf "(it seems EmuHawk is already running, NOT capturing output)\n" >&2
    exec mono EmuHawk.exe "$@"
fi
```

No fallback to another directory — but a **behavioural** coupling: when *any*
`mono EmuHawk.exe` is running anywhere on the machine, our launcher silently stops
capturing its output. Every `run.sh` log on this box is therefore affected by every
other project's emulator. I did not change it (it is BizHawk's shipped script and
editing a vendor script to work around a global namespace is its own trap) but it
is why `logs/*.log` can be short, and it is why `make doctor` reports libgtk2:
without it Mono's built-in X11 driver throws a `BadMatch` from BizHawk's input
thread about 30 s in — the window stays up, the bridge stops being serviced, and
the run looks like it froze.

## WHAT WAS BUILT

```
tools/bizhawk/bizpath.sh        sourced by run.sh + probe. THE only place that decides.
tools/bizhawk/setup.sh          make emu-setup. sha256-pinned downloads, builds core.so.
tools/bizhawk/display.sh        make display-up/down/status. Xephyr, no WM.
tools/bizhawk/doctor.sh         make doctor. One exit code per class, in dependency order.
tools/bizhawk/install_units.sh  make install-units. Symlinks, never installs silently.
tools/systemd/*.service         the nested display, and a run.
tools/isolation.sh              make check-isolation. The proof, as a command.
src/testing/test_bizpath.py     48 checks. Emulator-free.
src/testing/nodep.py            provenance vs dependency, for the whole tree.
```

Default install: `$HOME/code/games/magician-nes-bizhawk/BizHawk-2.11.1-linux-x64`.

**Named `-linux-x64`, not `-win-x64`.** The sibling's copy carries the Windows
name only because *its* launcher derives the directory name from its checkout.
Nothing here derives anything, so the name can be the truth — which also means
`BizHawk-2.11.1-win-x64` appearing in one of our log lines reads immediately as
"that was not us".

**`MAGICIAN_BIZHAWK` canonical, `BIZHAWK` accepted as an alias, disagreement is a
hard error.** The alias stays because every existing invocation and every shell
test sets it, and "keep the env-var override working" is part of the requirement.
Dropping it would have been tidier and would have broken the thing the change was
required to preserve.

**No fallback at all.** `${VAR:-<the sibling>}` cannot say "no"; it can only say
"not unless something is there". A named variable pointing somewhere unusable is
a non-zero exit whose message contains `make emu-setup`.

## MY OWN MISTAKES, ALL OF THEM

Recorded because they are the same failures this project already documents, in
new places, and because a journal that lists only the elegant parts is a
lie.

1. **`tools/isolation.sh` was `eval`-ing the command.** Three attempts died on
   quoting before one worked: make ate `$(python3 tools/cartref.py)` as a make
   variable; the shell then could not parse the resulting unquoted path. **Every
   dump filename on this machine contains a space.** Fixed by passing the command
   through `ISOLATE_CMD` in the environment and running `bash -c`, which is one
   quoting layer instead of two. The lesson is in the tool's header.
2. **`doctor.sh` reported two libraries as missing on a machine where both are
   installed.** `ldconfig -p | grep -q libgdiplus` under `set -o pipefail`: `grep
   -q` exits at the first match, `ldconfig` takes SIGPIPE, and the pipeline's
   status becomes 141 — so the test answered "not found" for a library that is
   found. Identical in shape to `[ x -lt 0x8000 ]`, which `test_run_sh.py` section
   F exists to pin: **a guard whose own plumbing decides the answer.**
3. **`doctor.sh` said the cartridge was missing** because it called
   `"$ROOT/tools/cartref.py"` — not executable — and `2>/dev/null` swallowed the
   `Permission denied`, so the answer was an empty string and the report was
   confidently wrong.
4. **`doctor.sh` executed `make display-up` while printing a message about it.**
   Unescaped backticks inside a double-quoted string are command substitution. A
   message that runs what it names. Found only because the target did not exist
   yet and the error was loud; a target that *did* exist would have been an
   invisible, unrequested side effect of a diagnostic.
5. **`doctor.sh` reported the wrong exit code** by taking the *highest* of several
   failure codes, so a machine with no `mono` and no cartridge was told about the
   cartridge. Codes here are a *precedence*, not a magnitude; it now reports the
   first failure in dependency order.
6. **`display.sh`'s diagnostic listed my own shell** as an Xephyr on `:5`, because
   it matched on `/proc/<pid>/cmdline` and the shell's command line mentioned
   `Xephyr :5`. Now filtered on `comm`, for the same reason
   `BizHawk.running_emuhawk` reads `comm` instead of `pkill -f`: **a pattern over
   command lines matches the messenger.**
7. **My test helper `launches_with()` popped `BIZHAWK` from the environment after
   merging the caller's settings**, so every "`BIZHAWK` alone" launch silently fell
   through to the default directory — and the check passed on the exit code while
   measuring nothing. It surfaced only because that check asserts *which stub*
   answered rather than that the command succeeded.
8. **`emu.resolve_bizhawk()` built its default from the real `$HOME` at import
   time**, so a caller naming a different home got the real machine's path back.
   Found by section F comparing the Python and bash answers under the same three
   environments — a check a comment saying "these agree" could never have been.
9. **`setup.sh` had never been run.** `make emu-setup` died with
   `MAGICIAN_BIZHAWK_DIR: unbound variable` on every invocation — it referenced
   the *resolved* directory in a mode that only defines the *default*. The reason
   it survived review is the embarrassing one: I performed its steps by hand
   (download, verify, extract, build `core.so`) and then never ran the script. I
   wrote a test suite, a doctor, a README section and this journal entry about a
   script I had never executed. All three paths are now run: idempotent (exit 0),
   `--force` (exit 0, sha256 pin matched, `core.so` 103 696 bytes, against a
   throwaway destination so the live install was untouched), a destination that
   exists without `EmuHawkMono.sh` (exit 5, refuses to merge), and no tools on
   `PATH` (exit 4, names the packages, never runs `sudo`).
10. **Two of my own test expectations were wrong and the code was right**: I asserted
   the resolver should succeed with an absent default (it must *refuse*), and that
   `child_env()` should re-resolve its `base` argument (it must pin the module's
   value, or the guard and the launch could diverge again). Both were corrected in
   the test with the reasoning kept, because the second one is a real design point.

## THE PROOF

`make check-isolation` snapshots a directory this project must not touch — a
recursive listing with sizes and mtimes, **and** a sha256 of every file — runs a
command, and diffs. Mtimes as well as checksums because a file could be written
and written back; the point is not to argue about likelihood.

Around a real Beta 1 session launched by `tools/bizhawk/run.sh` on `:2`, using
this project's own install:

```
baseline -- 928 entries under .../BizHawk-2.11.1-win-x64
manifest digest e2b47731b917ed933d089ad4492dcae0f5377629b69aea1e5118c46c838793a9
run.sh: verdict ok (system=NES, rom=Magician (USA) (Beta 1) (1990-03-02).nes,
                        sha1=6e46ba92ebbeb472ea9064f4f6bd17dfa4eb723a)
run.sh: EmuHawk alive; its window is on :2 (MAGICIAN_DISPLAY)
after    -- 928 entries
manifest digest e2b47731b917ed933d089ad4492dcae0f5377629b69aea1e5118c46c838793a9
OK -- byte-for-byte and mtime-for-mtime unchanged
```

928 entries: 479 in the listing (every entry, type + size + mtime + path) and 449
files with a sha256. Identical digests on both halves.

The tool reports the isolation result and the command's exit code **separately**,
and a failure of the command does not become a failure of the isolation or vice
versa — collapsing them is how a harness reports success while measuring nothing.
It also does not claim to catch a **symlink** into the other tree; it says so in
its own header, which is why the install is a copy.

**A second session was active in this repository throughout.** `asm/build.py`,
`asm/Makefile`, `tools/nestrace.py`, `tools/dis6502.py`, `src/magician/STARTLEV.PDS`
and `tools/findstore.py` appeared modified during this work — a parallel attempt at
`journal/13`'s `$004D` question. Those changes are **not in these commits**. It
also means the machine's single EmuHawk slot was intermittently occupied by an
unrelated `mono EmuHawk --gdi --config /tmp/x.ini`, which made `smoke.py` refuse
twice for reasons that had nothing to do with the isolation result. Recorded
because a reader comparing two runs will hit it and should know it is not a flake.

## NOT DONE, WITH THE EVIDENCE

1. **`make probe` still fails at its screenshot step, for an unrelated and
   pre-existing reason.** `tools/side_by_side.py` searches for windows named
   `magician-rebuilt` and `Magician [NES]`. MEASURED on `:2` with both sessions up:

   ```
   window: 'magician-rebuilt [NES] - BizHawk'
   window: 'Magician (USA) (Beta 1) (1990-03-02) [NES] - BizHawk'
   needle 'magician-rebuilt'   -> matches
   needle 'Magician [NES]'     -> MATCHES NOTHING
   ```

   The second needle assumes a ROM named literally `Magician.nes`. BizHawk titles
   the window after the file's stem, and every dump on this machine has a longer
   name. The failure is `IndexError` in `side_by_side.py:62`. **Out of scope**: it
   is not a display or isolation problem, it is a window-name matching problem, and
   fixing it means deciding what `make probe` should assert. Left alone rather than
   half-fixed.

   The two launches themselves are fine — `quickerNES: Booted with Mapper #4
   "mmc3"` in **both** logs.

2. **`make probe` passed the rebuilt ROM as a relative path**, which is the exact
   NullHawk trap `run.sh` documents at length: `EmuHawkMono.sh` does
   `cd "$(dirname "$(realpath "$0")")"`, so `asm/out/magician-rebuilt.nes` resolved
   against BizHawk's own directory and the file never opened. Observed in the
   process list (`--gdi --config ... asm/out/magician-rebuilt.nes`) before fixing.
   **Fixed** — both paths are now `readlink -f`'d. Verified afterwards by both logs
   reporting Mapper #4.

3. ~~**A `--snapshot` / two-manifest comparison was never exercised against a
   changed tree.**~~ **CLOSED, by a file I did not write, and the correction is
   worth more than the closure.** `src/testing/test_shell_tools.py` appeared during
   this session — written by the parallel session described below — and it pins
   `isolation.sh`'s negative controls directly: a content change, an mtime-only
   `touch`, a rename, a new file and a deleted file are each exit 3, a missing
   directory is exit 2, and a failing command is exit 4. All twenty-one of its
   checks pass against the tool as committed.

   It also pins an interface I had moved away from. It drives
   `isolation.sh <dir> -- <command>`, and I had switched to `ISOLATE_CMD` in the
   environment. Both are now supported and both funnel into one code path; `--` is
   the documented form and the one the suite drives, and `ISOLATE_CMD` exists
   because `make check-isolation cmd='...'` has to survive make's own expansion
   before any shell sees the command.

   Worth recording that this session's own negative control would not have been
   sufficient either: the first draft of *their* file took its before-snapshot,
   edited the tree, then invoked the tool — so both snapshots were taken after the
   edit and every detection check passed against a tool that had detected nothing.
   Six checks, green, meaningless. The mutation has to happen inside the command.
   Two authors, the same trap, on the same day.

4. **The i3 rules were not written**, because no i3 is started and none is needed
   (measured above). If a WM is ever started on `:2` here, its rules go in
   `~/.config/i3/config`, which is **outside this repository and untracked** — a
   fresh clone gets neither the unit files nor that config, and `make
   install-units` exists to cover the half that can be automated.

5. **`Xephyr` is not in `setup.sh`'s dependency check**, only in `doctor.sh`. It
   belongs in both; noted rather than fixed, because `setup.sh` installs the
   emulator and the display is optional (`MAGICIAN_DISPLAY=:0` is a legitimate
   configuration and the one this session used for the `:0` comparison).

## What the next attempt needs

1. **Nothing outstanding for `check-isolation`'s negative controls** — see the
   corrected item 3 above. What *is* outstanding is the thing neither session
   pinned: that `make check-isolation` with a real `ISOLATE=` and a real `cmd=`
   works end to end from the Makefile, rather than only through the environment
   form it was exercised with.
2. **Decide what `make probe` is for**, then fix `side_by_side.py`'s needle
   matching — match on the ROM's stem prefix rather than the literal string
   `Magician [NES]`, and fail loudly naming the titles it actually saw. It
   currently dies with `IndexError`, which names neither.
3. **`snapshot()`'s checkpoint guard blocks re-running `smoke.py`.**
   `logs/checkpoints/smoke/smoke/smoke_idle` exists from an earlier session, so
   `python3 src/play/smoke.py` always ends in `FileExistsError` after doing all the
   work. The guard is right — a checkpoint is written once — but `smoke.py` is the
   first thing anyone runs on a new machine, so it needs a run-unique checkpoint
   name or an explicit `--force`.
4. **A `make doctor` case that measures the display rather than reporting it.**
   `doctor.sh` reports whether `:2` is up; it does not launch BizHawk and confirm
   the bridge answers. That was measured by hand here and belongs in a tool.
5. **Finish the `SingleInstanceMode` question.** On a *fresh* install BizHawk writes
   `config.ini` itself with `"SingleInstanceMode": false`, so the three-concurrent-
   sessions measurement in `run.sh`'s comment does not depend on a hand-edited
   config — worth recording, because until now it read as though that setting had
   been tweaked by hand in the sibling's tree. `doctor.sh` reports the line, so it
   can only rot visibly.
