"""Slot 27 is a named spell whose cast enters our code (issue #3).

    python3 src/testing/test_spell_slot.py

Three overlays, each the size of the thing it replaces:

  * SPNAME.PDS writes 7 bytes at $CAC2. The cartridge's name there is
    " UNUSED", high-bit terminated, and FEATHERLITE begins on the next
    byte. "INSPECT" is the same length, so nothing after it moves.
  * SPSLOT.PDS writes the two halves of init-vector index $1B. The
    dispatcher at slot 0 $8F39 does `ldy $8F81,x / sty $13 / ldy $8FAB,x /
    sty $14 / jmp ($13)`. Index $1B used to be $9112, a one-byte rts whose
    following byte is live. The vector now holds `obinspect`.
  * INSPECT.PDS is that entry. A cast stores $5A at $0743 and returns.
    inspdraw, in the same run, is what the per-frame hook calls.

The rune table and `invsp` are not written. A fresh save still does not
have the spell equipped.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

import build as B  # noqa: E402
import dis6502  # noqa: E402

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


for module, entry, place in (
        ("INSPECT.PDS", "obinspect", 0xFB47),
        ("SPNAME.PDS", "sp27nm", 0xCAC2),
        ("SPSLOT.PDS", None, None)):
    path = ROOT / "src" / "magician" / module
    check(f"{module} exists", path.is_file(), str(path))
    check(f"{module} is listed in OUR_MODULES", module in B.OUR_MODULES,
          "add it to asm/build.py:OUR_MODULES")
    if entry is not None:
        meta = B.OUR_MODULES[module]
        check(f"{module} records {entry} at ${place:04X}",
              meta.get("entry") == entry and meta.get("expect_cpu") == place
              and bool(meta.get("expect_bytes")),
              f"{meta.get('entry')!r} {meta.get('expect_cpu')!r}")

check("SPSLOT.PDS records the two vector bytes in address order",
      B.OUR_MODULES["SPSLOT.PDS"].get("expect") == {"sp27lo": "47", "sp27hi": "FB"}
      and B.OUR_MODULES["SPSLOT.PDS"].get("slot") == 0,
      repr(B.OUR_MODULES["SPSLOT.PDS"].get("expect")))

check("manifest check reports no problem for the three new modules",
      not any(any(n in p for n in ("INSPECT.PDS", "SPNAME.PDS", "SPSLOT.PDS"))
              for p in B.check_our_manifest()),
      "; ".join(B.check_our_manifest()))

# Slot 0's recreate must run before the vector overlay, or the two pointer
# bytes are written and then replaced with the cartridge's $9112.
order = B.our_modules()
check("SLOT0.PDS is assembled before SPSLOT.PDS",
      order.index("SLOT0.PDS") < order.index("SPSLOT.PDS"),
      str([n for n in order if n in ("SLOT0.PDS", "SPSLOT.PDS")]))
check("SLOT14.PDS is assembled before SPNAME.PDS",
      order.index("SLOT14.PDS") < order.index("SPNAME.PDS"),
      str([n for n in order if n in ("SLOT14.PDS", "SPNAME.PDS")]))

prg_path = ROOT / "asm" / "out" / "prg.bin"
sym_path = ROOT / "asm" / "out" / "mag.sym"
if not prg_path.is_file() or not sym_path.is_file():
    print("  --   image checks skipped: asm/out/{prg.bin,mag.sym} not both "
          "present (run `make rom`)")
    print(f"\n{ok} checks passed")
    raise SystemExit(0)

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

check("obinspect resolved at $FB47", syms.get("obinspect") == 0xFB47,
      f"{syms.get('obinspect')!r}")

NAME = bytes.fromhex("49 4E 53 50 45 43 D4")
name_at = prg[0x1CAC2:0x1CAC2 + 7]
check("slot 27's name in the image is INSPECT, high bit on the T",
      name_at == NAME, name_at.hex(" "))
check("the bytes on either side of the name did not move",
      prg[0x1CAC1] == 0xCF and prg[0x1CAC9] == 0x46,
      f"before ${prg[0x1CAC1]:02X} after ${prg[0x1CAC9]:02X}")

# Dispatcher, so the table bases $8F81 / $8FAB are still what the vector
# indexes. File offset in slot 0 is cpu - $8000.
DISP = bytes.fromhex("BC 81 8F 84 13 BC AB 8F 84 14 6C 13 00")
check("the init dispatcher is still ldy/sty/ldy/sty/jmp-indirect",
      prg[0x8F39 - 0x8000:0x8F39 - 0x8000 + len(DISP)] == DISP,
      prg[0x8F39 - 0x8000:0x8F39 - 0x8000 + len(DISP)].hex(" "))

lo = prg[0x8F9B - 0x8000:0x8F9E - 0x8000]
hi = prg[0x8FC5 - 0x8000:0x8FC8 - 0x8000]
check("index $1B points at obinspect and its two neighbours do not",
      lo == bytes.fromhex("11 47 2E") and hi == bytes.fromhex("91 FB 91"),
      f"lo {lo.hex(' ')} hi {hi.hex(' ')}")
ptr = (hi[1] << 8) | lo[1]
check("that pointer is the obinspect the build resolved",
      ptr == syms.get("obinspect"), f"${ptr:04X}")

check("the old rts at $9112 is still an rts, and $9113 is still pha",
      prg[0x1112] == 0x60 and prg[0x1113] == 0x48,
      f"${prg[0x1112]:02X} ${prg[0x1113]:02X}")

placed = prg[0x1FB47:0x1FB4D]
check("the cast entry sets the latch and returns",
      placed == bytes.fromhex("A9 5A 8D 43 07 60"), placed.hex(" "))
got = tuple(dis6502.disasm_block(placed, 0xFB47))
check("that entry disassembles as lda/sta/rts",
      got == ((0xFB47, "A9 5A     lda #$5A"),
              (0xFB49, "8D 43 07  sta $0743"),
              (0xFB4C, "60        rts")),
      "\n         " + "\n         ".join(f"{a:04X}: {t}" for a, t in got))

hook = prg[0x1DFF0:0x1DFF6]
check("the sprite-wipe calls inspdraw and still jumps to emptypan",
      hook == bytes.fromhex("20 4D FB 4C 99 F1"), hook.hex(" "))
check("inspdraw and inspbody resolved where the hook and the body say",
      syms.get("inspdraw") == 0xFB4D and syms.get("inspbody") == 0xB92D
      and syms.get("insphook") == 0xDFF1,
      f"draw={syms.get('inspdraw')!r} body={syms.get('inspbody')!r} "
      f"hook={syms.get('insphook')!r}")
body = prg[0x392D:0x392D + 168]
check("the marker is 168 bytes at the slot-1 pad and the next byte is pad",
      len(body) == 168 and body[0] == 0xA2 and 0x60 in body
      and prg[0x392D + 168] == 0xFF,
      f"first ${body[0]:02X} after ${prg[0x392D + 168]:02X}")

import nestrace  # noqa: E402

bus = nestrace.Bus(prg, bytes(0x2000), 4, False)
cpu = nestrace.CPU(bus)
cpu.a, cpu.x, cpu.y = 0x3C, 0x1B, 0x02
cpu.push(0x7F)
cpu.push(0xFF)
cpu.pc = 0xFB47
for _ in range(8):
    cpu.step()
    if cpu.pc == 0x8000:
        break
check("executing obinspect sets the latch, returns, and keeps X and Y",
      cpu.pc == 0x8000 and (cpu.a, cpu.x, cpu.y) == (0x5A, 0x1B, 0x02)
      and bus.ram[0x0743] == 0x5A,
      f"pc=${cpu.pc:04X} a=${cpu.a:02X} x=${cpu.x:02X} y=${cpu.y:02X} "
      f"latch=${bus.ram[0x0743]:02X}")

print(f"\n{ok} checks passed")
