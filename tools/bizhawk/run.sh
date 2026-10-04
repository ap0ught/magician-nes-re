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
#   * a stale NES/SaveRAM left behind           -> exit 4
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
#     ever read. Wiping the SRAM is required, not cosmetic -- the cartridge has
#     battery-backed PRG RAM and a stale save resumes the previous session.
#
# Environment:
#   MAGICIAN_SETTLE   seconds to wait for the verdict (default 45)
#   MAGICIAN_EXPECT   colon-separated files that must exist and be non-empty
#   MAGICIAN_DONE     sentinel string each expected file must contain before the
#                     wait gives up. Set it for any dumping script: without it
#                     the wait returns on the first non-empty byte and the caller
#                     diffs a truncated file
#   MAGICIAN_SRAM     the SaveRAM directory (default $BIZ/NES/SaveRAM)
#   MAGICIAN_CORE     the SystemID that must be loaded (default NES)

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${1:?usage: run.sh <script.lua> <rom> [logfile]}"
ROM="${2:?usage: run.sh <script.lua> <rom> [logfile]}"
LOG="${3:-/tmp/opencode/bizhawk_run.log}"
BIZ="${BIZHAWK:-$HOME/code/games/aibeatszelda/BizHawk-2.11.1-win-x64}"
WANT_SYSTEM="${MAGICIAN_SYSTEM:-NES}"
# The board name is a second core fingerprint and it costs nothing: NullHawk has
# no board to name, so this catches a fallback even if getsystemid() lied.
WANT_BOARD="${MAGICIAN_BOARD:-}"

# Both paths must be absolute before the `cd "$BIZ"` below. A relative ROM path is
# not an error: BizHawk fails to load the file, falls back to NullHawk, and the
# run then measures nothing while reporting success.
[ -f "$ROM" ] || { echo "run.sh: no ROM at $ROM" >&2; exit 1; }
[ -f "$SCRIPT" ] || { echo "run.sh: no script at $SCRIPT" >&2; exit 1; }
ROM="$(readlink -f "$ROM")"
SCRIPT="$(readlink -f "$SCRIPT")"
[ -d "$BIZ" ] || { echo "run.sh: BizHawk not at $BIZ; set BIZHAWK=/path" >&2; exit 1; }

# A leftover EmuHawk holds the single-instance pipe, so this launch is diverted
# into it and shows no window of its own. Past runs were measured against exactly
# that and looked fine. Fail rather than measure, unless explicitly told to clean
# up: killing someone's emulator window is not this script's decision to make.
# The bracket in the pattern keeps pkill from matching its own command line,
# which is how the previous attempt killed the shell that was running it.
if pgrep -f '[m]ono EmuHawk' >/dev/null; then
  if [ "${MAGICIAN_KILL_STALE:-0}" = "1" ]; then
    echo "run.sh: MAGICIAN_KILL_STALE=1, killing the instance already running"
    pkill -f '[m]ono EmuHawk'
    sleep 3
    pgrep -f '[m]ono EmuHawk' >/dev/null \
      && { echo "run.sh: it would not die" >&2; exit 3; }
  else
    echo "run.sh: FAIL -- an EmuHawk is already running." >&2
    echo "run.sh: BizHawk allows one instance; this launch would be diverted into" >&2
    echo "run.sh: it and show no window, and the run would measure the old session." >&2
    echo "run.sh: re-run with MAGICIAN_KILL_STALE=1 to close it first." >&2
    pgrep -af '[m]ono EmuHawk' >&2
    exit 3
  fi
fi

# The identity the emulator will be asked to confirm.
#
# The SHA1 is of the file on disk, and is only what gets printed: this build of
# BizHawk has no client.getromhash(), so the shell cannot be the one to compare
# it. What the emulator is asked to compare is the ROM *window* below -- bytes cut
# out of the requested file and read back out of the loaded cartridge through the
# core's own "System Bus" domain. That is a byte-for-byte identity check that a
# fallback core or a wrong path cannot pass.
ROM_SHA1="$(sha1sum "$ROM" | cut -d' ' -f1)"

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

# $F9A8-$F9DF is the fixed window holding the nmi/irq/reset trampolines and the
# head of `reset`. It is always banked in on MMC3, and it is the region where the
# rebuild and the cartridge first differ (the release has no `lda $2002` in `nmi`,
# so its nmi vector is $F9AB and not $F9A8) -- which makes it a fingerprint that
# can tell the two ROMs apart as well as tell a real load from a fallback.
WIN_AT=$((0xF9A8))
WIN_LEN=48
# CPU $F9A8 is the last 16 KiB of a 128 KiB PRG, so it sits at PRG offset
# $1F9A8. The PRG starts *after* the 16-byte iNES header, so the offset into the
# file on disk is that plus 16. Getting this wrong by 16 is silent: the bytes read
# back are real cartridge bytes from $F998, they just are not the ones asked for,
# and the comparison fails with a first difference at byte 0 for no visible reason.
HDR=0
if [ "$(head -c 3 "$ROM" 2>/dev/null)" = "NES" ]; then
  # The byte after the magic is the PRG size in 16 KiB units; the CHR size follows.
  # A raw .bin has no header and is indexed from 0.
  HDR=16
fi
WIN_OFF=$((WIN_AT + 0x10000 + HDR))
if [ "$(stat -c %s "$ROM")" -ge $((WIN_OFF + WIN_LEN)) ]; then
  WIN_HEX="$(dd if="$ROM" bs=1 skip=$WIN_OFF count=$WIN_LEN status=none | xxd -p -c 256)"
else
  echo "run.sh: $ROM is too small to hold the fixed window at file offset $(printf '%#x' "$WIN_OFF")" >&2
  exit 1
fi

SRAM="${MAGICIAN_SRAM:-$BIZ/NES/SaveRAM}"
mkdir -p "$SRAM"
# Clear every saved image, not just one: the name comes from the ROM's filename.
find "$SRAM" -maxdepth 1 -name '*.SaveRAM*' -delete
# A save that BizHawk rewrites *during* the run is fine -- it is the state at
# boot that has to be clean, and that is what was just deleted.
if compgen -G "$SRAM/*.SaveRAM*" >/dev/null; then
  echo "run.sh: NES/SaveRAM is still not empty after wiping it:" >&2
  ls -la "$SRAM"/*.SaveRAM* >&2
  exit 4
fi
echo "run.sh: cleared $SRAM/*.SaveRAM"

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
if ! grep -q '^verdict ok$' "$VERIFY"; then
  echo >&2
  echo "run.sh: FAIL -- the emulator is not running what was asked for:" >&2
  grep '^verdict FAIL' "$VERIFY" | sed 's/^/run.sh:   /' >&2
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
  echo "run.sh: EmuHawk alive; window should be on :0"
else
  echo "run.sh: EmuHawk has exited (the script called client.exit())"
fi