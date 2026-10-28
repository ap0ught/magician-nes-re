"""AGGRO.PDS — the drawing layer's aggression predicate (issue #3).

    python3 src/testing/test_aggression.py

Pins the second concrete piece of the shared drawing layer: `obaggro`
(src/magician/AGGRO.PDS, fixed $E000 window at $FB0A), which answers "is
object X aggressive right now?" the way the game's own interaction handler
does — obtyp bit 7 (active, X0.PDS:542) plus obchr's attack-control == 3
(`oc_attak`, MISC.SRC:1418) AND attack-count == 0 (X4.PDS:353-357, X4.PDS:403).
obmod is deliberately not read (AGGRO.PDS's header records the measurement
that found the mode table carries movement/animation, not hostility).

Checks 1-6: source, manifest, placement, bytes, disassembly.
Checks 7-9: EXECUTED, not just read back. tools/nestrace.py carries this
            project's own 6502 core, so the placed code is really run against
            synthetic object tables — one case per type built from the game's
            own `obchars` table (MISC.SRC:1425, 44 entries; expounded by the
            constants at MISC.SRC:1405-1424), plus per-instance states the
            game reaches. The table is cross-checked against the image at
            `obchars = $D90D` (asm/out/mag.sym).

Checks 7-9 need asm/out/{prg.bin,mag.sym}; they say so loudly when a build is
missing, the same convention as test_cross_module_tables.py.
"""

import re
import sys
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


MODULE = "AGGRO.PDS"
SRC_FILE = ROOT / "src" / "magician" / MODULE

check("AGGRO.PDS exists in src/magician/", SRC_FILE.is_file(), str(SRC_FILE))

check("AGGRO.PDS is listed in OUR_MODULES", MODULE in B.OUR_MODULES,
      "add it to asm/build.py:OUR_MODULES")

check("manifest check reports no problem for AGGRO.PDS",
      not any("AGGRO.PDS" in p for p in B.check_our_manifest()),
      "; ".join(B.check_our_manifest()))

PLACE = B.OUR_MODULES[MODULE].get("expect_cpu")
ENTRY = B.OUR_MODULES[MODULE].get("entry")
check("the manifest records a placement and an expect readback",
      PLACE == 0xFB0A and ENTRY == "obaggro"
      and bool(B.OUR_MODULES[MODULE].get("expect_bytes")),
      f"{PLACE!r} {ENTRY!r}")

# 2. Assemble standalone, with obtyp/obchr seeded from the build's own symbol
#    table when one is on disk, else from the measured constants (X0.PDS:542 /
#    X0.PDS:552; the same values mag.sym prints).
sym_file = ROOT / "asm" / "out" / "mag.sym"
obtyp, obchr = 0x04F4, 0x0530


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
if "obtyp" in build_syms:
    obtyp = build_syms["obtyp"]
if "obchr" in build_syms:
    obchr = build_syms["obchr"]

image = bytearray(128 * 1024)
asm = pds6502.Assembler(image, ROOT / "vendor" / "Magician-NES",
                        [ROOT / "vendor" / "Magician-NES",
                         ROOT / "src" / "magician"], verbose=False)
asm.tolerate = True
x0 = ROOT / "vendor" / "Magician-NES" / "X0.PDS"
asm.prescan([x0])
asm.collect_macros([x0])
asm.sym["obtyp"] = obtyp
asm.sym["obchr"] = obchr
try:
    asm.run_file(SRC_FILE, slot=15, origin=PLACE)
    assembled, err = True, ""
except Exception as e:  # noqa: BLE001
    assembled, err = False, f"{type(e).__name__}: {e}"

check("AGGRO.PDS assembles without error", assembled, err)

check("AGGRO.PDS resolves its entry at the placed origin",
      asm.sym.get(ENTRY) == PLACE,
      f"got {asm.sym.get(ENTRY)!r}, want 0x{PLACE:04X}")

offs = sorted(asm.writes_by_file.get(MODULE, ()))
placed = bytes(image[offs[0]:offs[-1] + 1]) if offs else b""
manifest_hex = B.OUR_MODULES[MODULE]["expect_bytes"].replace(" ", "").upper()
check("the assembly emits exactly the manifest's bytes",
      placed.hex().upper() == manifest_hex,
      f"got {placed.hex(' ').upper()}")

got_disasm = tuple(dis6502.disasm_block(placed, PLACE))
check("disassembling the placed predicate yields the interaction "
      "handler's own branch",
      got_disasm == tuple(dis6502.disasm_block(
        bytes.fromhex(manifest_hex), PLACE)),
      "\n         " + "\n         ".join(f"{a:04X}: {t}" for a, t in got_disasm))


# --------------------------------------------------------------------------
# 3. The game's own table, parsed from vendor/, and the predicate EXECUTED.
#
# Expand the `c` macro exactly as X0.PDS:165-168 defines it and the field
# equates exactly as MISC.SRC:1405-1424 states them:
#
#     db @1*$80 + @2*$40 + @3*$10 + @4*$4 + @5
#
# where @4 is attack/interact control (per MISC.SRC:1418, `oc_attak equ 3;
# "he/she ignores you." cnt=0:attack`) and @5 the inter/attack cnt.

def obchars_from_misc() -> dict[int, int]:
    """44 `c @1,...,@5 ;comment` lines of obchars, expanded per the macro."""
    text = (ROOT / "vendor" / "Magician-NES" / "MISC.SRC").read_text(
        encoding="latin-1")
    # The table's own shape: entry 0 is `obchars\tc ...`, every later entry a
    # lone `\tc ...`, each with a `;name` comment (MISC.SRC:1425+).
    rows = []
    for m in re.finditer(r"^(?:\w+\s+)?\s*c\s+((?:oc_\w+\s*,\s*){4}oc_\w+)"
                         r"(?:\t|;)(.*)$", text, re.M):
        rows.append([f.strip() for f in m.group(1).split(",")])
    if len(rows) != 44:
        raise SystemExit(f"expected 44 obchars rows in MISC.SRC, found {len(rows)}"
                         "; the table moved and this test must be re-derived")
    return {i: (eval_occ(fields[0]) << 7) + (eval_occ(fields[1]) << 6)
            + (eval_occ(fields[2]) << 4) + (eval_occ(fields[3]) << 2)
            + eval_occ(fields[4]) for i, fields in enumerate(rows)}


def eval_occ(name: str) -> int:
    """The value MISC.SRC:1405-1424 gives an `oc_` constant."""
    permissive = (ROOT / "vendor" / "Magician-NES" / "MISC.SRC").read_text(
        encoding="latin-1")
    m = re.search(rf"^{name}\s+equ\s+(\d+)", permissive, re.M)
    if not m:
        raise SystemExit(f"MISC.SRC does not define {name}")
    return int(m.group(1))


table_bytes = obchars_from_misc()
aggressive_by_type = {typ: (1 if ((byte >> 2) & 3) == 3 and (byte & 3) == 0
                            else 0) for typ, byte in table_bytes.items()}
members = [i for i, v in aggressive_by_type.items() if v == 1]
check("the game's own table names the monsters and only the monsters as "
      "ctrl=3/cnt=0 aggressive",
      members == [0x05, 0x09, 0x0C, 0x14, 0x15, 0x16, 0x1A, 0x1C, 0x1D,
                  0x1E, 0x1F, 0x20, 0x22, 0x27],
      f"got " + ", ".join(f"${i:02X}" for i in members))

prg_path = ROOT / "asm" / "out" / "prg.bin"
have_build = prg_path.is_file() and sym_file.is_file()
if not have_build:
    print("  --   execution checks skipped: asm/out/{prg.bin,mag.sym} not "
          "both present (run `make rom`)")
    print(f"\n{ok} checks passed")
    raise SystemExit(0)

prg = prg_path.read_bytes()
obchars_addr = build_syms.get("obchars")
check("the build resolved obchars where asm/out/mag.sym says",
      obchars_addr is not None and 0xC000 <= obchars_addr < 0xE000,
      f"obchars = {obchars_addr!r}")
obchars_file = 0x10000 + obchars_addr          # fixed $C000-$DFFF -> slot 14
image_bytes = prg[obchars_file:obchars_file + len(table_bytes)]
check("the built image's obchars table is byte-identical to MISC.SRC's "
      "`c` expansion",
      image_bytes == bytes(table_bytes.values()),
      "the per-type characteristic table in the image and the vendor source "
      "disagree: one of them changed")


def run_obaggro(bus, make_cpu, idx):
    """Run the placed code with X = object index and return A. Entry at
    obaggro; the RTS must land at the $8000 sentinel: push 0x7F/0xFF so
    rts computes pop(pop)+1 = $8000."""
    cpu = make_cpu(bus)
    cpu.x = idx & 0xFF
    cpu.push(0x7F)
    cpu.push(0xFF)
    cpu.pc = PLACE
    for _ in range(32):
        cpu.step()
        if cpu.pc == 0x8000:
            return cpu.a
    raise AssertionError(f"predicate did not return from @{PLACE:04X}: "
                         f"pc={cpu.pc:04X} a={cpu.a:02X}")


import nestrace  # noqa: E402
bus = nestrace.Bus(prg, bytes(0x2000), 4, False)
cpu_of = lambda b: nestrace.CPU(b)  # noqa: E731

# Zero the synthetic object table first: obscan-style code would read stale
# bytes otherwise.
for i in range(4):
    bus.ram[obtyp + i] = 0x80        # bit 7 set = unused (X1.PDS:46)
    bus.ram[obchr + i] = 0x00

# One run per type: an object slot carries the type and its table byte.
bad = []
for typ, want in aggressive_by_type.items():
    ob_ind = typ % 4
    bus.ram[obtyp + ob_ind] = typ
    bus.ram[obchr + ob_ind] = table_bytes[typ]
    got = run_obaggro(bus, cpu_of, ob_ind)
    if got != want:
        bad.append(f"type ${typ:02X}: got {got}, want {want}")
check("the executed predicate's verdict matches every one of the game's own "
      "44 type entries", not bad, "; ".join(bad) or "44/44 agree")


def run_state(typv, chrv, idx):
    bus.ram[obtyp + idx] = typv
    bus.ram[obchr + idx] = chrv
    return run_obaggro(bus, cpu_of, idx)


states = [
    # (obtyp value, obchr value, index, want) — what the state means
    (0x85, 0x5C, 0, 0),   # bit 7 set = unused object (X1.PDS:46)
    (0x01, 0x5C, 1, 1),   # a fresh skeleton, active: attacks on contact
    (0x05, 0x5D, 2, 0),   # monk-type ctrl=3 but cnt=1: not yet
    (0x05, 0x5C, 3, 1),   # mushroom-type fresh: attacks (oc_attak c0)
    (0x06, 0x50, 0, 0),   # tree-type ctrl=0: "always no one there"
    (0x01, 0x58, 1, 0),   # boatman-type ctrl=1: ignores you, never attacks
    (0x15, 0x5F, 2, 0),   # werebat filling in its cntr: ctrl=3 cnt=3, not yet
    (0x01, 0xFF, 3, 0),   # ctrl=3 cnt=3 double-pass, like barbarian's c3
]
got = [run_state(*s[:3]) for s in states]
check("the executed predicate answers the per-instance states the game "
      "itself reaches",
      got == [s[3] for s in states],
      "; ".join(f"obtyp=${s[0]:02X} obchr=${s[1]:02X}: got {g}, want {s[3]}"
                for s, g in zip(states, got) if g != s[3]))

print(f"\n{ok} checks passed")
