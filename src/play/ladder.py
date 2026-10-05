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
        "id": "ninflag_pins_the_character",
        "claim": "on a NEW GAME this cartridge puts the player in the "
                 "game's own 'infinite' mode, so mana, gold, food, water and "
                 "health are all pinned every second and nothing drains",
        "read_in": "not read anywhere. Found by measurement, and it invalidated "
                   "three predicates this ladder had already written.",
        "against": "`initvars` ends `inc ninflag` (x1.pds:31), and Beta 1 has "
                   "the same instruction spelled out: at $8556 the bytes are "
                   "`A9 01 8D 1A 07` -- `lda #$01 / sta ninflag` -- which is the "
                   "same value on a cleared byte. `tickmana` (x7.pds:244-261) "
                   "tests `lda ninflag / bne !a` BEFORE `dec mclock`, and the "
                   "`!a` branch does `lda manatop / sta manacur / sta wealth` "
                   "and then `ldx #$00 ... dex / stx food / stx water`, i.e. it "
                   "pins mana AND gold to the mana cap and refills food, water "
                   "and health once a second.",
        "settled_by": "MEASURED on Beta 1 at frame 433 of the warrior rung: "
                      "ninflag=1, mclock=0 (and `mclock` is only ever written by "
                      "the branch this one skips, so a zero after 433 frames is "
                      "its own proof), food=255, water=255, plrhelm=255, "
                      "manacur=manatop=50, wealth=50. And the drop is TIMED: "
                      "`wealth` is 100 at frame 81 -- which is the source's "
                      "`lda #50 / asl a / sta wealth` -- and 50 at frame 155, the "
                      "first one-second tick after the town loaded. CONSEQUENCE: "
                      "gold cannot be used as a success predicate for a purchase, "
                      "and mana regeneration cannot be observed at all until "
                      "`manatop` is raised.",
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

# WHERE THE SEVEN DOORS ARE, read out of the source at import time.
#
# `PROBDAT.SRC:96-119` places them:
#     pt 00c8,0070,0018,0040,pt_shop,00
#     pt 05b8,0070,0018,0040,pt_shop,01
#     ...
# and x0.pds's `pt` macro is
#     db <$@1+p_hwi, $@3+$8, >$@1+p_hwi, <$@2+p_hhi, $@4+$8, >$@2+p_hhi, @5, @6
# so the trigger rectangle is x from `$@1+8` to `$@1+8+$@3+$8` -- the pub's is
# x 208..240, and the church's, at $0a68, is x 2672..2704. The town is ONE long
# east-west street and the player starts at x=60, which is why `shop_door` needs
# a long budget and why the seventh door is a long walk away.
#
# Parsed, not typed in, because a typed-in number is a number nobody checked.
# `src/testing/test_play_ladder.py` re-parses the source and compares.
TOWN_DOORS: dict[int, tuple[int, int]] = {}


def _load_shoplev() -> dict[int, int]:
    """`shopdat` -> the logical level that shop is played on.

    `pr07` (PROBS.SRC:267-275) is `jsr faceu / sty shopdat / ldx shoplev,y /
    lda #$07 / jmp newlev`, and `shoplev` (PROBS.SRC:520) is
    `hex d2 d5 d0 d5 d3 d1 d4 d6 d0 d4 d1 d4 d3 d7`. So the town's seven shops
    are levels $D0-$D6 and shopdat is NOT the level: the post office is shopdat 2
    but level $D0, and shopdat 1 and 3 are BOTH level $D5.

    MEASURED on Beta 1: standing in door 1's rectangle and pressing UP gives
    `curlev=$D5`, which is `shoplev[1]`. That is the first rung of the ladder to
    produce a number that agrees with the source, and it is the check that says
    the scout walked to the door it meant to walk to.
    """
    import pathlib
    import re as _re
    src = pathlib.Path(__file__).resolve().parents[2]
    text = (src / "vendor" / "Magician-NES" / "PROBS.SRC").read_bytes() \
        .decode("latin-1")
    line = next(l for l in text.split("\n") if l.startswith("shoplev"))
    return {i: int(h, 16) for i, h in enumerate(_re.findall(r"\b([0-9a-f]{2})\b", line))}


SHOPLEV: dict[int, int] = _load_shoplev()

def _load_town_doors() -> None:
    """Fill TOWN_DOORS from `vendor/Magician-NES/PROBDAT.SRC`. No path in here.

    Read as BYTES: the recovered text carries carriage returns inside a physical
    line, and `read_text()` would translate them into line breaks and shift
    every line number this file cites.
    """
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2]
    text = (src / "vendor" / "Magician-NES" / "PROBDAT.SRC").read_bytes() \
        .decode("latin-1")
    block = text[text.index("pt10\titr"):text.index("st10\tsti")]
    import re as _re
    p_hwi = 8          # x0.pds:242 -- `p_width equ $10`, `p_hwi equ p_width/2`
    for m in _re.finditer(
            r"pt\s+([0-9a-f]{4}),([0-9a-f]{4}),([0-9a-f]{4}),([0-9a-f]{4}),"
            r"pt_shop,([0-9a-f]{2})", block):
        x = int(m.group(1), 16)
        w = int(m.group(3), 16) + 8
        TOWN_DOORS[int(m.group(5), 16)] = (x + p_hwi, x + p_hwi + w)


def _load_shoplev() -> dict[int, int]:
    """`shopdat` -> the logical level that shop is played on.

    `pr07` (PROBS.SRC:267-275) is `jsr faceu / sty shopdat / ldx shoplev,y /
    lda #$07 / jmp newlev`, and `shoplev` (PROBS.SRC:520) is
    `hex d2 d5 d0 d5 d3 d1 d4 d6 d0 d4 d1 d4 d3 d7`. So the town's seven shops
    are levels $D0-$D6 and shopdat is NOT the level: the post office is shopdat 2
    but level $D0, and shopdat 1 and 3 are BOTH level $D5.

    MEASURED on Beta 1: standing in door 1's rectangle and pressing UP gives
    `curlev=$D5`, which is `shoplev[1]`. That is the first rung of the ladder to
    produce a number that agrees with the source, and it is the check that says
    the scout walked to the door it meant to walk to.
    """
    import pathlib
    import re as _re
    src = pathlib.Path(__file__).resolve().parents[2]
    text = (src / "vendor" / "Magician-NES" / "PROBS.SRC").read_bytes() \
        .decode("latin-1")
    line = next(l for l in text.split("\n") if l.startswith("shoplev"))
    return {i: int(h, 16) for i, h in enumerate(_re.findall(r"\b([0-9a-f]{2})\b", line))}


SHOPLEV: dict[int, int] = _load_shoplev()

_load_town_doors()


# How many times the tankard icon may be pressed. Three is not a guess: it is
# the number of `g1,set,drink` triples in SHOPDAT.SRC:83 before `gover`.
DRINK_LIMIT = 3


def safe_to_drink(presses: int) -> bool:
    """True while the run is still alive. The fourth press ends it."""
    return presses < DRINK_LIMIT


# A IS YES, OR IS IT. Settled from the source, against a comment that said
# otherwise.
#
# `waityn` (x5.pds:807-814) is where a shop records WHICH button was pressed:
#
#     waityn  jsr showshop / jsr waitpan
#             ldx #$80 / lda dfirea / bne !a
#             inx / lda dfireb / bne !a
#             rts
#     !a      stx ynflag / jmp rejump
#
# So A records `$80` and B records `$81`. What those two values MEAN differs by
# command, and this is the part the walkthrough-shaped intuition gets wrong:
#
#   * `syesno` (x5.pds:803-812) -- a plain question. It does `iny / lsr a`, and
#     tests the CARRY: `$80` shifts to C=1 and branches `bcs sjump`, which is the
#     NO path; `$81` shifts to C=0 and falls through to `iny / jmp redo`, which is
#     YES. **So for a question, B is yes and A is no.**
#
#   * `sbuy` (x5.pds:824-834) -- a purchase. It does `cpy #$81 / bcc !b`, which
#     is the opposite comparison: `$80` (A) is BELOW `$81`, so A takes `!b`, which
#     is the branch that calls `addinv` and `upwealth`. **So for a purchase, A
#     buys and B declines.**
#
# Both readings are self-consistent and neither is a typo, which is why the
# comment that used to sit on `shop_buy` -- "`waityn` records $80 for A and $81 for
# B and `syesno` tells them apart by the carry out of `lsr a`, so A is yes" --
# was wrong twice over: it attributed `sbuy`'s question to `syesno`, and it drew
# the wrong conclusion from the comparison it did cite. `p_icon(answer=True)`
# presses A, which is correct for a purchase and wrong for a question; the
# `buy_icon` and `answer_question` helpers below keep the two apart by name so a
# caller cannot get it wrong by accident.
#
# NOT YET MEASURED on Beta 1. This is what the source says, and it is the kind of
# reading that a scout then confirms or refutes -- which is the point of having
# both. `shop_buy`'s ledger will say whether A actually bought anything.
A_IS_YES = {"syesno": False, "sbuy": True}
# Which button answers "yes", per command. A for a purchase, B for a question.
BUY_BUTTON = "A"            # sbuy: cpy #$81 / bcc -> $80 (A) takes the buy path
QUESTION_YES_BUTTON = "B"   # syesno: iny / lsr a -> $81 (B) leaves C=0


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


def obchr_of(image: bytes, slot: int) -> int:
    """`obchr` for one object slot. One function, so the note and the test agree."""
    return image[ram.f("obchr").addr + slot]


def p_talk(rng, rec, max_frames, presses=(1, 2, 3)):
    """Find somebody, walk up to them, face them, and press UP until they answer.

    Four facts are in here and each is a reason a single press does nothing.
    All four came out of `x4.pds:305-412` and `MISC.SRC`'s `obchars`, and each was
    WRONG in the first version of this policy, which was scored a success that
    was nothing of the kind:

      * **THE COUNT.** `obchr` bits 0-1 are an interaction COUNT, and
        `oc_c0`..`oc_c2` (MISC.SRC:1421-1423) say it is "interact after the nth
        attempt". The adventurer is `oc_c0` -- zero, so it answers at once -- and
        the barbarian is `oc_c2`, so it ignores the first two presses and
        answers the third. `!i5` is the whole of that mechanism:
        `dey / sty t12 / and #%11111100 / ora t12 / sta obchr,x`.
      * **THE DISTANCE.** `!i10` wants the low byte of (plr.x - ob.x) below
        $28 and `lda t13 / bne !iz` wants the high bytes equal. The first
        version walked at the NPC from 70 pixels away, said exactly that in its
        own note, and was scored a success anyway.
      * **UP ALONE.** `getdir` returns 2 only when UP is held with neither left
        nor right (x6.pds:961-962), so the facing press must be RELEASED first
        or the game is being asked to walk, not to talk.
      * **ONE ATTEMPT PER PRESS.** `intflg` is "only test once till released
        from up" (x4.pds:313-314), so each UP must be released before the next
        one counts. That is why UP is pulsed here and not held.
    """
    slot, used, trail = p_walk_to_npc(rng, rec, max_frames)
    if slot is None:
        return f"no NPC within the walk budget: {trail}"
    img = rec.emu.work_ram()
    them = npc_at(img, slot)
    kind = npc_chr(img, slot)
    # Walk up to it, in short taps. The town row is one long east-west street,
    # and the guard is a WINDOW (|dx| <= $28, same page), so overshooting past
    # the far side is a real possibility rather than a theoretical one.
    for _ in range(26):
        img = rec.emu.work_ram()
        me = (ram.plrx(img), ram.plry(img))
        dx, dy = _delta(me, them)
        if abs(dx) <= 0x28 and dy == 0:
            break
        room = rec.remaining(max_frames) - 40
        if room <= 1:
            break
        # Step most of the remaining gap rather than 6 frames at a time: 1
        # px/frame is measured, and each poll is a socket round trip.
        n = max(1, min(room, abs(dx) - 0x28))
        rec.step((toward(me, them),), n)
        used += n
    img = rec.emu.work_ram()
    me = (ram.plrx(img), ram.plry(img))
    dx, dy = _delta(me, them)
    before = facing(img)
    d = toward(me, them)
    rec.step((d,), 10)
    rec.step((), 14)
    used += 24
    now = facing(rec.emu.work_ram())
    note = (f"slot {slot} type ${kind:02X} at {them}, plr {me}, dx={dx} dy={dy} "
            f"(guard: |dx|<=$28 and same page), facing {before}->{now} after {d}")
    if now not in (0, 1):
        note += (" -- STILL NOT FACING LEFT/RIGHT, and the game's own guard "
                 "(`and #$02 / cmp #$01`, x4.pds:309-311) refuses a talk unless "
                 "the player is")
        return note
    rec.step((), 8)
    used += 8
    gold0 = ram.f("wealth").get(rec.emu.work_ram())
    for i in range(rng.choice(presses) + 1):
        if rec.remaining(max_frames) <= 10:
            break
        rec.step(("Up",), 3)
        rec.step((), 6)
        used += 9
        b = obchr_of(rec.emu.work_ram(), slot)
        note += (f"; press {i + 1}: obchr=${b:02X} (ctrl={b >> 2 & 3} "
                 f"cnt={b & 3})")
        if (b >> 2) & 3 == 1:
            note += " -- ANSWERED: the game set this person to 'always ignore'"
            break
    img = rec.emu.work_ram()
    gold1 = ram.f("wealth").get(img)
    note += (f"; gold {gold0}->{gold1}"
             + ("  <<< UNEXPECTEDLY UP" if gold1 > gold0 else "")
             + f"; intflg={img[ram.f('intflg').addr]}"
             f" uflg={img[ram.f('uflg').addr]}"
             f" trail={trail}")
    return note


def _delta(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    """Signed 16-bit difference, because the game's guard subtracts two BYTES.

    `!i10` computes `plrx - obx` as an 8-bit low half plus an 8-bit high half
    and tests them separately, so a player 70 pixels to the WEST of the object
    has a low half of $BA and is refused -- which is not the same as being
    "far away" and is exactly what the first version of this policy got wrong.
    """
    out = []
    for p, q in zip(a, b):
        d = (p - q) & 0xFFFF
        out.append(d - 0x10000 if d > 0x7FFF else d)
    return out[0], out[1]



def p_enter_shop(rng, rec, max_frames, shop=None):
    """Walk to one of the seven doors and press UP to go in.

    The door's position is read out of `PROBDAT.SRC` rather than searched for,
    because `TOWN_DOORS` already has it: the town is one east-west street and
    the seven doors are `pt_shop` triggers along it. What the scout is left to
    find out is the part the source does not say -- how far the player actually
    gets before the pad stops moving, and whether one UP press or three is
    what the trigger wants.

    `pr07` is `faceu` then `newlev` with the trigger's data byte as the shop
    number (PROBS.SRC:267-278), and `plrevents` (PROBS.SRC:10-100) matches the
    player's box against the trigger box every frame, so the requirement is
    "inside that rectangle, facing up, on an up EDGE".
    """
    if shop is None:
        shop = rng.choice(sorted(TOWN_DOORS))
    lo, hi = TOWN_DOORS[shop]
    img = rec.emu.work_ram()
    start = ram.plrx(img)
    note = [f"door {shop} is x {lo}..{hi}, player starts at {start}"]
    # ONE STEP for the whole gap, then a short correction. This used to be
    # `step 8` in a loop with `work_ram()` after every one, which is 180 socket
    # round trips and 700 kB of bridge log for a single walk to the church --
    # and BizHawk stopped answering after about 1500 of them, so the attempt
    # died on a timeout with the walk half finished. The player moves about one
    # pixel per frame (MEASURED on Beta 1: 70 frames, 68-70 pixels), so the
    # distance IS the number of frames and there is nothing to poll for.
    for _ in range(8):
        img = rec.emu.work_ram()
        x = ram.plrx(img)
        if lo <= x <= hi:
            break
        room = rec.remaining(max_frames) - 60
        if room <= 8:
            break
        gap = (lo - x) if x < lo else (hi - x)
        n = max(1, min(room, gap))
        rec.step(("Right",) if x < lo else ("Left",), n)
    img = rec.emu.work_ram()
    x = ram.plrx(img)
    note.append(f"reached x={x} {'INSIDE' if lo <= x <= hi else 'OUTSIDE'} "
                f"the box {lo}..{hi}")
    if not (lo <= x <= hi):
        return "; ".join(note) + f"; gave up, {_brief(img)}"
    rec.step((), 10)
    for i in range(3):
        if rec.remaining(max_frames) <= 120:
            break
        rec.step(("Up",), 3)
        rec.step((), 6)
        img = rec.emu.work_ram()
        lev = ram.f("curlev").get(img)
        if lev == SHOPLEV.get(shop):
            # WAIT FOR THE FADES. The first version pressed UP three times and
            # tested `phase == 7` after each one, and read "entered nothing"
            # while the game was in g02 at the right level: `pr07` calls
            # `newlev`, and the change goes g01 -> g02 -> g03 -> g04 -> g05 ->
            # g06 -> g07 over about ninety frames. A press that has been
            # accepted and a press that has been ignored look identical one
            # frame later, and the honest reading of `curlev` already matching
            # `shoplev` is that the door OPENED.
            # 24 frames at a time, not 8: the fade is about ninety frames and
            # each poll is a socket round trip.
            for _ in range(8):
                if rec.remaining(max_frames) <= 24:
                    break
                rec.step((), 24)
                img = rec.emu.work_ram()
                if ram.f("phase").get(img) == ram.PHASE_SHOP:
                    break
            note.append(f"press {i + 1}: curlev reached "
                        f"${lev:02X} = shoplev[{shop}]; after the fades, "
                        f"{_brief(img)}")
            break
    else:
        note.append(f"three UP presses at x={x} entered nothing: {_brief(img)}")
    return "; ".join(note)


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
    """In the pub: move to the tankard and press A ONCE, and record the gold.

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
                f"gold {ram.f('wealth').get(img)} (recorded, NOT asserted: "
                f"`tickmana` re-stores `wealth` from `manatop` every second "
                f"while `ninflag` is set, so a price is refunded inside 60 "
                f"frames -- CLAIMS['ninflag_pins_the_character'])")
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


def talked_to():
    """Somebody in the street is now set to "always ignore". Nothing else does.

    This replaces a predicate the first version of the ladder had, "some live
    object's `obchr` CHANGED", and the reason is the most useful thing this
    session's own run found: **it passed, and it was wrong.** Seed 1002 was
    scored a success for walking into an adventurer at 70 pixels and pressing
    UP once, and it had not talked to anybody.

    Three separate things write `obchr`, and only one of them is a conversation:

      * `actob` (x6.pds:516-539) sets the whole byte from `obchars[type]` when
        the object is CREATED -- so merely walking into the adventurer's
        trigger changes `obchr`;
      * `!i5` (x4.pds:408-412) decrements bits 0-1, the interaction COUNT, on
        every ignored press -- so pressing UP at all changes `obchr`;
      * `!i61` (x4.pds:373-374) is the only writer of bits 2-3 during a talk:
        `eor #%00001100` on a control of 2 leaves control of 1, which the game's
        own comment calls "always ignore".

    `obchars` for the town's four talkable types is
    `oc_phyy, oc_sery, oc_beydy, oc_inter, oc_cN` composed by MISC.SRC's `c`
    macro as `@1*$80 + @2*$40 + @3*$10 + @4*$4 + @5`, so bits 2-3 are `oc_inter`
    = 2 at creation and can only become 1 by the talk path. So the assertion is
    bits 2-3 == 1 on a slot that is alive, which is a function of one RAM byte
    and cannot be satisfied by arriving or by pressing.
    """
    def ok(image: bytes) -> bool:
        live = npc_slots(image)
        for i in live:
            if (obchr_of(image, i) >> 2) & 0x03 == 1:
                return True
        return False
    ok.__doc__ = ("a LIVE object slot has obchr bits 2-3 == 1, the "
                  "'always ignore' state that only `!i61`'s "
                  "`eor #%00001100` produces (x4.pds:373-374). Activation sets "
                  "it to 2 from `obchars` and `!i5` only touches bits 0-1, so "
                  "neither arriving at the person nor pressing UP can pass this")
    return ok


def shop_pred(number: int):
    """Phase 7, `shopdat` n, AND the level `pr07` would have sent us to.

    All three, because all three are things the source says and each one rules
    out a different mistake: phase alone would accept any of the seven; phase
    and `shopdat` together would accept a shop reached some other way; and
    `curlev` is the one number the game computed from the door's own data byte,
    so agreeing with `shoplev[n]` is what says the scout walked to the door it
    meant to walk to.
    """
    lev = SHOPLEV.get(number)

    def ok(image: bytes) -> bool:
        return (ram.f("phase").get(image) == ram.PHASE_SHOP
                and ram.f("shopdat").get(image) == number
                and (lev is None or ram.f("curlev").get(image) == lev))
    ok.__doc__ = (f"phase 7 (g07) AND shopdat == {number} "
                  f"({TOWN_SHOPS[number][0]}) AND curlev == "
                  + (f"${lev:02X}" if lev is not None else "?")
                  + " (`shoplev`, PROBS.SRC:520 -- shopdat is not the level)")
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


# What a new character has, and why neither number can be a predicate.
#
# `initvars` (x1.pds:32-35) is
#     lda #50 / sta manacur / sta manatop / asl a / sta wealth
# so gold starts at 100 and mana at 50. Both were MEASURED on Beta 1 and both
# are then overwritten: `wealth` is 100 at frame 81 and 50 by frame 155, and it
# STAYS 50 because `tickmana`'s `!a` branch re-stores it from `manatop` every
# second while `ninflag` is set. `tickmana` is why this file has no gold
# predicate and no mana-regeneration predicate; see
# CLAIMS['ninflag_pins_the_character'].
START_GOLD = 100
START_MANA = 50
MANA_TOP_PINNED = True


def invop_changed(before: bytes):
    """Something was added to the inventory. The purchase assertion.

    NOT "gold went down", which is what this predicate said first and which
    `ninflag` makes meaningless: `tickmana`'s `!a` branch does `sta wealth`
    from `manatop` once a second, so a price is refunded inside sixty frames
    and a segment that waited for the gold to stay down would wait forever.
    `invop` is nine two-bit counters (x0.pds:566) and `addinv` is the only
    thing that raises one, so "the inventory changed" is a purchase and is
    durable for the rest of the run.
    """
    was = ram.f("invop").get(before)

    def ok(image: bytes) -> bool:
        return ram.f("invop").get(image) != was
    ok.__doc__ = (f"the nine-byte `invop` counter block differs from ${was:04X} "
                  "-- `addinv` is the only writer that raises a count, so this "
                  "is a purchase. NOT gold: `tickmana`'s `!a` branch re-stores "
                  "`wealth` from `manatop` once a second when `ninflag` is set")
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
          talked_to(), tries=14, max_frames=700, first_success=False,
          accept_after=14,
          why="find an object in the street, face it, press UP the way "
              "`standing:` reads a talk (x4.pds:305-400). Searched as a SURVEY: "
              "which of the four directions finds somebody is not in the "
              "source, because the town places its objects by SCREEN position "
              "(PROBDAT.SRC:96-119) and not by map position. The success test "
              "is the NPC being set to 'always ignore', which only the talk "
              "path writes -- the first version asserted that obchr CHANGED and "
              "was scored a success for arriving next to an adventurer, which "
              "is three writers and one conversation. GOLD IS MEASURED IN THE "
              "NOTE, not asserted: see "
              "CLAIMS['talking_pays_500'] -- the source has no path that adds "
              "500 to `wealth` at all")

    # ---- rung 3: an unmarked shop ----------------------------------------
    r.add("shop_door",
          lambda s=1: (lambda emu, rec, rng, mf: p_enter_shop(
              rng, rec, mf, shop=s)),
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
          invop_changed, tries=10, max_frames=500, first_success=False,
          prepare=True,
          # This `why` made two claims that were both wrong.
          #
          # "Success is gold BELOW 100" cannot hold, for two independent
          # reasons: `initvars` sets gold to 100 on a new game, so "below 100"
          # tests only that money was spent and not that anything was bought;
          # and `tickmana`'s `!a` branch re-stores `wealth` from `manatop` once a
          # second while `ninflag` is set, so a price is refunded inside sixty
          # frames and a segment waiting for the gold to STAY down waits forever
          # (CLAIMS['ninflag_pins_the_character']). What is asserted instead is
          # `invop` CHANGING, which is what `addinv` does and nothing else does.
          #
          # "A is yes" is wrong, and reading the source settles it against the
          # comment rather than the other way round. See A_IS_YES below.
          why="A on a bought icon, then A AGAIN to buy. A is NOT 'yes' in a shop "
              "-- see A_IS_YES, which settles it from `waityn`/`syesno`/`sbuy`. "
              "Success is `invop` "
              "CHANGING, not gold moving: `addinv` is the only writer that raises "
              "a count, and gold cannot be used because `ninflag` refunds it "
              "inside a second. WHICH item came in is read in the note, and where "
              "the shop's own icon script names one it is asserted with "
              "`ram.carried_pred`")

    r.add("leave_shop",
          lambda: (lambda emu, rec, rng, mf: p_leave_shop(rng, rec, mf)),
          leaving_shop(), tries=6, max_frames=400, first_success=False,
          why="icon 7 is always EXIT -- `isa` writes it after copying icons 0..6 "
              "(x5.pds:684-686) -- and `sexit` does `newlev` back to `oldlev`. "
              "g07 reads `dfirea` and nothing else, so SELECT does nothing here")

    # ---- rung 4: the priest ----------------------------------------------
    r.add("church_door",
          lambda s=4: (lambda emu, rec, rng, mf: p_enter_shop(
              rng, rec, mf, shop=s)),
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
          lambda s=2: (lambda emu, rec, rng, mf: p_enter_shop(
              rng, rec, mf, shop=s)),
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
          lambda s=0: (lambda emu, rec, rng, mf: p_enter_shop(
              rng, rec, mf, shop=s)),
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
          holds(ram.pred("second", "ne", 0)), tries=1, max_frames=1300,
          first_success=False,
          why="stand still for twenty seconds with NOTHING pressed, which is "
              "the measurement the guide's numbers are about and the only thing "
              "a player never does. The success test is that the one-second "
              "timer is still running -- because that is the ONLY honest test "
              "available here. It used to be `mclock ne 0`, which would have "
              "been a better measurement and cannot hold: `mclock` is written "
              "only by the branch `tickmana` SKIPS when `ninflag` is set, and "
              "Beta 1 sets `ninflag` in `initvars` (see "
              "CLAIMS['ninflag_pins_the_character']). So the number this "
              "segment produces is a NEGATIVE one, and the note carries the "
              "three bytes that say so: manacur before and after, `mclock`, and "
              "`ninflag`")

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