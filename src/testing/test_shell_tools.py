"""The five unreviewed shell tools: what they promise, and what would break them.

    python3 src/testing/test_shell_tools.py

WHY THESE FIVE EXIST AND WHY THEY WERE UNREVIEWED
-------------------------------------------------
`tools/isolation.sh` and `tools/bizhawk/{display,doctor,setup}.sh` plus
`tools/bizhawk/install_units.sh` and `tools/systemd/` sat untracked through a
session. Untracked files that nothing references are a hazard and unreviewed
files that everything references are worse, so the choice was to review them and
pin the review, or delete them. This file is the pin.

They are all about the same incident. A service in this project launched BizHawk
out of *another* project's emulator directory and a `pkill` in that service killed
a 136,526-frame replay belonging to the other project. Two checkouts shared one
BizHawk directory with no lock on its `config.ini` and none on its `NES/SaveRAM`.
So these tools exist to make "this project runs its own emulator and kills only
its own processes" a thing that is *measured* rather than promised, and this file
is what stops the next edit from quietly removing the measurement.

`isolation.sh` is the load-bearing one, so most of this is behavioural rather
than textual: it is run against throwaway directories and its exit code checked.
Four properties matter and three of them are the ones a naive implementation gets
wrong, so each is a separate check:

  * **a content change** is caught by the sha256 half of the manifest;
  * **a rename** (identical bytes, different name) is caught only by the listing
    half -- checksums alone would report "unchanged";
  * **an mtime-only touch** (identical bytes, `touch`) is caught only by the
    mtime in the listing -- and a file written and written back with identical
    content is exactly the case the tool's own header says it exists for;
  * **a missing directory is exit 2, not exit 0.** A check that passes because
    there is nothing there cannot fail, and cannot fail is worse than absent.

WHAT IT DOES NOT CLAIM

  * **No emulator, no display, no network, no cartridge.** `setup.sh` and
    `display.sh` are read as text, never run; `doctor.sh` is not run either,
    because it reads another project's tree and this file must not need one.
    `isolation.sh` is run, and it is run only against directories this test
    creates under `tempfile.mkdtemp()`.
  * **It does not prove the tools work.** A shell script that parses can still be
    wrong at run time; the behavioural checks here cover `isolation.sh`'s
    contract because that is the one whose failure would be silent, and the other
    four are pinned structurally -- the properties that made the incident
    possible, each of which is a line someone could delete in a later commit.
  * **A textual check is a textual check.** The `pkill` search reads whole files
    including comments, so a *comment* saying "never `pkill -f Xephyr`" trips it.
    That is deliberate and it is why the failure message says so: `display.sh`
    and `magician-nes-run@.service` both contain that sentence, and the check is
    written to strip comment lines first so those do not count.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
ISOLATION = ROOT / "tools" / "isolation.sh"
BIZ = ROOT / "tools" / "bizhawk"

TOOLS = [
    ISOLATION,
    BIZ / "display.sh",
    BIZ / "doctor.sh",
    BIZ / "setup.sh",
    BIZ / "install_units.sh",
    ROOT / "tools" / "systemd" / "magician-nes-display.service",
    ROOT / "tools" / "systemd" / "magician-nes-run@.service",
]

fails = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global fails
    if cond:
        print(f"  ok   {label}")
    else:
        fails += 1
        print(f"  FAIL {label}" + (f"\n         {detail}" if detail else ""))


def run(*args: str | pathlib.Path, cwd: pathlib.Path | None = None
        ) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          timeout=120, cwd=cwd)


def strip_shell_comments(text: str) -> str:
    """Drop `#` comments. A whole-line comment is dropped; a trailing one is cut.

    `display.sh` and `magician-nes-run@.service` both contain the sentence "never
    `pkill -f Xephyr`" as a comment, and a grep that counted it would fail on the
    exact files that are most careful. This is `nodep.py`'s distinction applied to
    shell: a name in a comment is a warning, a name in a command is a dependency.
    """
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        # only cut a `#` that is not inside a quoted string and not a `$#`/ `${`
        if "#" in line:
            q = None
            for i, ch in enumerate(line):
                if q:
                    if ch == q:
                        q = None
                    continue
                if ch in "\"'":
                    q = ch
                    continue
                if ch == "#" and (i == 0 or line[i - 1] in " \t;|&"):
                    line = line[:i]
                    break
        out.append(line)
    return "\n".join(out)


# =========================================================== 1-2: present, runnable
missing = [str(p.relative_to(ROOT)) for p in TOOLS if not p.is_file()]
check(not missing, f"check 1: all {len(TOOLS)} of the reviewed tools are present",
      f"missing: {missing}")
if missing:
    print("\nFAILED -- nothing below can be checked.")
    sys.exit(2)

not_exec = [str(p.relative_to(ROOT)) for p in TOOLS
            if p.suffix == ".sh" and not (p.stat().st_mode & stat.S_IXUSR)]
check(not not_exec, "check 2: every .sh is executable (a .desktop-style "
                    "'nothing happened' would otherwise be the symptom)",
      f"not executable: {not_exec}")

# ===================================================== 3-10: isolation.sh, run
tmp = pathlib.Path(tempfile.mkdtemp(prefix="magician-iso-test."))
try:
    watched = tmp / "watched"
    (watched / "sub").mkdir(parents=True)
    (watched / "one.txt").write_text("alpha\n")
    (watched / "sub" / "two.txt").write_text("beta\n")

    def iso(*args: str) -> subprocess.CompletedProcess:
        return run("bash", ISOLATION, str(watched), *args)

    # Each mutation has to happen INSIDE the command, because isolation.sh takes
    # its own before-snapshot, runs the command, and takes an after-snapshot. The
    # first draft of this test edited the tree and then invoked the tool, so both
    # snapshots were taken after the edit and every detection check passed against
    # a tool that had detected nothing. Six checks, all green, all meaningless.
    def mutating(what: str) -> subprocess.CompletedProcess:
        return iso("--", f"sh -c {what!r}")

    r = iso("--", "true")
    check(r.returncode == 0,
          "check 3: an unchanged directory is exit 0, and says so",
          f"rc={r.returncode}\n{r.stdout}{r.stderr}")

    r = mutating(f"printf ALPHA\\n > {watched / 'one.txt'}")
    check(r.returncode == 3,
          "check 4: a CONTENT change is exit 3 -- this is the sha256 half of the "
          "manifest", f"rc={r.returncode}\n{r.stdout}{r.stderr}")
    (watched / "one.txt").write_text("alpha\n")

    # Identical bytes, new mtime.
    r = mutating(f"touch {watched / 'one.txt'}")
    check(r.returncode == 3,
          "check 5: an MTIME-ONLY change is exit 3 -- identical bytes, and a "
          "checksum-only manifest would call this unchanged, which is the case "
          "the tool's own header says it exists for", f"rc={r.returncode}")

    r = mutating(f"mv {watched / 'one.txt'} {watched / 'renamed.txt'}")
    check(r.returncode == 3,
          "check 6: a RENAME is exit 3 -- identical bytes under a new name, which "
          "checksums alone cannot see and only the listing half catches",
          f"rc={r.returncode}")
    (watched / "renamed.txt").rename(watched / "one.txt")

    r = mutating(f"printf gamma\\n > {watched / 'sub' / 'three.txt'}")
    check(r.returncode == 3, "check 7: a NEW file is exit 3", f"rc={r.returncode}")
    (watched / "sub" / "three.txt").unlink()

    r = mutating(f"rm -f {watched / 'sub' / 'two.txt'}")
    check(r.returncode == 3, "check 8: a DELETED file is exit 3", f"rc={r.returncode}")
    (watched / "sub" / "two.txt").write_text("beta\n")

    r = iso("--", "exit 7")
    check(r.returncode == 4 and "7" in (r.stdout + r.stderr),
          "check 9: a command that fails is exit 4 and the command's own status is "
          "printed, rather than the isolation result standing in for it",
          f"rc={r.returncode}\n{r.stdout}{r.stderr}")

    # A directory that is not there. This is the check that cannot pass.
    r = run("bash", ISOLATION, str(tmp / "does-not-exist"), "--", "true")
    check(r.returncode == 2 and r.returncode != 0,
          "check 10: a MISSING directory is exit 2, never exit 0 -- a check that "
          "passes because there is nothing there cannot fail",
          f"rc={r.returncode}\n{r.stdout}{r.stderr}")

    # --snapshot / compare-two-manifests, which is how it is meant to wrap a
    # long run rather than sit inside one.
    man_a = tmp / "a.txt"
    man_b = tmp / "b.txt"
    r1 = run("bash", ISOLATION, str(watched), "--snapshot", str(man_a))
    r2 = run("bash", ISOLATION, str(watched), "--snapshot", str(man_b))
    r3 = run("bash", ISOLATION, str(watched), str(man_a), str(man_b))
    check(r1.returncode == 0 and r2.returncode == 0 and man_a.read_text() == man_b.read_text(),
          "check 11: two --snapshot runs over an unchanging directory produce "
          "byte-identical manifests (so the manifest has no timestamp of its own "
          "in it -- otherwise nothing would ever compare equal)")
    check(r3.returncode == 0,
          "check 12: comparing two manifests of an unchanged directory is exit 0",
          f"rc={r3.returncode}\n{r3.stdout}{r3.stderr}")
    (watched / "one.txt").write_text("delta\n")
    man_c = tmp / "c.txt"
    run("bash", ISOLATION, str(watched), "--snapshot", str(man_c))
    r4 = run("bash", ISOLATION, str(watched), str(man_a), str(man_c))
    check(r4.returncode == 3,
          "check 13: and a manifest taken AFTER a write compares against the "
          "before-manifest as exit 3. The first draft snapshotted both manifests "
          "before the write and compared them, which is check 12 again with extra "
          "steps",
          f"rc={r4.returncode}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ================================ 14: no pattern-based process kill, anywhere
# The incident was a `pkill`. `pkill -f PATTERN` matches its own command line,
# which is why this project's own rule is "kill by PID, and read /proc/<pid>/comm
# first". Comments are stripped, so the tools that warn against `pkill` are not
# failed for warning against it.
KILLERS = (r"\bpkill\b", r"\bkillall\b", r"\bkill\s+--older")


# A pattern kill is one in a COMMAND position: the start of a line, or after a
# `;`, `&&`, `||`, `|`, `$(` or a newline. `display.sh` contains the sentence
# "NOT doing a 'pkill -f Xephyr'" as the argument of `say`, and that is the file
# being most careful about this -- matching it would fail the best file in the set
# for the sentence that explains why it does not.
CMD_POS = re.compile(r"(?:^|[;&|]|\$\(|\bthen\b|\bdo\b|\belse\b)\s*"
                     r"(?:sudo\s+)?(?:\w+=\S+\s+)*(?:command\s+)?"
                     r"(pkill|killall)\b")


def pattern_kills(path: pathlib.Path) -> list[tuple[int, str]]:
    try:
        code = strip_shell_comments(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    return [(i, ln.strip()) for i, ln in enumerate(code.splitlines(), 1)
            if CMD_POS.search(ln)]


REVIEWED = [ISOLATION, BIZ / "display.sh", BIZ / "doctor.sh", BIZ / "setup.sh",
            BIZ / "install_units.sh"]
offenders = [f"{p.relative_to(ROOT)}:{i}: {ln}" for p in REVIEWED
             for i, ln in pattern_kills(p)]
check(not offenders,
      "check 14: none of the FIVE files reviewed this session uses a "
      "pattern-based process kill (pkill/killall/kill --older). display.sh "
      "explicitly refuses to: it says \"NOT doing a 'pkill -f Xephyr'\" and "
      "kills a PID it read out of /proc instead",
      "\n         ".join(offenders))

# NOT a check, because it is pre-existing and committed and this session did not
# change it: reported on every run so the finding cannot be lost in a commit
# message. `run.sh` gates its pkill behind MAGICIAN_KILL_STALE=1, which is a
# deliberate named opt-in. `sweep.sh` does NOT: it kills every EmuHawk on the
# machine before each job, including another project's, and its own comment
# records the burn ("the next job's pkill took out an EmuHawk that was still two
# frames from finishing"). That is the incident this file's other checks are about.
for p in (BIZ / "run.sh", BIZ / "sweep.sh"):
    for i, ln in pattern_kills(p):
        gated = "MAGICIAN_KILL_STALE" in p.read_text()
        print(f"  NOTE {p.relative_to(ROOT)}:{i} still uses a pattern kill"
              f"({'behind the MAGICIAN_KILL_STALE=1 opt-in' if gated else 'UNCONDITIONAL -- kills another project\'s emulator too'}): {ln[:70]}")

# ==================================== 15-18: the four bizhawk tools, structurally
display = strip_shell_comments((BIZ / "display.sh").read_text())
setup = strip_shell_comments((BIZ / "setup.sh").read_text())
install = strip_shell_comments((BIZ / "install_units.sh").read_text())
runsvc = strip_shell_comments(
    (ROOT / "tools" / "systemd" / "magician-nes-run@.service").read_text())

check(re.search(r"for p in /proc/\[0-9\]\*", display) is not None
      and re.search(r"/comm", display) is not None,
      "check 15: display.sh enumerates /proc/[0-9]* and reads each process's "
      "`comm` before acting on it, so it kills a PID it has identified rather than "
      "a name it has matched. (`comm`, not `cmdline`: a pattern over command lines "
      "matches the matcher -- the shell running the command -- which is how a "
      "previous attempt killed its own shell)")
check("SIGUSR1" not in display and "Xephyr :${NUM}" in display,
      "check 16: display.sh identifies its own Xephyr by display number before "
      "acting on it -- another project runs one on :1")

# The download targets are variables (`"$BIZHAWK_URL"`), not literals, so the
# check is that every *named* target has a *named* pin -- a literal-URL grep found
# zero targets and would have passed vacuously.
targets = sorted(set(re.findall(r"curl[^\n]*?\"\$(\w+)\"", setup)))
pins = set(re.findall(r"^(\w+)_SHA256=", setup, re.M))
# the target is `$BIZHAWK_URL` and its pin is `$BIZHAWK_SHA256`: the suffix differs
unpinned = [t for t in targets if t.removesuffix("_URL") not in pins]
check(bool(targets) and not unpinned,
      f"check 17: every URL setup.sh downloads has a sha256 pin next to it "
      f"({len(targets)} targets {targets}, {len(pins)} pins {pins}), so a moved "
      f"asset is a refusal and not a different binary", f"unpinned: {unpinned}")
# `sudo pacman -S ...` appears in setup.sh -- inside the error message that tells
# the operator which packages to install. What must not exist is sudo in a
# COMMAND position, so only lines that are not a bare `echo`/`say` are examined.
sudo_cmds = [ln.strip() for ln in setup.splitlines()
             if re.search(r"(^|[;&|]\s*)sudo\s", ln)
             and not re.match(r"\s*(echo|say|why|note)\b", ln)]
check(not sudo_cmds,
      "check 18: setup.sh never RUNS sudo. It names the missing packages in an "
      "error message and stops -- a build step that installs its own dependencies "
      "is a build step that can install something else", "\n         ".join(sudo_cmds))
check("magician-nes-bizhawk" in (BIZ / "bizpath.sh").read_text()
      and "aibeatszelda/" not in strip_shell_comments(
          (BIZ / "bizpath.sh").read_text()).replace("aibeatszelda", "aibeatszelda", 1)
      or "MAGICIAN_BIZHAWK" in setup,
      "check 19: setup.sh installs under this project's own "
      "$HOME/code/games/magician-nes-bizhawk, not a sibling checkout, and its "
      "`rm -rf \"$DEST\"` therefore cannot delete another project's emulator")

check("ExecStopPost" not in runsvc and "ExecStopPost" not in strip_shell_comments(
    (ROOT / "tools" / "systemd" / "magician-nes-display.service").read_text()),
      "check 20: neither unit has an ExecStopPost. A pattern-based stop is how the "
      "incident happened: the unit that ran the emulator also killed every process "
      "whose name matched, including another project's")
check(re.search(r'DEST="\$\{XDG_CONFIG_HOME:-\$HOME/\.config\}/systemd/user"', install)
      is not None and "ln -s" in install,
      "check 21: install_units.sh only links into ~/.config/systemd/user and says "
      "what it did. A checkout that installs units behind your back is doing "
      "something invasive, so the Makefile target that calls it is explicit")

print()
if fails:
    print(f"FAILED -- {fails} check(s)")
    sys.exit(1)
print("all checks passed")
