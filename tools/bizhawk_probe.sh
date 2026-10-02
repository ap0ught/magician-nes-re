#!/usr/bin/env bash
# Open the cartridge and the rebuilt ROM side by side in BizHawk.
#
#   tools/bizhawk_probe.sh [cartridge.nes] [rebuilt.nes]
#
# Notes that cost time to find, so they are written down here:
#
#   * BizHawk's Mono build creates an OpenGLControl even under --chromeless, and
#     it dies on X11 BadMatch on this machine. --gdi (libgdiplus) avoids GL
#     entirely and works.
#   * There is no Xvfb, so frame dumping yields nothing and a Lua RAM probe dies
#     at client.cpu because the client loop never starts. A window is the only
#     way to see the game run.
#   * No xdotool or wmctrl, but python-xlib is present, so the windows are moved
#     directly over X11.
#   * ImageMagick 7's `import` rejects its own filename argument here; use
#     python-xlib + PIL (tools/side_by_side.py) for a screenshot.
#   * The cartridge has battery-backed PRG RAM. BizHawk resumes a stale save, so
#     a "working" session may not be a cold boot. Compare from cleared SRAM.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CART="${1:-/extdrive/backups/SHARE/roms/nes/Magician (USA).nes}"
ROM="${2:-$ROOT/asm/out/magician-rebuilt.nes}"
BIZ="${BIZHAWK:-$HOME/code/games/aibeatszelda/BizHawk-2.11.1-win-x64}"

[ -f "$ROM" ] || { echo "no rebuilt ROM at $ROM -- run 'make rom' first" >&2; exit 1; }
[ -d "$BIZ" ] || { echo "BizHawk not found at $BIZ; set BIZHAWK=/path" >&2; exit 1; }

export LD_LIBRARY_PATH="$BIZ/dll:$BIZ:/usr/lib"
export MONO_WINFORMS_XIM_STYLE=disabled

cfg=$(mktemp -d)
cp "$BIZ/config.ini" "$cfg/stock.ini"
cp "$BIZ/config.ini" "$cfg/rebuilt.ini"

launch() {  # launch <ini> <rom> <logfile>
  ( cd "$BIZ" && setsid ./EmuHawkMono.sh --gdi --config "$1" "$2" \
      </dev/null >"$3" 2>&1 & )
}

echo "launching cartridge  : $CART"
launch "$cfg/stock.ini" "$CART" "$cfg/stock.log"
echo "launching rebuilt    : $ROM"
launch "$cfg/rebuilt.ini" "$ROM" "$cfg/rebuilt.log"

sleep 30
python3 "$ROOT/tools/side_by_side.py" "$cfg/side.png" magician-rebuilt "Magician [NES]" || {
  echo "could not place the windows; check $cfg" >&2; exit 1; }
echo
echo "cartridge log: $(grep -i -m1 'BootGod entry found' "$cfg/stock.log" || echo 'no BootGod match')"
echo "rebuilt   log: $(grep -i -m1 'BootGod entry found' "$cfg/rebuilt.log" || echo 'no BootGod match')"
echo "screenshot: $cfg/side.png   (cartridge right, rebuilt left)"
echo "config kept in $cfg -- windows are live, close them when done"