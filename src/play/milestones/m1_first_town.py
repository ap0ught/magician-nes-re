#!/usr/bin/env python3
"""MILESTONE 1: power-on -> title -> map screen -> the first town.

    python3 src/play/milestones/m1_first_town.py [--rom PATH] [--label NAME]
    python3 src/play/milestones/m1_first_town.py --beta1     # the stock cartridge
    python3 src/play/milestones/m1_first_town.py --rebuild   # ours (the default)

WHY BETA 1 IS THE TARGET AND THE REBUILD IS A REPORT
----------------------------------------------------
Everything in `src/play/` is developed and proven against **Beta 1** -- the
cartridge the source was written for, body SHA1 `af51e12d...`, the one dump with
the battery bit CLEAR. A segment is finished when it works on Beta 1. The
rebuild is then run with the identical route and the identical assertions, and
its result is a *finding about our ROM*, not a bug in the driver:

  * works on Beta 1, fails on ours  ->  our ROM differs here, and the run says
    where
  * fails on both                   ->  the driver is wrong

That distinction is the whole reason the driver is proven on stock first. A
harness that is only ever run against a build that does not boot cannot tell
those two apart, and this project has already spent a day being unable to.

THE PROOF
---------
1. The route runs from power-on. Every segment snapshots itself before it
   starts, and each checkpoint records the SHA1 of the route's segment list, so
   a checkpoint from another route cannot be loaded into this one.
2. Each segment is searched by a SCOUT from a savestate copy of MAIN's state,
   and the winner's inputs are replayed into MAIN one frame at a time -- the
   run's artifact is the input log and nothing else.
3. The input log is saved.
4. A SECOND, FRESH emulator replays that log from power-on, and the SHA1 of
   `$0000-$07FF` is compared with the first run's. No savestate, no bookmark:
   the inputs alone have to produce the same RAM.
5. `MILESTONE 1 COMPLETE AND VERIFIED` is printed ONLY if step 4 matched AND
   every segment's success test held on MAIN.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from play import first_town as route_mod  # noqa: E402
from play import ram  # noqa: E402
from play.emu import ActionFailed, BizHawk, BridgeError  # noqa: E402
from play.runner import N_EMULATORS, Run  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]

ap = argparse.ArgumentParser()
ap.add_argument("--rom", default="", help="a dump to run against")
ap.add_argument("--label", default="", help="a name for this run's checkpoints")
ap.add_argument("--beta1", action="store_true",
                help="run against Beta 1, the cartridge the source was written "
                     "for. THIS is the target; --rebuild is the report.")
ap.add_argument("--rebuild", action="store_true",
                help="run against asm/out/magician-rebuilt.nes (the default)")
ap.add_argument("--no-replay", action="store_true",
                help="skip the replay-from-power-on proof (then this is NOT a "
                     "milestone, and it says so)")
ap.add_argument("--tries", type=int, default=0,
                help="override every segment's try count (0 = the route's own)")
ap.add_argument("--scouts", type=int, default=1,
                help="how many emulator windows to use. 1 is MAIN only, which is "
                     "the inline path. More than that opens N-1 scout emulators "
                     "and searches across them with search.parallel_search. "
                     f"Capped at runner.N_EMULATORS ({N_EMULATORS}), "
                     "which is the width that was MEASURED on this machine -- three "
                     "concurrent sessions, each with its own pid, window, bridge "
                     "port and RAM fingerprint")
args = ap.parse_args()

sys.path.insert(0, str(ROOT))
from asm import patches  # noqa: E402  (after the path insert; needs the repo root)

REBUILD = ROOT / "asm" / "out" / "magician-rebuilt.nes"

if args.rom:
    ROM = pathlib.Path(args.rom).resolve()
    LABEL = args.label or ROM.stem.replace(" ", "_")
elif args.beta1:
    ROM = patches.cart_path("beta1")
    LABEL = args.label or "beta1"
else:
    ROM = REBUILD
    LABEL = args.label or "rebuild"

OUT = ROOT / "logs" / "milestones"
OUT.mkdir(parents=True, exist_ok=True)
LOGFILE = OUT / f"m1_{LABEL}.txt"

_lines: list[str] = []


def say(*parts) -> None:
    s = " ".join(str(p) for p in parts)
    print(s, flush=True)
    _lines.append(s)
    LOGFILE.write_text("\n".join(_lines) + "\n", encoding="utf-8")


ACTIVITY_STATUS = [
    ("walking", "WORKS", "the walk segment asserts the player's position CHANGED "
     "and that the game is still in g00. MEASURED on Beta 1: 70 frames = 68-70 "
     "pixels. A wall fails the ATTEMPT, with the player's position in the note, "
     "rather than passing it."),
    ("inventory", "WORKS", "START reaches phase 8 AND curlev $E0: the inventory "
     "is a separate level, not an overlay, so both are asserted."),
    ("talking", "DOES NOT EXIST", "talk.py raises with its finding. Measured: the "
     "first town has no object slot with a live obint, and the source defines "
     "only two interaction messages (intmsg: wise man/tree, begmsg: beggar), "
     "neither of them here. Deferred, as asked."),
    ("shopping", "NOT REACHED", "shop.py exists and refuses to guess: enter() "
     "asserts phase 7 AND the caller's target curlev, move_icon() asserts shopind "
     "CHANGED (no wrap: g07 masks with #$07), choose() asserts ynflag."),
    ("fighting", "NOT REACHED", "fight.py lists live slots and refuses a health "
     "threshold, because obhel is a 16x scale (helind divides by 32) and no "
     "number in it has been measured."),
    ("spells", "NOT REACHED", "spells.py's commit() REQUIRES the expected mana "
     "cost from the caller and has no default, because the source's `levmana` "
     "costs (4/8/12/16) and the TAS subtitle's 50 disagree and neither was "
     "measured."),
]


def main() -> int:
    t0 = time.time()
    if args.beta1 or args.rom or LABEL == "beta1":
        verdict = ("This is the TARGET run: Beta 1 is the cartridge the source "
                   "was written for, so a segment that works here is a working "
                   "driver.")
    else:
        verdict = ("This is the REPORT run, on our rebuild. Its failures are "
                   "findings about our ROM, not about the driver -- the driver "
                   "is proven on Beta 1 with the identical route.")
    say(f"MILESTONE 1 -- first town -- [{LABEL}]")
    say(f"  ROM        {ROM}")
    beta1 = patches.cart_path("beta1")
    sha1 = patches.CARTS["beta1"]["sha1"]
    say(f"  cartridge  {str(sha1)[:8]} "
        f"({'beta1' if ROM == beta1 else 'NOT beta1'})")
    say(f"  {verdict}")
    say(f"  ram map    {ram.map_digest()} ({len(ram.all_fields())} fields)")
    say(f"  emulators  {args.scouts} "
        f"({args.scouts - 1} scout(s) beside MAIN; runner.N_EMULATORS="
        f"{N_EMULATORS})")
    ram.load_button_order()
    say(f"  buttons    {', '.join(f'{k}->{v}' for k, v in ram.BUTTON_ORDER.items() if k != 'evidence')}")

    # A window that predates this run is refused, and the refusal no longer claims
    # BizHawk would divert the launch. MEASURED on this machine: BizHawk 2.11.1
    # runs with SingleInstanceMode=false and holds several windows, so the reason
    # to refuse is that this run cannot PROVE the window it is about to get is its
    # own -- not that the launch would be swallowed. With --scouts the caller has
    # said it means to run several, so the stale window is still refused (it is
    # nobody's, as far as this process can tell) but the reason is named.
    stale = BizHawk.running_emuhawk()
    if stale and not os.environ.get("MAGICIAN_ALLOW_CONCURRENT"):
        say(f"REFUSING to start: mono pids {stale} were already running before "
            "this process launched anything, so they are not this run's and this "
            "run cannot prove the window it gets is its own.")
        say("  Close them (by PID -- never `pkill -f EmuHawk`, which matches its "
            "own command line) or set MAGICIAN_ALLOW_CONCURRENT=1 to run beside "
            "them.")
        return 2
    if stale:
        say(f"NOTE: mono pids {stale} predate this run and "
            "MAGICIAN_ALLOW_CONCURRENT=1 is set, so running beside them. "
            "Run.start asserts that this launch added its OWN pid; if it did "
            "not, the run is measuring someone else's session and says so.")

    route = route_mod.first_town()
    say(f"  route      {route.name}  segment list {route.digest()}")
    for i, s in enumerate(route.segments):
        say(f"    {i}. {s.name:<16} tries={s.tries}"
            f"{'  (first success)' if s.first_success else ''}"
            f"  {s.describe_success()}")

    failure = None
    result = {}
    run = Run(route.name, rom=ROM, label=LABEL, log=say,
              scouts=args.scouts, verify=not args.no_replay)
    try:
        run.start(route)              # installs the route, so snapshots carry it
        say("=== RUN ===")
        for i, seg in enumerate(route.segments):
            say("")
            say(f"[{i + 1}/{len(route.segments)}] {seg.name}")
            run.segment(seg, tries=args.tries or None)
        result = run.finish()
    except ActionFailed as e:
        failure = f"{type(e).__name__}: {e}"
        say("")
        say(f"ASSERTION FAILED: {failure}")
        say("  THIS IS THE FAILING SEGMENT. Every segment above it held on MAIN.")
    except BridgeError as e:
        failure = f"BridgeError: {e}"
        say("")
        say(f"BRIDGE FAILED: {failure}")
    finally:
        # Always close: BizHawk's single-instance pipe means the next launch
        # would be diverted into a window that is still up, and a run that dies
        # with its emulator open poisons every run after it.
        run.close()
    result = run.result

    say("")
    say("=== SEGMENT COST ===")
    if run.reports:
        for r in run.reports:
            say(f"  {r.name:<16} {r.frames:>6}f   {r.wins}/{r.tries} attempts "
                f"succeeded, stopped {r.why}")
    else:
        say("  (none completed)")

    say("")
    say("=== SEARCH LEDGERS ===")
    for f in sorted(run.outdir.glob("*.attempts.txt")):
        losers = [l for l in f.read_text().splitlines() if l.strip().startswith("seed")]
        won = [l for l in losers if " OK  " in l]
        say(f"  {f.name:<24} {len(losers)} attempts, {len(won)} succeeded")
        say(f"    {f}")

    say("")
    say("=== REPLAY FROM POWER-ON ===")
    if args.no_replay:
        say("  SKIPPED (--no-replay). This is NOT a verified milestone and no "
            "completion line follows.")
        return 1 if failure else 2
    say(f"  run fingerprint    {result.get('fingerprint')}")
    say(f"  replay fingerprint {result.get('replay_fingerprint')}")
    verified = result.get("verified")
    if verified is True:
        say("  MATCH -- the recorded inputs reproduce the run from power-on in a "
            "fresh emulator")
    else:
        say("  NOT VERIFIED -- see the run's own log for why")

    say("")
    say("=== ACTIVITY STATUS ===")
    for name, status, why in ACTIVITY_STATUS:
        say(f"  {name:<12} {status}")
        say(f"      {why}")

    say("")
    if failure or not verified:
        say("MILESTONE 1 NOT COMPLETE")
        if failure:
            say(f"  failed segment: {failure}")
        if not verified:
            say("  replay-from-power-on did not reproduce the run")
        say(f"  ({round(time.time() - t0, 1)}s)")
        return 1
    say("MILESTONE 1 COMPLETE AND VERIFIED")
    say(f"  every segment's success test held on MAIN, and the recorded inputs "
        f"reproduced fingerprint {result['fingerprint']} from power-on in a "
        f"fresh emulator.")
    say(f"  ({round(time.time() - t0, 1)}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())