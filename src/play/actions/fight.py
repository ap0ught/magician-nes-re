"""Fighting.

MEASURED: **no enemy was encountered and nothing here has been run.** In the
first town of Beta 1 the player object slot is the ONLY live slot for the whole
walk the recon did, so there is nothing to fight yet.

The object table is four slots (`maxob equ $04`, x0.pds:242) and slot 3 is always
the player (`pi equ maxob-1`). `initob` clears all four with `ror obtyp,x` and
then activates only slot 3 with `lda #$00 / jsr actob`, so a slot left at $FF by
initob is an empty slot -- BUT the recon saw a slot with obtyp=$FF carrying live
looking data (obmod=$04, obchr=$18, obint=$00, a real position), so `obtyp ==
$FF` is NOT by itself proof that a slot is empty. `live_slots()` below therefore
reports a slot when any of several arrays is non-$FF, and says which.

Health is `obhel[3]`, and it is NOT a hit-point count: `helind` (x6.pds:33)
computes "health rating = health / 32", so the unit is a 16x scale. There is no
"HP bar" and no integer that means "how close to dying" that this harness has
measured. `player_low()` therefore compares against a threshold the caller
supplies and refuses a default it has not measured.
"""
from __future__ import annotations

from .. import ram
from ..emu import BizHawk, Result

# Which arrays have to be examined to decide whether a slot holds anything.
# $FF is what an untouched slot holds, because every one of these arrays is
# initialised to $FF or left as such; anything else is the game having written it.
_SLOT_ARRAYS = ("obtyp", "obmod", "obstat", "obxl", "obxh", "obyl", "obyh",
                "obhel", "obchr", "obint")


def live_slots(emu: BizHawk) -> list[dict]:
    """Every object slot with anything in it, and what is in it."""
    img = emu.work_ram()
    out = []
    for i in range(4):
        s = ram.object_slot(img, i)
        touched = [n for n in _SLOT_ARRAYS if s[n] != 0xFF]
        if touched:
            out.append({"slot": i, "arrays": touched, **s})
    return out


def enemies(emu: BizHawk) -> list[dict]:
    """Live slots that are not the player's (slot 3)."""
    return [s for s in live_slots(emu) if s["slot"] != ram.PLAYER_IDX]


def face_toward(emu: BizHawk, target_slot: int, budget: int = 300) -> Result:
    """Walk in the direction of another object until the player is closer.

    Asserts the distance SHRANK. "Closer" is computed from the two position
    arrays, so it is a claim about RAM and not about a screenshot.
    """
    img = emu.work_ram()
    p = ram.object_slot(img, ram.PLAYER_IDX)
    t = ram.object_slot(img, target_slot)

    def dist(image: bytes) -> int:
        a = ram.object_slot(image, ram.PLAYER_IDX)
        b = ram.object_slot(image, target_slot)
        return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])

    start = dist(img)
    if start == 0:
        raise ValueError("already adjacent")
    direction = "Right" if t["x"] > p["x"] else "Left"
    if abs(t["y"] - p["y"]) > abs(t["x"] - p["x"]):
        direction = "Down" if t["y"] > p["y"] else "Up"
    emu.note(f"face slot {target_slot} at ({t['x']},{t['y']}): {direction}, "
             f"player at ({p['x']},{p['y']}), distance {start}")
    emu.step((direction,), 60)
    after = emu.work_ram()
    now = dist(after)
    emu.screenshot(f"face_{target_slot}")
    emu.snapshot(f"face_{target_slot}")
    res = Result(name=f"face_{target_slot}", used=60, before=img, after=after,
                 held=now < start, note=f"distance {start} -> {now}")
    if not res.held:
        from ..emu import ActionFailed
        raise ActionFailed(
            f"face slot {target_slot}: holding {direction} for 60 frames did not "
            f"reduce the distance ({start} -> {now}). player "
            f"({ram.plrx(after)},{ram.plry(after)}) target "
            f"({ram.object_slot(after, target_slot)['x']},"
            f"{ram.object_slot(after, target_slot)['y']})")
    return res


def cast(emu: BizHawk, budget: int = 300) -> Result:
    """Press A to cast the spell in hand.

    Asserts `cursp` changed from $FF -- `x1.pds:711-712` clears it to $FF every
    frame the player is not casting, so it is the one byte that says a cast
    happened rather than a button was pressed.
    """
    from . import act
    return act(emu, "fight_cast",
               [ram.pred("cursp", "ne", 0xFF)],
               buttons=("A",), budget=budget, settle=10,
               expect_note="A must start a cast (cursp leaves $FF)")


def player_low(threshold: int | None) -> int:
    """`obhel` threshold meaning 'low'. Refuses to guess."""
    if threshold is None:
        raise AssertionError(
            "no health threshold has been MEASURED for this game. `obhel` is a "
            "16x scale (x6.pds:33 divides it by 32 for a rating) and this "
            "harness has never watched a character get hurt, so any number "
            "passed here would be an invention. Watch a fight, write down what "
            "obhel was when the character looked hurt, and pass that.")
    return threshold
