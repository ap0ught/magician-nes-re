#!/usr/bin/env bash
# Run one BizHawk session with a Lua script attached, headful on the live display.
#
#   tools/bizhawk/run.sh <script.lua> <rom> [logfile]
#
# BizHawk 2.11.1 facts this depends on, all measured on this machine:
#
#   * `--lua <path>` loads a script at startup and implies `--luaconsole`. This is
#     the only supported way to get code running inside the emulator, and it is
#     what makes BizHawk usable as an instrument: emu.frameadvance() gives exact
#     frame boundaries and memory.* reads the core's own state, instead of
#     scraping a window that may be occluded.
#   * `--gdi` (libgdiplus) is required. The OpenGL control dies with an X11
#     BadMatch on this session.
#   * Only ONE instance may run: the second is diverted to the first through the
#     single-instance pipe and never shows a window. Run these serially.
#   * NES/SaveRAM is written by BizHawk, not by us; the cartridge itself is only
#     ever read. Wiping the SRAM is required, not cosmetic -- the cartridge has
#     battery-backed PRG RAM and a stale save resumes the previous session.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${1:?usage: run.sh <script.lua> <rom> [logfile]}"
ROM="${2:?usage: run.sh <script.lua> <rom> [logfile]}"
LOG="${3:-/tmp/opencode/bizhawk_run.log}"
BIZ="${BIZHAWK:-$HOME/code/games/aibeatszelda/BizHawk-2.11.1-win-x64}"

[ -f "$ROM" ] || { echo "no ROM at $ROM" >&2; exit 1; }
[ -f "$SCRIPT" ] || { echo "no script at $SCRIPT" >&2; exit 1; }
[ -d "$BIZ" ] || { echo "BizHawk not at $BIZ; set BIZHAWK=/path" >&2; exit 1; }

SRAM="${MAGICIAN_SRAM:-$BIZ/NES/SaveRAM}"
mkdir -p "$SRAM"
# Clear every saved image, not just one: the name comes from the ROM's filename.
find "$SRAM" -maxdepth 1 -name '*.SaveRAM*' -delete
echo "run.sh: cleared $SRAM/*.SaveRAM"

CFG="${MAGICIAN_INI:-$BIZ/config.ini}"
cp -f "$CFG" "$CFG.runsh.bak" 2>/dev/null || true

export LD_LIBRARY_PATH="$BIZ/dll:$BIZ:/usr/lib"
export MONO_WINFORMS_XIM_STYLE=disabled
export MONO_CRASH_NOFILE=1

echo "run.sh: $ROM with $SCRIPT"
# Every fd of the launcher subshell is detached, not just EmuHawk's: EmuHawkMono.sh
# backgrounds two `tee` processes that inherit stdout, so redirecting only the
# exec'd command leaves the caller's pipe held open forever.
SCRIPT_ABS="$(readlink -f "$SCRIPT")"
( cd "$BIZ" && setsid ./EmuHawkMono.sh --gdi --config "$CFG" \
      --lua "$SCRIPT_ABS" "$ROM" </dev/null >"$LOG" 2>&1 & ) \
    </dev/null >/dev/null 2>&1

sleep "${MAGICIAN_SETTLE:-20}"
if pgrep -f 'EmuHawk.exe' >/dev/null; then
  echo "run.sh: EmuHawk alive; window should be on :0"
else
  echo "run.sh: EmuHawk EXITED -- log follows" >&2
fi
cat "$LOG"
