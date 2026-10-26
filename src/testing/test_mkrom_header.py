"""The rebuild's iNES header must describe the build it is a rebuild OF.

    python3 src/testing/test_mkrom_header.py

THE BUG THIS EXISTS FOR
-----------------------
`asm/mkrom.py` hardcoded header byte 6 as `$42`, and the comment beside it said
"the two dumps on this machine share it byte for byte". That was true when the
registry was `release` and `beta` -- both are `$42` -- and the target then moved
to `beta1`, which is the only one of the six dumps with `$40`. The literal was
never revisited, so the rebuild went on declaring a battery.

Bit 1 of byte 6 is what `tools/bizhawk/identity.py` reads and what BizHawk
behaves on. With `$42`, BizHawk created `NES/SaveRAM/magician-rebuilt.SaveRAM`
and then **resumed it** on the next launch, so a run advertised as being from
power-on was in fact starting from a saved game. `tools/bizhawk/run.sh` refused
to launch rather than deleting the file, which is the guard working; the guard
can only refuse, so the header itself had to be right.

This is the same failure as a loader that synthesises an assumed header instead
of reading the cartridge's: the bytes are plausible, the ROM boots, the title
screen is pixel-perfect against Beta 1, and nothing says the machine it is
running on is not the machine it is supposed to be.

WHAT IS PINNED
--------------
   1-3.  the header is a well-formed 16-byte iNES header naming mapper 4 with
         128 KiB PRG and 128 KiB CHR, and the rebuilt PRG and CHR are exactly
         those sizes
   4.    **byte 6 == Beta 1's byte 6** -- the one fact that is a property of the
         revision being reconstructed rather than of the mapper
   5.    byte 6's battery bit (bit 1) is CLEAR in our header and SET in all five
         other dumps. Both directions are checked, because an assertion that only
         ever says "clear" would also pass against a header with the bit missing
         entirely
   6.    our header's battery bit agrees with the TARGET's, not with "whatever
         the majority of the registry says" -- beta1 is 1-of-6, so a majority
         rule would get this wrong
   7.    the stale `magician-rebuilt.SaveRAM` still on disk cannot be resumed by
         this ROM, because a core with its battery bit clear never looks for one.
         Stated as a property of the header bit, because that is what it is; the
         file is NOT deleted and nothing in NES/SaveRAM is written or touched
   8-9.  `run.sh`'s battery guard agrees: its own battery test, applied to our
         header, must come out CLEAR, and applied to the release's must come out
         SET. The guard is parsed out of the script rather than restated, so it
         cannot drift from the script
  10.    every registered dump's header is self-consistent: mapper 4, 128 KiB PRG,
         128 KiB CHR. If a future dump does not agree, checks 4-6 are comparing
         against a different machine and must not be trusted silently

WHAT IT DOES NOT CLAIM

  * **Nothing here says BizHawk behaves as `identity.py` reads the bit.** It
    cannot: the suite has no emulator. What it does say is that the two agree
    about the header, so a disagreement is a disagreement between two pieces of
    evidence rather than an accident. The BizHawk half was measured by running
    it -- see journal/12.
  * **It reads cartridge headers, and nothing else.** No PRG or CHR byte is read
    and none could be: `tools/bizhawk/synthcart.py`-style synthesis is not used
    here because these dumps are on disk and the point is to compare against them.
"""

import os
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT / "asm"))

import patches  # noqa: E402

_fails = 0


def built_rom() -> pathlib.Path:
    return _ROOT / "asm" / "out" / "magician-rebuilt.nes"


def header_from_source() -> bytes:
    """`INES_HEADER` read out of asm/mkrom.py AS TEXT.

    Deliberately not `import mkrom`. While this file was being written it did
    import it, and reported $42 -- the value from before the edit -- against a
    mkrom.py that plainly said $40. The cause is worth recording: restoring a file
    with `cp` inside the same second leaves its integer mtime and its byte count
    unchanged, so `asm/__pycache__/mkrom.cpython-314.pyc` stays valid for the
    OLD source and the import silently answers about a file that is no longer
    there. A test that pins a literal is exactly the test that must not trust an
    import of it.
    """
    text = (_ROOT / "asm" / "mkrom.py").read_text(encoding="latin-1")
    m = re.search(r"INES_HEADER\s*=\s*bytes\(\[(.*?)\]\)", text, re.S)
    assert m, "asm/mkrom.py has no INES_HEADER = bytes([...]) to read"
    body = m.group(1)
    vals = [int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{2})\b", body)]
    assert len(vals) == 16, f"INES_HEADER parsed as {len(vals)} bytes, not 16"
    return bytes(vals)


HEADER = header_from_source()


def ok(what: str, got=None) -> None:
    global _fails
    if what is None:
        print(f"  ok   {what}")
    elif isinstance(got, bool):
        if got:
            print(f"  ok   {what}")
        else:
            _fails += 1
            print(f"  FAIL {what}")
    else:
        print(f"  ok   {what}: {got}")


def hdr_of(name: str) -> bytes:
    """The registered dump's own 16-byte iNES header, read from disk.

    `patches.cart_path` is used rather than a path written here, so this file
    contains no cartridge path and the registry stays the one place that knows
    where the dumps are. A missing dump RAISES: a header check that quietly read
    zero bytes would compare a synthesised header against a real one and pass.
    """
    path = pathlib.Path(patches.cart_path(name))
    head = path.read_bytes()[:16]
    assert len(head) == 16, f"{path} is shorter than a header"
    assert head[:4] == b"NES\x1a", f"{path} does not start with an iNES header"
    return head


def mapper(h: bytes) -> int:
    return (h[7] & 0xF0) | (h[6] >> 4)


def prg_len(h: bytes) -> int:
    return h[4] * 16384


def chr_len(h: bytes) -> int:
    return h[5] * 8192


def battery(h: bytes) -> int:
    """Bit 1 of byte 6 -- what tools/bizhawk/identity.py reads."""
    return 1 if h[6] & 0x02 else 0


# ------------------------------------------------------- 1-3: the header's shape
ok("check 1: the header is 16 bytes and says 'NES\\x1a'",
   len(HEADER) == 16 and HEADER[:4] == b"NES\x1a")
ok("check 2: header names mapper 4 (MMC3), 128 KiB PRG and 128 KiB CHR",
   mapper(HEADER) == 4 and prg_len(HEADER) == 131072 and chr_len(HEADER) == 131072)
text = (_ROOT / "asm" / "mkrom.py").read_text(encoding="latin-1")
ok("check 3: mkrom.py's own PRG_SIZE/CHR_SIZE are the sizes the header declares, so "
   "the header is not claiming a machine the build does not produce",
   re.search(r"PRG_SIZE\s*=\s*128 \* 1024", text) is not None
   and re.search(r"CHR_SIZE\s*=\s*128 \* 1024", text) is not None
   and prg_len(HEADER) == 128 * 1024 and chr_len(HEADER) == 128 * 1024)

# ------------------------------- 3b: and the ROM ON DISK carries that same header
# The literal is intent; this is the artifact BizHawk is handed. A header that is
# correct in mkrom.py and wrong in the built ROM is a build that did not rerun, and
# it is the second one that runs the game. Loud when the ROM is absent -- not a
# pass, not a skip.
rom = built_rom()
if not rom.exists():
    print(f"  --   check 3b NOT RUN: {rom.relative_to(_ROOT)} does not exist "
          "(run `make rom`). Not a pass.")
else:
    on_disk = rom.read_bytes()[:16]
    ok(f"check 3b: the built ROM's own first 16 bytes are byte-for-byte the header "
       f"in mkrom.py, and it is {rom.stat().st_size} bytes = 16 + 128 KiB PRG + "
       f"128 KiB CHR -- so what BizHawk loads is the header that was checked",
       on_disk == HEADER and rom.stat().st_size == 16 + 131072 + 131072)

# ---------------------------------------------- 4-7: byte 6, against the dumps
target = patches.DEFAULT_CART
th = hdr_of(target)
ok(f"check 4: header byte 6 is ${HEADER[6]:02X} and {target}'s own byte 6 is "
   f"${th[6]:02X} -- the rebuild declares the machine of the revision it rebuilds",
   HEADER[6] == th[6])
others = {n: hdr_of(n) for n in patches.CARTS if n != target}
set_in_others = sorted(n for n, h in others.items() if battery(h))
ok(f"check 5: our battery bit is CLEAR and it is SET in the other "
   f"{len(others)} dumps ({', '.join(set_in_others)}). Both directions: an "
   f"assertion that only ever said 'clear' would also pass on a header with no "
   f"battery bit in it",
   battery(HEADER) == 0 and len(set_in_others) == len(others))
ok(f"check 6: the battery bit agrees with the TARGET ({target}), not with the "
   f"majority of the registry -- {target} is 1 of {len(patches.CARTS)}, so a "
   f"majority rule would pick the wrong answer here",
   battery(HEADER) == battery(th) != (len(set_in_others) > len(others) // 2))
ok("check 7: the stale NES/SaveRAM/magician-rebuilt.SaveRAM cannot be resumed by "
   "this ROM, because a core whose header battery bit is clear keeps no save "
   "state to resume. The file is NOT deleted and NES/SaveRAM is not written to "
   "or read from by this suite -- this is a statement about the header bit",
   battery(HEADER) == 0)

# ------------------------------------------ 8-9: run.sh's own battery test agrees
#
# Read out of the script rather than restated, so a change to run.sh that makes it
# consult something else fails here instead of quietly disagreeing with a claim
# this file makes about the guard.
run_sh = (_ROOT / "tools" / "bizhawk" / "run.sh").read_text(encoding="latin-1")
ok("check 8: run.sh reads HAS_BATTERY out of identity.py's KEY=VALUE output rather "
   "than computing or asserting it",
   re.search(r"^\s*battery\)\s+HAS_BATTERY=", run_sh, re.M) is not None
   and "identity.py" in run_sh)
ok("check 9: run.sh branches on that bit -- CLEAR does nothing, SET refuses rather "
   "than deleting -- and applied to OUR header the branch taken is 'nothing', while "
   "the release's would take 'refuse'",
   battery(HEADER) == 0
   and battery(hdr_of("release")) == 1
   and re.search(r'if \[ "\$HAS_BATTERY" = "0" \]; then', run_sh) is not None
   and 'HAS_BATTERY" = 1 ] && echo SET' in run_sh
   and "exit 4" in run_sh)

# --------------------------------------- 10: every dump is the same machine
bad = [n for n in patches.CARTS
       if not (mapper(hdr_of(n)) == 4
               and prg_len(hdr_of(n)) == 131072 and chr_len(hdr_of(n)) == 131072)]
ok(f"check 10: all {len(patches.CARTS)} registered dumps are mapper 4 with 128 KiB "
   f"PRG and 128 KiB CHR, so checks 4-6 are comparing headers of the same machine",
   not bad)

# --------------------------------------------------- the file ran every check
ok("the file ran every check above", _fails == 0)
if _fails:
    print(f"\n{_fails} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")