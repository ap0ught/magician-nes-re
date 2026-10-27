#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ROM="/home/cmayfield/Downloads/Games/Prototypes/Magician-NES/Magician (USA) (Beta 1) (1990-03-02).nes"
BASE="/tmp/opencode/captures2"
mkdir -p "$BASE"
fails=0
run_one() {
  local name="$1"; local frames="$2"; local presses="$3"
  local out="$BASE/$name"
  mkdir -p "$out"
  echo "=== $name (frames=$frames presses=$presses) ==="
  # run.sh treats any nonempty MAGICIAN_EXPECT as success, so a previous
  # chrfull.bin would pass a capture that never wrote one.
  if ! rm -f "$out/chrfull.bin"; then
    echo "FAIL: $name could not remove $out/chrfull.bin" >&2
    fails=$((fails + 1))
    return
  fi
  # capture.lua writes into MAGICIAN_CAPTURE_OUT. run.sh checks MAGICIAN_EXPECT
  # as a literal path, so the expected file has to include $out.
  if ! MAGICIAN_CAPTURE_OUT="$out" MAGICIAN_CAPTURE_FRAMES="$frames" MAGICIAN_CAPTURE_PRESSES="$presses" \
    MAGICIAN_EXPECT="$out/chrfull.bin" MAGICIAN_SETTLE=60 \
    DISPLAY=:0 "$ROOT/tools/bizhawk/run.sh" "$ROOT/tools/capture/capture.lua" "$ROM" 2>&1 | tail -1
  then
    echo "FAIL: $name" >&2
    fails=$((fails + 1))
  fi
}
# Try various Start timings with a pulse (press for ~4 frames) at different lead-ins.
# The joypad key is Start, not START (src/play/bridge.lua).
run_one "t40"  120 "40:Start"
run_one "t50"  120 "50:Start"
run_one "t70"  120 "70:Start"
run_one "t80"  120 "80:Start"
run_one "t40_pulse" 120 "40:Start,44:Start,48:Start"
if [ "$fails" -ne 0 ]; then
  echo "FAIL: $fails capture(s) failed" >&2
  exit 1
fi
echo "DONE"
