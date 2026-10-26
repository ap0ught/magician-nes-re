"""The menu screens: inventory, spell book and map.

Measured on Beta 1 (src/play/recon.py step 7): pressing START in the playing
level takes `phase` to 8, which `MISC.SRC:894` names g08 = the inventory.
`g08` exits back to the main level when START or SELECT is pressed again
(x6.pds:164-168), and exits through `newlev` with `curlev` set to $E0 -- the
inventory is its own LEVEL, not an overlay. That is why `curlev` changing is part
of the predicate rather than a curiosity.

`g09` is the spell screen (`MISC.SRC:894`): it is reached by SELECT in the level
or from the inventory, it exits on SELECT (x6.pds:293-295), and it commits a
spell with START (x6.pds:311-313).

Nothing here asserts that a spell can be made. Whether it can is a measurement,
and `spells.py` is where that question is asked.
"""
from __future__ import annotations

from .. import ram
from ..emu import BizHawk, Result
from . import act


def open_inventory(emu: BizHawk, budget: int = 300) -> Result:
    """START into the inventory. Asserts phase 8 AND curlev $E0.

    Two predicates, because `phase == 8` alone is satisfied for a frame or two
    while the screen is still being drawn, and a screenshot then shows the level.
    `curlev == $E0` is the second half of the same fact: the inventory really is
    a separate level in this game.
    """
    return act(emu, "open_inventory",
               [ram.pred("phase", "eq", ram.PHASE_INVENTORY),
                ram.pred("curlev", "eq", 0xE0)],
               buttons=("Start",), budget=budget, settle=30, pulse=8,
               expect_note="START must open the inventory, which is level $E0")


def close_inventory(emu: BizHawk, budget: int = 300) -> Result:
    """START or SELECT back out to the playing level."""
    return act(emu, "close_inventory",
               [ram.pred("phase", "eq", ram.PHASE_MAIN)],
               buttons=("Start",), budget=budget, settle=30, pulse=8,
               expect_note="START must leave the inventory")


def open_map(emu: BizHawk, budget: int = 300) -> Result:
    """The map screen, phase $0A.

    Reached from the inventory rather than from the level: measured, the level's
    own SELECT opens the spell screen, and the map is on the inventory's menu.
    This asserts the phase only, because the level the map is drawn for is the
    map level itself and its value is not what identifies it.
    """
    return act(emu, "open_map",
               [ram.pred("phase", "eq", ram.PHASE_MAP)],
               buttons=("Select",), budget=budget, settle=30,
               expect_note="SELECT must reach the map screen (g0a)")


def open_spell_screen(emu: BizHawk, budget: int = 300) -> Result:
    """SELECT from the playing level, phase $09."""
    return act(emu, "open_spell_screen",
               [ram.pred("phase", "eq", ram.PHASE_SPELL)],
               buttons=("Select",), budget=budget, settle=30,
               expect_note="SELECT in a level opens the spell screen (g09)")
