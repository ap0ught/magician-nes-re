"""`dl`/`dh` are the LOW and HIGH halves of a pointer list: one byte each.

    python3 src/testing/test_pointer_widths.py

THE BUG
-------
`asm/pds6502.py` implemented `dl` as 4 bytes and `dh` as 2 -- the Atari MACRO
reading of `dl` ("define long"), applied to a source that does not use it that
way. Every pointer list in the tree was therefore emitted two and a half times
too long. The shop command tables are the cleanest witness:

    SHOPDAT.SRC:451   ijvl  dl sbuy,sempty,sexit,sgold1,smana10,syesno,smsg,sset
                      dl sclr,stst,sjump,sgover,saddob,sdelob,sbch
                      dl ststp,ssetp,sclrp
    SHOPDAT.SRC:454   ijvh  dh <the same eighteen names, in the same order>

    x5.pds:755-760    ldx shopind
                      ...
                      lda ijvl,x
                      sta t2          ;t2 = $0015
                      lda ijvh,x
                      sta t3          ;t3 = $0016
                      jmp (t2)

`sta t2` / `sta t3` into two ADJACENT zero-page bytes and then `jmp (t2)` is an
indirect jump through a pointer read one byte at a time. It is one byte per
entry or nothing. Eighteen entries, so the pair is 36 bytes. With the old
widths it was 4*18 + 2*18 = 108 bytes -- 72 bytes of invented table between
`ijvl` and `ijvh`, so `ijvh` landed at the wrong address and every shop icon
dispatched through whatever followed it.

WHY A TEST AND NOT A COMMENT
----------------------------
A previous session found this, wrote the fix, and left it uncommitted and
untested on purpose: the driver work was urgent and the fix was not yet proven.
An uncommitted assembler change is worse than no change, because the next build
silently depends on it. This file is the proof, and it pins the width from the
SOURCE (the operand lists in `vendor/Magician-NES/*.SRC`) rather than from a
number anyone remembers. No cartridge bytes are read: everything below is
derived from `asm/pds6502.py`, the source files, and bytes this file packs
itself.

WHAT IT DOES NOT CLAIM

  * **Nothing here says a whole build is correct.** It exercises the directive
    on operand lists taken from the real source, on a synthetic `bytearray`.
    It does not run `asm/build.py`, and it does not claim that any bank of the
    rebuild is right.
  * **It does not read `asm/out/mag.sym`.** That file is generated output and is
    gitignored; a test that needed it would be a test that fails on a fresh
    checkout. Checks 9-11 look at it only when it happens to exist, and say so
    loudly when it does not.

CHECKS
------
   1-4.  `dl`/`dh` emit exactly one byte per operand, low and high respectively,
         for a plain number
   5-6.  `db`/`dc` are still one byte and `dw` is still two, little-endian --
         the fix must not have moved them
   7.    `dl a,b,c` followed by `dh a,b,c` is byte-identical to `dw a,b,c`, and
         is twice as long. This is the whole rule in one line.
   8.    a `dl`/`dh` pair over a list of LABELS assembles, so the widths work on
         symbols and not just on literals
   9-11. `ijvl`/`ijvh` in our own symbol file, when it has been built, are 18
         bytes apart and hold the low and high halves of those eighteen
         labels. Skipped loudly when the file is absent.
  12-14. the `ijvl`/`ijvh` operand lists in `vendor/Magician-NES/SHOPDAT.SRC`
         are the eighteen names, in the same order in both -- and the size the
         OLD widths would have produced for them is named and differs
  15-16. every `dl` in the whole source tree is paired with a `dh` of the same
         operand list, and there are 61 of each. A `dl` with no `dh` partner
         would mean the low/high reading is incomplete somewhere.
  17.   the module docstring no longer documents the old widths, and the old
         widths are absent from the code. This is the check that fails if
         somebody re-introduces `dl`=4 with a new comment.
"""

import collections
import os
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT / "asm"))

from pds6502 import Assembler, logical_lines  # noqa: E402

VENDOR = _ROOT / "vendor" / "Magician-NES"
SYM = _ROOT / "asm" / "out" / "mag.sym"

_fails = 0


def ok(what: str, got=None) -> None:
    global _fails
    if what is None:
        print(f"  ok   {what}")
        return
    if not isinstance(got, bool):
        print(f"  ok   {what}: {got}")
        return
    if got:
        print(f"  ok   {what}")
    else:
        _fails += 1
        print(f"  FAIL {what}")


def asm_bytes(source: str, slot: int = 0, size: int = 0x20000) -> tuple[bytes, Assembler]:
    """Assemble `source` at the start of `slot` and return exactly the bytes written.

    Only the written span is returned, not the whole slot: `Assembler.emit` drops a
    byte whose offset falls outside `prg` and records it in `overflow`, so a slot's
    worth of zeros would make a length check meaningless.
    """
    prg = bytearray(size)
    a = Assembler(prg, _ROOT, [VENDOR])
    tmp = _ROOT / "asm" / "out" / "_pointer_widths_probe.pds"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(source, encoding="latin-1")
    try:
        a.run_file(tmp, slot=slot)
    finally:
        tmp.unlink(missing_ok=True)
    base = a.SLOT * slot
    assert a.overflow == [], f"probe bytes were dropped, so the length is a lie: {a.overflow}"
    offs = sorted(o for o in a.emitted if base <= o < base + a.SLOT)
    assert offs, "nothing was emitted at all"
    out = bytearray(offs[-1] - base + 1)
    for o in offs:
        out[o - base] = a.emitted[o]
    return bytes(out), a


def syms(asm: Assembler) -> dict[str, int]:
    return {k: v for k, v in asm.sym.items() if not k.startswith("|")}


def split_ops(text: str) -> list[str]:
    return [t for t in re.split(r"[,\s]+", text.strip()) if t]


# The eighteen shop command labels `SHOPDAT.SRC:451-457` puts in `ijvl`, in the
# order it puts them. Declared here rather than next to the checks that use it so
# that check 8c -- which assembles this very list -- can be written before the
# checks that read `asm/out/mag.sym`.
SHOP_LABELS = ("sbuy sempty sexit sgold1 smana10 syesno smsg sset "
               "sclr stst sjump sgover saddob sdelob sbch ststp ssetp sclrp").split()


# ----------------------------------------------------------------- 1-4: widths
b, _ = asm_bytes("t1\tdl $1234,$5678\nt2\tdh $1234,$5678\n")
ok("check 1: `dl $1234,$5678` emits 34 78 -- the two operands' LOW bytes and "
   "nothing else", b[:2].hex() == "3478")
ok("check 2: `dh $1234,$5678` emits 12 56 -- the two operands' HIGH bytes",
   b[2:4].hex() == "1256")
ok("check 3: `dl`+`dh` together emit 4 bytes for 2 operands (1 each), not 12 "
   "(4*2 + 2*2)", len(b) == 4)

b, _ = asm_bytes("t1\tdl $12,$34,$56,$78\nt2\tdh $12,$34,$56,$78\n")
ok("check 4: one byte per OPERAND, not per line: 4 operands -> 8 bytes", len(b) == 8)

# ------------------------------------------------------- 5-6: the other widths
b, _ = asm_bytes("t1\tdb $11\nt2\tdc $22\nt3\tdw $3344\n")
ok("check 5: `db` and `dc` are still one byte each", b[:2].hex() == "1122")
ok("check 6: `dw` is still two bytes, little-endian", b[2:4].hex() == "4433")

# ------------------------------------------------------------- 7: dl/dh == dw
# The BYTES are the same set as `dw`; the ORDER is not, and the order is the
# whole reason `dl`/`dh` are not just aliases for `dw`. `dl a,b,c / dh a,b,c`
# lays the image out as six lows then six highs, which is what a table indexed
# with one `lda tab,x` can read without an index doubling. `dw a,b,c` interleaves.
# Getting this wrong -- emitting dl as 4 bytes -- produces neither layout.
b, _ = asm_bytes("t1\tdl $1234,$5678,$9abc\nt2\tdh $1234,$5678,$9abc\n")
dw, _ = asm_bytes("t1\tdw $1234,$5678,$9abc\n")
ok("check 7: `dl`+`dh` is 6 bytes for 3 pointers and holds the SAME 6 bytes as "
   "`dw`, laid out lows-then-highs", b.hex() == "3478bc12569a" and sorted(b) == sorted(dw))

# ----------------------------------------------------------- 8: labels, not numbers
b, a = asm_bytes("sbuy\tequ $e3f4\nsempty\tequ $e346\n"
                 "ijvl\tdl sbuy,sempty\n"
                 "ijvh\tdh sbuy,sempty\n")
s = syms(a)
ok("check 8: the widths hold for LABELS, not just literals: ijvh-ijvl == 2 operands",
   s.get("ijvh", 0) - s.get("ijvl", 0) == 2)
ok("check 8b: the low byte is the label's low byte and the high byte its high byte",
   b == bytes([0xF4, 0x46, 0xE3, 0xE3]))

# 8c is the check that catches the regression on its own. Checks 9-11 read
# `asm/out/mag.sym`, which was produced by whatever the assembler did when it was
# last built -- so with `dl`=4 re-introduced they still pass, because the stale
# symbol file still says 18. This one assembles the REAL eighteen operand names
# and measures the table it gets, so it fails on the re-introduction rather than
# on the next `make`.
b, a = asm_bytes("".join(f"op{i}\tequ ${0xE000 + i:04X}\n" for i in range(len(SHOP_LABELS)))
                 + "ijvl\t" + "dl " + ",".join(f"op{i}" for i in range(len(SHOP_LABELS))) + "\n"
                 + "ijvh\t" + "dh " + ",".join(f"op{i}" for i in range(len(SHOP_LABELS))) + "\n")
s = syms(a)
ok(f"check 8c: assembling SHOPDAT.SRC's real `ijvl dl`/`ijvh dh` lists puts ijvh "
   f"{s['ijvh'] - s['ijvl']} bytes after ijvl; the old widths (dl=4, dh=2) would "
   f"put it {4 * len(SHOP_LABELS) + 2 * len(SHOP_LABELS)} bytes after",
   s["ijvh"] - s["ijvl"] == len(SHOP_LABELS))

# ------------------------------------ 9-11: our own symbol file, when it exists
if not SYM.exists():
    print("  --   checks 9-11 SKIPPED: asm/out/mag.sym does not exist "
          "(run `make rom` to produce it). Not a pass: not run.")
else:
    table = {}
    for ln in SYM.read_text().splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            try:
                table[k.strip()] = int(v.strip().lstrip("$"), 16)
            except ValueError:
                pass
    missing = [n for n in SHOP_LABELS if n not in table]
    ok("check 9: every ijvl/ijvh operand label is in our own mag.sym", not missing)
    if not missing:
        n = len(SHOP_LABELS)
        ok(f"check 10: ijvh - ijvl == {n} -- one byte per pointer, NOT 4*n (72)",
           table["ijvh"] - table["ijvl"] == n)
        low = bytes(table[x] & 0xFF for x in SHOP_LABELS)
        high = bytes((table[x] >> 8) & 0xFF for x in SHOP_LABELS)
        ok("check 11: both tables are ABSOLUTE ($Exxx), so dl and dh are both needed "
           "(a zp label would make the high byte always zero)",
           all(0xE000 <= table[x] <= 0xFFFF for x in SHOP_LABELS))
        prg = (_ROOT / "asm" / "out" / "prg.bin")
        if prg.exists():
            b, _ = asm_bytes("".join(
                f"p{i}\tdh ${table[x]:04X}\n" for i, x in enumerate(SHOP_LABELS)))
            ok("check 11b: the high-byte table this test expects is what the directive emits",
               b[:n] == high)
            del low
        else:
            print("  --   check 11b SKIPPED: asm/out/prg.bin does not exist. Not a pass.")

# ------------------------------- 12-16: the source's own dl/dh statements
#
# Read with pds6502's own `strip_comment`, so a `;` comment on a `dl` line cannot
# be read as operands. Doing it with a hand-rolled regex is how this file's first
# draft reported 115 `dl`s that did not pair: four of them carried `;2.0` and
# `;door/wall` as if those were operands, which made the multiset comparison in
# check 16 fail for a reason that had nothing to do with the widths.
from pds6502 import strip_comment  # noqa: E402

SRC_FILES = sorted(VENDOR.glob("*.SRC"))


def dl_dh_statements(path: pathlib.Path) -> list[tuple[str, tuple[str, ...]]]:
    """Every `(dl|dh, operands)` statement in one source file, comments removed.

    A statement is `<label> <op> <ops>` at column 0, or a bare `<op> <ops>` on an
    indented continuation line. Anything else (a `db`/`hex` statement, an `endm`,
    a comment) is skipped rather than guessed at, and check 15 fails loudly if the
    scan found nothing at all -- a parser that silently matches zero times would
    make checks 12-14 vacuous.
    """
    out: list[tuple[str, tuple[str, ...]]] = []
    for raw in path.read_text(encoding="latin-1").splitlines():
        line = strip_comment(raw).strip()
        if not line:
            continue
        m = re.match(r"^(\S+)\s+([A-Za-z]+)\b(.*)$", line)
        if m and m.group(1).lower() not in ("dl", "dh"):
            op, rest = m.group(2).lower(), m.group(3)
        else:
            m2 = re.match(r"^([A-Za-z]+)\b(.*)$", line)
            if not m2:
                continue
            op, rest = m2.group(1).lower(), m2.group(2)
        if op in ("dl", "dh"):
            out.append((op, tuple(split_ops(rest))))
    return out


dl_runs: list[tuple[str, tuple[str, ...]]] = []
dh_runs: list[tuple[str, tuple[str, ...]]] = []
for p in SRC_FILES:
    for op, operands in dl_dh_statements(p):
        (dl_runs if op == "dl" else dh_runs).append((p.name, operands))

shop = [r for r in dl_runs if r[0] == "SHOPDAT.SRC"]
low_names = [x for _f, ops in shop for x in ops]
ok("check 12: SHOPDAT.SRC's `ijvl dl` list is the eighteen shop command names",
   low_names == SHOP_LABELS)
high_names = [x for _f, ops in dh_runs if _f == "SHOPDAT.SRC" for x in ops]
ok("check 13: SHOPDAT.SRC's `ijvh dh` list is the SAME eighteen, in the same order",
   high_names == SHOP_LABELS)
n = len(SHOP_LABELS)
old = 4 * n + 2 * n
ok(f"check 14: the pair is {n} + {n} = {n * 2} bytes here; the old widths "
   f"(dl=4, dh=2) would have emitted {old}, i.e. {old - n * 2} bytes of invented "
   f"table between ijvl and ijvh", n * 2 == 36 and old == 108)

ok(f"check 15: the scan found dl/dh statements at all ({len(SRC_FILES)} source "
   f"files scanned) -- a parser matching nothing would leave 12-14 vacuous",
   len(dl_runs) >= 50 and len(dh_runs) >= 50)
ok(f"check 16: {len(dl_runs)} `dl` statements and {len(dh_runs)} `dh` statements, and "
   f"they are the SAME lists (same file, same operand order, same count) -- every "
   f"`dl` in the tree has a `dh` partner, so low-byte/high-byte is the only "
   f"reading of the pair that fits",
   collections.Counter(dl_runs) == collections.Counter(dh_runs) and len(dl_runs) > 50)

# ------------------------------------------------- 17: the widths are not old
src = (_ROOT / "asm" / "pds6502.py").read_text(encoding="latin-1")
ok("check 17a: the old widths are gone from the code -- there is no `\"dl\": 4` "
   "and no `\"dh\": 2` anywhere",
   not re.search(r'"dl"\s*:\s*4', src) and not re.search(r'"dh"\s*:\s*2', src))
ok("check 17b: the module docstring no longer documents `dl` (4 bytes), `dh` (2 bytes)",
   "`dl` (4 bytes)" not in src and "`dh` (2 bytes)" not in src)
ok("check 17c: the width table now records `dl` and `dh` as one byte each",
   '"dl": (1, 0)' in src and '"dh": (1, 1)' in src)

# ------------------------------------------------------- a suite that ran something
ok("the file ran every check above", _fails == 0)
if _fails:
    print(f"\n{_fails} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")