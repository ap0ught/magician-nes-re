"""The four inspect levels, and what each one costs (issue #3).

    python3 src/testing/test_inspect_levels.py

Creation cost is g09's own arithmetic (x6.pds, the B-button rune path),
applied to this spell's row: one level rune and four element runes. Level
1 of that row was measured on the rebuild, mana 50 to 41. Levels 2-4 are
the same routine with the level rune changed; they were not watched on a
second playthrough. The test reads levmana and levmul out of the image and
fails if the totals are not 9, 20, 27 and 40.

A cast costs spelmana[slot] times the power firespell builds with `iny`
(1..4). mul8 returns that product in A. chkmana subtracts A as the low
byte and the high byte, which firespell loads from mul8's Y. For this
spell that is 1, 2, 3, 4.

The drawer is executed. The sprite banks have no digit glyphs, so the
health number is the segment count. Level 2 adds a pip when obven is not
zero (the harm path's beq). Level 3 adds a pip per shield byte that is
not zero; those counters are write-only, so the pip is the stored byte.
Level 4 adds one tile from obweak's four exits.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import nestrace  # noqa: E402

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


prg_path = ROOT / "asm" / "out" / "prg.bin"
sym_path = ROOT / "asm" / "out" / "mag.sym"
if not prg_path.is_file() or not sym_path.is_file():
    raise SystemExit("asm/out/{prg.bin,mag.sym} missing. Run `make rom`.")

prg = prg_path.read_bytes()
syms = {}
for line in sym_path.read_text().splitlines():
    if "=" not in line or "|" in line:
        continue
    k, _, v = line.partition("=")
    v = v.strip()
    if v.startswith("$"):
        try:
            syms[k.strip()] = int(v[1:], 16)
        except ValueError:
            pass


def need(name):
    if name not in syms:
        raise SystemExit(f"{name} is not in mag.sym")
    return syms[name]


# ---------------------------------------------------------------- creation
TABLE = bytes((4, 8, 12, 16, 2, 3, 2, 3))
hits = []
start = 0
while True:
    at = prg.find(TABLE, start)
    if at < 0:
        break
    hits.append(at)
    start = at + 1
check("levmana then levmul occurs once in the image", hits == [0x1E74B],
      " ".join(hex(h) for h in hits))
levmana = tuple(prg[hits[0]:hits[0] + 4])
levmul = tuple(prg[hits[0] + 4:hits[0] + 8])
check("the level rune costs 4, 8, 12, 16 and the multipliers are 2, 3, 2, 3",
      levmana == (4, 8, 12, 16) and levmul == (2, 3, 2, 3),
      f"{levmana} {levmul}")


def creation_cost(level):
    """Mana to enter this spell's five runes at level 1..4.

    Phase 0 charges levmana[level-1]. The first element rune charges
    (level)*2, or (level)*3 when levmul[level-1] is 3. Each of the other
    three element runes charges `level`.
    """
    index = level - 1
    first = level * (3 if levmul[index] == 3 else 2)
    return levmana[index] + first + level * 3


COSTS = {1: 9, 2: 20, 3: 27, 4: 40}
got = {level: creation_cost(level) for level in COSTS}
check("creating INSPECT costs 9, 20, 27, 40 mana at levels 1..4",
      got == COSTS, str(got))
check("level 1's 9 is the measured drop, 50 to 41",
      creation_cost(1) == 9, str(creation_cost(1)))

# ---------------------------------------------------------------- cast
RUN = bytes([1] * 40 + [0, 0])
at = prg.find(RUN)
check("spelmana is forty 1s and then two 0s",
      at >= 0 and prg.find(RUN, at + 1) < 0, hex(at))
check("slot 27's cast base is 1", prg[at + 0x1B] == 1, f"${prg[at + 0x1B]:02X}")

bus = nestrace.Bus(prg, bytes(0x2000), 4, False)
cpu = nestrace.CPU(bus)
products = []
for power in (1, 2, 3, 4):
    cpu.a, cpu.y = 1, power
    cpu.push(0x7F)
    cpu.push(0xFF)
    cpu.pc = need("mul8")
    for _ in range(80):
        cpu.step()
        if cpu.pc == 0x8000:
            break
    products.append((cpu.a, cpu.y))
check("a cast costs 1, 2, 3, 4 mana at levels 1..4",
      products == [(1, 0), (2, 0), (3, 0), (4, 0)], str(products))

# ---------------------------------------------------------------- drawer
for name, addr in (
        ("inspbody", 0xB92D), ("obtyp", 0x04F4), ("obchr", 0x0530),
        ("obhel", 0x0524), ("obven", 0x0528), ("obphysh", 0x0538),
        ("obxl", 0x0514), ("obxh", 0x0518), ("obyl", 0x051C),
        ("obyh", 0x0520), ("mapx", 0x0094), ("mapy", 0x0096),
        ("spr", 0x0200), ("sprind", 0x00B0), ("insppow", 0x0744)):
    check(f"{name} is at ${addr:04X}", need(name) == addr, f"${need(name):04X}")

BAR = 0xB2
VEN = 0xD8
ATTR = 0x02
# obweak exits: ethereal, explode-only, neither, damage.
CLASS = {0x8C: 0xC8, 0x0C: 0xD8, 0x2C: 0xD0, 0x1C: 0xDE}


def draw(obchr, power, obhel=0xFF, obven=0, shields=(0, 0, 0, 0),
         x=0x40, y=0x80, xh=0):
    ram = bytearray(0x800)
    ram[need("obtyp")] = 0x11
    ram[need("obchr")] = obchr
    ram[need("obhel")] = obhel
    ram[need("obven")] = obven
    ram[need("obxl")] = x
    ram[need("obxh")] = xh
    ram[need("obyl")] = y
    ram[need("obyh")] = 0
    for i, byte in enumerate(shields):
        ram[need("obphysh") + i * 4] = byte
    ram[need("insppow")] = power
    cpu.bus.ram[:] = ram
    cpu.bus.regs[7] = 1
    cpu.a = cpu.x = cpu.y = 0
    cpu.push(0x7F)
    cpu.push(0xFF)
    cpu.pc = need("inspbody")
    for _ in range(4000):
        cpu.step()
        if cpu.pc == 0x8000:
            break
    n = cpu.bus.ram[need("sprind")]
    spr = need("spr")
    out = []
    for i in range(n):
        o = spr + i * 4
        out.append((cpu.bus.ram[o + 3], cpu.bus.ram[o],
                    cpu.bus.ram[o + 1], cpu.bus.ram[o + 2]))
    return out


def tiles(sprites):
    return [s[2] for s in sprites]


bar = draw(0x1C, 1)
check("level 1 draws seven health segments and nothing else",
      len(bar) == 7 and set(tiles(bar)) == {BAR}
      and all(s[1] == 0x68 and s[3] == ATTR for s in bar)
      and [s[0] for s in bar] == [0x40 + 8 * i for i in range(7)],
      str(bar))
check("level 2 with no venom is the same bar",
      draw(0x1C, 2, obven=0) == bar, str(draw(0x1C, 2, obven=0)))
ven = draw(0x1C, 2, obven=1)
check("level 2 adds one venom pip under the bar when obven is set",
      ven[:7] == bar and ven[7] == (0x40, 0x70, VEN, ATTR), str(ven))

one = draw(0x1C, 3, shields=(0xFF, 0, 0, 0))
check("level 3 adds one pip for a non-zero shield byte",
      one[:7] == bar and one[7:] == [(0x40, 0x60, BAR, ATTR)], str(one))
four = draw(0x1C, 3, shields=(0xFF, 1, 2, 0xFF))
check("level 3 adds one pip per non-zero shield, in order",
      four[7:] == [(0x40 + 8 * i, 0x60, BAR, ATTR) for i in range(4)],
      str(four[7:]))
check("level 3 with every shield clear adds nothing",
      draw(0x1C, 3) == bar, str(draw(0x1C, 3)))

for obchr, tile in CLASS.items():
    got = draw(obchr, 4)
    mark = [s for s in got if s[1] == 0x58]
    check(f"level 4 obchr ${obchr:02X} adds tile ${tile:02X}",
          mark == [(0x40, 0x58, tile, ATTR)], str(got))

friendly = draw(0x1D, 1)
check("a friendly object at level 1 gets the plus and not the bar",
      len(friendly) == 5 and set(tiles(friendly)) == {BAR}
      and (0x40, 0x60, BAR, ATTR) in friendly, str(friendly))
check("a zero obhbar draws nothing, at any asked level",
      draw(0x1C, 4, obhel=0x1F, obven=9, shields=(0xFF,) * 4) == [],
      str(draw(0x1C, 4, obhel=0x1F, obven=9, shields=(0xFF,) * 4)))
check("an object with no interact control is not marked",
      draw(0x00, 4) == [], str(draw(0x00, 4)))
check("an object on the next screen is not marked",
      draw(0x1C, 4, xh=1) == [], str(draw(0x1C, 4, xh=1)))

print(f"\n{ok} checks passed")
