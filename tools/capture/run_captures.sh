#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ROM="/home/cmayfield/Downloads/Games/Prototypes/Magician-NES/Magician (USA) (Beta 1) (1990-03-02).nes"
BASE="/tmp/opencode/captures"
mkdir -p "$BASE"

run_one() {
  local name="$1"; local frames="$2"; local presses="$3"
  local out="$BASE/$name"
  mkdir -p "$out"
  echo "=== $name (frames=$frames presses=$presses) ==="
  MAGICIAN_CAPTURE_OUT="$out" MAGICIAN_CAPTURE_FRAMES="$frames" MAGICIAN_CAPTURE_PRESSES="$presses" \
    MAGICIAN_EXPECT="chrfull.bin" MAGICIAN_SETTLE=60 \
    DISPLAY=:0 "$ROOT/tools/bizhawk/run.sh" "$ROOT/tools/capture/capture.lua" "$ROM" 2>&1 | tail -3
}

run_one "01_boot"    5   ""
run_one "02_title"   60  ""
run_one "03_post_start" 120 "60:START"
run_one "04_post_start2" 200 "60:START,120:START"
echo "DONE"
