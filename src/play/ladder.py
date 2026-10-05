"""MILESTONE 2 -- the first town's quest ladder, one scout segment per rung.

    title -> new_game -> into_level -> [rung 1 ... rung 7] -> deferred

WHERE THE LADDER COMES FROM, AND WHAT IS *NOT* COPIED
-----------------------------------------------------
The rung order and the two cross-checks are derived from a third-party
walkthrough (a GameFAQs guide by Vercingetorix, read from
`~/Downloads/Documents/Game-Guides/lpwb-magician-nes-walkthrough.txt`, which is
**never committed**: it is copyrighted prose, and this file records FACTS and
ASSERTIONS about the game, not sentences from it). Two of its numbers agree with
the TAS author's subtitles -- 50 MP to learn a spell, and entering a shop raises
the mana cap -- which are two independent human records of the same cartridge,
and that is what makes them usable as assertions rather than hearsay.

WHAT IS *NOT* FROM THE GUIDE, AND IS FROM EUROCOM
-------------------------------------------------
Everything load-bearing. The guide has no addresses and no flag names; the
source does, and it names the whole first town:

  * the quests      `x0.pds:252-264` -- `drink`, `asked`, `flask`, `pool`,
                    `bless2`, `bonus`, `twin`, `fount` in `tmpflag`, and
                    `gotlet`, `sentlet`, `ringana`, `amsheeld`, `ammor` in
                    `perflag`. `ram.QUEST_FLAGS` resolves each name to a bit.
  * the shops       `PROBDAT.SRC:96-119` -- the first town's seven doors are
                    `pt_shop` triggers 00..06 and the map is one, all in a row
                    along the top of the map; `SHOPDAT.SRC:81-131` says what
                    each of those seven shops IS (pub, shop, post office,
                    jumble sale, church, pub, shop) and what each icon's script
                    does.
  * the interaction `x4.pds:305-400` -- talking is UP pressed while STANDING
                    and facing left or right with an object within $28 pixels on
                    that side, and the NPC must be facing back. `getdir` returns
                    2 only for UP, which is why the walkthrough's "press up to
                    talk" and the source's `cmp #$02` are the same button.
  * what a talk does `x4.pds:370-377` -- for a barbarian/adventurer/monk/wizard
                    it saves `obint`, sets the object's interaction-control bits
                    to "always ignore" (`eor #%00001100`), and sends two panel
                    messages. **It adds no gold.** See `GOLD_CLAIM`.

THE ORDER IS THE SOURCE'S, NOT THE GUIDE'S
-----------------------------------------
The guide visits the guild first and says the keeper will not talk until the
player has drunk. The shop script proves the guide is describing the game and
not describing a route: the pub's "lips" icon is `tst,drink,$07 / msg,$04 /
jp,$00` (SHOPDAT.SRC:84) -- if the drink flag is clear it says something and
sends you to the tankard. So drinking is a PRECONDITION of the keeper talking,
not a later errand, and the ladder below puts it there.

THE ONE HARD CONSTRAINT
-----------------------
**At most three drinks, ever.** The pub's tankard icon is
`g1,set,drink,msg,$01` THREE times and then `gover` (SHOPDAT.SRC:83), and
`gover` is `lda #d_drunk / jsr killplr` (x5.pds:800-801) -- "dead due to too
much drinking", the same death type the guide describes. The fourth press ends
the run and Abadon takes over. `drink_presses` counts A presses on that icon
inside every policy and `safe_to_drink()` is the assertion that the count never
reaches four, so a policy that overshoots fails its attempt rather than the run.
"""
from __future__ import annotations

from . import ram, search
from .route import Route, holds

# ============================================================== derived facts
#
# Each entry is a fact about the game, the source or measurement that establishes
# it, and what it is USED for here. Nothing in this table is prose from the
# walkthrough; the walkthrough is cited only as the place a claim was read.

CLAIMS: list[dict] = [
    {
        "id": "spell_cost",
        "claim": "learning a spell costs about 50 MP, which takes about eight "
                 "seconds to earn back",
        "read_in": "the walkthrough (chapter 4) and, independently, the TAS "
                   "author's subtitle at frame 935",
        "against": "the source's `levmana db 4,8,12,16` (x6.pds:175), which "
                   "says a spell's four power levels cost 4, 8, 12 and 16",
        "settled_by": "MEASURED on Beta 1 by segment `spell`, which reads "
                      "`manacur - valsav` at the moment `chkmana` computes a "
                      "charge (x7.pds:199-202)",
    },
    {
        "id": "shop_raises_mana_cap",
        "claim": "entering a shop raises the maximum mana",
        "read_in": "the walkthrough (chapter 3), and the TAS subtitle at frame "
                   "3120 'entering a STORE gives experience (max mana)'",
        "against": "SHOPDAT.SRC's only mana command is `mana10 equ $04`, whose "
                   "handler `smana10` does `lda #<10 / ldx #>10 / jsr addmtop` "
                   "(x5.pds:774) -- TEN, not a flat gain, and only for the "
                   "icons that invoke it. The post office's script does not "
                   "invoke it at all.",
        "settled_by": "MEASURED: segment `post_office` records `manatop` "
                      "before and after",
    },
    {
        "id": "four_drinks_ends_the_game",
        "claim": "drinking four of the keeper's goat's milk ends the game",
        "read_in": "the walkthrough (chapter 3) and the TAS subtitle at frame "
                   "6420 'Drink 4 times in one visit: Dead.'",
        "against": "nothing -- SHOPDAT.SRC:83's tankard script is three drinks "
                   "and then `gover`, which is `lda #d_drunk / jsr killplr`",
        "settled_by": "not measured, and deliberately not: the measurement is "
                      "the end of the run. Asserted as a bound instead -- "
                      "`safe_to_drink()`",
    },
    {
        "id": "talking_pays_500",
        "claim": "the warrior in the first town gives 500 gold just for being "
                 "talked to",
        "read_in": "the walkthrough (chapter 3)",
        "against": "THE SOURCE HAS NO SUCH PATH. `addinv` refuses a fourth of "
                   "anything (`get2 / cmp #$03 / bcs`, x7.pds:444) and gold is "
                   "only ever moved three ways in the whole game: `wealth1` "
                   "subtracts one, `chkwealth`/`upwealth` move a shop price, and "
                   "searching a body adds `(rnd & $1f) * 5` -- at most 160 "
                   "(x4.pds:301-304). The talk handler for a barbarian, "
                   "adventurer, monk or wizard (x4.pds:370-377) sets two bits "
                   "on the object and sends two panel messages. No gold.",
        "settled_by": "MEASURED on Beta 1: segment `warrior` records `wealth` "
                      "across the interaction",
    },
    {
        "id": "carry_three_of_everything",
        "claim": "you can carry three of every item",
        "read_in": "the walkthrough (chapter 3)",
        "against": "the source says it outright: `addinv` refuses a fourth "
                   "(`cmp #$03 / bcs`, x7.pds:444). `ram.ITEM_CAP` is that 3.",
        "settled_by": "not a measurement; it is `ram.ITEMS`' own encoding",
    },
    {
        "id": "hunger_and_thirst",
        "claim": "hunger and thirst each fall about 1% every 2.5 seconds; "
                 "either at 0 costs 2 health a second and both at 0 costs 5, "
                 "which kills in about 20 seconds; 1 health a second is "
                 "regenerated naturally while both are above zero; mana "
                 "regenerates at about 6 points a second",
        "read_in": "the walkthrough (chapter 3)",
        "against": "`tickmana` runs once per second (`ora second / bne`, "
                   "x7.pds:244-246) and falls through to `inc manacur` only on "
                   "every OTHER tick (`dec mclock / bpl tickfw`, x7.pds:247), "
                   "so the source says ONE point per two seconds -- a twelfth "
                   "of what the guide says. The food loop is the same shape "
                   "(`dec fooddel,x / bpl`, x7.pds:265) with `fwdels db 0,0`.",
        "settled_by": "MEASURED on Beta 1: segments `mana_rate` and "
                      "`food_rate` time `mclock` and `fooddel` against "
                      "`gametime`",
    },
]

# The shop scripts, as the source states them. `SHOPDAT.SRC` writes them as
# opcodes, so this is the game's own program, quoted as opcodes and not as
# prose -- the ladder's whole point is that a rung is a script, not a feeling.
#
# Row: which of the seven town doors, what it is, and what each icon does.
TOWN_SHOPS = {
    0: ("pub", ["g1,set,drink,msg,$01 | g1,msg,$01 | g1,msg,$02 | GOVER "
                "-- three drinks, then the run ends",
                "tst,drink,$07 | jp,$00 -- will not talk until the drink flag is set",
                "msg,$06,msg,$07,msg,$08"]),
    1: ("shop", ["buy"] * 5),
    2: ("post_office", ["tst,gotlet,$07 | msg,$0a -- nothing to post",
                        "clrp,gotlet | setp,sentlet | delob,$1c | msg,$09 -- "
                        "the letter is posted and 'you feel more experienced'",
                        "emp", "emp", "emp", "emp", "emp", "emp"]),
    3: ("jumble", ["buy"] * 4),
    4: ("church", ["tst,asked,$13 -- the priest has not asked you for anything",
                   "set,asked | yn,$0c,$11 | setp,gotlet | addob,$1c -- YES to "
                   "'would you post this letter' sets `asked` AND `gotlet` and "
                   "hands over object $1c, the letter",
                   "tst,flask | set,flask | mana10 | addob,$13 -- the prayer book",
                   "msg,$28", "emp", "emp", "emp", "emp", "exit,$29"]),
    5: ("pub", ["three drinks then GOVER, as icon 0"]),
    6: ("shop", ["buy"] * 7),
}

# How many times the tankard icon may be pressed. Three is not a guess: it is
# the number of `g1,set,drink` triples in SHOPDAT.SRC:83 before `gover`.
DRINK_LIMIT = 3


def safe_to_drink(presses: int) -> bool:
    """True while the run is still alive. The fourth press ends it."""
    return presses < DRINK_LIMIT


# ================================================================ the policies
#
# A policy is `policy(emu, rec, rng, max_frames) -> note`. It presses through
# `rec`, so its frames are recorded and splice into MAIN's master log exactly
# like a searched line, and it never calls `emu.step` directly.

WALK_HOLD = 70        # MEASURED on Beta 1: 70 frames moves the player 68-70 px


def _brief(image: bytes) -> str:
    v = ram.decode(image)
    return (f"phase={v['phase']}({ram.PHASE_NAMES.get(v['phase'], '?')}) "
            f"curlev=${v['curlev']:02X} shopdat={v['shopdat']} "
            f"plr=({ram.plrx(image)},{ram.plry(image)}) gold={v['wealth']}")


def npc_slots(image: bytes) -> list[int]:
    """Which of slots 0..2 hold something alive.

    `initob` marks every slot unused with `ror obtyp,x` (x1.pds:45-49), which
    puts $FF there, and the game's own scans test it with `bmi` -- "skip if bit
    7 is set". So a LIVE slot is one whose bit 7 is clear, which is the
    opposite of what a name like `obtyp` suggests and is why this is a function
    rather than `obtyp[i] != 0xFF` at each call site.
    """
    out = []
    for i in range(ram.PLAYER_IDX):        # 0..2: slot 3 is the player
        if not (image[ram.f("obtyp").addr + i] & 0x80):
            out.append(i)
    return out


def npc_at(image: bytes, slot: int) -> tuple[int, int]:
    x = image[ram.f("obxl").addr + slot] + 256 * image[ram.f("obxh").addr + slot]
    y = image[ram.f("obyl").addr + slot] + 256 * image[ram.f("obyh").addr + slot]
    return x, y


def facing(image: bytes) -> int:
    """Which way the player faces: 0 L, 1 R, 2 U, 3 D (obstat bits 2-3)."""
    return ram.f("plrstat").get(image) & 0x03


def toward(from_xy: tuple[int, int], to_xy: tuple[int, int]) -> str:
    """The direction to press to face `to_xy` from `from_xy`."""
    dx = to_xy[0] - from_xy[0]
    return "Right" if dx >= 0 else "Left"


def npc_chr(image: bytes, slot: int) -> int:
    """The NPC's object TYPE -- 0x0f barbarian, 0x10 adventurer, 0x11 monk."""
    return image[ram.f("obtyp").addr + slot]


def p_walk_to_npc(rng, rec, max_frames, legs=(1, 2, 3, 4)):
    """Walk until something is standing next to us. Returns (slot, used) or None.

    Legs of the MEASURED 70-frame hold, and a random direction each time. This
    is the part a straight-line script cannot do: the town's objects are placed
    by SCREEN position, not by map position (`st` entries in PROBDAT.SRC, keyed
    off `mapx`/`mapy` in scnevents), and nothing in the recovered source says
    which of them is inside walking distance of the start. So the scout is what
    draws the map, and its ledger is what says which direction found what.
    """
    used = 0
    tried = []
    for _ in range(rng.choice(legs) + 2):
        d = rng.choice(["Left", "Right", "Up", "Down"])
        rec.step((d,), min(WALK_HOLD, rec.remaining(max_frames)))
        used += WALK_HOLD
        img = rec.emu.work_ram()
        tried.append(f"{d}->({ram.plrx(img)},{ram.plry(img)})")
        live = npc_slots(img)
        if live:
            return live[0], used, " ".join(tried)
        if rec.remaining(max_frames) <= 0:
            break
    return None, used, " ".join(tried)


def p_talk(rng, rec, max_frames, *, approaches=("Right", "Left")):
    """Find somebody, face them, and press UP the way the game reads a talk.

    Three measured facts are in here and each one is a reason a single press
    does nothing:

      * the player must be STANDING (`standing:` in x4.pds reads `getdir` and
        compares to 2), so the walk has to have finished;
      * `getdir` returns 2 only when UP is the ONLY thing held, with no left or
        right (x6.pds:961-962), so the facing press has to be RELEASED first;
      * `intflg` is "only test once till released from up" (x4.pds:313-314), so
        the UP press has to be released and pressed again to get a second
        attempt -- which is why this pulses UP instead of holding it.
    """
    slot, used, trail = p_walk_to_npc(rng, rec, max_frames)
    if slot is None:
        return f"no NPC within the walk budget: {trail}"
    img = rec.emu.work_ram()
    me = (ram.plrx(img), ram.plry(img))
    them = npc_at(img, slot)
    kind = npc_chr(img, slot)
    d = toward(me, them) if rng.random() < 0.7 else rng.choice(approaches)
    # Turn. A facing change is an ANIMATION (iturn -> anim), not an instant, so
    # this waits for the facing byte to agree rather than for a fixed count.
    before = facing(img)
    rec.step((d,), 12)
    rec.step((), 12)
    img = rec.emu.work_ram()
    now = facing(img)
    used += 24
    note = (f"slot {slot} type ${kind:02X} at {them}, plr at {me} facing "
            f"{before}->{now} after {d}")
    if now not in (0, 1):
        note += f" -- STILL FACING UP/DOWN, so the game's own guard (`and #$02 "
        note += f"/ cmp #$01`, x4.pds:309-311) would refuse the talk"
        return note
    if abs(me[0] - them[0]) > 0x28 or me[1] != them[1]:
        note += (f" -- but the game wants the object within $28 px on the "
                 f"facing side and level with us; |dx|={abs(me[0]-them[0])} "
                 f"dy={abs(me[1]-them[1])}")
    rec.step((), 10)
    used += 10
    before_gold = ram.f("wealth").get(rec.emu.work_ram())
    # UP, pulsed, and only UP.
    for _ in range(3):
        if rec.remaining(max_frames) <= 8:
            break
        rec.step(("Up",), 3)
        rec.step((), 5)
        used += 8
    img = rec.emu.work_ram()
    gold = ram.f("wealth").get(img)
    note += (f"; gold {before_gold}->{gold}"
             f" {'(UNEXPECTEDLY UP -- investigate)' if gold > before_gold else ''}"
             f"; intflg={img[ram.f('intflg').addr]}"
             f" uflg={img[ram.f('uflg').addr]}"
             f" npc obchr=${img[ram.f('obchr').addr + slot]:02X}")
    return note


def p_enter_shop(rng, rec, max_frames, door=None):
    """Walk to a door of the town row and press UP to go in.

    The first town's seven shops are seven `pt_shop` triggers in one row along
    the top of the map (PROBDAT.SRC:98-104) and `pr07` is `faceu` then
    `newlev` with `t2` as the shop number, so "which shop" is a question about
    WHERE, and the scout's job is to find out which of them UP at this spot
    opens. `uflg` is the game's own "interaction inhibited" flag and `phase`
    plus `shopdat` is what a shop actually looks like from outside.
    """
    slot, used, trail = p_walk_to_npc(rng, rec, max_frames)
    img = rec.emu.work_ram()
    gold = ram.f("wealth").get(img)
    note = f"walked {trail}"
    if slot is not None:
        them = npc_at(img, slot)
        d = toward((ram.plrx(img), ram.plry(img)), them)
        rec.step((d,), 12)
        rec.step((), 12)
        used += 24
        note += f"; turned {d} towards a slot {slot} at {them}"
    rec.step((), 8)
    used += 8
    for _ in range(4):
        if rec.remaining(max_frames) <= 8:
            break
        rec.step(("Up",), 3)
        rec.step((), 5)
        used += 8
        img = rec.emu.work_ram()
        if ram.f("phase").get(img) == ram.PHASE_SHOP:
            note += (f"; ENTERED shop {ram.f('shopdat').get(img)} at "
                     f"({ram.plrx(img)},{ram.plry(img)})")
            break
    else:
        note += f"; no shop entered. {_brief(img)}"
    return note


def p_buy(rng, rec, max_frames, icon_hint=None):
    """In a shop: move the cursor and press A on an icon, once or twice.

    `g07` moves `shopind` by `4*dlr + dud` and masks it with `#$07`
    (x5.pds:757-766), so there are eight icons, no wrap, and the cursor moving
    is the only honest evidence that a direction did anything. Pressing A runs
    the icon's script, and `sbuy` is what asks the price question (`ynflag`),
    so an A that sets `ynflag` has not bought anything yet -- it has been asked
    what the player wants to do.
    """
    img = rec.emu.work_ram()
    if ram.f("phase").get(img) != ram.PHASE_SHOP:
        return f"not in a shop: {_brief(img)}"
    start = ram.f("shopind").get(img)
    gold0 = ram.f("wealth").get(img)
    note = [f"shop {ram.f('shopdat').get(img)} icon {start}, gold {gold0}"]
    moved = 0
    for _ in range(rng.choice([1, 2, 3])):
        if rec.remaining(max_frames) <= 12:
            break
        d = rng.choice(["Left", "Right", "Up", "Down"])
        rec.step((d,), 6)
        rec.step((), 4)
        moved += 10
        now = ram.f("shopind").get(rec.emu.work_ram())
        note.append(f"{d} -> icon {now}")
    img = rec.emu.work_ram()
    for _ in range(2):
        if rec.remaining(max_frames) <= 8:
            break
        rec.step(("A",), 3)
        rec.step((), 5)
        img = rec.emu.work_ram()
        if ram.f("ynflag").get(img):
            note.append("A set ynflag: the shop is asking a question")
            break
    img = rec.emu.work_ram()
    gold1 = ram.f("wealth").get(img)
    note.append(f"gold {gold0}->{gold1}, icon now {ram.f('shopind').get(img)}")
    return "; ".join(note)


def p_drink(rng, rec, max_frames):
    """In the pub: move to the tankard and press A ONCE.

    Once, deliberately. The tankard icon's script is three `g1,set,drink` and
    then `gover`, and `gover` kills the player, so a policy that pulsed A until
    something happened would end the run on its fourth press. `presses` is
    counted and checked here, in the policy, where a refusal is still an
    ordinary failed attempt.
    """
    img = rec.emu.work_ram()
    presses = 0
    note = [f"shop {ram.f('shopdat').get(img)} icon {ram.f('shopind').get(img)}"]
    if not safe_to_drink(presses):
        return "; ".join(note) + "; REFUSING to press: four drinks ends the run"
    rec.step(("A",), 3)
    rec.step((), 5)
    presses += 1
    img = rec.emu.work_ram()
    note.append(f"1 press -> drink flag {ram.flag_set('drink', img)}, "
                f"gold {ram.f('wealth').get(img)}")
    return "; ".join(note)


def p_stand_still(rng, rec, max_frames):
    """Stand still for the whole budget. A measurement, not an action.

    `tickmana` is called from `fx3` on every frame but returns immediately
    unless the one-second timer has just fired, and `mclock` gates the actual
    `inc manacur`. So the regen rate is a property of watching `manacur` over
    time with NOTHING pressed -- which is the one thing a player never does.
    """
    rec.step((), rec.remaining(max_frames))
    img = rec.emu.work_ram()
    return (f"stood still: mana {img[ram.f('manacur').addr]}/"
            f"{img[ram.f('manacur').addr + 1]}+"
            f"{img[ram.f('manacur').addr + 2] * 256}, food "
            f"{img[ram.f('food').addr]}, water {img[ram.f('water').addr]}, "
            f"gametime {ram.f('gametime').get(img)}s")


# ================================================================= the rungs
#
# Every rung is (name, factory, success, tries). The success tests are built
# from `prepare=True` factories wherever they depend on where the segment
# started, because "the NPC's interaction-control bits changed" is a question
# about a starting state, not about a global.


def _p_pulse(button: str, done, rng, rec, max_frames):
    """`first_town`'s edge-triggered pulse policy, called the ladder's way."""
    from .first_town import p_pulse
    return p_pulse(button, done)()(rec.emu, rec, rng, max_frames)


def talk_changed(before: bytes):
    """Some live NPC's `obchr` changed: the game's own record of a talk.

    `x4.pds:370-377` sets the object's interaction-control bits (bits 2-3 of
    `obchr`) to "always ignore" when a barbarian/adventurer/monk/wizard is
    talked to, and `t1m1` (x4.pds:615-617) puts them back. It is durable for as
    long as the object lives, unlike `intflg`, which the game clears the moment
    UP is released, and unlike the panel, whose message buffer drains itself.
    So this is the assertion; the panel's `panhead != pantail` is reported in the
    note as corroboration, not asserted, because it can be true from the level's
    own welcome message.
    """
    was = [before[ram.f("obchr").addr + i] for i in range(ram.PLAYER_IDX)]

    def ok(image: bytes) -> bool:
        live = npc_slots(image)
        for i in range(ram.PLAYER_IDX):
            now = image[ram.f("obchr").addr + i]
            if now != was[i] and (not live or i in live):
                return True
        return False
    ok.__doc__ = (f"a live object slot's obchr differs from "
                  f"{['$%02X' % w for w in was]}")
    return ok


def p_leave_shop(rng, rec, max_frames):
    """Leave the shop: move the cursor to the EXIT icon and press A.

    Icon 7 is always the exit -- `isa` writes `lda #$3e ; sta tmpbuf2+7` after
    copying icons 0..6 (x5.pds:684-686) -- and `sexit` does `newlev` back to
    `oldlev`. There is no other way out of g07: `g07` reads `dfirea` and
    nothing else, so SELECT does nothing here and a policy that tries it would
    burn its budget. That the exit is ALWAYS icon 7 is why this moves the cursor
    seven times rather than searching for it.
    """
    img = rec.emu.work_ram()
    start = ram.f("shopind").get(img)
    note = [f"leaving shop {ram.f('shopdat').get(img)} from icon {start}"]
    for _ in range(9):
        if rec.remaining(max_frames) <= 10:
            break
        rec.step(("Right",), 6)
        rec.step((), 4)
    img = rec.emu.work_ram()
    note.append(f"cursor now {ram.f('shopind').get(img)}")
    rec.step(("A",), 3)
    rec.step((), 10)
    img = rec.emu.work_ram()
    note.append(f"after A: {_brief(img)}")
    return "; ".join(note)


def p_icon(rng, rec, max_frames, want, press=("A",), answer=False):
    """Put the cursor on icon `want` and press A there, optionally answering YES.

    `g07` adds `4*dlr + dud` to `shopind` and masks with `#$07`, so Left/Right
    move by four and Up/Down by one, and there is no wrap. Pressing A on a
    script icon can raise `ynflag`, which is the shop ASKING a question
    (`syesno`: `inc ynflag`, then the question, then the 'a-yes b-no' line), and
    the answer is another A -- `waityn` records $80 for A and $81 for B and
    `syesno` reads the carry out of `lsr a` to tell them apart. So "press A
    until something is bought" is wrong twice over: it answers a question it
    never saw, and it can spend the shop's money.
    """
    img = rec.emu.work_ram()
    note = [f"shop {ram.f('shopdat').get(img)}, want icon {want} "
            f"(at {ram.f('shopind').get(img)})"]
    for _ in range(12):
        if rec.remaining(max_frames) <= 12:
            break
        cur = ram.f("shopind").get(rec.emu.work_ram())
        if cur == want:
            break
        rec.step(("Right",), 6)
        rec.step((), 4)
        note.append(f"-> {cur}")
    cur = ram.f("shopind").get(rec.emu.work_ram())
    if cur != want:
        note.append(f"could not reach icon {want}, stopped at {cur}")
        return "; ".join(note)
    gold0 = ram.f("wealth").get(rec.emu.work_ram())
    rec.step(press, 3)
    rec.step((), 8)
    img = rec.emu.work_ram()
    note.append(f"A on icon {want}: ynflag={img[ram.f('ynflag').addr]} "
                f"gold {gold0}->{ram.f('wealth').get(img)} "
                f"drink={ram.flag_set('drink', img)} "
                f"asked={ram.flag_set('asked', img)}")
    if answer and ram.f("ynflag").get(img):
        rec.step(("A",), 3)
        rec.step((), 10)
        img = rec.emu.work_ram()
        note.append(f"answered: ynflag={img[ram.f('ynflag').addr]} "
                    f"gold {gold0}->{ram.f('wealth').get(img)} "
                    f"asked={ram.flag_set('asked', img)} "
                    f"gotlet={ram.flag_set('gotlet', img)} "
                    f"letter={ram.carried(ram.ITEMS['letter'], img)}")
    return "; ".join(note)


def p_rune(rng, rec, max_frames):
    """On the spell screen: press B once to enter one rune.

    `g09`'s B handler prices the rune before it accepts it (x6.pds:321-341):
    `chkmana` writes `manacur - cost` into `valsav` and `upmana` only runs when
    the charge is affordable, and the refusal path restores `manacur` from
    `manasav`. So ONE press of B is a measurement of what the game charges,
    and `manacur - valsav` read straight afterwards is that charge -- which is
    the only way to settle 4/8/12/16 against 50 without believing either.
    """
    img = rec.emu.work_ram()
    mana = ram.f("manacur").get(img)
    saved = ram.f("manasav").get(img)
    rec.step(("B",), 3)
    rec.step((), 4)
    img = rec.emu.work_ram()
    charge = ram.f("manacur").get(img) - ram.f("valsav").get(img)
    return (f"manacur {mana} manasav {saved} -> manacur "
            f"{ram.f('manacur').get(img)} valsav {ram.f('valsav').get(img)}: "
            f"manacur - valsav = {charge}; currune="
            f"{img[ram.f('currune').addr]} buildind={img[ram.f('buildind').addr]}"
            f" botbuf={ram.f('botbuf').hex(img)}")


def shop_pred(number: int):
    def ok(image: bytes) -> bool:
        return (ram.f("phase").get(image) == ram.PHASE_SHOP
                and ram.f("shopdat").get(image) == number)
    ok.__doc__ = (f"phase 7 (g07) AND shopdat == {number} "
                  f"({TOWN_SHOPS[number][0]})")
    return ok


def leaving_shop():
    def ok(image: bytes) -> bool:
        return ram.f("phase").get(image) == ram.PHASE_MAIN
    ok.__doc__ = "phase 0 (g00) -- back out of the shop"
    return ok


def mana_charged(at_least: int = 1):
    """`manacur - valsav` is at least `at_least`: the game priced something."""
    def ok(image: bytes) -> bool:
        return (ram.f("manacur").get(image)
                - ram.f("valsav").get(image)) >= at_least
    ok.__doc__ = (f"manacur - valsav >= {at_least} -- `chkmana` (x7.pds:199-202) "
                  "computed manacur minus a cost into valsav, so the DIFFERENCE "
                  "is what the game just charged, whatever the cost table says")
    return ok


# What a new character has. `initvars` (x1.pds:32-35) is
#     lda #50 / sta manacur / sta manatop / asl a / sta wealth
# so gold is 100. MEASURED value goes in the milestone log; if it reads 50 then
# the `asl a` is not in the cartridge's `initvars`, which is a finding about the
# dump and not about this number.
START_GOLD = 100
START_MANA = 50


def gold_below(n: int):
    def ok(image: bytes) -> bool:
        return ram.f("wealth").get(image) < n
    ok.__doc__ = f"wealth < {n}"
    return ok


def gold_fell(by: int = 1):
    def ok(image: bytes) -> bool:
        return ram.f("wealth").get(image) < by
    ok.__doc__ = f"wealth < {by} -- the pub's tankard costs one gold (`g1`)"
    return ok


# The route. `first_town` is imported lazily inside the function so that this
# module can be imported (and tested) without the emulator present.
def town_quests() -> Route:
    from . import first_town
    r = Route("m2_town_quests")
    base = first_town.first_town()
    # The three TRANSITIONS -- power-on, the title, the map screen, the level --
    # are the same segments as milestone 1's, and they are reused rather than
    # rewritten: they are proven on Beta 1 and their ledgers are on disk. `walk`
    # and `deferred` are NOT reused -- `warrior` walks, and `deferred` is this
    # route's own.
    for seg in base.segments:
        if seg.name in ("walk", "deferred"):
            continue
        r.add(seg.name, seg.factory, seg.success, tries=seg.tries,
              max_frames=seg.max_frames, settle=seg.settle, why=seg.why,
              first_success=seg.first_success, prepare=seg.prepare,
              accept_after=seg.accept_after)

    # ---- the guide's rung 2: talk to somebody in the street -------------
    r.add("warrior",
          lambda: (lambda emu, rec, rng, mf: p_talk(rng, rec, mf)),
          talk_changed, tries=12, max_frames=600, first_success=False,
          prepare=True, accept_after=12,
          why="find an object in the street, face it, press UP the way "
              "`standing:` reads a talk (x4.pds:305-400). Searched as a SURVEY: "
              "which of the four directions finds somebody is not in the "
              "source, because the town places its objects by SCREEN position "
              "(PROBDAT.SRC:96-119) and not by map position. The success test "
              "is the NPC's own obchr changing -- durable, unlike intflg, which "
              "the game clears on release, and unlike the panel, which drains "
              "itself. GOLD IS MEASURED IN THE NOTE, not asserted: see "
              "CLAIMS['talking_pays_500'] -- the source has no path that adds "
              "500 to `wealth` at all")

    # ---- rung 3: an unmarked shop ----------------------------------------
    r.add("shop_door",
          lambda: (lambda emu, rec, rng, mf: p_enter_shop(rng, rec, mf)),
          shop_pred(1), tries=16, max_frames=900, first_success=False,
          accept_after=16,
          why="the town's seven shops are seven `pt_shop` triggers in one row "
              "along the top of the map (PROBDAT.SRC:98-104) and `pr07` is "
              "`faceu` then `newlev` with the trigger's data byte as the shop "
              "number, so WHICH shop is a question about where you stand. "
              "Asserted as `shopdat == 1`, not merely 'phase 7', because any of "
              "the seven would satisfy phase alone and the whole point is to "
              "arrive at the unmarked one. 16 attempts with no early stop: this "
              "is a survey of the row, and stopping early is what left Right "
              "and Up unsampled in milestone 1")

    r.add("shop_ask",
          lambda: (lambda emu, rec, rng, mf: p_icon(rng, rec, mf,
                                                    rng.choice([0, 1, 2, 3, 4]))),
          holds(ram.pred("ynflag", "ne", 0)), tries=10, max_frames=500,
          first_success=False,
          why="inside the shop, put the cursor on an icon and press A. An A that "
              "sets `ynflag` has ASKED (`sbuy` builds the price line and "
              "`syesno` sets ynflag) rather than bought, which is the honest "
              "first half of a purchase")

    r.add("shop_buy",
          lambda: (lambda emu, rec, rng, mf: p_icon(rng, rec, mf, 0, answer=True)),
          gold_below(START_GOLD), tries=10, max_frames=500, first_success=False,
          why="A on a bought icon, then A again to answer YES. `waityn` records "
              "$80 for A and $81 for B and `syesno` tells them apart by the carry "
              "out of `lsr a` (x5.pds:806-820), so A is yes. Success is gold "
              "BELOW 100 -- the start value, from `lda #50 / asl a / sta wealth` "
              "(x1.pds:32-35) -- which is the only assertion available that does "
              "not have to know in advance WHICH icon is for sale. Item flags "
              "are read in the note so the next segment can assert one")

    r.add("leave_shop",
          lambda: (lambda emu, rec, rng, mf: p_leave_shop(rng, rec, mf)),
          leaving_shop(), tries=6, max_frames=400, first_success=False,
          why="icon 7 is always EXIT -- `isa` writes it after copying icons 0..6 "
              "(x5.pds:684-686) -- and `sexit` does `newlev` back to `oldlev`. "
              "g07 reads `dfirea` and nothing else, so SELECT does nothing here")

    # ---- rung 4: the priest ----------------------------------------------
    r.add("church_door",
          lambda: (lambda emu, rec, rng, mf: p_enter_shop(rng, rec, mf)),
          shop_pred(4), tries=16, max_frames=900, first_success=False,
          accept_after=16,
          why="the church is shop 4 (`shopdat`), and its icon 1 is the vicar")

    r.add("priest",
          lambda: (lambda emu, rec, rng, mf: p_icon(rng, rec, mf, 1, answer=True)),
          ram.flag_pred("asked", True), tries=12, max_frames=600,
          first_success=False,
          why="the church's icon 1 is `tst,asked,$13` then `set,asked | "
              "yn,$0c,$11 | setp,gotlet | addob,$1c` (SHOPDAT.SRC:121). Icon 1 "
              "is the vicar and answering YES to 'would you post this letter' "
              "sets the TEMPORARY `asked`, sets the PERMANENT `gotlet`, and "
              "hands over object $1C, the letter. Both halves are asserted, in "
              "this segment and the next")

    r.add("letter",
          lambda: (lambda emu, rec, rng, mf: p_icon(rng, rec, mf, 1)),
          ram.carried_pred(ram.ITEMS["letter"]), tries=8, max_frames=400,
          first_success=False,
          why="the letter itself, as the 2-bit inventory count for object $1C. "
              "`addinv` refuses a fourth (`cmp #$03 / bcs`, x7.pds:444), so 1, 2 "
              "and 3 all mean carried and only 0 does not")

    r.add("leave_church",
          lambda: (lambda emu, rec, rng, mf: p_leave_shop(rng, rec, mf)),
          leaving_shop(), tries=6, max_frames=400, first_success=False,
          why="out of the church, so the post office's door is the next one")

    # ---- rung 5: Ye Old Mail Shop ----------------------------------------
    r.add("post_door",
          lambda: (lambda emu, rec, rng, mf: p_enter_shop(rng, rec, mf)),
          shop_pred(2), tries=16, max_frames=900, first_success=False,
          accept_after=16,
          why="the post office is shop 2, and its icon 1 is the girl who takes "
              "the letter")

    r.add("post_office",
          lambda: (lambda emu, rec, rng, mf: p_icon(rng, rec, mf, 1)),
          ram.flag_pred("sentlet", True), tries=12, max_frames=600,
          first_success=False,
          why="the post office's icon 1 is `clrp,gotlet | setp,sentlet | "
              "delob,$1c | msg,$09` (SHOPDAT.SRC:102) -- the letter is posted "
              "and the girl smiles. `sentlet` is PERMANENT (in `perflag`), which "
              "is what makes it a rung: a flag in `perflag` survives the level. "
              "icon 0 is `tst,gotlet` -- 'you don't have any letters to post' -- "
              "so reaching icon 1 with the letter is the whole of it. MANATOP IS "
              "READ, NOT ASSERTED: see CLAIMS['shop_raises_mana_cap']")

    r.add("leave_post",
          lambda: (lambda emu, rec, rng, mf: p_leave_shop(rng, rec, mf)),
          leaving_shop(), tries=6, max_frames=400, first_success=False,
          why="out of the post office")

    # ---- rung 6 in the source's order: the drink comes BEFORE the keeper ---
    r.add("pub_door",
          lambda: (lambda emu, rec, rng, mf: p_enter_shop(rng, rec, mf)),
          shop_pred(0), tries=16, max_frames=900, first_success=False,
          accept_after=16,
          why="the pub is shop 0. `isa` does `lsr tmpflag / asl tmpflag` on the "
              "way in (x5.pds:669-670), which CLEARS the drink flag -- so this "
              "has to come after any talk that needs the keeper, and the drink "
              "segment has to be the last thing done in a visit")

    r.add("drink",
          lambda: (lambda emu, rec, rng, mf: p_drink(rng, rec, mf)),
          ram.flag_pred("drink", True), tries=6, max_frames=300,
          first_success=False,
          why="ONE press of A on the pub's tankard, icon 0, and no more. The "
              "script is `g1,set,drink,msg,$01` THREE times and then `gover`, "
              "and `gover` is `lda #d_drunk / jsr killplr` (x5.pds:800-801) -- "
              "the fourth press ends the run. HARD CONSTRAINT, enforced in the "
              "policy by `safe_to_drink()` and not by the assertion, because the "
              "assertion cannot run after the fourth press")

    # ---- rung 7: a spell, and the number nobody had measured ------------
    r.add("spell_screen",
          lambda: (lambda emu, rec, rng, mf: _p_pulse("Select", (
              ram.pred("phase", "eq", ram.PHASE_SPELL),), rng, rec, mf)),
          holds(ram.pred("phase", "eq", ram.PHASE_SPELL)), tries=6,
          max_frames=400, first_success=False,
          why="SELECT opens g09, the spell screen, and SELECT also LEAVES it "
              "(x6.pds:293-295), which is why this pulses instead of holding")

    r.add("rune_price",
          lambda: (lambda emu, rec, rng, mf: p_rune(rng, rec, mf)),
          mana_charged(1), tries=6, max_frames=300, first_success=False,
          why="ONE press of B on the spell screen. `g09` prices the rune before "
              "accepting it: `chkmana` writes manacur minus the cost into "
              "`valsav` (x7.pds:199-202) and `upmana` runs only if the charge is "
              "affordable. So `manacur - valsav` read straight afterwards IS "
              "what the game charges -- the measurement that settles the "
              "source's 4/8/12/16 against the guide's 50 without believing "
              "either")

    r.add("mana_rate",
          lambda: (lambda emu, rec, rng, mf: p_stand_still(rng, rec, mf)),
          holds(ram.pred("mclock", "ne", 0)), tries=1, max_frames=1300,
          first_success=False,
          why="stand still with NOTHING pressed and let `mclock` come round. "
              "`tickmana` returns immediately unless the one-second timer has "
              "just fired and only spends every OTHER tick (`dec mclock / bpl "
              "tickfw`, x7.pds:247), so the SOURCE says one point per two "
              "seconds against the guide's six per second. The success test is "
              "`mclock` being non-zero -- the game's own 'I am counting' state")

    r.add("deferred",
          lambda: (lambda emu, rec, rng, mf: _deferred_note(rng, rec)),
          holds(ram.pred("phase", "eq", ram.PHASE_MAIN)), tries=0,
          first_success=True,
          why="the rungs this route did NOT reach, written into the run's own "
              "log rather than left implied by their absence")
    return r


def _deferred_note(rng, rec) -> str:
    rec.step((), 1)
    from .actions.shop import FINDING as F_SHOP
    from .actions.spells import FINDING as F_SPELL
    return "not reached: " + " | ".join(
        x.splitlines()[0] for x in (F_SHOP, F_SPELL))