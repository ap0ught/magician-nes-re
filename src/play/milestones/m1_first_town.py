#!/usr/bin/env python3
"""MILESTONE 1: power-on -> title -> map screen -> the first town.

    python3 src/play/milestones/m1_first_town.py [--rom PATH] [--label NAME]

The default ROM is the rebuild. `--rom` runs the identical route against another
dump, which is how a difference gets attributed to the build rather than to the
game -- and that turned out to be the whole story here: the route completes on
Beta 1 and stops on our rebuild, at the same step, for a reason the run's own log
names.

THE PROOF
---------
1. The route runs from power-on. Every segment takes a checkpoint snapshot
   before it, and each checkpoint records the SHA1 of the route's segment list so
   a checkpoint from another route cannot be loaded into this one.
2. The input log is saved.
3. A SECOND, FRESH emulator is launched, the recorded input log is replayed into
   it from power-on, and the SHA1 of $0000-$07FF is compared with the first run's.
   No savestate, no bookmark, no frame-sync assumption: the inputs alone have to
   produce the same RAM.
4. `MILESTONE 1 COMPLETE AND VERIFIED` is printed ONLY if step 3 matched AND
   every route assertion held. Otherwise this names the assertion that failed.

WHAT IT PRINTS WHEN IT FAILS
----------------------------
The failing segment, the predicate that did not hold, the frame budget it ran
out of, and the RAM at that point -- all read out of the core, none of it
recalled. A milestone that cannot fail is a script that prints a line.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from play import ram, route as route_mod  # noqa: E402
from play.emu import ActionFailed, BizHawk, BridgeError  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]

ap = argparse.ArgumentParser()
ap.add_argument("--rom", default=str(ROOT / "asm/out/magician-rebuilt.nes"))
ap.add_argument("--label", default="rebuild")
ap.add_argument("--keep-going", action="store_true",
                help="on a failed assertion, report and exit 1 instead of raising")
ap.add_argument("--no-replay", action="store_true",
                help="skip the replay-from-power-on proof (then this is NOT a "
                     "milestone, and it says so)")
args = ap.parse_args()

ROM = pathlib.Path(args.rom).resolve()
LABEL = args.label
OUT = ROOT / "logs" / "milestones"
OUT.mkdir(parents=True, exist_ok=True)
LOGFILE = OUT / f"m1_{LABEL}.txt"

_lines: list[str] = []


def say(*parts) -> None:
    s = " ".join(str(p) for p in parts)
    print(s, flush=True)
    _lines.append(s)
    LOGFILE.write_text("\n".join(_lines) + "\n", encoding="utf-8")


def replay_prove(rom: pathlib.Path, log_name: str, expect_fp: str) -> tuple[str, dict]:
    """Replay the recorded inputs from power-on in a FRESH emulator.

    A fresh process, a fresh core, frame 0. The only thing carried across is the
    text file of buttons. If the fingerprint matches, the inputs alone produced
    the same RAM -- which is the claim the whole harness rests on.
    """
    with BizHawk(rom=rom, log_name=log_name, route="replay",
                    run="replay") as emu2:
        say(f"  replay emulator: frame {emu2.frame}, RAM "
            f"{len(emu2.work_ram())} bytes, fingerprint before replay "
            f"{emu2.fingerprint()[:16]}")
        frames = emu2.load_inputs(INPUTS_PATH)
        say(f"  replaying {len(frames)} frames from power-on "
            f"(batching runs of identical input)")
        emu2.run_inputs(frames)
        img = emu2.work_ram()
        fp = emu2.fingerprint()
        v = ram.decode(img)
        say(f"  replay ended at frame {emu2.frame}, fingerprint {fp}")
        say(f"  replay state: phase={v['phase']}({ram.PHASE_NAMES.get(v['phase'],'?')}) "
            f"curlev=${v['curlev']:02X} mapind={v['mapind']} "
            f"plr=({ram.plrx(img)},{ram.plry(img)}) mana={v['manacur']} "
            f"gold={v['wealth']} time={v['gametime']}s")
        emu2.screenshot(f"m1_{LABEL}_replay")
        emu2.snapshot(f"m1_{LABEL}_replay")
    return fp, v


INPUTS_PATH: pathlib.Path = OUT / f"m1_{LABEL}.inputs.txt"


def main() -> int:
    t0 = time.time()
    say(f"MILESTONE 1 -- first town -- [{LABEL}]")
    say(f"  ROM        {ROM}")
    say(f"  ram map    {ram.map_digest()} ({len(ram.all_fields())} fields)")
    say(f"  route      (installed below)")
    ram.load_button_order()
    say(f"  buttons    {', '.join(f'{k}->{v}' for k, v in ram.BUTTON_ORDER.items() if k != 'evidence')}")

    stale = BizHawk.running_emuhawk()
    if stale:
        say(f"REFUSING to start: mono pids {stale} are already running. BizHawk "
            "allows one instance and would divert this launch into the other.")
        return 2

    r = route_mod.install(route_mod.first_town())
    say(f"  segments   {r.digest()}")
    for i, s in enumerate(r.segments):
        say(f"    {i}. {s.name}")

    fingerprint = None
    failure = None
    results = []
    with BizHawk(rom=ROM, log_name=f"m1_{LABEL}", route=r.name,
                    run=f"m1_{LABEL}") as emu:
        say(f"  connected in {emu.connect_seconds:.1f}s, domains "
            f"{[d.name for d in emu.domains]}")
        say("")
        say("=== RUN ===")
        try:
            results = r.execute(emu, log=say)
            fingerprint = emu.fingerprint()
            say("")
            say(f"route complete at frame {emu.frame}, "
                f"{sum(x.used for x in results)} input frames, "
                f"fingerprint {fingerprint}")
            v = ram.decode(emu.work_ram())
            say(f"  final state: phase={v['phase']}({ram.PHASE_NAMES.get(v['phase'],'?')}) "
                f"curlev=${v['curlev']:02X} mapind={v['mapind']} "
                f"plr=({ram.plrx(emu.work_ram())},{ram.plry(emu.work_ram())}) "
                f"mana={v['manacur']}/{v['manatop']} gold={v['wealth']} "
                f"time={v['gametime']}s")
        except Exception as e:
            failure = f"{type(e).__name__}: {e}"
            say("")
            say(f"ASSERTION FAILED: {failure}")
            img = emu.work_ram()
            v = ram.decode(img)
            say(f"  at frame {emu.frame}: phase={v['phase']}"
                f"({ram.PHASE_NAMES.get(v['phase'],'?')}) curlev=${v['curlev']:02X} "
                f"oldlev=${v['oldlev']:02X} mapind={v['mapind']} "
                f"nmiflag={v["nmiflag"]} bnksel=${v["bnksel"]:02X} "
                f"plr=({ram.plrx(img)},{ram.plry(img)}) stat=${v['plrstat']:02X} "
                f"flg={v['plrflg']} pandflg={v['pandflg']} mana={v['manacur']} "
                f"gold={v['wealth']}")
            say("  THIS IS THE FAILING ASSERTION. Everything above it did hold.")
            fingerprint = emu.fingerprint()
        # save_inputs() writes to logs/inputs/, and returns where. Copying it
        # from a path written here instead was reading a file that is never
        # created -- the milestone died with a FileNotFoundError AFTER the run,
        # which is the worst place for it to die.
        written = emu.save_inputs(f"m1_{LABEL}")
        INPUTS_PATH.write_bytes(written.read_bytes())
        say(f"  input log  {written} -> {INPUTS_PATH} "
            f"({len(emu.inputs)} frames, valid_from_poweron={emu.input_log_valid})")

    say("")
    say("=== REPLAY FROM POWER-ON ===")
    if args.no_replay:
        say("  SKIPPED (--no-replay). This is NOT a verified milestone and no "
            "completion line follows.")
        return 1 if failure else 2
    try:
        fp2, v2 = replay_prove(ROM, f"m1_{LABEL}_replay", fingerprint)
    except BridgeError as e:
        say(f"  the replay emulator could not be launched or driven: {e}")
        return 1
    if fingerprint is None:
        say("  the first run produced no fingerprint (it failed), so there is "
            "nothing to compare the replay against. NOT VERIFIED.")
        return 1
    matched = fp2 == fingerprint
    say(f"  run      {fingerprint}")
    say(f"  replay   {fp2}")
    if matched:
        say("  MATCH -- the recorded inputs reproduce the run from power-on")
    else:
        say("  MISMATCH -- the recorded inputs do not reproduce the run, so this "
            "run is not reproducible")

    say("")
    say("=== ACTIVITY STATUS ===")
    for name, status, why in ACTIVITY_STATUS:
        say(f"  {name:<12} {status}")
        say(f"      {why}")

    say("")
    if failure or not matched:
        say("MILESTONE 1 NOT COMPLETE")
        if failure:
            say(f"  failed assertion: {failure}")
        if not matched:
            say("  replay-from-power-on did not reproduce the run")
        say(f"  ({round(time.time() - t0, 1)}s)")
        return 1
    say("MILESTONE 1 COMPLETE AND VERIFIED")
    say(f"  every route assertion held and the recorded inputs reproduced "
        f"fingerprint {fingerprint} from power-on in a fresh emulator.")
    say(f"  ({round(time.time() - t0, 1)}s)")
    return 0


ACTIVITY_STATUS = [
    ("walking", "WORKS", "walk.step() holds one direction and asserts the "
     "player's obxl[3]/obyl[3] CHANGED. Measured on Beta 1: 70 frames = 68-70 "
     "pixels. A wall raises ActionFailed instead of passing."),
    ("talking", "DOES NOT EXIST", "talk.py raises with its finding. Measured: "
     "the first town has no object slot with a live obint, and the source "
     "defines only two interaction messages (intmsg: wise man/tree, begmsg: "
     "beggar), neither of them here. Deferred, as asked."),
    ("shopping", "NOT REACHED", "shop.py exists and refuses to guess: enter() "
     "asserts phase 7 AND the caller's target curlev, move_icon() asserts "
     "shopind CHANGED (no wrap: g07 masks with #$07), choose() asserts ynflag. "
     "No shop was reached on either ROM."),
    ("fighting", "NOT REACHED", "fight.py lists live slots and refuses a health "
     "threshold, because obhel is a 16x scale (helind divides by 32) and no "
     "number in it has been measured. No enemy was encountered."),
    ("spells", "NOT REACHED", "spells.py's commit() REQUIRES the expected mana "
     "cost from the caller and has no default, because the source's "
     "`levmana` costs (4/8/12/16) and the TAS subtitle's 50 disagree and neither "
     "was measured."),
    ("inventory", "WORKS on Beta 1", "START reaches phase 8 AND curlev $E0: the "
     "inventory is a separate level, not an overlay, so both are asserted."),
]


if __name__ == "__main__":
    sys.exit(main())