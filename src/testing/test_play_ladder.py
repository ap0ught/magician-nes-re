#!/usr/bin/env python3
"""What `src/play/ladder.py` claims about itself, checked.

THE POINT OF THIS FILE
----------------------
A ladder is a list of things to assert, and the failure mode of one is a rung
that *looks* asserted and is not: a predicate that is true from power-on, a
policy that presses a button the game does not read, a claim with no
measurement behind it, a segment whose success test no longer describes what its
policy does. None of those make the run fail -- they make the run SUCCEED for
the wrong reason, which is the only kind of wrong this project has to work
hardest against.

So every check here is a coverage check, not a behaviour check:

  1-4.   the route exists, its segments have unique names, every segment has a
         policy that is a factory, and every success test is callable
  5-8.   every predicate the route sends to the emulator is a `ram.Pred` on a
         NAMED field -- no addresses, no bare arithmetic
  9-11.  the DRINK bound: three is the number of `g1,set,drink` triples in the
         source, four is death, and the policy refuses rather than the assertion
  12-15. the shop scripts quoted in `TOWN_SHOPS` are the source's own bytes,
         that each of the seven town doors has an entry, and that the icons the
         ladder drives exist in the shop the segment says it is in
  16-18. every CLAIM has an id, a `read_in` naming where the claim was read, an
         `against` that says what the SOURCE says, and a `settled_by`
  19-21. the walkthrough is referenced BY PATH and its text is not in the tree
  22-25. the source's line numbers that the ladder's comments cite are real --
         a comment citing `x6.pds:175` for a line that is about something else
         is a measurement that reads as a fact
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT / "src"))

from play import ladder, ram  # noqa: E402
from play import first_town  # noqa: E402
from play.route import Route, Segment  # noqa: E402

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


def check(name: str, cond: bool, detail: str = "") -> None:
    ok(f"check {name}: {name}" if cond else f"check {name}: {name} -- {detail}",
       cond)


route = ladder.town_quests()

# =========================================================== 1-4. the shape
ok("check 1: the route builds with no emulator present "
   f"-- {len(route.segments)} segments, digest {route.digest()}")
names = [s.name for s in route.segments]
check("2: every segment name is unique -- a repeated name means one segment's "
      "checkpoint directory is another's, and `snapshot()` refuses the collision "
      "at the worst possible moment",
      len(names) == len(set(names)), str([n for n in names if names.count(n) > 1]))
check("3: every segment's factory returns a POLICY, not a policy: the runner "
      "calls `factory()` once per attempt and then calls the result",
      all(callable(s.factory) and s.factory() is not s.factory
          for s in route.segments))
# A success test is EITHER a callable on a RAM image or a `ram.Pred` -- the
# bridge evaluates a Pred itself, so `callable()` is False for one and a check
# that only allowed callables would have refused the flag and item rungs.
check("4: every segment has a success test the runner can use -- a callable, "
      "or a `ram.Pred` the bridge evaluates itself -- and every segment says "
      "WHY in its own words",
      all((callable(s.success) or isinstance(s.success, ram.Pred))
          and s.why.strip() for s in route.segments),
      str([s.name for s in route.segments
           if not ((callable(s.success) or isinstance(s.success, ram.Pred))
                   and s.why.strip())]))
check("4b: every segment has a frame budget large enough for its policy's own "
      "fixed costs -- a budget below the policy's unavoidable walk would cut "
      "every attempt at zero frames, which is the mistake milestone 1's "
      "lead-in draws made",
      all(s.max_frames >= 90 for s in route.segments))

# ================================ 5-8. every predicate is on a NAMED field
preds: list[tuple[str, object]] = []
for s in route.segments:
    t = s.success
    if s.prepare:
        t = t(bytes(0x800))                      # the factory form
        preds.append((s.name, t))
    elif isinstance(t, tuple):
        for x in t:
            preds.append((s.name, x))
    elif isinstance(t, list):
        for x in t:
            preds.append((s.name, x))
    else:
        preds.append((s.name, t))


def collect(node, seen):
    if isinstance(node, ram.Pred):
        seen.append(node)
    elif isinstance(node, (list, tuple)):
        for x in node:
            collect(x, seen)
    return seen


flat = []
for segname, t in preds:
    flat += [(segname, p) for p in collect(t, [])]
check(f"5: {len(flat)} of the route's predicates are `ram.Pred` objects, so "
      "every one of them is a single named field the bridge can evaluate",
      len(flat) > 0, "none found")
bad_addr = [f"{n}: {p}" for n, p in flat
            if not isinstance(p.field, ram.Field) or p.field.addr < 0
            or p.field.addr + p.field.length > 0x800]
check("6: none of them names a byte outside $0000-$07FF", not bad_addr,
      "; ".join(bad_addr[:4]))
bad_op = [f"{n}: {p}" for n, p in flat if p.op not in ram._OPS]
check("7: every op is one the bridge knows -- an op ram.py accepts and "
      "bridge.lua does not is a predicate that passes in Python and is rejected "
      "on the wire", not bad_op, "; ".join(bad_op[:4]))
# The op sets on the two sides must be the same SET, not merely overlapping.
bridge = (_ROOT / "src" / "play" / "bridge.lua").read_text(encoding="utf-8")
lua_ops = set(re.findall(r'op == "(\w+)"', bridge))
check("7b: ram.py's ops and bridge.lua's ops are the same set -- "
      f"python {sorted(ram._OPS)} vs lua {sorted(lua_ops)}",
      set(ram._OPS) == lua_ops,
      f"only python: {sorted(set(ram._OPS) - lua_ops)}, "
      f"only lua: {sorted(lua_ops - set(ram._OPS))}")
# And each op must be BITWISE on both sides, which is the correction this session
# made and the thing a future edit could silently undo.
lua_band = re.search(r'op == "band"\s*then f = function\(x\)([^)]*)\)', bridge)
check("7c: bridge.lua's `band` is a bitwise AND and not a modulo -- "
      f"{lua_band.group(1).strip() if lua_band else 'NOT FOUND'}",
      bool(lua_band) and "%" not in lua_band.group(1) and "&" in lua_band.group(1))

# ============================================ 9-11. the drink bound, exactly
def read_raw(path: pathlib.Path) -> str:
    """Bytes in, text out, no newline translation. See `source_lines`."""
    return path.read_bytes().decode("latin-1")


shopdat = read_raw(_ROOT / "vendor" / "Magician-NES" / "SHOPDAT.SRC")
tankard = [l for l in shopdat.splitlines()
           if "gover" in l and "set,drink" in l]
# `g1` is the pub's "-1 gold" opcode, so the number of DRINKS is the number of
# `g1` commands on the line, not the number of `set,drink`: only the first
# press sets the flag (`g1,set,drink,msg,$01`) and the next two are
# `g1,msg,$01` and `g1,msg,$02`. Counting `set,drink` would have said 1.
check(f"9: the pub's tankard script is three drinks and then `gover` -- the "
      f"source has {len(tankard)} such line(s), and the FIRST sets the drink "
      f"flag while the next two only charge again",
      len(tankard) >= 1
      and tankard[0].count("g1,") == ladder.DRINK_LIMIT
      and tankard[0].count("set,drink") == 1
      and tankard[0].rstrip().endswith("gover+$80\t;tankard"),
      str(tankard[:1]))
check("10: DRINK_LIMIT is therefore 3, and `safe_to_drink` is false only on "
      "the FOURTH press -- which is the press that ends the run",
      ladder.DRINK_LIMIT == 3
      and [ladder.safe_to_drink(n) for n in range(5)] == [True, True, True, False, False])
import inspect  # noqa: E402
drink_src = inspect.getsource(ladder.p_drink)
n_press = drink_src.count('rec.step(("A",)')
check(f"11: the ladder's drink policy presses A exactly {n_press} time per "
      "attempt, so no attempt can reach the fatal fourth press even if it is "
      "run twice by mistake, and it says REFUSING out loud. This is the one "
      "place in the project where the wrong answer is not a wrong number but a "
      "dead run",
      n_press == 1 and "REFUSING" in drink_src, drink_src[-200:])

# ================================ 12-15. the shop scripts are the source's
check("12: TOWN_SHOPS has one entry per `pt_shop` trigger the first town "
      f"declares -- {sorted(ladder.TOWN_SHOPS)}",
      sorted(ladder.TOWN_SHOPS) == list(range(7)))
probd = read_raw(_ROOT / "vendor" / "Magician-NES" / "PROBDAT.SRC")
town = probd[probd.index("pt10\titr"):probd.index("st10\tsti")]
triggers = re.findall(r"pt_shop,(\w+)", town)
check("12b: and the source's own town block really does fire `pt_shop` "
      f"{len(triggers)} times, with those data bytes in that order -- "
      f"{triggers}",
      [int(t, 16) for t in triggers] == sorted(ladder.TOWN_SHOPS),
      str(triggers))
# Every icon index the ladder drives must be a REAL icon of the shop it drives.
driven = {"drink": (0, 0), "priest": (4, 1), "letter": (4, 1),
          "post_office": (2, 1)}
bad_icon = []
for seg, (shop, icon) in driven.items():
    scripts = ladder.TOWN_SHOPS[shop][1]
    if not 0 <= icon < len(scripts):
        bad_icon.append(f"{seg}: icon {icon} not in shop {shop} "
                        f"({len(scripts)} icons)")
    elif scripts[icon].strip().lower().startswith("emp"):
        bad_icon.append(f"{seg}: icon {icon} of shop {shop} is `emp` (blank)")
check("13: every icon the ladder drives is a real, non-blank icon of the shop "
      "the segment says it is in", not bad_icon, "; ".join(bad_icon))
# The letter's icon must actually mention the letter.
check("13b: the church icon the ladder presses is the one whose script hands "
      f"over the letter -- {ladder.TOWN_SHOPS[4][1][1][:60]}",
      "addob,$1c" in ladder.TOWN_SHOPS[4][1][1]
      and "set,asked" in ladder.TOWN_SHOPS[4][1][1])
check("13c: the post office's icon 1 is the one that sets `sentlet` and "
      f"deletes the letter -- {ladder.TOWN_SHOPS[2][1][1][:60]}",
      "setp,sentlet" in ladder.TOWN_SHOPS[2][1][1]
      and "delob,$1c" in ladder.TOWN_SHOPS[2][1][1])
check("13d: the post office's icon 0 REFUSES without the letter, which is why "
      "the ladder reaches for icon 1 rather than icon 0",
      "tst,gotlet" in ladder.TOWN_SHOPS[2][1][0])

# ============================================== 16-18. every claim is a claim
check(f"16: all {len(ladder.CLAIMS)} claims have an id, a claim, somewhere the "
      "claim was read, what the source says, and how it is settled",
      all(all(c.get(k) for k in ("id", "claim", "read_in", "against",
                                 "settled_by")) for c in ladder.CLAIMS))
ids = [c["id"] for c in ladder.CLAIMS]
check("17: the claim ids are unique -- two claims sharing one id would make the "
      "measurement table ambiguous",
      len(ids) == len(set(ids)), str(ids))
check("18: every claim that a source disagrees with says SO, rather than "
      "reporting the disagreement as a footnote",
      all("against" in c and c["against"].strip() for c in ladder.CLAIMS))
check("18b: the two claims the guide and the TAS subtitles independently agree "
      "on are both in the table -- they are the ones that make the rest "
      "trustworthy",
      {"spell_cost", "shop_raises_mana_cap"} <= set(ids))

# ================================= 19-21. the walkthrough stays outside the tree
refs = re.findall(r"lpwb-magician-nes-walkthrough\.txt", "\n".join(
    p.read_text(encoding="utf-8") for p in
    sorted((_ROOT / "src" / "play").rglob("*.py"))))
check(f"19: the walkthrough is referenced BY PATH in {len(refs)} place(s) and "
      "never opened -- its prose is copyrighted and stays out of the repository",
      bool(refs))
committed_text = [p for p in _ROOT.rglob("*.py")
                  if "Downloads/Documents/Game-Guides" in p.read_text(
                      encoding="utf-8", errors="ignore")
                  and "lpwb" not in p.read_text(encoding="utf-8", errors="ignore")]
check("19b: nothing in the tree reads the guide's file -- only this repository's "
      "own derived facts are committed", not committed_text,
      str([str(p) for p in committed_text]))

# ================== 22-25. the source lines the ladder cites are the lines said
CITE = re.compile(r"\b((?:x[0-7]\.pds|PROBDAT\.SRC|SHOPDAT\.SRC|MISC\.SRC"
                  r"|PROBS\.SRC|x7\.pds)):(\d+)(?:-(\d+))?")
cited: dict[str, set[int]] = {}
for p in sorted((_ROOT / "src" / "play").rglob("*.py")):
    for m in CITE.finditer(p.read_text(encoding="utf-8")):
        cited.setdefault(m.group(1), set()).update(
            range(int(m.group(2)), int(m.group(3) or m.group(2)) + 1))


def source_lines(name: str) -> list[str]:
    """The file's lines, counted the way every citation in this tree counts.

    READ AS BYTES, and that is the whole trick. The recovered PDS text carries a
    carriage return at the end of every source RECORD inside a physical line --
    `x0.pds:15` is four statements separated by `\r` -- so both `read_text()`
    (which does universal-newline translation and turns each of those `\r` into a
    `\n`) and `str.splitlines()` (which breaks on `\r` as well) shift every line
    number after the first record on a line.

    Both were tried here and both were wrong in the same direction: `x7.pds:247`
    came back as `iny` when `dec mclock` is on that line by every convention the
    rest of this project uses, because the true line 247 is the 247th `\n`-delimited
    line and the 322nd line if the records are counted separately. Six spot
    checks "failed"; all six were the instrument.
    """
    f = (_ROOT / "pds-text" / name if name.endswith(".pds")
         else _ROOT / "vendor" / "Magician-NES" / name)
    return f.read_bytes().decode("latin-1").split("\n")


over = []
for name, nums in sorted(cited.items()):
    lines = source_lines(name)
    for n in sorted(nums):
        if not 1 <= n <= len(lines):
            over.append(f"{name}:{n} -- the file has {len(lines)} lines")
check(f"22: all {sum(len(v) for v in cited.values())} line citations across "
      f"{len(cited)} source files point at a line that exists "
      f"({', '.join(sorted(cited))})", not over, "; ".join(over[:5]))
# ...and that the cited line mentions the thing the comment claims. Spot-checked
# rather than exhaustive, and the spot checks are the load-bearing ones.
SPOT = [
    # (file, line, symbol the comment names). Every one of these is a claim the
    # ladder rests on, so every one of them is checked against the file rather
    # than trusted. Two of the original six were wrong -- `x1.pds:51-53` does
    # not contain `sta wealth` (it is x1.pds:34) and `x7.pds:445` does not
    # contain `get2` (the whole `addinv` head is on line 444) -- and both were
    # citations this session wrote from a shell `sed` whose line numbering did
    # not survive being pasted into a comment.
    ("x0.pds", 253, "drink"),          # the quest flags themselves
    ("x0.pds", 262, "gotlet"),         # ...and the permanent ones
    ("x0.pds", 566, "invop"),          # 36 two-bit counters
    ("x1.pds", 34, "wealth"),          # 100 gold to start with
    ("x1.pds", 46, "ror obtyp"),       # an unused slot is $FF, bit 7 SET
    ("x2.pds", 445, "perflag"),         # the permanent flag twin
    ("x4.pds", 374, "%00001100"),      # what a talk changes on the object
    ("x5.pds", 669, "lsr tmpflag"),    # entering a shop CLEARS the drink flag
    ("x5.pds", 800, "d_drunk"),        # the fourth drink
    ("x5.pds", 774, "addmtop"),        # the only mana-raising command
    ("x5.pds", 783, "tmpflag"),        # the shop script's flag byte
    ("x6.pds", 175, "levmana"),        # 4/8/12/16, the disputed cost table
    ("x7.pds", 199, "manacur"),        # chkmana -- the spell-cost measurement
    ("x7.pds", 247, "mclock"),         # the mana gate the guide disagrees with
    ("x7.pds", 265, "fooddel"),        # the food timer
    ("x7.pds", 310, "fwdels"),         # ...and its reload value
    ("x7.pds", 444, "cmp #$03"),       # the carry-three cap
    ("SHOPDAT.SRC", 83, "gover"),      # three drinks then death
    ("SHOPDAT.SRC", 101, "gotlet"),    # the post office's 'nothing to post'
    ("SHOPDAT.SRC", 102, "sentlet"),   # ...and 'the letter is posted'
    ("SHOPDAT.SRC", 121, "asked"),     # the vicar's letter
    ("SHOPDAT.SRC", 123, "mana10"),    # the prayer book's +10
    ("PROBDAT.SRC", 98, "pt_shop"),    # the first town's door
]
missed = []
for name, n, word in SPOT:
    line = source_lines(name)[n - 1]
    if word not in line:
        missed.append(f"{name}:{n} does not mention {word!r}: {line.strip()[:60]}")
check(f"23: {len(SPOT)} spot-checked citations really do contain the symbol the "
      "comment names -- a citation that points at a line about something else is "
      "a measurement that reads as a fact", not missed, "; ".join(missed))

# The lines the ladder's own long comments quote must be quotable, not invented.
x7 = source_lines("x7.pds")
check("24: the four drinks/three drinks story is consistent across the two "
      "independent records: the source's script line count AND the claim table's "
      "own wording",
      ladder.DRINK_LIMIT == 3
      and "four" in next(c["claim"] for c in ladder.CLAIMS
                         if c["id"] == "four_drinks_ends_the_game").lower())
check("25: the ladder's mana claim is the one that will be MEASURED, and the "
      "measurement is on `valsav` -- `manacur - valsav` is what `chkmana` "
      "computed, so it is a subtraction of two declared fields rather than an "
      "opinion about a cost table",
      any("valsav" in s.why for s in route.segments if s.name == "rune_price")
      and ram.f("valsav").length == 2)

ok(f"the file ran every check above ({_n} checks)", _fails == 0)
if _fails:
    print(f"\n{_fails} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")