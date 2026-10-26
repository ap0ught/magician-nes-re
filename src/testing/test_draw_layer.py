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

print(f"\n{ok} checks passed")
