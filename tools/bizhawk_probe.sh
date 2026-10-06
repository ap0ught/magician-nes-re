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
#   * **CORRECTION.** This file used to say "there is no Xvfb, so frame dumping
#     yields nothing and a Lua RAM probe dies at client.cpu because the client
#     loop never starts. A window is the only way to see the game run." That was
#     wrong, and it cost two sessions: it is what pushed them into writing a
#     from-scratch emulator and produced two sessions of confidently wrong
#     measurements. BizHawk *is* drivable here, headfully on :0, with no Xvfb
#     installed and the X/Wayland session untouched. `EmuHawkMono.sh --help`
#     documents `--lua <path>` (which implies `--luaconsole`), and from inside
#     that script `emu.frameadvance()` is frame-exact, `client.screenshot(path)`
#     writes the core's own video buffer, `memory.*` reads the core's memory
#     domains, and `client.exit()` closes the session so the next launch is not
#     swallowed by the single-instance pipe. See tools/bizhawk/run.sh and
#     journal/05-bizhawk-is-the-instrument.md. What is genuinely missing is
#     *input injection*, not rendering: SDL2 takes the input and the harness
#     cannot press Start.
#   * Both ROM and script paths must be ABSOLUTE. `run.sh` cds into the BizHawk
#     directory, so a relative ROM path is not an error -- BizHawk fails to load
#     the file and falls back to NullHawk, whose Lua then dies on the first memory
#     domain call with "NullHawk does not implement memory domains". That reads
#     as "the ROM has no memory", not "the path was wrong".
#   * No xdotool or wmctrl, but python-xlib is present, so the windows are moved
#     directly over X11.
#   * ImageMagick 7's `import` rejects its own filename argument here; use
#     python-xlib + PIL (tools/side_by_side.py) for a screenshot.
#   * The cartridge has battery-backed PRG RAM. BizHawk resumes a stale save, so
#     a "working" session may not be a cold boot. Compare from cleared SRAM.
#   * The two windows have DIFFERENT titles -- BizHawk titles a window after the
#     ROM's file name, so they are "Magician [NES]" and "magician-rebuilt [NES]"
#     -- so tools/side_by_side.py can tell them apart by substring. They do not
#     both exist at once quickly: the second EmuHawk instance takes ~60 s to put
#     its window up (the first holds the single-instance lock), which is why
#     SETTLE is 60 rather than the 30 this used to be.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CART="${1:-$(python3 "$ROOT/tools/cartref.py")}"
ROM="${2:-$ROOT/asm/out/magician-rebuilt.nes}"

# WHERE THE EMULATOR IS. Sourced, so this script and tools/bizhawk/run.sh cannot
# each carry their own default and drift -- which is what they did, both pointing
# into another project's install. `set -e` is on here, so a refusal exits with
# bizpath.sh's own exit status and its reason already printed.
. "$ROOT/tools/bizhawk/bizpath.sh"
BIZ="$MAGICIAN_BIZHAWK_DIR"

[ -f "$ROM" ] || { echo "no rebuilt ROM at $ROM -- run 'make rom' first" >&2; exit 1; }

# ABSOLUTE, before anything launches. `EmuHawkMono.sh` does
# `cd "$(dirname "$(realpath "$0")")"`, so a relative ROM path resolves against
# BizHawk's own directory, the file never opens, and BizHawk comes up on a
# different core while this script goes on to report what it found. This is the
# same trap run.sh documents for the relative-path NullHawk fallback, and
# `make probe` had it: the rebuilt ROM arrived as the relative string
# `asm/out/magician-rebuilt.nes`, which `$BIZ/asm/out/` does not contain.
#
# The cartridge argument is absolute by default (tools/cartref.py prints an
# absolute path) but a caller may pass a relative one, so both are resolved.
ROM="$(readlink -f "$ROM")"
CART="$(readlink -f "$CART")"

export LD_LIBRARY_PATH="$BIZ/dll:$BIZ:/usr/lib"
export MONO_WINFORMS_XIM_STYLE=disabled

cfg=$(mktemp -d)
cp "$BIZ/config.ini" "$cfg/stock.ini"
cp "$BIZ/config.ini" "$cfg/rebuilt.ini"

launch() {  # launch <ini> <rom> <logfile>
  ( cd "$BIZ" && setsid ./EmuHawkMono.sh --gdi --config "$1" "$2" \
      </dev/null >"$3" 2>&1 & )
}

# The SaveRAM directory is this project's own now. The comment above used to say
# "only ever read, never written" over three lines that do `mkdir -p` and `rm -f`,
# and they did it inside ANOTHER PROJECT's install -- so `make probe` was writing
# to a directory two projects share, which is the whole reason they must not
# share one. The names are normalised too: `MAGICIAN_SRAM` is what run.sh calls
# it, `BIZHAWK_SRAM` is what this file used to call it, and two names for one
# setting is how `BIZHAWK`/`MAGICIAN_BIZHAWK` diverged in the first place.
SRAM="${MAGICIAN_SRAM:-${BIZHAWK_SRAM:-$BIZ/NES/SaveRAM}}"

echo "clearing $SRAM"
mkdir -p "$SRAM"
rm -f "$SRAM"/Magician.SaveRAM "$SRAM"/Magician.SaveRAM.bak \
      "$SRAM"/magician-rebuilt.SaveRAM

echo "launching cartridge  : $CART"
launch "$cfg/stock.ini" "$CART" "$cfg/stock.log"
echo "launching rebuilt    : $ROM"
launch "$cfg/rebuilt.ini" "$ROM" "$cfg/rebuilt.log"

sleep "${SETTLE:-60}"
python3 "$ROOT/tools/side_by_side.py" "$cfg/side.png" magician-rebuilt "Magician [NES]" || {
  echo "could not place the windows; check $cfg" >&2; exit 1; }
echo
echo "cartridge log: $(grep -i -m1 'BootGod entry found' "$cfg/stock.log" || echo 'no BootGod match')"
echo "rebuilt   log: $(grep -i -m1 'BootGod entry found' "$cfg/rebuilt.log" || echo 'no BootGod match')"
echo "screenshot: $cfg/side.png   (cartridge right, rebuilt left)"
echo "config kept in $cfg -- windows are live, close them when done"