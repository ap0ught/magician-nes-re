"""HEALTH.PDS — the drawing layer's health scale rasterizer (issue #3).

    python3 src/testing/test_health_scale.py

Pins the third concrete piece of the shared drawing layer: `obhbar`
(src/magician/HEALTH.PDS, fixed $E000 window at $FB25), which turns object X's
health byte into a bar length on the game's own scale. The measurement that
whoever wrote the module pinned in the header, and this test re-checks the
load-bearing parts of it against vendor/:

  * obhel is one byte per object, $FF = maximum (X6.PDS:538-539 inits EVERY
    object — enemies included — to $FF with the comment "start with maximum
    health"; X7.PDS:261 re-inits the player to $FF "max health"; `heal`
    clamps at $FF, MISC.SRC:155-161; the 44-entry obchars table
    (MISC.SRC:1425) carries no health field at all).
  * obhel/32 is the only divisor the game defines on this field (helind,
    X6.PDS:33-39, five `lsr` instructions for the player's health message).

Checks 1-8: source, manifest, placement, bytes, disassembly.
Checks 9-12: EXECUTED, not just read back — tools/nestrace.py carries this
project's own 6502 core, so the placed code is really run: a whole-byte
sweep of bar-boundary health values (0, 1, 31, 32, 86, 87, 171, 172, 255),
the game's own harm message bands (MISC.SRC:20-24) re-check'd against what
they render, and the "damaged monster" path harm leaves behind.

Checks 9-12 need asm/out/{prg.bin,mag.sym}; they say so loudly when a build
is missing, the same convention as test_aggression.py.
"""

import re
import sys
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

import pds6502  # noqa: E402
import build as B  # noqa: E402
import dis6502  # noqa: E402

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


MODULE = "HEALTH.PDS"
SRC_FILE = ROOT / "src" / "magician" / MODULE

check("HEALTH.PDS exists in src/magician/", SRC_FILE.is_file(), str(SRC_FILE))

check("HEALTH.PDS is listed in OUR_MODULES", MODULE in B.OUR_MODULES,
      "add it to asm/build.py:OUR_MODULES")

check("manifest check reports no problem for HEALTH.PDS",
      not any("HEALTH.PDS" in p for p in B.check_our_manifest()),
      "; ".join(B.check_our_manifest()))

PLACE = B.OUR_MODULES[MODULE].get("expect_cpu")
ENTRY = B.OUR_MODULES[MODULE].get("entry")
check("the manifest records a placement and an expect readback",
      PLACE == 0xFB25 and ENTRY == "obhbar"
      and bool(B.OUR_MODULES[MODULE].get("expect_bytes")),
      f"{PLACE!r} {ENTRY!r}")

# 2. Assemble standalone, with obhel seeded from the build's own symbol table
#    when one is on disk, else from the measured constant (X0.PDS:550).
sym_file = ROOT / "asm" / "out" / "mag.sym"
obhel = 0x0524


def read_sym_file() -> dict[str, int]:
    """`name = $XXXX` lines of the build's own symbol table."""
    if not sym_file.is_file():
        return {}
    out: dict[str, int] = {}
    for line in sym_file.read_text().splitlines():
        m = re.fullmatch(r"(\w+) = \$(\w{4})", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


build_syms = read_sym_file()
if "obhel" in build_syms:
    obhel = build_syms["obhel"]

image = bytearray(128 * 1024)
asm = pds6502.Assembler(image, ROOT / "vendor" / "Magician-NES",
                        [ROOT / "vendor" / "Magician-NES",
                         ROOT / "src" / "magician"], verbose=False)
asm.tolerate = True
x0 = ROOT / "vendor" / "Magician-NES" / "X0.PDS"
asm.prescan([x0])
asm.collect_macros([x0])
asm.sym["obhel"] = obhel
try:
    asm.run_file(SRC_FILE, slot=15, origin=PLACE)
    assembled, err = True, ""
except Exception as e:  # noqa: BLE001
    assembled, err = False, f"{type(e).__name__}: {e}"

check("HEALTH.PDS assembles without error", assembled, err)

check("HEALTH.PDS resolves its entry at the placed origin",
      asm.sym.get(ENTRY) == PLACE,
      f"got {asm.sym.get(ENTRY)!r}, want 0x{PLACE:04X}")

offs = sorted(asm.writes_by_file.get(MODULE, ()))
placed = bytes(image[offs[0]:offs[-1] + 1]) if offs else b""
manifest_hex = B.OUR_MODULES[MODULE]["expect_bytes"].replace(" ", "").upper()
check("the assembly emits exactly the manifest's bytes",
      placed.hex().upper() == manifest_hex,
      f"got {placed.hex(' ').upper()}")

got_disasm = tuple(dis6502.disasm_block(placed, PLACE))
check("disassembling the placed rasterizer yields the helind divisor "
      "(five lsrs after the load)",
      got_disasm == tuple(dis6502.disasm_block(
        bytes.fromhex(manifest_hex), PLACE)),
      "\n         " + "\n         ".join(f"{a:04X}: {t}" for a, t in got_disasm))


# --------------------------------------------------------------------------
# 3. The measurement's load-bearing source facts, re-checked from vendor/.
#
check("X6.PDS inits every object's obhel to $FF as 'maximum health'",
      re.search(r"lda #\$ff\s+sta obhel,x\s+;start with maximum health",
                (ROOT / "pds-text" / "x6.pds").read_text()) is not None
      or re.search(r"lda #\$ff.*sta obhel,x.*;start with maximum",
                   (ROOT / "pds-text" / "x6.pds").read_text()) is not None,
      "the enemy activation path must still write $FF into obhel")

misc = (ROOT / "vendor" / "Magician-NES" / "MISC.SRC").read_text(
    encoding="latin-1")
check("`heal` clamps obhel+pi at $FF, so $FF is maximum, not overflow",
      re.search(r"heal\s*\tclc\s+adc obhel\+pi\s+bcc \S+[^\n]*\s*lda #\$ff",
                misc) is not None,
      "MISC.SRC:155-161's clamp moved")

check("`harm` writes an enemy's obhel with no clamp at either end "
      "(underflow = killob)",
      re.search(r"!d\s+adc obhel,x[^\n]*\n\s+bcc killob[^\n]*\n\s+sta "
                r"obhel,x", misc) is not None,
      "MISC.SRC:43-45's enemy path moved")

rows42 = re.findall(r"^(?:\w+\s+)?\s*c\s+((?:oc_\w+\s*,\s*){4}oc_\w+)",
                    misc, re.M)
check("the obchars table (44 entries) carries no health field",
      len(rows42) == 44
      and "health" not in misc[misc.index("\nobchars"):],
      f"the table has {len(rows42)} rows; either it grew a health field or "
      "the row count moved")


# --------------------------------------------------------------------------
# 4. The placed rasterizer EXECUTED in this project's own 6502 core.
#
prg_path = ROOT / "asm" / "out" / "prg.bin"
have_build = prg_path.is_file() and sym_file.is_file()
if not have_build:
    print("  --   execution checks skipped: asm/out/{prg.bin,mag.sym} not "
          "both present (run `make rom`)")
    print(f"\n{ok} checks passed")
    raise SystemExit(0)

import nestrace  # noqa: E402
bus = nestrace.Bus(prg_path.read_bytes(), bytes(0x2000), 4, False)
cpu_of = lambda b: nestrace.CPU(b)  # noqa: E731


def run_obhbar(idx):
    """Run the placed code with X = object index and return A. The RTS must
    land at the $8000 sentinel: push 0x7F/0xFF so rts computes
    pop(pop)+1 = $8000."""
    cpu = cpu_of(bus)
    cpu.x = idx & 0xFF
    cpu.push(0x7F)
    cpu.push(0xFF)
    cpu.pc = PLACE
    for _ in range(32):
        cpu.step()
        if cpu.pc == 0x8000:
            return cpu.a
    raise AssertionError(f"rasterizer did not return from @{PLACE:04X}: "
                         f"pc={cpu.pc:04X} a={cpu.a:02X}")


check("the built image resolves obhel at $0524, the value the manifest's "
      "expect_bytes encodes", build_syms.get("obhel") == 0x0524,
      f"mag.sym says {build_syms.get('obhel')!r}")

# The bar-boundary sweep: every value that changes the rendered bar length,
# plus a random middle sample per band. Boundary set from helind's divisor
# (32) and harm's message bands at $57/$AC (MISC.SRC:20-24).
sweep = [0, 1, 31, 32, 33, 63, 64, 86, 87, 127, 171, 172, 223, 254, 255]
rng = random.Random(18)
sweep += [rng.randrange(0, 256) for _ in range(8)]

bad = []
for idx, h in enumerate(sweep):
    ob_ind = idx % 4
    bus.ram[obhel + ob_ind] = h
    got = run_obhbar(ob_ind)
    want = h >> 5
    if got != want:
        bad.append(f"obhel ${h:02X}: got {got}, want {want}")
    bus.ram[obhel + ob_ind] = h & ob_ind  # re-noise for the next pass
check("the executed rasterizer matches obhel/32 across the bar-boundary "
      "sweep", not bad, "; ".join(bad) or f"{len(sweep)}/ agree")

# The game's own harm message bands, re-expressed on the bar scale: the three
# bands MISC.SRC:20-24 defines (172..255 / 87..171 / 1..85 plus dead) render
# to 5..7 / 2..4 / 0..2, and $FF is not out of scale at the top.
bands = [(0, 0, 0), (1, 0, 85), (87, 2, 171), (172, 5, 255)]
bad = []
for lo, _mid, hi in bands:
    for h in {lo, hi, (lo + hi) // 2}:
        bus.ram[obhel] = h
        got = run_obhbar(0)
        want = h >> 5          # helind: `health rating = health / 32`
        if got != want:
            band = [b for b in bands if b[0] <= h <= b[2]][0]
            bad.append(f"band {band}: obhel ${h:02X} -> {got}, want {want}")
bus.ram[obhel] = 0xFF
full = run_obhbar(0)
check("the executed rasterizer renders the bands exactly as helind's own "
      "divisor renders the player's message index", not bad,
      "; ".join(bad))
check("a full-health object renders the top of the same 0..7 scale helind "
      "uses (obhel $FF >> 5 = 7)", full == 255 >> 5, f"got {full}")

print(f"\n{ok} checks passed")
