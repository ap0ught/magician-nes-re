#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ROM="/home/cmayfield/Downloads/Games/Prototypes/Magician-NES/Magician (USA) (Beta 1) (1990-03-02).nes"
BASE="/tmp/opencode/captures"
mkdir -p "$BASE"
fails=0

run_one() {
  local name="$1"; local frames="$2"; local presses="$3"
  local out="$BASE/$name"
  mkdir -p "$out"
  echo "=== $name (frames=$frames presses=$presses) ==="
  # capture.lua writes into MAGICIAN_CAPTURE_OUT. run.sh checks MAGICIAN_EXPECT
  # as a literal path, so the expected file has to include $out.
  if ! MAGICIAN_CAPTURE_OUT="$out" MAGICIAN_CAPTURE_FRAMES="$frames" MAGICIAN_CAPTURE_PRESSES="$presses" \
    MAGICIAN_EXPECT="$out/chrfull.bin" MAGICIAN_SETTLE=60 \
    DISPLAY=:0 "$ROOT/tools/bizhawk/run.sh" "$ROOT/tools/capture/capture.lua" "$ROM" 2>&1 | tail -3
  then
    echo "FAIL: $name" >&2
    fails=$((fails + 1))
  fi
}

run_one "01_boot"    5   ""
run_one "02_title"   60  ""
run_one "03_post_start" 120 "60:Start"
run_one "04_post_start2" 200 "60:Start,120:Start"
if [ "$fails" -ne 0 ]; then
  echo "FAIL: $fails capture(s) failed" >&2
  exit 1
fi
echo "DONE"
