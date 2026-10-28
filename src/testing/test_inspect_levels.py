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

The drawer is executed. The body recolor is on for inspph 1..30 and
off for 31..60, so the dim half leaves his own colours. Both pulses
OR attribute $03 onto his 10 standing sprites (tiles $74-$7D). Hostile
bright queues $06/$16 into cursprpal palette 3 (index 2 and 3);
friendly leaves the spr.pal green $2A/$3A. Neither adds a sprite, and
tile $B2 is not drawn above him. The bar stays on palette 2. Level 2
adds a pip when obven is not zero (the harm path's beq). Level 3 adds
a pip per shield byte that is not zero; those counters are write-only,
so the pip is the stored byte. Level 4 adds one tile from obweak's
four exits.
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
        ("spr", 0x0200), ("sprind", 0x00B0), ("insppow", 0x0744),
        ("inspph", 0x0745), ("inspalt", 0x0746), ("insppal", 0x0747),
        ("orgsprpal", 0x04C4), ("cursprpal", 0x04E4)):
    check(f"{name} is at ${addr:04X}", need(name) == addr, f"${need(name):04X}")

BAR = 0xB2
VEN = 0xD8
ATTR = 0x02
# His robe is palette 1 with the horizontal flip the frame stores.
# The pulse ORs on palette 3 and leaves the flip bit.
ROBE = 0x41
GLOW = 0x43
ORG = 0x04C4
CUR = 0x04E4
# spr.pal through movepal, X=$1F. Palette 3 is the last four bytes.
SPR = bytes.fromhex("0F0F2737 0F0F2636 0F0F1030 0F0F2A3A")
# obweak exits: ethereal, explode-only, neither, damage.
CLASS = {0x8C: 0xC8, 0x0C: 0xD8, 0x2C: 0xD0, 0x1C: 0xDE}
# Standing frame: two columns, five rows, tiles $74-$7D. A $74 that is
# not on that body, and one player sprite, must keep their attributes.
DECOY = [(0x08, 0x30, 0x74, 0x01), (0x18, 0x28, 0x12, 0x00)]


def standing(x, y, attr):
    """The monk's 10 sprites, in the order frame draws them."""
    rows = (0xEC, 0xF4, 0xFC, 0x04, 0x0C)
    cols = (0xF8, 0x00)
    out = []
    tile = 0x74
    for dy in rows:
        for dx in cols:
            out.append(((x + dx) & 0xFF, (y + dy) & 0xFF, tile, attr))
            tile += 1
    return out


def glowed(x=0x40, y=0x80):
    return standing(x, y, GLOW) + DECOY


def robed(x=0x40, y=0x80):
    return standing(x, y, ROBE) + DECOY


last_phase = 0
last_flag = 0
last_pal = bytes(16)


def draw(obchr, power, obhel=0xFF, obven=0, shields=(0, 0, 0, 0),
         x=0x40, y=0x80, xh=0, x2=None, obhel2=None, turn=0xFF,
         phase=0, flag=0, cur=None):
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
    # $FF is what this slack is at power-on. inc makes the first frame
    # visit object 0 first. phase 0 ticks to 1, which is the bright half.
    ram[need("inspalt")] = turn
    ram[need("inspph")] = phase
    ram[need("insppal")] = flag
    ram[ORG:ORG + 16] = SPR
    ram[CUR:CUR + 16] = SPR if cur is None else cur
    seed = standing(x, y, ROBE) + DECOY
    ram[need("sprind")] = len(seed)
    spr0 = need("spr")
    for i, (sx, sy, tile, attr) in enumerate(seed):
        o = spr0 + i * 4
        ram[o] = sy
        ram[o + 1] = tile
        ram[o + 2] = attr
        ram[o + 3] = sx
    if x2 is not None:
        ram[need("obtyp") + 1] = 0x11
        ram[need("obchr") + 1] = obchr
        ram[need("obhel") + 1] = obhel if obhel2 is None else obhel2
        ram[need("obxl") + 1] = x2
        ram[need("obyl") + 1] = y
    cpu.bus.ram[:] = ram
    cpu.bus.regs[7] = 1
    cpu.a = cpu.x = cpu.y = 0
    cpu.push(0x7F)
    cpu.push(0xFF)
    cpu.pc = need("inspbody")
    for _ in range(20000):
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
    global last_phase, last_flag, last_pal
    last_phase = cpu.bus.ram[need("inspph")]
    last_flag = cpu.bus.ram[need("insppal")]
    last_pal = bytes(cpu.bus.ram[CUR:CUR + 16])
    return out


BAR_ROW = [(0x40 + 8 * i, 0x68, BAR, ATTR) for i in range(7)]
SEEDED = len(glowed())

angry = draw(0x1C, 1)
check("level 1 hostile recolors his 10 sprites and adds none above him",
      angry == glowed() and len(angry) == SEEDED
      and [s[2] for s in angry[:10]] == list(range(0x74, 0x7E))
      and all(s[2] != BAR for s in angry)
      and all(s[3] == GLOW for s in angry[:10])
      and angry[10:] == DECOY
      and last_phase == 1 and last_flag == 0x5A
      and last_pal[0x0E] == 0x06 and last_pal[0x0F] == 0x16
      and last_pal[0x02:0x04] == bytes((0x27, 0x37))
      and last_pal[0x0A:0x0C] == bytes((0x10, 0x30)),
      f"{angry} phase={last_phase:02X} flag={last_flag:02X} pal={last_pal.hex()}")
friendly = draw(0x1D, 1)
check("level 1 friendly recolors the same sprites and leaves palette 3 green",
      friendly == glowed() and len(friendly) == SEEDED
      and last_flag == 0 and last_pal[0x0E:0x10] == bytes((0x2A, 0x3A)),
      f"{friendly} flag={last_flag:02X} pal={last_pal.hex()}")
dim = draw(0x1C, 1, phase=30, flag=0x5A,
           cur=bytes.fromhex("0F0F2737 0F0F2636 0F0F1030 0F0F0616"))
check("level 1 dim leaves his robe, restores the green, and advances",
      dim == robed() and last_phase == 31 and last_flag == 0
      and last_pal[0x0E:0x10] == bytes((0x2A, 0x3A)),
      f"{dim} phase={last_phase:02X} flag={last_flag:02X} pal={last_pal.hex()}")
check("a phase of $FF starts the bright half",
      draw(0x1D, 1, phase=0xFF) == glowed() and last_phase == 1)
check("a phase of 60 wraps back to the bright half",
      draw(0x1C, 1, phase=60) == glowed() and last_phase == 1
      and last_pal[0x0F] == 0x16)
dim_bar = draw(0x1C, 2, phase=30)
check("level 2 dim leaves the white bar and does not recolor him",
      dim_bar == robed() + BAR_ROW and last_phase == 31
      and last_pal[0x0A:0x0C] == bytes((0x10, 0x30)),
      str(dim_bar))
check("level 2 adds the white bar under the recolored body",
      draw(0x1C, 2, obven=0) == glowed() + BAR_ROW,
      str(draw(0x1C, 2, obven=0)))
check("level 2 adds the white bar under the friendly body too",
      draw(0x1D, 2) == glowed() + BAR_ROW, str(draw(0x1D, 2)))
ven = draw(0x1C, 2, obven=1)
check("level 2 adds one venom pip under the bar when obven is set",
      ven == glowed() + BAR_ROW + [(0x40, 0x70, VEN, ATTR)], str(ven))

one = draw(0x1C, 3, shields=(0xFF, 0, 0, 0))
check("level 3 adds one pip for a non-zero shield byte",
      one == glowed() + BAR_ROW + [(0x40, 0x60, BAR, ATTR)], str(one))
four = draw(0x1C, 3, shields=(0xFF, 1, 2, 0xFF))
check("level 3 adds one pip per non-zero shield, in order",
      four[SEEDED + 7:] == [(0x40 + 8 * i, 0x60, BAR, ATTR)
                            for i in range(4)],
      str(four[SEEDED + 7:]))
check("level 3 with every shield clear adds only the body and the bar",
      draw(0x1C, 3) == glowed() + BAR_ROW, str(draw(0x1C, 3)))

for obchr, tile in CLASS.items():
    got = draw(obchr, 4)
    mark = [s for s in got if s[1] == 0x58]
    check(f"level 4 obchr ${obchr:02X} adds tile ${tile:02X}",
          mark == [(0x40, 0x58, tile, ATTR)], str(got))

check("a zero obhbar keeps the recolor and draws no bar",
      draw(0x1C, 2, obhel=0x1F) == glowed(),
      str(draw(0x1C, 2, obhel=0x1F)))
check("a zero obhbar at level 4 still adds venom, shields and the class",
      draw(0x1C, 4, obhel=0x1F, obven=9, shields=(0xFF,) * 4) ==
      glowed()
      + [(0x40, 0x70, VEN, ATTR)]
      + [(0x40 + 8 * i, 0x60, BAR, ATTR) for i in range(4)]
      + [(0x40, 0x58, 0xDE, ATTR)],
      str(draw(0x1C, 4, obhel=0x1F, obven=9, shields=(0xFF,) * 4)))
check("an object with no interact control is not marked",
      draw(0x00, 4) == robed(), str(draw(0x00, 4)))
check("an object on the next screen is not marked",
      draw(0x1C, 4, xh=1) == robed(), str(draw(0x1C, 4, xh=1)))
edge = draw(0x1C, 2, x=0xF8)
check("x=$F8 recolors the body and does not wrap the bar",
      edge == glowed(0xF8) + [(0xF8, 0x68, BAR, ATTR)]
      and all(s[2] != BAR or s[1] == 0x68 for s in edge),
      str(edge))
edge3 = draw(0x1C, 3, x=0xF8, shields=(0xFF, 0xFF, 0xFF, 0xFF))
check("power 3 at x=$F8 draws the first shield pip and does not wrap",
      edge3 == glowed(0xF8) + [(0xF8, 0x68, BAR, ATTR), (0xF8, 0x60, BAR, ATTR)]
      and all(s[0] != 0x00 for s in edge3),
      str(edge3))
edge4 = draw(0x1C, 4, x=0xF8, shields=(0xFF, 0xFF, 0xFF, 0xFF))
check("power 4 at x=$F8 keeps the class tile on the anchor",
      edge4 == edge3 + [(0xF8, 0x58, 0xDE, ATTR)]
      and all(s[0] != 0x00 for s in edge4),
      str(edge4))


def bars_at(sprites, y=0x68):
    return [s for s in sprites if s[1] == y]


shared = draw(0x1C, 2, x2=0x80)
check("two full bars on one y draw seven tiles and not a clipped eighth",
      bars_at(shared) == BAR_ROW
      and shared[:SEEDED] == glowed()
      and all(s[1] != 0x48 for s in shared),
      str(bars_at(shared)))
other = draw(0x1C, 2, x2=0x80, turn=0x00)
check("the next frame draws the other object's complete bar",
      bars_at(other) == [(0x80 + 8 * i, 0x68, BAR, ATTR) for i in range(7)],
      str(bars_at(other)))
both = draw(0x1C, 2, x2=0x80, obhel2=0x20)
check("a one-segment bar still fits beside a full one",
      bars_at(both) == BAR_ROW + [(0x80, 0x68, BAR, ATTR)],
      str(bars_at(both)))

print(f"\n{ok} checks passed")
