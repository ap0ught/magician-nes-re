#!/usr/bin/env bash
# Run a BizHawk measurement on several ROMs, one at a time.
#
#   tools/bizhawk/sweep.sh <lua-script> <outdir-root> <tag:rom> [<tag:rom> ...]
#
# One job at a time, because the sentinel in `MAGICIAN_DONE` is what tells us a
# job finished. Starting the next one before the previous wrote its sentinel is
# what the old `pkill` was trying to prevent -- and it prevented it by killing
# processes, including another project's, and including (measured) the shell
# running the pkill itself.
#
# It does not need to. The fix is to WAIT for the sentinel, which `MAGICIAN_EXPECT`
# already does, and to reap only the EmuHawk processes this sweep started. See
# tools/bizhawk/emuhawk_pids.sh for the identification rules and for the
# measurement that a command-line pattern matches the messenger.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$1"; shift
OUTROOT="$1"; shift

export MAGICIAN_EXPECT_WAIT="${MAGICIAN_EXPECT_WAIT:-600}"
export MAGICIAN_SETTLE="${MAGICIAN_SETTLE:-90}"

PIDS="$ROOT/tools/bizhawk/emuhawk_pids.sh"
[ -x "$PIDS" ] || { echo "sweep: $PIDS is missing or not executable" >&2; exit 2; }

rc=0
# A previous sweep of ours may have been interrupted. Those are ours to close.
"$PIDS" reap >/dev/null 2>&1

for spec in "$@"; do
  tag="${spec%%:*}"
  rom="${spec#*:}"
  if [ ! -f "$rom" ]; then echo "sweep: no ROM at $rom" >&2; rc=1; continue; fi

  # Anything running that is NOT in our record belongs to somebody else. It is
  # left strictly alone. And rather than kill it to satisfy `run.sh`'s guard --
  # which is what this script used to do -- the sweep says so and stops, because
  # it cannot prove the window it is about to open is a new one. The opt-in is
  # the same one `run.sh` offers, and it is measured safe on this machine
  # (SingleInstanceMode=false).
  foreign="$("$PIDS" foreign)"
  if [ -n "$foreign" ]; then
    if [ "${MAGICIAN_ALLOW_CONCURRENT:-0}" = "1" ]; then
      echo "sweep: an EmuHawk this sweep did not start is running (pids$(echo $foreign | tr '\n' ' '));"
      echo "sweep: MAGICIAN_ALLOW_CONCURRENT=1, so it is left running and a second window opens."
    else
      echo "sweep: refusing to touch EmuHawk pids$(echo $foreign | tr '\n' ' ') -- not started by this sweep." >&2
      echo "sweep: close them yourself, or set MAGICIAN_ALLOW_CONCURRENT=1 to run beside them." >&2
      rc=1; continue
    fi
  fi

  before="$("$PIDS" list)"
  mkdir -p "$OUTROOT/$tag"
  echo "=== sweep: $tag  $(basename "$rom")"
  # MAGICIAN_EXPECT is what makes run.sh wait for the sentinel instead of
  # returning on the first non-empty byte. Returning early is how the previous
  # sweep killed its own run -- and it is also why the pkill existed at all.
  MAGICIAN_LUA_OUT="$OUTROOT/$tag" MAGICIAN_EXPECT="$OUTROOT/$tag/frames.txt" \
    MAGICIAN_DONE="done, frames" \
    bash "$ROOT/tools/bizhawk/run.sh" "$SCRIPT" "$rom" "$OUTROOT/$tag.log" 2>&1 \
    | grep -E 'verdict|ok /|FAIL|coverage' | sed 's/^/    /'
  st=${PIPESTATUS[0]}

  # Whatever EmuHawk run.sh just opened is ours, by the difference between the
  # set running now and the set running before it. Recording them is what lets
  # the NEXT job close them without touching anybody else's.
  after="$("$PIDS" list)"
  new=""
  for pid in $after; do
    grep -qx "$pid" <<<"$before" || new="$new $pid"
  done
  [ -n "$new" ] && "$PIDS" add $new >/dev/null 2>&1

  [ "$st" -eq 0 ] || { echo "    run.sh exited $st for $tag" >&2; rc=1; }
done

# Leave nothing of ours running: this script started them, and a sweep that exits
# with its windows still up is how the next run meets a stale instance.
"$PIDS" reap >/dev/null 2>&1
exit $rc