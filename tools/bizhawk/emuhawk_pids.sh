#!/usr/bin/env bash
# List, register and kill the EmuHawk processes THIS PROJECT started.
#
#   tools/bizhawk/emuhawk_pids.sh list                 -> PIDs, one per line
#   tools/bizhawk/emuhawk_pids.sh add PID...           -> remember PIDs as ours
#   tools/bizhawk/emuhawk_pids.sh forget               -> drop the record
#   tools/bizhawk/emuhawk_pids.sh reap                 -> kill only recorded PIDs
#   tools/bizhawk/emuhawk_pids.sh foreign              -> PIDs we did NOT start
#
# WHY THIS EXISTS AT ALL
# ---------------------
# `sweep.sh` used to run `pkill -f '[m]ono EmuHawk'` before every job. That is the
# incident `tools/isolation.sh` exists to prevent, committed inside the tooling
# written to prevent it, and `sweep.sh`'s own comment records the burn:
#
#   "the next job's pkill took out an EmuHawk that was still two frames from
#    finishing."
#
# Two separate faults, and both are reproducible rather than theoretical.
#
# 1. **It killed other projects' emulators.** There is nothing in `mono EmuHawk`
#    that says whose window this is. `aibeatszelda` has its own BizHawk and its
#    own long replay; a sweep here would end it.
#
# 2. **A pattern over command lines matches the messenger.** `pgrep -f` and
#    `pkill -f` compare the pattern against every process's `cmdline`, including
#    the shell and the Python that are running the command that mentions the
#    string. Measured while writing this: with one stand-in EmuHawk running,
#    `pgrep -f '[m]ono EmuHawk'` returned THREE pids -- the emulator, and the
#    `bash` and `python3` whose argv contained the pattern as an argument.
#    `pkill` on that list kills the caller. This is the same failure `run.sh` and
#    `display.sh` already stopped making; `display.sh` enumerates `/proc/[0-9]*`
#    and reads each process's own `comm`, and this does the same.
#
# HOW A PROCESS IS IDENTIFIED
# ---------------------------
# Two reads out of `/proc/<pid>/`, never a search:
#
#   * `comm` is exactly `mono`. On this machine BizHawk runs under mono, so the
#     executable name is `mono` and the arguments carry `EmuHawk`.
#   * `cmdline` contains `EmuHawk`. This is read from THAT process's own cmdline,
#     which is what makes it safe: it cannot match a shell that merely mentions
#     the string, because a shell's comm is `bash`, not `mono`.
#
# Both halves are needed. `comm` alone would match any mono process; the cmdline
# half alone is the pattern search this file exists to avoid.
#
# `reap` kills only PIDs that are BOTH in the record AND still identifiable as an
# EmuHawk. A recorded PID that has been recycled by an unrelated process is left
# alone -- that check is the difference between a pidfile and a loaded gun, and
# `src/testing/test_sweep_pids.py` asserts it.
#
# THE RECORD
# ----------
# `MAGICIAN_SWEEP_PIDFILE`, defaulting under `logs/` (which `.gitignore` already
# excludes). It is a plain list of PIDs, one per line, so it can be read and
# checked without this script. `add` refuses a PID that is not an identifiable
# EmuHawk, so a mistyped or recycled PID cannot be recorded in the first place.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIDFILE="${MAGICIAN_SWEEP_PIDFILE:-$ROOT/logs/sweep-emuhawk.pids}"

die() { echo "emuhawk_pids: $*" >&2; exit 2; }

# Is PID an EmuHawk this machine would recognise? comm first, cmdline second.
is_emuhawk() {
  local pid="$1" p="/proc/$1" comm
  [ -d "$p" ] || return 1
  comm="$(cat "$p/comm" 2>/dev/null || true)"
  [ "$comm" = "mono" ] || return 1
  tr '\0' ' ' < "$p/cmdline" 2>/dev/null | grep -q "EmuHawk"
}

# Is PID still running? A ZOMBIE DOES NOT COUNT.
#
# `test_sweep_pids.py`'s stand-ins are children of the test process, so a killed
# one lingers as a zombie until it is waited for, and `/proc/<pid>` keeps existing
# for the whole of that time. `[ -d /proc/$pid ]` therefore reported "would not
# die" about a process that had died, and `reap` exits 3 on that -- so the first
# version of this tool raised a false alarm on every successful kill of its own
# child. Found by check 6b, which is why that check exists.
#
# A zombie has released its memory, its files and its windows. It is holding
# nothing, so treating it as alive is wrong in the direction that matters: it makes
# the tool complain about a kill that worked.
pid_running() {
  local pid="$1" st
  [ -d "/proc/$pid" ] || return 1
  st="$(sed -n 's/^State:[[:space:]]*\([A-Z]\).*/\1/p' "/proc/$pid/status" 2>/dev/null)"
  [ "$st" = "Z" ] && return 1
  return 0
}

running() {
  local p pid
  for p in /proc/[0-9]*; do
    pid="${p#/proc/}"
    is_emuhawk "$pid" && printf '%s\n' "$pid"
  done
  return 0
}

recorded() {
  [ -f "$PIDFILE" ] || return 0
  grep -E '^[0-9]+$' "$PIDFILE" 2>/dev/null || true
}

case "${1:-list}" in
list)
  running
  ;;

add)
  shift
  [ "$#" -gt 0 ] || die "add needs at least one PID"
  mkdir -p "$(dirname "$PIDFILE")"
  for pid in "$@"; do
    case "$pid" in
      ''|*[!0-9]*) die "not a PID: '$pid'" ;;
    esac
    # Refuse to record something we cannot identify. Without this a typo lands
    # in the pidfile and the next `reap` either kills a stranger or kills
    # nothing and reports success.
    is_emuhawk "$pid" || die "pid $pid is not an EmuHawk here (comm must be 'mono', cmdline must contain 'EmuHawk'); refusing to record it"
    recorded | grep -qx "$pid" || printf '%s\n' "$pid" >> "$PIDFILE"
  done
  echo "emuhawk_pids: recorded $* in $PIDFILE"
  ;;

forget)
  rm -f "$PIDFILE"
  echo "emuhawk_pids: forgot $PIDFILE"
  ;;

reap)
  ours=""
  for pid in $(recorded); do
    if is_emuhawk "$pid"; then
      kill "$pid" 2>/dev/null && ours="$ours $pid"
    else
      # Either it is gone, or the PID has been recycled by something that is
      # not an EmuHawk. Either way it is not ours to kill.
      echo "emuhawk_pids: pid $pid is no longer an EmuHawk; not killing it" >&2
    fi
  done
  if [ -n "$ours" ]; then
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      still=""
      for pid in $ours; do pid_running "$pid" && still="$still $pid"; done
      [ -n "$still" ] || break
      sleep 0.5
    done
    for pid in $ours; do
      if pid_running "$pid"; then
        echo "emuhawk_pids: own pid $pid would not die" >&2
        exit 3
      fi
    done
    echo "emuhawk_pids: killed own EmuHawk pids:$ours"
  else
    echo "emuhawk_pids: nothing of ours to reap"
  fi
  ;;

foreign)
  ours="$(recorded)"
  running | while read -r pid; do
    grep -qx "$pid" <<<"$ours" || printf '%s\n' "$pid"
  done
  ;;

*)
  die "unknown command '$1' (list|add|forget|reap|foreign)"
  ;;
esac