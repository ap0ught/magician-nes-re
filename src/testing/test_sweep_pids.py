"""A sweep must close its own emulator windows and nobody else's.

    python3 src/testing/test_sweep_pids.py

THE INCIDENT THIS PINS
----------------------
`tools/bizhawk/sweep.sh` ran, before every job:

    pkill -f '[m]ono EmuHawk'

That is the exact incident `tools/isolation.sh` exists to prevent -- a service in
this project killed an EmuHawk that belonged to another project -- sitting inside
the tooling committed to prevent it. Its own comment recorded the burn: *"the next
job's pkill took out an EmuHawk that was still two frames from finishing."* It ran
unconditionally, so unlike `run.sh`'s (which is behind `MAGICIAN_KILL_STALE=1`)
there was no opt-in to set.

Two faults, and the second one is the reason a behavioural test is here at all:

  1. it killed other projects' emulators, because nothing in `mono EmuHawk` says
     whose window this is; and
  2. **a pattern over command lines matches the messenger.** `pgrep -f`/`pkill -f`
     compare the pattern against every process's `cmdline`, including the shell and
     the Python that are running the command that mentions the string.

Fault 2 is measured below (check 8) rather than asserted from memory, because it is
the one that decides whether the fix is real: a reaper that still searched cmdlines
would kill the test harness itself.

WHY A FAKE EMUHAWK AND NOT THE REAL ONE
---------------------------------------
The real EmuHawk is 150 MB, needs a display, and takes ~40 s to boot. What the
identification actually reads is two fields out of `/proc/<pid>/`, so the stand-in
reproduces both exactly: a copy of `sleep` named `mono` (so `comm` is `mono`, which
is what BizHawk-on-mono gives) exec'd with `argv[0]` containing `EmuHawk` (so
`cmdline` contains it). Check 1 proves the stand-in is indistinguishable from a real
one *by the criteria the tool uses* -- if that check failed, every other check here
would be measuring nothing and passing vacuously.

A VACUOUS PASS IS WORSE THAN NO CHECK
-------------------------------------
The failure mode this file is built against is a check that cannot fail. Four
guards, each aimed at a specific way that could happen:

  * **check 1** -- the stand-in really is identified as an EmuHawk. Without it,
    "the foreign process survived" is consistent with "the tool never looked".
  * **check 5** -- `reap` must report the foreign PID by NAME while leaving it
    alive. A reaper that silently ignored it would also pass check 4.
  * **check 6** -- the same `reap` call must kill a PID it *is* allowed to kill, in
    the same invocation. So "survived" cannot mean "the whole command is a no-op".
  * **check 7** -- a recorded PID that has been recycled into something that is not
    an EmuHawk must not be killed. This is the one property that separates a pidfile
    from a loaded gun, and it is the one a naive implementation gets wrong.
  * **check 3** -- `sweep.sh` itself must contain no command-position pattern kill.

Nothing here needs an emulator, a display, a network or a cartridge.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SWEEP = ROOT / "tools" / "bizhawk" / "sweep.sh"
PIDS = ROOT / "tools" / "bizhawk" / "emuhawk_pids.sh"

fails = 0
checks = 0
seen: list[str] = []
stand_ins: list[subprocess.Popen] = []

# EVERY check this file is supposed to make, named. Asserted at the end.
#
# This used to be a hardcoded `print(f"sweep pid hygiene: {10} checks")`, which
# was already wrong -- there are eleven check calls -- and, worse, was a literal.
# A literal count cannot detect a check that stopped running: delete check 7 and
# the file still prints "10 checks". `run_all.py` counts `  ok ` lines for its
# summary and does not fail on a low number, so nothing downstream noticed.
# Deleting or renaming a check here now fails loudly, by name.
EXPECTED_CHECKS = ["check 1", "check 2", "check 3", "check 4", "check 4b",
                   "check 5", "check 6a", "check 6b", "check 7", "check 8",
                   "check 9"]


def check(cond: bool, label: str, detail: str = "") -> None:
    global fails, checks
    checks += 1
    seen.append(label.split(":")[0])
    if cond:
        print(f"  ok   {label}")
    else:
        fails += 1
        print(f"  FAIL {label}" + (f"\n         {detail}" if detail else ""))


def alive(pid: int) -> bool:
    """Running, not merely present.

    Same rule as the tool's `pid_running`: a zombie has released everything it was
    holding. The stand-ins are children of this process, so a killed one stays in
    `/proc` until it is reaped, and treating that as alive would fail check 6b for
    the same reason it failed before the tool was fixed.
    """
    p = pathlib.Path(f"/proc/{pid}")
    if not p.is_dir():
        return False
    try:
        status = (p / "status").read_text()
    except OSError:
        return False
    return not any(ln.startswith("State:") and ln.split()[1] == "Z" for ln in status.splitlines())


def start_stand_in(tmp: pathlib.Path, name: str, argv0: str) -> subprocess.Popen:
    """A process that is identifiable as an EmuHawk by /proc alone.

    `sleep` copied to a file named `mono`, exec'd with an argv[0] that contains
    `EmuHawk`. comm comes from the execve *pathname*, so it is `mono`; cmdline
    comes from argv, so it carries `EmuHawk`. That is the whole of what the
    reaper looks at.

    Each stand-in gets its OWN subdirectory holding a file named exactly `mono`.
    Sharing one directory was the first version's bug and it failed loudly, which
    is the right way: the second stand-in was called `mono2`, so its `comm` was
    `mono2`, and `add` refused it -- correctly. The tool was right and the test was
    wrong. That failure is check 6a.
    """
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    exe = d / "mono"
    if not exe.exists():
        shutil.copy2("/usr/bin/sleep", exe)
    p = subprocess.Popen([argv0, "600"], executable=str(exe),
                         start_new_session=True)
    stand_ins.append(p)
    return p


def reap(pidfile: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, MAGICIAN_SWEEP_PIDFILE=str(pidfile))
    return subprocess.run(["bash", str(PIDS), *args], capture_output=True,
                          text=True, timeout=60, env=env)


def tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(PIDS), *args], capture_output=True,
                          text=True, timeout=60,
                          env=dict(os.environ,
                                   MAGICIAN_SWEEP_PIDFILE="/nonexistent/never"))


def cleanup() -> None:
    for p in stand_ins:
        try:
            p.kill()
            p.wait(timeout=5)
        except Exception:                                   # noqa: BLE001
            pass


def main() -> int:
    global fails
    print("sweep.sh must close its own EmuHawk windows and nobody else's")
    print("  no emulator, no display, no network, no cartridge")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sweep-pids-"))
    pidfile = tmp / "ours.pids"
    try:
        # -------------------------------------------------- 1: the stand-in is real
        foreign = start_stand_in(tmp, "mono", "mono EmuHawk --gdi --config /tmp/x.ini")
        time.sleep(0.4)
        comm = pathlib.Path(f"/proc/{foreign.pid}/comm").read_text().strip()
        cmdline = pathlib.Path(f"/proc/{foreign.pid}/cmdline").read_bytes()
        listed = tool("list").stdout.split()
        check(comm == "mono" and b"EmuHawk" in cmdline
              and str(foreign.pid) in listed,
              "check 1: the stand-in is identified as an EmuHawk by the criteria "
              "the tool uses (comm == 'mono', cmdline contains 'EmuHawk'), so "
              "'it survived' cannot mean 'it was never examined'",
              f"comm={comm!r} cmdline={cmdline!r} listed={listed}")

        # ------------------------------- 2: the old pattern kill DOES match the messenger
        # The decoy is the hazard made deliberately: a process whose OWN cmdline
        # contains "mono EmuHawk" and whose comm is `sleep`. That is what a shell
        # or a Python running a command that mentions the string looks like, and
        # it is what `pkill -f` acts on.
        #
        # The first version of this check counted extra `pgrep -f` hits and
        # asserted there was at least one. It passed and failed depending on
        # whether stand-ins from an earlier run were still lying around -- a check
        # whose result depends on leftovers is not a check. This version builds the
        # exact case and asserts both halves: the pattern sees it, the tool does not.
        decoy = subprocess.Popen(["mono EmuHawk decoy", "600"],
                                 executable="/usr/bin/sleep",
                                 start_new_session=True)
        stand_ins.append(decoy)
        time.sleep(0.4)
        decoy_comm = pathlib.Path(f"/proc/{decoy.pid}/comm").read_text().strip()
        pat = subprocess.run(["pgrep", "-f", "[m]ono EmuHawk"],
                             capture_output=True, text=True).stdout.split()
        listed_now = tool("list").stdout.split()
        check(decoy_comm == "sleep" and str(decoy.pid) in pat
              and str(decoy.pid) not in listed_now,
              "check 2: a process whose cmdline merely MENTIONS `mono EmuHawk` is "
              "found by `pgrep -f` but NOT by this tool, because the tool also "
              "requires comm == 'mono'. This is the process the old "
              "`pkill -f '[m]ono EmuHawk'` would have killed -- including the "
              "shell running it",
              f"comm={decoy_comm!r} pgrep={pat} tool_list={listed_now}")

        # ------------------------------------ 3: sweep.sh has no pattern kill at all
        sweep_src = SWEEP.read_text()
        code = "\n".join(ln for ln in sweep_src.splitlines()
                         if not ln.strip().startswith("#"))
        offenders = [ln.strip() for ln in code.splitlines()
                     if ("pkill" in ln or "killall" in ln) and not ln.strip().startswith("echo")]
        check(not offenders,
              "check 3: sweep.sh contains no pattern-based process kill in a "
              "command position -- the unconditional `pkill -f '[m]ono EmuHawk'` "
              "that killed another project's emulator is gone",
              "\n         ".join(offenders))

        # ------------------------------- 4: reap leaves a foreign EmuHawk ALONE
        r = reap(pidfile, "reap")
        check(alive(foreign.pid),
              "check 4: `reap` does NOT kill an EmuHawk this project did not "
              "start -- another project's emulator survives a sweep",
              f"reap said: {r.stdout.strip()} / {r.stderr.strip()}")
        check(str(foreign.pid) not in pidfile.read_text()
              if pidfile.exists() else True,
              "check 4b: the foreign PID was not written into our record, so no "
              "later reap can reach it either")

        # ------------------- 5: reap NAMES the foreign pid while leaving it alive
        f = tool("foreign").stdout.split()
        check(str(foreign.pid) in f and alive(foreign.pid),
              "check 5: `foreign` reports the un-started PID by name, and it is "
              "still alive -- so the tool demonstrably saw it and chose not to "
              "kill it, rather than never having looked",
              f"foreign={f} alive={alive(foreign.pid)}")

        # ------------- 6: the same reap DOES kill one of ours (anti-vacuity)
        mine = start_stand_in(tmp, "mono2", "mono EmuHawk --gdi --config /tmp/y.ini")
        time.sleep(0.4)
        add = reap(pidfile, "add", str(mine.pid))
        check(add.returncode == 0 and str(mine.pid) in pidfile.read_text(),
              "check 6a: `add` records a PID this project started",
              f"add said: {add.stdout.strip()} / {add.stderr.strip()}")
        r = reap(pidfile, "reap")
        for _ in range(20):
            if not alive(mine.pid):
                break
            time.sleep(0.25)
        check(r.returncode == 0 and not alive(mine.pid),
              "check 6b: the SAME `reap` invocation that spared the foreign "
              "emulator killed ours AND exited 0. Without this, check 4 would "
              "also pass if `reap` were a no-op -- and without the exit code, a "
              "reap that killed a child and then called it 'would not die' (the "
              "zombie bug) would pass too",
              f"reap rc={r.returncode} said: {r.stdout.strip()} / "
              f"{r.stderr.strip()} mine_alive={alive(mine.pid)}")

        # ------------ 7: a recorded pid recycled into something else is not killed
        victim = subprocess.Popen(["sleep", "600"], start_new_session=True)
        time.sleep(0.3)
        pidfile.write_text(f"{victim.pid}\n")
        r = reap(pidfile, "reap")
        check(alive(victim.pid),
              "check 7: a PID in our record that is no longer an EmuHawk is NOT "
              "killed -- the identification is re-checked at reap time, so a "
              "recycled PID cannot take an innocent process with it",
              f"reap said: {r.stdout.strip()} / {r.stderr.strip()} "
              f"alive={alive(victim.pid)}")
        victim.kill()

        # --------------------------------------- 8: `add` refuses a non-EmuHawk pid
        r = reap(pidfile, "add", str(victim.pid))
        check(r.returncode == 2,
              "check 8: `add` refuses to record a PID that is not an identifiable "
              "EmuHawk, so a typo cannot land in the pidfile where the next reap "
              "would act on it",
              f"rc={r.returncode} out={r.stdout.strip()} err={r.stderr.strip()}")

        # --------------------------- 9: coverage -- no live EmuHawk left in the record
        # Not "the file is empty": check 7 deliberately leaves a non-EmuHawk PID in
        # it, and that is the correct end state -- a stale line that will never be
        # acted on. The property that matters is that nothing in the record is a
        # live EmuHawk, because that is all `reap` will ever act on.
        listed = set(tool("list").stdout.split())
        still = [p for p in (pidfile.read_text().split() if pidfile.exists() else [])
                 if p in listed]
        check(not still,
              "check 9: no PID left in our record is a live EmuHawk, so no later "
              "reap can reach one. (The record is deliberately NOT required to be "
              "empty: check 7 leaves a recycled PID in it, which is inert.)",
              f"still-live EmuHawk pids in the record: {still}")
    finally:
        cleanup()
        shutil.rmtree(tmp, ignore_errors=True)

    # The file's own coverage, asserted rather than printed. Two ways this can be
    # wrong and both are failures: a check that did not run (crash, early exit, a
    # `continue` that skipped it) and a check added here but not to
    # EXPECTED_CHECKS. `check()` above runs before the finally block on the happy
    # path, so this is reached with the full tally.
    missing = [c for c in EXPECTED_CHECKS if c not in seen]
    unexpected = sorted(set(seen) - set(EXPECTED_CHECKS))
    if missing or unexpected:
        print(f"  FAIL coverage: expected {len(EXPECTED_CHECKS)} named checks; "
              f"missing={missing} unexpected={unexpected} ran={seen}")
        fails += 1
    else:
        print(f"  ok   coverage: all {len(EXPECTED_CHECKS)} named checks ran "
              f"({', '.join(c.split()[-1] for c in EXPECTED_CHECKS)})")

    print(f"sweep pid hygiene: {checks} checks")
    if fails:
        print(f"{fails} FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())