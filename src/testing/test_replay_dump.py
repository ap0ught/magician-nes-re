"""replay_dump.lua frame-count header.

    python3 src/testing/test_replay_dump.py

BizHawk.load_inputs replays every input line when a log has no `# frames=`
header, and refuses a header that does not match the line count. The capture
script used to leave nframes at 0 in the no-header case, run no inputs, and
still write the dump and `done`. A log with no frames must not do that.
"""

import os
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SCRIPT = ROOT / "tools" / "capture" / "replay_dump.lua"

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


text = SCRIPT.read_text(encoding="utf-8")
fail_at = text.find('w("FAIL no frames to replay")')
dump_at = text.find('wr("chrfull.bin"')
check("a zero-frame log is rejected before the capture files are written",
      0 < fail_at < dump_at, f"fail={fail_at} dump={dump_at}")
check("a missing header takes the frame count from the input lines",
      "if not declared then" in text and "nframes = #lines" in text)


def run_lua(log: str, out: Path) -> str:
    out.mkdir(parents=True, exist_ok=True)
    src = out / "in.txt"
    src.write_text(log, encoding="utf-8")
    wrapper = r'''
local OUT = os.getenv("OUT")
local INPUT = os.getenv("INPUT")
local orig_getenv = os.getenv
os.getenv = function(k)
  if k == "MAGICIAN_CAPTURE_OUT" then return OUT end
  if k == "MAGICIAN_REPLAY_INPUT" then return INPUT end
  return orig_getenv(k)
end
local fc = 0
emu = {
  framecount = function() return fc end,
  frameadvance = function() fc = fc + 1 end,
}
client = {
  exit = function() end,
  screenshot = function() end,
  sleep = function() end,
}
joypad = { set = function() end }
memory = {
  usememorydomain = function() return true end,
  read_bytes_as_binary_string = function(addr, len) return string.rep("\0", len) end,
  readbyte = function() return 0 end,
}
dofile(os.getenv("SCRIPT"))
'''
    env = dict(os.environ)
    env.update(OUT=str(out), INPUT=str(src), SCRIPT=str(SCRIPT))
    proc = subprocess.run(["lua", "-e", wrapper], capture_output=True, text=True,
                          env=env, timeout=30)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    replay = out / "replay.txt"
    return replay.read_text(encoding="utf-8") if replay.is_file() else ""


with tempfile.TemporaryDirectory(prefix="replay-dump-") as tmp:
    base = Path(tmp)
    no_header = run_lua("A\n-\n", base / "nohdr")
    check("no header replays every input line",
          "frames=2 input_lines=2" in no_header and "\ndone\n" in no_header + "\n",
          no_header)
    check("no header still writes the capture",
          (base / "nohdr" / "chrfull.bin").is_file())
    zero = run_lua("# a comment only\n", base / "zero")
    check("no header and no lines is not a successful capture",
          "FAIL no frames" in zero and "done" not in zero.splitlines(),
          zero)
    check("that rejection writes no chrfull.bin",
          not (base / "zero" / "chrfull.bin").exists())
    declared_zero = run_lua("# frames=0\n", base / "decl0")
    check("a header of zero frames is not a successful capture",
          "FAIL no frames" in declared_zero and "done" not in declared_zero.splitlines(),
          declared_zero)
    mismatch = run_lua("# frames=1\nA\n-\n", base / "bad")
    check("a mismatched header is still rejected before the dump",
          "FAIL frame count" in mismatch
          and not (base / "bad" / "chrfull.bin").exists(),
          mismatch)

print(f"\n{ok} checks passed")
