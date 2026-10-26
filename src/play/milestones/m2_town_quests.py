#!/usr/bin/env python3
"""MILESTONE 2: the first town's quest ladder, on Beta 1 first.

    python3 src/play/milestones/m2_town_quests.py --beta1
    python3 src/play/milestones/m2_town_quests.py --rebuild

Same contract as milestone 1, and for the same reason: a segment is finished
when it works on **Beta 1**, the cartridge the source was written for. The
rebuild is run with the identical route and the identical assertions and its
result is a finding about our ROM.

WHAT THIS ADDS TO MILESTONE 1
-----------------------------
`m1_first_town` reached the town, walked in it and opened the inventory. This
route goes on to the things the town is FOR: talking to somebody, buying
something, delivering the letter, drinking the goat's milk, and creating a
spell. Two of those are measurements that two human records disagree about --
the mana cost of a spell, and the mana regeneration rate -- and both are settled
here by reading `manacur` and `valsav` out of RAM rather than by picking a side.

The claims, what each is measured against, and what it was actually measured as
are in `play/ladder.py:CLAIMS`. This script PRINTS that table with the
measurement beside it, because a claim with no number next to it is how a
report becomes fiction.

THE PROOF
---------
1. The route runs from power-on; every segment snapshots itself first, and each
   checkpoint records the SHA1 of the route's segment list.
2. Each segment is searched by a SCOUT from a savestate copy of MAIN's state, and
   the winner's inputs are replayed into MAIN one frame at a time. The run's
   artifact is the input log and nothing else.
3. The input log is saved.
4. A SECOND, FRESH emulator replays it from power-on and the SHA1 of
   `$0000-$07FF` is compared. No savestate, no bookmark.
5. `MILESTONE 2 COMPLETE AND VERIFIED` is printed ONLY if step 4 matched AND
   every segment's success test held on MAIN.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from play import ladder as ladder_mod  # noqa: E402
from play import ram  # noqa: E402
from play.emu import ActionFailed, BizHawk, BridgeError  # noqa: E402
from play.runner import Run  # noqa: E402

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
ap.add_argument("--only", default="",
                help="run just these segments, comma separated, after the "
                     "transitions. The transitions always run: a ladder whose "
                     "rungs start somewhere else is not a ladder.")
ap.add_argument("--tries", type=int, default=0,
                help="override every segment's try count (0 = the route's own)")
ap.add_argument("--stop-after", default="",
                help="stop after this segment and still write the input log "
                     "(for a partial climb whose ledger is worth keeping)")
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
LOGFILE = OUT / f"m2_{LABEL}.txt"

_lines: list[str] = []


def say(*parts) -> None:
    s = " ".join(str(p) for p in parts)
    print(s, flush=True)
    _lines.append(s)
    LOGFILE.write_text("\n".join(_lines) + "\n", encoding="utf-8")


# The numbers the run is expected to explain, lifted out of the ledgers rather
# than typed in. `--only` exists so a ladder can be climbed as far as it goes and
# the ledger kept either way; this is what turns those ledgers into a table.
_NUMBER_RE = re.compile(r"gold (\d+)->(\d+)")
_CHARGE_RE = re.compile(r"manacur - valsav = (-?\d+)")


def claim_measurements(outdir: pathlib.Path) -> dict[str, list[str]]:
    """Pull the measured numbers out of every attempt ledger on disk."""
    found: dict[str, list[str]] = {}
    for f in sorted(outdir.glob("*.attempts.txt")):
        text = f.read_text(encoding="utf-8")
        key = f.stem
        bits = []
        for m in _NUMBER_RE.finditer(text):
            bits.append(f"gold {m.group(1)}->{m.group(2)}")
        for m in _CHARGE_RE.finditer(text):
            bits.append(f"manacur-valsav={m.group(1)}")
        if bits:
            found[key] = sorted(set(bits))
    return found


def main() -> int:
    t0 = time.time()
    target = (args.beta1 or args.rom or LABEL == "beta1")
    say(f"MILESTONE 2 -- first town quest ladder -- [{LABEL}]")
    say(f"  ROM        {ROM}")
    beta1 = patches.cart_path("beta1")
    say(f"  cartridge  {str(patches.CARTS['beta1']['sha1'])[:8]} "
        f"({'beta1' if ROM == beta1 else 'NOT beta1'})")
    say("  THIS IS THE TARGET RUN" if target else
        "  THIS IS THE REPORT RUN. Its failures are findings about our ROM, "
        "not about the driver: the driver is proven on Beta 1 with the "
        "identical route.")
    say(f"  ram map    {ram.map_digest()} ({len(ram.all_fields())} fields)")
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

    route = ladder_mod.town_quests()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if only:
        keep, segs = [], []
        for s in route.segments:
            if s.name in only:
                segs.append(s)
            elif s.name in ("title", "new_game", "into_level"):
                keep.append(s)
        route = ladder_mod.Route(route.name)
        for s in keep + segs:
            route.segments.append(s)

    say(f"  route      {route.name}  segment list {route.digest()}")
    for i, s in enumerate(route.segments):
        say(f"    {i:2}. {s.name:<16} tries={s.tries}"
            f"{'  (first success)' if s.first_success else ''}"
            f"  {s.describe_success()}")

    failure = None
    run = Run(route.name, rom=ROM, label=LABEL, log=say,
              verify=not args.no_replay)
    stopped_early = False
    try:
        run.start(route)              # installs the route, so snapshots carry it
        say("=== RUN ===")
        for i, seg in enumerate(route.segments):
            say("")
            say(f"[{i + 1}/{len(route.segments)}] {seg.name}")
            run.segment(seg, tries=args.tries or None)
            if args.stop_after and seg.name == args.stop_after:
                stopped_early = True
                say(f"  --stop-after {seg.name}: stopping here with the log "
                    f"whole. This is NOT a milestone and no completion line "
                    f"follows.")
                break
        result = run.finish() if not stopped_early else {}
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
        run.close()
    result = run.result or {}

    say("")
    say("=== SEGMENT COST ===")
    if run.reports:
        for r in run.reports:
            say(f"  {r.name:<16} {r.frames:>6}f   {r.wins}/{r.tries} attempts "
                f"succeeded, stopped {r.why}")
    else:
        say("  (none completed)")

    say("")
    say("=== CLAIMS, AND WHAT WAS MEASURED ===")
    measured = claim_measurements(run.outdir)
    for c in ladder_mod.CLAIMS:
        say(f"  {c['id']}")
        say(f"      claim      {c['claim']}")
        say(f"      read in    {c['read_in']}")
        say(f"      source     {c['against']}")
        say(f"      settled by {c['settled_by']}")
        for key in ("warrior", "drink", "rune_price", "shop_buy", "post_office",
                    "mana_rate"):
            if key in measured:
                say(f"      MEASURED   {key}: " + "; ".join(measured[key]))

    say("")
    say("=== SEARCH LEDGERS ===")
    for f in sorted(run.outdir.glob("*.attempts.txt")):
        lines = [l for l in f.read_text().splitlines()
                 if l.strip().startswith("seed")]
        won = [l for l in lines if " OK  " in l]
        say(f"  {f.name:<24} {len(lines)} attempts, {len(won)} succeeded")
        say(f"    {f}")

    say("")
    say("=== REPLAY FROM POWER-ON ===")
    if args.no_replay or stopped_early:
        say("  SKIPPED. This is NOT a verified milestone and no completion line "
            "follows.")
        return 1 if (failure or stopped_early) else 2
    say(f"  run fingerprint    {result.get('fingerprint')}")
    say(f"  replay fingerprint {result.get('replay_fingerprint')}")
    verified = result.get("verified")
    if verified is True:
        say("  MATCH -- the recorded inputs reproduce the run from power-on in a "
            "fresh emulator")
    else:
        say("  NOT VERIFIED -- see the run's own log for why")

    say("")
    if failure or not verified:
        say("MILESTONE 2 NOT COMPLETE")
        if failure:
            say(f"  failed segment: {failure}")
        if not verified:
            say("  replay-from-power-on did not reproduce the run")
        say(f"  ({round(time.time() - t0, 1)}s)")
        return 1
    say("MILESTONE 2 COMPLETE AND VERIFIED")
    say(f"  every segment's success test held on MAIN, and the recorded inputs "
        f"reproduced fingerprint {result['fingerprint']} from power-on in a "
        f"fresh emulator.")
    say(f"  ({round(time.time() - t0, 1)}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())