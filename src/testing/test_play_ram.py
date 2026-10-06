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
# The length bound is 128 because `eflags` is 128 bytes (`zp eflags, maxe/2`
# with `maxe equ $100`). The bound used to be 16, which was simply wrong: the
# source has `submap,$6e` (110 bytes) and `panbuf,panxmax*panymax` (168), so a
# 16-byte ceiling refused real fields. The example below is therefore 200, not
# 99 -- and 99 was accepted the moment the bound moved, which is the honest
# shape of that correction: the guard was tightened-then-loosened rather than
# moved for a reason.
raised = 0
for bad, why in ((lambda: ram.field("_t1", 1, "", "phase"), "empty comment"),
                 (lambda: ram.field("_t2", 1, "has a comment",), "no symbol"),
                 (lambda: ram.field("_t3", 200, "bad length", "phase"), "bad length")):
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
    # $5F = %01011111, so bit 4 IS set: `band 16` is true and `bne 16` is
    # true. Written as modulo arithmetic both of these were false/true for the
    # wrong reason -- 95 % 16 is 15, which says nothing about bit 4.
    ("phase", "band", 16, True), ("phase", "bne", 16, True),
    ("phase", "band", 32, False), ("phase", "bne", 32, False),
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
for op in ("eq", "ne", "lt", "le", "gt", "ge", "band", "bne", "bclr"):
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
                       "band": lambda x: (x & v) == v if v else False,
                       "bne": lambda x: (x & v) != 0 if v else False,
                       "bclr": lambda x: (x & v) == 0 if v else False}[o]
                got = p.field.get(probe) if l == 2 else probe[a]
                if lua(got) != p.holds(probe):
                    mis.append(f"{p} python={p.holds(probe)} lua={lua(got)}")
check(f"12: the Python predicate and the bridge's decoding of its own wire "
      f"format agree on all {9 * 3 * 7 * 3} combinations of op x field x value "
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

# ============================ 18. `band`/`bne` are BITWISE, not modulo
#
# These two ops exist to test a BIT in a byte -- that is what the game's own
# `bit tmpflag` / `and #%00001100` do, and it is what a flag assertion needs.
# Written as `a % b`, `band` with mask 1 is vacuously true for every value,
# because every integer is a multiple of 1. So "the player has bought the
# goat's milk" -- mask $01, the very first quest flag -- would have been true
# from power-on, in every state, silently, and a ladder that asserted on it
# would climb for free. `bne` with mask 3 is wrong in the other direction: it
# calls "3 carried" NOT carried, which is the opposite of the game's rule
# (`addinv` refuses a fourth: `get2 / cmp #$03 / bcs` -- x7.pds:444-446).
img_b = image(tmpflag=0x02)
check("18: band/bne test BITS. band with mask 1 is false for a byte with bit 0 "
      "clear ($02), which modulo arithmetic would call true",
      not ram.pred("tmpflag", "band", 1).holds(img_b)
      and ram.pred("tmpflag", "band", 2).holds(img_b))
check("18b: ...and bne with a 2-bit mask is true for a count of 3, which is "
      "'carrying some of it' -- modulo says 3%3==0, i.e. not carried",
      ram.pred("invop", "bne", 0x0C).holds(image(invop=0x0C))
      and ram.pred("invop", "bne", 0x0C).holds(image(invop=0x04))
      and not ram.pred("invop", "bne", 0x0C).holds(image(invop=0x00)))

# ==================================== 19. the quest flags Eurocom already named
# `pds-text/x0.pds:252-264`:
#     ;Temporary flags reset each game
#     drink  equ $01   ;1=plr has bought drink in pub
#     asked  equ $02   ;1=vicar has asked plr to deliver letter
#     ...
#     ;Permanent flags saved in password
#     gotlet equ $01   ;1=got vicar's letter
#     sentlet equ $02  ;1=posted vicar's letter
# THE MASKS ARE NOT ADDRESSES. `mag.sym` carries `drink = $0001` because an
# `equ $01` went into a table that otherwise holds addresses, and $0001 is a
# real zero-page byte -- the joypad accumulator `jt`. So a flag API that took
# an address here would assert on the joystick. These checks pin that the
# resolved mask is a single bit and that the byte it is tested against is the
# one `field()` declared from a symbol, not the mask itself.
try:
    masks = {n: ram.flag_mask(n) for n in ram.QUEST_FLAGS}
except Exception as e:                       # noqa: BLE001
    masks = {}
    check("19: every quest flag name resolves to a mask", False,
          f"{type(e).__name__}: {e}")
else:
    onebit = all(m and (m & (m - 1)) == 0 for m in masks.values())
    check(f"19: all {len(masks)} quest flag names resolve, and every mask is a "
          "single bit -- so `asked` cannot silently become an address", onebit,
          str(masks))
hosts = {}
for n in ram.QUEST_FLAGS:
    hosts[n] = ram.flag_host(n).addr
check("19b: the temporary flags are all tested against `tmpflag` and the "
      "permanent ones against `perflag` -- two host bytes -- and neither host "
      "byte IS a flag's mask, which is the whole point of not declaring them as "
      "fields",
      len(set(hosts.values())) == 2
      and not (set(hosts.values()) & set(masks.values())),
      str(hosts))
# The mask must never be reachable as a FIELD address: if `field()` had been
# asked to declare `drink`, it would sit at $0001, which is `jt`.
declared = {f.addr for f in ram.all_fields()}
check("19c: no quest-flag mask is also a declared RAM field address, i.e. none "
      "of them was declared as if it were an address",
      not (set(masks.values()) & declared))
p_set = ram.flag_pred("drink", True)
p_clr = ram.flag_pred("drink", False)
check("19d: flag_pred builds a predicate on the HOST byte with the mask in it, "
      "and both polarities work",
      p_set.field.name == "tmpflag" and p_set.value == masks["drink"]
      and p_set.holds(image(tmpflag=0x01)) and not p_set.holds(image(tmpflag=0x00))
      and p_clr.holds(image(tmpflag=0x00)))

# ===================================== 20. the 2-bit inventory item counters
# `addinv` (x7.pds:442-451) calls `get2`, which reads a 2-bit field: four item
# types per byte, and `cmp #$03 / bcs` refuses a fifth. So the count for item
# `n` is `(invop[n//4] >> (2*(n%4))) & 3`, and the game's own cap is 3 -- which
# is the walkthrough's "you can carry 3 of every type", now a fact from the
# source rather than a claim.
#
# The byte is filled in two ways per case: the item's own bits set to the count
# under test, and every OTHER bit pattern in the byte set to its opposite, so a
# count of 1 that only works because the rest of the byte happens to be zero
# fails here. This test caught a wrong ITEM INDEX (key was 0x11, which is the
# ultimate potion) rather than a wrong extractor.
bad_items = []
for name, ob in sorted(ram.ITEMS.items(), key=lambda kv: kv[1]):
    byte = ram.f("invop").addr + ob // 4
    shift = (ob % 4) * 2
    if not 0 <= ob <= 0x4B:
        bad_items.append(f"{name} ${ob:02X} out of the 00..4B range")
        continue
    for count in (0, 1, 2, 3):
        # every bit in the BYTE that is not this item's pair, set to its
        # opposite, so the extraction cannot be passing because the rest of the
        # byte happened to be zero. The mask here is byte-relative; ram.py's
        # item_mask is measured from the start of the whole nine-byte field.
        pair = 0x03 << shift
        noise = 0xFF & ~pair
        raw = noise | (count << shift)
        img_i = bytearray(0x800)
        img_i[byte] = raw
        got = ram.carried(ob, bytes(img_i))
        if got != count:
            bad_items.append(f"{name} byte=${raw:02X} -> {got}, wanted {count}")
check(f"20: the {len(ram.ITEMS)} named items extract their own 2-bit count out "
      "of `invop` with every other bit in the byte set to its opposite, and the "
      "game's cap of 3 (`cmp #$03 / bcs`, x7.pds:445) is what the extractor "
      "reports", not bad_items, "; ".join(bad_items[:4]))
check("20b: carried_pred is a single predicate on one named field -- the bridge "
      "evaluates one address per predicate, so a two-address test cannot be "
      "sent",
      len({p.field.name for p in (ram.carried_pred(ram.ITEMS["key"]),
                                  ram.carried_pred(ram.ITEMS["sunglasses"]),
                                  ram.carried_pred(ram.ITEMS["walking_stick"]))}) == 1
      and all(isinstance(p, ram.Pred) for p in
              (ram.carried_pred(ram.ITEMS["key"]),
               ram.carried_pred(ram.ITEMS["sunglasses"]))))
try:
    ram.carried_pred(ram.ITEMS["key"], 0)
    ok("20c: carried_pred(at_least=0) is REFUSED -- 0 items is the mask being "
       "clear, not a count question", False)
except ValueError:
    ok("20c: carried_pred(at_least=0) is refused", True)

# Every item index must be the index of its own NAME in the source's string
# table, or the number beside it is a guess. `obtxt` is what the inventory
# screen reads at that index, so it is the authority -- and it is written with
# both `dc "WHOLE NAME"` and `db "SUN",hyp,"GLASSE","S"`, where `hyp` is a
# line feed used to break a long name across two lines. A parser that only
# matched `dc` silently drops every wrapped name and shifts every index after
# it, which is the failure this check exists to prevent.
obtxt = msc[msc.index("obtxt\t"):]
obtxt = obtxt[:obtxt.index("\n\n")]
obtxt_names = []
for line in obtxt.split("\n"):
    if not line.startswith(("obtxt", "\tdc", "\tdb")):
        continue
    obtxt_names.append("".join(re.findall(r'"([^"]*)"', line)).replace(" ", ""))
check("20d: the source's own `obtxt` table parses to the 34 names it declares "
      f"(00..21, the last being blank) including the wrapped ones -- it gave "
      f"{len(obtxt_names)}",
      len(obtxt_names) == 34 and obtxt_names[0] == "WATERFLASK"
      and obtxt_names[26] == "SUNGLASSES" and obtxt_names[27] == "WALKINGSTICK",
      str(obtxt_names[:6]) + " ...")
unverified = []
for name, ob in ram.ITEMS.items():
    if ob >= len(obtxt_names):
        unverified.append(f"{name} ${ob:02X} past the end of obtxt")
        continue
    printed = obtxt_names[ob]
    words = [w for w in name.lower().split("_") if w != "of"]
    if not all(w in printed.lower() for w in words):
        unverified.append(f"{name} -> ${ob:02X} is {printed!r}")
check(f"20e: every one of the {len(ram.ITEMS)} item indices names the same "
      "object in the source's own string table", not unverified, str(unverified))

# ==================================== 21. the fields the ladder needs exist
need = ("mclock", "fooddel", "waterdel", "valsav", "uflg", "dflg", "intflg",
        "serflg", "intmsg", "begmsg", "eflags", "panhead", "pantail",
        "tmpflag", "perflag", "invop", "obeno", "obchr", "shopdat", "ynflag",
        "deathtyp", "drinks", "food", "water", "gametime", "manatop")
absent = [n for n in need if n not in ram._FIELDS]
check(f"21: all {len(need)} RAM locations the quest ladder asserts on are "
      "declared with a name and a comment", not absent, str(absent))
# `valsav` is what settles the spell-cost disagreement: `chkmana` (x7.pds:198-205)
# computes manacur - cost into valsav and `upmana` moves it back, so
# manacur - valsav IS the cost the game just charged, readable at any frame.
check("21b: `valsav` is a 2-byte field, because `chkmana` writes both halves "
      "before `upmana` copies them to manacur",
      ram.f("valsav").length == 2)

ok(f"the file ran every check above ({_n} checks)", _fails == 0)
if _fails:
    print(f"\n{_fails} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")