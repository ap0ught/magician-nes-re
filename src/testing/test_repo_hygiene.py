"""The three things that must never be committed, and the guard that says so.

    python3 src/testing/test_repo_hygiene.py

`tools/guard_staged.sh` exists because LEGAL.md already said the cartridge is
never committed and the movie is not either, and saying it was not enough: a new
tool arrives with a 709 847-byte `.fm2` on disk and a `--lua out.lua` a few
centimetres away from a ROM, and the cheapest thing at that moment is `git add
-A`. The brief for this suite called specifically for "a test that fails if the
`.fm2` is ever staged", so that is what most of this file does -- and it is done
by *running the guard against a synthetic index*, not by grepping for the string.

Twenty checks:

  A. what git is tracking: no cartridge, no movie, no save, no archive, and
     nothing big enough to be a cartridge in disguise
  B. what is in the working tree but not tracked, same list
  C. the guard refuses, for each of the four classes -- exercised, not asserted
  D. the guard exits 0 on a clean index, so it can be wired into a hook
  E. `vendor/` is byte-identical to what is committed, and the upstream pin in
     LEGAL.md and README.md is the same 40 characters in both
  F. no CODE in this repository reaches into another project's checkout. Prose,
     docstrings and `journal/` may name it -- that is where the provenance lives
     -- but a default path may not. See `src/testing/nodep.py` for why the
     distinction is not made by `grep`.

PROVENANCE OF THE NUMBERS HERE

  * The four filename classes are the ones `tools/guard_staged.sh` matches, read
    out of the script rather than restated, so the two cannot drift.
  * `bf653a407cd97e4dfdca665063f25d8b44da130a` is the upstream commit LEGAL.md
    records for Eurocom's tree. It is a constant from a document in this
    repository, not from a cartridge, and check E compares the two documents
    against each other so a wrong pin cannot survive in one place.
  * The size ceiling is 512 KiB. The largest legitimate file in this repository
    is `PROVENANCE.md` at about 59 KiB and the largest generated thing is a
    128 KiB PRG image, neither of which is tracked; a cartridge is 256 KiB and a
    base64 one would be a third larger. So the ceiling is above everything real
    and below every way of smuggling a cartridge in.

WHAT IT DOES NOT CLAIM

  * **It cannot prove the cartridge was never committed in an earlier commit.**
    `git ls-files` sees HEAD, and `guard_staged.sh` sees the index; a blob that
    reached history would need `git log --all --diff-filter=A -- '*.nes'` to
    find, and that is a different and much slower question.
  * **It does not check the working tree for cartridge *content*,** only for
    cartridge *filenames*. A base64 blob called `notes.txt` would pass A and B.
    Check A's size ceiling is the backstop for that, and it is a backstop.
  * **It does not require the pre-commit hook to be installed.** A hook is a
    local file and is deliberately not committed; `make install-hooks` puts it
    there. What is required is that the guard WORKS, which is C and D.

WHAT IT NEEDS: python3, git, and a checkout. No emulator, no cartridge, no
movie, no display. Under a second.

Run:  python3 src/testing/test_repo_hygiene.py
"""

import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

os.chdir(ROOT)

GUARD = ROOT / "tools" / "guard_staged.sh"

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


def fnmatch_name(name, pattern):
    """Match `name` against ONE of the guard's own case patterns.

    The guard is a bash `case`, so its patterns are shell globs: `*` crosses `/`
    and a `vendor/` prefix is literal. `fnmatch` agrees with that on both counts
    and adds nothing, which is what is wanted -- a different matcher here would
    let a path through that the guard itself refuses.
    """
    return fnmatch.fnmatch(name, pattern)


def git(*args, index=None, check_rc=True):
    env = dict(os.environ)
    if index:
        env["GIT_INDEX_FILE"] = str(index)
    p = subprocess.run(["git", *args], capture_output=True, text=True, env=env,
                       timeout=120)
    if check_rc and p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
    return p


# =====================================================================================
# The classes, read out of the guard itself. Restating them here would let the two
# lists drift, which is the bug this whole project has: two places to forget.
# =====================================================================================
guard_src = GUARD.read_text(encoding="utf-8")
CLASSES = {}
for m in re.finditer(r"^\s{4}(\S+)\)(.*?)\s*;;\s*$", guard_src, re.M | re.S):
    classes = [c for c in m.group(1).split("|") if c and "*" in c]
    label = m.group(2).strip().rstrip(";")
    for c in classes:
        CLASSES.setdefault(c, label)

# The guard's patterns fall into two kinds, and conflating them is a bug:
#
#   NEVER IN THE TREE   a cartridge, a movie, a save, an archive, generated text,
#                       capture output. If one of these is in HEAD it is already
#                       too late and the guard cannot help.
#   NEVER *STAGED*      vendor/*, because Eurocom's files ARE tracked -- that is
#                       how the build reads them. What the guard refuses is a
#                       *change* to one, and check E covers the working tree.
#
# The split is stated here rather than inferred, because the guard itself cannot
# tell them apart: it only ever sees the index.
# `roms/*` is in .gitignore, not in the guard's case list: a directory that is
# ignored outright cannot be staged, so the guard has nothing to say about it.
NEVER_IN_TREE = ("*.nes", "*.unf", "*.unif", "*.unl", "*.fds", "*.nes2",
                 "*.nsf", "*.nsf2", "*.fm2", "*.fm2s", "*.bk2", "*.bkm", "*.gmv",
                 "*.vbm", "*.inp", "*.m64", "*.gmw", "*.vfr", "*.lsmv", "*.zip",
                 "*.7z", "pds-text/*", "tools/bizhawk/out/*", "shots/*", "out/*",
                 "*.sav", "*.srm", "*.srm2", "*.battery", "*.SaveRAM")
NEVER_STAGED = ("vendor/*",)
check("the guard's own case list was read out of the script, and it covers both "
      "kinds this file separates",
      set(NEVER_IN_TREE) <= set(CLASSES)
      and set(NEVER_STAGED) <= set(CLASSES)
      and not (set(NEVER_IN_TREE) & set(NEVER_STAGED)),
      f"parsed from the script: {sorted(CLASSES)}\n"
      f"         this file never-in-tree: {sorted(set(NEVER_IN_TREE) - set(CLASSES))} "
      f"missing\n"
      f"         this file never-staged:  {sorted(set(NEVER_STAGED) - set(CLASSES))} "
      f"missing\n"
      f"         `roms/*` is ignored by .gitignore rather than refused by the "
      f"guard, which is why it is not in either list.")

TRACKED = git("ls-files", "-z").stdout.split("\0")
TRACKED = [f for f in TRACKED if f]

# A. What git is tracking.
bad = [f for f in TRACKED if any(fnmatch_name(f, pat) for pat in NEVER_IN_TREE)]
check("git is tracking no cartridge, movie, save, archive or vendor path", not bad,
      f"tracked: {bad}\n         A cartridge is 256 KiB of bytes nobody here is "
      f"entitled to redistribute, and the movie is third-party and copyrighted.")

SIZE_CEILING = 512 * 1024
big = []
for f in TRACKED:
    try:
        n = (ROOT / f).stat().st_size
    except OSError:
        continue
    if n > SIZE_CEILING:
        big.append(f"{f} ({n} bytes)")
check(f"no tracked file is over {SIZE_CEILING // 1024} KiB", not big,
      "\n         ".join(big) + "\n         The largest legitimate file in this "
      "repository is a text document of a few tens of KiB, so anything this big "
      "is a cartridge, a movie, or a base64 of one.")

# vendor/ IS tracked -- Eurocom's own files, by name, are what the build reads.
# What must not be tracked is a *change* to it, which is check E.
check("vendor/ IS tracked, because the build reads Eurocom's files from it",
      any(f.startswith("vendor/") for f in TRACKED),
      "the rule is not 'never commit vendor/' but 'never modify it'. LEGAL.md "
      "records the upstream commit these files are byte-identical to.")

# B. What is in the tree but not tracked.
# `--exclude-standard` is what applies .gitignore; without it `git ls-files
# --others` lists ignored files too, and then every generated PRG and decoded
# source file in the tree reads as an untracked cartridge.
UNTRACKED = [f for f in git("ls-files", "-z", "--others",
                            "--exclude-standard").stdout.split("\0") if f]
bad_u = [f for f in UNTRACKED if any(fnmatch_name(f, pat) for pat in NEVER_IN_TREE)]
# .gitignore'd paths are expected to exist and must not be committed; they are
# listed separately so their presence is visible rather than silently tolerated.
IGNORED = [f for f in git("ls-files", "-z", "--others", "--ignored",
                          "--exclude-standard").stdout.split("\0") if f]
check("nothing untracked in the working tree is a cartridge, a movie or a save",
      not bad_u, f"untracked: {bad_u}")
# The rebuilt cartridge the assembler writes IS a .nes and IS present -- under
# `asm/out/`, which is ignored. That is the one legitimate case, and it is worth a
# check of its own because it is the shape a leaked cartridge would also have.
rebuilt = [f for f in IGNORED if f.endswith(".nes")]
check("the only .nes file in the tree is the generated rebuild under asm/out/, "
      "and it is ignored",
      all(f.startswith("asm/out/") for f in rebuilt)
      and git("check-ignore", "-q", "asm/out/magician-rebuilt.nes").returncode == 0,
      f"ignored .nes files present: {rebuilt}")
# Every dump of this title is 262160 bytes -- 16-byte header + 128 KiB PRG +
# 128 KiB CHR -- so SIZE PROVES NOTHING here. What distinguishes the rebuild is
# its bytes, and the registry already records a whole-file SHA1 for every dump.
# Comparing against the registry is a hash of a file this suite did not create,
# which is exactly what the rules allow: it is derived from a file in the
# repository, not from a previous session's report.
sys.path.insert(0, str(ROOT / "asm"))
import patches  # noqa: E402  (asm/patches.py)

registry = {e["sha1_full"]: n for n, e in patches.CARTS.items()}
import hashlib  # noqa: E402

same_size = []
for f in rebuilt:
    data = (ROOT / f).read_bytes()
    h = hashlib.sha1(data).hexdigest()
    if h in registry:
        same_size.append(f"{f} IS the {registry[h]} cartridge, byte for byte")
check("no generated .nes in the tree is byte-identical to any registered dump, "
      "compared by the whole-file SHA1 the registry records",
      not same_size,
      "\n         ".join(same_size) + "\n         Size cannot distinguish them: "
      "every dump and the rebuild are 262160 bytes.")

# =====================================================================================
# C+D. The guard, exercised. A synthetic index, so the real one is never touched and
#      no offending blob is ever written into the object database.
# =====================================================================================
TMP = Path("/tmp/opencode/magician-testing/hygiene")
TMP.mkdir(parents=True, exist_ok=True)
IDX = TMP / "index"


def guard_against(paths):
    """Run guard_staged.sh against an index that has `paths` staged."""
    if IDX.exists():
        IDX.unlink()
    git("rev-parse", "--git-dir")
    import shutil
    shutil.copyfile(ROOT / ".git" / "index", IDX)
    for path in paths:
        # A blob id for content that is one byte of text. Nothing is written to
        # the object database (`--info-only` below), so this leaves no trace in
        # .git and creates no object a later `git gc` could keep.
        blob = subprocess.run(["git", "hash-object", "--stdin"], input="x\n",
                              capture_output=True, text=True, timeout=60).stdout.strip()
        # --info-only: record the name without writing the blob into .git at all.
        git("update-index", "--add", "--info-only",
            "--cacheinfo", f"100644,{blob},{path}", index=IDX)
    env = dict(os.environ, GIT_INDEX_FILE=str(IDX))
    return subprocess.run([str(GUARD)], capture_output=True, text=True, cwd=str(ROOT),
                          env=env, timeout=120)


for path, why in (("Magician (USA).nes", "a cartridge"),
                  ("run.fm2", "the movie"),
                  ("nvram.srm", "a save file"),
                  ("roms.zip", "an archive, which could hold either"),
                  ("vendor/Magician-NES/MISC.SRC", "a modification of vendor/"),
                  ("pds-text/x0.pds", "generated source text"),
                  ("tools/bizhawk/out/frame.png", "capture output")):
    p = guard_against([path])
    check(f"the guard REFUSES {why} (`{path}`) with a non-zero exit",
          p.returncode == 1 and "FAIL" in p.stdout,
          f"exit {p.returncode}\n{p.stdout}\n{p.stderr}")

clean = guard_against([])
check("the guard exits 0 on a clean index, so it can be wired into a hook and "
      "left there",
      clean.returncode == 0, f"exit {clean.returncode}\n{clean.stdout}{clean.stderr}")

# The real index, right now. If something is staged that the guard would refuse,
# this fails -- which is the "fails if the .fm2 is ever staged" requirement, on the
# index a commit would actually use.
STAGED = [f for f in git("diff", "--cached", "--name-only",
                         "--diff-filter=ACDMRTUXB").stdout.splitlines() if f]
bad_s = [f for f in STAGED if any(fnmatch_name(f, pat)
                                       for pat in NEVER_IN_TREE + NEVER_STAGED)]
check("nothing staged RIGHT NOW would be refused by the guard",
      not bad_s,
      f"staged and offending: {bad_s}\n         The index a commit would use "
      f"currently holds: {STAGED}")

# =====================================================================================
# E. vendor/ is unmodified, and its pin is the same in both documents.
# =====================================================================================
vstatus = git("status", "--porcelain", "--", "vendor").stdout.strip()
check("git status vendor/ is empty -- Eurocom's tree is unmodified",
      vstatus == "", f"git status says:\n{vstatus}")

PIN = "bf653a407cd97e4dfdca665063f25d8b44da130a"
pins = {}
for doc in ("LEGAL.md", "README.md", "src/magician/README.md", "PROVENANCE.md"):
    found = re.findall(r"\b[0-9a-f]{40}\b", (ROOT / doc).read_text(encoding="utf-8"))
    pins[doc] = sorted(set(found))
check("every document that records vendor's pin records the SAME 40 characters",
      all(PIN in v for v in pins.values()),
      "\n         ".join(f"{d}: {v}" for d, v in pins.items()))
check("README.md names the upstream repository the pin belongs to, so the hash is "
      "resolvable rather than decorative",
      "Magician-NES" in (ROOT / "README.md").read_text(encoding="utf-8"),
      "a bare 40-character hash with no repository is not a pin")

# ...and no source file incbins or embeds cartridge bytes.
offenders = []
for f in TRACKED:
    if not f.endswith((".py", ".sh", ".lua", ".pds")):
        continue
    try:
        text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for pat in (r"incbin\s+[^\n]*\.nes", r"base64\.b64decode\s*\(\s*[\"'][A-Za-z0-9+/]{200,}"):
        if re.search(pat, text):
            offenders.append(f"{f}: {pat}")
check("no committed source incbins a cartridge or embeds a base64 blob",
      not offenders, "\n         ".join(offenders))

# =====================================================================================
# F. No code reaches into another project's checkout.
# =====================================================================================
# THE INCIDENT. A service in this project launched BizHawk out of a *different*
# project\'s emulator directory, and a `pkill` in that service killed a
# 136,526-frame replay belonging to that other project. Two checkouts shared one
# BizHawk directory with no lock on its config.ini or its NES/SaveRAM, and nothing
# in either project said so.
#
# WHY THIS IS NOT A GREP. `aibeatszelda` appears in dozens of files here and almost
# all of them are correct: honest credit for the MAIN/SCOUT split ported into
# `src/play/`, two real bugs found by reading that project\'s tests, and a
# `journal/` that is a truthful record. A grep that failed on those would get
# deleted, and then the check would be gone. So `nodep.py` strips comments and
# docstrings first and looks at what is left, which is the only part that can
# determine a path.
#
# WHAT IT DOES NOT CATCH, and the reason this is not the whole answer: a SYMLINK
# from this project\'s install into another project\'s would satisfy this check
# exactly as it satisfies `grep`. The install is a real copy for that reason, and
# `journal/14` records the measurement of what BizHawk actually writes -- six
# paths, none of them shared -- which is what makes the copy cheap enough to be
# the right answer rather than merely the cautious one.
import nodep as nd  # noqa: E402  (src/testing/nodep.py)

hits = nd.find_dependency_hits(ROOT)
check("no source file in this repository reaches into another project's checkout",
      not hits,
      "Code naming `" + nd.SIBLING + "`:\n"
      + "\n".join(f"{rel}:{ln}: {line}" for rel, ln, line in hits))

# ...and a POSITIVE CONTROL, without which the check above is vacuous: the stripper
# must actually be able to see a dependency. Written to a scratch file outside the
# tree and pointed at directly, because a fixture inside the repository would be a
# dependency by construction.
_scratch = TMP / "nodeptest"
_scratch.mkdir(parents=True, exist_ok=True)
(_scratch / "bad.sh").write_text(
    "BIZ=${BIZHAWK:-$HOME/code/games/" + nd.SIBLING + "/BizHawk}\n", encoding="utf-8")
(_scratch / "good.sh").write_text(
    "# mentions " + nd.SIBLING + " in a comment, which is provenance\n"
    "BIZ=${MAGICIAN_BIZHAWK:-$HOME/code/games/magician-nes-bizhawk}\n",
    encoding="utf-8")
check("the scan DOES see a dependency written the old way",
      [h for h in nd.find_dependency_hits(_scratch) if h[0] == "bad.sh"],
      "if this fails the scan is not looking, and 'no hits' above means nothing. "
      "A guard that cannot fail is not a guard -- the `[ x -lt 0x8000 ]` case in "
      "test_run_sh.py section F.")
check("...and does NOT flag the same name in a comment",
      not [h for h in nd.find_dependency_hits(_scratch) if h[0] == "good.sh"],
      "over-reporting is how a check like this gets deleted instead of fixed")

# And the provenance that must SURVIVE, asserted positively. A future session
# "tidying up" the sibling's name out of these files would be removing the record
# of where a design came from, and this is the check that says so.
prov = {
    "src/play/bridge.lua": "Ported from",
    "src/play/emu.py": "aibeatszelda",
    "src/play/runner.py": "aibeatszelda",
    "src/play/search.py": "aibeatszelda",
}
missing = [f for f, needle in prov.items()
           if needle not in (ROOT / f).read_text(encoding="utf-8")]
check("the provenance that credits the ported design is still there",
      not missing,
      "these are the honest credits for the MAIN/SCOUT split and the bridge, and "
      "this project values knowing where a thing came from. If one of these was "
      "removed on purpose, delete this check deliberately rather than because a "
      f"grep complained. Missing from: {missing}")

print(f"repo hygiene: {ok} checks")
print("all checks passed")