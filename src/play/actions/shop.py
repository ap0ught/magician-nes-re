"""Shops.

MEASURED, and this is the honest part: **no shop was reached**, so nothing in
this file has been run. It exists because the activity list asked for shopping,
and the right response to "the shop has not been reached" is a module that says
so and refuses to pretend.

WHAT THE SOURCE SAYS, so that whoever reaches the first shop knows what to
assert. All of it is `phase == 7` (g07) plus `shopind`:

    x5.pds:734-752  g07: bnk 7,#bshop / jsr g07a / ... / lda ynflag /
                    bne waityn / lda dlr / asl a / asl a / clc / adc dud /
                    clc / adc shopind / and #$07 / cmp shopind / beq / sta shopind
                    ... jsr showshop / jsr waitpan / lda dfirea / beq ret3
    `dlr` is the edge of `lr` -- the LEFT/RIGHT edge byte at $0036, MEASURED -- so
    the shop's icon cursor moves on left/right; `dud` is $0037, the UP/DOWN edge
    byte. The mask is #$07, so `shopind` is 0..7: EIGHT icons.

    The icon list is `ijvl`/`ijvh` -- 18 entries, one byte each, which is the
    dl/dh pair the assembler had wrong for most of its life (see commit 4db43ee
    and src/testing/test_pointer_widths.py).

WHAT IS NOT CLAIMED
-------------------
That a shop EXISTS in the first town. The source's first-town entrance table is
`prxx` in PROBS.SRC:140-176 -- `pr04` is "enter level when facing down" and it
calls `newlev` with the level in `t2` -- but which of those probes are reachable
from the starting position is a question about the map, and the map has not been
walked. `enter()` below therefore takes the target level from the caller and
asserts only what it can see.
"""
from __future__ import annotations

from .. import ram
from ..emu import BizHawk, Result
from . import act

FINDING = (
    "No shop was reached, so shop.py has not been run. The first town's "
    "entrances are the prxx probes in PROBS.SRC:140-176 -- pr04 is 'enter level "
    "when facing down' and calls newlev with the level in t2 -- but which of them "
    "are reachable from the starting position was not determined, because the "
    "map was not walked that far. What is known: a shop is phase 7 (g07, "
    "MISC.SRC:894), its icon cursor is `shopind` 0..7 (masked with #$07 after "
    "adding 4*lr+ud, x5.pds:736-748) and moves on the lr/ud EDGE bytes at "
    "$0036/$0037, and buying asks a question that sets `ynflag` (x5.pds:758-766)."
)


def enter(emu: BizHawk, target_level: int | None = None, budget: int = 400) -> Result:
    """Face a door and press A, asserting the game is in phase 7.

    `target_level` is the logical level index the door should lead to. When it is
    given it is asserted too, because "phase 7" alone would also be satisfied by
    any shop, and this action's whole point is that it went to the RIGHT one.
    When it is not given the caller is saying "I do not know where this door
    goes", and the action says so in its Result rather than pretending.
    """
    preds = [ram.pred("phase", "eq", ram.PHASE_SHOP)]
    note = "A into a door must reach a shop (g07)"
    if target_level is not None:
        preds.append(ram.pred("curlev", "eq", target_level))
        note += f", and curlev must become ${target_level:02X}"
    else:
        note += " -- the target level is UNKNOWN, so only the phase is asserted"
    return act(emu, "shop_enter", preds, buttons=("A",), budget=budget, settle=40,
               expect_note=note)


def move_icon(emu: BizHawk, direction: str, budget: int = 120) -> Result:
    """Move the shop's icon cursor. Asserts `shopind` CHANGED.

    Not "shopind becomes N": the icon order is shop data (SHOPDAT.SRC) and the
    point of an automated shopper is to read where the cursor is, not to assume.
    """
    if direction not in ("Left", "Right", "Up", "Down"):
        raise ValueError(f"{direction!r} is not a direction")
    img = emu.work_ram()
    start = ram.f("shopind").get(img)
    emu.step((direction,), 24)
    after = emu.work_ram()
    now = ram.f("shopind").get(after)
    emu.screenshot(f"shop_icon_{direction}")
    emu.snapshot(f"shop_icon_{direction}")
    res = Result(name=f"shop_icon_{direction}", used=24, before=img, after=after,
                 held=now != start, note=f"shopind {start} -> {now}")
    if not res.held:
        from ..emu import ActionFailed
        raise ActionFailed(
            f"shop_icon {direction}: shopind stayed {start}. Either the shop is "
            f"not in phase 7 (phase={ram.f('phase').get(after)}), or the cursor is "
            f"at an end: g07 masks shopind with #$07 after adding 4*lr+ud, so "
            f"there is no wrap and no edge is a legal result.")
    return res


def choose(emu: BizHawk, budget: int = 400) -> Result:
    """Press A on the highlighted icon.

    Asserts the shop asked a question -- `ynflag` is the answer slot, set by
    `waityn` (x5.pds:758-766), which is the only thing that can be observed
    without knowing what was bought. `wealth` and `manacur` are reported by the
    shared diff, so a run that bought something says so in its own log.
    """
    return act(emu, "shop_choose",
               [ram.pred("ynflag", "ne", 0)],
               buttons=("A",), budget=budget, settle=20,
               expect_note="A on an icon must make the shop ask (ynflag)")