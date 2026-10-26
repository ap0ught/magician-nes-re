#!/usr/bin/env bash
# Load this project's KWin script, tile this project's EmuHawk windows, unload.
#
#   tools/bizhawk/tile.sh once        arrange them now, then unload (the usual one)
#   tools/bizhawk/tile.sh up          arrange them and hold, until Ctrl-C
#   tools/bizhawk/tile.sh down        unload if this script is somehow still loaded
#   tools/bizhawk/tile.sh status      is it loaded? what display?
#   tools/bizhawk/tile.sh selftest    prove the KWin round trip, with no windows
#
# Any of `once`/`up`/`hold` take `--no-kwin` (place them, skip the second
# opinion), plus anything x11_tile.py accepts: --screen, --desktop, --dry-run,
# --max-scale, --no-verify.
#
# THE WALL IS OPT-IN: MAGICIAN_WALL=1
# ------------------------------------
# Without it this script refuses and says why. The default for this project is
# an INVISIBLE emulator: every number it produces comes from
# `client.screenshot()` and from memory-domain reads through BizHawk's Lua
# bridge, neither of which needs a window -- measured, in the header of
# tools/bizhawk/display.sh: BizHawk runs on the nested display with NO window
# manager at all and the work-RAM fingerprint is byte-identical to the one taken
# with a decorated window on :0.
#
# So a window that nobody asked for is pure cost, and on a machine shared with
# other projects it is worse than cost: it is a window somebody has to close by
# hand. MAGICIAN_WALL=1 is the deliberate request for a visible wall; see
# tools/bizhawk/kwin-tile.js for the long version of this argument.
#
# WHY THE SPLIT BETWEEN KWIN AND X11
# ----------------------------------
# The brief asked for KWin scripting, and this script does use it -- to enumerate
# the session's windows and to report the output geometry. It cannot be used to
# PLACE them, and that is measured rather than assumed. On kwin 6.7.5 the
# per-`Client` scripting API has no `move()` and no `resize()`; `x/y/width/height`
# are read-only; and `frameGeometry = {...}` is swallowed and then read back as
# the value written while the X server says the window never moved. The full
# transcript is in the header of tools/bizhawk/kwin-tile.js.
#
# So placement is tools/bizhawk/x11_tile.py over X11, and the KWin report is
# kept as an INDEPENDENT second opinion on which windows exist and how big the
# usable area is. Two subsystems, two answers: agreement is evidence, and
# disagreement is printed rather than resolved.
#
# ...but only if they are looking at the SAME DISPLAY, and they usually are not.
# KWin answers over the session bus, so it always describes the session's own
# compositor; `DISPLAY` names whatever X server the windows are on, and
# display.sh runs this project's emulator on a nested Xephyr at :2 that has no
# window manager on it at all. MEASURED with no emulator running: `DISPLAY=:2`
# printed "KWin and X11 both see 0 window(s)" -- KWin counting the :0 session's
# 9 windows, X11 counting :2's zero. An agreement between two different window
# sets, reported as evidence. So `cross_check` compares the two geometries FIRST
# and refuses when they differ, and `--no-kwin` is the way to say "place them,
# skip the second opinion" on a display KWin cannot describe.
#
# CLEANUP IS THE POINT OF THIS SCRIPT
# ------------------------------------
# A KWin script that is loaded and never unloaded stays loaded: KWin holds a
# reference and keeps calling into it for the rest of the session. Every exit
# path here unloads, including SIGINT, SIGTERM and a failure in the middle --
# `trap ... EXIT INT TERM`, with `trap - EXIT` inside the handler so the handler
# cannot re-enter itself. After unloading, `isScriptLoaded` is asked again, and
# if KWin still claims the script is there the exit status says so. A cleanup
# that fails silently is the same class of bug as the incident this whole file
# family exists to prevent, so it is checked rather than assumed.
#
# EXIT CODES
#   0  done (and, if a script was loaded, it is provably unloaded again)
#   2  usage, or MAGICIAN_WALL was not set
#   3  KWin scripting is unavailable, and the command needed it
#   4  the tiler failed (windows did not end up where the plan said)
#   5  cleanup failed: KWin still holds the script after unloadScript
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KWIN_JS="$ROOT/tools/bizhawk/kwin-tile.js"
X11_TILE="$ROOT/tools/bizhawk/x11_tile.py"
# The plugin name is this project's, so `down` can tell our script from anyone
# else's and `unloadScript` cannot be pointed at a stranger's.
PLUGIN="${MAGICIAN_TILE_PLUGIN:-magicianTile}"

# The service is `org.kde.KWin` and the adaptor is on the object path
# `/Scripting`. The brief named the service `org.kde.kwin.Scripting`, which is
# not registered on this machine: `qdbus6 org.kde.kwin.Scripting /Scripting`
# answers "Service does not exist".
SERVICE="org.kde.KWin"
OBJECT="/Scripting"
IFACE="org.kde.kwin.Scripting"

QDBUS="${MAGICIAN_QDBUS:-qdbus6}"
LOADED=0
HOLD=0

say()  { printf 'tile.sh: %s\n' "$*"; }
warn() { printf 'tile.sh: %s\n' "$*" >&2; }
die()  { warn "$1"; exit "${2:-2}"; }

kwin_call() {
  "$QDBUS" "$SERVICE" "$OBJECT" "$IFACE.$1" "${@:2}" 2>&1
}

kwin_available() {
  command -v "$QDBUS" >/dev/null 2>&1 || return 1
  kwin_call isScriptLoaded "$PLUGIN" >/dev/null 2>&1
}

is_loaded() {
  [ "$(kwin_call isScriptLoaded "$PLUGIN")" = "true" ]
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [ "$LOADED" = "1" ]; then
    # A load that KWin answered with an id is the only reason to unload; if the
    # load failed, unloading by name could hit a script that is not ours.
    kwin_call unloadScript "$PLUGIN" >/dev/null 2>&1 || true
    sleep 0.3
    if is_loaded; then
      warn "CLEANUP FAILED -- KWin still reports '$PLUGIN' as loaded."
      warn "  It will keep calling into it for the rest of your session. Unload it"
      warn "  by hand with:"
      warn "    $QDBUS $SERVICE $OBJECT $IFACE.unloadScript $PLUGIN"
      rc=5
    else
      say "cleaned up: KWin is no longer holding '$PLUGIN'"
    fi
    LOADED=0
  fi
  exit "$rc"
}

# Only register the trap once something has been loaded, so an early usage error
# does not run a handler that has nothing to do. `load_kwin` sets LOADED and
# installs the trap in the same breath: a load that succeeds and a shell that
# dies between the two is exactly the leak this file is about.
load_kwin() {
  if ! kwin_available; then
    return 1
  fi
  # A previous run of this script that was killed hard could have left ours
  # loaded. Clear it before loading again, so `loadScript` cannot accumulate
  # copies and so the state we find is the state we created.
  if is_loaded; then
    say "a previous '$PLUGIN' is still loaded; unloading it first"
    kwin_call unloadScript "$PLUGIN" >/dev/null 2>&1 || true
    sleep 0.3
  fi
  local id
  id="$(kwin_call loadScript "$KWIN_JS" "$PLUGIN")"
  case "$id" in
    ''|*[!0-9]*)
      warn "loadScript did not return an id (got: ${id:-nothing})."
      return 1
      ;;
  esac
  LOADED=1
  trap cleanup EXIT INT TERM
  # `start()` is called for its side effect only: this KWin does not call a
  # loaded script's `start()` (measured -- see kwin-tile.js), so the script does
  # its work at load time. Calling it is still right, because a KWin that does
  # call it should get the report, and a duplicate report is inert.
  kwin_call start >/dev/null 2>&1 || true
  say "loaded $KWIN_JS as '$PLUGIN' (id $id)"
  return 0
}

kwin_report() {
  # One report block, not three. The script runs `main()` at load time AND
  # defines `start()` (see kwin-tile.js: this KWin calls neither reliably enough
  # to rely on, so both are kept), so the same lines can arrive two or three
  # times. Every block begins with the `screen` line, so keeping everything after
  # the LAST one leaves exactly one report -- and `tail -1` on each line type
  # downstream would have produced the same numbers while hiding the duplication,
  # which is the kind of tidy output that makes a reader believe something that
  # is not what happened.
  journalctl --user -u plasma-kwin_wayland --no-pager --since '-30s' 2>/dev/null \
    | grep -o 'magician-tile-v1.*' \
    | awk '/magician-tile-v1 screen/{buf=""} {buf = buf $0 "\n"} END{printf "%s", buf}'
}

# Cross-check the two subsystems. Returns 1 on disagreement, and says what.
cross_check() {
  local all="$1" report kwin_n x11_n
  # The `screen` line and the `done` line are BOTH needed and they are separate
  # lines, so the narrowing to `done` has to come after the geometry is read out.
  # Getting that order wrong is not hypothetical: the first version of this
  # function narrowed first and then looked for `screen` in a variable that no
  # longer contained one, so `kwin_geom` came back empty, the empty-compare guard
  # `[ -n "$kwin_geom" ]` let it through, and `DISPLAY=:2` still reported a clean
  # agreement. A guard that treats "I could not measure it" as "they match" is
  # the same bug as the one this whole file family exists to prevent.
  local kwin_geom x11_geom
  kwin_geom="$(sed -n 's/.*screen name=[^ ]* .*w=\([0-9]*\) h=\([0-9]*\).*/\1x\2/p' \
               <<<"$(printf '%s\n' "$all" | grep 'magician-tile-v1 screen' | tail -1)")"
  x11_geom="${X11_DISPLAY##* }"

  report="$(printf '%s\n' "$all" | grep 'magician-tile-v1 done' | tail -1)"
  if [ -z "$report" ]; then
    warn "KWin reported nothing (no 'magician-tile-v1 done' line in the journal)."
    warn "  That is not a pass: the KWin half of this tool could not be checked."
    return 1
  fi

  # ARE THE TWO HALVES EVEN LOOKING AT THE SAME DISPLAY?
  #
  # KWin answers over the session bus, so it always describes the session's own
  # compositor. `DISPLAY` may name something else -- and it usually does here:
  # `display.sh` runs this project's emulator on a nested Xephyr at :2 with no
  # window manager on it at all, while KWin is the :0 compositor. Comparing window
  # counts across two different window sets is not evidence, and when both counts
  # happen to be 0 it reads as a clean agreement.
  #
  # MEASURED before this check existed: `DISPLAY=:2 tile.sh once` with no
  # emulator running printed "cross-check: KWin and X11 both see 0 window(s)"
  # -- KWin counting the :0 session's 9 windows, X11 counting :2's 0. Same trap,
  # reported as evidence. So the geometry is compared first, and a mismatch is
  # said out loud instead of being averaged into a verdict.
  #
  # A MISSING geometry is a refusal too, not a pass. If either side could not
  # report its size, this function has no basis for calling them consistent.
  if [ -z "$kwin_geom" ] || [ -z "$x11_geom" ]; then
    warn "CANNOT CROSS-CHECK -- one side did not report its geometry."
    warn "  KWin: ${kwin_geom:-<no 'screen' line in the report>}"
    warn "  X11 : ${x11_geom:-<no 'display ... size=' line from x11_tile.py>}"
    warn "  A cross-check with a missing operand is not an agreement. Use"
    warn "  --no-kwin to place the windows on the X11 half alone."
    return 1
  fi
  if [ "$kwin_geom" != "$x11_geom" ]; then
    warn "THE TWO HALVES ARE LOOKING AT DIFFERENT DISPLAYS -- not a cross-check."
    warn "  KWin is reporting the session compositor at ${kwin_geom}, because it"
    warn "  answers over the session bus and cannot be pointed elsewhere."
    warn "  X11 is reporting DISPLAY=${X11_DISPLAY%% *} at ${x11_geom}."
    warn "  Two window counts from two different window sets agree for no reason."
    warn "  To cross-check, run this on the session's own display (DISPLAY=:0), or"
    warn "  accept the X11 half alone with --no-kwin (the placement is still"
    warn "  verified by x11_tile.py's own read-back)."
    return 1
  fi

  kwin_n="$(sed -n 's/.*matched=\([0-9]*\).*/\1/p' <<<"$report")"
  x11_n="$(printf '%s\n' "$X11_N")"

  if [ "$kwin_n" != "$x11_n" ]; then
    warn "DISAGREEMENT -- KWin matched $kwin_n window(s), X11 found $x11_n."
    warn "  Two subsystems counting windows differently means at least one is"
    warn "  wrong, and a tiler built on the wrong one moves the wrong windows."
    return 1
  fi
  say "cross-check: KWin and X11 both see $kwin_n window(s)"
  kwin_report_screen
  return 0
}

kwin_report_screen() {
  local report
  report="$(kwin_report | grep 'magician-tile-v1 screen' | tail -1)"
  [ -n "$report" ] && say "KWin says the usable output is: ${report#magician-tile-v1 screen }"
  return 0
}

require_wall() {
  if [ "${MAGICIAN_WALL:-0}" != "1" ]; then
    die "refusing: the emulator wall is opt-in and MAGICIAN_WALL is not 1.
  The default for this project is an INVISIBLE emulator, because every
  measurement comes from client.screenshot() and from Lua memory reads and
  neither needs a window -- see the header of tools/bizhawk/kwin-tile.js.
  If you want to watch the windows:
      MAGICIAN_WALL=1 $0 once" 2
  fi
}

X11_N=""
X11_DISPLAY=""

run_x11() {
  [ -f "$X11_TILE" ] || die "$X11_TILE is missing" 4
  local out rc
  out="$(python3 "$X11_TILE" "$@" 2>&1)"
  rc=$?
  printf '%s\n' "$out" | sed 's/^/  /'
  X11_N="$(printf '%s\n' "$out" | sed -n 's/.*-> \([0-9]*\) window(s).*/\1/p' | tail -1)"
  [ -n "$X11_N" ] || X11_N=0
  X11_DISPLAY="$(printf '%s\n' "$out" \
                 | sed -n 's/^x11_tile: display DISPLAY=\([^ ]*\) size=\([0-9]*\)x\([0-9]*\).*/\1 \2x\3/p' \
                 | tail -1)"
  return "$rc"
}

cmd_selftest() {
  # Proves the KWin round trip and the cleanup guarantee, with no emulator and no
  # windows. A tiler that cannot be shown to clean up after itself is not a
  # tiler, so this is a real check and it fails loudly rather than reporting OK
  # when KWin is not there to answer.
  say "selftest: needs a live KWin; no emulator, no windows, no cartridge"
  kwin_available || die "KWin scripting is not answering on $SERVICE$OBJECT.
  Is this a Wayland/X11 session with KWin running? ($QDBUS present: $(command -v "$QDBUS" || echo no))" 3
  is_loaded && die "'$PLUGIN' was ALREADY loaded before this selftest.
  Something is holding it; unload it first so this measures only its own work." 3
  load_kwin || die "loadScript failed for $KWIN_JS" 3
  is_loaded || die "loadScript returned an id but isScriptLoaded says false." 3
  sleep 1
  local report
  report="$(kwin_report)"
  if ! printf '%s\n' "$report" | grep -q 'magician-tile-v1 done'; then
    warn "the script loaded but printed no report:"
    printf '%s\n' "$report" | sed 's/^/  /'
    die "no 'magician-tile-v1 done' line in journalctl --user -u plasma-kwin_wayland" 3
  fi
  printf '%s\n' "$report" | sed 's/^/  /'
  # Hand over to the EXIT trap and let it prove the unload.
  say "selftest: now unloading via the EXIT handler, which must also prove it"
  return 0
}

cmd_down() {
  kwin_available || die "KWin scripting is not answering on $SERVICE$OBJECT" 3
  if is_loaded; then
    kwin_call unloadScript "$PLUGIN" >/dev/null 2>&1 || true
    sleep 0.3
    if is_loaded; then die "unloadScript did not take effect for '$PLUGIN'" 5; fi
    say "unloaded '$PLUGIN'"
  else
    say "'$PLUGIN' is not loaded; KWin holds nothing of ours"
  fi
  return 0
}

cmd_status() {
  say "display    : ${DISPLAY:-<unset>}   (windows here are moved over X11)"
  say "kwin script: $KWIN_JS"
  say "plugin     : $PLUGIN"
  if ! kwin_available; then
    say "kwin       : NOT AVAILABLE ($QDBUS / $SERVICE$OBJECT)"
    say "wall opt-in: MAGICIAN_WALL=${MAGICIAN_WALL:-0}"
    return 3
  fi
  say "kwin       : available"
  say "loaded     : $(is_loaded && echo yes || echo no)"
  say "wall opt-in: MAGICIAN_WALL=${MAGICIAN_WALL:-0}"
  return 0
}

cmd_tile() {
  local hold="$1"; shift
  require_wall "$hold"
  # `--no-kwin` is consumed HERE, not passed down, so it never reaches
  # x11_tile.py's argparse. It exists because `cross_check` can now refuse: KWin
  # describes the session compositor and `DISPLAY` may not be it, and on this
  # machine it usually is not (the emulator lives on a nested Xephyr at :2).
  # Skipping the KWin half keeps the placement, which x11_tile.py verifies against
  # the X server independently, and drops only the second opinion.
  local no_kwin=0
  local rest=()
  local a
  for a in "$@"; do
    case "$a" in
      --no-kwin) no_kwin=1 ;;
      *) rest+=("$a") ;;
    esac
  done
  set -- ${rest[@]+"${rest[@]}"}

  local kwin_ok=0 report=""
  if [ "$no_kwin" = "1" ]; then
    say "--no-kwin: not loading the KWin script, so there is no second opinion"
    say "  and nothing to unload. Placement is still verified against the X"
    say "  server by x11_tile.py's own read-back."
  elif load_kwin; then
    kwin_ok=1
    sleep 1
    report="$(kwin_report)"
    printf '%s\n' "$report" | sed 's/^/  /'
  else
    warn "KWin scripting is unavailable, so the KWin half of this tool is not"
    warn "  being exercised. The X11 half below still places and verifies the"
    warn "  windows; the cross-check between the two subsystems will be skipped."
  fi

  run_x11 "$@"
  local xrc=$?
  if [ "$xrc" -ne 0 ]; then
    die "the tiler failed (exit $xrc); see its output above." 4
  fi

  if [ "$kwin_ok" = "1" ]; then
    cross_check "$report" || die "the two subsystems disagree; refusing to call
  this a success. Nothing else has been changed, and the KWin script is being
  unloaded by the EXIT handler." 4
  fi

  if [ "$hold" != "0" ]; then
    say "holding the wall open; Ctrl-C (or TERM) unloads the KWin script."
    say "the windows are NOT closed by this script -- it does not own them."
    while :; do sleep 1; done
  fi
  say "done"
}

case "${1:-}" in
once)     shift; cmd_tile 0 "$@" ;;
up)       shift; cmd_tile 1 "$@" ;;
hold)     shift; H="$1"; shift; cmd_tile "$H" "$@" ;;
down)     cmd_down ;;
status)   cmd_status ;;
selftest) cmd_selftest ;;
*) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
esac
