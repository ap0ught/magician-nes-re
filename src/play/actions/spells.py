"""Creating a spell.

MEASURED: **the spell screen was not reached and nothing here has been run.**

What the source says, so that whoever gets there knows what to assert:

    x6.pds:293-330  g09 is the spell screen. SELECT leaves it (x6.pds:293-295),
                    taking the top window's spell into `plrspell`. A brings the
                    top window's spell DOWN into `botbuf`. START runs
                    `chkspell` then `newspell`, and on success writes `botbuf`
                    into `manacur` -- so the observable effect of a successful
                    creation is that MANA DROPS.
    x6.pds:320-345  B adds a rune. `buildind` is the entry phase 0..5 and
                    `currune` is the rune 0..F.
    x6.pds:299-345  `levmana db 4,8,12,16` -- spell POWER LEVELS 1..4 cost
                    4, 8, 12 and 16 mana. So the cheapest spell costs 4, and a
                    new character with 50 mana can make twelve of them.

WHY NO DEFAULT EXISTS FOR THE MANA COST
--------------------------------------
The TAS author's subtitles say "needs 50 to learn a spell". The source says the
per-power-level costs are 4/8/12/16 (`levmana`, x6.pds:296). Both cannot be the
same number and neither has been measured here, so `commit()` takes the expected
drop from the caller and REFUSES to default it. A function that quietly assumes
4 and then asserts mana went down by 4 would fail loudly -- which is fine -- but
one that assumes 50 and asserts nothing would pass on a run where nothing
happened, which is the failure this whole module is written against.
"""
from __future__ import annotations

from .. import ram
from ..emu import BizHawk, Result
from . import act

POWER_LEVEL_COSTS = (4, 8, 12, 16)   # x6.pds:296 `levmana db 4,8,12,16`
ELEMENTS = 4                          # `levmul db 2,3,2,3` -- 2 or 3 per element

FINDING = (
    "The spell screen was not reached, so no spell was created and nothing in "
    "spells.py has been run. What is known is from the source: g09 is phase 9 "
    "(MISC.SRC:894); SELECT leaves it (x6.pds:293-295); A brings the top "
    "window's spell down into botbuf; START runs chkspell then newspell and, on "
    "success, writes botbuf into manacur, so the observable effect of a "
    "successful creation is MANA DROPPING (x6.pds:299-345). The per-power-level "
    "mana costs are 4/8/12/16 from `levmana` (x6.pds:296) -- which contradicts "
    "the TAS subtitle's 'needs 50 to learn a spell', and neither number has "
    "been measured here, so commit() takes the expected drop from the caller and "
    "has no default."
)


def open_screen(emu: BizHawk, budget: int = 300) -> Result:
    """SELECT into the spell screen (phase 9)."""
    return act(emu, "spells_open",
               [ram.pred("phase", "eq", ram.PHASE_SPELL)],
               buttons=("Select",), budget=budget, settle=30,
               expect_note="SELECT must open the spell screen (g09)")


def add_rune(emu: BizHawk, count: int = 6, budget: int = 600) -> Result:
    """Press B until `buildind` has advanced `count` times.

    Asserts the entry phase moved, not that it equals a particular number: the
    number of runes a spell needs is spell data and this harness has not read
    the spell table. `buildind` is 0..5 (x0.pdes:497), so a spell is at most six
    runes and `count` is capped by that rather than by anything measured.
    """
    if not 0 < count <= 6:
        raise ValueError(f"buildind is 0..5, so {count} runes is out of range")
    want = ram.f("buildind").get(emu.work_ram()) + count
    if want > 5:
        want = 5
    return act(emu, "spells_runes",
               [ram.pred("buildind", "eq", want)],
               buttons=("B",), budget=budget, settle=6,
               expect_note=f"B must advance buildind to {want}")


def commit(emu: BizHawk, expect_mana_cost: int, budget: int = 600) -> Result:
    """START, asserting mana fell by exactly `expect_mana_cost`.

    `expect_mana_cost` is REQUIRED and has no default. `newspell` failing is a
    real outcome -- it is what happens when you have not paid enough -- and
    "mana did not drop" has to be distinguishable from "mana dropped by an
    amount nobody predicted".
    """
    # `None` is the interesting case and the natural mistake, so it is checked
    # for BY NAME: `1 <= None <= 0xFFFF` is a TypeError, and a TypeError reads as
    # a bug in this module rather than as "you did not supply the one number
    # this function needs".
    if expect_mana_cost is None or not isinstance(expect_mana_cost, int) \
            or not 1 <= expect_mana_cost <= 0xFFFF:
        raise ValueError(
            f"spells.commit() needs expect_mana_cost: how much mana this spell "
            f"costs. It has no default on purpose. The source's `levmana` says "
            f"power levels 1-4 cost 4/8/12/16 (x6.pds:296) and the TAS "
            f"subtitles say 50; neither has been measured here, so pass the one "
            f"you have measured. Got {expect_mana_cost!r}.")
    img = emu.work_ram()
    before = ram.f("manacur").get(img)

    # manacur is one byte wide on the wire, and mana can exceed 255 (a late-game
    # character has thousands), so the predicate is on the LOW byte alone and the
    # caller is told in the note that only the low byte was asserted.
    used, held = emu.step_until(
        [ram.pred("manacur", "lt", before & 0xFF)],
        buttons=("Start",), budget=budget, what="mana to fall")
    after = emu.work_ram()
    emu.screenshot("spells_commit")
    emu.snapshot("spells_commit")
    now = ram.f("manacur").get(after)
    res = Result(name="spells_commit", used=used, before=img, after=after,
                 held=held, note=f"mana {before} -> {now}, expected -{expect_mana_cost}")
    if not held:
        from ..emu import ActionFailed
        raise ActionFailed(
            f"spells_commit: mana did not fall within {budget} frames of START. "
            f"manacur {before} -> {now}; manasav "
            f"{ram.f('manasav').get(after)}; manatop {ram.f('manatop').get(after)}; "
            f"buildind={ram.f('buildind').get(after)} "
            f"currune={ram.f('currune').get(after)} "
            f"botbuf={ram.f('botbuf').hex(after)} -- newspell refused, or the "
            f"spell was incomplete")
    if expect_mana_cost >= 0x100:
        res.note += " (only manacur's LOW byte was asserted: it cannot cross a "
        res.note += "page boundary in one write)"
        return res
    if before - now != expect_mana_cost:
        raise AssertionError(
            f"spells_commit: mana fell by {before - now}, not by the "
            f"{expect_mana_cost} the caller expected. That is not a harness bug "
            f"necessarily -- it is what a successful creation costs -- but it "
            f"means the expected cost was wrong, so the cost table in this "
            f"module is wrong.")
    return res
