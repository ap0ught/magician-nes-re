#!/usr/bin/env bash
# Run one BizHawk session with a Lua script attached, headful on the live display.
#
#   tools/bizhawk/run.sh <script.lua> <rom> [logfile]
#
# Exits non-zero unless it can prove that the emulator ran the core it was asked
# for, on the ROM it was asked for, and produced the file it was asked to
# produce. This is not defensive decoration: `run.sh` once passed a *relative*
# ROM path, BizHawk failed to load the file and silently fell back to NullHawk,
# nothing rendered, and the run still looked successful. The project's
# established failure mode is a harness reporting success while measuring
# nothing, and it has already cost real time here, so every way that can happen
# is now a loud failure:
#
#   * a ROM path that does not exist            -> exit 1, before launching
#   * a core other than the one requested       -> exit 3
#   * a ROM whose SHA1 is not the one requested -> exit 3
#   * no memory domains (NullHawk)             -> exit 3
#   * a stale NES/SaveRAM left behind           -> exit 4 (only if this ROM has a
#                                                   battery bit; Beta 1 does not)
#   * an expected output that is missing or 0 B -> exit 5
#
# BizHawk 2.11.1 facts this depends on, all measured on this machine:
#
#   * `--lua <path>` loads a script at startup and implies `--luaconsole`. This
#     is the only supported way to get code running inside the emulator, and it
#     is what makes BizHawk usable as an instrument: emu.frameadvance() gives
#     exact frame boundaries and memory.* reads the core's own state, instead of
#     scraping a window that may be occluded.
#   * Only ONE `--lua` script is accepted, so the identity check is a preamble
#     that run.sh generates and that loads the real script itself.
#   * `--gdi` (libgdiplus) is required. The OpenGL control dies with an X11
#     BadMatch on this session.
#   * Only ONE instance may run: the second is diverted to the first through the
#     single-instance pipe and never shows a window. Run these serially.
#   * NES/SaveRAM is written by BizHawk, not by us; the cartridge itself is only
#     ever read. Whether a stale save can affect a run is a property of the
#     dump's battery bit, which is read out of header byte 6 per ROM. Beta 1's
#     bit is CLEAR, so nothing is cleared and nothing in NES/SaveRAM is touched.
#
# Environment:
#   MAGICIAN_SETTLE   seconds to wait for the verdict (default 45)
#   MAGICIAN_EXPECT   colon-separated files that must exist and be non-empty
#   MAGICIAN_DONE     sentinel string each expected file must contain before the
#                     wait gives up. Set it for any dumping script: without it
#                     the wait returns on the first non-empty byte and the caller
#                     diffs a truncated file
#   MAGICIAN_SRAM     the SaveRAM directory to LOOK in (default
#                     $MAGICIAN_BIZHAWK_DIR/NES/SaveRAM). See the SaveRAM
#                     section: this does not change where BizHawk writes
#   MAGICIAN_CORE     the SystemID that must be loaded (default NES)
#   MAGICIAN_BIZHAWK  where this project's own BizHawk is. See bizpath.sh for
#                     the whole contract; `BIZHAWK` is accepted as an alias and
#                     setting the two to different directories is a hard error.
#   MAGICIAN_DISPLAY  the X display the window goes on (default :2, or whatever
#                     DISPLAY was inherited). Exported to the emulator here,
#                     because a systemd user service does not inherit the
#                     session's DISPLAY and ends up at "Could not open display".

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${1:?usage: run.sh <script.lua> <rom> [logfile]}"
ROM="${2:?usage: run.sh <script.lua> <rom> [logfile]}"
LOG="${3:-/tmp/opencode/bizhawk_run.log}"
WANT_SYSTEM="${MAGICIAN_SYSTEM:-NES}"
# The board name is a second core fingerprint and it costs nothing: NullHawk has
# no board to name, so this catches a fallback even if getsystemid() lied.
WANT_BOARD="${MAGICIAN_BOARD:-}"

# WHERE THE EMULATOR AND THE DISPLAY ARE. One file, sourced, so `run.sh` and
# `src/play/emu.py` cannot disagree about which directory a run is about -- which
# is exactly what they did: run.sh read $BIZHAWK and emu.py read
# $MAGICIAN_BIZHAWK, each with its own hardcoded default, and emu.py never handed
# BIZHAWK to the child. A caller who exported MAGICIAN_BIZHAWK got a guard that
# certified their directory and a launch out of another project's. See
# tools/bizhawk/bizpath.sh and journal/14.
#
# `|| exit 1` rather than bare `.`: run.sh runs under `set -uo pipefail` without
# `-e`, and bizpath.sh signals a refusal by returning 1 with the reason already
# on stderr. Propagating it means the exit code is the resolver's, not a later
# coincidence.
. "$ROOT/tools/bizhawk/bizpath.sh" || exit 1
BIZ="$MAGICIAN_BIZHAWK_DIR"

# Both paths must be absolute before the `cd "$BIZ"` below. A relative ROM path is
# not an error: BizHawk fails to load the file, falls back to NullHawk, and the
# run then measures nothing while reporting success.
[ -f "$ROM" ] || { echo "run.sh: no ROM at $ROM" >&2; exit 1; }
[ -f "$SCRIPT" ] || { echo "run.sh: no script at $SCRIPT" >&2; exit 1; }
ROM="$(readlink -f "$ROM")"
SCRIPT="$(readlink -f "$SCRIPT")"

# A leftover EmuHawk: does a second launch get diverted into the first?
#
# THE MEASUREMENT, because this guard's justification was wrong and the wrongness
# was load-bearing. `runner.py` used to refuse `scouts > 1` because "BizHawk
# diverts a second launch into the first through its single-instance pipe". On
# THIS machine it does not: BizHawk 2.11.1's config.ini carries
# `"SingleInstanceMode": false` (line 1502), and three concurrent sessions were
# launched, each with its own PID, its own window, its own bridge port, and a
# distinct $0000-$07FF fingerprint from a distinct input pattern. What refused a
# second launch was THIS GUARD, not BizHawk.
#
# Which is why the guard was never the thing under test: three sessions racing
# past a `pgrep` all see an empty result and all proceed. Serial, it fires.
#
# So the guard is kept -- a stray window from an interrupted run should not be
# adopted by the next one -- but it is now HONEST about what it does, and
# `MAGICIAN_ALLOW_CONCURRENT=1` is the opt-in for the parallel case, which says
# on stdout that it is being used. The default is still to refuse, because a
# genuinely diverted launch shows no window and the run would measure a session
# it did not start; refusing loudly is better than that.
# The bracket in the pattern keeps pkill from matching its own command line,
# which is how the previous attempt killed the shell that was running it.
if pgrep -f '[m]ono EmuHawk' >/dev/null; then
  if [ "${MAGICIAN_ALLOW_CONCURRENT:-0}" = "1" ]; then
    echo "run.sh: an EmuHawk is already running and MAGICIAN_ALLOW_CONCURRENT=1."
    echo "run.sh: launching a second window. BizHawk 2.11.1 here runs with"
    echo "run.sh: SingleInstanceMode=false, so this is a separate session -- but"
    echo "run.sh: verify the PIDs differ (runner.py asserts it) before trusting"
    echo "run.sh: anything measured through it."
    pgrep -af '[m]ono EmuHawk' >&2
  elif [ "${MAGICIAN_KILL_STALE:-0}" = "1" ]; then
    echo "run.sh: MAGICIAN_KILL_STALE=1, killing the instance already running"
    pkill -f '[m]ono EmuHawk'
    sleep 3
    pgrep -f '[m]ono EmuHawk' >/dev/null \
      && { echo "run.sh: it would not die" >&2; exit 3; }
  else
    # The wording is corrected: it used to say "BizHawk allows one instance; this
    # launch would be diverted into it", which is FALSE here and was believed for
    # months. What actually happens is that this launcher declines to run beside
    # a window it does not own, because it cannot prove the new window is the new
    # window. `runner.Run.start` now proves it by comparing EmuHawk PIDs.
    echo "run.sh: FAIL -- an EmuHawk is already running and this is not it." >&2
    echo "run.sh: refusing rather than adopting a window this launch did not" >&2
    echo "run.sh: start, because it could not prove the new session is new." >&2
    echo "run.sh:   MAGICIAN_KILL_STALE=1        close that one first" >&2
    echo "run.sh:   MAGICIAN_ALLOW_CONCURRENT=1 run a second window beside it" >&2
    echo "run.sh:                                  (MEASURED safe on this machine:" >&2
    echo "run.sh:                                   SingleInstanceMode=false, three" >&2
    echo "run.sh:                                   concurrent sessions verified)" >&2
    pgrep -af '[m]ono EmuHawk' >&2
    exit 3
  fi
fi

# The identity the emulator will be asked to confirm.
#
# Every byte-level derivation below -- header bytes 4/5/6, the three vectors,
# their order, the window that contains them, and that window's file offset --
# is `tools/bizhawk/identity.py`. It was bash, and two of the worst quiet
# failures in this project were in it: a `head -c 1 | tail -c +5` that read no
# byte at all and so reported **three zero vectors**, and a vector order of
# NMI/IRQ/RESET instead of NMI/RESET/IRQ. Both printed a number and raised
# nothing. The arithmetic now lives in one place where a wrong value is an
# exception, and `src/testing/test_identity.py` pins each of those.
#
# The SHA1 is of the file on disk, and is only what gets printed here: this build
# of BizHawk has no client.getromhash(), so the shell cannot be the one to compare
# it. What the emulator is asked to compare is the ROM *window* below -- bytes cut
# out of the requested file and read back out of the loaded cartridge through the
# core's own "System Bus" domain. That is a byte-for-byte identity check that a
# fallback core or a wrong path cannot pass.
#
# `identity.py` prints KEY=VALUE lines; they are read here without `eval`, so a
# path with a space or an `=` in it cannot become shell syntax.
IDENT_RAW=""
IDENT_ERR="$(mktemp "${TMPDIR:-/tmp}/magician-identity.XXXXXX")"
if ! python3 "$ROOT/tools/bizhawk/identity.py" "$ROM" >"$IDENT_ERR.raw" 2>"$IDENT_ERR"; then
  sed 's/^/run.sh: /' "$IDENT_ERR" >&2
  echo "run.sh:        the identity of $ROM could not be derived, so nothing" >&2
  echo "run.sh:        about this run can be trusted. Nothing was launched." >&2
  rm -f "$IDENT_ERR" "$IDENT_ERR.raw"
  exit 1
fi
IDENT_RAW="$(cat "$IDENT_ERR.raw")"
rm -f "$IDENT_ERR" "$IDENT_ERR.raw"
while IFS='=' read -r _k _v; do
  case "$_k" in
    sha1) ROM_SHA1="$_v" ;;
    hdr) HDR="$_v" ;;
    prg_len) PRG_LEN="$_v" ;;
    chr_len) CHR_LEN="$_v" ;;
    vec_nmi) VEC_NMI="$_v" ;;
    vec_reset) VEC_RESET="$_v" ;;
    vec_irq) VEC_IRQ="$_v" ;;
    win_at) WIN_AT="$_v" ;;
    win_len) WIN_LEN="$_v" ;;
    win_off) WIN_OFF="$_v" ;;
    win_hex) WIN_HEX="$_v" ;;
    battery) HAS_BATTERY="$_v" ;;
    save_stem) SAVE_STEM="$_v" ;;
  esac
done <<<"$IDENT_RAW"
for _k in ROM_SHA1 HDR PRG_LEN CHR_LEN VEC_NMI VEC_RESET VEC_IRQ \
          WIN_AT WIN_LEN WIN_OFF WIN_HEX HAS_BATTERY SAVE_STEM; do
  if [ -z "${!_k:-}" ]; then
    echo "run.sh: FAIL -- identity.py did not report $_k, so the run would" >&2
    echo "run.sh:        proceed on an unknown ROM. Refusing rather than" >&2
    echo "run.sh:        guessing; that is how three zero vectors were reported." >&2
    exit 1
  fi
done
# And the derived values are re-checked here, in the shell, against the same
# ranges identity.py enforces. Belt and braces: if this file is ever edited to
# parse the KEY=VALUE lines wrongly, the two disagree and the run stops.
# NOTE the `$((0x...))`: bash's `[` does not parse hex in -lt/-gt. A bare `0x`
# makes it print "integer expected" and the test then *passes*, which defeats
# the guard entirely.
for _k in VEC_NMI VEC_RESET VEC_IRQ; do
  case "${!_k}" in
    ''|*[!0-9]*) echo "run.sh: FAIL -- $_k is '${!_k}', not a number." >&2; exit 1 ;;
  esac
  if [ "${!_k}" -lt "$((0xC000))" ] || [ "${!_k}" -gt "$((0xFFFF))" ]; then
    echo "run.sh: FAIL -- $_k = \$${!_k} is outside the fixed window (\$C000-\$FFFF)." >&2
    exit 1
  fi
done
if [ "$PRG_LEN" -lt "$((0x8000))" ] || [ "$PRG_LEN" -gt "$((0x100000))" ]; then
  echo "run.sh: FAIL -- PRG length is $PRG_LEN bytes, outside 32 KiB..1 MiB." >&2
  echo "run.sh:        A PRG length of 0 is what a failed read of header byte 4" >&2
  echo "run.sh:        looks like, and it is what this harness once reported" >&2
  echo "run.sh:        three zero vectors from. It is an error, never a result." >&2
  exit 1
fi
if [ "${#WIN_HEX}" -ne $((WIN_LEN * 2)) ]; then
  echo "run.sh: FAIL -- the identity window is ${#WIN_HEX} hex characters," >&2
  echo "run.sh:        not $((WIN_LEN * 2)) for $WIN_LEN bytes. A window that" >&2
  echo "run.sh:        read fewer bytes than it claims still reads real" >&2
  echo "run.sh:        cartridge bytes, so nothing downstream can tell." >&2
  exit 1
fi

# The vector line, in the order the vectors are actually stored. NMI/RESET/IRQ,
# not NMI/IRQ/RESET: RESET is the middle word at $FFFC. This used to be read as
# NMI, IRQ, RESET, which swaps two labels and produces three numbers that all
# look fine. `src/testing/test_run_sh.py` asserts this line against a synthetic
# ROM whose six vector bytes are all different, so a swap cannot pass.
printf 'run.sh: vectors from the file: nmi=$%04X reset=$%04X irq=$%04X\n' \
  "$VEC_NMI" "$VEC_RESET" "$VEC_IRQ"

# The window check below proves BizHawk loaded *the file that was pointed at*. It
# cannot prove that file is the one the caller had in mind -- a tampered ROM is
# self-consistent and passes. So a caller that knows which image it wants can pin
# it here, and a mismatch is a failure before the emulator is even started.
if [ -n "${MAGICIAN_EXPECT_SHA1:-}" ]; then
  if [ "${MAGICIAN_EXPECT_SHA1,,}" != "${ROM_SHA1,,}" ]; then
    echo "run.sh: FAIL -- $ROM has SHA1 $ROM_SHA1," >&2
    echo "run.sh:        but MAGICIAN_EXPECT_SHA1 asked for ${MAGICIAN_EXPECT_SHA1}." >&2
    exit 1
  fi
  echo "run.sh: sha1 matches MAGICIAN_EXPECT_SHA1"
fi

# `identity.py` also prints header byte 6 so the SaveRAM decision below is
# derived per dump rather than asserted in a comment. Beta 1's is $40, so its
# battery bit is CLEAR and nothing in NES/SaveRAM/ can affect a run against
# it; the release's is $42 and set.
echo "run.sh: PRG $PRG_LEN bytes, CHR $CHR_LEN bytes, battery bit \"$([ "$HAS_BATTERY" = 1 ] && echo SET || echo CLEAR)\""


# ------------------------------------------------------------------- SaveRAM
# Nothing here is deleted.
#
# This used to `find "$SRAM" -name '*.SaveRAM*' -delete` unconditionally, on the
# stated grounds that "the cartridge has battery-backed PRG RAM and a stale save
# resumes the previous session". That reason is a property of the *dump*, not of
# this script, and it is false for the target: Beta 1's header byte 6 is $40, its
# battery bit is CLEAR, and BizHawk keeps no save for it. So the wipe was
# destroying the user's save files to protect against a hazard that does not
# exist for the ROM being run.
#
# What is true, and all that is needed:
#   * battery CLEAR  -> BizHawk has no save state for this ROM. Nothing to do.
#   * battery SET, and no save file for this ROM's name -> nothing to do.
#   * battery SET, and a save file exists -> a stale save *would* be resumed, and
#     it is the user's file, so this refuses rather than deleting it.
#
# `--never write to NES/SaveRAM/` is taken literally: this script creates nothing
# there either. MAGICIAN_SRAM still names the directory to *look* in.
SRAM="${MAGICIAN_SRAM:-$BIZ/NES/SaveRAM}"
# BizHawk names the save after the ROM's filename, stem only.
#
# Pure parameter expansion, no `basename`. The obvious
# `SAVE_STEM="$(basename "$ROM")"; SAVE_STEM="${SAVE_STEM%.*}"` does not work
# here and `bash -n` does not catch it: `set -x` shows the line is reached and
# then produces **no trace at all** -- not the `++ basename` line, not the
# `+ SAVE_STEM=` line -- and the next `echo` fails with "SAVE_STEM: unbound
# variable". Inserting an `echo` immediately before it prints, so control does
# reach it; a two-line copy of it in isolation works. Whatever the parser is
# doing, an external command and a command substitution are not needed to strip a
# path, and this version is verifiable.
#
# `identity.py` also reports a `save_stem`, and this recomputes it the long way.
# The two agree by construction (both take the basename and drop the last
# extension); the local form is kept because it is the one this file's parser has
# been shown to handle, and `src/testing/test_run_sh.py` checks the two agree.
SAVE_STEM="${ROM##*/}"
SAVE_STEM="${SAVE_STEM%.*}"
echo "run.sh: save stem would be '$SAVE_STEM'"
if [ "$HAS_BATTERY" = "0" ]; then
  echo "run.sh: battery bit CLEAR -- BizHawk keeps no save for $SAVE_STEM," \
       "so there is no stale state to clear. NES/SaveRAM untouched."
elif [ -d "$SRAM" ] && compgen -G "$SRAM/$SAVE_STEM.SaveRAM*" >/dev/null; then
  echo "run.sh: FAIL -- battery bit SET and $SRAM/$SAVE_STEM.SaveRAM exists," >&2
  echo "run.sh:        so BizHawk would resume it and this run's memory reads" >&2
  echo "run.sh:        would not be from a cold boot." >&2
  echo "run.sh:        BizHawk names the save after the ROM's filename, so the" >&2
  echo "run.sh:        clean way out is to run a ROM whose filename has no save" >&2
  echo "run.sh:        yet -- copy it to a scratch directory under a fresh name." >&2
  echo "run.sh:        Pointing MAGICIAN_SRAM elsewhere does NOT work: it changes" >&2
  echo "run.sh:        where this script looks, not where BizHawk writes, so the" >&2
  echo "run.sh:        stale save would still be resumed and the run would still" >&2
  echo "run.sh:        not be from a cold boot. This script will not delete it." >&2
  ls -la "$SRAM/$SAVE_STEM.SaveRAM"* >&2
  exit 4
else
  echo "run.sh: battery bit SET but no save for $SAVE_STEM in $SRAM; nothing to clear."
fi

# The verdict file must not be able to survive from a previous run, or a failed
# launch would be reported as a passing one -- the exact bug, one level up.
VERIFY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/magician-verify.XXXXXX")"
VERIFY="$VERIFY_DIR/verify.txt"
rm -f "$VERIFY"

# The preamble BizHawk actually loads: it checks the core and the ROM, writes the
# verdict, and only then runs the script that was asked for. Generated rather
# than committed so the expected values cannot drift from the values passed here.
export MAGICIAN_VERIFY_DIR="$VERIFY_DIR"
export MAGICIAN_MAIN_LUA="$SCRIPT"
export MAGICIAN_WANT_SYSTEM="$WANT_SYSTEM"
export MAGICIAN_WANT_BOARD="$WANT_BOARD"
export MAGICIAN_WANT_ROM="$(basename "$ROM")"
export MAGICIAN_WANT_SHA1="$ROM_SHA1"
# The absolute path as well as the basename, so a measuring script can read the
# same file the identity check above was taken from. `bankprobe.lua` needs it to
# prove that what the core returns for $8000 is PRG through the bank window
# rather than an MMC3 register, which is the difference between an instrument
# that can answer the bank-sequence question and one that cannot.
export MAGICIAN_WANT_ROM_PATH="$ROM"
export MAGICIAN_WANT_WINDOW_AT="$WIN_AT"
export MAGICIAN_WANT_WINDOW_HEX="$WIN_HEX"
PRE="$VERIFY_DIR/preamble.lua"
cat >"$PRE" <<LUA
$(cat "$ROOT/tools/bizhawk/verify_preamble.lua")
LUA

CFG="${MAGICIAN_INI:-$BIZ/config.ini}"
cp -f "$CFG" "$CFG.runsh.bak" 2>/dev/null || true

export LD_LIBRARY_PATH="$BIZ/dll:$BIZ:/usr/lib"
export MONO_WINFORMS_XIM_STYLE=disabled
export MONO_CRASH_NOFILE=1

echo "run.sh: $ROM ($ROM_SHA1) with $SCRIPT"
echo "run.sh: emulator $BIZ (from $MAGICIAN_BIZHAWK_SOURCE), window on $MAGICIAN_DISPLAY (from $MAGICIAN_DISPLAY_SOURCE)"
echo "run.sh: want system=$WANT_SYSTEM board=${WANT_BOARD:-<any>} rom=$(basename "$ROM")"
printf 'run.sh: want $%04X = %s\n' "$WIN_AT" "$WIN_HEX"
# Every fd of the launcher subshell is detached, not just EmuHawk's: EmuHawkMono.sh
# backgrounds two `tee` processes that inherit stdout, so redirecting only the
# exec'd command leaves the caller's pipe held open forever.
( cd "$BIZ" && setsid ./EmuHawkMono.sh --gdi --config "$CFG" \
      --lua "$PRE" "$ROM" </dev/null >"$LOG" 2>&1 & ) \
    </dev/null >/dev/null 2>&1

# ---------------------------------------------------------------- the guard
# Wait for the verdict rather than sleeping a fixed time and hoping. A fixed
# sleep is the other half of the same bug: on a slow start it reads the file
# before it exists and calls that success.
SETTLE="${MAGICIAN_SETTLE:-45}"
waited=0
while [ ! -s "$VERIFY" ] && [ "$waited" -lt "$SETTLE" ]; do
  sleep 1
  waited=$((waited + 1))
  # The script may have called client.exit(); stop waiting on a dead process.
  if [ "$waited" -ge 5 ] && ! pgrep -f 'EmuHawk.exe' >/dev/null \
     && [ ! -s "$VERIFY" ]; then
    break
  fi
done

if [ ! -s "$VERIFY" ]; then
  echo "run.sh: FAIL -- BizHawk produced no verdict after ${waited}s." >&2
  echo "run.sh: the identity check never ran, so nothing about this run can be trusted." >&2
  if pgrep -f 'EmuHawk.exe' >/dev/null; then
    echo "run.sh: EmuHawk is still running, so the preamble did not complete." >&2
  else
    echo "run.sh: EmuHawk is not running -- it exited before the preamble finished." >&2
  fi
  echo "run.sh: log follows" >&2
  cat "$LOG" >&2
  exit 3
fi

cat "$VERIFY"
# A verdict passes only if it says `verdict ok` **and** contains no `verdict FAIL`
# line anywhere. `grep -q '^verdict ok$'` on its own is not enough: it matches as
# soon as ONE such line is present, so a file carrying both an ok and a FAIL --
# which a preamble that appends rather than replaces would produce -- is
# accepted. The preamble writes one or the other, so this cannot arise from it
# today; it is the sort of hole that is cheap to close now and expensive to
# discover later. `src/testing/test_run_sh.py` builds exactly that file.
if grep -q '^verdict FAIL' "$VERIFY" || ! grep -q '^verdict ok$' "$VERIFY"; then
  echo >&2
  # Three different things can get here, and they need three different messages.
  # Collapsing them into "the emulator is not running what was asked for" is
  # worse than useless: a preamble that wrote its notes and then died leaves
  # `verdict FAIL` lines absent, and the operator is sent looking for a core
  # problem that is not there. `src/testing/test_run_sh.py` checks the third
  # case specifically.
  if grep -q '^verdict FAIL' "$VERIFY"; then
    echo "run.sh: FAIL -- the emulator is not running what was asked for:" >&2
    grep '^verdict FAIL' "$VERIFY" | sed 's/^/run.sh:   /' >&2
  elif grep -q '^verdict' "$VERIFY"; then
    echo "run.sh: FAIL -- the verdict has a line starting 'verdict' that is" >&2
    echo "run.sh:        neither 'verdict ok' nor 'verdict FAIL ...'." >&2
    grep '^verdict' "$VERIFY" | sed 's/^/run.sh:   /' >&2
  else
    echo "run.sh: FAIL -- the preamble wrote notes but never a verdict." >&2
    echo "run.sh:        It got as far as printing 'want_system' and stopped," >&2
    echo "run.sh:        which is a Lua error in the preamble, not a wrong core" >&2
    echo "run.sh:        and not a wrong ROM. The full verdict file follows." >&2
  fi
  echo "run.sh: log follows" >&2
  cat "$LOG" >&2
  exit 3
fi
echo "run.sh: verdict ok (system=$WANT_SYSTEM, rom=$(basename "$ROM"), sha1=$ROM_SHA1)"

# ------------------------------------------------- the outputs were produced
# A script that dies after the preamble leaves a passing verdict behind, so the
# verdict alone is not enough. Anything the caller declared it wanted must exist
# and be non-empty: a zero-byte screenshot is not a picture of nothing, it is a
# screenshot that was never taken.
#
# The verdict appears *before* the measuring script runs, so this has to wait for
# the files rather than look once. Checking immediately reports every run as a
# failure, which is the same class of bug as never checking at all.
if [ -n "${MAGICIAN_EXPECT:-}" ]; then
  IFS=':' read -r -a _want <<<"$MAGICIAN_EXPECT"
  OUT_WAIT="${MAGICIAN_EXPECT_WAIT:-120}"
  # MAGICIAN_DONE names a sentinel string that only appears once the script has
  # finished writing. Without it the wait below returns the moment the file is
  # non-empty, which for a dumping script is one frame in -- and the caller then
  # diffs a truncated dump and concludes the ROM is broken.
  DONE="${MAGICIAN_DONE:-}"
  waited=0
  while :; do
    missing=0
    alldone=1
    for f in "${_want[@]}"; do
      [ -n "$f" ] || continue
      if [ ! -s "$f" ]; then missing=1; fi
    done
    if [ "$missing" -eq 0 ] && [ -n "$DONE" ]; then
      for f in "${_want[@]}"; do
        [ -n "$f" ] || continue
        grep -q -- "$DONE" "$f" 2>/dev/null || alldone=0
      done
    else
      alldone=1
    fi
    [ "$missing" -eq 0 ] && [ "$alldone" -eq 1 ] && break
    [ "$waited" -ge "$OUT_WAIT" ] && break
    # Stop early once the script is done: client.exit() means nothing more is
    # coming, so waiting out the full timeout would only make failure slower.
    if [ "$waited" -ge 5 ] && ! pgrep -f '[m]ono EmuHawk' >/dev/null; then break; fi
    sleep 1
    waited=$((waited + 1))
  done
  missing=0
  for f in "${_want[@]}"; do
    [ -n "$f" ] || continue
    if [ ! -e "$f" ]; then
      echo "run.sh: FAIL -- expected output $f does not exist (waited ${waited}s)" >&2
      missing=1
    elif [ ! -s "$f" ]; then
      echo "run.sh: FAIL -- expected output $f is 0 bytes (waited ${waited}s)" >&2
      missing=1
    elif [ -n "$DONE" ] && ! grep -q -- "$DONE" "$f"; then
      echo "run.sh: FAIL -- $f never reached its sentinel '$DONE' (waited ${waited}s)." >&2
      echo "run.sh: the script exited before finishing; this file is truncated." >&2
      missing=1
    else
      echo "run.sh: ok $f ($(stat -c %s "$f") bytes)"
    fi
  done
  [ "$missing" -eq 0 ] || { echo "run.sh: log follows" >&2; cat "$LOG" >&2; exit 5; }
fi

if pgrep -f 'EmuHawk.exe' >/dev/null; then
  echo "run.sh: EmuHawk alive; its window is on $MAGICIAN_DISPLAY ($MAGICIAN_DISPLAY_SOURCE)"
else
  echo "run.sh: EmuHawk has exited (the script called client.exit())"
fi