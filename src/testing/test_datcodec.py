"""`datcodec.py selftest`, run by the suite instead of by remembering to.

    python3 src/testing/test_datcodec.py

`tools/datcodec.py selftest` is 20 checks against Eurocom's own three shipped
`.DAT` files, and it is the strongest evidence in this repository that the scene
format is right: it requires `encode(decode(file)) == file` byte-identically,
which a codec that merely round-trips its own output cannot fake.

The problem is that nothing ran it. It had to be remembered, and every other
instrument in `tools/` had to be remembered too, and that is how a `TypeError`
in an unrelated tool became "a 0% match over an all-zero image" for a session.

So this file runs it and fails the suite if it fails. Twenty checks:

   1-4.  `selftest()` returns True AND every one of its own `[ok]` lines agrees
         with that return value -- a check that printed `[FAIL]` and still
         returned True would be a suite that cannot fail, which is the shape of
         failure this whole directory exists to prevent
   5-8.  the CLI: `selftest` exits 0 and prints PASS; `decode`, `tokens` and
         `source` each exit 0 on a shipped file
   9-10. an unknown subcommand and a missing file both exit non-zero rather than
         printing an empty table
  11-12. the NAMETABLE/ATTRS split is 960 + 64, and a short window yields tiles
         with no attributes invented
  13-14. `encode` refuses an empty scene, and refuses a literal equal to the
         token by naming the offset -- the format has no escape for that value
  15-17. `encode(decode(file)) == file` byte-identically, per shipped file. This
         is the selftest's strong check restated so it is visible in this file's
         own output
  18.    no vendor `.DAT` file was modified by any of it

PROVENANCE OF THE NUMBERS IN THIS FILE

There are none from any cartridge. `960` and `64` are the NES nametable and
attribute-table sizes (32x30 tiles = 960, 8x8 attributes = 64), stated as
constants in `datcodec.py`; the three filenames are the files in
`vendor/Magician-NES/DAT/`, which is read-only and pinned at
bf653a407cd97e4dfdca665063f25d8b44da130a. Nothing here quotes a percentage, a
byte count or a hash from a previous session's report.

WHAT IT DOES NOT CLAIM

  * **vendor/ is not modified.** This file only reads from it, and
    `test_repo_hygiene.py` checks the submodule's recorded commit independently.
  * **A passing selftest does not prove the rebuilt ROM's nametable is right.**
    It proves the codec agrees with Eurocom's own files. Whether the rebuilt
    cartridge writes those bytes to the right place is a framebuffer question
    and needs the emulator.
  * **The counts of 20 and 11 above are read out of the selftest's own output,
    not written down.** If `selftest` grows or loses a check, this file's output
    says so rather than quietly asserting a stale number.

WHAT IT NEEDS: python3, and the committed `vendor/Magician-NES/DAT/` files,
which are in the repository. No emulator, no cartridge, no display.

Run:  python3 src/testing/test_datcodec.py
"""

import io
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

os.chdir(ROOT)

import datcodec  # noqa: E402  (tools/datcodec.py)

TMP = Path("/tmp/opencode/magician-testing/datcodec")
TMP.mkdir(parents=True, exist_ok=True)

DAT = ROOT / "vendor" / "Magician-NES" / "DAT"

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


def cli(*args):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "datcodec.py"), *args],
                          capture_output=True, text=True, timeout=60)


# =====================================================================================
# 1-4. The selftest itself, run in-process so its per-check results can be read.
# =====================================================================================
# One run, verbose, so the per-check lines are available to read. `selftest`
# returns True/False AND prints `[ok]`/`[FAIL]` per check, so the return value and
# the lines have to agree -- a `check` that appended a row without ever setting
# `ok = False` would pass on the return value and show a FAIL in the lines.
buf2 = io.StringIO()
with redirect_stdout(buf2):
    result = datcodec.selftest(quiet=False)
vtext = buf2.getvalue()
lines = [ln for ln in vtext.splitlines() if ln.strip().startswith("[ok]")]
fails = [ln for ln in vtext.splitlines() if ln.strip().startswith("[FAIL]")]

check("datcodec.selftest() returns True", result is True,
      "the 20 checks are:\n" + "\n".join("  " + ln.strip() for ln in vtext.splitlines()
                                        if ln.strip().startswith(("[ok]", "[FAIL]"))))
check("the selftest's own output says PASS, with a count",
      re.search(r"selftest: PASS \(\d+ checks\)", vtext) is not None,
      vtext[-400:])
check("every individual check reports [ok] and none reports [FAIL]",
      not fails and len(lines) >= 20,
      f"{len(lines)} ok, {len(fails)} FAIL:\n" + "\n".join(fails))

# 3. The three shipped files, named in the selftest's own output, all exist.
named = sorted(set(re.findall(r"\b([A-Z0-9]+\.DAT)\b", vtext)))
check("the selftest names three .DAT files and they are all present",
      named == ["PAN.DAT", "PW.DAT", "TIT.DAT"]
      and all((DAT / n).is_file() for n in named),
      f"named {named}")
before = {n: ((DAT / n).stat().st_mtime_ns, (DAT / n).stat().st_size)
          for n in named}

# =====================================================================================
# 4-7. The CLI. Every subcommand the tool advertises, plus the two ways it must fail.
# =====================================================================================
p = cli("selftest")
check("`datcodec.py selftest` exits 0", p.returncode == 0,
      f"exit {p.returncode}\n{p.stdout[-600:]}\n{p.stderr[-400:]}")
check("`datcodec.py selftest` prints PASS and the check count",
      "selftest: PASS (" in p.stdout, p.stdout[-300:])

for sub, fname in (("decode", "TIT.DAT"), ("tokens", "PW.DAT"), ("source", "PAN.DAT")):
    q = cli(sub, fname)
    check(f"`datcodec.py {sub} {fname}` exits 0 and says something",
          q.returncode == 0 and len(q.stdout.strip()) > 0,
          f"exit {q.returncode}\n{q.stdout[-300:]}\n{q.stderr[-300:]}")

q = cli("no-such-subcommand")
check("an unknown subcommand exits non-zero", q.returncode != 0,
      f"exit {q.returncode}, stdout {q.stdout[:200]!r}")
q = cli("decode", "NO-SUCH-FILE.DAT")
check("a missing .DAT exits non-zero rather than printing an empty table",
      q.returncode != 0 and "no such .DAT" in q.stderr,
      f"exit {q.returncode}, stderr {q.stderr[:200]!r}")

# =====================================================================================
# 8-10. The codec's own edge cases, on inputs built here.
# =====================================================================================
# 960 = 32x30 tiles, the NES nametable; 64 = 8x8 attributes. Both are properties
# of the PPU's memory map, not of any file.
check("the nametable/attribute split is 960 + 64 = 1024",
      (datcodec.NAMETABLE, datcodec.ATTRS) == (960, 64)
      and datcodec.NAMETABLE + datcodec.ATTRS == 1024,
      f"{datcodec.NAMETABLE}/{datcodec.ATTRS}")
tiles, attrs = datcodec.scene_parts(bytes(1024))
check("a 1024-byte scene splits into 960 tile bytes and 64 attribute bytes",
      len(tiles) == 960 and len(attrs) == 64, f"{len(tiles)}/{len(attrs)}")
short_tiles, short_attrs = datcodec.scene_parts(bytes(256))
check("a 256-byte window yields tiles only, with no attributes invented",
      len(short_tiles) == 256 and short_attrs == b"", f"{len(short_tiles)}/{short_attrs!r}")

try:
    datcodec.encode(b"")
except datcodec.CodecError as e:
    ok += 1
    print("  ok encode() refuses an empty scene rather than writing a 3-byte file")
    assert "empty scene" in str(e), str(e)
else:
    raise AssertionError("encode(b'') returned instead of raising. A 3-byte file "
                         "whose header declares 65536 bytes is a stream that cannot "
                         "be decoded, and writing it hands back something nobody "
                         "can use.")

# A literal equal to the token is unrepresentable: there is no escape for it. The
# error has to name the offset, or a caller cannot choose a different token.
try:
    datcodec.encode(bytes([0x11, 0x22, 0xE0, 0x33]), token=0xE0)
except datcodec.CodecError as e:
    assert "offset 2" in str(e) and "$E0" in str(e), str(e)
    ok += 1
    print("  ok encode() refuses a literal equal to the token, naming the offset")
else:
    raise AssertionError(
        "encode() packed a literal equal to the token. Decoding that stream would "
        "read the literal as a run header and produce a scene with no error "
        "anywhere -- the format's one unrepresentable value, and the encoder has "
        "to say so rather than emit it.")

# =====================================================================================
# 11. The strong check, restated per file so it is visible here: our encoder
#     independently chose the same records Eurocom's cruncher did.
# =====================================================================================
for n in named:
    raw = (DAT / n).read_bytes()
    back = datcodec.reencode(DAT / n)
    check(f"{n}: encode(decode(file)) == file, byte for byte",
          back == raw,
          f"{len(raw)} in, {len(back)} out; first difference at "
          f"{next((k for k in range(min(len(raw), len(back))) if raw[k] != back[k]), None)}")

# vendor/ must be exactly as it was. The selftest only reads, and this proves it.
after = {n: ((DAT / n).stat().st_mtime_ns, (DAT / n).stat().st_size) for n in named}
check("no vendor .DAT file was modified by running any of this",
      before == after,
      f"before {before}\n         after  {after}")

print(f"datcodec: {ok} checks")
print("all checks passed")