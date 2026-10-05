"""The first town, as segments with a factory and a success test.

    title -> new_game -> into_level -> walk -> inventory -> inventory_close
          -> deferred

Every segment is `(name, factory, success, tries)`. Where there is genuinely
more than one sensible approach the scout is what finds out which one works, and
the attempts that failed stay in the ledger with their notes:

  * **new_game** -- START is an EDGE, so it must be pulsed. The choices are how
    long to wait before the first press, how long to hold, and how long to
    release. A press made before the screen is listening is lost for the rest of
    the run, so the lead-in is a real variable rather than a constant that
    happens to work.
  * **into_level** -- the same shape, and this is the segment where Beta 1 and
    our rebuild come apart: Beta 1 reaches g00 at logical level `$10`, ours
    computes `$20` and wedges in g03.
  * **walk** -- four directions, and a wall is a legal answer, so the scout
    discovers which way is open rather than being told. It is also the only
    segment here with more than one *winning* line, so `first_success` is false:
    the short way round is worth finding.
  * **inventory** -- START again in the playing level. It reaches phase 8 AND
    curlev `$E0`, because the inventory is a separate *level* and not an
    overlay: `newlev` moves to it. Asserting only the phase would pass on a
    build that had drawn an inventory over the town without changing level.

`deferred` is a segment too, with `tries=0`, because the activities that were
NOT reached should say so in the run's own log rather than being implied by
their absence. `talk.py` raises with its finding; shopping, fighting and spells
are unreachable from this town and each module refuses to guess.
"""
from __future__ import annotations

from . import ram
from .actions.shop import FINDING as SHOP_FINDING
from .actions.spells import FINDING as SPELL_FINDING
from .actions.talk import FINDING as TALK_FINDING
from .route import Route, holds

# Logical level and map index Beta 1 computes for the first town. MEASURED on
# Beta 1 with `src/play/differential.py`, from the two ROMs' own RAM: our
# rebuild computes $20 / mapind 2 instead, which is the whole of the difference
# between the builds at this point.
FIRST_LEVEL = 0x10
FIRST_MAPIND = 0x01


# ------------------------------------------------------------------- policies
# A policy is `policy(emu, rec, rng, max_frames) -> note`. It presses through
# `rec`, so the frames are recorded and can be spliced into MAIN's log, and it
# never touches `emu.step` directly, which would put them there immediately.

def pulse_until(rng, rec, button: str, done, budget: int) -> tuple[int, bool]:
    """Press/release in short bursts until `done` holds, or the budget runs out.

    Pulsed rather than held, and that is not a refinement. `waitbut`
    (x0.pds:745-752) reads the debounced edge bytes, and those are `$FF` only on
    the frame the value CHANGES (DISP.SRC:325-333). A button held for four
    hundred frames produces exactly one edge, on the first frame -- so if the
    screen is not yet listening, that edge is gone and nothing else happens for
    the rest of the run.

    Hold and release lengths are drawn from the rng, so two attempts are two
    different conversations with an edge-triggered input.
    """
    used = 0
    pressing = True
    while used < budget:
        n = min(rng.choice([2, 3, 4, 5, 6, 8]), budget - used)
        rec.step((button,) if pressing else (), n)
        used += n
        if all(p.holds(rec.emu.work_ram()) for p in done):
            return used, True
        pressing = not pressing
    return used, False


def p_pulse(button: str, done, leads=(0, 30, 60, 120)):
    """A pulsed-button policy for a screen transition, plus its own success test.

    Both take `done`. That is the point: a policy that waits for one thing while
    its segment asserts another is a segment that can succeed by accident, and
    this project has two of those already (an `act()` that noted an expired
    budget, and a title that was asserted ready before the fade had finished).
    """
    def factory():
        def policy(emu, rec, rng, max_frames):
            lead = rng.choice(list(leads))
            rec.step((), lead)
            used, held = pulse_until(rng, rec, button, done, max_frames - lead)
            return (f"{button} after {lead}f of lead-in, pulsed {used}f "
                    f"(held={held})")
        return policy
    return factory


def p_title(done):
    def policy(emu, rec, rng, max_frames):
        rec.step((), rng.choice([0, 10, 30, 60]))
        used, held = pulse_until(rng, rec, "A", done, max_frames)
        return f"title ready after {used}f (held={held})"
    return policy


def p_walk():
    """Walk in one random direction and hold it.

    One leg per attempt, so the scout is what decides which way this town lets
    you go, and a direction that turns out to be a wall comes back as a failed
    attempt with the player's position unchanged -- recorded with its note, not
    discarded.
    """
    def policy(emu, rec, rng, max_frames):
        d = rng.choice(["Left", "Right", "Up", "Down"])
        img0 = rec.emu.work_ram()
        p0 = (ram.plrx(img0), ram.plry(img0))
        held = rng.choice([40, 60, 70, 80, 100])
        rec.step((d,), held)
        img1 = rec.emu.work_ram()
        p1 = (ram.plrx(img1), ram.plry(img1))
        return f"{d} for {rec.frames}f: {p0} -> {p1}" + \
               ("" if p0 != p1 else "   DID NOT MOVE (wall?)")
    return policy


def p_notes(lines):
    def policy(emu, rec, rng, max_frames):
        rec.step((), 1)
        return "; ".join(lines)
    return policy


def walk_prepare(start: bytes):
    """`success` for `walk`, relative to where the player actually started.

    "The position changed" is only a meaningful question about a starting
    position, and the starting position is a fact about the run rather than about
    the route -- so the runner hands it in rather than the module keeping it in
    a global. A `walk` searched without one raises instead of passing an
    unasked question.
    """
    p0 = (ram.plrx(start), ram.plry(start))

    def ok(image: bytes) -> bool:
        return ((ram.plrx(image), ram.plry(image)) != p0
                and ram.f("phase").get(image) == ram.PHASE_MAIN)
    ok.__doc__ = (f"the player moved from ({p0[0]},{p0[1]}) AND is still in "
                  f"g00")
    return ok


# --------------------------------------------------------------------- route
def first_town() -> Route:
    """Power-on -> title -> map screen -> the first level -> a walk -> inventory."""
    r = Route("m1_first_town")

    # The title cannot be detected by `phase`: `dotitle` runs from `start` at
    # x0.pds:601, BEFORE the IRQ loop is installed, so phase is whatever reset
    # left it. What IS usable is the pair the GAME waits for inside `waitbut`
    # (x0.pds:745-746) -- `lda fadevec / bne waitbut` -- plus `second` from the
    # main loop (x5.pds:225-227), which says the main loop is alive at all. That
    # last part matters because a wedged build has phase 0 too.
    #
    # `nmiflag` was tried here first and is WRONG for this: $002C is also
    # joykey's decoded UP-button byte, so its value between frames depends on
    # whether a direction is held. That cost a milestone run.
    title_done = (ram.pred("phase", "eq", 0), ram.pred("fadevec", "eq", 0),
                  ram.pred("second", "ne", 0))
    r.add("title", p_title(title_done), holds(*title_done),
          tries=0, max_frames=400, first_success=True,
          why="wait out the title screen, on the game's own readiness test "
              "(fadevec 0 and the one-second timer running)")

    # `waitbut` returns C=0 for START and C=1 for SELECT (x0.pds:745-752), and
    # `start` does lda #$0a / ldx #$e2 / jsr newlev on the C=0 path. So START
    # must reach phase $0a at curlev $E2.
    map_done = (ram.pred("phase", "eq", ram.PHASE_MAP),
                ram.pred("curlev", "eq", ram.START_LEVEL))
    r.add("new_game", p_pulse("Start", map_done), holds(*map_done),
          tries=6, max_frames=600, first_success=True,
          why=f"START out of the title must reach phase $0A at curlev "
              f"${ram.START_LEVEL:02X}. PULSED, because waitbut reads the "
              f"START EDGE")

    # A out of the map screen into the playing level. This is the segment where
    # Beta 1 and our rebuild come apart: Beta 1 reaches g00 at logical level
    # $10 with mapind 1 and the game clock running.
    level_done = (ram.pred("phase", "eq", ram.PHASE_MAIN),
                  ram.pred("curlev", "eq", FIRST_LEVEL),
                  ram.pred("mapind", "eq", FIRST_MAPIND))
    r.add("into_level", p_pulse("A", level_done), holds(*level_done),
          tries=6, max_frames=900, first_success=True,
          why=f"the playing level (g00) at curlev ${FIRST_LEVEL:02X} and mapind "
              f"{FIRST_MAPIND}. PULSED: g0a reads the fire A EDGE. MEASURED on "
              f"Beta 1; our rebuild computes ${FIRST_LEVEL * 2:02X} and wedges "
              f"in g03")

    r.add("walk", p_walk(), walk_prepare, tries=8, max_frames=140,
          first_success=False, prepare=True,
          why="walk in one direction and hold it. MEASURED on Beta 1: 70 frames "
              "moves the player 68-70 pixels. Every attempt asserts the position "
              "CHANGED, so a wall fails the attempt instead of passing it")

    # START in the playing level opens the inventory. It reaches phase 8 AND
    # curlev $E0: the inventory is a separate level, not an overlay, so
    # `newlev` moves to it and the level byte changes. Both are asserted.
    inv_done = (ram.pred("phase", "eq", ram.PHASE_INVENTORY),
                ram.pred("curlev", "eq", 0xE0))
    r.add("inventory", p_pulse("Start", inv_done), holds(*inv_done),
          tries=6, max_frames=600, first_success=True,
          why="START opens the inventory, which is level $E0 (g08), not an "
              "overlay: phase AND curlev are both asserted")

    back_done = (ram.pred("phase", "eq", ram.PHASE_MAIN),)
    r.add("inventory_close", p_pulse("Start", back_done), holds(*back_done),
          tries=6, max_frames=600, first_success=True,
          why="START again leaves the inventory and puts MAIN back in the town")

    r.add("deferred",
          p_notes([f"shop.py: {SHOP_FINDING.splitlines()[0]}",
                   f"spells.py: {SPELL_FINDING.splitlines()[0]}",
                   f"talk.py: {TALK_FINDING.splitlines()[0]}"]),
          holds(ram.pred("phase", "eq", ram.PHASE_MAIN)), tries=0,
          first_success=True,
          why="the activities that were NOT reached, recorded so a run's own log "
              "says so rather than leaving it implied by their absence")
    return r