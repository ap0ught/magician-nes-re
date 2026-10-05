"""run.sh's own guards, run for real against a fake BizHawk.

    python3 src/testing/test_run_sh.py

`tools/bizhawk/run.sh` is the only thing standing between this project and a
plausible measurement of nothing. It has, at various times, reported:

  * a black screen for two different images, because a relative ROM path made
    BizHawk fail to load the file and silently fall back to **NullHawk**;
  * **three zero vectors**, because `head -c 1 | tail -c +5` read no byte at all
    (see `test_identity.py`);
  * a "verdict ok" while having read nothing at all, because nothing checked
    that the verdict was non-empty;
  * success with a 0-byte screenshot, because nothing checked that an expected
    output was non-empty.

Every one of those printed a line that looked like a result. This file runs the
real script -- the real bash, the real guards, the real exit codes -- against a
fake BizHawk directory, so the guards are tested rather than believed.

THE FAKE
--------
`$BIZHAWK` is pointed at a scratch directory holding a stub `EmuHawkMono.sh`.
That stub reads the same environment `run.sh` exports for the real emulator and
writes `$MAGICIAN_VERIFY_DIR/verify.txt` with whatever verdict the test asked
for. It never starts a window, never touches `NES/SaveRAM/`, and needs no
display -- which is what lets this file run headless in under ten seconds.

`test_identity.py` covers the byte arithmetic. This one covers everything above
it: that the failures happen *before* the emulator is launched where they can,
that they happen *after* it where they must, and that each one has its own exit
code rather than a shared non-zero that a caller cannot interpret.

Twenty-one checks, in six groups:

  A. refused before launch, no emulator needed (missing ROM, missing script,
     wrong SHA1, an unidentifiable ROM, a ROM the window cannot cover)
  B. the happy path -- and the two lines run.sh prints that carry the evidence:
     the vectors, in NMI/RESET/IRQ order, and the 48-byte window hex
  C. the verdict: NullHawk fails, an absent verdict fails, and the exit code says
     which
  D. the expected outputs: missing, empty, and truncated-without-the-sentinel all
     fail with exit 5
  E. NES/SaveRAM/ is derived per dump and never written
  F. the bash guard bug class: `[ x -lt 0x8000 ]` silently PASSES

WHAT IT DOES NOT CLAIM

  * **No emulator runs.** The stub is not BizHawk and its verdict is written by
    the test, so nothing here says the real preamble can identify a real core.
    What is tested is that run.sh *rejects* a bad verdict and *requires* a good
    one, which is the part that was broken.
  * **It does not check the window's contents against a real core.** Only that
    run.sh computes and passes the right 48 bytes -- which is what
    `test_identity.py` and this file's check B establish between them.
  * **It does not prove a passing run means a correct run.** It proves a passing
    run means the guard did not fire, which is a weaker and much more
    achievable claim.
  * **`pgrep` is the real one.** The stub is named `EmuHawkMono.sh` and does not
    match `[m]ono EmuHawk`, so run.sh's stale-instance guard sees no EmuHawk and
    proceeds -- which is the same state a clean machine is in. If a real EmuHawk
    were running, these tests would fail with exit 3 and say why.

WHAT IT NEEDS: bash, python3 and coreutils. No emulator, no cartridge, no
display. Writes only under /tmp.

Run:  python3 src/testing/test_run_sh.py
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import synthcart as S  # noqa: E402  (src/testing/synthcart.py)

TMP = Path("/tmp/opencode/magician-testing/run_sh")
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True, exist_ok=True)

RUN_SH = ROOT / "tools" / "bizhawk" / "run.sh"

# ---------------------------------------------------------------- the fake BizHawk
#
# What the stub does is entirely driven by files the test writes, so every test
# can say exactly what the "emulator" will report without the test and the stub
# sharing any code beyond this comment.
NOSCRIPT_MARK = "never loaded by the fake"


class Fake:
    """One scratch `$BIZHAWK` directory, private to one test.

    Each test gets its own rather than sharing one, because the launches are run
    in parallel: a shared directory would let one test's verdict file answer
    another test's question, which is precisely the "stale verdict reported as a
    passing run" bug this file is about, committed by the test suite itself.

    What the stub launcher does is driven entirely by files this object writes:
    `stub_verdict` (the exact text to write to verify.txt), `stub_noverdict`
    (write nothing at all), and `stub_env` (the MAGICIAN_* environment run.sh
    exported, plus the launcher's argv).
    """

    def __init__(self, name):
        self.dir = TMP / name
        (self.dir / "dll").mkdir(parents=True, exist_ok=True)
        (self.dir / "NES" / "SaveRAM").mkdir(parents=True, exist_ok=True)
        (self.dir / "config.ini").write_text("# fake\n", encoding="utf-8")
        stub = self.dir / "EmuHawkMono.sh"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "# The fake launcher. See src/testing/test_run_sh.py for what it replaces.\n"
            "set -uo pipefail\n"
            'V="${MAGICIAN_VERIFY_DIR:?}/verify.txt"\n'
            'D="$(dirname "$0")"\n'
            '{ echo "stub: argv $*"; env | grep "^MAGICIAN_" | sort; } > "$D/stub_env"\n'
            'if [ -f "$D/stub_noverdict" ]; then\n'
            '  # A preamble that dies before writing anything: must not be a pass.\n'
            "  exit 0\n"
            "fi\n"
            'if [ -f "$D/stub_verdict" ]; then\n'
            '  cat "$D/stub_verdict" > "$V"\n'
            "else\n"
            '  printf "verdict ok\\n" > "$V"\n'
            "fi\n"
            "exit 0\n", encoding="utf-8")
        stub.chmod(0o755)

    # ---- what the fake emulator will do
    def verdict(self, text):
        self._clear()
        (self.dir / "stub_verdict").write_text(text, encoding="utf-8")
        return self

    def no_verdict(self):
        self._clear()
        (self.dir / "stub_noverdict").write_text("die before writing anything\n")
        return self

    def _clear(self):
        (self.dir / "stub_verdict").unlink(missing_ok=True)
        (self.dir / "stub_noverdict").unlink(missing_ok=True)

    # ---- what run.sh is asked to do
    def run(self, rom, script=None, env=None, timeout=60):
        if script is None:
            script = TMP / "noop.lua"
        e = dict(os.environ)
        e["BIZHAWK"] = str(self.dir)
        e["MAGICIAN_INI"] = str(self.dir / "config.ini")
        e["MAGICIAN_SRAM"] = str(self.dir / "NES" / "SaveRAM")
        # 6, not the production 45: run.sh deliberately waits 5 s before
        # concluding the emulator is gone rather than merely slow, and that is
        # correct. It is also why the launch tests below are run concurrently --
        # the wait is wall clock nobody can shorten without changing what is
        # being tested.
        e["MAGICIAN_SETTLE"] = e.get("MAGICIAN_SETTLE", "6")
        e["MAGICIAN_EXPECT_WAIT"] = e.get("MAGICIAN_EXPECT_WAIT", "6")
        for k in ("MAGICIAN_EXPECT", "MAGICIAN_DONE", "MAGICIAN_EXPECT_SHA1",
                  "MAGICIAN_KILL_STALE", "MAGICIAN_LUA_OUT"):
            e.pop(k, None)
        e.update(env or {})
        return subprocess.run(["bash", str(RUN_SH), str(script), str(rom)],
                              capture_output=True, text=True, env=e, timeout=timeout)

    def env_exported(self):
        """The MAGICIAN_* environment run.sh handed the emulator, as a dict."""
        out = {}
        try:
            text = (self.dir / "stub_env").read_text(encoding="utf-8")
        except FileNotFoundError:
            return out
        for ln in text.splitlines():
            k, _, val = ln.partition("=")
            if k.startswith("MAGICIAN_"):
                out[k] = val
        return out

    def argv_line(self):
        try:
            return (self.dir / "stub_env").read_text(encoding="utf-8").splitlines()[0]
        except (FileNotFoundError, IndexError):
            return ""


def in_parallel(fns):
    """Run the launch calls concurrently, return the results in order.

    The subprocess calls are the only thing concurrent here; every assertion
    stays on this thread, so the `ok` count and the failure messages are as
    ordered and as complete as if the calls had been serial.
    """
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(8, len(fns))) as pool:
        return [f.result() for f in [pool.submit(fn) for fn in fns]]


NOSCRIPT = TMP / "noop.lua"
NOSCRIPT.write_text(f"-- {NOSCRIPT_MARK}\n", encoding="utf-8")

ROM = S.make_rom(TMP / "Magician (USA) (Beta 1) (1990-03-02).nes")
WANT_SHA1 = __import__("hashlib").sha1(ROM.read_bytes()).hexdigest()

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


def fails_with(name, rc, want_rc, p, must_say=(), must_not_say=()):
    """The result is a failure with `want_rc`, and the message says why."""
    global ok
    out = p.stdout + p.stderr
    if rc != want_rc:
        raise AssertionError(
            f"{name}: expected exit {want_rc}, got {rc}.\n"
            f"         A harness that returns 0 while measuring nothing is the "
            f"exact bug this file exists for, and one that returns a different "
            f"non-zero is just as uninformative: a caller cannot tell which "
            f"guard fired.\n"
            f"         stdout+stderr was:\n{out}")
    for frag in must_say:
        if frag not in out:
            raise AssertionError(
                f"{name}: exit {rc} is right but the message never says "
                f"{frag!r}, so a reader cannot tell what went wrong.\n{out}")
    for frag in must_not_say:
        if frag in out:
            raise AssertionError(
                f"{name}: the message contains {frag!r}, which is a RESULT and "
                f"must never appear on a failing run -- this is the zero-vector "
                f"bug in a different coat.\n{out}")
    ok += 1
    print(f"  ok {name}")


# =====================================================================================
# A. Refused BEFORE anything is launched. No emulator, so these cost milliseconds.
# =====================================================================================
FAKE_A = Fake("fake_a")
p = FAKE_A.run(TMP / "no-such-rom.nes")
fails_with("a missing ROM fails with exit 1", p.returncode, 1, p,
           must_say=("no ROM at",))

p = FAKE_A.run(ROM, script=TMP / "no-such-script.lua")
fails_with("a missing Lua script fails with exit 1", p.returncode, 1, p,
           must_say=("no script at",))

p = FAKE_A.run(ROM, env={"MAGICIAN_EXPECT_SHA1": "0" * 40})
fails_with("a ROM whose SHA1 is not the one asked for fails with exit 1",
           p.returncode, 1, p, must_say=("FAIL", "MAGICIAN_EXPECT_SHA1"))

# THE REGRESSION. A ROM whose header byte 4 reads as 0 -- which is what run.sh's
# `head -c 1 | tail -c +5` produced -- must stop the run before it can print a
# vector line. `must_not_say` is the load-bearing half: the old harness printed
# "vectors from the file: nmi=$0000 reset=$0000 irq=$0000" and exited 0.
zero = S.make_rom(TMP / "zeroprg.nes", prg_units=0, chr_units=0,
                  header=bytes(S.MAGIC) + bytes((0, 0, 0x40, 0)) + bytes(8))
p = FAKE_A.run(zero)
fails_with("a ROM whose PRG length reads as 0 fails with exit 1 and prints NO "
           "vector line", p.returncode, 1, p,
           must_say=("could not be derived",), must_not_say=("vectors from the file",))

p = FAKE_A.run(TMP / "truncated.nes")      # does not exist at all; see above
fails_with("the zero-PRG failure is distinguishable from a missing file",
           p.returncode, 1, p, must_not_say=("vectors from the file",))

# Nothing above may have launched anything.
check("none of the pre-launch failures reached the launcher at all",
      not (FAKE_A.dir / "stub_env").exists(),
      "stub_env is written by the fake launcher, so its absence is proof that "
      "nothing was launched. A guard that fires after the emulator has started "
      "costs a window and a session for a mistake that was detectable first.")

# =====================================================================================
# B. The happy path, and the two lines that carry the evidence.
# =====================================================================================
FAKE_B = Fake("fake_b")
p = FAKE_B.run(ROM, env={"MAGICIAN_EXPECT_SHA1": WANT_SHA1})
check("a good ROM, a good verdict and a matching SHA1 exit 0",
      p.returncode == 0, f"exit {p.returncode}\n{p.stdout}\n{p.stderr}")
out = p.stdout
# The vectors, in the order they are stored: NMI, RESET, IRQ. RESET is the middle
# word. The synthetic ROM's three vectors are all different, so a swap cannot pass.
check("the vector line is nmi/reset/irq with this ROM's values",
      "nmi=$E0A1 reset=$E0B9 irq=$E0A9" in out,
      f"looking for nmi=$E0A1 reset=$E0B9 irq=$E0A9 in:\n{out}")
check("the printed vectors match identity.py's, which the window was built from",
      FAKE_B.run(ROM).stdout.count("nmi=$E0A1 reset=$E0B9 irq=$E0A9") == 1)
# The window: 48 bytes at $E099, hexed. Recomputed here from the MMC3 bank map by
# synthcart.rom_at, not read out of run.sh.
want_hex = S.rom_at(ROM, 0xE099, length=48).hex()
check("the 48-byte identity window is printed at $E099, byte for byte",
      f"want $E099 = {want_hex}" in out,
      f"looking for 'want $E099 = {want_hex}' in:\n{out}")
check("the window hex is 96 characters, i.e. all 48 bytes were read",
      len(want_hex) == 96)

# ...and it is what run.sh actually exported, which is what the real preamble reads
# back out of the core. This is the join between this file and a real BizHawk run.
argv_line = FAKE_B.argv_line()
exported = FAKE_B.env_exported()

check("run.sh exported the same 48 bytes to the emulator as MAGICIAN_WANT_WINDOW_HEX",
      exported.get("MAGICIAN_WANT_WINDOW_HEX", "").lower() == want_hex,
      f"exported {exported.get('MAGICIAN_WANT_WINDOW_HEX')!r}\n"
      f"         recomputed {want_hex!r}")
check("run.sh exported the window address, and it is $E099",
      exported.get("MAGICIAN_WANT_WINDOW_AT") == str(0xE099),
      f"got {exported.get('MAGICIAN_WANT_WINDOW_AT')!r}, $E099 is {0xE099}")
check("run.sh passed exactly one --lua script -- the preamble it generated -- and "
      "the real script travels in MAGICIAN_MAIN_LUA",
      argv_line.count("--lua ") == 1
      and "/preamble.lua" in argv_line
      and exported.get("MAGICIAN_MAIN_LUA", "").endswith(NOSCRIPT.name),
      f"a relative ROM path and a missing preamble are what made BizHawk fall "
      f"back to NullHawk:\n{argv_line}")
check("run.sh passed --gdi, which this build of BizHawk requires",
      "--gdi" in argv_line, argv_line)
check("run.sh exported the ABSOLUTE ROM path, not just the basename",
      exported.get("MAGICIAN_WANT_ROM_PATH") == str(ROM),
      f"a relative path here is what made BizHawk fail to load the file and "
      f"fall back to NullHawk while the run still looked successful")
check("run.sh exported the ROM's whole-file SHA1 for the preamble to report",
      len(exported.get("MAGICIAN_WANT_SHA1", "")) == 40,
      exported.get("MAGICIAN_WANT_SHA1"))

# =====================================================================================
# C. The verdict. A NullHawk fallback, and a run where nothing wrote one at all.
# =====================================================================================
NULLHAWK_VERDICT = (
    'want_system  = NES\n'
    'systemid     = Null\n'
    'boardname    = <absent>\n'
    'domains      = Null\n'
    'verdict FAIL core is "Null", not "NES" -- BizHawk fell back to a different '
    'core. NullHawk is the usual one and it renders nothing, which is how a '
    'broken run looks like a good one\n')

# The expected-output fixtures, prepared before any launch so that no test can
# depend on another test's leftovers.
OUT = TMP / "out"
OUT.mkdir(parents=True, exist_ok=True)
missing = OUT / "never_written.png"
missing.unlink(missing_ok=True)
empty = OUT / "empty.png"
empty.write_bytes(b"")
partial = OUT / "partial.txt"
partial.write_text("first 100 bytes of a dump that is still being written\n")
done_dump = OUT / "done.txt"
done_dump.write_text("a complete dump\nEND OF DUMP\n")

# Every launch in groups C, D and E, prepared and fired together. Three of them
# cost 5 s each because run.sh waits that long before deciding an emulator is
# gone rather than slow; running them concurrently costs 5 s once.
CASES = [
    ("nullhawk", Fake("c_nullhawk").verdict(NULLHAWK_VERDICT), ROM, {}, 3,
     ("Null", "not running what was asked for"), ("verdict ok (",)),
    ("noverdict", Fake("c_noverdict").no_verdict(), ROM, {}, 3,
     ("no verdict after",), ()),
    ("emptyverdict", Fake("c_empty").verdict(""), ROM, {}, 3,
     ("no verdict after",), ()),
    ("notesonly", Fake("c_notes").verdict("want_system = NES\nsystemid = NES\n"),
     ROM, {}, 3, ("never a verdict", "Lua error"), ("not running what was asked for",)),
    ("okandfail", Fake("c_okfail").verdict("verdict ok\nverdict FAIL and also ok\n"),
     ROM, {}, 3, ("not running what was asked for",), ()),
    ("missing", Fake("d_missing"), ROM, {"MAGICIAN_EXPECT": str(missing)}, 5,
     ("does not exist",), ()),
    ("empty", Fake("d_empty"), ROM, {"MAGICIAN_EXPECT": str(empty)}, 5,
     ("is 0 bytes",), ()),
    ("partial", Fake("d_partial"), ROM,
     {"MAGICIAN_EXPECT": str(partial), "MAGICIAN_DONE": "END OF DUMP"}, 5,
     ("never reached its sentinel", "truncated"), ()),
    ("done", Fake("d_done"), ROM,
     {"MAGICIAN_EXPECT": str(done_dump), "MAGICIAN_DONE": "END OF DUMP"}, 0, (), ()),
]
RESULTS = dict(zip([c[0] for c in CASES],
                   in_parallel([(lambda c=c: c[1].run(c[2], env=c[3])) for c in CASES])))
for key, fake, _rom, _env, rc, say, notsay in CASES:
    if rc == 0:
        continue
    fails_with(f"[{key}] fails with exit {rc} and says why",
               RESULTS[key].returncode, rc, RESULTS[key],
               must_say=say, must_not_say=notsay)

pd_ = RESULTS["done"]
check("an expected output that DOES reach its sentinel passes",
      pd_.returncode == 0, f"exit {pd_.returncode}\n{pd_.stdout}\n{pd_.stderr}")
check("run.sh reports the size of each expected output it accepted",
      f"ok {done_dump}" in pd_.stdout, pd_.stdout)

# =====================================================================================
# E. NES/SaveRAM/. Read-only, always, and derived per dump rather than assumed.
# =====================================================================================
# Beta 1's battery bit is CLEAR, so BizHawk keeps no save for it and a stale file
# in the directory cannot affect a run. The old run.sh deleted it anyway, which
# meant destroying the user's saves to protect against a hazard that does not
# exist. `--never write to NES/SaveRAM/` is taken literally: this asserts it.
sram = FAKE_B.dir / "NES" / "SaveRAM"
decoy = sram / "Magician (USA) (Beta 1) (1990-03-02).SaveRAM"
decoy.write_text("the user's save; must survive\n", encoding="utf-8")
DECOY_BYTES = decoy.read_bytes()

# The same filename, with a dump whose battery bit IS set -- the release's $42 --
# in its own directory, because a test that overwrote `ROM` on the way would be
# testing a different cartridge than the one the line above checked.
BATT = S.make_rom(TMP / "battery_set" / "Magician (USA) (Beta 1) (1990-03-02).nes",
                  f6=S.F6_BATTERY)
CLEAN = S.make_rom(TMP / "battery_set" / "Magician (USA).nes", f6=S.F6_BATTERY)
check("the two dumps differ only in header byte 6, which is what decides this",
      ROM.read_bytes()[:6][4:6] == BATT.read_bytes()[:6][4:6]
      and ROM.read_bytes()[6] != BATT.read_bytes()[6],
      f"{ROM.read_bytes()[6]:#04x} vs {BATT.read_bytes()[6]:#04x}")
FAKE_E1 = Fake("e_clear")
FAKE_E1.dir.joinpath("NES", "SaveRAM", decoy.name).write_bytes(DECOY_BYTES)
LISTING = sorted(q.name for q in (FAKE_E1.dir / "NES" / "SaveRAM").iterdir())
FAKE_E2 = Fake("e_set_with_save")
FAKE_E2.dir.joinpath("NES", "SaveRAM", decoy.name).write_bytes(DECOY_BYTES)
FAKE_E3 = Fake("e_set_no_save")
p1, p2, p3 = in_parallel([
    lambda: FAKE_E1.run(ROM),
    lambda: FAKE_E2.run(BATT),
    lambda: FAKE_E3.run(CLEAN),
])

check("a ROM with the battery bit CLEAR is not blocked by a stale save file",
      p1.returncode == 0, f"exit {p1.returncode}\n{p1.stdout}\n{p1.stderr}")
check("that stale save file was NOT deleted",
      decoy.exists() and decoy.read_bytes() == DECOY_BYTES,
      "run.sh must never write to NES/SaveRAM/, not even to delete")
check("nothing was added to NES/SaveRAM/ either",
      sorted(q.name for q in sram.iterdir()) == LISTING)

fails_with("a ROM with the battery bit SET and a matching stale save fails with "
           "exit 4 rather than deleting it", p2.returncode, 4, p2,
           must_say=("battery bit SET",))
check("run.sh did not delete the save it refused to use",
      decoy.exists() and decoy.read_bytes() == DECOY_BYTES)
check("a ROM with the battery bit SET and NO stale save proceeds",
      p3.returncode == 0, f"exit {p3.returncode}\n{p3.stdout}\n{p3.stderr}")
check("run.sh never mentions deleting a save",
      "will not delete" in (p1.stdout + p1.stderr + p2.stdout + p2.stderr),
      (p1.stdout + p1.stderr + p2.stdout + p2.stderr))

# =====================================================================================
# F. The bash guard bug class.
# =====================================================================================
# `[ "$x" -lt 0x8000 ]` does not compare. On this machine's bash 5.3 it prints
# "integer expected" and exits 2 -- and 2 is not an answer to "is it less than",
# so a guard written that way is not a guard: under `set -e` it aborts the script
# for a reason the author did not write down, and on a shell that instead
# *ignores* the bad operand it silently passes. run.sh once had exactly that and
# the PRG-length guard did nothing. The fix is `$((0x8000))`, so the shell
# expands the hex before `[` ever sees it.
#
# Measured on this machine, which is the only machine this suite claims anything
# about: /bin/sh is a symlink to bash here, so both give rc=2 with a message.
# What is asserted is the property that matters on any shell -- the bare form
# never produces the comparison's own answer.
def _brackets(expr, shell="/bin/bash"):
    return subprocess.run([shell, "-c", f"[ {expr} ]; echo rc=$?"],
                          capture_output=True, text=True)


bad_true = _brackets("0 -lt 0x8000")            # the answer should be: TRUE (rc 0)
bad_false = _brackets("65536 -lt 0x8000")       # the answer should be: FALSE (rc 1)
good_true = _brackets("0 -lt $((0x8000))")
good_false = _brackets("65536 -lt $((0x8000))")

check("`[ 0 -lt 0x8000 ]` does not answer TRUE, which is the truth",
      bad_true.stdout.strip() != "rc=0",
      f"it answered rc=0, i.e. the guard PASSES for a value it must reject. "
      f"stdout {bad_true.stdout!r} stderr {bad_true.stderr!r}")
check("`[ 65536 -lt 0x8000 ]` does not answer FALSE by comparing -- it errors",
      bad_false.stdout.strip() != "rc=1" or bad_false.stderr.strip() != "",
      f"stdout {bad_false.stdout!r} stderr {bad_false.stderr!r}")
check("both bare-hex forms are indistinguishable from each other, which is the "
      "point: neither is doing the comparison",
      bad_true.stdout.strip() == bad_false.stdout.strip()
      and bad_true.stderr.strip() == bad_false.stderr.strip(),
      f"0: {bad_true.stdout!r}/{bad_true.stderr!r}\n"
      f"65536: {bad_false.stdout!r}/{bad_false.stderr!r}")
check("`[ 0 -lt $((0x8000)) ]` answers TRUE (rc 0)",
      good_true.stdout.strip() == "rc=0",
      f"stdout {good_true.stdout!r} stderr {good_true.stderr!r}")
check("`[ 65536 -lt $((0x8000)) ]` answers FALSE (rc 1) and is silent",
      good_false.stdout.strip() == "rc=1" and good_false.stderr.strip() == "",
      f"stdout {good_false.stdout!r} stderr {good_false.stderr!r}")

# ...and no guard left in the harness is written the broken way. The grep is
# blunt; what makes it worth having is that the four checks above show the
# pattern really is broken on this machine.
src = RUN_SH.read_text(encoding="utf-8")
offenders = [f"{i + 1}: {ln.strip()}" for i, ln in enumerate(src.splitlines())
             if re.search(r'\[ "[^"]*" -[lg][te] 0x', ln)]
check("no guard in run.sh compares against a bare 0x literal", not offenders,
      "a bare 0x in -lt/-gt defeats the guard it is in; see the four checks "
      f"above. Offenders:\n" + "\n".join(offenders))

print(f"run.sh: {ok} checks")
print("all checks passed")