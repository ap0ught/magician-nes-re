"""One name for the emulator directory, and no way to fall back onto someone else's.

    python3 src/testing/test_bizpath.py

WHAT THIS FILE IS ABOUT
----------------------
`tools/bizhawk/run.sh` resolved the emulator from `$BIZHAWK` and `src/play/emu.py`
resolved it from `$MAGICIAN_BIZHAWK`, and both had a hardcoded default pointing
into a *different project*:

    tools/bizhawk/run.sh:57   BIZ="${BIZHAWK:-$HOME/code/games/aibeatszelda/BizHawk-2.11.1-win-x64}"
    src/play/emu.py:65        ... or (home / "code/games/aibeatszelda/BizHawk-2.11.1-win-x64")

`emu.py` builds the child's environment with `dict(os.environ)` and adds only
`MAGICIAN_*` keys, so it never handed `BIZHAWK` to `run.sh`. The two names were
never joined, and MEASURED on this machine before the fix (2026-10-05):

    exported            emu.py's guard checked          run.sh launched
    ------------------  ------------------------------  ------------------------------
    BIZHAWK=/new        the SIBLING's directory          /new
    MAGICIAN_BIZHAWK=/new  /new                          the SIBLING's directory

Both directions disagree, and the second is the dangerous one: the guard
certifies a directory and the emulator runs out of a different one, so the run
reports success while measuring the other project's copy. That is this
repository's characteristic silent failure -- "a harness reporting success while
measuring nothing" -- and the cost was a 136,526-frame replay in that other
project killed by a `pkill` from a service that pointed at its emulator.

The consequence that makes this a *test* and not a comment: a path that happens
to exist is a fallback. Both sides used `$VAR` with a default, and the default
existed, so a typo in the variable silently got the sibling. So:

  * ONE canonical name, `MAGICIAN_BIZHAWK`, plus `BIZHAWK` as an accepted alias
    -- both funnelled through one resolver, with a HARD ERROR if they disagree.
  * A named default *location*, not a path that happens to exist: missing means
    exit non-zero with the fix in the message, never a fallback.
  * `emu.py` exports the resolved directory to `run.sh`, so the guard and the
    launch are the same string by construction.
  * Nothing in the executable tree may name the other project. `test_repo_
    hygiene.py` pins that half; this file pins the resolution.

A. the resolver: one name, one answer, precedence and refusals
B. run.sh actually honours `MAGICIAN_BIZHAWK` (the regression, run for real
   against the stub launcher, with `BIZHAWK` deliberately unset)
C. a directory that exists but is not a BizHawk is refused by name
D. the display: `:2`, ours, exported, overridable, never invented

WHAT IT DOES NOT CLAIM

  * **No emulator runs.** Every launch here goes to the stub in
    `test_run_sh.py`'s shape, so nothing here says BizHawk works. That was
    measured separately and is in `journal/14`.
  * **It does not check the install is the right version.** It checks the files
    `run.sh` will actually touch exist: `EmuHawkMono.sh`, `dll/`, `config.ini`'s
    directory. A wrong version with those files present is `make doctor`'s
    business, not this file's.

WHAT IT NEEDS: bash, python3, coreutils. No emulator, no cartridge, no movie,
no display, no network. Writes only under /tmp.
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
import nodep as nd    # noqa: E402  (src/testing/nodep.py)

BIZPATH = ROOT / "tools" / "bizhawk" / "bizpath.sh"
RUN_SH = ROOT / "tools" / "bizhawk" / "run.sh"
PROBE_SH = ROOT / "tools" / "bizhawk_probe.sh"

TMP = Path("/tmp/opencode/magician-testing/bizpath")
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True, exist_ok=True)

# The string the resolver must never fall back onto. Taken from nodep.py, which is
# also what strips comments so that a name in prose is not a hit.
SIBLING = nd.SIBLING

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


# ---------------------------------------------------------------- the fixtures
def fake_biz(name, *, launcher=True, dll=True, base=None):
    """A directory that is shaped like a BizHawk install, in as much detail as
    `run.sh` will actually look at.

    Deliberately cheap: no 150 MB of DLLs and no `EmuHawk.exe`. Nothing in the
    resolution path inspects those, and a test that needed them would be a test
    that quietly needed the emulator.
    """
    d = (base or TMP) / name
    (d / "NES" / "SaveRAM").mkdir(parents=True, exist_ok=True)
    (d / "config.ini").write_text("# fake\n", encoding="utf-8")
    if dll:
        (d / "dll").mkdir(exist_ok=True)
    if launcher:
        p = d / "EmuHawkMono.sh"
        p.write_text(
            "#!/usr/bin/env bash\n"
            "# stub launcher; see src/testing/test_bizpath.py\n"
            'V="${MAGICIAN_VERIFY_DIR:?}/verify.txt"\n'
            'D="$(dirname "$0")"\n'
            '{ echo "stub: argv $*"; echo "stub: DISPLAY=${DISPLAY:-<unset>}"; '
            'env | grep "^MAGICIAN_" | sort; } > "$D/stub_env"\n'
            'if [ -f "$D/stub_verdict" ]; then cat "$D/stub_verdict" > "$V"; '
            'else printf "verdict ok\\n" > "$V"; fi\n'
            "exit 0\n", encoding="utf-8")
        p.chmod(0o755)
    return d


def resolve(env, script="printf %s \"$MAGICIAN_BIZHAWK_DIR\"", home=None):
    """Source bizpath.sh under `env` and return (stdout, stderr, returncode).

    `home` overrides `$HOME` so the *default* location can be made absent
    without touching the real one, which is what lets these checks say "the
    default is missing" on a machine where the default is present.
    """
    e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if home:
        e["HOME"] = str(home)
    e.update({k: str(v) for k, v in env.items()})
    body = (
        'set -uo pipefail\n'
        f'. "{BIZPATH}" || exit 1\n'
        f'{script}\n')
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True, env=e)


def launches_with(env, home=None, timeout=90):
    """Run the REAL run.sh and the REAL stub. Returns the CompletedProcess."""
    # HOME is always set, and always to the scratch one: the resolver reads it to
    # build the default location, so leaving the real HOME in place would let a
    # launch silently find the real install instead of the stub.
    e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
         "HOME": str(home or SCRATCH_HOME),
         "MAGICIAN_ALLOW_CONCURRENT": "1",
         "MAGICIAN_SETTLE": "6",
         "MAGICIAN_EXPECT_WAIT": "6"}
    # Strip the ambient environment FIRST, then apply `env`. The other order
    # strips the caller's own settings, and it did: this helper popped BIZHAWK
    # after merging `env`, so every "BIZHAWK alone" launch silently fell through
    # to the default location -- and the check passed on the return code while
    # measuring nothing. The first version of this file has that bug, and the
    # only reason it showed up is that the alias case asserts on WHICH stub
    # answered rather than on the exit code.
    for k in ("BIZHAWK", "MAGICIAN_BIZHAWK", "MAGICIAN_EXPECT", "MAGICIAN_DONE",
              "MAGICIAN_EXPECT_SHA1", "MAGICIAN_KILL_STALE", "MAGICIAN_INI",
              "MAGICIAN_SRAM", "MAGICIAN_DISPLAY", "DISPLAY"):
        e.pop(k, None)
    e.update({k: str(v) for k, v in env.items()})
    return subprocess.run(["bash", str(RUN_SH), str(TMP / "noop.lua"), str(ROM)],
                          capture_output=True, text=True, env=e, timeout=timeout)


SCRIPT = TMP / "noop.lua"
SCRIPT.write_text("-- stub script\n", encoding="utf-8")
ROM = S.make_rom(TMP / "Magician (USA) (Beta 1) (1990-03-02).nes")

SCRATCH_HOME = TMP / "home"
SCRATCH_HOME.mkdir(exist_ok=True)

# =====================================================================================
# A. The resolver.
# =====================================================================================
A1 = fake_biz("a1")
A2 = fake_biz("a2")
SPACEY = fake_biz("a dir with spaces")

# -- precedence
r = resolve({"MAGICIAN_BIZHAWK": A1}, home=SCRATCH_HOME)
check("MAGICIAN_BIZHAWK alone resolves to that directory",
      r.returncode == 0 and r.stdout.strip() == str(A1),
      f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")

r = resolve({"BIZHAWK": A2}, home=SCRATCH_HOME)
check("BIZHAWK alone -- the historical name -- still resolves, as an alias",
      r.returncode == 0 and r.stdout.strip() == str(A2),
      f"the override has to keep working or every existing invocation breaks; "
      f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")

r = resolve({"MAGICIAN_BIZHAWK": A1, "BIZHAWK": A1}, home=SCRATCH_HOME)
check("both names set to the SAME directory is not an error",
      r.returncode == 0 and r.stdout.strip() == str(A1),
      f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")

# -- THE AMBIGUITY. This is the case that made the old pair dangerous: two names,
# two answers, and no way for a reader to tell which one the emulator used.
r = resolve({"MAGICIAN_BIZHAWK": A1, "BIZHAWK": A2}, home=SCRATCH_HOME)
check("both names set to DIFFERENT directories is a hard error that names both",
      r.returncode != 0 and str(A1) in r.stderr and str(A2) in r.stderr,
      f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}\n"
      f"         A resolver that picks one of two contradictory settings is the "
      f"silent failure this file exists for: the caller cannot tell which "
      f"directory was used, and neither can the log.")

# -- the default is a NAMED LOCATION, not a path that happens to exist
want_default = SCRATCH_HOME / "code/games/magician-nes-bizhawk" / "BizHawk-2.11.1-linux-x64"
r = resolve({}, home=SCRATCH_HOME)
check("with neither name set and the default ABSENT, resolution refuses",
      r.returncode != 0 and str(want_default) in (r.stdout + r.stderr),
      f"the default must be somewhere that is THIS project's, or the whole "
      f"change is cosmetic. rc={r.returncode}\n{r.stdout}{r.stderr}")
check("that refusal names the default location it would have used",
      "magician-nes-bizhawk" in (r.stdout + r.stderr),
      f"an operator who set nothing needs to be told WHICH location was wanted, "
      f"not just that something was not there:\n{r.stdout}{r.stderr}")

# Under SCRATCH_HOME, because that is the HOME the resolution is run with: a
# fake under TMP would test the override, not the default.
DEFAULT_BIZ = fake_biz("code/games/magician-nes-bizhawk/BizHawk-2.11.1-linux-x64",
                       base=SCRATCH_HOME)
r = resolve({}, home=SCRATCH_HOME)
check("with neither name set and the default PRESENT, that is what is used",
      r.returncode == 0 and r.stdout.strip() == str(DEFAULT_BIZ.resolve()),
      f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")

check("the default location does NOT name the other project",
      SIBLING not in str(want_default),
      f"{want_default} -- a default that points at a sibling project is the "
      f"dependency this change is about")

ELSEWHERE = fake_biz("elsewhere/BizHawk-2.11.1-linux-x64")
r = resolve({"MAGICIAN_BIZHAWK_HOME": TMP / "elsewhere"}, home=SCRATCH_HOME)
check("MAGICIAN_BIZHAWK_HOME moves the default location without either name set",
      r.returncode == 0
      and r.stdout.strip() == str(ELSEWHERE.resolve()),
      f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")

# -- NEVER FALL BACK. A name set to something that does not exist must fail. The
# old code's `${VAR:-<the sibling>}` could not express this.
r = resolve({"MAGICIAN_BIZHAWK": TMP / "does-not-exist"}, home=SCRATCH_HOME)
check("a name pointing at a directory that does not exist FAILS",
      r.returncode != 0, f"rc={r.returncode} stdout={r.stdout!r}")
check("that failure names the fix, not a path",
      "emu-setup" in (r.stdout + r.stderr),
      f"a missing emulator must say how to get one. Got:\n{r.stdout}{r.stderr}")
check("that failure does NOT fall back to the other project's directory",
      SIBLING not in (r.stdout + r.stderr),
      f"falling back to a directory that happens to exist is the bug class this "
      f"repository documents at length. Got:\n{r.stdout}{r.stderr}")

r = resolve({"BIZHAWK": TMP / "does-not-exist"}, home=SCRATCH_HOME)
check("the alias refuses a nonexistent directory too, not just the canonical name",
      r.returncode != 0 and "emu-setup" in (r.stdout + r.stderr),
      f"rc={r.returncode}\n{r.stdout}{r.stderr}")

# -- the export contract. Three names, one value, or something downstream is
# reading a different answer than the guard checked.
body = ('set -uo pipefail\n. "{p}" || exit 1\n'
        'printf "%s\\n%s\\n%s\\n" "$MAGICIAN_BIZHAWK_DIR" "$MAGICIAN_BIZHAWK" "$BIZHAWK"'
        ).format(p=BIZPATH)
r = subprocess.run(["bash", "-c", body], capture_output=True, text=True,
                   env={"PATH": os.environ["PATH"], "HOME": str(SCRATCH_HOME),
                        "MAGICIAN_BIZHAWK": str(A1)})
vals = r.stdout.split()
check("MAGICIAN_BIZHAWK_DIR, MAGICIAN_BIZHAWK and BIZHAWK all carry the SAME path",
      len(vals) == 3 and vals[0] == vals[1] == vals[2] == str(A1),
      f"three names for one thing is the inconsistency being removed; they must "
      f"agree or a reader cannot tell which one a log line came from. Got "
      f"{vals} (rc={r.returncode}, stderr={r.stderr!r})")

# -- paths with spaces. Every BizHawk dump path on this machine has one, and
# `cd "$BIZ"` is not optional.
r = resolve({"MAGICIAN_BIZHAWK": SPACEY}, home=SCRATCH_HOME)
check("a path with a space survives resolution",
      r.returncode == 0 and r.stdout.strip() == str(SPACEY),
      f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")

# -- relative paths. run.sh `cd`s into the directory, so a relative one resolves
# against the wrong place -- the same trap as a relative ROM path, which is what
# produced the NullHawk fallback.
r = subprocess.run(["bash", "-c", 'set -uo pipefail\n. "%s" || exit 1\nprintf "%%s" "$MAGICIAN_BIZHAWK_DIR"\n' % BIZPATH],
                   capture_output=True, text=True, cwd=str(TMP),
                   env={"PATH": os.environ["PATH"], "HOME": str(SCRATCH_HOME),
                        "MAGICIAN_BIZHAWK": "a1"})
check("a relative directory is made absolute, because run.sh cds into it",
      r.returncode == 0 and r.stdout.strip() == str(A1.resolve()),
      f"got {r.stdout!r}, want {str(A1.resolve())!r} -- `EmuHawkMono.sh` does "
      f"`cd $(dirname $(realpath $0))`, so a relative BIZ is a different "
      f"directory by the time anything reads it")

# =====================================================================================
# B. run.sh honours the canonical name. THE REGRESSION.
# =====================================================================================
# This is the check that would have caught the measured disagreement, and it is
# written the only way that can: `BIZHAWK` is *removed* from the environment, so
# the only thing that can point run.sh at the stub is `MAGICIAN_BIZHAWK`.
B1 = fake_biz("b1")
p = launches_with({"MAGICIAN_BIZHAWK": B1})
check("run.sh launches out of MAGICIAN_BIZHAWK with BIZHAWK unset",
      p.returncode == 0, f"rc={p.returncode}\n{p.stdout}\n{p.stderr}")
check("...and the stub that answered is the one in THAT directory",
      (B1 / "stub_env").exists(),
      f"stub_env is written by the stub in {B1}, so its absence is proof that "
      f"the emulator was launched from somewhere else -- which is exactly the "
      f"measured bug this check pins.\n{p.stdout}\n{p.stderr}")

# And the same launch with only the alias set, because the shell tests and every
# old invocation use that name.
B2 = fake_biz("b2")
p = launches_with({"BIZHAWK": B2})
check("run.sh still launches out of the BIZHAWK alias alone",
      p.returncode == 0 and (B2 / "stub_env").exists(),
      f"rc={p.returncode} (B2/stub_env exists: {(B2 / 'stub_env').exists()})\n"
      f"{p.stdout}\n{p.stderr}")

# -- and the ambiguity reaches run.sh too, rather than being resolved quietly.
# Both stubs' `stub_env` are deleted first, so "which one was launched" is a
# measurement rather than an inference from an exit code.
(B1 / "stub_env").unlink(missing_ok=True)
(B2 / "stub_env").unlink(missing_ok=True)
p = launches_with({"MAGICIAN_BIZHAWK": B1, "BIZHAWK": B2})
body = p.stdout + p.stderr
check("run.sh refuses when the two names disagree, and says so before launching",
      p.returncode != 0 and str(B1) in body and str(B2) in body,
      f"rc={p.returncode}\n{body}")
check("that refusal reached NEITHER launcher, so it cost no window and no session",
      not (B1 / "stub_env").exists() and not (B2 / "stub_env").exists(),
      f"stub_env exists: B1={(B1 / 'stub_env').exists()} "
      f"B2={(B2 / 'stub_env').exists()}\n{body}")

# =====================================================================================
# C. A directory that exists but is not a BizHawk.
# =====================================================================================
# This is the other half of "never fall back onto a path that happens to exist":
# the path the operator names can exist and still be wrong. `[ -d "$BIZ" ]` was
# the whole check.
NOTBIZ = TMP / "not_bizhawk"
NOTBIZ.mkdir(exist_ok=True)
p = launches_with({"MAGICIAN_BIZHAWK": NOTBIZ})
body = p.stdout + p.stderr
check("a directory with no EmuHawkMono.sh is refused, not launched",
      p.returncode != 0 and "EmuHawkMono.sh" in body,
      f"[ -d ] is not enough: an empty directory passes it and the run then "
      f"measures nothing. rc={p.returncode}\n{body}")

NODLL = fake_biz("no_dll", dll=False)
p = launches_with({"MAGICIAN_BIZHAWK": NODLL})
body = p.stdout + p.stderr
check("a BizHawk with no dll/ is refused by name",
      p.returncode != 0 and "dll" in body,
      f"LD_LIBRARY_PATH is set to $BIZ/dll, so its absence is a launch that "
      f"cannot work. rc={p.returncode}\n{body}")

# =====================================================================================
# D. The display.
# =====================================================================================
# :2, not :1. aibeatszelda owns :1, and the point of a separate display is that
# a search in either project can be watched while the other runs. The number is
# a default and it is overridable, but the default is THIS project's.
body = ('set -uo pipefail\n. "{p}" || exit 1\n'
        'printf "%s|%s|%s\\n" "$MAGICIAN_DISPLAY" "$DISPLAY" "$MAGICIAN_DISPLAY_SOURCE"'
        ).format(p=BIZPATH)
r = subprocess.run(["bash", "-c", body], capture_output=True, text=True,
                   env={"PATH": os.environ["PATH"], "HOME": str(SCRATCH_HOME)})
parts = r.stdout.strip().split("|")
check("the default display is :2, and it is exported as DISPLAY",
      len(parts) == 3 and parts[0] == ":2" and parts[1] == ":2",
      f"aibeatszelda uses :1. Got {r.stdout!r} stderr={r.stderr!r}")

r = subprocess.run(["bash", "-c", body], capture_output=True, text=True,
                   env={"PATH": os.environ["PATH"], "HOME": str(SCRATCH_HOME),
                        "MAGICIAN_DISPLAY": ":7"})
parts = r.stdout.strip().split("|")
check("MAGICIAN_DISPLAY overrides it",
      parts[0] == ":7" and parts[1] == ":7", f"got {r.stdout!r}")

r = subprocess.run(["bash", "-c", body], capture_output=True, text=True,
                   env={"PATH": os.environ["PATH"], "HOME": str(SCRATCH_HOME),
                        "DISPLAY": ":0"})
check("an inherited DISPLAY is respected rather than overwritten -- so the "
      "live desktop still works, and it says which one it used",
      r.stdout.strip().split("|")[1] == ":0" and "inherited" in r.stdout,
      f"overriding a caller's DISPLAY silently would be this project's own bug "
      f"class. Got {r.stdout!r}")

# ...and it reaches the emulator. A systemd unit does not inherit DISPLAY, which
# is why the export has to be inside run.sh and not in the caller's shell.
D1 = fake_biz("d1")
p = launches_with({"MAGICIAN_BIZHAWK": D1, "MAGICIAN_DISPLAY": ":2"})
stub = (D1 / "stub_env")
check("run.sh exports DISPLAY to the emulator, so the window lands on ours",
      p.returncode == 0 and stub.exists()
      and "stub: DISPLAY=:2" in stub.read_text(encoding="utf-8"),
      f"rc={p.returncode}\n{p.stdout}\n{p.stderr}"
      + (f"\nstub_env:\n{stub.read_text(encoding='utf-8')}" if stub.exists() else
         "\nstub_env was never written, so nothing was launched"))

# =====================================================================================
# E. The resolver says where things are, and no CODE in the tree reaches the other
#    project. Prose may name it; a default path may not.
# =====================================================================================
text = BIZPATH.read_text(encoding="utf-8")
check("bizpath.sh states the default location it installs into",
      "magician-nes-bizhawk" in text,
      "a default nobody can find in the source is a default nobody can fix")

for sh in (BIZPATH, RUN_SH, PROBE_SH):
    rel = sh.relative_to(ROOT)
    hits = [(n, ln) for n, ln in nd.code_lines(sh) if SIBLING in ln]
    check(f"{rel} has no executable line naming the other project",
          not hits,
          "a name in a COMMENT is provenance and is welcome -- bizpath.sh's whole "
          "header is the story of why this file exists. A name in CODE is a "
          "dependency. Offenders:\n"
          + "\n".join(f"{rel}:{n}: {t}" for n, t in hits))

for sh in (RUN_SH, PROBE_SH):
    t = sh.read_text(encoding="utf-8")
    check(f"{sh.name} resolves the emulator through bizpath.sh",
          "bizpath.sh" in t,
          f"{sh} must have ONE place that decides where the emulator is. A "
          f"second default expansion for BIZHAWK is how the two drifted in the "
          f"first place.")
    offenders = [f"{i + 1}: {ln.strip()}" for i, ln in enumerate(t.splitlines())
                 if re.search(r'\$\{?BIZHAWK:?-', ln)]
    check(f"{sh.name} has no BIZHAWK-with-default expansion left",
          not offenders, "found:\n" + "\n".join(offenders))

# =====================================================================================
# F. The Python half agrees with the shell half, and hands run.sh the SAME answer.
# =====================================================================================
# `src/play/emu.py` resolved the directory from `$MAGICIAN_BIZHAWK` while
# `run.sh` resolved it from `$BIZHAWK`, and `emu.py` built the child's environment
# from `dict(os.environ)` without ever mentioning `BIZHAWK` -- so the guard and the
# launch could not agree even in principle. These checks compare the two
# implementations against each other rather than trusting either, because two
# copies of a rule in two languages drift silently and a comment saying they match
# is worth nothing.
sys.path.insert(0, str(ROOT / "src"))
import play.emu as PE  # noqa: E402  (importing must not need an emulator)

py_resolve = PE.resolve_bizhawk

r = py_resolve({"MAGICIAN_BIZHAWK": str(A1), "HOME": str(SCRATCH_HOME)})
check("emu.py resolves MAGICIAN_BIZHAWK to that directory",
      r[0] == A1 and r[1] == "MAGICIAN_BIZHAWK", f"got {r}")

r = py_resolve({"BIZHAWK": str(A2), "HOME": str(SCRATCH_HOME)})
check("emu.py resolves the BIZHAWK alias to that directory",
      r[0] == A2, f"got {r}")

try:
    py_resolve({"MAGICIAN_BIZHAWK": str(A1), "BIZHAWK": str(A2),
                "HOME": str(SCRATCH_HOME)})
    raised = None
except Exception as exc:            # noqa: BLE001 -- the class is what is asserted
    raised = exc
check("emu.py raises on two names set differently, and the message names both",
      isinstance(raised, PE.BizHawkConfigError)
      and str(A1) in str(raised) and str(A2) in str(raised),
      f"got {raised!r}")

r = py_resolve({"HOME": str(SCRATCH_HOME)})
check("emu.py's default is the same location bizpath.sh's default is",
      r[0] == want_default and r[1] == "the default location",
      f"emu.py says {r[0]}, bizpath.sh says {want_default}. Two implementations "
      f"of one rule in two languages; this check is the only thing keeping them "
      f"equal, so it must run on every commit rather than being assumed.")

# ...and the cross-check itself, rather than the value repeated a second time:
# run the real resolver under the same three environments and compare.
for label, env in (("MAGICIAN_BIZHAWK", {"MAGICIAN_BIZHAWK": str(A1)}),
                   ("BIZHAWK alias", {"BIZHAWK": str(A2)}),
                   ("the default", {})):
    shell = resolve(env, home=SCRATCH_HOME)
    py = py_resolve({**env, "HOME": str(SCRATCH_HOME)})
    check(f"python and shell agree on {label}",
          shell.stdout.strip() == str(py[0]),
          f"shell {shell.stdout.strip()!r} vs python {str(py[0])!r}")

# -- THE JOIN. `emu.py` hands run.sh the directory IT checked. This is the whole
# fix; without it the guard certifies one directory and the launch uses another,
# and both report success.
child = PE.child_env({"BIZHAWK": "/somewhere/else", "HOME": str(SCRATCH_HOME)})
check("child_env() exports the directory the guard checked",
      child.get("MAGICIAN_BIZHAWK") == str(PE.BIZHAWK),
      f"got {child.get('MAGICIAN_BIZHAWK')!r}, and __init__'s guard checks "
      f"{str(PE.BIZHAWK)!r}. These have to be one value; a guard and a launch "
      f"that read different variables is the measured bug.")
check("...and OVERWRITES an inherited BIZHAWK rather than letting it through, so "
      "run.sh cannot be pointed somewhere the guard never looked",
      child.get("BIZHAWK") == child.get("MAGICIAN_BIZHAWK"),
      f"BIZHAWK={child.get('BIZHAWK')!r} vs "
      f"MAGICIAN_BIZHAWK={child.get('MAGICIAN_BIZHAWK')!r}. The whole measured "
      f"bug was these two carrying different answers into one run.")
check("...and it pins the MODULE's answer rather than re-resolving `base`, "
      "because re-resolving is how the guard and the launch could diverge again",
      PE.child_env({"MAGICIAN_BIZHAWK": "/somewhere/else",
                    "HOME": str(SCRATCH_HOME)}).get("MAGICIAN_BIZHAWK")
      == str(PE.BIZHAWK),
      "if child_env re-resolved from its argument, then a base whose "
      "MAGICIAN_BIZHAWK differed from the module constant would send run.sh to a "
      "directory __init__ never checked -- the original bug, one level down")
check("child_env() leaves the rest of the environment alone",
      child.get("HOME") == str(SCRATCH_HOME),
      f"got {child.get('HOME')!r}")

# -- and the message when it is missing names the fix, not a path that exists
src = (ROOT / "src" / "play" / "emu.py").read_text(encoding="utf-8")
missing_msg = [ln for ln in src.splitlines()
               if "no BizHawk at" in ln or "make emu-setup" in ln]
check("emu.py's missing-emulator message names `make emu-setup`",
      any("make emu-setup" in ln for ln in missing_msg),
      f"a missing emulator must say how to get one. Found: {missing_msg}")
check("...and emu.py no longer hardcodes a default under another project",
      not [1 for _n, ln in nd.python_prose(src) if SIBLING in ln],
      "the dependency, in the Python half")

print(f"bizpath: {ok} checks")
print("all checks passed")
