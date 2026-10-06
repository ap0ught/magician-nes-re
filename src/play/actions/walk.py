"""Walking.

The player's position is `obxl[3]`/`obxh[3]` and `obyl[3]`/`obyh[3]` -- four
separate bytes in two separate arrays, because `zp obxl,maxob` and
`zp obxh,maxob` are not interleaved (x0.pds:548-549). Slot 3 is the player
because `maxob equ $04` and `pi equ maxob-1` (x0.pds:242-243).

MEASURED on Beta 1 (src/play/recon.py): holding a direction for 70 frames moves
the player about 68-70 pixels, which is one pixel per frame less a walk
animation. A leg's predicate is therefore "the position CHANGED", not "the
position equals some number": the animation timing is not something to hard-code,
and an action that asserts an exact tile would break the moment a frame budget
moved by one.
"""
from __future__ import annotations

from .. import ram
from ..emu import BizHawk, Result
from . import act

DIRS = ("Left", "Right", "Up", "Down")


def at(emu: BizHawk, x: int | None = None, y: int | None = None,
       budget: int = 600, buttons=None) -> Result:
    """Walk until the player's map position satisfies `x`/`y` (either may be None).

    The predicate is on the position bytes, read by the core each frame.
    """
    img = emu.work_ram()
    preds = []
    if x is not None:
        preds.append(ram.pred("plrxlo", "eq", x & 0xFF))
        if x > 0xFF:
            preds.append(ram.pred("plrxhi", "eq", x >> 8))
    if y is not None:
        preds.append(ram.pred("prylo", "eq", y & 0xFF))
        if y > 0xFF:
            preds.append(ram.pred("pryhi", "eq", y >> 8))
    if not preds:
        raise ValueError("walk.at() needs an x or a y")
    return act(emu, f"walk_at_{x}_{y}", preds, buttons=buttons or (),
               budget=budget,
               expect_note=f"from ({ram.plrx(img)},{ram.plry(img)})")


def step(emu: BizHawk, direction: str, frames: int = 70, name: str = "step",
         leg: int = 0) -> Result:
    """Hold one direction until the player's position has moved at all.

    Asserting "moved" rather than "moved to a tile" is what makes this usable on
    an unknown map: it fails when a wall blocks the way, which is information,
    rather than silently walking into it and reporting success.
    """
    if direction not in DIRS:
        raise ValueError(f"{direction!r} is not one of {DIRS}")
    img = emu.work_ram()
    x0, y0 = ram.plrx(img), ram.plry(img)
    # "Moved" cannot be a single predicate, because the core evaluates one
    # address. So the leg is driven for a fixed window first, and the assertion
    # is made afterwards on the position bytes -- still only ever on RAM.
    # The leg number is in every checkpoint name. Two legs in the same direction
    # is not a degenerate route -- it is what "walk east, then further east" is --
    # and a snapshot name that collides is a hard error by design, because a
    # checkpoint silently overwritten is a claim nothing can check.
    tag = f"{name}_leg{leg}_{direction}"
    emu.note(f"step {direction} for {frames}f from ({x0},{y0})")
    emu.step((direction,), frames)
    after = emu.work_ram()
    x1, y1 = ram.plrx(after), ram.plry(after)
    emu.screenshot(f"{tag}_out")
    emu.snapshot(f"{tag}_out")
    res = Result(name=tag, used=frames, before=img, after=after,
                 held=(x0, y0) != (x1, y1),
                 note=f"({x0},{y0}) -> ({x1},{y1})")
    if not res.held:
        from ..emu import ActionFailed
        v = ram.decode(after)
        raise ActionFailed(
            f"step {direction} for {frames} frames: the player did not move. "
            f"position ({x0},{y0}) -> ({x1},{y1}), stat=${v['plrstat']:02X} "
            f"plrflg={v['plrflg']} ctrl=${v['ctrl']:02X} phase={v['phase']} "
            f"({ram.PHASE_NAMES.get(v['phase'],'?')}). Either a wall, or the game "
            f"is not in a phase that reads the pad (ctrl bit 5 makes it ignore "
            f"buttons; {v['ctrl']:#04x}).")
    return res


def wander(emu: BizHawk, legs, name: str = "walk") -> list[Result]:
    """A list of (direction, frames) legs. Raises on the first leg that fails."""
    out = []
    for i, (d, n) in enumerate(legs):
        out.append(step(emu, d, n, name=name, leg=i))
    return out
