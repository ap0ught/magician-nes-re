"""An ordered list of segments, with a checkpoint snapshot before each one.

WHAT A ROUTE IS
---------------
A route is a LIST OF SEGMENTS, and a segment is "do this activity". The route's
identity is the SHA1 of that list -- names and everything -- and every checkpoint
records it. `BizHawk.load_checkpoint()` refuses a checkpoint produced by a
different list, so a run cannot quietly continue through a state it never
earned by editing the route underneath it.

The identity is deliberately the list and nothing else. Not the ROM, not the
frame count, not the ram map: those change for reasons that are not "the plan
changed", and a checkpoint that is invalidated by an unrelated edit is a
checkpoint nobody keeps.

`route.py` does NOT talk to the emulator. It is a plan. `run.py` executes one.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

from . import ram
from .emu import ActionFailed, BizHawk, Result


@dataclass(frozen=True)
class Segment:
    """One activity: a name, a callable, and what it is for.

    `why` is not decoration: a route that says "walk east" without saying what
    for cannot be checked by anybody reading it later, and the segments here are
    the ones whose purpose is a measurement.
    """
    name: str
    run: Callable[[BizHawk], object]
    why: str = ""


@dataclass
class Route:
    name: str
    segments: list[Segment] = field(default_factory=list)

    def add(self, name: str, run: Callable[[BizHawk], object], why: str = "") -> "Route":
        if any(s.name == name for s in self.segments):
            raise ValueError(f"route {self.name!r} already has a segment called {name!r}")
        self.segments.append(Segment(name, run, why))
        return self

    def digest(self) -> str:
        return segment_list_sha1(self.segments)

    def execute(self, emu: BizHawk, log=print) -> list[Result]:
        """Run every segment, snapshotting before each one.

        The pre-segment snapshot is the point. A run that fails inside segment 4
        then has a checkpoint at the start of segment 4, which is the state the
        failure is about -- not a checkpoint of the failure itself, which is a
        picture of a broken thing and tells you nothing about what preceded it.
        """
        if not emu.route:
            raise ValueError(
                "BizHawk was constructed without a route name, so a snapshot "
                "would have no route to record and the checkpoint guard would be "
                "inert. Construct it with route=<name>.")
        results: list[Result] = []
        for i, seg in enumerate(self.segments):
            pre = f"{i:02d}_{seg.name}_pre"
            log(f"[{i + 1}/{len(self.segments)}] snapshot {pre} before {seg.name}")
            emu.snapshot(pre)
            if seg.why:
                log(f"    {seg.why}")
            try:
                out = seg.run(emu)
            except Exception as e:
                # Any failure, not just a failed assertion: a snapshot that
                # cannot be written, a name collision, a BridgeError. Each of
                # those ends the run the same way and each is worth a checkpoint
                # of the state the run was in when it stopped. Catching only
                # ActionFailed let a FileExistsError from inside a walk escape
                # with no FAILED checkpoint at all.
                try:
                    emu.snapshot(f"{i:02d}_{seg.name}_FAILED")
                except Exception as se:
                    log(f"    (could not write the FAILED checkpoint either: {se})")
                log(f"    FAILED: {type(e).__name__}: {e}")
                raise
            if isinstance(out, Result):
                results.append(out)
                log(f"    {out.name}: {out.used} frames"
                    + (f" -- {out.note}" if out.note else ""))
            elif isinstance(out, list):
                results.extend(x for x in out if isinstance(x, Result))
                log(f"    {seg.name}: {len(out)} result(s)")
            emu.snapshot(f"{i:02d}_{seg.name}_post")
        return results


def segment_list_sha1(segments) -> str:
    """SHA1 of a segment list: names, in order, and nothing else."""
    h = hashlib.sha256()
    for s in segments:
        h.update((s.name if isinstance(s, Segment) else str(s)).encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


# The route the milestone runs. Kept here, not in the milestone, so that the
# segment list the checkpoints record is a list that exists in the repository
# rather than something a script builds as it goes.
def first_town() -> Route:
    """Power-on -> title -> map screen -> the first level -> a walk.

    Every step's assertion is in the step. The walk legs are the ones that can
    legitimately fail -- a wall is a legal answer and walk.step() raises rather
    than pretending, so the route below is written to walk in directions the
    measurement showed the player can actually go.
    """
    from .actions import walk

    r = Route("m1_first_town")

    r.add("title",
          lambda emu: _wait_title(emu),
          "wait out the title screen. It cannot be detected by `phase`: "
          "`dotitle` runs before the IRQ loop exists, so phase is whatever reset "
          "left it. Measured: the title's own NMI installs the game IRQ only when "
          "phase is zero (x0.pds:770-777), which is the only reason phase means "
          "anything afterwards.")

    r.add("new_game",
          lambda emu: menus_and_new_game(emu),
          "START out of the title. `waitbut` returns C=0 for START and C=1 for "
          "SELECT (x0.pds:745-752) and `start` does lda #$0a / ldx #$e2 / jsr "
          "newlev on the C=0 path, so the assertion is phase $0a at curlev $E2.")

    r.add("into_level",
          lambda emu: _into_level(emu),
          "A out of the map screen into the playing level. MEASURED: this is what "
          "distinguishes a working build from ours -- Beta 1 reaches phase 0 with "
          "curlev $10 and the game clock starts; our rebuild stops in phase 3.")

    r.add("walk",
          lambda emu: walk.wander(emu, [("Right", 70), ("Right", 70), ("Up", 70),
                                        ("Left", 70)], name="town"),
          "walk east and back. Measured on Beta 1: holding a direction for 70 "
          "frames moves the player 68-70 pixels. Every leg asserts the position "
          "CHANGED, so a wall fails the run instead of passing it.")

    r.add("inventory",
          lambda emu: _inventory(emu),
          "START opens the inventory, which is level $E0 (g08). Not an overlay: "
          "`newlev` moves to it, so curlev changing is part of the assertion.")

    r.add("deferred",
          lambda emu: _deferred(emu),
          "the activities that were NOT reached, recorded so that a run's own log "
          "says so rather than leaving it implied by their absence.")
    return r


def _wait_title(emu: BizHawk):
    from .actions import act
    # `phase == 0` is true at the title AND in the playing level, so it is not a
    # usable predicate on its own. What IS usable is the same pair of things the
    # GAME waits for inside `waitbut` (x0.pds:745-746):
    #     lda fadevec / bne waitbut      -- the fade must have finished
    # and, from the main loop (x5.pds:225-227):
    #     dec second / bpl !c           -- the one-second timer must be running
    # `second` is the part that says the main loop is alive at all, which matters
    # because a wedged build has phase 0 too.
    #
    # `nmiflag` was tried here first and is WRONG for this: $002C is also
    # joykey's decoded UP-button byte, so its value between frames depends on
    # whether a direction is held. That cost a milestone run.
    ram.load_button_order()
    return act(emu, "title_ready",
               [ram.pred("phase", "eq", 0), ram.pred("fadevec", "eq", 0),
                ram.pred("second", "ne", 0)],
               buttons=(), budget=400, settle=0,
               expect_note="the title screen's own readiness: fadevec 0 and the "
                           "one-second timer running")


def menus_and_new_game(emu: BizHawk):
    from .actions import act
    return act(emu, "new_game",
               [ram.pred("phase", "eq", ram.PHASE_MAP),
                ram.pred("curlev", "eq", ram.START_LEVEL)],
               buttons=("Start",), budget=600, settle=40, pulse=8,
               expect_note=f"START must reach phase $0A at curlev ${ram.START_LEVEL:02X}"
                           f" with mapind {ram.START_MAPIND}. PULSED, not held: "
                           f"waitbut reads the START EDGE, so one long hold "
                           f"produces one edge and if the screen is not listening "
                           f"yet it is lost for the rest of the run")


def _into_level(emu: BizHawk):
    from .actions import act
    return act(emu, "into_level",
               [ram.pred("phase", "eq", ram.PHASE_MAIN)],
               buttons=("A",), budget=600, settle=60, pulse=8,
               expect_note="A on the map screen must enter the playing level "
                           "(g00). PULSED, not held, for the same reason: "
                           "g0a reads the fire A EDGE")


def _inventory(emu: BizHawk):
    from .actions import menus
    a = menus.open_inventory(emu)
    b = menus.close_inventory(emu)
    return [a, b]


def _deferred(emu: BizHawk):
    from .actions.shop import FINDING as SHOP
    from .actions.spells import FINDING as SPELL
    from .actions.talk import FINDING as TALK
    for title, text in (("shop.py", SHOP), ("spells.py", SPELL), ("talk.py", TALK)):
        emu.note(f"{title}: {text.splitlines()[0]}")
    emu.step(frames=1)
    return []


_ACTIVE: dict = {"route": None}


def active() -> Route:
    """The route currently installed, for `BizHawk.segment_digest()`."""
    if _ACTIVE["route"] is None:
        raise AssertionError("no route installed; call install() first")
    return _ACTIVE["route"]


def install(r: Route) -> Route:
    _ACTIVE["route"] = r
    return r


def segment_list_sha1_default() -> str:
    """Digest of the active route, or '-' when there is none."""
    r = _ACTIVE["route"]
    return r.digest() if r is not None else "-"