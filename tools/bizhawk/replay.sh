#!/usr/bin/env bash
# Replay the verified TAS movie into BizHawk against one ROM, and report how far
# the replay stayed in sync.
#
#   tools/bizhawk/replay.sh <rom> [frames] [mode]
#
#   rom     the cartridge to run. Not verified against the movie's checksum here:
#           tools/fm2.py does that, and it is the thing that must not be skipped.
#   frames  how many movie frames to run (default: all 44003)
#   mode    "sync-first"  stop at the first divergence from the reference series
#           "full"        run to the end regardless
#
# Everything else comes from the environment:
#
#   MOVIE          path to the .fm2 (never in the repo; third-party, copyrighted)
#   OUTDIR         where the run's dumps go (default tools/bizhawk/out/<rom-stem>)
#   OFFSET         inject movie frame (f + OFFSET); 0 unless calibration says
#   PADCHECK       0 to skip the per-frame $4016 readback (not advised)
#   SHOTS          comma-separated frames to screenshot
#   REF_SERIES     a series.tsv from another run, for the drift comparison
#
# Exits non-zero unless the emulator produced a verdict of its own AND the run
# covered the frames it was asked for. A truncated run.sh that "succeeded" is the
# exact bug this repository has already paid for twice, so it is not repeated
# here.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ROM="${1:?usage: replay.sh <rom> [frames] [sync-first|full]}"
FRAMES="${2:-44003}"
MODE="${3:-full}"
MOVIE="${MOVIE:-/tmp/opencode/tas/Magician (U)-FatRatKnight GoodEnd+subtitle.fm2}"
STEM="$(basename "${ROM%.*}")"
OUTDIR="${OUTDIR:-$ROOT/tools/bizhawk/out/replay-$STEM}"
[ -f "$ROM" ] || { echo "replay.sh: no ROM at $ROM" >&2; exit 1; }
[ -f "$MOVIE" ] || { echo "replay.sh: no movie at $MOVIE" >&2
                     echo "             set MOVIE=; it is never committed." >&2
                     exit 1; }
# The table is regenerated into this run's own directory rather than read from a
# shared one, so a stale table from a previous movie cannot be replayed by
# accident. tools/fm2.py re-checks the checksum every time; the header comments it
# writes into the table name the cartridge the movie was recorded on.
mkdir -p "$OUTDIR"
python3 "$ROOT/tools/fm2.py" "$MOVIE" --rom "$ROM" --expect-frames 44003 \
        --replay-anyway "replay.sh run: measuring drift, not claiming a match" \
        --lua "$OUTDIR/movie.lua" >"$OUTDIR/movie-check.txt" 2>&1
if [ $? -ne 0 ]; then
  echo "replay.sh: FAIL -- tools/fm2.py refused the movie/ROM pair:" >&2
  cat "$OUTDIR/movie-check.txt" >&2
  exit 2
fi

export MAGICIAN_PADLUA="$OUTDIR/movie.lua"
export MAGICIAN_REPLAY_OUT="$OUTDIR"
export MAGICIAN_FRAMES="$FRAMES"
export MAGICIAN_PADCHECK="${PADCHECK:-1}"
export MAGICIAN_REPLAY_SHOTS="${SHOTS:-}"
export MAGICIAN_REPLAY_OFFSET="${OFFSET:-0}"
export MAGICIAN_SETTLE="${MAGICIAN_SETTLE:-120}"
export MAGICIAN_EXPECT="$OUTDIR/summary.txt"
export MAGICIAN_DONE="done"
export MAGICIAN_EXPECT_WAIT="${MAGICIAN_EXPECT_WAIT:-900}"

# Syntax-check the Lua before launching an emulator. BizHawk reports a Lua parse
# error through a mono stacktrace and a partially written summary.txt, which is a
# much worse way to learn about a missing `end` than this is.
if command -v luac >/dev/null 2>&1; then
  if ! luac -p "$ROOT/tools/bizhawk/replay.lua" 2>"$OUTDIR/luac.txt"; then
    echo "replay.sh: FAIL -- replay.lua does not parse:" >&2
    sed 's/^/  /' "$OUTDIR/luac.txt" >&2
    exit 1
  fi
  echo "replay.sh: replay.lua parses"
fi

echo "replay.sh: $STEM, $FRAMES frames, mode $MODE, out $OUTDIR"

"$ROOT/tools/bizhawk/run.sh" "$ROOT/tools/bizhawk/replay.lua" "$ROM" \
        "$OUTDIR/bizhawk.log"
rc=$?
echo
if [ $rc -ne 0 ]; then
  echo "replay.sh: FAIL -- run.sh exited $rc" >&2
  exit $rc
fi

echo "replay.sh: summary"
sed 's/^/  /' "$OUTDIR/summary.txt"
echo

# Coverage assertion, in the shell, where it cannot be argued with: the series must
# have a line for every frame asked for. A Lua run that died at frame 400 of 44003
# still writes a passing-looking summary block, and the only thing that catches it
# is counting.
want=$FRAMES
# `grep -c` prints 0 *and* exits 1 when there are no matches, so `|| echo 0`
# appended a second line and the comparison below read "0\n0" as an integer.
# Read it once, tolerate the non-zero exit, and validate it is a number.
got=$(grep -c '^[0-9]' "$OUTDIR/series.tsv" 2>/dev/null) || true
case "$got" in
  ''|*[!0-9]*)
    echo "replay.sh: FAIL -- could not count frames in $OUTDIR/series.tsv" >&2
    echo "            (got '$got'); the run produced no per-frame record at all," >&2
    echo "            which is not a measurement of zero divergence." >&2
    exit 3 ;;
esac
if [ "$got" -ne "$want" ]; then
  echo "replay.sh: FAIL -- series.tsv has $got frames, $want were asked for." >&2
  echo "            The run stopped early; its summary is not a measurement of a" >&2
  echo "            full replay." >&2
  exit 3
fi
echo "replay.sh: ok -- $got/$want frames recorded"

if grep -q '^verdict ok$' "$OUTDIR/summary.txt"; then
  exit 0
fi
echo "replay.sh: FAIL -- the emulator's own verdict was not ok:" >&2
grep -E '^(FAIL|FATAL)' "$OUTDIR/summary.txt" | sed 's/^/  /' >&2
exit 3