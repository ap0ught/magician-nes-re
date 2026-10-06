#!/usr/bin/env bash
# This project's own X display.  `make display-up` / `make display-down` / `make display-status`
#
#   tools/bizhawk/display.sh up      [WxH]
#   tools/bizhawk/display.sh down
#   tools/bizhawk/display.sh status
#
# WHY A NESTED DISPLAY AT ALL, WHEN :0 WORKS
# -------------------------------------------
# MEASURED, 2026-10-05, before writing any of this: BizHawk runs fine on Xephyr :2
# with NO window manager on it at all.
#
#     DISPLAY=:0  python3 src/play/smoke.py 120  ->  connected in 2.0s
#     DISPLAY=:2  python3 src/play/smoke.py 120  ->  connected in 2.2s
#     work RAM fingerprint, both: efa771ed1c1964d152baae62c0ee68abf0a12b16
#
# Identical RAM, so the display does not change the emulation; and on :2 with no WM
# at all, BizHawk maps a real, correctly-sized window:
#
#     0x200077 "magician-rebuilt [NES] - BizHawk": ()  586x503+22+22
#
# So i3 is NOT needed here and is not started. The neighbouring project runs five
# emulator windows as a documentary and needs a tiling WM to read them; this one
# measures the core through Lua and takes its screenshots from
# `client.screenshot()`, so window placement is irrelevant to every number it
# produces. Simpler is preferred here, and it was measured rather than assumed --
# `journal/05` records the earlier claim that "BizHawk cannot be driven headless
# here", which was really a claim about Xvfb specifically and was never true about
# a nested X server.
#
# :2 AND NOT :1
# --------------
# The neighbouring project uses :1. Two projects on one display cannot be watched
# at the same time, which is the entire reason for nesting one. Override with
# MAGICIAN_DISPLAY if you need to.
#
# WHY `down` KILLS BY PID AND CHECKS THE PID'S COMMAND LINE
# ---------------------------------------------------------
# The incident that caused this file to exist was a `pkill` in one project killing
# a long replay belonging to another. `pkill -f Xephyr` here would do exactly that
# to the other project's display server, so it is not used anywhere in this file.
# `down` reads a pidfile, then verifies via `/proc/<pid>/cmdline` that the process
# really is an Xephyr on the display it started before sending it anything, and it
# refuses outright on a display this project does not own.
#
# Xephyr, NOT Xvfb
# ----------------
# There is no Xvfb on this machine and no `xorg-server-xvfb` installed, which is
# what `Makefile`'s old note and `tools/nestrace.py`'s docstring said. Both were
# literally true and both were read as "BizHawk is GUI-only". Xephyr is present
# (`xorg-server-xephyr` 21.1.24) and it nests inside the session's own X server, so
# it needs `DISPLAY` to already be right -- which it is, under a desktop session,
# and which a systemd unit has to set for itself.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MAGICIAN_BIZHAWK_CONSTANTS_ONLY=1
. "$ROOT/tools/bizhawk/bizpath.sh"

# The resolution here is ONLY for the display half; the emulator half is not this
# script's business and must not be able to fail the display. So the display is
# resolved by the same three-rule precedence as bizpath.sh but without the
# emulator validation, rather than by sourcing a resolver that refuses when there
# is no install -- which would make `display-up` fail for an unrelated reason.
if [ -n "${MAGICIAN_DISPLAY:-}" ]; then
  DISP="$MAGICIAN_DISPLAY"; WHY="MAGICIAN_DISPLAY"
elif [ -n "${DISPLAY:-}" ]; then
  DISP="$DISPLAY"; WHY="inherited DISPLAY"
else
  DISP="${MAGICIAN_DISPLAY_DEFAULT:-:2}"; WHY="the default"
fi
NUM="${DISP#:}"

# The one display this project must never manage. Hard-coded on purpose: it is the
# neighbouring project's, and a `down` that killed it would cost them a run.
NOT_OURS=":1"

PIDFILE="$ROOT/logs/xephyr-${NUM}.pid"
LOGFILE="$ROOT/logs/xephyr-${NUM}.log"
GEOMETRY="${MAGICIAN_XEPHYRY_GEOMETRY:-1280x800}"

say()  { printf 'display.sh: %s\n' "$*"; }
die()  { printf 'display.sh: FAIL -- %s\n' "$1" >&2; exit "${2:-1}"; }

# Is a server already listening on this display? A unix socket under
# /tmp/.X11-unix is the authority; a lock file under /tmp/.X{N}-lock is the other.
# Both are checked because either alone has a failure mode: the socket can be a
# stale file left by a crashed server, and the lock file can be missing entirely.
display_busy() {
  [ -S "/tmp/.X11-unix/X${NUM}" ] || [ -e "/tmp/.X${NUM}-lock" ]
}

xephyr_pids_here() {
  # The pids of every Xephyr whose command line names THIS display. Used only to
  # report; `down` still acts on the pidfile alone.
  #
  # `comm` is the executable name, and requiring it is what keeps the SHELL that
  # launched Xephyr out of the answer. The first version matched on
  # /proc/<pid>/cmdline alone and cheerfully listed the shell running the command
  # that mentioned `Xephyr :5` -- the same reason `BizHawk.running_emuhawk` reads
  # `comm` instead of `pkill -f`: a pattern over command lines matches the matcher,
  # and here it matched the messenger.
  local p pid comm
  for p in /proc/[0-9]*; do
    pid="${p#/proc/}"
    comm="$(cat "$p/comm" 2>/dev/null || true)"
    [ "$comm" = "Xephyr" ] || continue
    [ -r "$p/cmdline" ] || continue
    if tr '\0' ' ' < "$p/cmdline" 2>/dev/null | grep -q "Xephyr :${NUM}\b"; then
      printf '%s ' "$pid"
    fi
  done
}

case "${1:-status}" in
up)
  command -v Xephyr >/dev/null \
    || die "no Xephyr on this machine. On Arch/CachyOS: sudo pacman -S xorg-server-xephyr" 4
  [ "$DISP" = "$NOT_OURS" ] && \
    say "WARNING: $DISP is another project's display. This will not work beside whatever is on it."
  mkdir -p "$(dirname "$PIDFILE")"

  if [ -s "$PIDFILE" ]; then
    pid="$(cat "$PIDFILE")"
    if [ -d "/proc/$pid" ] && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null \
         | grep -q "Xephyr :${NUM}\b"; then
      say "$DISP is already up (pid $pid), started by this project. Nothing to do."
      exit 0
    fi
    say "stale pidfile for $DISP (pid $pid is not an Xephyr on $DISP); removing it"
    rm -f "$PIDFILE"
  fi

  if display_busy; then
    die "$DISP is already in use by a server this project did not start.
  Refusing to start a second X server on it: Xephyr would fail on the lock, or
  worse, one of the two would end up owning windows the other put up.
  Use a different display:  MAGICIAN_DISPLAY=:3 make display-up
  Running now: $(xephyr_pids_here)" 5
  fi

  # Detached properly. The neighbouring project documents the failure this avoids:
  # a background job of a shell that later exits takes its process group with it,
  # and the display vanishes with no message. `setsid` gives it its own.
  DISPLAY="${DISPLAY:-$DISP}" setsid Xephyr "$DISP" \
      -screen "$GEOMETRY" -resizeable -ac -nolisten tcp \
      >"$LOGFILE" 2>&1 </dev/null &
  sleep 3
  pid="$(pgrep -f "Xephyr ${DISP} " | head -1 || true)"
  if [ -z "$pid" ] || [ ! -S "/tmp/.X11-unix/X${NUM}" ]; then
    say "Xephyr did not come up on $DISP. Log:"
    tail -n 20 "$LOGFILE" | sed 's/^/  /' >&2
    exit 6
  fi
  printf '%s\n' "$pid" > "$PIDFILE"
  say "$DISP is up: Xephyr pid $pid, $GEOMETRY, no window manager (not needed -- see the header)"
  say "  window manager: none, deliberately. MEASURED: BizHawk runs and the bridge"
  say "  answers on $DISP with no WM, and the work-RAM fingerprint is identical to :0."
  say "  start a run with:  DISPLAY=$DISP make probe   (or just: make probe, if $DISP"
  say "  is what your session already has)"
  say "  log: $LOGFILE"
  ;;

down)
  if [ ! -s "$PIDFILE" ]; then
    say "no pidfile at $PIDFILE, so this project has no display to stop."
    say "NOT doing a 'pkill -f Xephyr': another project runs one on $NOT_OURS, and"
    say "killing a long run of theirs is what this script exists to prevent."
    exit 0
  fi
  pid="$(cat "$PIDFILE")"
  if [ "$DISP" = "$NOT_OURS" ]; then
    die "refusing to stop a display this project does not own ($DISP). Set
  MAGICIAN_DISPLAY to the display you actually want stopped." 7
  fi
  if [ ! -d "/proc/$pid" ]; then
    say "pid $pid is gone; the display is already down. Removing the pidfile."
    rm -f "$PIDFILE"
    exit 0
  fi
  cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)"
  case "$cmd" in
    *Xephyr*" :${NUM} "*|*Xephyr*" :${NUM}") ;;
    *)  die "pid $pid is no longer the Xephyr this script started on $DISP.
  It is now: $cmd
  Refusing to kill it. A recycled PID is a real thing and a pattern match cannot
  tell one from the process you meant." 8 ;;
  esac
  say "stopping Xephyr pid $pid on $DISP"
  kill "$pid" 2>/dev/null || die "kill $pid failed" 8
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -d "/proc/$pid" ] || break
    sleep 0.5
  done
  [ -d "/proc/$pid" ] && die "$pid is still alive after 5s; not escalating to SIGKILL on a display we do not fully own" 8
  rm -f "$PIDFILE"
  say "$DISP is down"
  ;;

status)
  printf 'display         : %s  (from %s)\n' "$DISP" "$WHY"
  printf 'geometry        : %s\n' "$GEOMETRY"
  printf 'pidfile         : %s%s\n' "$PIDFILE" \
         "$([ -s "$PIDFILE" ] && printf ' (pid %s)' "$(cat "$PIDFILE")" || printf ' (absent)')"
  if display_busy; then
    printf 'server on %s   : yes\n' "$DISP"
    printf 'xephyr pids here: %s\n' "$(xephyr_pids_here)"
  else
    printf 'server on %s   : no\n' "$DISP"
    printf '                 start it with: make display-up\n'
  fi
  # Informational, and it is the question a reader has after seeing "no WM":
  # does anything need arranging? MEASURED answer: no.
  printf 'window manager  : none started, and none needed. BizHawk runs on %s\n' "$DISP"
  printf '                 without one and takes its screenshots from\n'
  printf '                 client.screenshot(), not from a window.\n'
  ;;

*)
  die "unknown command '${1:-}'. Use up | down | status." 2
  ;;
esac
