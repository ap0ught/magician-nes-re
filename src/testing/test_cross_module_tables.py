"""A data symbol must name an address whose BYTES are that table.

    python3 src/testing/test_cross_module_tables.py

THE BUG
-------
`X2.PDS` defines the compressed-text tables and `X5.PDS`'s `decompchr` reads them
by absolute address. In the cartridge:

    x5.pds:9-14    decompchr  ldy r7 / sty t2 / fbnk 7,#1,n    <- bank slot 1 in
                   ...
    x5.pds:31      !b        cmp codesl,x                       <- then read it
    x2.pds:1118    CODESL    HEX 06010B0908060100...
    x2.pds:1120    CODESH    HEX 0000...0101
    x2.pds:1123    ORBYTE    HEX 20454F5441534E52...           (" NOTASNR IHLUY")

`fbnk 7,#1,n` means the tables have to be inside **slot 1**, presented at
`$A000-$BFFF`. Beta 1 has them there, and its own layout puts `codesl` at `$B8AA`.

Our rebuild has `codesl` at **`$C9EA`**. `$C9EA` is in the `$C000-$DFFF` window,
which on this cartridge is **fixed to slot $0E** -- X5's own window -- and the
bytes there are not the table:

    ours  $C9EA -> 4F 54 49 4F 4E 20 B8 20    "OTION " + $B8 + ' '  (message text)
    beta1 $B8AA -> 06 01 0B 09 08 06 01 00    the real code-length table

So `decompchr` searched a table of message-string bytes, found no 3-bit code in
it, eight times, and fell through to `orbyte,x` -- which was equally wrong. That
is `phase=3(g03) curlev=$E0`: the inventory is the first level whose `idata` runs
`ishop -> isa -> addcmsg`, and `addcmsg` is the first thing in the game that
decompresses text.

WHY IT IS A PLACEMENT BUG AND NOT A BAD NUMBER
----------------------------------------------
Measured 2026-10-06 by instrumenting `Assembler.run_file`:

    X2.PDS slot=1 origin=$A000: 10861 bytes emitted, per-slot {1: 8192, 14: 2669}

**X2.PDS is 10 861 bytes and an MMC3 slot is 8 192.** With its base at `$A000` the
last 2 669 bytes run into `$C000-$DFFF`, and `Assembler.prg_offset` files that
spill in the *fixed* window -- slot 14, X5's. All five table labels land in that
spill, so all five resolve to addresses another module owns. X5 is assembled after
X2 and overwrites the spill with its own bytes, which is why `asm/out/mag.sym` is
self-consistent and the `$C000` window is only 137 bytes off: the symbols are
DANGLING, and nothing in the build asks whether a symbol's address holds what the
symbol names. Two of the three literal tables are not in the image at all.

That is the whole reason this file exists. Every other check in this repository
asks whether a symbol's *value* is right. This one asks whether the bytes at that
value are the thing the symbol is a label for.

WHY A TEST AND NOT A COMMENT
----------------------------
A dangling label is invisible by construction: the build is self-consistent, the
byte-match percentage is high, the symbol file looks reasonable, and the game
boots to a title screen and a playable town. It cost a session to find and will
cost another the next time a module's footprint moves. A comment does not stop it.

COVERAGE, AND WHY THE CHECK IS NOT VACUOUS
------------------------------------------
A "does the table appear at the symbol" check is worthless if the signature it
looks for is not distinctive or the window arithmetic is wrong. So this file pins
its own instruments before it uses them:

  * **check 1a/1b** fail if any table's statement kind or declared bytes cannot be
    read, so a moved line or a changed spelling stops the run with exit 2 instead
    of leaving the content checks silently matching nothing.
  * **check 1c** fails if one signature is a prefix of another.
  * **check 1d** asserts the declared exclusion list `WINDOW_ONLY` is exactly the
    set of tables whose bytes cannot identify them. `CODESH` is thirty zeros then
    `01 01 01`; this file's first run called two of X5's `sql`/`sqh` tables a
    match. If CODESH ever becomes distinguishable the file says so.
  * **checks 2-4** pin the MMC3 window rule every offset below is derived with,
    from the cartridge's own MMC3 note, and assert a `$C000-$DFFF` address is never
    treated as slot-relative. This is the third PRG-offset slip in this project;
    the rule is written out rather than assumed.
  * **check 5** asserts the labels are read by `X5.PDS` and not defined there, so
    this really is a cross-module reference and a future move of the tables is not
    silently asserted away.

WHAT IT DOES NOT CLAIM

  * **No cartridge is read.** Every ROM byte below comes from `asm/out/prg.bin`,
    which `asm/build.py` writes. No dump's mtime can change and no battery save can
    be written. Beta 1's addresses are quoted in the docstring as *measurements*
    taken on 2026-10-06, not recomputed here, so this file passes on a machine
    with no dumps at all.
  * **It does not fix anything.** It reports which window and which slot each label
    lands in and whether that window holds the table, and exits non-zero. Deciding
    where X2's 2 669-byte tail belongs is a placement question with evidence
    requirements, and a guess there is worse than the bug.
  * **Checks that need `asm/out/` say so loudly when it is absent**, following
    `test_pointer_widths.py`. "Skipped" is never printed as a pass.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

VENDOR = ROOT / "vendor" / "Magician-NES"
SYM = ROOT / "asm" / "out" / "mag.sym"
PRG = ROOT / "asm" / "out" / "prg.bin"

SLOT_BYTES = 0x2000

# X5.PDS:10-21, the cartridge's own MMC3 note:
#   $8000-$9FFF : register 6 -- any of slots $00..$0D
#   $A000-$BFFF : register 7 -- any of slots $00..$0D
#   $C000-$DFFF : slot $0E, whatever the registers say
#   $E000-$FFFF : slot $0F, whatever the registers say
# So on THIS cartridge the fixed pair is 14/15 and everything else is
# switchable. The load-bearing consequence, and the reason this file re-derives
# every offset instead of adding $10000: an address in `$C000-$DFFF` is not
# "file offset $C000 plus the slot number", it is slot 14 at offset `addr & $1FFF`.
WINDOWS = (
    (0x8000, 0x9FFF, "R6", None),
    (0xA000, 0xBFFF, "R7", None),
    (0xC000, 0xDFFF, "fixed slot 14", 14),
    (0xE000, 0xFFFF, "fixed slot 15", 15),
)

# label -> (vendor file, the directive that defines it)
TABLES = {
    "codesl": ("X2.PDS", "hex"),      # X2.PDS:1118  CODESL HEX 06 01 0B 09 ...
    "codesh": ("X2.PDS", "hex"),      # X2.PDS:1120  CODESH HEX 00 ... 01 01 01
    "orbyte": ("X2.PDS", "hex"),      # X2.PDS:1123  ORBYTE HEX 20 45 4F 54 ...
    "mptrl": ("X2.PDS", "dl"),        # X2.PDS:1135  MPTRL DL M00
    "mptrh": ("X2.PDS", "dh"),        # X2.PDS:1152  MPTRH DH M00
}

# `CODESH HEX 00 00 ... 00 01 01 01` is thirty zero bytes and three ones, so there
# is no way to identify it by its own content. It is checked for the WINDOW it
# resolves into -- the part that is actually broken -- and not for content.
# `mptrl`/`mptrh` are `dl`/`dh` pointer lists with no literal bytes at all.
# Both exclusions are asserted in check 1d, so neither can quietly become stale.
WINDOW_ONLY = ("codesl", "orbyte")          # content-checked, and the only two
NO_CONTENT = ("codesh", "mptrl", "mptrh")   # with nothing to compare against

fails = 0


def ok(cond: bool, label: str) -> None:
    global fails
    if cond:
        print(f"  ok   {label}")
    else:
        fails += 1
        print(f"  FAIL {label}")


def die(msg: str) -> None:
    """The instrument is broken, so nothing below it means anything. Exit 2, which
    is distinct from exit 1 so a caller can tell 'the ROM is wrong' from 'this
    test cannot run'."""
    print(f"  FAIL {msg}")
    print("\nFAILED -- the instrument is broken, so nothing below it means anything.")
    sys.exit(2)


# --------------------------------------------------------------------- the rule
def window_of(cpu: int) -> tuple[str, int | None]:
    """Which MMC3 window a CPU address is presented in, and the SLOT if fixed."""
    for lo, hi, name, slot in WINDOWS:
        if lo <= cpu <= hi:
            return name, slot
    raise ValueError(f"${cpu:04X} is not a PRG address on this cartridge")


def prg_file_offset(cpu: int, r6_slot: int, r7_slot: int) -> tuple[int, int]:
    """PRG file offset for a CPU address, and the slot it came from.

    `r6_slot`/`r7_slot` apply only to the two switchable windows, and they are
    arguments rather than globals so a caller has to state them -- which is what
    stops the MMC1 habit of deriving a slot from the address.
    """
    name, slot = window_of(cpu)
    if name == "R6":
        slot = r6_slot
    elif name == "R7":
        slot = r7_slot
    if slot is None:
        raise ValueError(f"${cpu:04X} is in the {name} window and no slot was given")
    return slot * SLOT_BYTES + (cpu & 0x1FFF), slot


# ------------------------------------------------------- read the tables' bytes
def vendor_text(name: str) -> list[str]:
    from pds_extract import decode  # noqa: PLC0415 -- sys.path is set above
    return decode((VENDOR / name).read_bytes()).replace("\r", "\n").split("\n")


def declared_bytes(lines: list[str], label: str) -> bytes:
    """The literal bytes a `HEX ...` statement declares."""
    for ln in lines:
        if re.match(rf"^\s*{label}\s+hex\b", ln, re.I):
            return bytes.fromhex(ln.split(None, 2)[2].split(";")[0].replace(" ", ""))
    return b""


x2 = vendor_text("X2.PDS")
x5 = vendor_text("X5.PDS")

# ------------------------------------------------- checks 1a-1d: the instruments
absent = [n for n, (_f, kind) in TABLES.items()
          if not any(re.match(rf"^\s*{n}\s+{kind}\b", ln, re.I) for ln in x2)]
die(f"no table directive found for {absent} in X2.PDS -- the directive or the "
    f"spelling moved, and every content check below would be vacuous"
    ) if absent else None

sigs = {n: declared_bytes(x2, n) for n in WINDOW_ONLY}
unstated = [n for n, s in sigs.items() if len(s) < 4]
die(f"the scan found no declared bytes for {unstated}, but they are in "
    f"WINDOW_ONLY which claims they DO have literal content") if unstated else None
ok(True, f"check 1a: all {len(TABLES)} tables found in X2.PDS with the directive "
         f"kind this file claims; {len(WINDOW_ONLY)} have literal bytes "
         f"({', '.join(WINDOW_ONLY)}), {len(NO_CONTENT)} do not "
         f"({', '.join(NO_CONTENT)})")

prefixes = [(a, b) for a in sigs for b in sigs
            if a < b and sigs[b][:len(sigs[a])] == sigs[a]]
die(f"these signatures are prefixes of each other, so finding one proves nothing: "
    f"{prefixes}") if prefixes else None
ok(True, f"check 1b: all {len(sigs)} signatures are "
         f"{min(len(s) for s in sigs.values())}-{max(len(s) for s in sigs.values())}"
         f" bytes and none is a prefix of another")


def degenerate(sig: bytes) -> bool:
    """True when the bytes cannot identify the table: one value dominates."""
    return (len(set(sig)) <= 2
            and max(sig.count(b) for b in set(sig)) > len(sig) * 0.8)


# a table is excluded from a content check if its directive emits no literal
# bytes at all (`dl`/`dh`), or if the literal bytes cannot identify it
hex_tables = [n for n, (_f, k) in TABLES.items() if k == "hex"]
measured_excluded = sorted(
    [n for n in hex_tables if degenerate(declared_bytes(x2, n))]
    + [n for n, (_f, k) in TABLES.items() if k != "hex"])
ok(measured_excluded == sorted(NO_CONTENT),
   f"check 1c: the set of tables this file excludes from a content check is "
   f"exactly the set that cannot be content-checked ({NO_CONTENT}): CODESH is "
   f"{len(declared_bytes(x2, 'codesh'))} bytes of which "
   f"{declared_bytes(x2, 'codesh').count(0)} are zero and the rest is `01 01 01`, "
   f"so 'found' would match any zero run -- and the first run of this file matched "
   f"two of X5's own `sql`/`sqh` tables and called it a hit"
   if measured_excluded == sorted(NO_CONTENT) else
   f"check 1c: declared exclusions {sorted(NO_CONTENT)} but measured "
   f"{measured_excluded} -- one of the exclusions no longer holds, or a table "
   f"that was content-checked has become unidentifiable")

prg = PRG.read_bytes() if PRG.exists() else None
if prg is not None:
    for name in WINDOW_ONLY:
        hits = [i for i in range(len(prg)) if prg.startswith(sigs[name], i)]
        print(f"       {name:7} signature occurs {len(hits)}x in asm/out/prg.bin "
              f"({len(sigs[name])} bytes) at "
              f"{' '.join(f'${h:05X}' for h in hits[:4]) or 'NOWHERE'}")
else:
    print("  --   prg not read: asm/out/prg.bin does not exist (run `make rom`)")

# ------------------------------------------------- checks 2-4: the window rule
ok(window_of(0x8000) == ("R6", None), "check 2a: $8000 is the R6 window")
ok(window_of(0xBFFF) == ("R7", None), "check 2b: $BFFF is the R7 window")
ok(window_of(0xC000) == ("fixed slot 14", 14), "check 2c: $C000 is fixed slot 14")
ok(window_of(0xFFFF) == ("fixed slot 15", 15), "check 2d: $FFFF is fixed slot 15")
try:
    window_of(0x7FFF)
    ok(False, "check 3: an address below $8000 RAISES rather than being filed")
except ValueError:
    ok(True, "check 3: an address below $8000 RAISES rather than being filed")

off, slot = prg_file_offset(0xC9EA, r6_slot=0, r7_slot=1)
ok(slot == 14 and off == 14 * SLOT_BYTES + 0x09EA,
   f"check 4: $C9EA files at ${off:05X} in slot {slot} -- the fixed window, NOT "
   f"$C9EA-as-a-file-offset and NOT slot-relative $C9EA. This is the third "
   f"PRG-offset slip in this project and the arithmetic is written out, not "
   f"assumed")

# -------------------------------- check 5: this is a cross-module reference
used = [n for n in TABLES if re.search(rf"\b{n}\b", "\n".join(x5), re.I)]
ok(sorted(used) == sorted(TABLES),
   f"check 5a: X5.PDS reads all {len(TABLES)} of them ({len(used)}/{len(TABLES)})")
local = [n for n in TABLES
         if re.search(rf"^\s*{n}\s+(hex|dl|dh)\b", "\n".join(x5), re.I | re.M)]
die(f"{local} is now DEFINED in X5.PDS too; the cross-module premise of this file "
    f"is stale and its assertions would be asserting the wrong thing") if local else None
ok(True, "check 5b: none of them is defined in X5.PDS, so this is still a "
         "cross-module reference")

# ---------------------------------------- the actual check, on our own build
if prg is None or not SYM.exists():
    print("  --   checks 6-8 SKIPPED: asm/out/{prg.bin,mag.sym} not both present "
          "(run `make rom`). Not a pass: not run.")
else:
    table = {}
    for ln in SYM.read_text().splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            try:
                table[k.strip()] = int(v.strip().lstrip("$"), 16)
            except ValueError:
                pass
    gone = [n for n in TABLES if n not in table]
    die(f"our own mag.sym has no {gone}, so the build no longer defines them and "
        f"this file is asserting about a build that does not exist") if gone else None

    # Beta 1's measured values, 2026-10-06. Quoted, not recomputed.
    BETA1 = {"codesl": 0xB8AA, "codesh": 0xB8CB, "orbyte": 0xB8EC,
             "mptrl": 0xB90D, "mptrh": 0xB91D}

    dangling, unplaceable = [], []
    for name in TABLES:
        cpu = table[name]
        try:
            off, slot = prg_file_offset(cpu, r6_slot=0, r7_slot=1)
        except ValueError as exc:
            print(f"       {name:7} ${cpu:04X}  {exc}")
            unplaceable.append(name)
            continue
        if name in WINDOW_ONLY:
            good = prg[off:off + len(sigs[name])] == sigs[name]
            how = "HOLDS THE TABLE" if good else "does NOT hold it"
            extra = f"   bytes {prg[off:off + 8].hex(' ')}"
        else:
            good = True
            how = "no content to compare (see WINDOW_ONLY / NO_CONTENT)"
            extra = ""
        print(f"       {name:7} ${cpu:04X}  window {window_of(cpu)[0]:<15} "
              f"file ${off:05X} slot {slot:<2}  {how}{extra}"
              f"   beta1 had ${BETA1[name]:04X}")
        if not good:
            dangling.append(name)

    ok(not (dangling or unplaceable),
       f"check 6: every content-checked table's label names an address holding "
       f"that table ({len(WINDOW_ONLY) - len(dangling)}/{len(WINDOW_ONLY)}"
       + (f"; DANGLING: {dangling}" if dangling else "") + ")")

    fixed = [n for n in TABLES if window_of(table[n])[1] is not None]
    ok(not fixed,
       (f"check 7: {len(fixed)} of {len(TABLES)} table labels resolve into a "
        f"FIXED window: {fixed}. `decompchr` does `fbnk 7,#1,n` before reading "
        f"them and banks slot 1 back out at `gotit`, so all {len(TABLES)} are "
        f"reached through the R7 window -- a label in a fixed window is a label "
        f"pointing into a slot another module owns"
        if fixed else
        f"check 7: all {len(TABLES)} table labels resolve into a switchable "
        f"window, which is the only thing `fbnk 7,#1,n` can bank in"))

    lost = [n for n in WINDOW_ONLY if sigs[n] not in prg]
    ok(not lost,
       f"check 8: every content-checked table's bytes are present SOMEWHERE in the "
       f"128 KiB image ({len(WINDOW_ONLY) - len(lost)}/{len(WINDOW_ONLY)}"
       + (f"; NOT EMITTED AT ALL: {lost}" if lost else "") + "). A label can be "
       f"dangling because the table moved OR because the table was never "
       f"emitted, and the two want opposite fixes")

print()
if fails:
    print(f"FAILED -- {fails} check(s)")
    sys.exit(1)
print("all checks passed")
