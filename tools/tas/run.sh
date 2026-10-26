#!/usr/bin/env bash
# Run an FCEUX .fm2 TAS to its last frame and decide whether it ran correctly.
#
#   tools/tas/run.sh --movie M --rom R [--outdir D] [--keep] [--quiet]
#
# EXIT CODES -- four states, because "it didn't work" and "I couldn't tell" are
# different answers and an agent that reports them the same way is lying.
#
#   0  verified   the movie played every frame, the input it delivered was the
#                 movie's own input at a single consistent alignment, and the
#                 game's main loop advanced
#   2  identity   the ROM is not the cartridge the movie was recorded on, or the
#                 movie is unreadable. Nothing was emulated.
#   3  movie      FCEUX ran and the movie did not verify: it stalled early, the
#                 delivered input was not the movie's, or the game did not move
#   4  harness    the run itself could not be completed or judged: FCEUX missing,
#                 a Lua parse error, a timeout, or no usable summary
#
# WHAT IS AND IS NOT PROVEN
# -------------------------
# An .fm2 stores input, not state -- every frame line of this movie has empty
# state columns -- so "the emulator did not complain" is NOT evidence that the
# game followed the run. Three things are checked that are: the cartridge is the
# one the movie names (identity.py, before any emulation), the sequence of
# buttons FCEUX delivered to port 1 equals the movie's own sequence at exactly
# one consistent frame offset (drive.lua, measured rather than assumed), and the
# game's `phase` word -- the main-loop state, which indexes g00..g0a -- takes
# more than one value over the run. A run whose input arrives but whose main
# loop never advances reports 3, which is what the BizHawk replay did.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAS="$ROOT/tools/tas"

MOVIE=""; ROM=""; OUTDIR=""; KEEP=0; QUIET=0
usage() { sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --movie)  MOVIE="$2"; shift 2 ;;
    --rom)    ROM="$2"; shift 2 ;;
    --outdir) OUTDIR="$2"; shift 2 ;;
    --keep)   KEEP=1; shift ;;
    --quiet)  QUIET=1; shift ;;
    -h|--help) usage ;;
    *) echo "run.sh: unknown argument $1" >&2; usage ;;
  esac
done
[ -n "$MOVIE" ] && [ -n "$ROM" ] || usage

command -v fceux >/dev/null 2>&1 || {
  echo "run.sh: FAIL (4) -- fceux is not installed. On Arch: sudo pacman -S fceux" >&2
  exit 4; }

OUTDIR="${OUTDIR:-$(mktemp -d /tmp/opencode/tasrun-XXXXXX)}"
mkdir -p "$OUTDIR/shots" || exit 4
say() { [ "$QUIET" = 1 ] || echo "$@"; }

# ---------------------------------------------------------------- identity
say "run.sh: identity gate"
if ! python3 "$TAS/identity.py" --movie "$MOVIE" --rom "$ROM" --out "$OUTDIR"; then
  echo "run.sh: FAIL (2) -- the movie and this ROM are not a pair." >&2
  exit 2
fi

# ------------------------------------------------------- isolate the run
# The release cartridge has its battery bit set, so FCEUX will load a <rom>.sav
# if one is next to the ROM. Replaying a TAS on top of a battery save is a
# plausible-looking run of the wrong starting state, so the run gets its own copy
# in its own directory and its own HOME. Nothing under the user's ROM collection
# or FCEUX config is read or written.
RUN="$OUTDIR/run"; rm -rf "$RUN"; mkdir -p "$RUN/home"
cp "$ROM" "$RUN/cart.nes" || { echo "run.sh: cannot stage the ROM" >&2; exit 4; }
rm -f "$RUN/cart.sav"
HOME="$RUN/home"; export HOME

export OUT_DIR="$OUTDIR"
export FRAMES_FILE="$OUTDIR/inputs.txt"
export WATCH_FILE="$OUTDIR/watch.txt"
export SHOT_FRAMES="$(cat "$OUTDIR/shots.txt")"

# A stray FCEUX from an earlier attempt would still be emulating and would
# fight for the display; say so rather than racing it.
if pgrep -x fceux >/dev/null 2>&1; then
  echo "run.sh: note -- another fceux is already running; leaving it alone." >&2
fi

# FCEUX has no headless mode in the Arch package (Qt's offscreen plugin
# segfaults on QOpenGLWidget), so it needs the display. --no-config keeps it from
# persisting settings; HOME is already redirected.
LAUNCH=(fceux --no-config 1 --sound 0
        --playmov "$MOVIE"
        --loadlua "$TAS/drive.lua"
        "$RUN/cart.nes")

# Compile-check the driver before launching a GUI, the way replay.sh does: FCEUX
# reports a Lua parse error as a mono stacktrace plus a half-written summary,
# which is a far worse way to learn about a missing `end`.
if command -v luac >/dev/null 2>&1; then
  if ! luac -p "$TAS/drive.lua" 2>"$OUTDIR/luac.txt"; then
    echo "run.sh: FAIL (4) -- drive.lua does not parse:" >&2
    sed 's/^/  /' "$OUTDIR/luac.txt" >&2; exit 4
  fi
  say "run.sh: drive.lua parses"
fi

# 44003 frames at the ~660 fps this runs at is ~70s; the ceiling is generous but
# finite, because an unbounded wait is how a hung run reads as a pass.
TIMEOUT="${TIMEOUT:-900}"
say "run.sh: playing ${SHOT_FRAMES:+}the movie in FCEUX (timeout ${TIMEOUT}s)"
timeout -s KILL "$TIMEOUT" "${LAUNCH[@]}" >"$OUTDIR/fceux.log" 2>&1
rc=$?

# ------------------------------------------------------------------ judge
# The emulator dying and the movie failing are different failures. FCEUX exiting
# nonzero after a complete run is not itself a verdict; the summary is.
SUMMARY="$OUTDIR/summary.txt"
if [ ! -s "$SUMMARY" ]; then
  echo "run.sh: FAIL (4) -- FCEUX produced no summary (exit $rc). Tail of its log:" >&2
  tail -15 "$OUTDIR/fceux.log" | sed 's/^/  /' >&2
  exit 4
fi

# A summary with no `verdict` line means the driver never reached its own
# conclusion: FCEUX died part-way through, and the most common reason is that
# somebody closed the window. That is an aborted run, not a movie that failed to
# verify, and reporting it as "the movie did not verify" would put a false claim
# about the TAS into the world.
if ! grep -q '^verdict ' "$SUMMARY"; then
  got=$(grep -c '^[0-9]' "$OUTDIR/series.tsv" 2>/dev/null) || true
  case "$got" in ''|*[!0-9]*) got=0 ;; esac
  echo "run.sh: FAIL (4) -- the run was interrupted after $got frames; FCEUX exited" \
       "before the driver reached a verdict (exit $rc)." >&2
  echo "           If the FCEUX window was closed, that is this. Leave it alone:" >&2
  echo "           the Arch package has no headless mode, so the window IS the run." >&2
  tail -5 "$OUTDIR/fceux.log" | sed 's/^/  /' >&2
  exit 4
fi

say ""
say "run.sh: summary"
sed 's/^/  /' "$SUMMARY"
say ""

if ! grep -q '^verdict ok$' "$SUMMARY"; then
  echo "run.sh: FAIL (3) -- the movie did not verify." >&2
  grep -E '^FAIL ' "$SUMMARY" | sed 's/^/  /' >&2
  say "run.sh: series in $OUTDIR/series.tsv, screenshots in $OUTDIR/shots"
  exit 3
fi

# Independent confirmation that the run covered the movie, counted here rather
# than trusted: series.tsv must have a line for at least every movie frame.
# movie.framecount() is NOT usable for this -- in FCEUX 2.6.6 it counts frames
# elapsed and runs past movie.length(), so it reads 44010 for a 44003-frame movie
# and comparing it against the movie length makes the check unfalsifiable.
want=$(grep -c '^' "$OUTDIR/inputs.txt" 2>/dev/null) || true
case "$want" in ''|*[!0-9]*) want=0 ;; esac
rows=$(grep -c '^[0-9]' "$OUTDIR/series.tsv" 2>/dev/null) || true
case "$rows" in ''|*[!0-9]*) rows=0 ;; esac
say "run.sh: covered $want of $want movie frames over $rows recorded frames"
if [ "$rows" -lt "$want" ]; then
  echo "run.sh: FAIL (3) -- summary said ok but only $rows of $want movie frames were recorded." >&2
  exit 3
fi

say "run.sh: OK -- the movie ran to its last frame."

# FCEUX's gui.savescreenshot ignores the path it is handed and writes
# $HOME/.fceux/snaps/<romstem>-<n>.png, counting n from 0. HOME is redirected, so
# that location is predictable and the staged ROM's stem is known -- but relying on
# either is how a run ends up reporting screenshots that do not exist. Collect by
# the map drive.lua recorded, and say so if the count does not line up.
SNAPS="$RUN/home/.fceux/snaps"
if [ -s "$OUTDIR/shots.map" ]; then
  collected=0; missing=0
  while IFS=$'\t' read -r n frame iter; do
    src="$SNAPS/cart-$n.png"
    if [ -f "$src" ]; then
      cp "$src" "$OUTDIR/shots/frame-$(printf '%06d' "$frame").png"
      collected=$((collected + 1))
    else
      missing=$((missing + 1))
    fi
  done < "$OUTDIR/shots.map"
  say "run.sh: collected $collected screenshot(s) into $OUTDIR/shots"
  [ "$missing" -gt 0 ] && say "run.sh: WARNING -- $missing screenshot(s) FCEUX claimed but did not write"
fi

say "run.sh: series     $OUTDIR/series.tsv"
say "run.sh: screenshots $OUTDIR/shots"
say "run.sh: subtitles  $OUTDIR/subtitles.srt"
[ "$KEEP" = 1 ] || say "run.sh: (outdir kept at $OUTDIR)"
exit 0