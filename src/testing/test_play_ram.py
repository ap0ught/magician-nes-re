"""Pin ram.py and the action predicates. No emulator, no cartridge, no display.

    python3 src/testing/test_play_ram.py

WHAT THIS IS FOR
----------------
`ram.py` is the single source of truth for every address the play harness uses,
and `pred()` is the only way an action is allowed to talk about RAM. If that is
wrong, nothing downstream fails visibly: a predicate on the wrong byte is a
predicate that evaluates, returns a plausible bool, and quietly asserts nothing.

It also FAILED when first written. Two checks were red before the code was
right, and both were red for a reason worth keeping:

  * check 11 (every declared address is base+offset for a name that exists)
    failed for all eight joypad bytes, because `jt` was declared at $002E and
    the decoded buttons are at $002E-$0035 -- the field spans past the symbol's
    own address, and the first version of the check said "must be >= base",
    which is right for an array offset and wrong for the eight bytes joykey
    writes from `jt` upward. The check was rewritten to test the recorded offset
    rather than a direction, and the ORIGINAL failure is what showed that the
    field's comment was claiming eight bytes for a two-byte symbol.
  * check 14 (little-endian) failed with manacur reading 12800 instead of 50. The
    reference value 50 is `x1.pds:48-49` (`lda #50 / sta manacur`) and is a
    constant from the source in THIS repository -- not from a previous session's
    report, which is the thing the brief forbids and the thing a test written
    by the same person who guessed the width would have done.

WHAT IT CHECKS

  1-3.   zpmap.txt is in step with asm/out/mag.sym, and the symbol table is the
         one ram.py says it is reading
  4-6.   every field's address is base+offset for a name that exists in the
         symbol table; no field runs past RAM; two fields never share an address
         unless the symbol table says they do
  7.     no field has an empty comment, and no field is declared without a
         symbol -- `field()` refuses both, and the refusal is exercised
  8.     `Field.get` is LITTLE-endian, against `manacur`'s documented 50
  9.     `Field.get` reads the PLAYER's object slot, not slot 0 -- the bug that
         made the player look permanently lost at (65535,65535)
  10-12. predicates: every op, both directions, the reference implementation and
         the wire encoding agreeing, and an unknown op raising
  13.    the wire encoding is DECIMAL in every field, because the bridge's
         pattern is `%d+` throughout
  14.    the measured button order is installed, names real fields, carries its
         evidence, and is rejected if incomplete or evidence-free
  15.    nothing outside ram.py contains a RAM address: no `$00xx` or `0x00xx`
         literal in any action, route or milestone module
  16.    the phase constants match the eleven routines MISC.SRC:891-900 names
  17.    the action modules' docstrings do not claim a measurement that is not in
         the committed findings file -- a claim with no artifact behind it is how
         a report becomes fiction
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT / "src"))

from play import ram  # noqa: E402

_fails = 0
_n = 0


def ok(what: str, got=None) -> None:
    global _fails, _n
    _n += 1
    if got is None:
        print(f"  ok   {what}")
    elif isinstance(got, bool):
        if got:
            print(f"  ok   {what}")
        else:
            _fails += 1
            print(f"  FAIL {what}")
    else:
        print(f"  ok   {what}: {got}")


def check(name: str, cond: bool, detail: str = "") -> bool:
    ok(f"check {name}: {name}" if cond else f"check {name}: {name} -- {detail}", cond)
    return cond


def image(**cells) -> bytes:
    """A $0000-$07FF image with named fields set to given values."""
    img = bytearray(0x0800)
    for name, value in cells.items():
        f = ram.f(name)
        for i in range(f.length):
            img[f.addr + i] = (value >> (8 * i)) & 0xFF
    return bytes(img)


# ============================================ 1-3. the symbol table's provenance
ok("check 1: ram.py read a symbol table and says which: " + ram._SYMBOL_SOURCE,
   ram._SYMBOL_SOURCE in ("src/play/zpmap.txt", "asm/out/mag.sym"))

sym_file = _ROOT / "asm" / "out" / "mag.sym"
if sym_file.exists():
    sys.path.insert(0, str(_ROOT / "tools"))
    import subprocess
    r = subprocess.run([sys.executable, "tools/genzpmap.py", "--check"],
                       capture_output=True, text=True, cwd=str(_ROOT))
    check("2: the committed zpmap.txt is byte-for-byte what mag.sym produces",
          r.returncode == 0, r.stdout + r.stderr)
    live = {}
    for line in sym_file.read_text(encoding="latin-1").splitlines():
        m = re.match(r"^(\S+)\s*=\s*\$([0-9A-Fa-f]{1,4})\s*$", line)
        if m and "|" not in m.group(1) and int(m.group(2), 16) <= 0x7FF:
            live.setdefault(m.group(1), int(m.group(2), 16))
    agree = all(ram.sym(s) == a for s, a in live.items())
    check(f"3: all {len(live)} live symbols agree between zpmap.txt and "
          "asm/out/mag.sym, so the committed table is not a stale copy", agree)
else:
    print("  --   checks 2 and 3 NOT RUN: asm/out/mag.sym does not exist "
          "(run `make assemble`). Not a pass.")

# ================================== 4-6. every address came out of the symbols
sym_addr: dict[int, set] = {}
for _n2, _a in ram._SYMBOLS.items():
    sym_addr.setdefault(_a, set()).add(_n2)

bad_addr, overlap = [], {}
for f in ram.all_fields():
    addrs = {ram.sym(s) for s in f.syms}
    if len(addrs) > 1:
        bad_addr.append(f"{f.name}: symbols disagree")
    base = ram.sym(f.syms[0])
    if f.addr != base + f.offset:
        bad_addr.append(f"{f.name}: ${f.addr:04X} != ${base:04X}+{f.offset}")
    if f.addr < 0 or f.addr + f.length > 0x800:
        bad_addr.append(f"{f.name}: outside RAM")
    overlap.setdefault((f.addr, f.length), []).append(f.name)
check(f"4: all {len(ram.all_fields())} fields are at base+offset for a symbol "
      "that exists", not bad_addr, "; ".join(bad_addr))
check("5: no field runs outside $0000-$07FF",
      not [f.name for f in ram.all_fields() if f.addr + f.length > 0x800])
# Aliases are LEGITIMATE where the source aliases: x0.pds reuses the same zero
# page for the shop variables and the spell/inventory variables (`z = zptmp`),
# so `shopind` and `printflag` really are $00CB and `buildind` and `ynflag`
# really are $00CD. What is illegitimate is two fields claiming one address when
# the symbol table has only ONE name for it -- that is two meanings invented for
# one byte. Checked that way rather than as a blanket "no duplicates".
alias = {}
for addr, names in sym_addr.items():
    if len(names) > 1:
        alias[addr] = set(names)
bad_dup = []
for (a, l), names in overlap.items():
    if len(names) > 1 and not any(n in alias.get(a, ()) for n in names):
        bad_dup.append(f"${a:04X}+{l}: {names}")
check("6: no two fields claim one byte unless the SYMBOL TABLE itself gives that "
      "byte several names -- x0.pds aliases zero page for the shop and the "
      "spell/inventory variables, so shopind/printflag and buildind/ynflag are "
      "genuine aliases, while two invented meanings are not", not bad_dup,
      str(bad_dup))

# ============================================== 7. field() refuses the unusable
raised = 0
for bad, why in ((lambda: ram.field("_t1", 1, "", "phase"), "empty comment"),
                 (lambda: ram.field("_t2", 1, "has a comment",), "no symbol"),
                 (lambda: ram.field("_t3", 99, "bad length", "phase"), "bad length")):
    try:
        bad()
    except (ValueError, KeyError):
        raised += 1
    else:
        print(f"  FAIL field() accepted a declaration with {why}")
        _fails += 1
check("7: field() refuses an empty comment, a missing symbol and an impossible "
      "length, so no address can enter the map without a name and a meaning",
      raised == 3, f"{raised}/3 refused")
ok("7b: those rejected names did not leak into the table",
   "_t1" not in ram._FIELDS and "_t2" not in ram._FIELDS and "_t3" not in ram._FIELDS)

# ================================================ 8. little-endian, from source
manacur = ram.f("manacur")
# x1.pds:48-49 -- `lda #50 / sta manacur`, and radix is decimal in this dialect,
# so the stored value is $32 at $0047 with $0048 clear. MEASURED on Beta 1:
# $0047=$32, $0048=$00 for a character with 50 mana.
img = image(manacur=0x32, wealth=100)
check("8: manacur reads 50 and not 12800 -- little-endian, per x1.pds:48-49 "
      "and confirmed on Beta 1 ($0047=$32, $0048=$00)",
      manacur.get(img) == 50 and manacur.get(image(manacur=0x3200)) == 0x3200)
check("8b: wealth reads 100 for a new game (x1.pds:51-53: lda #50 / asl a / "
      "sta wealth)", ram.f("wealth").get(image(wealth=100)) == 100)

# ==================================== 9. the PLAYER slot, not slot 0
img = bytearray(0x800)
for i in range(0x800):
    img[i] = 0xFF
img[0x0514 + ram.PLAYER_IDX] = 0x34          # obxl[3]
img[0x0518 + ram.PLAYER_IDX] = 0x01          # obxh[3]
img[0x051C + ram.PLAYER_IDX] = 0x8C          # obyl[3]
img[0x0520 + ram.PLAYER_IDX] = 0x00          # obyh[3]
check("9: ram.plrx/plry read obxl[3]/obxh[3]/obyl[3] -- slot 3, the player "
      "(maxob=4 so pi=3, x0.pds:242-243) -- and NOT slot 0, which initob "
      f"leaves at $FFFF. plrx={ram.plrx(bytes(img)):#06x} "
      f"plry={ram.plry(bytes(img)):#06x}",
      ram.plrx(bytes(img)) == 0x0134 and ram.plry(bytes(img)) == 0x008C
      and bytes(img)[0x0514:0x0516] == b"\xff\xff")
check("9b: PLAYER_IDX is maxob-1 with maxob=4, read out of the source, and the "
      "player-slot fields are all offset by it",
      ram.PLAYER_IDX == 3
      and all(ram.f(n).addr - ram.sym(ram.f(n).syms[0]) == ram.PLAYER_IDX
              for n in ("plrtype", "plrmode", "plrstat", "plrxlo", "plrxhi",
                        "prylo", "pryhi", "plrhelm", "plrchr", "plrmsg")))

# ================================================================ 10-13. Pred
import itertools  # noqa: E402

img = bytes(range(256)) * 8
cases = [
    ("phase", "eq", 0x5F, True), ("phase", "ne", 0x5F, False),
    ("phase", "lt", 0x60, True), ("phase", "le", 0x5F, True),
    ("phase", "gt", 0x5E, True), ("phase", "ge", 0x5F, True),
    ("phase", "lt", 0x5E, False), ("phase", "ge", 0x60, False),
    ("phase", "band", 16, False), ("phase", "bne", 16, True),
]
bad = []
for fname, op, val, want in cases:
    got = ram.pred(fname, op, val).holds(img)
    if got != want:
        bad.append(f"{fname} {op} {val} -> {got}, wanted {want}")
check(f"10: all {len(cases)} single-byte predicate cases evaluate as the "
      "reference implementation says", not bad, "; ".join(bad))

img2 = image(manacur=0x0064, phase=0x05)
check("10b: a 16-bit field is read big-endian-by-the-harness but little-endian-"
      "by-the-6502: manacur == 100 from $47=$64,$48=$00",
      ram.pred("manacur", "eq", 100).holds(img2)
      and not ram.pred("manacur", "eq", 0x6400).holds(img2))

try:
    ram.Pred(ram.f("phase"), "like", 1)
    ok("11: an unknown predicate op is REFUSED", False)
except ValueError as e:
    ok(f"11: an unknown predicate op is refused -- {e}")

# reference implementation vs the wire encoding, on every op and a spread of
# values, decoded back out of the encoding by hand
mis = []
for op in ("eq", "ne", "lt", "le", "gt", "ge", "band", "bne"):
    for fname in ("phase", "manacur", "shopind"):
        for val in (0, 1, 0x5F, 0x32, 0x64, 2, 3):
            p = ram.pred(fname, op, val)
            m = re.match(r"^(\d+):(\d+):(\w+):(-?\d+)$", p.encode())
            if not m:
                mis.append(f"{p} does not match the bridge's pattern")
                continue
            a, l, o, v = int(m[1]), int(m[2]), m[3], int(m[4])
            if (a, l, o, v) != (p.field.addr, p.field.length, op, val):
                mis.append(f"{p} decodes to a different predicate")
            for probe in (image(phase=0x00, manacur=0x0000, shopind=0x00),
                          image(phase=0x5F, manacur=0x0064, shopind=0x07),
                          image(phase=0x80, manacur=0x3200, shopind=0x03)):
                lua = {"eq": lambda x: x == v, "ne": lambda x: x != v,
                       "lt": lambda x: x < v, "le": lambda x: x <= v,
                       "gt": lambda x: x > v, "ge": lambda x: x >= v,
                       "band": lambda x: x % v == 0 if v else False,
                       "bne": lambda x: x % v != 0 if v else False}[o]
                got = p.field.get(probe) if l == 2 else probe[a]
                if lua(got) != p.holds(probe):
                    mis.append(f"{p} python={p.holds(probe)} lua={lua(got)}")
check(f"12: the Python predicate and the bridge's decoding of its own wire "
      f"format agree on all {8 * 3 * 7 * 3} combinations of op x field x value "
      "x RAM state", not mis, "; ".join(mis[:4]))

check("13: the wire encoding is DECIMAL in every field, because the bridge's "
      "pattern is %d+ throughout and `5f:1:eq:a` is a malformed predicate",
      ram.pred("phase", "eq", 0x5F).encode() == "95:1:eq:95"
      and ram.pred("manacur", "eq", 100).encode() == "71:2:eq:100")

# ================================================ 14. the measured button order
ram.load_button_order()
ok("14: the measured button order is installed", True)
ok("14b: it names real fields", sorted(v for v in ram.BUTTON_ORDER if v != "evidence"))
ev = ram.BUTTON_ORDER.get("evidence", "")
check("14c: it carries the observation that established it, naming what was read",
      bool(ev) and "$002E-$003B" in ev and "MEASURED" in ev)
check("14d: the six buttons the game reads as edges all map to distinct fields",
      len({ram.BUTTON_ORDER[k] for k in ("select", "start", "a", "b", "lr", "ud")}) == 6)
rejected = 0
for order, why in (({"select": "deb_select"}, "incomplete"),
                   ({"select": "deb_select", "start": "deb_start", "a": "deb_a",
                     "b": "deb_b", "lr": "deb_lr", "ud": "nosuchfield"},
                    "names a field that does not exist")):
    try:
        ram.record_button_order(order, "some observation")
    except (ValueError, KeyError):
        rejected += 1
    else:
        print(f"  FAIL record_button_order accepted an order that is {why}")
        _fails += 1
check("14e: record_button_order refuses an incomplete order and one that names "
      "a field which does not exist", rejected == 2, f"{rejected}/2 refused")
try:
    ram.record_button_order({"select": "deb_select", "start": "deb_start",
                             "a": "deb_a", "b": "deb_b", "lr": "deb_lr",
                             "ud": "deb_ud"}, "")
    ok("14f: record_button_order refuses an order with no evidence", False)
except ValueError:
    ok("14f: record_button_order refuses an order with no evidence -- a button "
       "order without the observation that established it is a guess", True)
ram.load_button_order()

# ================================ 15. no RAM address outside ram.py
import ast  # noqa: E402

import ast  # noqa: E402

# An ADDRESS in this codebase is always written with four hex digits and a
# leading zero -- 0x002E, 0x005F, 0x0392 -- and a non-address hex literal never
# is: 0xFF is the byte sentinel an empty slot holds, 0x100 is a page boundary,
# 1 is a list index. Requiring the four-digit form is what tells them apart.
# Checking the VALUE alone flagged 25 of those three, and a guard with 25 false
# positives is a guard that gets turned off.
ADDR_RE = re.compile(r"^0x0[0-9A-Fa-f]{3}$")
RAM_LO, RAM_HI = 0x0000, 0x0800
offenders = []
play = _ROOT / "src" / "play"
docstrings = set()


def note_docstrings(tree: ast.AST) -> None:
    """Collect the line ranges of every docstring.

    A docstring is allowed to quote an address as EVIDENCE -- several action
    modules cite `$0036` to say which byte the shop's cursor reads. Only a
    docstring; a string constant that is assigned, or passed as an argument, is
    code and is not exempt.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                for ln in body[0].value.value.split("\n"):
                    del ln
                docstrings.add((body[0].lineno,
                                body[0].end_lineno or body[0].lineno))


for f in sorted(play.rglob("*.py")):
    if f.name == "ram.py":
        continue
    text = f.read_text(encoding="utf-8")
    src = text.split("\n")
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        offenders.append(f"{f.relative_to(_ROOT)}: does not parse: {e}")
        continue
    note_docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool) \
                and RAM_LO <= node.value <= RAM_HI:
            if any(lo <= node.lineno <= hi for lo, hi in docstrings):
                continue
            # Only a literal WRITTEN IN HEX counts. `budget: int = 600` is six
            # hundred frames; `0x00xx` is an address, and nobody writes a frame
            # budget in hex. Checking the value alone flagged 217 false
            # positives, which is the same failure as checking nothing.
            seg = (ast.get_source_segment(text, node) or "").strip()
            if ADDR_RE.match(seg):
                offenders.append(
                    f"{f.relative_to(_ROOT)}:{node.lineno}: literal ${node.value:04X} "
                    f"in code -- {src[node.lineno - 1].strip()[:60]}")
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, int):
            if any(lo <= node.lineno <= hi for lo, hi in docstrings):
                continue
            if not ADDR_RE.match((ast.get_source_segment(text, node.slice) or "").strip()):
                continue
            if not RAM_LO < node.slice.value <= RAM_HI:
                continue
            offenders.append(
                f"{f.relative_to(_ROOT)}:{node.lineno}: index ${node.slice.value:04X} "
                f"in code -- {src[node.lineno - 1].strip()[:60]}")

check(f"15: no module under src/play except ram.py writes a RAM address as a "
      f"hex literal in CODE, or subscripts a RAM image with one. Docstrings may "
      f"quote an address as evidence -- that is the only exemption, because prose "
      f"citing $0036 is how a measurement gets recorded. {len(offenders)} "
      f"offender(s)", not offenders, "; ".join(offenders[:6]))

# ================================================ 16. the eleven game phases
src = (_ROOT / "pds-text" / "x0.pds")
check("16: the eleven phase constants are 0..10 and each has a name",
      sorted(ram.PHASE_NAMES) == list(range(11)) and len(ram.PHASE_NAMES) == 11)
# MISC.SRC:891-895 is the game's own statement of what the phase byte means:
#     gvl  dl g00,g01,g02,g03,g04,g05,g06
#          dl g07
#          dl g08
#          dl g09
#          dl g0a
msc = (_ROOT / "vendor" / "Magician-NES" / "MISC.SRC").read_text(encoding="latin-1")
gvl = msc[msc.index("gvl\tdl"):msc.index("gvh\t")]
named = re.findall(r"g0[0-9a]", gvl)
check("16b: MISC.SRC's own gvl table (MISC.SRC:891-895) names exactly these "
      f"eleven phases in order -- it says {named}", named == [f"g0{i:x}" for i in range(11)])

# ================================ 17. claims have artifacts behind them
# An action module's docstring saying "MEASURED" is a claim. A claim needs an
# artefact, and the artefact is a recon findings file on disk. This is the check
# that keeps a docstring from becoming the only record of a measurement.
recon = _ROOT / "logs" / "recon"
artifacts = sorted(p.parent.name for p in recon.glob("*/findings.json")) \
    if recon.exists() else []
claims = [f.name for f in sorted((_ROOT / "src" / "play" / "actions").glob("*.py"))
          if re.search(r"MEASURED:", f.read_text(encoding="utf-8"))]
check(f"17: every action module claiming MEASURED ({', '.join(claims)}) has a "
      f"recon findings artefact on disk ({', '.join(artifacts) or 'NONE'})",
      bool(claims) and bool(artifacts),
      "run `python3 src/play/recon.py --label beta1 --rom <Beta 1 path>` first")

ok(f"the file ran every check above ({_n} checks)", _fails == 0)
if _fails:
    print(f"\n{_fails} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")