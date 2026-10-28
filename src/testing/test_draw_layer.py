"""Shared drawing layer scaffold (src/magician/DRAW.PDS).

    python3 src/testing/test_draw_layer.py

Pins the first concrete piece of the shared drawing layer: a horizontal fill
primitive that both enemy health bars and the status HUD will call, assembled
from our own source into the fixed $E000 window. It must assemble cleanly,
resolve its entry label, and emit the bytes the manifest records -- without
touching vendor/.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

os.chdir(ROOT)

import pds6502  # noqa: E402
import build as B  # noqa: E402

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


MODULE = "DRAW.PDS"
SRC_FILE = ROOT / "src" / "magician" / MODULE

check("DRAW.PDS exists in src/magician/", SRC_FILE.is_file(),
      str(SRC_FILE))

check("DRAW.PDS is listed in OUR_MODULES", MODULE in B.OUR_MODULES,
      "add it to asm/build.py:OUR_MODULES")

check("manifest check reports no problem for DRAW.PDS",
      not any("DRAW.PDS" in p for p in B.check_our_manifest()),
      B.check_our_manifest())

text = SRC_FILE.read_text(encoding="utf-8")
check("module defines a drawfill entry point",
      "drawfill" in text, "entry label missing")
check("module writes to the PPU data port ($2007)",
      "$2007" in text, "no PPU write")
check("module terminates with rts",
      "rts" in text.lower(), "no rts")

image = bytearray(128 * 1024)
asm = pds6502.Assembler(image, ROOT / "vendor" / "Magician-NES",
                        [ROOT / "vendor" / "Magician-NES",
                         ROOT / "src" / "magician"], verbose=False)
asm.tolerate = True
asm.prescan([ROOT / "vendor" / "Magician-NES" / "X0.PDS"])
asm.collect_macros([ROOT / "vendor" / "Magician-NES" / "X0.PDS"])
try:
    asm.run_file(SRC_FILE, slot=15, origin=0xFAFF)
    assembled = True
    err = ""
except Exception as e:  # noqa: BLE001
    assembled = False
    err = f"{type(e).__name__}: {e}"

check("DRAW.PDS assembles without error", assembled, err)
check("DRAW.PDS resolves the drawfill label",
      asm.sym.get("drawfill") == 0xFAFF,
      f"got {asm.sym.get('drawfill')!r}")
check("DRAW.PDS emits at least one byte",
      bool(asm.writes_by_file.get(MODULE)), "emitted nothing")
off = 15 * 8192 + (0xFAFF - 0xE000)
check("first emitted byte is the cpx opcode ($E0)",
      image[off] == 0xE0, f"got ${image[off]:02X}")

# --- the placed code, and the order that keeps it in the ROM ---------------
#
# Two failures are worth pinning, both of which shipped at once in the
# scaffold's first build (README/journal 18, iteration 2):
#
#   * the assemble ORDER. A SLOTx.PDS recreate writes every byte of its slot,
#     so a module of ours placed inside that slot survives only when it is
#     assembled after the recreate. our_modules() used to sort DRAW.PDS
#     alphabetically in front of SLOT15.PDS, which erased the 11 bytes and
#     still reported a 100.00% byte match -- both sides carrying Beta 1's own
#     $FF padding at $FAFF.

# The machine code the module must emit: 11 bytes at $FAFF-$FB09, and the
# disassembly the same 11 bytes must give back. This is also what the build's
# expect_bytes readback in asm/build.py demands of the finished image, so the
# two must not be edited apart.
PLACE = 0xFAFF
prg_off = 15 * 8192 + (PLACE - 0xE000)
placed = bytes(image[prg_off:prg_off + 11])
want_hex = "E0 00 F0 06 8D 07 20 CA D0 FA 60"

check("the manifest records the placed bytes (expect_bytes)",
      B.OUR_MODULES[MODULE].get("expect_bytes") == want_hex,
      f"got {B.OUR_MODULES[MODULE].get('expect_bytes')!r}, want {want_hex!r}")

check("the assembled image holds the manifest's code at the placement",
      placed.hex(" ").upper() == want_hex,
      f"got {placed.hex(' ').upper()}, want {want_hex!r}")

import dis6502  # noqa: E402

EXPECTED_DISASM = (
    (0xFAFF, "E0 00     cpx #$00"),
    (0xFB01, "F0 06     beq $FB09"),
    (0xFB03, "8D 07 20  sta $2007 <PPUDATA>"),
    (0xFB06, "CA        dex"),
    (0xFB07, "D0 FA     bne $FB03"),
    (0xFB09, "60        rts"),
)

got_disasm = tuple(dis6502.disasm_block(placed, PLACE))
check("disassembling the placed address yields the expected instructions",
      got_disasm == EXPECTED_DISASM,
      "\n         " + "\n         ".join(f"{a:04X}: {t}" for a, t in got_disasm))

check("a slot recreate is assembled before DRAW.PDS (the clobber order)",
      B.our_modules().index("SLOT15.PDS") < B.our_modules().index(MODULE),
      f"order got {[n for n in B.our_modules() if 'SLOT15' in n or n == MODULE]}")

# And the strongest statement available when a build is on disk: the FINAL
# image carries the code, not just a standalone assembly of it. Stale builds
# are not hidden -- this only checks when out/prg.bin exists, and the build's
# own expect_bytes readback is the gate that can never be stale.
prg_path = ROOT / "asm" / "out" / "prg.bin"
if prg_path.is_file():
    final = bytes(prg_path.read_bytes())
    at_place = final[0x1FAFF:0x1FAFF + 11]
    check("the built image carries the placed code (final readback)",
          at_place == placed,
          f"got {at_place.hex(' ').upper()}, want {placed.hex(' ').upper()}")
else:
    print("  -- no asm/out/prg.bin; skipped the final-image readback (run make)")

# An empty module that still declares expect_bytes used to skip the readback
# (`if not offs: continue` ran first) and the build reported success. The
# stub writes nothing for every module, so the first declared placement is
# the one that has to fail.
class _NoWrites:
    writes_by_file = {}
    sym = {}


try:
    B.verify_our_scenes(_NoWrites(), bytearray(1), b"", [])
    empty_rejected = False
    empty_detail = "verify_our_scenes returned"
except SystemExit as e:
    empty_detail = str(e)
    empty_rejected = "wrote no bytes" in empty_detail and "expect_bytes" in empty_detail
check("an empty module with expect_bytes is rejected",
      empty_rejected, empty_detail)

print(f"\n{ok} checks passed")
