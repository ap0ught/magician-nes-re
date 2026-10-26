"""The action contract, and the machinery every action shares.

THE CONTRACT
------------
An action takes the emulator, a RAM predicate and a frame budget. It drives
input ONLY while the predicate is false. When the budget expires it **raises** --
never warns, never returns a "best effort", never quietly succeeds with
`held=False`. It returns the frames used and the RAM images from before and
after. It takes a screenshot and a checkpoint snapshot on entry and on exit.

Nothing outside `ram.py` may contain a RAM address. Every predicate is built
from a named field, so an address that is not in the symbol table cannot reach an
action, and one that is has to arrive with the comment saying what it means.

WHY THE SHARED MACHINERY IS HERE AND NOT IN EACH ACTION
------------------------------------------------------
Five activities, five files, one loop. The first version of this had the loop
written out in each of them, and the thing that made it tempting to write it out
was the thing that made it dangerous: the "exits" are the same five lines
(`snapshot`, `screenshot`, diff, `Result`) and they are the part a reader trusts.
Copied five times they drift, and a snapshot that is written on entry but not on
exit is a checkpoint that looks complete.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .. import ram
from ..emu import ActionFailed, BizHawk, Result

__all__ = [
    "ActionFailed", "Result", "act", "drive", "watch", "snapshot_names",
    "ACTION_REPORT",
]

# Which named fields an action's before/after report shows by default. Small on
# purpose: a diff of 102 fields prints 102 lines of which 98 are noise.
ACTION_REPORT = ("phase", "curlev", "oldlev", "mapind", "plrstat", "plrflg",
                 "plrxlo", "plrxhi", "prylo", "pryhi", "wealth", "manacur",
                 "pandflg", "invhand", "shopind", "plrspell", "gametime")


def _label(snap_name: str) -> str:
    """Checkpoint names must be unique and must survive the wire."""
    if not snap_name or any(c.isspace() for c in snap_name):
        raise ValueError(
            f"checkpoint name {snap_name!r} is empty or contains whitespace; the "
            "bridge's snapshot command takes one token")
    return snap_name


def act(emu: BizHawk,
        name: str,
        done: Sequence[ram.Pred],
        buttons: Iterable[str] | str = (),
        budget: int = 600,
        settle: int = 0,
        expect_note: str = "",
        shot: bool = True,
        snapshots: bool = True,
        pulse: int = 0) -> Result:
    """Drive `buttons` until every predicate in `done` holds. Raise if it does not.

    `settle` frames are spent with nothing pressed AFTER the predicate first
    holds, because the game's own animations finish a few frames after the flag
    that started them: `phase` reaches PHASE_ENTER a few frames before the view
    is actually on screen, and a screenshot taken at that moment shows the
    previous screen.

    `pulse` alternates press/release instead of holding. Use it whenever the
    game reads an EDGE rather than a level: the debounced bytes are $FF only on
    the frame the value changes (DISP.SRC:325-333), so a button held down for
    four hundred frames produces one edge on the first frame and nothing after.
    An action that holds is right for walking, where the level is what matters,
    and wrong for every menu.

    The entry snapshot is taken BEFORE any input, so a run that fails halfway
    still has a checkpoint at the place it failed at.
    """
    if not done:
        raise ValueError(f"action {name!r} has no predicate; an action that cannot "
                         "tell whether it worked is not an action")
    before = emu.work_ram()
    if snapshots:
        emu.snapshot(_label(f"{name}_in"))
    if shot:
        emu.screenshot(f"{name}_in")

    emu.note(f"{name}: waiting for "
             + " AND ".join(str(p) for p in done)
             + (f" ({expect_note})" if expect_note else ""))
    used, held = emu.step_until(done, buttons=buttons, budget=budget, what=name,
                                pulse=pulse)
    if settle:
        used += emu.step(frames=settle)
    after = emu.work_ram()

    res = Result(name=name, used=used, before=before, after=after, held=held,
                 note=expect_note)
    if shot:
        emu.screenshot(f"{name}_out")
    if snapshots:
        emu.snapshot(_label(f"{name}_out"))
    res.diff([ram.f(n) for n in ACTION_REPORT])
    if not held:
        # Re-read and report, because an assertion that says only "failed" costs
        # the reader a rerun. The values below were read out of RAM by the core.
        v = ram.decode(after)
        raise ActionFailed(
            f"{name}: the predicate did not hold within {budget} frames holding "
            f"{buttons!r}. Wanted "
            + " AND ".join(str(p) for p in done)
            + f". RAM now: phase={v['phase']}({ram.PHASE_NAMES.get(v['phase'],'?')}) "
              f"curlev=${v['curlev']:02X} mapind={v['mapind']} "
              f"plr=({ram.plrx(after)},{ram.plry(after)}) stat=${v['plrstat']:02X} "
              f"flg={v['plrflg']} mana={v['manacur']} gold={v['wealth']} "
              f"panel={v['pandflg']} -- {res.diff([ram.f(n) for n in ACTION_REPORT])}")
    return res


def drive(emu: BizHawk, buttons: Iterable[str] | str, frames: int) -> None:
    """Hold buttons for a fixed number of frames. No predicate, no claim.

    For legs whose outcome is not yet known -- reconnaissance. An action that
    uses this has to assert something afterwards, and `act()` is how.
    """
    emu.step(buttons, frames)


def watch(emu: BizHawk, pred: ram.Pred, budget: int = 600) -> tuple[int, bool]:
    """Wait for one predicate with nothing pressed. Returns (frames, held)."""
    return emu.step_until([pred], buttons=(), budget=budget)


def report(lines: list[str], res: Result) -> None:
    d = res.diff([ram.f(n) for n in ACTION_REPORT])
    lines.append(f"  {res.name:<22} {res.used:>5}f  "
                 + (", ".join(d) if d else "no field changed"))