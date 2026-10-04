#!/usr/bin/env bash
# Run a BizHawk measurement on several ROMs, one at a time.
#
#   tools/bizhawk/sweep.sh <lua-script> <outdir-root> <tag:rom> [<tag:rom> ...]
#
# BizHawk allows one instance at a time: a second launch is diverted into the
# first through the single-instance pipe, shows no window, and quietly measures
# the wrong thing. So this kills any leftover instance first, runs the jobs in
# order, and waits for each one's completion sentinel before starting the next.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$1"; shift
OUTROOT="$1"; shift

export MAGICIAN_EXPECT_WAIT="${MAGICIAN_EXPECT_WAIT:-600}"
export MAGICIAN_SETTLE="${MAGICIAN_SETTLE:-90}"

rc=0
for spec in "$@"; do
  tag="${spec%%:*}"
  rom="${spec#*:}"
  if [ ! -f "$rom" ]; then echo "sweep: no ROM at $rom" >&2; rc=1; continue; fi
  if pgrep -f '[m]ono EmuHawk' >/dev/null; then
    pkill -f '[m]ono EmuHawk'
    sleep 3
    pgrep -f '[m]ono EmuHawk' >/dev/null && { echo "sweep: stale instance would not die" >&2; rc=1; continue; }
  fi
  mkdir -p "$OUTROOT/$tag"
  echo "=== sweep: $tag  $(basename "$rom")"
  # MAGICIAN_EXPECT is what makes run.sh wait for the sentinel instead of
  # returning on the first non-empty byte -- and returning early is how the
  # previous sweep killed its own run: the next job's pkill took out an EmuHawk
  # that was still two frames from finishing.
  MAGICIAN_LUA_OUT="$OUTROOT/$tag" MAGICIAN_EXPECT="$OUTROOT/$tag/frames.txt" \
    MAGICIAN_DONE="done, frames" \
    bash "$ROOT/tools/bizhawk/run.sh" "$SCRIPT" "$rom" "$OUTROOT/$tag.log" 2>&1 \
    | grep -E 'verdict|ok /|FAIL|coverage' | sed 's/^/    /'
  st=${PIPESTATUS[0]}
  [ "$st" -eq 0 ] || { echo "    run.sh exited $st for $tag" >&2; rc=1; }
done
exit $rc