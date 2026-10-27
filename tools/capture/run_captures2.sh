#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ROM="/home/cmayfield/Downloads/Games/Prototypes/Magician-NES/Magician (USA) (Beta 1) (1990-03-02).nes"
BASE="/tmp/opencode/captures2"
mkdir -p "$BASE"
run_one() {
  local name="$1"; local frames="$2"; local presses="$3"
  local out="$BASE/$name"
  mkdir -p "$out"
  echo "=== $name (frames=$frames presses=$presses) ==="
  MAGICIAN_CAPTURE_OUT="$out" MAGICIAN_CAPTURE_FRAMES="$frames" MAGICIAN_CAPTURE_PRESSES="$presses" \
    MAGICIAN_EXPECT="chrfull.bin" MAGICIAN_SETTLE=60 \
    DISPLAY=:0 "$ROOT/tools/bizhawk/run.sh" "$ROOT/tools/capture/capture.lua" "$ROM" 2>&1 | tail -1
}
# Try various START timings with a pulse (press for ~4 frames) at different lead-ins
run_one "t40"  120 "40:START"
run_one "t50"  120 "50:START"
run_one "t70"  120 "70:START"
run_one "t80"  120 "80:START"
run_one "t40_pulse" 120 "40:START,44:START,48:START"
echo "DONE"
