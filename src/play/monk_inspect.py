#!/usr/bin/env python3
"""Cast Inspect on the wilderness monk, then make him attack.

    python3 src/play/monk_inspect.py

Not part of src/testing/run_all.py. That suite does not launch an emulator.

The monk in wilderness 2.0 (PROBDAT, x=$0080 y=$A1, type $11) is attack
control with count 1, so he ignores you until one interact. The interact
handler's !i5 path decrements that count (x4.pds). obaggro is 0 before and
1 after. A cast of slot 27 sets insplatch. At power 1 the mark is a green
glow while he is friendly and a red glow once he is aggressive. The bar
is level 2.

The first run replays logs/inputs/inspect-slot27.inputs.txt from power-on,
walks the town's east door (pt 0ea8,0000,0020,00b0, pt_dmap) onto the map,
and confirms into level $20. It then writes logs/saves/monk-before-cast.state
with the player already beside the monk and the latch still off. Later runs
load that state and cast from there.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from play import ram  # noqa: E402
from play.emu import BizHawk  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROM = ROOT / "asm" / "out" / "magician-rebuilt.nes"
LOG = ROOT / "logs" / "inputs" / "inspect-slot27.inputs.txt"
STATE = ROOT / "logs" / "saves" / "monk-before-cast.state"
# Addresses come from ram.py's fields, which resolve the assembler names.
# The latch bytes are the slack under x0.pds's RAM-overflow guard.
LATCH = ram.f("insplatch").addr
POWER = ram.f("insppow").addr
PULSE = ram.f("inspph").addr
LATCH_ON = 0x5A
MONK = 0x11
DOOR_X = 0x0EA8
WILD = 0x20
TOWN = 0x10


def say(text: str) -> None:
    print(text, flush=True)


def fail(emu: BizHawk, why: str) -> None:
    img = emu.work_ram()
    ph = ram.f("phase").get(img)
    say(f"FAIL: {why}")
    say(f"  phase={ph} ({ram.PHASE_NAMES.get(ph, '?')}) "
        f"curlev=${ram.f('curlev').get(img):02X} "
        f"at ({ram.plrx(img)},{ram.plry(img)}) "
        f"latch=${img[LATCH]:02X}")
    try:
        shot = emu.screenshot("monk-fail")
        say(f"  shot {shot}")
    except Exception as e:  # noqa: BLE001
        say(f"  no shot: {e}")
    raise SystemExit(2)


def monk_slot(img: bytes, obtyp: int) -> int | None:
    for i in range(3):
        if img[obtyp + i] == MONK:
            return i
    return None


def leave_shop(emu: BizHawk) -> bool:
    """Press A across the shop's icons until the phase is the town again.

    sexit (x5.pds) is one of the eight shop controls. B cancels the yes/no
    a purchase opens. Eight icons is the `and #$07` in g07a.
    """
    for _ in range(8):
        emu.tap("B")
        emu.step((), 6)
        if ram.f("phase").get(emu.work_ram()) == ram.PHASE_MAIN:
            return True
        emu.tap("A")
        emu.step((), 25)
        if ram.f("phase").get(emu.work_ram()) == ram.PHASE_MAIN:
            return True
        emu.tap("Right")
    return ram.f("phase").get(emu.work_ram()) == ram.PHASE_MAIN


def walk_to_door(emu: BizHawk) -> None:
    """Hold right until the east door opens the map.

    Shop doors sit on y $70-$B0. Landing in one is recovered by leaving,
    then stepping up so the next stretch of street is above them.
    """
    for n in range(400):
        emu.step(("Right",), 20)
        img = emu.work_ram()
        ph = ram.f("phase").get(img)
        if ph == ram.PHASE_MAP:
            say(f"map opened after {n + 1} stretches, "
                f"at ({ram.plrx(img)},{ram.plry(img)})")
            return
        if ph == ram.PHASE_SHOP:
            x, y = ram.plrx(img), ram.plry(img)
            say(f"shop at ({x},{y}); leaving")
            if not leave_shop(emu):
                fail(emu, "could not leave the shop")
            emu.step(("Up",), 40)
            say(f"  now ({ram.plrx(emu.work_ram())},"
                f"{ram.plry(emu.work_ram())})")
        elif ph not in (ram.PHASE_MAIN, ram.PHASE_CHANGE, ram.PHASE_FADE,
                        ram.PHASE_ENTER, ram.PHASE_FADE_UP, ram.PHASE_FADE2,
                        ram.PHASE_TUNE):
            fail(emu, f"left the street into phase {ph}")
    fail(emu, f"never reached the east door at x=${DOOR_X:04X}")


def confirm_map(emu: BizHawk) -> None:
    """g0a enters the next level on any key edge. The town's mapind makes
    that level $20. Do not hold A afterwards: A is also the cast button."""
    emu.step((), 40)
    for _ in range(4):
        if ram.f("phase").get(emu.work_ram()) != ram.PHASE_MAP:
            emu.step((), 20)
            continue
        emu.tap("A")
        used, held = emu.step_until(
            [ram.pred("phase", "eq", ram.PHASE_MAIN),
             ram.pred("curlev", "eq", WILD)],
            budget=300, what="wilderness")
        if held:
            say(f"wilderness after {used} frames")
            emu.step((), 30)
            return
    fail(emu, "the map did not enter level $20")


def walk_x(emu: BizHawk, target: int, budget: int = 2500) -> None:
    used = 0
    while used < budget:
        img = emu.work_ram()
        if ram.f("phase").get(img) != ram.PHASE_MAIN:
            fail(emu, "left the wilderness while walking")
        x = ram.plrx(img)
        if abs(x - target) <= 4:
            emu.step((), 12)
            return
        emu.step(("Right" if x < target else "Left",), 8)
        used += 8
    fail(emu, f"did not reach x=${target:04X}")


def beside_monk(img: bytes, obtyp: int, obchr: int, obxl: int, obxh: int):
    """The pre-cast moment: wilderness, spell equipped, latch off, monk at
    count 1, player on his left inside the interact range."""
    if ram.f("phase").get(img) != ram.PHASE_MAIN:
        return None
    if ram.f("curlev").get(img) != WILD:
        return None
    if (ram.f("plrspell").get(img) >> 2) != 0x1B:
        return None
    if img[LATCH] == LATCH_ON:
        return None
    slot = monk_slot(img, obtyp)
    if slot is None:
        return None
    ch = img[obchr + slot]
    if ch & 0x0C != 0x0C or ch & 0x03 != 0x01:
        return None
    mx = img[obxl + slot] + 256 * img[obxh + slot]
    x = ram.plrx(img)
    if not (mx - 0x28 <= x < mx):
        return None
    return slot, mx, ch, x, ram.plry(img)


def write_state(emu: BizHawk, img: bytes) -> tuple:
    """Save, reload, and return the bytes that have to survive the round trip."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if STATE.exists():
        STATE.unlink()
    reply = emu.cmd(f"save {STATE}")
    if not reply.startswith("ok ") or not STATE.exists() or STATE.stat().st_size < 32:
        fail(emu, f"savestate was not written: {reply}")
    saved = (
        ram.f("phase").get(img),
        ram.f("curlev").get(img),
        ram.f("plrspell").get(img),
        img[LATCH],
        ram.plrx(img),
        ram.plry(img),
    )
    emu.cmd(f"load {STATE}")
    back = emu.work_ram()
    loaded = (
        ram.f("phase").get(back),
        ram.f("curlev").get(back),
        ram.f("plrspell").get(back),
        back[LATCH],
        ram.plrx(back),
        ram.plry(back),
    )
    if loaded != saved:
        fail(emu, f"reloaded state does not match:\n  saved  {saved}\n"
                  f"  loaded {loaded}")
    say(f"saved {STATE} ({STATE.stat().st_size} bytes)")
    return saved


def reach_monk(emu: BizHawk, obtyp: int, obchr: int, obxl: int, obxh: int):
    frames = BizHawk.load_inputs(LOG)
    emu.run_inputs(frames)
    img = emu.work_ram()
    ph = ram.f("phase").get(img)
    lev = ram.f("curlev").get(img)
    spell = ram.f("plrspell").get(img)
    if ph != ram.PHASE_MAIN or lev != TOWN or (spell >> 2) != 0x1B:
        fail(emu, f"replay ended at phase {ph} curlev ${lev:02X} "
                  f"plrspell ${spell:02X}")
    if img[LATCH] == LATCH_ON:
        fail(emu, f"latch was ${img[LATCH]:02X} before any cast")
    say(f"in town at ({ram.plrx(img)},{ram.plry(img)}), "
        f"plrspell=${spell:02X}, latch ${img[LATCH]:02X}")

    walk_to_door(emu)
    confirm_map(emu)
    img = emu.work_ram()
    say(f"entered wilderness at ({ram.plrx(img)},{ram.plry(img)})")

    slot = monk_slot(img, obtyp)
    if slot is None:
        walk_x(emu, 128)
        img = emu.work_ram()
        slot = monk_slot(img, obtyp)
    if slot is None:
        types = " ".join(f"{img[obtyp + i]:02X}" for i in range(4))
        fail(emu, f"no monk (obtyp {types})")
    mx = img[obxl + slot] + 256 * img[obxh + slot]
    ch = img[obchr + slot]
    say(f"monk slot {slot} at x=${mx:04X} obchr=${ch:02X}")
    if ch & 0x0C != 0x0C or ch & 0x03 != 0x01:
        fail(emu, f"monk obchr=${ch:02X}, wanted attack control and count 1")

    # On his left, facing right: the !i2 range is 1..40 pixels.
    walk_x(emu, mx - 0x14)
    emu.step((), 16)
    img = emu.work_ram()
    found = beside_monk(img, obtyp, obchr, obxl, obxh)
    if found is None:
        fail(emu, "stopped short of the pre-cast spot")
    return found


def describe_sprites(img: bytes) -> str:
    """OAM tiles $74-$7D and anything else parked above the monk.

    spr is $0200 and sprind is $00B0. ram.f does not export those two;
    they are the sprite buffer, not a named gameplay field.
    """
    n = img[0x00B0]
    spr = 0x0200
    pal = img[0x04F2:0x04F4].hex()
    rows = [f"pal3={pal} sprind={n}"]
    for i in range(min(n, 64)):
        o = spr + i * 4
        y, tile, attr, x = img[o], img[o + 1], img[o + 2], img[o + 3]
        if 0x74 <= tile <= 0x7D or y < 0x60:
            rows.append(f"  {i}: y=${y:02X} t=${tile:02X} a=${attr:02X} x=${x:02X}")
    return "\n".join(rows)


def wait_bright(emu: BizHawk) -> int:
    """Step until the pulse is inside the bright half, past its first frame.

    The palette and the glow are uploaded at the next vblank, so phase 1
    is still showing the previous frame. Phase 2..20 has already been
    bright for a frame.
    """
    for _ in range(80):
        phase = emu.work_ram()[PULSE]
        if 2 <= phase <= 20:
            return phase
        emu.step((), 1)
    fail(emu, f"the pulse never went bright (phase=${emu.work_ram()[PULSE]:02X})")
    return 0


def wait_latch(emu: BizHawk) -> None:
    for _ in range(200):
        emu.step((), 1)
        if emu.work_ram()[LATCH] == LATCH_ON:
            emu.step((), 6)
            return
    fail(emu, "A did not set the inspect latch")


def main() -> int:
    if not ROM.exists():
        say(f"no ROM at {ROM}. Run `make rom` first.")
        return 2
    obtyp = ram.f("obtyp").addr
    obchr = ram.f("obchr").addr
    obxl = ram.f("obxl").addr
    obxh = ram.f("obxh").addr

    ram.load_button_order()
    stale = BizHawk.running_emuhawk()
    if stale and not os.environ.get("MAGICIAN_ALLOW_CONCURRENT"):
        say(f"REFUSING to start: mono pids {stale} are already running.")
        return 2

    if not STATE.exists() and not LOG.exists():
        say(f"no savestate at {STATE} and no input log at {LOG}")
        return 2
    with BizHawk(rom=ROM, log_name="monk_inspect", route="monk_inspect",
                 run="1") as emu:
        say(f"connected in {emu.connect_seconds:.1f}s")
        if STATE.exists():
            emu.cmd(f"load {STATE}")
            img = emu.work_ram()
            found = beside_monk(img, obtyp, obchr, obxl, obxh)
            if found is None:
                fail(emu, f"{STATE.name} is not the pre-cast spot")
            say(f"loaded {STATE.name}")
        else:
            found = reach_monk(emu, obtyp, obchr, obxl, obxh)
            img = emu.work_ram()
            write_state(emu, img)
            img = emu.work_ram()
            found = beside_monk(img, obtyp, obchr, obxl, obxh)
            if found is None:
                fail(emu, "the reloaded state is not the pre-cast spot")
        slot, mx, ch, x, y = found
        before = emu.screenshot("monk-before")
        say(f"before the cast, monk slot {slot} obchr=${ch:02X} "
            f"at ({x},{y}), monk x=${mx:04X}")

        emu.tap("A")
        wait_latch(emu)
        img = emu.work_ram()
        if img[obchr + slot] & 0x03 != 0x01:
            fail(emu, "the cast changed his count")
        phase = wait_bright(emu)
        friendly = emu.screenshot("monk-friendly")
        say(f"friendly phase=${phase:02X}")
        say(describe_sprites(emu.work_ram()))
        if img[POWER] != 1:
            fail(emu, f"level-1 cast stored power ${img[POWER]:02X}")
        say(f"friendly mark, obchr=${img[obchr + slot]:02X} "
            f"latch=${img[LATCH]:02X} power=${img[POWER]:02X}")

        emu.tap("Up")
        emu.step((), 12)
        img = emu.work_ram()
        if img[obchr + slot] & 0x03 != 0:
            say("still count 1 from the left; trying his right side")
            walk_x(emu, mx + 0x14)
            emu.step(("Left",), 6)
            emu.step((), 10)
            emu.tap("Up")
            emu.step((), 12)
            img = emu.work_ram()
        if img[obchr + slot] & 0x03 != 0:
            fail(emu, f"interact left obchr=${img[obchr + slot]:02X}")
        if img[obchr + slot] & 0x0C != 0x0C:
            fail(emu, f"interact cleared the attack control "
                      f"(obchr=${img[obchr + slot]:02X})")
        if img[LATCH] != LATCH_ON:
            fail(emu, "the latch dropped when he turned")
        emu.step((), 8)
        phase = wait_bright(emu)
        hostile = emu.screenshot("monk-bar")
        say(f"bar, obchr=${emu.work_ram()[obchr + slot]:02X} phase=${phase:02X}")
        say(describe_sprites(emu.work_ram()))

        journal = ROOT / "journal"
        bar_dest = journal / "18-monk-bar.png"
        friendly_dest = journal / "18-monk-friendly.png"
        shutil.copyfile(hostile, bar_dest)
        shutil.copyfile(friendly, friendly_dest)
        say(f"copied {bar_dest}")
        say(f"copied {friendly_dest}")
        say(f"shots {before} {friendly} {hostile}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
