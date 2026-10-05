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

# The identity window: a fixed-window read of the nmi/irq/reset trampolines, taken
# out of the requested file and read back out of the loaded cartridge through the
# core's own "System Bus" domain. A byte-for-byte identity check that a fallback
# core or a wrong path cannot pass.
#
# **The window is derived from the ROM's own vectors, not written down.** It used
# to be hard-coded at $F9A8, which was the *rebuild's* nmi against the *release*.
# The six dumps of this title do not agree on where those trampolines are:
#
#   beta1    nmi $F917  irq $F922  reset $F930
#   beta2    nmi $FA05  irq $FA0D  reset $FA1B
#   beta3    nmi $F96E  irq $F976  reset $F984
#   beta4    nmi $F9AB  irq $F9B3  reset $F9C1
#   release  nmi $F9AB  irq $F9B3  reset $F9C1
#
# A hard-coded $F9A8 is inside beta1's X7 but nowhere near beta2's, so it reads
# real cartridge bytes and simply fails, or worse passes against the wrong dump.
# The vectors are the last 6 bytes of PRG, so they are read out of the file itself
# and the window is placed to cover all three entry points.
HDR=0
PRG_LEN=0
if [ "$(head -c 3 "$ROM" 2>/dev/null)" = "NES" ]; then
  # Header byte 4 -- the 5th byte -- is the PRG size in 16 KiB units. It is the
  # FIFTH byte, so it has to be read as `head -c 5 | tail -c 1`: the previous
  # `head -c 1 | tail -c +5` asked for byte 5 of a one-byte stream, produced
  # nothing, and made PRG_LEN 0 -- which sent the vector read to offset 10, i.e.
  # the header's zero padding, and reported three zero vectors without complaint.
  # That is the project's recurring bug: a plausible number about the wrong bytes.
  # So PRG_LEN is now range-checked below, and the vectors are range-checked after
  # that, and neither can be wrong quietly.
  HDR=16
  PRG_NIB=$(head -c 5 "$ROM" | tail -c 1 | xxd -p)
  case "$PRG_NIB" in
    ''|*[!0-9a-fA-F]*)
      echo "run.sh: FAIL -- could not read header byte 4 of $ROM as hex (got '$PRG_NIB')." >&2
      exit 1 ;;
  esac
  PRG_LEN=$(( 0x$PRG_NIB * 16384 ))
fi
# `$((0x...))` and not a bare `0x...`: bash's `[` does not parse hex in
# -lt/-gt. It prints "integer expected" and the test then *passes*, which would
# defeat the guard entirely -- the failure mode this file exists to prevent.
if [ "$PRG_LEN" -lt "$((0x8000))" ] || [ "$PRG_LEN" -gt "$((0x100000))" ]; then
  echo "run.sh: FAIL -- PRG length is $PRG_LEN bytes." >&2
  echo "run.sh:        Every dump of this title is 128 KiB. A value outside 32 KiB" >&2
  echo "run.sh:        ..1 MiB means the header was misread, and every offset" >&2
  echo "run.sh:        derived from it below would be wrong." >&2
  exit 1
fi
VEC_OFF=$((HDR + PRG_LEN - 6))
# The iNES vector order is NMI ($FFFA), RESET ($FFFC), IRQ/BRK ($FFFE) -- note
# that RESET is in the middle. Reading the three little-endian words in the order
# NMI, IRQ, RESET swaps two labels and produces numbers that all look fine.
#
# All three on ONE line. Emitting one per line and reading them with `read a b c`
# looks right and is not: `read` consumes a single line, so the other two are
# discarded and every vector comes out empty.
VEC_LINE="$(od -An -tu1 -j "$VEC_OFF" -N 6 "$ROM" | awk '
  { for (i = 1; i <= NF; i++) v[NR, i] = $i }
  END {
    if (NR < 1) exit 1
    printf "%d %d %d\n", v[1,1] + v[1,2]*256, v[1,3] + v[1,4]*256, v[1,5] + v[1,6]*256
  }')"
if [ -z "$VEC_LINE" ]; then
  echo "run.sh: FAIL -- read no vectors from $ROM at file offset $(printf '%#x' "$VEC_OFF")." >&2
  exit 1
fi
read -r VEC_NMI VEC_RESET VEC_IRQ <<<"$VEC_LINE"
# Belt and braces: a non-numeric or empty vector must stop the run here, not
# become a window offset that happens to read real cartridge bytes.
for v in VEC_NMI VEC_RESET VEC_IRQ; do
  case "${!v:-}" in
    ''|*[!0-9]*) echo "run.sh: FAIL -- $v is '${!v}', not a number, read from $ROM." >&2
                exit 1 ;;
  esac
  if [ "${!v}" -lt "$((0xC000))" ] || [ "${!v}" -gt "$((0xFFFF))" ]; then
    echo "run.sh: FAIL -- $v = \$${!v} is outside slot 15 (\$C000-\$FFFF)." >&2
    echo "run.sh:        These dumps are MMC3 with a 128 KiB PRG, so all three" >&2
    echo "run.sh:        vectors live in the fixed \$E000 window. A value out of" >&2
    echo "run.sh:        range means the offset arithmetic above is wrong." >&2
    exit 1
  fi
done

# The window starts a little below the lowest of the three so the whole prologue
# is covered, and is then *checked* to contain all three. This assertion is the
# point: an off-by-16 in the file offset below is silent, because the bytes read
# back are still real cartridge bytes -- just the ones 16 earlier.
WIN_LEN=48
WIN_AT=$(( VEC_NMI - 8 ))
for v in VEC_NMI VEC_RESET VEC_IRQ; do
  t=${!v}
  if [ "$t" -lt "$WIN_AT" ] || [ "$t" -ge $((WIN_AT + WIN_LEN)) ]; then
    echo "run.sh: FAIL -- the identity window \$$(printf '%04X' "$WIN_AT")+$$WIN_LEN" >&2
    echo "run.sh:        does not contain $v = \$$(printf '%04X' "$t")." >&2
    echo "run.sh:        widen WIN_LEN or move WIN_AT; do not paper over it." >&2
    exit 1
  fi
done
# CPU addresses in slot 15 ($C000-$FFFF) sit at PRG offset +$10000, and the PRG
# starts after the 16-byte iNES header, so the file offset is that plus $HDR.
WIN_OFF=$((WIN_AT + 0x10000 + HDR))
if [ "$(stat -c %s "$ROM")" -ge $((WIN_OFF + WIN_LEN)) ]; then
  WIN_HEX="$(dd if="$ROM" bs=1 skip=$WIN_OFF count=$WIN_LEN status=none | xxd -p -c 256)"
else
  echo "run.sh: $ROM is too small to hold the fixed window at file offset $(printf '%#x' "$WIN_OFF")" >&2
  exit 1
fi
printf 'run.sh: vectors from the file: nmi=$%04X reset=$%04X irq=$%04X\n' \
  "$VEC_NMI" "$VEC_RESET" "$VEC_IRQ"

# Battery-backed PRG RAM, per dump, from the header bit -- never assumed. Beta 1's
# header byte 6 is $40, so its battery bit is CLEAR and every other dump's is $42
# and set. It used to be written in this script's header comment as a fact about
# "the cartridge", which was true of the release and false of the target.
HAS_BATTERY=0
if [ "$HDR" = "16" ]; then
  F6=$(od -An -tu1 -j 6 -N 1 "$ROM" | tr -d ' ')
  if [ $(( F6 & 2 )) -ne 0 ]; then HAS_BATTERY=1; fi
  echo "run.sh: header byte 6 = \$$(printf '%02X' "$F6"), battery bit $([ "$HAS_BATTERY" = 1 ] && echo SET || echo CLEAR)"
fi

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