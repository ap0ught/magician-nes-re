"""One command for the suite: every `src/testing/test_*.py`, each in its own process, with a real exit code.

    python3 src/testing/run_all.py                     # every test_*.py
    python3 src/testing/run_all.py --list              # the files and nothing else
    python3 src/testing/run_all.py test_identity.py test_fm2.py
    python3 src/testing/run_all.py -j 4                # four at a time
    python3 src/testing/run_all.py --timeout 30

Exits 0 only if every file exits 0.

WHAT THIS EXISTS FOR
--------------------
This repository had no tests. `tools/datcodec.py selftest` was 20 checks and
`tools/dis6502.py --selfcheck` was one, both of which had to be *remembered*, and
everything else in `tools/` was a script that printed a number. In that state the
project produced these, all of them plausible, none of them an error:

  * `tools/nestrace.py` reported a black screen for two different images.
  * `tools/modrange.py` reported a 0% match over an all-zero image, from a
    `TypeError` swallowed by a bare `except`.
  * `tools/bizhawk/zp.lua` bounded a loop *index* instead of an address, read 36
    bytes instead of 256, and printed five tidy rows of hex.
  * `tools/bizhawk/run.sh` read **no byte at all** for the PRG length
    (`head -c 1 | tail -c +5`) and reported three zero vectors without
    complaint.
  * a bash guard wrote `[ "$x" -lt 0x8000 ]`, which prints "integer expected" and
    then *passes*.

None of those is a hard bug. Each is a measurement that cannot be wrong loudly,
which is worse, because the number is what gets acted on. The instruments are the
thing that has to be pinned, and a test suite is the only thing that pins them.

FOUR RULES, and each exists because of a failure in the list above.

1. **A FRESH PROCESS PER FILE.** These are scripts, not a framework. Each one
   `os.chdir`s to the repo root at import and several import `asm/build.py`,
   which reads module-level state. Imported into one interpreter, file N sees
   file N-1's globals and working directory -- a suite whose result depends on
   alphabetical order, which is the failure nobody notices until it costs a day.

2. **AN IMPORT ERROR IS A FAILURE, NOT A SKIP.** If a file cannot be imported the
   subprocess exits 1 with a traceback and that is a failure, with the traceback
   printed. There is no `try/except` around a run here that lets a file be passed
   over, and there is deliberately no "skip unless the cartridge is present" path:
   `synthcart.py` builds a synthetic image for anything that needed a real one, so
   no test has a reason to ask for one. A test that wants to know something is
   unavailable says so inside itself, loudly, where the reason is visible.

3. **A FAILING FILE'S OUTPUT IS PRINTED IN FULL.** Not the last line, not a
   summary: the whole captured stdout and stderr under a rule, so the assertion
   that fired and the line above it that says what the check established are both
   there.

4. **THE COUNTS ARE COUNTS.** Collected, passed and failed are printed, and the
   number of files is asserted against the number discovered -- so a glob that
   matched nothing is a failure here rather than a cheerful "0 tests, all green".
   A suite that exits 0 having run nothing is the exact shape of failure this
   project keeps running into, and this file refuses to be it.

WHAT IT DOES NOT CLAIM

  * **It does not know what a check MEANS.** Each file prints its own facts; this
    one counts files and forwards their output. "Collected" and "passed" below
    mean *files*, because these files do not report a framework test count.

  * **A zero exit code from a file means every `assert` in it held.** These are
    scripts, so a failed assert raises out of module scope. It does NOT mean the
    file exercised the code it claims to: a file whose asserts are all inside a
    loop that never runs exits 0 having asserted nothing. This runner cannot
    detect that and does not pretend to; the defence is a test file's own
    fail-check -- each one ends by asserting something that must be false if the
    checks above it did not run.

  * **It does not check that the checks are still *needed*.** A test that pins a
    bug which has been fixed by construction still passes. `test_identity.py`
    addresses this by also asserting the *absence* of the bad spellings -- there
    is no `head -c 1 | tail -c +5` left in run.sh because a test greps for it.

  * **`-j` interleaves output.** Parallel runs print a file's block when it
    finishes, so two files finishing together appear in completion order.
    Single-threaded (the default) output is in file order.

  * **`--timeout` is 0 by default and a file that hangs hangs this.** A timeout
    is reported as TIMEOUT with the partial output and counted as a failure, never
    a skip. It is 0 because the slowest file here should be well under a second,
    and a timeout low enough to be useful here would turn a slow machine's real
    pass into a false failure.

WHAT IT NEEDS: python3 and bash. No emulator, no cartridge, no movie, no display,
no network. The whole suite is meant to finish in a couple of seconds so it can
gate every commit.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PATTERN = "test_*.py"


def discover(argv):
    """The files to run: those named on the command line, else every `test_*.py`."""
    if argv:
        out = []
        for name in argv:
            p = Path(name)
            out.append(p if p.exists() else HERE / name)
        return out
    return sorted(HERE.glob(PATTERN))


def _text(b):
    """subprocess gives bytes on a timeout and str otherwise. Normalise, never swallow."""
    if b is None:
        return ""
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else b


def run_one(path, timeout=0.0, env=None):
    """One file, in its own interpreter, with its own working directory at the repo root.

    The cwd matters: every test file does `os.chdir(_ROOT)` at import, but a file
    that fails BEFORE that line -- a syntax error, a bad import -- would otherwise
    be reported against whatever directory the runner was started in.
    """
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(path)], cwd=str(REPO),
                          capture_output=True, text=True,
                          timeout=timeout or None, env=env)
    return proc, time.time() - t0


def main():
    ap = argparse.ArgumentParser(description="run every src/testing/test_*.py in its own process")
    ap.add_argument("files", nargs="*", help="specific test files to run (default: all of them)")
    ap.add_argument("-j", "--jobs", type=int, default=1,
                    help="how many to run at once (default 1: output in file order)")
    ap.add_argument("--list", action="store_true", help="print the files and exit")
    ap.add_argument("--timeout", type=float, default=0,
                    help="seconds per file; 0 (the default) means wait as long as it takes")
    a = ap.parse_args()

    paths = discover(a.files)
    if a.list:
        for p in paths:
            print(p.relative_to(REPO) if p.is_absolute() else p)
        return 0

    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"FAIL: {len(missing)} named test file(s) do not exist: "
              + ", ".join(str(p) for p in missing), file=sys.stderr)
        return 1
    if not a.files and not paths:
        print(f"FAIL: no files matched {PATTERN} in {HERE}. That is a broken glob, "
              f"not a clean run.", file=sys.stderr)
        return 1

    print(f"{len(paths)} test files in {HERE}, one process each, {a.jobs} at a time")
    print("=" * 78)

    results = {}

    def one(p):
        try:
            proc, dt = run_one(p, a.timeout)
        except subprocess.TimeoutExpired as e:
            return p, (-1, 0.0, f"  TIMEOUT after {a.timeout}s\n"
                                 + _text(e.stdout) + _text(e.stderr))
        return p, (proc.returncode, dt, _text(proc.stdout) + _text(proc.stderr))

    if a.jobs > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for p, (rc, dt, out) in pool.map(one, paths):
                results[p] = (rc, dt, out)
    else:
        for p in paths:
            p, (rc, dt, out) = one(p)
            results[p] = (rc, dt, out)

    passed = failed = 0
    total = 0.0
    for p in paths:
        rc, dt, out = results[p]
        total += dt
        if rc == 0:
            passed += 1
            tag = "PASS"
        else:
            failed += 1
            tag = "FAIL" if rc > 0 else "TIMEOUT"
        checks = sum(1 for ln in out.splitlines() if ln.startswith("  ok "))
        print(f"{tag}  {p.name:34s} {dt:6.2f}s  exit={rc:<3d} {checks:4d} checks")

    print("=" * 78)
    for p in paths:
        rc, _dt, out = results[p]
        if rc == 0:
            continue
        print(f"\n---- {p.name} exit={rc} " + "-" * (58 - len(p.name)))
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
        if not out.strip():
            print("(no output at all: the file died before its first print)")

    print("\n" + "=" * 78)
    print(f"collected {len(paths)} files   passed {passed}   failed {failed}   "
          f"{total:.2f}s of wall clock in total")
    if failed:
        print(f"FAILED: {', '.join(p.name for p in paths if results[p][0] != 0)}")
        return 1
    # Belt and braces: a count that does not add up is not a green run.
    assert passed + failed == len(paths) == len(results), (passed, failed, len(paths))
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())