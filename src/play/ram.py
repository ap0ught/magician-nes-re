"""Named RAM for The Magician (NES), decoded from OUR OWN symbols.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
No other module in src/play/ may contain a RAM address. If it is not a `Field`
here, with a name and a comment saying what the byte means and where that
meaning came from, it does not go in an action.

The rule is not tidiness. This project has three recorded cases of an address
being asserted on without ever being read:

  * `$0496` is `levsav` -- per-level saved state, 2 bytes -- and was written down
    somewhere as "HP". It is not HP and there is no HP variable in this game in
    the sense that word usually means.
  * `$005A` is `food`, and the TAS author's subtitles imply a 60-frame timer.
    Both cannot be the same byte, and the one in the symbol file is the one the
    assembler derived from the source.
  * `maxob` is 4, so `pi` -- the PLAYER's object index -- is 3. `mag.sym` also
    has a `pi` at `$001C`, from SUBGAMES.SRC, meaning "pointer index". An action
    that indexed the player's position with the wrong `pi` would read a valid
    byte of a valid array and get another object's coordinates, and every
    assertion about "where the player is" would then be about something else.

Addresses here therefore come from `asm/out/mag.sym` (via the committed slice
`zpmap.txt`, which `tools/genzpmap.py --check` keeps in step) and each is
cross-checked at import time. A name that is not in the symbol file cannot be
added here without either fixing the assembly or admitting a guess.

WHERE THE MEANINGS COME FROM
----------------------------
Every comment below cites the file and line of Eurocom's source that defines or
uses the name. The decoded text is `pds-text/x*.pds` (produced by
tools/pds_extract.py from the binary PDS containers) and `vendor/Magician-NES/
*.SRC` where the module shipped as text. Two of the readings below were
contradicted by the source's own comments and settled by MEASUREMENT instead; the
measurement is named in the comment, because a comment that quietly keeps the
wrong name is worse than no comment.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
from dataclasses import dataclass

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]

ZP_MAP = HERE / "zpmap.txt"
MAG_SYM = ROOT / "asm" / "out" / "mag.sym"

# ----------------------------------------------------------- the symbol table
_SYMBOLS: dict[str, int] = {}
_SYMBOL_SOURCE = ""


def _load(path: pathlib.Path, source: str) -> None:
    global _SYMBOL_SOURCE
    _SYMBOL_SOURCE = source
    for line in path.read_text(encoding="latin-1").splitlines():
        if line.startswith("#"):
            continue
        m = re.match(r"^(\S+)\s*=\s*\$([0-9A-Fa-f]{1,4})\s*$", line)
        if m:
            _SYMBOLS.setdefault(m.group(1), int(m.group(2), 16))


if ZP_MAP.exists():
    _load(ZP_MAP, "src/play/zpmap.txt")
elif MAG_SYM.exists():
    _load(MAG_SYM, "asm/out/mag.sym")
else:
    raise FileNotFoundError(
        "no symbol table. src/play/zpmap.txt is committed and should be here; "
        "if it is missing, regenerate it with `python3 tools/genzpmap.py` after "
        "`make assemble`.")


def sym(name: str) -> int:
    """The address the assembler gave this name. Raises if it has no such name."""
    try:
        return _SYMBOLS[name]
    except KeyError:
        raise KeyError(
            f"{name!r} is not in {_SYMBOL_SOURCE}. An address that is not in the "
            "symbol table is not usable: it is either a typo or a guess, and "
            "both look identical once they are in a predicate."
        ) from None


def plrx(image: bytes) -> int:
    """The player's 16-bit map X. Combined from the two separate arrays."""
    return _FIELDS["plrxlo"].get(image) + 256 * _FIELDS["plrxhi"].get(image)


def plry(image: bytes) -> int:
    """The player's 16-bit map Y."""
    return _FIELDS["prylo"].get(image) + 256 * _FIELDS["pryhi"].get(image)


def object_slot(image: bytes, i: int) -> dict:
    """One object slot's readable state, by slot number."""
    out = {}
    for nm, base in (("obtyp", 0x04F4), ("obmod", 0x04F8), ("obstat", 0x04FC),
                     ("obxl", 0x0514), ("obxh", 0x0518), ("obyl", 0x051C),
                     ("obyh", 0x0520), ("obhel", 0x0524), ("obven", 0x0528),
                     ("obchr", 0x0530), ("obint", 0x0534)):
        out[nm] = image[base + i]
    out["x"] = out["obxl"] + 256 * out["obxh"]
    out["y"] = out["obyl"] + 256 * out["obyh"]
    out["active"] = out["obtyp"] != 0xFF
    return out


def sym_name(addr: int) -> list[str]:
    """Every name the symbol table gives this address."""
    return sorted(n for n, a in _SYMBOLS.items() if a == addr)


# ------------------------------------------------------------------- fields
@dataclass(frozen=True)
class Field:
    """One named RAM location: an address, a width, and what it means.

    `comment` is not documentation, it is the thing the contract requires. A
    field whose comment is empty cannot be created (see `field()`), because the
    alternative is an address in an action with nothing saying what it is.
    """
    name: str
    addr: int
    length: int
    comment: str
    syms: tuple[str, ...] = ()
    offset: int = 0

    def get(self, image: bytes) -> int:
        """The value, LITTLE-ENDIAN.

        Little-endian because that is what the 6502 does and therefore what the
        source's own comments describe. `manacur` is declared
        `zp manacur,2` (x0.pds:414) and set with `lda #50 / sta manacur`
        (x1.pds:48-49); on screen $0047 is $32 and $0048 is $00, so the value is
        50 and NOT 0x3200. Reading it big-endian reports 12800 for a character
        who has 50 mana, which is exactly the kind of plausible wrong number
        this file exists to stop.
        """
        lo = self.addr + self.length - 1
        if lo >= len(image):
            raise IndexError(
                f"{self.name} is ${self.addr:04X}+{self.length}, past the "
                f"{len(image)}-byte image given to it")
        v = 0
        for i in range(self.length):
            v |= image[self.addr + i] << (8 * i)
        return v

    def byte(self, image: bytes) -> int:
        return image[self.addr]

    def hex(self, image: bytes) -> str:
        return image[self.addr:self.addr + self.length].hex()

    def pred(self, op: str, value: int) -> "Pred":
        return Pred(self, op, value)


_FIELDS: dict[str, Field] = {}
_ORDER: list[Field] = []


def field(name: str, length: int, comment: str, *symbols: str,
          offset: int = 0) -> Field:
    """Declare a field. At least one symbol name must exist in the symbol table.

    `symbols` are the names this address is known by in mag.sym; at least one
    must resolve, which is the cross-check that keeps this file honest.

    `offset` is for an address that is a documented OFFSET from a named
    location -- the player's slot in `obxl`, or one byte of `joykey`'s eight
    button bytes. The base symbol must still exist and the offset is recorded,
    so `self_check()` still proves the address came out of the symbol table
    rather than out of a comment.
    """
    if not comment.strip():
        raise ValueError(f"field {name!r} has no comment saying what it means")
    if length < 1 or length > 128:
        # The upper bound is 128 because `eflags` IS 128 bytes (`zp eflags,
        # maxe/2` with `maxe equ $100`, x0.pds:243 + 566), so the guard used to
        # be 16 and it refused a field the assembler had named. It is still a
        # sanity bound rather than a real limit: the whole of the game's RAM is
        # $0000-$07FF, and `field()` separately refuses anything that runs past
        # it.
        raise ValueError(f"field {name!r} has length {length}")
    if not symbols:
        raise ValueError(
            f"field {name!r} names no symbol. Every address here must be one the "
            "assembler gave a name to; a bare address is a guess.")
    resolved = [sym(s) for s in symbols]
    f = Field(name, resolved[0] + offset, length, comment, tuple(symbols), offset)
    _FIELDS[name] = f
    _ORDER.append(f)
    return f


def f(name: str) -> Field:
    try:
        return _FIELDS[name]
    except KeyError:
        raise KeyError(f"no RAM field named {name!r}; declared: "
                       + ", ".join(sorted(_FIELDS))) from None


def all_fields() -> list[Field]:
    return list(_ORDER)


def decode(image: bytes) -> dict[str, int]:
    """Every field's value in a $0000-$07FF image."""
    return {x.name: x.get(image) for x in _ORDER}


# ============================================================== the game state
# ------------------------------------------------------------------ the loop
# `x5.pds:228-231`:  lda gvl,x / sta t0 / lda gvh,x / sta t1 / jsr jmpt0
# `MISC.SRC:891-900`: gvl/dl g00..g06, g07 shops, g08 inv, g09 spell, g0a map
# so `phase` selects one of eleven main-loop routines. The comment on each of
# the PHASE_* constants below is that routine's name in MISC.SRC.
field("phase", 1, "main game phase; indexes gvl/gvh in MISC.SRC:891-900, so "
      "0..10 selects g00..g0a. Read by the IRQ main loop at x5.pds:228.", "phase", "l4c")
field("newphase", 1, "phase the game is about to move to; g06 copies it into "
      "phase and exits (x5.pds:573-575)", "newphase", "tok3")
field("subphase", 1, "current subgame, 0..5 (x0.pds:508-509). Not the main "
      "phase", "subphase", "printflag")
field("nmiflag", 1, "the IRQ main loop's re-entry guard: `lda nmiflag / bne "
      "bankexit` (x5.pds:222-223), set before the level's work and cleared after. "
      "1 that never returns to 0 means the loop is stuck INSIDE a phase, which is "
      "how the rebuild's g03 hang was identified. CAUTION: $002C is ALSO joykey's "
      "decoded UP-button byte (jt[4]) -- the main loop writes nmiflag, then calls "
      "joykey, then writes nmiflag again (x5.pds:221-259) -- so it is only "
      "meaningful BETWEEN frames, which is where this harness reads it.",
      "nmiflag")
field("bnksel", 1, "the PRG bank currently selected at $8000 (x0.pds:401). The IRQ "
      "main loop's epilogue restores it to bank 6 (x5.pds:246-252), so a value "
      "other than 6 with the game otherwise idle is a symptom of a phase that "
      "never returned -- which is what the rebuild's g03 hang looked like",
      "bnksel")
field("fadevec", 1, "colour-fade direction, -1/0/+1; 0 = no fade in progress. "
      "`waitbut` spins on `lda fadevec / bne waitbut` (x0.pds:745-746), so it is "
      "the title screen's own 'ready' flag", "fadevec")
field("fade_del", 1, "colour-fade frame counter (x0.pds:403)", "fadedel")
field("second", 1, "frames until the next one-second tick: the main loop does "
      "`dec second / bpl !c / lda #$3b / sta second` (x5.pds:225-227). Non-zero "
      "means the main loop is RUNNING, which is the difference between a live "
      "game and one wedged in a phase", "second")

# -------------------------------------------------------------- where we are
field("curlev", 1, "logical level index of the level being played, 0..$FF. "
      "convind() splits it into main level (high nibble) and sub level (low "
      "nibble) -- x6.pds:912-940", "curlev")
field("oldlev", 1, "level being left, and the level newlev() stages (x6.pds:888-895)",
      "oldlev")
field("curlevind", 1, "physical level index produced by convind() (x6.pds:924-940)",
      "curlevind", "rightarrow")
field("lastgame", 1, "last level entered from the title screen; 0xFF means none "
      "(x1.pds:24, x6.pds:634)", "lastgame")
field("mapind", 1, "current map index, 1..A -- which floor of the tower. initvars "
      "sets it to stlev ($01) on a new game (x1.pds:27-29, x5.pds:8)", "mapind")
field("hilev", 1, "highest level reached so far (x0.pds:544)", "hilev")
field("xsize", 1, "map width in tiles (x0.pds:512 area; see getlev)", "xsize")
field("ysize", 1, "map height in tiles", "ysize")
field("mapx", 2, "16-bit map X origin: the on-screen column the view starts at "
      "(x0.pds:395 area, x1.pds:695)", "mapx")
field("mapy", 2, "16-bit map Y origin: the on-screen row the view starts at", "mapy")
field("scnxv", 1, "screen X scroll vector, -1/0/+1: whether the view must move "
      "horizontally this frame (x0.pds:449-451, written at x1.pds:698)",
      "scnxv")
field("scnyv", 1, "screen Y scroll vector, -1/0/+1 (x1.pds:705)", "scnyv")
field("svecx", 1, "x scroll vector as an INDEX, 0..2, distinct from `scnxv` "
      "which is a signed direction (x0.pdes:448, x1.pds:269)", "svecx")
field("svecy", 1, "y scroll vector as an index, 0..2 (x0.pdes:449)", "svecy")
field("doorflg", 1, "hidden doors enabled; cleared by clrall() (x5.pds:545-547)",
      "doorflg")
field("perflag", 1, "permanent game flags -- events that stay done (x0.pds:415)", "perflag")
field("tmpflag", 1, "temporary game flags, reset per level (x0.pds:416)", "tmpflag")
field("tmpflag1", 1, "a second byte of temporary flags, distinct from tmpflag "
      "(x0.pds:421). Its bit names are not written down in the recovered source, "
      "so nothing in this file asserts on it", "tmpflag1")
field("valsav", 2, "the NEW value of manacur or wealth, computed before it is "
      "moved into place. `chkmana` (x7.pds:198-205) does manacur minus the "
      "requested cost into valsav and `upmana` then copies it back, so while "
      "the spell screen is charging for a rune `manacur - valsav` IS the cost "
      "the game just computed -- which is how the spell cost gets MEASURED "
      "instead of argued about", "valsav")
field("mclock", 1, "mana regain timer. `tickmana` (x7.pds:244-254) runs only on "
      "the one-second tick (`ora second / bne`) and does `dec mclock / bpl "
      "tickfw` before `inc manacur`, so mclock's own period sets the regen rate. "
      "It is not in `initvars`, so its starting value is a measurement, not "
      "something this file claims", "mclock")
field("fooddel", 1, "food drain timer, one per second tick (x7.pds:264-273); "
      "`dec fooddel,x / bpl` then reload from `fwdels`, so fooddel's period is "
      "how fast `food` falls", "fooddel")
field("waterdel", 1, "water drain timer, same shape as fooddel, x[1] of that "
      "loop (x7.pds:264-273)", "waterdel")
field("uflg", 1, "0 = interaction is allowed right now, non-zero = inhibited. "
      "`faced`/`faceu` set it (PROBS.SRC) and the player handler's "
      "interaction path starts `ldy uflg / bne !c0` (x4.pds:310-311), so this "
      "byte says whether the game is LISTENING for a talk this frame", "uflg")
field("dflg", 1, "0 = searching is allowed, non-zero = inhibited; the twin of "
      "uflg for the DOWN direction (x4.pds:274-276 `!ser`)", "dflg")
field("intflg", 1, "set once a talk has been attempted and cleared when UP is "
      "released, so 'only test once till released from up' (x4.pds:313-315). "
      "Non-zero means a talk was already offered on this press", "intflg")
field("serflg", 1, "the same one-shot guard for searching a body (x4.pds:275)",
      "serflg")
field("intmsg", 1, "which line of a multi-line conversation the wise man or the "
      "tree is on (x0.pds:591, x4.pds:378-395)", "intmsg")
field("begmsg", 1, "which line the beggar is on (x0.pds:592, x4.pds:854-859)",
      "begmsg")
field("panph", 1, "panel phase: 0 = printing, non-zero = scrolling the panel "
      "text up. `emptypan` branches on it (x7.pds:117-118)", "panph")
field("pantyp", 1, "the panel message TYPE: the high bit selects compressed "
      "vs uncompressed and bits 5-6 the message base ($0C interaction, $0F "
      "misc, ...); `addmsg` sets it and `emptypan` tests bit 7 "
      "(x7.pds:126-133, 141)", "pantyp")
field("eflags", 128, "the main-level EVENT flags, 4 bits each, 256 events. "
      "`flag1`/`get4`/`set4` (x7.pds:403-431) index it with the event number the "
      "trigger carried, which is how the game remembers 'this message has been "
      "shown' and 'this chest has been taken' for the current level", "eflags")
field("pulind", 1, "which of the eight spell-screen colour-pulse patterns is "
      "showing (x6.pds:378-390)", "pulind")
field("puldel", 1, "frames between spell-screen colour pulses (x6.pds:376-378)",
      "puldel")


# ------------------------------------------------------- the game's quest flags
# Eurocom named the first town's quests, and the names are in the recovered
# source rather than in a guide:
#
#     pds-text/x0.pds:252-259   ;Temporary flags reset each game
#       drink  equ $01   ;1=plr has bought drink in pub
#       asked  equ $02   ;1=vicar has asked plr to deliver letter
#       flask  equ $04   ;1=vicar given plr flask of holy water
#       pool   equ $08   ;1=holy water dropped in pool
#       bless2 equ $10   ;1=second vicar blessed plr
#       bonus  equ $20   ;1=plr collected subgame bonus
#       twin   equ $40   ;1=twin spell cast
#       fount  equ $80   ;1=plr entered fountain
#     pds-text/x0.pds:262-264   ;Permanent flags saved in password
#       gotlet  equ $01  ;1=got vicar's letter
#       sentlet equ $02  ;1=posted vicar's letter
#       ringana equ $04  ;1=ring of ana used
#       amsheeld equ $08 ;1=amulet of sheeld used
#       ammor   equ $10  ;1=amulet of mor used
#
# THE MASKS ARE NOT ADDRESSES, and that is the whole reason this is an API and
# not a table of fields. The assembler emitted each `equ $01` into mag.sym as a
# symbol whose VALUE is 1, so `sym("drink")` is 1 -- and $0001 is a real byte of
# zero page holding the joypad accumulator `jt`. A `field()` per flag would have
# declared thirteen bytes at $0001-$0010 and every assertion made against them
# would have been about the joystick and the power-rune constants. So the mask
# is resolved from the symbol table, checked to be a single bit, and tested
# against the byte the source says holds it.
#
# Which byte holds them is not a guess either: `sset`/`sclr`/`stst`
# (x5.pds:783-792) all `ora`/`and`/`bit tmpflag`, and `once` (x2.pds:445-446)
# does the same to `perflag`. The shop script language's `set`/`clr`/`tst`/
# `setp`/`clrp` (SHOPDAT.SRC:68-77) are those same routines, so `drink` and
# `asked` are set by shop scripts and `gotlet`/`sentlet` by the permanent twin.
QUEST_FLAGS: dict[str, tuple[str, str]] = {
    "drink": ("tmpflag", "the player has bought a drink in the pub. Set by the "
                        "pub's tankard icon, `set,drink` (SHOPDAT.SRC:83)"),
    "asked": ("tmpflag", "the vicar has asked the player to deliver the letter. "
                         "The church's `set,asked` (SHOPDAT.SRC:113)"),
    "flask": ("tmpflag", "the vicar has given the player a flask of holy water "
                         "(SHOPDAT.SRC:115)"),
    "pool": ("tmpflag", "the holy water has been dropped in the pool; `pcoa` "
                        "`bit tmpflag` tests it (x5.pds:522-524)"),
    "bless2": ("tmpflag", "the second vicar has blessed the player"),
    "bonus": ("tmpflag", "the player has collected the subgame bonus"),
    "twin": ("tmpflag", "the twin spell has been cast"),
    "fount": ("tmpflag", "the player has entered the fountain"),
    "gotlet": ("perflag", "the player is carrying the vicar's letter; the post "
                          "office's `tst,gotlet` (SHOPDAT.SRC:104)"),
    "sentlet": ("perflag", "the letter has been posted; the post office's "
                           "`setp,sentlet` (SHOPDAT.SRC:105)"),
    "ringana": ("perflag", "the ring of ana has been used, once only"),
    "amsheeld": ("perflag", "the amulet of sheeld has been used, once only"),
    "ammor": ("perflag", "the amulet of mor has been used, once only"),
}


def flag_mask(name: str) -> int:
    """The bit this quest flag occupies, resolved from the symbol table.

    Raises for a name the assembler never emitted, and for a value that is not
    a single bit. Both refusals are load-bearing: a mask of 0 would make
    `flag_pred(..., True)` vacuously true, and a mask above $80 would mean the
    symbol we matched is an address that happens to share the name.
    """
    if name not in QUEST_FLAGS:
        raise KeyError(
            f"{name!r} is not one of the quest flags the source names: "
            + ", ".join(sorted(QUEST_FLAGS)))
    mask = sym(name)
    if mask < 1 or (mask & (mask - 1)) != 0:
        raise ValueError(
            f"quest flag {name!r} resolved to {mask:#x}, which is not a single "
            "bit. `x0.pds:252-264` writes these as bit masks, and a mask of two "
            "or more bits would silently mean two different flags at once.")
    return mask


def flag_host(name: str) -> Field:
    """The byte this quest flag lives in."""
    if name not in QUEST_FLAGS:
        raise KeyError(f"{name!r} is not a quest flag the source names: "
                       + ", ".join(sorted(QUEST_FLAGS)))
    return f(QUEST_FLAGS[name][0])


def flag_set(name: str, image: bytes) -> bool:
    return bool(f(QUEST_FLAGS[name][0]).get(image) & flag_mask(name))


def flag_pred(name: str, want: bool = True) -> Pred:
    """A predicate on the quest flag -- one address, so the bridge can take it."""
    return Pred(flag_host(name), "bne" if want else "bclr", flag_mask(name))


# ------------------------------------------------------- carried item counters
# `addinv` (x7.pds:442-451) is `get2` -> `cmp #$03 / bcs` -> `set2`, so a carried
# item is a 2-BIT count, four per byte, and the game's own cap is three. That is
# the walkthrough's "you can carry 3 of every type" as a fact from the source
# rather than a claim from a guide, and it is also the reason an item predicate
# needs a mask rather than an equality: a count of 1, 2 or 3 all mean "carrying
# some of it", and only 0 means not.
#
# The indices are the source's own object numbers (x2.pds:346-355 comments, and
# MISC.SRC's `obtxt` is the string table the inventory screen reads at the same
# index). test_play_ram.py check 20c proves each index against that table, so a
# shifted number here cannot pass.
ITEMS: dict[str, int] = {
    "water_flask": 0x00,
    "bread": 0x01,
    "chicken": 0x02,
    "ham": 0x03,
    "vegetables": 0x04,
    "pouch_of_coins": 0x05,
    "ultimate_potion": 0x11,
    "key": 0x12,
    "holy_water": 0x13,
    "magic_charm": 0x14,
    "sunglasses": 0x1A,
    "walking_stick": 0x1B,
    "letter": 0x1C,
    "rune_stone": 0x1D,
}
ITEM_CAP = 3            # `cmp #$03 / bcs` -- addinv's own refusal


def item_bits(ob: int) -> int:
    """Item `ob`'s low bit, counted from the start of the whole `invop` field.

    Absolute, not byte-relative: `invop` is nine bytes read as one little-endian
    integer, so item 4's pair is at bits 8-9 and not at bits 0-1. Getting this
    wrong is invisible for the first four items and wrong for every other one,
    which is why src/testing/test_play_ram.py check 20 fills the rest of the
    byte with noise.
    """
    if not 0x00 <= ob < 0x24:
        raise ValueError(
            f"object ${ob:02X} is outside the 00..23 range `invop` covers. The "
            "range above it is the SCROLL table (x7.pds:476-481 `cmp #$24 / "
            "bcs`), which is `invsc` and holds 1-BIT flags, not counts -- so a "
            "scroll's carried-ness is a different question with a different "
            "answer and is not answered by this function.")
    return 8 * (ob // 4) + 2 * (ob % 4)


def item_mask(ob: int, bits: int = 0b11) -> int:
    """The bits of `invop` that hold item `ob`'s count."""
    return (bits & 0x03) << item_bits(ob)


def carried(ob: int, image: bytes) -> int:
    """How many of item `ob` the player is carrying, 0..3."""
    shift = item_bits(ob)
    return (f("invop").get(image) >> shift) & 0x03


def carried_pred(ob: int, at_least: int = 1) -> Pred:
    """A single-address predicate: "at least `at_least` of item `ob`".

    Single address on purpose. The bridge evaluates one address per predicate,
    so "this item's bits in this byte are non-zero" is expressible and "the
    count is exactly 2" is not -- for that, read the field and compare.
    """
    if at_least < 1:
        raise ValueError(f"at_least={at_least}; 0 items is 'not carried', which "
                         "is the mask being clear, not a count question")
    if at_least == 1:
        return Pred(f("invop"), "bne", item_mask(ob))
    if at_least == 2:
        # 2 is 10 and 3 is 11: the low bit is set in 3 only, the high bit in
        # both, so "the high bit, or the low bit" is everything from 2 up and
        # still excludes 1.
        return Pred(f("invop"), "bne", item_mask(ob, 0b01) | item_mask(ob, 0b10))
    return Pred(f("invop"), "band", item_mask(ob))

# ------------------------------------------------------------------ the player
# `x0.pds:242-243`: `maxob equ $04` / `pi equ maxob-1`, so the PLAYER is object
# index 3. mag.sym ALSO has `pi = $001C`, which is SUBGAMES.SRC's "pointer
# index" and has nothing to do with this. Every object array below is
# `zp <name>,maxob` -- four bytes, one per object -- so the player is at
# <base>+3. This is the single most load-bearing offset in the file.
PLAYER_IDX = 3

# The whole of the game's RAM: `x0.pds:522` says "** Unexpanded RAM $0000-$07FF
# **", and this harness fingerprints exactly this window. It lives HERE and not
# in emu.py because an address range in code is an address in code, and the rule
# this project set is that there are none outside this file.
WORK_RAM = (0x0000, 0x0800)
field("plrtype", 1, f"obtyp[{PLAYER_IDX}] -- the player's object type. obtyp is "
      f"'type/active flag', 4 bytes, one per object (x0.pds:542); player index "
      f"{PLAYER_IDX} from `pi equ maxob-1` (x0.pds:243)", "obtyp",
      offset=PLAYER_IDX)
field("plrmode", 1, f"obmod[{PLAYER_IDX}] -- the player's movement mode "
      "(x0.pds:543)", "obmod", offset=PLAYER_IDX)
field("plrstat", 1, f"obstat[{PLAYER_IDX}] -- 'anim/movement/dead/general flag'; "
      f"bit 7 is DEAD, bits 2-3 are the facing, and bit 6 is 'in the air' "
      f"(x1.pds:1 uses it as: bmi = dead, bvc = air, lsr/lsr + bcs = moving). "
      f"Every claim this harness makes about the player standing still or "
      f"facing a direction rests on this byte", "obstat", offset=PLAYER_IDX)
field("plrflg", 1, "the PLAYER object flag: >0 while walking, <0 while jumping "
      "or falling, 0 standing (x0.pds not named; x1.pds:120-128 'ldy plrflg / "
      "bne', x5.pds:512 'bit plrflg / bvs' for in-air)", "plrflg")
# The two halves of the position are NOT adjacent: `zp obxl,maxob` and
# `zp obxh,maxob` are SEPARATE arrays (x0.pds:548-549), so the player's low byte
# is obxl[3] = $0517 and the high byte is obxh[3] = $051B, four bytes apart. A
# two-byte field cannot span them, so they are declared separately and combined
# by plrx()/plry().
#
# The first version of this file declared `plrx` as `obxl` with no offset and
# length 2, which reads SLOT 0 -- and slot 0 is always $FFFF, because initob
# clears slots 0..2 and activates only slot 3 (`lda #$00 / jsr actob` with X=pi).
# So "the player is at 65535,65535" was not the player being lost, it was the
# harness reading the one slot that is always empty. MEASURED on Beta 1: the
# player's position really does change while walking, and obxl[3]/obyl[3] are
# where it changes.
field("plrxlo", 1, f"obxl[{PLAYER_IDX}] -- the player's map X, low byte",
      "obxl", offset=PLAYER_IDX)
field("plrxhi", 1, f"obxh[{PLAYER_IDX}] -- the player's map X, high byte",
      "obxh", offset=PLAYER_IDX)
field("prylo", 1, f"obyl[{PLAYER_IDX}] -- the player's map Y, low byte",
      "obyl", offset=PLAYER_IDX)
field("pryhi", 1, f"obyh[{PLAYER_IDX}] -- the player's map Y, high byte",
      "obyh", offset=PLAYER_IDX)
field("plrchest", 1, f"obaorg[{PLAYER_IDX}], also called obchest -- the player's "
      f"animation-origin / chest counter (x0.pds:546)", "obchest", offset=PLAYER_IDX)
field("plrhelm", 1, f"obhel[{PLAYER_IDX}] -- the player's health counter. "
      f"`helind` (x6.pds:33) computes 'health rating = health / 32', so it is a "
      f"16x-scale counter, NOT a hit point count and not an HP bar", "obhel", offset=PLAYER_IDX)
field("plrven", 1, f"obven[{PLAYER_IDX}] -- venom counter (x0.pds:551)", "obven", offset=PLAYER_IDX)
field("plrchr", 1, f"obchr[{PLAYER_IDX}] -- object characteristics: which floor "
      f"types pass (x0.pds:552)", "obchr", offset=PLAYER_IDX)
field("plrmsg", 1, f"obint[{PLAYER_IDX}] -- the interaction message attached to "
      f"the player, used by the wise man and the beggar (x0.pds:553, x0.pds:735 "
      f"intmsg/begmsg are the object-side copies)", "obint", offset=PLAYER_IDX)

# ------------------------------------------------------------------- objects
# maxob = 4, so these arrays are 4 bytes each and every index 0..3 is a slot.
# slot 0 is always the player (initob puts the player at pi=3 and zeroes the
# rest); slots 0..2 are the three live NPC/monster slots.
field("obtyp", 4, "object type / active flag, one byte per slot. 0 means the "
      "slot is free (initob clears them with `ror obtyp,x` so the flag lands in "
      "bit 7; x1.pds:112-118). Slot 3 is the player.", "obtyp")
field("obmod", 4, "object movement mode, per slot", "obmod")
field("obstat", 4, "object anim/movement/dead/general flag, per slot", "obstat")
field("obxl", 4, "object 16-bit map X, low byte, per slot", "obxl")
field("obxh", 4, "object 16-bit map X, high byte, per slot", "obxh")
field("obyl", 4, "object 16-bit map Y, low byte, per slot", "obyl")
field("obyh", 4, "object 16-bit map Y, high byte, per slot", "obyh")
field("obhel", 4, "object health, per slot", "obhel")
field("obven", 4, "object venom counter, per slot", "obven")
field("obchr", 4, "object characteristics (passable floor types), per slot", "obchr")
field("obint", 4, "object interaction message, per slot", "obint")
field("obeno", 4, "event number that triggered the object, per slot (x0.pds:552)",
      "obeno")
field("obphysh", 4, "physical shield hits left, per slot (x0.pds:554)", "obphysh")

# ---------------------------------------------------------------- resources
field("wealth", 2, "current gold (x0.pds:413 'current wealth'). A new game sets "
      "it to 100 (x1.pds:52-53)", "wealth")
field("manacur", 2, "current mana (x0.pdes:414 'current mana'); the spell screen "
      "debits it (x6.pds:308-312) and a new game sets it to 50 (x1.pds:48-50)",
      "manacur")
field("manasav", 2, "mana as it was when the spell screen was entered; the spell "
      "screen restores manacur from it (x6.pds:307-310)", "manasav")
field("manatop", 2, "maximum mana (x0.pds:415); a new game sets it to 50 "
      "(x1.pdes:49-50)", "manatop")
field("gametime", 2, "elapsed game time in seconds (x0.pds:416)", "gametime")
field("food", 1, "food level; initvars sets it to $FF, 'maximum' (x1.pds:18-20). "
      "NOTE: the TAS author's subtitles call $005A a 60-frame timer. Both cannot "
      "be this byte; this one is what the source says it is.", "food")
field("water", 1, "water level, $FF = maximum (x1.pds:18-20)", "water")
field("drinks", 1, "drinks left in the flask; a new game sets it to 5 "
      "(x1.pds:22-23)", "drinks", "l1c", "s17")
field("deathtyp", 1, "player death type; bit 7 is 'no death + no fall' "
      "(x1.pds:25-26)", "deathtyp")
field("ninflag", 1, ">0 = infinite mana (x0.pds:539). The password screen sets it "
      "(x0.pds:638)", "ninflag")
field("invhand", 1, "inventory slot in the player's hand; $FF = empty "
      "(x1.pds:13, x6.pds:77-82)", "invhand")
field("invind", 1, "inventory cursor (x6.pds:170-178)", "invind")
field("invop", 9, "object/potion inventory counts: 36 TWO-BIT counters, four per "
      "byte (x0.pds:566 `zp invop,mxin/4` with `mxin equ $24`). It is 9 bytes, "
      "not the 7 an earlier version of this file declared -- the symbol table "
      "settles it, because `invsc` follows at $0707 and $06FE + 9 = $0707. "
      "`addinv`/`delinv`/`tstinv` all read and write it through `get2`/`set2`",
      "invop")
field("invsc", 5, "scroll inventory flags, 5 bytes", "invsc")
field("invsp", 5, "spell book flags, 5 bytes. `invsc`/`invsp` are separate tables "
      "over the same 40 spell slots -- scrolls learned and spells learned "
      "(x0.pds:541)", "invsp")
field("bookcur", 1, "the spell currently on show in the spell screen's top "
      "window (x1.pds:35-36 sets it to $17)", "bookcur")

# --------------------------------------------------------------- spells
field("plrspell", 1, "the spell in the player's hand: low 2 bits are the power "
      "level, the rest is the spell number (x6.pds:296-299 masks and rebuilds it "
      "exactly that way)", "plrspell")
field("cursp", 1, "the spell currently being CAST. Cleared to $FF every frame "
      "the player is not casting (x1.pds:711-712). NOT the spell screen's "
      "`currune` at $00CC -- two different bytes with confusingly similar names, "
      "and the symbol table is what keeps them apart", "cursp")
field("currune", 1, "the rune being entered on the spell screen, 0..F "
      "(x0.pds:496-497)", "currune")
field("curob", 1, "the object currently being used; cleared with cursp "
      "(x1.pds:712-713)", "curob")
field("buildind", 1, "spell-building phase, 0..5 (x0.pdes:497)", "buildind")
field("botbuf", 6, "the spell being WRITTEN on the spell screen, 6 runes "
      "(x0.pds:457, used at x6.pds:305-330)", "botbuf")
field("topbuf", 6, "the spell in the spell screen's top window, 6 runes; a "
      "spell is copied from here into botbuf when a rune is committed "
      "(x6.pds:322-330)", "topbuf")
field("castflag", 1, "set while a spell is being cast (x0.pds:458)", "castflag")
field("topspell", 1, "the spell in the spell screen's top window; $28+ means "
      "empty or unknown (x6.pds:300-302)", "topspell")

# ------------------------------------------------------------------ shops
field("shopdat", 1, "shop/pub/p.o./church flag -- which kind of shop the current "
      "shop is (x0.pds:492)", "shopdat", "inuse", "subgame")
field("shopind", 1, "current shop icon, 0..7 (x0.pds:493; the icon list ijvl/ijvh "
      "has 18 entries but shl/shh/shi are read with x masked to 0..7)",
      "shopind")
field("shoptyp", 1, "shop type, used for the icon X positions (x0.pdes:494)",
      "shoptyp")
field("ynflag", 1, "holds the answer to a shop's A/B question: non-zero means "
      "'waiting for a button' (x0.pdes:495, x5.pds:737)", "ynflag")

# ------------------------------------------------------------------- panels
# The panel is the game's text/message system: the box at the bottom of the
# screen. `pandflg` is the test every "is something being said" question has to
# go through, because the panel is written asynchronously -- a box can be on
# screen with nothing in it yet.
field("pandflg", 1, "panel flag. `emptypan` (x5.pds:594) is the main loop's last "
      "call, and `waitpan` waits for it to clear, so >0 = the panel still has "
      "something to draw or say", "pandflg")
field("panhead", 1, "first panel line to write (x0.pds:499)", "panhead")
field("pantail", 1, "last panel line to write (x0.pds:500)", "pantail")
field("panmsg", 1, "which panel message is being displayed (x0.pdes:462)", "panmsg")
field("lastbas", 1, "last panel message received, as $FF when none (clrall sets "
      "it to $FF; x5.pds:541-544)", "lastbas")
field("lastmsg", 1, "panel's last displayed message; $80 means 'always print' "
      "(x5.pds:746-747)", "lastmsg")
field("help", 1, "help message flag (x5.pds:519)", "help")
field("helmsg", 1, "help message number (x0.pdes:455)", "helmsg")
field("ctrl", 1, "game control flag; cleared when a level is entered "
      "(x6.pds:632). Bit 5 ($20) is 'ignore button A/B' and the player handler "
      "tests it before looking at the fire byte (x1.pds:708-711), so a walk that "
      "sets ctrl would stop the game reading A and B at all", "ctrl")
field("wait", 2, "scratch `wait` used by the main loop's tail (x5.pds:236-245). "
      ">0 means the loop is deliberately spending frames", "wait")

# ------------------------------------------------------------- joypad decode
# THIS IS THE PART OF THE GAME THAT HAD TO BE RE-DERIVED, and it is worth
# reading before trusting any button. Three things are wrong in the obvious
# reading, and the source's own comments are wrong about two of them.
#
# 1. `jt` is TWO BYTES, not eight.
#    `x0.pds:405`: `zp jt,2`. jt[0] ($002E) is a single ACCUMULATOR:
#        jk0:  lda #$01 / sta jt / sta $4016 / lsr a / sta $4016
#              lda $4016 / and #$03 / cmp #$01 / rol jt / bcc !a
#    Eight reads of $4016, each one shifting the accumulator left and inserting
#    one bit at bit 0. The initial $01 is a marker: it walks from bit 0 to bit 7
#    and out, and `bcc` stops the loop the moment it falls off, which is why
#    there are exactly eight reads.
#
# 2. `joykey` explodes that one accumulator into EIGHT BYTES AT $0028-$002F,
#    which is BELOW jt.
#        joykey: lda jk0 ... tax / ldx #$07
#                !a asl a / ldy #$00 / bcc !b / iny / !b sty jt,x / dex / bpl !a
#    `sty jt,x` with x counting 7,6,...,0 addresses $002F,$002E,...,$0028. So the
#    per-button bytes are NOT named in the symbol table except by accident:
#        $0028 A        $0029 B        $002A Select  $002B Start
#        $002C Up       $002D Down      $002E Left    $002F Right
#    and `$002C` is ALSO `nmiflag`, `$002A/$002B` are `stk0`/`stk1`, `$002D` is
#    `bnksel`. The main loop writes nmiflag, calls joykey, and writes nmiflag
#    again (x5.pds:221-259), so $002C carries the Up bit in between. Which is a
#    good reason not to read "is the player alive" out of nmiflag.
#
# 3. $0030-$0035 are NOT buttons. joykey builds two combined flags first:
#        ld x lr / bne !c / ldx lr+1 / beq !c / ldx #$ff / !c stx ud
#        ldx jt / bne !d / ldx jt+1 / beq !d / ldx #$ff / !d stx lr
#    so `$0030` = $FF when A OR B is held, and `$0031` = $FF when Select OR
#    Start is held. The four bytes after them are the individual states of
#    whichever buttons those two groups cover, so:
#        $0032 = Select   $0033 = Start   $0034 = Up   $0035 = Down
#
# 4. The six-byte "DEBOUNCED" block at $0036-$003B is the same six source bytes
#    ($0030-$0035) put through an edge detector:
#        ldx #$05
#        !e lda lr,x / beq !f / cmp oldlr,x / beq !g
#        !f sta oldlr,x / db $2c            (skip the next instruction)
#        !g lda #$00 / sta dlr,x / dex / bpl !e
#    `dlr[x]` is $FF on the frame the value CHANGED -- it is set on a press AND
#    on a release, and is 0 while the value is steady. So these are "this button
#    changed this frame" bytes, not "pressed this frame" bytes.
#
# WHICH BUTTON EACH DEBOUNCED BYTE IS. The source disagrees with itself and this
# is MEASURED, never assumed:
#   * the NAMES say $0038 dsta = START, $0039 dsel = SELECT, $003A dfireb = B,
#     $003B dfirea = A -- `waitbut` (x0.pds:747-748) relies on dsel/dsta being
#     SELECT/START, and g09's comments say dsta commits a spell while dsel leaves
#     the screen;
#   * the CODE ORDER above says $0038 is the Select edge, $0039 the Start edge,
#     $003A the Up edge and $003B the Down edge, because the source names them
#     fireb/firea for what the hardware order calls Up and Down.
#
# Both readings cannot be right, and every reading is self-consistent enough to
# look right. So `select_edge()` and `start_edge()` REFUSE to answer until
# src/play/milestones/m1_first_town.py has pressed one button at a time and read
# the bytes back. That is the only reason they exist.
field("pad", 8, "the eight bytes joykey decodes, $002E-$0035, in ITS ORDER: "
      "index 0 ($002E) = RIGHT, index 1 ($002F) = LEFT, and indices 2 and 3 are "
      "overwritten again by `lr`/`ud` (see lr/ud). The accumulator the source "
      "calls `jt` is $002E, which is ALSO index 0 -- `sta jt` in jk0, then "
      "`sty jt,x` for x=7..0 in joykey, so jt is one byte that is also the first "
      "of eight. MEASURED: one button at a time, held past the two-equal-reads "
      "debounce, then $002E-$003B read back; see src/play/recon.py step 1",
      "jt", offset=0)
field("pad_right", 1, "MEASURED: $002E = 1 while RIGHT is held. jt[0] = bit 0 of "
      "jk0's accumulator = the EIGHTH $4016 read", "jt")
field("pad_left", 1, "MEASURED: $002F = 1 while LEFT is held. jt[1] = bit 1 = the "
      "SEVENTH read", "jt", offset=1)
field("lr", 1, "MEASURED: $FF while LEFT is held, 01 while RIGHT is held, 00 when "
      "neither is. So it is a signed DIRECTION, not 'left or right' -- `getdir` "
      "(x6.pds:951-957) reads its sign to tell them apart. This is joykey's "
      "`stx lr`, computed from $002E/$002F (DISP.SRC:316-322)", "lr")
field("ud", 1, "MEASURED: $FF while UP is held, 01 while DOWN is held, 00 when "
      "neither. The same signed-direction shape as `lr`, from $0030/$0031 which "
      "at that moment still hold jt[2] (DOWN) and jt[3] (UP)", "ud")
field("start_raw", 1, "MEASURED: $0032 = 1 while START is held. jt[4] = bit 4 = "
      "the FOURTH $4016 read. The source calls it `sta`, and it is right", "sta")
field("select_raw", 1, "MEASURED: $0033 = 1 while SELECT is held. jt[5] = bit 5 = "
      "the THIRD read. The source calls it `sel`, and it is right", "sel")
field("fireb_raw", 1, "MEASURED: $0034 = 1 while B is held. jt[6] = bit 6 = the "
      "SECOND read. The source calls it `fireb`, and it is right", "fireb")
field("firea_raw", 1, "MEASURED: $0035 = 1 while A is held. jt[7] = bit 7 = the "
      "FIRST read. The source calls it `firea`, and it is right", "firea")
field("deb_lr", 1, "$0036 -- edge of `lr`: $FF on the frame LEFT/RIGHT changed "
      "state, 0 while it is steady. MEASURED by stepping one frame at a time "
      "across a press", "dlr", "fs")
field("deb_ud", 1, "$0037 -- edge of `ud`, same shape. MEASURED for both UP and "
      "DOWN", "dud")
field("deb_start", 1, "$0038 -- edge of START. MEASURED. The source calls it "
      "`dsta` and it IS the START edge; a reading of joykey that put SELECT here "
      "was wrong because it ignored that the lr/ud step overwrites $0030/$0031",
      "dsta")
field("deb_select", 1, "$0039 -- edge of SELECT. MEASURED. The source calls it "
      "`dsel` and it is right", "dsel")
field("deb_b", 1, "$003A -- edge of B. MEASURED. The source calls it `dfireb`",
      "dfireb")
field("deb_a", 1, "$003B -- edge of A. MEASURED. The source calls it `dfirea`",
      "dfirea")
field("oldlr", 6, "the previous frame's $0030-$0035, six bytes, which is what the "
      "edge detector compares against (DISP.SRC:325-333)", "oldlr", "lb")

# The measured button table, in the shape an action needs: a logical button name
# to the FIELD that carries it. Recorded by ram.record_button_order(), which
# requires the observation that established it -- a button order recorded
# without its evidence is a guess wearing a measurement's clothes.
BUTTON_ORDER: dict[str, str] | None = None

BUTTON_ORDER_EVIDENCE = (
    "MEASURED 2026-10-05 on this machine with BizHawk 2.11.1 / quickerNES, by "
    "src/play/recon.py step 1: each button held alone for 120 frames -- long "
    "enough for joykey's two-consecutive-equal-reads debounce to settle -- and "
    "$002E-$003B read back, with an 8-sample stability check per button (all "
    "eight samples identical for every button). One distinct byte per button, "
    "and it matched the source's OWN names for the six named bytes: "
    "$0032 sta=START, $0033 sel=SELECT, $0034 fireb=B, $0035 firea=A, "
    "$0038 dsta=START edge, $0039 dsel=SELECT edge, $003A dfireb=B edge, "
    "$003B dfirea=A edge. $0036/$0037 are the lr/ud EDGES, shared by Left/Right "
    "and Up/Down respectively, so they do not identify a single button. "
    "$0030 lr is $FF for LEFT and 01 for RIGHT; $0031 ud is $FF for UP and 01 "
    "for DOWN -- signed directions, not ORs. "
    "The two readings that disagreed (the source's names vs. the order joykey "
    "writes the bytes in) were resolved by noticing that joykey OVERWRITES "
    "$0030/$0031 with lr/ud after the decode loop, which is why a naive reading "
    "of the write order puts the wrong button there."
)


def select_edge() -> Field:
    """The debounced byte that carries SELECT. Raises unless recon has run."""
    return _edge("select", "select")


def start_edge() -> Field:
    return _edge("start", "START")


def a_edge() -> Field:
    return _edge("a", "A")


def b_edge() -> Field:
    return _edge("b", "B")


def direction_edge(name: str) -> Field:
    """The edge byte for a direction. Shared: one byte covers both ways."""
    if name in ("Left", "Right"):
        return _edge("lr", "Left/Right")
    if name in ("Up", "Down"):
        return _edge("ud", "Up/Down")
    raise KeyError(f"{name!r} is not a direction")


def _edge(what: str, label: str) -> Field:
    if BUTTON_ORDER is None:
        raise AssertionError(
            f"which debounced byte carries {label} has not been measured. Run "
            "src/play/recon.py, whose step 1 presses one button at a time and "
            "reads $002E-$003B back.")
    return f(BUTTON_ORDER[what])


def record_button_order(order: dict[str, str], evidence: str) -> None:
    """Record the measurement.

    `order` maps a logical name to a FIELD NAME, never an address: the address
    has to come through a declared field or the whole rule is lost. `evidence`
    is required and must name what was observed.
    """
    global BUTTON_ORDER
    need = {"select", "start", "a", "b", "lr", "ud"}
    missing = need - set(order)
    if missing:
        raise ValueError(f"button order is missing {sorted(missing)}")
    unknown = set(order.values()) - set(_FIELDS)
    if unknown:
        raise ValueError(f"button order names fields that do not exist: {unknown}")
    if not evidence.strip():
        raise ValueError("record_button_order needs the observation, not just the answer")
    BUTTON_ORDER = dict(order)
    BUTTON_ORDER["evidence"] = evidence


def load_button_order() -> None:
    """Install the committed measurement. Used by actions and by tests."""
    record_button_order(
        {"select": "deb_select", "start": "deb_start", "a": "deb_a", "b": "deb_b",
         "lr": "deb_lr", "ud": "deb_ud"},
        BUTTON_ORDER_EVIDENCE)


# ------------------------------------------------------------------ the phases
# The eleven main-loop routines, from MISC.SRC:891-900. Used as
# `phase == PHASE_SHOP` in an action's predicate.
PHASE_MAIN = 0            # g00  the playing level
PHASE_CHANGE = 1          # g01  set the new level, fade colours down
PHASE_FADE = 2            # g02  fading
PHASE_ENTER = 3           # g03  build a level: load data, draw the view
PHASE_FADE_UP = 4         # g04  fade colours up
PHASE_FADE2 = 5           # g05  fading
PHASE_TUNE = 6            # g06  init the sound chip and exit
PHASE_SHOP = 7            # g07  shops and other town buildings
PHASE_INVENTORY = 8       # g08  the inventory screen
PHASE_SPELL = 9           # g09  the spell screen
PHASE_MAP = 10            # g0a  the map screen

PHASE_NAMES = {
    PHASE_MAIN: "g00 playing level",
    PHASE_CHANGE: "g01 change level",
    PHASE_FADE: "g02 fade",
    PHASE_ENTER: "g03 enter level",
    PHASE_FADE_UP: "g04 fade up",
    PHASE_FADE2: "g05 fade",
    PHASE_TUNE: "g06 tune",
    PHASE_SHOP: "g07 shop",
    PHASE_INVENTORY: "g08 inventory",
    PHASE_SPELL: "g09 spell screen",
    PHASE_MAP: "g0a map screen",
}

# The first town, as a level number, from x0.pds:602-604:
#     start jsr dotitle / jsr initvars / lda #$0a / ldx #$e2 / jsr newlev
# so a clean start puts the game in phase $0a (the map) at logical level $E2,
# with mapind = stlev = $01 (x1.pds:27-29, x5.pds:8).
START_LEVEL = 0xE2
START_MAPIND = 0x01


# ================================================================== predicates
_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    # BITWISE, and this is a correction rather than a style choice. These two
    # ops exist to ask "is this BIT of this byte set", which is what the game's
    # own `bit tmpflag` (x5.pds:523) and `and #%00001100` (x4.pds:376) do and
    # what every quest flag assertion needs. Written as `a % b`, `band` with
    # mask 1 is vacuously TRUE for every value -- every integer is a multiple
    # of 1 -- so "the player has bought the goat's milk" would have held from
    # power-on in every state, silently. And `bne` with a 2-bit mask said that
    # a count of 3 was "not carried", which is backwards: `addinv` refuses a
    # FOURTH (`get2 / cmp #$03 / bcs`, x7.pds:444-446), so 3 is carrying as
    # much of it as the game allows. src/testing/test_play_ram.py checks 18/18b
    # are the cases that distinguish the two readings, and they were written
    # first and watched fail.
    "band": lambda a, b: (a & b) == b if b > 0 else False,
    "bne": lambda a, b: (a & b) != 0 if b > 0 else False,
    # "this bit is CLEAR". Neither of the two above says it: `band` asks for
    # every bit of the mask to be set and `bne` for at least one. "The vicar has
    # NOT asked yet" is a question the ladder asks before every step of that
    # quest, and there is no way to spell it without a third op.
    "bclr": lambda a, b: (a & b) == 0 if b > 0 else False,
}


@dataclass(frozen=True)
class Pred:
    """A test on a RAM field, evaluated against a $0000-$07FF image or in the core.

    `holds(image)` is the reference implementation; `encode()` is what the Lua
    bridge evaluates. The two must agree, and src/testing/test_play_ram.py
    checks that by feeding the same cases to both -- a wire format that means
    something slightly different from the Python predicate is a predicate that
    passes for the wrong reason.
    """
    field: Field
    op: str
    value: int

    def __post_init__(self):
        if self.op not in _OPS:
            raise ValueError(f"unknown predicate op {self.op!r}; "
                             f"the bridge knows {sorted(_OPS)}")

    def holds(self, image: bytes) -> bool:
        return _OPS[self.op](self.field.get(image), self.value)

    def encode(self) -> str:
        # DECIMAL on the wire, in every field. The bridge's pattern is
        # `^(%d+):(%d+):(%a+):(-?%d+)$`, so a hex value here is rejected as a
        # malformed predicate -- and because the address happens to be hex-safe,
        # `5f:1:eq:a` fails only on the value, which is exactly the sort of
        # near-miss that wastes an afternoon.
        return f"{self.field.addr:d}:{self.field.length:d}:{self.op}:{self.value:d}"

    def __str__(self) -> str:
        return f"{self.field.name} {self.op} {self.value:#x} @${self.field.addr:04X}"


def pred(field_name: str, op: str, value: int) -> Pred:
    return Pred(f(field_name), op, value)


def all_of(*preds: Pred) -> list[Pred]:
    """AND a set of predicates. A list, because the bridge ANDs them all."""
    flat = []
    for p in preds:
        if isinstance(p, (list, tuple)):
            flat.extend(all_of(*p))
        else:
            flat.append(p)
    return flat


def describe(fields=None, image: bytes | None = None) -> str:
    """A readable dump of the named fields, for the run log."""
    names = [x.name for x in (fields or all_fields())]
    if image is None:
        return "\n".join(f"  {n:<16} ${f(n).addr:04X} (+{f(n).length}) "
                         f"-- {f(n).comment.splitlines()[0]}" for n in names)
    lines = []
    for n in names:
        x = f(n)
        lines.append(f"  {n:<16} ${x.addr:04X} = {x.get(image):<6} "
                     f"[{x.hex(image)}]")
    return "\n".join(lines)


def self_check() -> list[str]:
    """Every field's address agrees with the symbol table. Returns problems.

    Called at import time of any module that uses this one. An empty list means
    every declared address is one the assembler gave that name.
    """
    bad = []
    for x in _ORDER:
        addrs = {sym(s) for s in x.syms}
        if len(addrs) > 1:
            pairs = ", ".join(f"{s}=${a:04X}" for s, a in zip(x.syms, sorted(addrs)))
            bad.append(f"{x.name}: symbols {x.syms} disagree ({pairs})")
        base = sym(x.syms[0])
        # The address must be EXACTLY base+offset, whatever the offset is. A
        # negative offset is a real thing here -- joykey writes the eight decoded
        # button bytes at $0028-$002F, which is six bytes BELOW `jt` at $002E --
        # so the rule is not "above the base" but "the base plus the offset the
        # declaration recorded". A hand-typed address cannot pass this.
        if x.addr != base + x.offset:
            bad.append(f"{x.name}: declared at ${x.addr:04X} but {x.syms[0]} "
                       f"(${base:04X}) plus its declared offset {x.offset} is "
                       f"${base + x.offset:04X}")
        if x.addr + x.length > 0x0800 or x.addr < 0:
            bad.append(f"{x.name}: ${x.addr:04X}+{x.length} is outside RAM")
    return bad


PROBLEMS = self_check()
if PROBLEMS:
    raise AssertionError(
        "ram.py disagrees with the symbol table:\n  " + "\n  ".join(PROBLEMS))


def map_digest() -> str:
    """SHA1 of the declared field table.

    Printed by every run log. If the table changes, every previously recorded
    fingerprint stops being comparable, and this is how a reader can tell.
    """
    h = hashlib.sha256()
    for x in _ORDER:
        h.update(f"{x.name}={x.addr:x}+{x.length} {x.syms}\n".encode())
    return h.hexdigest()[:16]