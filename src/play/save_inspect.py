#!/usr/bin/env python3
"""Create a savestate in which spell slot 27 has been learned.

    python3 src/play/save_inspect.py

This is not part of `src/testing/run_all.py`. That suite does not launch an
emulator. This script drives BizHawk, lets the game create the spell, and
writes the state only after the game's own bytes say the slot was created.

The rebuild's header battery bit is clear, the same as Beta 1, so BizHawk
will not load an `NES/SaveRAM` file for it. The file this writes is a
savestate. Load it with the bridge's `load`, or from BizHawk's state menu.

What "created" means, measured on the rebuild, 2026-10-27:

  * The rune nybbles are read out of the image at `spells` + 27*3. They are
    not typed in here. A trailing 0 nybble is the unused tail of the six-rune
    buffer, and the script does not try to enter it: once the power rune is
    committed the tail is already 0, and a later phase will not accept rune 0.
  * START runs `newspell`, which stores the index in `bookcur` and `topspell`.
  * SELECT leaves g09 and writes `plrspell`. The spell number is `plrspell`
    shifted right twice, which is how `ispell` reads it back.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from play import ram  # noqa: E402
from play.emu import BizHawk  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROM = ROOT / "asm" / "out" / "magician-rebuilt.nes"
SYM = ROOT / "asm" / "out" / "mag.sym"
TOWN = ROOT / "logs" / "inputs" / "m1_beta1.inputs.txt"
STATE = ROOT / "logs" / "saves" / "inspect-slot27.state"
SLOT = 0x1B  # sptxt index 27, between SLOWMO and FEATHERLITE

# The seven bytes SPNAME.PDS places at sp27nm. High bit terminates the T.
INSPECT = bytes((0x49, 0x4E, 0x53, 0x50, 0x45, 0x43, 0xD4))


def say(text: str) -> None:
    print(text, flush=True)


def rom_sym(name: str) -> int:
    """A label from `asm/out/mag.sym`.

    `ram.sym` reads `zpmap.txt`, which stops at $07FF, so a ROM label such as
    `spells` or `sp27nm` is not in it. The address still has to come from the
    assembler that built the ROM this script is about to boot.
    """
    if not SYM.exists():
        raise SystemExit(f"no symbol table at {SYM}. Run `make rom` first.")
    for line in SYM.read_text(encoding="latin-1").splitlines():
        m = re.match(rf"^{re.escape(name)}\s*=\s*\$([0-9A-Fa-f]+)\s*$", line)
        if m:
            return int(m.group(1), 16)
    raise SystemExit(f"{name} is not in {SYM.name}")


def prg_of(rom: pathlib.Path) -> bytes:
    raw = rom.read_bytes()
    if raw[:4] == b"NES\x1a":
        raw = raw[16:]
    return raw


def fixed_byte(image: bytes, addr: int, n: int) -> bytes:
    """`n` bytes at a CPU address in the fixed $C000/$E000 window.

    File offset = CPU address + $10000, which is `tools/align6502.py`'s
    `cpu_to_file`. It is only meaningful from $C000 up.
    """
    if not 0xC000 <= addr <= 0xFFFF or addr + n > 0x10000:
        raise SystemExit(f"${addr:04X}+{n} is not in the fixed window")
    off = addr + 0x10000
    return image[off:off + n]


def rune_nybbles(image: bytes) -> list[int]:
    """The six nybbles of spell 27, from the image, then the ones you enter.

    `expspell0` unpacks each byte high nybble then low nybble. The power rune
    is first. A 0 after that is the buffer's empty tail, not a rune the spell
    screen will accept in a later phase.
    """
    raw = fixed_byte(image, rom_sym("spells") + SLOT * 3, 3)
    nybbles = []
    for b in raw:
        nybbles.append(b >> 4)
        nybbles.append(b & 0x0F)
    if not nybbles or nybbles[0] > 3:
        raise SystemExit(f"spell {SLOT:#04x} does not start with a power rune: "
                         f"{nybbles}")
    enter = [nybbles[0]]
    for n in nybbles[1:]:
        if n == 0:
            break
        enter.append(n)
    tail = nybbles[len(enter):]
    if any(tail):
        raise SystemExit(f"spell {SLOT:#04x} has a 0 nybble before the end, so "
                         f"the entry sequence is not the one measured: {nybbles}")
    return enter


def tap_until_rune(emu: BizHawk, target: int) -> None:
    for _ in range(16):
        cur = ram.f("currune").get(emu.work_ram())
        if cur == target:
            return
        emu.tap("Right" if (cur & 0xFF) < target else "Left", hold=2, release=4)
    cur = ram.f("currune").get(emu.work_ram())
    raise SystemExit(f"currune stayed ${cur:02X}; wanted ${target:02X}")


def commit_rune(emu: BizHawk) -> None:
    before = ram.f("buildind").get(emu.work_ram())
    emu.tap("B", hold=3, release=6)
    after = ram.f("buildind").get(emu.work_ram())
    if after == before:
        bot = emu.work_ram()[ram.f("botbuf").addr:ram.f("botbuf").addr + 6]
        raise SystemExit(f"B did not advance buildind (stuck at ${before:02X}, "
                         f"botbuf {bot.hex()})")


def invsp_bytes(image: bytes) -> bytes:
    f = ram.f("invsp")
    return image[f.addr:f.addr + f.length]


def spell_of(plrspell: int) -> int:
    """The spell index `ispell` reads: two `lsr`s off `plrspell`."""
    return (plrspell >> 2) & 0xFF


def main() -> int:
    if not ROM.exists():
        say(f"no ROM at {ROM}. Run `make rom` first.")
        return 2
    if not TOWN.exists():
        say(f"no town input log at {TOWN}")
        return 2
    image = prg_of(ROM)
    name = fixed_byte(image, rom_sym("sp27nm"), len(INSPECT))
    if name != INSPECT:
        say(f"sp27nm is {name.hex()}, not INSPECT ({INSPECT.hex()}). "
            f"This ROM is not the build the save is for.")
        return 2
    enter = rune_nybbles(image)
    say(f"spell ${SLOT:02X} entry nybbles: "
        + " ".join(f"{n:X}" for n in enter))

    ram.load_button_order()
    stale = BizHawk.running_emuhawk()
    if stale and not os.environ.get("MAGICIAN_ALLOW_CONCURRENT"):
        say(f"REFUSING to start: mono pids {stale} are already running, so this "
            "script cannot prove the window it gets is its own.")
        return 2

    frames = BizHawk.load_inputs(TOWN)
    with BizHawk(rom=ROM, log_name="save_inspect", route="save_inspect",
                 run="1") as emu:
        say(f"connected in {emu.connect_seconds:.1f}s")
        emu.run_inputs(frames)
        phase = ram.f("phase").get(emu.work_ram())
        curlev = ram.f("curlev").get(emu.work_ram())
        if phase != ram.PHASE_MAIN or curlev != 0x10:
            say(f"town log ended at phase={phase} curlev=${curlev:02X}, "
                "not g00 at $10")
            return 2
        before_flags = invsp_bytes(emu.work_ram())

        used, held = emu.step_until(
            [ram.pred("phase", "eq", ram.PHASE_SPELL)],
            buttons=("Select",), budget=400, what="spell screen", pulse=4)
        if not held:
            say(f"SELECT did not open the spell screen ({used} frames)")
            return 2
        emu.step((), 30)
        if ram.f("phase").get(emu.work_ram()) != ram.PHASE_SPELL:
            say("left the spell screen before entering runes")
            return 2

        for n in enter:
            tap_until_rune(emu, n)
            commit_rune(emu)
        emu.tap("Start", hold=3, release=8)
        emu.step((), 20)
        img = emu.work_ram()
        book = ram.f("bookcur").get(img)
        top = ram.f("topspell").get(img)
        if book != SLOT or top != SLOT:
            say(f"START did not store slot ${SLOT:02X}: "
                f"bookcur=${book:02X} topspell=${top:02X}")
            return 2
        after_flags = invsp_bytes(img)
        say(f"invsp before {before_flags.hex()} after {after_flags.hex()}")

        used, held = emu.step_until(
            [ram.pred("phase", "eq", ram.PHASE_MAIN),
             ram.pred("curlev", "eq", 0x10)],
            buttons=("Select",), budget=400, what="back to town", pulse=4)
        if not held:
            say(f"SELECT did not return to the town ({used} frames)")
            return 2
        emu.step((), 40)
        img = emu.work_ram()
        equipped = ram.f("plrspell").get(img)
        if spell_of(equipped) != SLOT:
            say(f"plrspell=${equipped:02X} is spell ${spell_of(equipped):02X}, "
                f"not ${SLOT:02X}")
            return 2
        if ram.f("bookcur").get(img) != SLOT:
            say("bookcur changed on the way out of the spell screen")
            return 2
        flags = invsp_bytes(img)
        say(f"in town, plrspell=${equipped:02X} "
            f"(spell ${spell_of(equipped):02X}, power {equipped & 3}), "
            f"bookcur=${ram.f('bookcur').get(img):02X}, invsp {flags.hex()}")

        STATE.parent.mkdir(parents=True, exist_ok=True)
        if STATE.exists():
            STATE.unlink()
        reply = emu.cmd(f"save {STATE}")
        if not reply.startswith("ok ") or not STATE.exists() or STATE.stat().st_size < 32:
            say(f"savestate was not written: {reply}")
            return 2
        saved = (
            ram.f("phase").get(img),
            ram.f("curlev").get(img),
            ram.f("plrspell").get(img),
            ram.f("bookcur").get(img),
            flags,
        )
        emu.cmd(f"load {STATE}")
        img = emu.work_ram()
        loaded = (
            ram.f("phase").get(img),
            ram.f("curlev").get(img),
            ram.f("plrspell").get(img),
            ram.f("bookcur").get(img),
            invsp_bytes(img),
        )
        if loaded != saved:
            say(f"reloaded state does not match what was saved:\n"
                f"  saved  {saved}\n  loaded {loaded}")
            return 2
        log = emu.save_inputs("inspect-slot27")
        say(f"saved {STATE} ({STATE.stat().st_size} bytes)")
        say(f"inputs {log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
