#!/usr/bin/env bash
# Is this project's instrument actually installed and usable?  `make doctor`
#
#   tools/bizhawk/doctor.sh [--quiet]
#
# WHY THIS IS A SCRIPT AND NOT A TEST
# -----------------------------------
# `make check-py` is the gate and it must stay runnable in seconds with no
# emulator, no cartridge, no display and no network -- so it cannot answer any of
# this. This is the other half: a report about the MACHINE, which is what you need
# before the first run of a session and what nothing else tells you.
#
# IT DOES NOT SOURCE THE RESOLVER
# -------------------------------
# `bizpath.sh` refuses when there is no emulator, and "there is no emulator" is
# the single most likely thing this script is called to report. Sourcing it would
# mean reporting the resolver's refusal instead of a diagnosis. So the constants
# are sourced constants-only and the resolution is reproduced here -- which is a
# second copy of one rule, and `src/testing/test_bizpath.py` section F is what
# keeps the copies equal. Where this script has to disagree with `bizpath.sh`
# about something it can see (a directory that exists but is not a BizHawk), it
# says so rather than exiting, because that is the case worth a report.
#
# EXIT CODES, one per thing, so a script can act on which one failed
# ---------------------------------------------------------------
#   0  ready
#   2  the emulator install is missing or incomplete   -> make emu-setup
#   3  the runtime tools are missing                   -> pacman -S ...
#   4  the display is not available or not up          -> make display-up
#   5  the cartridge or the rebuild is absent          -> your own dump / make rom
#
# More than one can apply and ALL of them are printed. The one REPORTED is the
# FIRST to fail in the order the checks run, which is the order of dependency:
# missing tools make the emulator unusable whatever else is wrong, a missing
# emulator makes the display irrelevant, and so on.
#
# The first version took the HIGHEST code, which is a magnitude where the truth is
# a precedence: on a machine with no `mono` and no cartridge it reported exit 5,
# the cartridge, and said nothing about the runtime. Reporting the first failure is
# what makes "exit 3" mean "install these packages" rather than "something, and
# probably not that".

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MAGICIAN_BIZHAWK_CONSTANTS_ONLY=1
. "$ROOT/tools/bizhawk/bizpath.sh"

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

# The display half, resolved the same three-rule way bizpath.sh does.
if [ -n "${MAGICIAN_DISPLAY:-}" ]; then DISP="$MAGICIAN_DISPLAY"; DWHY="MAGICIAN_DISPLAY"
elif [ -n "${DISPLAY:-}" ]; then  DISP="$DISPLAY";            DWHY="inherited DISPLAY"
else                              DISP=":2";                  DWHY="the default"
fi
NUM="${DISP#:}"

# The emulator, resolved WITHOUT validating -- a refusal is what we are reporting.
if [ -n "${MAGICIAN_BIZHAWK:-}" ] && [ -n "${BIZHAWK:-}" ] \
   && [ "$MAGICIAN_BIZHAWK" != "$BIZHAWK" ]; then
  BIZ="$MAGICIAN_BIZHAWK"; BWHY="MAGICIAN_BIZHAWK (CONFLICTS with BIZHAWK=$BIZHAWK -- fix this first)"
elif [ -n "${MAGICIAN_BIZHAWK:-}" ]; then BIZ="$MAGICIAN_BIZHAWK"; BWHY="MAGICIAN_BIZHAWK"
elif [ -n "${BIZHAWK:-}" ]; then          BIZ="$BIZHAWK";          BWHY="BIZHAWK (alias)"
else BIZ="$MAGICIAN_BIZHAWK_DEFAULT";     BWHY="the default location"
fi

# `ldconfig -p` ONCE, into a variable, and everything below greps the variable.
#
# `ldconfig -p | grep -q libgdiplus` is the obvious way to write this and it is
# WRONG under `pipefail`, which this script sets. `grep -q` exits the moment it
# finds a match, ldconfig dies on SIGPIPE, and the pipeline's status becomes 141 --
# so the test answers "not found" for a library that is installed. This script
# reported exactly that, for libgdiplus and libgtk-x11-2.0.so.0, on the first run
# on a machine where both are present. Same family as `[ x -lt 0x8000 ]`: a guard
# whose own plumbing decides the answer.
LDCACHE="$(ldconfig -p 2>/dev/null)"
has_lib() { case "$LDCACHE" in *"$1"*) return 0 ;; *) return 1 ;; esac; }

# Failures in the order they were discovered; the verdict reports the first.
FAILS=""
note() { FAILS="$FAILS $1"; }
rc_first() { for c in $FAILS; do printf '%s' "$c"; return; done; printf '0'; }
out()  { [ "$QUIET" = "1" ] || printf '%s\n' "$*"; }

yes() { out "  ok      $*"; }
no()  { out "  MISSING $*"; }
why() { out "          $*"; }

printf 'magician-nes doctor\n'
printf '  repo      %s\n' "$ROOT"
printf '  emulator  %s\n' "$BIZ"
printf '            (from %s)\n' "$BWHY"
printf '  display   %s  (from %s)\n' "$DISP" "$DWHY"
out ""

# ------------------------------------------------------------------ the emulator
if [ ! -d "$BIZ" ]; then
  no "no emulator directory"
  why "make emu-setup     installs $MAGICIAN_BIZHAWK_DIRNAME (~150 MB) into"
  why "                  $MAGICIAN_BIZHAWK_HOME and builds Lua/socket/core.so"
  note 2
elif [ ! -f "$BIZ/EmuHawkMono.sh" ]; then
  no "$BIZ exists but has no EmuHawkMono.sh -- it is not a BizHawk install"
  why "make emu-setup, or MAGICIAN_BIZHAWK=/path/to/a/real/one"
  note 2
elif [ ! -x "$BIZ/EmuHawkMono.sh" ]; then
  no "$BIZ/EmuHawkMono.sh is not executable"
  why "chmod +x '$BIZ/EmuHawkMono.sh'   -- the tarball ships it 0644"
  note 2
else
  yes "EmuHawkMono.sh present and executable"
  if [ -d "$BIZ/dll" ]; then
    yes "dll/ present (LD_LIBRARY_PATH is set to \$BIZ/dll:\$BIZ)"
  else
    no "no dll/ -- mono EmuHawk.exe cannot resolve BizHawk's own assemblies"
    note 2
  fi
  if [ -w "$BIZ" ]; then
    yes "directory writable (EmuHawkMono.sh writes EmuHawkMono_last*.txt here)"
  else
    no "directory is NOT writable"
    why "BizHawk writes config.ini and NES/State/ here, and the launcher writes"
    why "EmuHawkMono_laststdout.txt and _laststderr.txt here on every launch"
    note 2
  fi
  if [ -f "$BIZ/Lua/socket/core.so" ]; then
    yes "Lua/socket/core.so present (bridge.lua's require('socket.core'))"
  else
    no "no Lua/socket/core.so -- src/play/bridge.lua will die on its first line"
    why "BizHawk ships only the Windows core.dll. Without this module the run"
    why "reports a CONNECT TIMEOUT, which reads like a network problem."
    why "make emu-setup     builds it against the Lua 5.4 NLua embeds"
    note 2
  fi
  if [ -f "$BIZ/config.ini" ]; then
    # MEASURED on a fresh install: BizHawk writes this itself on first launch and
    # it already carries "SingleInstanceMode": false. So this line is reported,
    # not assumed -- run.sh's concurrency opt-in is justified by that setting, and
    # if a future BizHawk changed the default the justification would silently rot.
    sim="$(grep -o '"SingleInstanceMode": *[a-z]*' "$BIZ/config.ini" 2>/dev/null | head -1)"
    yes "config.ini present ${sim:-(-- SingleInstanceMode not found in it)}"
  else
    yes "no config.ini yet -- BizHawk writes one on first launch"
  fi
  # The version is only knowable from the directory name, and that is stated
  # rather than dressed up as a version probe.
  case "$(basename "$BIZ")" in
    "$MAGICIAN_BIZHAWK_DIRNAME") yes "directory name matches $MAGICIAN_BIZHAWK_DIRNAME" ;;
    *) no "directory is called '$(basename "$BIZ")', not '$MAGICIAN_BIZHAWK_DIRNAME'"
       why "not necessarily wrong -- an install of a different version, or one"
       why "you named yourself -- but nothing here can tell you which it is"
       ;;
  esac
fi
out ""

# ------------------------------------------------------------------ the runtime
for t in mono python3 curl; do
  command -v "$t" >/dev/null && yes "$t present" || { no "$t not on PATH"; note 3; }
done
if has_lib libgdiplus; then
  yes "libgdiplus present -- run.sh's --gdi needs it (the OpenGL control dies with an X11 BadMatch here)"
else
  no "no libgdiplus"
  why "sudo pacman -S libgdiplus   -- run.sh passes --gdi, which needs it"
  note 3
fi
if has_lib libgtk-x11-2.0.so.0; then
  yes "libgtk-x11-2.0 present"
  why "without it Mono falls back to its built-in X11 driver, which throws a"
  why "BadMatch from BizHawk's input thread ~30 s into play: the window stays up,"
  why "the Lua bridge stops being serviced, and the run looks like it froze"
else
  no "no libgtk-x11-2.0.so.0"
  why "sudo pacman -S gtk2          -- see the line above; the symptom is a frozen"
  why "                             run, not a crash, and it is easy to misread"
  note 3
fi
out ""

# ------------------------------------------------------------------ the display
if command -v Xephyr >/dev/null; then
  yes "Xephyr present"
  why "Xephyr, not Xvfb. There is no Xvfb on this machine and no"
  why "xorg-server-xvfb installed, which is what an old comment here said -- and"
  why "which was read as \"BizHawk is GUI-only\". A nested X server is what is"
  why "actually needed, and BizHawk runs on one (journal/14)."
else
  no "no Xephyr"
  why "sudo pacman -S xorg-server-xephyr"
  note 3
fi
if [ -S "/tmp/.X11-unix/X${NUM}" ]; then
  yes "$DISP is up"
else
  no "$DISP is not up"
  why "make display-up       starts Xephyr on $DISP with no window manager"
  why "                      (measured: none is needed; the bridge reads the core"
  why "                      and screenshots come from client.screenshot())"
  note 4
fi
out ""

# ------------------------------------------------------------------ the cartridges
# `python3` explicitly: tools/cartref.py is not executable, and calling it as
# "$ROOT/tools/cartref.py" produced "Permission denied" on stderr -- swallowed by
# the 2>/dev/null below, so cartref's answer was an empty string and this script
# reported the cartridge missing on a machine where it is right there.
cart="$(python3 "$ROOT/tools/cartref.py" 2>/dev/null || true)"
if [ -n "$cart" ] && [ -f "$cart" ]; then
  yes "cartridge present: $cart"
  why "read in place, never written, never committed (LEGAL.md)"
else
  no "no cartridge at the path tools/cartref.py prints"
  why "python3 tools/cartref.py --release/--beta1 prints the candidates"
  why "or: make probe CART=/path/to/your/dump.nes"
  note 5
fi
if [ -f "$ROOT/asm/out/magician-rebuilt.nes" ]; then
  yes "the rebuild is present: asm/out/magician-rebuilt.nes"
else
  no "no asm/out/magician-rebuilt.nes"
  why "make rom"
  note 5
fi
out ""

# --------------------------------------------------------------------- verdict
RC="$(rc_first)"
case "$RC" in
  0) out "doctor: ready.  make probe   /   python3 src/play/smoke.py" ;;
  2) out "doctor: the EMULATOR is not usable. Exit 2. See MISSING above." ;;
  3) out "doctor: RUNTIME tools are missing. Exit 3." ;;
  4) out "doctor: the DISPLAY is not up. Exit 4. Run: make display-up" ;;
  5) out "doctor: a CARTRIDGE or the REBUILD is absent. Exit 5." ;;
  *) out "doctor: not ready. Exit $RC. Failures found:$FAILS" ;;
esac
exit "$RC"
