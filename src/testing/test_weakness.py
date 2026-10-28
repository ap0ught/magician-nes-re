"""WEAK.PDS — the drawing layer's vulnerability class (issue #3).

    python3 src/testing/test_weakness.py

Pins the fourth concrete piece of the shared drawing layer: `obweak`
(src/magician/WEAK.PDS, fixed $E000 window at $FB2E). It answers "what would
the bullet hit test do to object X?" with the test's own four exits, which
is the only per-enemy vulnerability the shipped game defines.

Measured, and re-checked here:

  * Slot 4 $8B94 (the collision routine entered at $8B0D) reads obchr and
    nothing else: bit 7 set means ethereal, bullets cannot hit; bits 5-4
    are the physical-bullet action (0 explode-only, 2 neither, 1 or 3 the
    damage path). That is the `c` macro's @1 and @3 (X0.PDS:163-169).
  * The four shield counters are written by the game and never read by it.
    A shield spell stores $FF on the player; actob clears the four bytes.
    The one absolute load in the image is inspect's level-3 pip.
  * speldam (x3.pds) is 42 zero bytes. The cartridge damage path is
    `lda #$E0 / bit harm` at slot 4 $8BEB — one constant, and bit does not
    call harm. Spell type picks an explosion animation (shexp), not a
    weakness.

Checks 1-8: source, manifest, placement, bytes, disassembly.
The rest re-check the measurement and EXECUTE the placed code in
tools/nestrace.py, once per obchars row and against obchr values the table
itself never stores (ethereal overriding a damaging action, and the unused
bullet-action value 3).

Execution needs asm/out/{prg.bin,mag.sym}; it says so when a build is missing.
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


MODULE = "WEAK.PDS"
SRC_FILE = ROOT / "src" / "magician" / MODULE

check("WEAK.PDS exists in src/magician/", SRC_FILE.is_file(), str(SRC_FILE))

check("WEAK.PDS is listed in OUR_MODULES", MODULE in B.OUR_MODULES,
      "add it to asm/build.py:OUR_MODULES")

check("manifest check reports no problem for WEAK.PDS",
      not any("WEAK.PDS" in p for p in B.check_our_manifest()),
      "; ".join(B.check_our_manifest()))

PLACE = B.OUR_MODULES[MODULE].get("expect_cpu")
ENTRY = B.OUR_MODULES[MODULE].get("entry")
WANT_HEX = ("BD 30 05 30 0B 29 30 F0 0A C9 20 F0 09 A9 03 60 "
            "A9 80 60 A9 01 60 A9 02 60")
check("the manifest records the placement, the entry and the bytes",
      PLACE == 0xFB2E and ENTRY == "obweak"
      and B.OUR_MODULES[MODULE].get("expect_bytes") == WANT_HEX,
      f"{PLACE!r} {ENTRY!r} {B.OUR_MODULES[MODULE].get('expect_bytes')!r}")

# Assemble standalone. obchr comes from the build's symbol table when one is
# on disk, else from the measured constant (X0.PDS:552, obchr = $0530).
sym_file = ROOT / "asm" / "out" / "mag.sym"
obchr = 0x0530


def read_sym_file() -> dict[str, int]:
    if not sym_file.is_file():
        return {}
    out: dict[str, int] = {}
    for line in sym_file.read_text().splitlines():
        m = re.fullmatch(r"(\w+) = \$(\w{4})", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


build_syms = read_sym_file()
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
asm.sym["obchr"] = obchr
try:
    asm.run_file(SRC_FILE, slot=15, origin=PLACE)
    assembled, err = True, ""
except Exception as e:  # noqa: BLE001
    assembled, err = False, f"{type(e).__name__}: {e}"

check("WEAK.PDS assembles without error", assembled, err)
check("WEAK.PDS resolves obweak at the placed origin",
      asm.sym.get(ENTRY) == PLACE,
      f"got {asm.sym.get(ENTRY)!r}, want 0x{PLACE:04X}")

offs = sorted(asm.writes_by_file.get(MODULE, ()))
placed = bytes(image[offs[0]:offs[-1] + 1]) if offs else b""
check("the assembly emits exactly the manifest's 25 bytes",
      placed.hex(" ").upper() == WANT_HEX,
      f"got {placed.hex(' ').upper()}")

# Slot 15's fixed window: CPU $E000 is file offset $1E000, so $FB2E is
# $1E000 + ($FB2E - $E000) = $1FB2E.
PRG_OFF = 0x1E000 + (PLACE - 0xE000)
check("the standalone image holds those bytes at the slot-15 file offset",
      bytes(image[PRG_OFF:PRG_OFF + len(placed)]) == placed,
      f"file ${PRG_OFF:05X}")

EXPECTED_DISASM = (
    (0xFB2E, "BD 30 05  lda $0530,x"),
    (0xFB31, "30 0B     bmi $FB3E"),
    (0xFB33, "29 30     and #$30"),
    (0xFB35, "F0 0A     beq $FB41"),
    (0xFB37, "C9 20     cmp #$20"),
    (0xFB39, "F0 09     beq $FB44"),
    (0xFB3B, "A9 03     lda #$03"),
    (0xFB3D, "60        rts"),
    (0xFB3E, "A9 80     lda #$80"),
    (0xFB40, "60        rts"),
    (0xFB41, "A9 01     lda #$01"),
    (0xFB43, "60        rts"),
    (0xFB44, "A9 02     lda #$02"),
    (0xFB46, "60        rts"),
)
got_disasm = tuple(dis6502.disasm_block(placed, PLACE))
check("disassembling the placed bytes is the hit test's four exits",
      got_disasm == EXPECTED_DISASM,
      "\n         " + "\n         ".join(f"{a:04X}: {t}" for a, t in got_disasm))


# --------------------------------------------------------------------------
# The measurement, re-derived from vendor source. The `c` macro
# (X0.PDS:169) is `db @1*$80+@2*$40+@3*$10+@4*$4+@5`, and @1/@3 are the
# two fields the hit test reads.

def eval_occ(text: str, name: str) -> int:
    m = re.search(rf"^{name}\s+equ\s+(\d+)", text, re.M)
    if not m:
        raise SystemExit(f"MISC.SRC does not define {name}")
    return int(m.group(1))


misc = (ROOT / "vendor" / "Magician-NES" / "MISC.SRC").read_text(encoding="latin-1")
rows = []
names = []
for m in re.finditer(r"^(?:\w+\s+)?\s*c\s+((?:oc_\w+\s*,\s*){4}oc_\w+)"
                     r"(?:\t|;)(.*)$", misc, re.M):
    rows.append([f.strip() for f in m.group(1).split(",")])
    names.append(m.group(2).strip().lstrip(";").strip())

check("obchars is still 44 rows and the macro still packs @1 into bit 7 "
      "and @3 into bits 5-4",
      len(rows) == 44
      and re.search(r"db @1\*\$80\+@2\*\$40\+@3\*\$10\+@4\*\$4\+@5",
                    (ROOT / "vendor" / "Magician-NES" / "X0.PDS")
                    .read_bytes()[0x200:].decode("latin-1", "replace")
                    .replace("\r\x00", "\n")) is not None,
      f"{len(rows)} rows")

table = {}
for i, fields in enumerate(rows):
    table[i] = ((eval_occ(misc, fields[0]) << 7)
                + (eval_occ(misc, fields[1]) << 6)
                + (eval_occ(misc, fields[2]) << 4)
                + (eval_occ(misc, fields[3]) << 2)
                + eval_occ(misc, fields[4]))


def classify(byte: int) -> int:
    """The hit test's own priority: ethereal first, then bits 5-4."""
    if byte & 0x80:
        return 0x80
    action = (byte >> 4) & 3
    if action == 0:
        return 0x01
    if action == 2:
        return 0x02
    return 0x03


by_name = {n: i for i, n in enumerate(names)}
named = {
    "spectre": 0x80,          # oc_phyn AND oc_bendn: ethereal wins
    "talking snake": 0x01,    # oc_beydn
    "talking tree": 0x02,     # oc_bendn
    "troll": 0x03,            # oc_beydy
    "victory machine": 0x80,
    "mushroom": 0x03,
}
missing = [n for n in named if n not in by_name]
check("the named types the header cites are still in obchars",
      not missing, ", ".join(missing))
bad_named = [f"{n}: class ${classify(table[by_name[n]]):02X}, want ${w:02X}"
             for n, w in named.items() if n in by_name
             and classify(table[by_name[n]]) != w]
check("those named types classify the way the hit test branches",
      not bad_named, "; ".join(bad_named))

speldam = re.sub(r"\s+", " ",
                  (ROOT / "pds-text" / "x3.pds").read_text(encoding="latin-1"))
m = re.search(r"speldam (.*?) ;", speldam)
# Two-digit tokens only. The word `hex` is three letters, so it is not a byte,
# and a looser `[0-9a-f]+` would swallow it and then fail fromhex on an odd nibble.
spel_bytes = (bytes(int(b, 16) for b in re.findall(r"\b[0-9A-Fa-f]{2}\b", m.group(1)))
              if m else b"")
check("the source's per-spell damage table is 42 zero bytes and unused "
      "as a weakness",
      spel_bytes == bytes(42),
      f"got {spel_bytes.hex(' ') if spel_bytes else 'no speldam'}")


# --------------------------------------------------------------------------
# The built image: the hit test, the shield census, and obweak executed.
#
prg_path = ROOT / "asm" / "out" / "prg.bin"
if not prg_path.is_file() or not sym_file.is_file():
    print("  --   image checks skipped: asm/out/{prg.bin,mag.sym} not both "
          "present (run `make rom`)")
    print(f"\n{ok} checks passed")
    raise SystemExit(0)

prg = prg_path.read_bytes()
check("the built image carries obweak at $FB2E (final readback)",
      prg[PRG_OFF:PRG_OFF + len(placed)] == placed,
      prg[PRG_OFF:PRG_OFF + len(placed)].hex(" ").upper())

# The collision routine's characteristic test, slot 4 file $08B94.
HIT = bytes.fromhex("BD 30 05 30 08 29 30 F0 57 C9 20 D0 05")
check("slot 4 $8B94 is still the obchr hit test this routine copies",
      prg[0x8B94:0x8B94 + len(HIT)] == HIT,
      prg[0x8B94:0x8B94 + len(HIT)].hex(" "))

# And the damage path really is a constant, not a table index.
DAMAGE = bytes.fromhex("A9 E0 2C 03 C3")
check("slot 4 $8BEB is `lda #$E0 / bit harm`, not a per-spell lookup",
      prg[0x8BEB:0x8BEB + len(DAMAGE)] == DAMAGE,
      prg[0x8BEB:0x8BEB + len(DAMAGE)].hex(" "))

# Absolute and absolute-indexed forms. A shield that was consulted would
# show up as one of these with an operand in $0538-$0547.
ABS_OPS = {
    0x0D, 0x0E, 0x2C, 0x2D, 0x2E, 0x4D, 0x4E, 0x6D, 0x6E, 0x8C, 0x8D, 0x8E,
    0xAC, 0xAD, 0xAE, 0xCC, 0xCD, 0xCE, 0xEC, 0xED, 0xEE,
    0x1D, 0x1E, 0x3D, 0x3E, 0x5D, 0x5E, 0x7D, 0x7E, 0x9D, 0xBC, 0xBD, 0xDD,
    0xDE, 0xFD, 0xFE, 0x19, 0x39, 0x59, 0x79, 0x99, 0xB9, 0xD9, 0xF9, 0xBE,
}
WRITES = {0x8C, 0x8D, 0x8E, 0x9D, 0x99, 0xCE, 0xDE, 0xEE, 0xFE}
shield_reads = []
shield_writes = []
for i in range(len(prg) - 2):
    op = prg[i]
    if op not in ABS_OPS:
        continue
    addr = prg[i + 1] | (prg[i + 2] << 8)
    if not 0x0538 <= addr <= 0x0547:
        continue
    slot = i // 0x2000
    (shield_writes if op in WRITES else shield_reads).append(
        (slot, i, op, addr))

# Eurocom writes the four bytes and never loads them. The one load is
# inspect's `lda obphysh,y` (opcode $B9), inside the slot-1 body.
body_at = 0x392D
body_len = len(bytes.fromhex(B.OUR_MODULES["INSPBODY.PDS"]["expect_bytes"]))
ours = [r for r in shield_reads if body_at <= r[1] < body_at + body_len]
theirs = [r for r in shield_reads if r not in ours]
check("Eurocom's code stores the four shields and never loads them",
      theirs == [] and len(shield_writes) == 8,
      f"{len(theirs)} other read(s), {len(shield_writes)} write(s): "
      + ", ".join(f"slot {s} ${i:05X} op ${op:02X} ${a:04X}"
                  for s, i, op, a in theirs + shield_writes))
check("the only shield load is inspect's lda obphysh,y",
      len(ours) == 1 and ours[0][0] == 1 and ours[0][2] == 0xB9
      and ours[0][3] == 0x0538,
      str(ours))

# The eight writes are the four player-spell stores and actob's four clears.
write_addrs = sorted(a for _, _, _, a in shield_writes)
check("those writes are the player slot ($053B/$3F/$43/$47) and the "
      "per-object bases ($0538/$3C/$40/$44)",
      write_addrs == [0x0538, 0x053B, 0x053C, 0x053F, 0x0540, 0x0543,
                      0x0544, 0x0547],
      " ".join(f"${a:04X}" for a in write_addrs))

obchars_addr = build_syms.get("obchars")
check("the build resolved obchars in the fixed $C000 window",
      obchars_addr is not None and 0xC000 <= obchars_addr < 0xE000,
      f"{obchars_addr!r}")
image_table = prg[0x10000 + obchars_addr:0x10000 + obchars_addr + 44]
check("the image's obchars matches the vendor `c` expansion",
      image_table == bytes(table[i] for i in range(44)),
      image_table.hex(" "))

import nestrace  # noqa: E402

bus = nestrace.Bus(prg, bytes(0x2000), 4, False)


def run_obweak(idx, byte, y=0x5A):
    cpu = nestrace.CPU(bus)
    cpu.x = idx & 0xFF
    cpu.y = y & 0xFF
    cpu.push(0x7F)
    cpu.push(0xFF)
    cpu.pc = PLACE
    bus.ram[obchr + (idx & 0xFF)] = byte & 0xFF
    for _ in range(32):
        cpu.step()
        if cpu.pc == 0x8000:
            return cpu.a, cpu.x, cpu.y
    raise AssertionError(f"obweak did not return: pc=${cpu.pc:04X}")


bad = []
for typ, byte in table.items():
    a, x, y = run_obweak(typ % 4, byte, y=0xA5)
    want = classify(byte)
    if (a, x, y) != (want, typ % 4, 0xA5):
        bad.append(f"${typ:02X}: got A=${a:02X} X={x} Y=${y:02X}, "
                   f"want A=${want:02X}")
check("obweak executed against all 44 obchars rows matches the hit test, "
      "and preserves X and Y",
      not bad, "; ".join(bad) or "44/44")

# States the table does not store, so a routine that only memorised the 44
# bytes would still pass the sweep above and be wrong on a live object.
live = [
    (0x10, 0x03),   # bits 5-4 = 1, not ethereal: damage path
    (0x30, 0x03),   # unused equate oc_unusd: the bne takes the damage path
    (0x00, 0x01),   # bits 5-4 = 0: explode, do not damage
    (0x20, 0x02),   # bits 5-4 = 2: neither
    (0x90, 0x80),   # ethereal overrides a damaging action
    (0xA0, 0x80),   # ethereal overrides "neither"
    (0xFF, 0x80),   # every other bit set still loses to bit 7
    (0x7F, 0x03),   # bit 7 clear, bits 5-4 = 3
]
got_live = [run_obweak(i % 4, b)[0] for i, (b, _) in enumerate(live)]
check("obweak follows the hit test on obchr values no table row holds",
      got_live == [w for _, w in live],
      "; ".join(f"obchr=${b:02X}: got ${g:02X}, want ${w:02X}"
                for (b, w), g in zip(live, got_live) if g != w))

print(f"\n{ok} checks passed")
