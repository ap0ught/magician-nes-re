"""Talking.

MEASURED: **there is no talking in the first town**, so this module does nothing
and says so.

The evidence, in the order it is worth:

  1. There is no conversation code in the source's interaction paths. The
     interaction entry point is `obint` -- the object's interaction MESSAGE
     index (x0.pds:553) -- and it is read for exactly two objects:
       * x0.pds:735  `intmsg` -- "cur interaction msg for wise man/tree"
       * x0.pds:736  `begmsg` -- "cur beggar food msg"
     Both are NPC dialogue in a sense (a wise man, a beggar), and neither is
     present in the first town: the walk in src/play/recon.py never saw an
     object slot with a non-$FF `obint`, and the first town of Beta 1 has the
     player as the only live slot.

  2. There is no `talk` verb. `getdir`/`uflg`/`intflg` (x0.pds:738-740) are the
     interaction machinery and they resolve to `obint`, i.e. to a message, not to
     a dialogue state. The game's text system is the PANEL (`pandflg`,
     `panhead`, `pantail`, `panmsg`, x0.pds:455-500) and every message in the
     game goes through it.

  3. The first NPC subtitle in the TAS is `21300 That beggar took my only bread!`
     -- a beggar, i.e. `begmsg`, and not in the first town.

WHAT THIS MODULE THEREFORE DOES
-------------------------------
`talk()` raises, naming the finding. That is the deliverable the brief asked for
("if it does not, say so and defer talk.py rather than inventing an
interaction"), and a module that raises with a reason is worth more than one that
presses A and calls whatever happens "talking".

When a talking NPC IS found, the assertion to build is on `pandflg` becoming
non-zero and then clearing -- `waitpan` waits for exactly that (x5.pds:751), and
it is the only signal in the game that says "something is being said".
`panel_said()` below is that assertion, ready but not wired to a button press,
because pressing a button is the part that needs a measurement.
"""
from __future__ import annotations

from .. import ram
from ..emu import BizHawk

FINDING = (
    "There is no talking in the first town, and no `talk.py` action is offered. "
    "Measured: walking the first town of Beta 1 (src/play/recon.py) never puts "
    "anything but the player in an object slot, so `obint` -- the interaction "
    "message index -- is $FF in every slot. From the source: `intmsg` (wise man, "
    "tree) and `begmsg` (beggar) are the only two interaction messages the game "
    "defines (x0.pds:735-736) and neither is in this town; the first NPC "
    "subtitle in the TAS is a beggar, which is `begmsg`. The game's only text "
    "channel is the panel (`pandflg`/`panmsg`), and `waitpan` waits for "
    "`pandflg` to clear, which is the assertion a talk action would use once "
    "there is something to talk to."
)


def talk(*args, **kwargs):
    """Always raises. See FINDING."""
    raise NotImplementedError(FINDING)


def found() -> bool:
    """False: no interaction target has been found. The whole point of the module."""
    return False


def panel_said(emu: BizHawk, budget: int = 300) -> int:
    """Wait for the panel to say something and then finish. Returns the frames.

    Not wired to any button. It is the only assertion in here that has a measured
    meaning, and it is here so that whoever finds an NPC does not have to
    re-derive that `pandflg` is the signal.
    """
    used, said = emu.step_until([ram.pred("pandflg", "ne", 0)], buttons=(),
                                budget=budget, what="the panel to say something")
    if not said:
        return 0
    more, cleared = emu.step_until([ram.pred("pandflg", "eq", 0)], buttons=(),
                                   budget=budget, what="the panel to finish")
    return used + more