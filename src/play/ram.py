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
        lo = self.addr + self.length - 1
        if lo >= len(image):
            raise IndexError(
                f"{self.name} is ${self.addr:04X}+{self.length}, past the "
                f"{len(image)}-byte image given to it")
        v = 0
        for i in range(self.length):
            v = (v << 8) | image[self.addr + i]
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
    if length < 1 or length > 16:
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
field("nmiflag", 1, "non-zero while game processing is in progress; the IRQ "
      "main loop skips everything else while it is set (x5.pds:222-223)", "nmiflag")

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

# ------------------------------------------------------------------ the player
# `x0.pds:242-243`: `maxob equ $04` / `pi equ maxob-1`, so the PLAYER is object
# index 3. mag.sym ALSO has `pi = $001C`, which is SUBGAMES.SRC's "pointer
# index" and has nothing to do with this. Every object array below is
# `zp <name>,maxob` -- four bytes, one per object -- so the player is at
# <base>+3. This is the single most load-bearing offset in the file.
PLAYER_IDX = 3
field("plrtype", 1, f"obtyp[{PLAYER_IDX}] -- the player's object type. obtyp is "
      f"'type/active flag', 4 bytes, one per object (x0.pds:542); player index "
      f"{PLAYER_IDX} from `pi equ maxob-1` (x0.pds:243)", "obtyp")
field("plrmode", 1, f"obmod[{PLAYER_IDX}] -- the player's movement mode "
      "(x0.pds:543)", "obmod")
field("plrstat", 1, f"obstat[{PLAYER_IDX}] -- 'anim/movement/dead/general flag'; "
      f"bit 7 is DEAD, bits 2-3 are the facing, and bit 6 is 'in the air' "
      f"(x1.pds:1 uses it as: bmi = dead, bvc = air, lsr/lsr + bcs = moving). "
      f"Every claim this harness makes about the player standing still or "
      f"facing a direction rests on this byte", "obstat")
field("plrflg", 1, "the PLAYER object flag: >0 while walking, <0 while jumping "
      "or falling, 0 standing (x0.pds not named; x1.pds:120-128 'ldy plrflg / "
      "bne', x5.pds:512 'bit plrflg / bvs' for in-air)", "plrflg")
field("plrx", 2, f"obxl[{PLAYER_IDX}]:obxh[{PLAYER_IDX}] -- the player's "
      f"16-bit MAP position, not a screen position (x0.pds:548-549)", "obxl")
field("plry", 2, f"obyl[{PLAYER_IDX}]:obyh[{PLAYER_IDX}] -- the player's 16-bit "
      f"map Y (x0.pds:549-550)", "obyl")
field("plrchest", 1, f"obaorg[{PLAYER_IDX}], also called obchest -- the player's "
      f"animation-origin / chest counter (x0.pds:546)", "obchest")
field("plrhelm", 1, f"obhel[{PLAYER_IDX}] -- the player's health counter. "
      f"`helind` (x6.pds:33) computes 'health rating = health / 32', so it is a "
      f"16x-scale counter, NOT a hit point count and not an HP bar", "obhel")
field("plrven", 1, f"obven[{PLAYER_IDX}] -- venom counter (x0.pds:551)", "obven")
field("plrchr", 1, f"obchr[{PLAYER_IDX}] -- object characteristics: which floor "
      f"types pass (x0.pds:552)", "obchr")
field("plrmsg", 1, f"obint[{PLAYER_IDX}] -- the interaction message attached to "
      f"the player, used by the wise man and the beggar (x0.pds:553, x0.pds:735 "
      f"intmsg/begmsg are the object-side copies)", "obint")

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
field("invop", 7, "object inventory flags (x0.pds:540), 7 bytes = 28 bits for "
      "the 0x1C object types", "invop")
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
field("pad", 8, "the eight bytes joykey decodes, at $0028-$002F. Derived from "
      "`jt` ($002E) minus 6: joykey writes them with `sty jt,x` for x=7..0, "
      "which addresses $002F down to $0028. Index 0 is $0028. WHICH BUTTON IS "
      "WHICH INDEX is measured, not assumed -- see BUTTON_ORDER", "jt", offset=-6)
field("pad_a", 1, "pad[0] = $0028. joykey's decoded byte 0", "jt", offset=-6)
field("pad_b", 1, "pad[1] = $0029. joykey's decoded byte 1", "jt", offset=-5)
field("pad_select", 1, "pad[2] = $002A. joykey's decoded byte 2", "jt", offset=-4)
field("pad_start", 1, "pad[3] = $002B. joykey's decoded byte 3", "jt", offset=-3)
field("pad_up", 1, "pad[4] = $002C -- WHICH IS ALSO nmiflag, so it is only valid "
      "between frames, not inside the main loop", "jt", offset=-2)
field("pad_down", 1, "pad[5] = $002D -- WHICH IS ALSO bnksel, same caveat",
      "jt", offset=-1)
field("pad_left", 1, "pad[6] = $002E -- the accumulator's own address, `jt`",
      "jt")
field("pad_right", 1, "pad[7] = $002F", "jt", offset=1)
field("lr", 1, "$FF when A OR B is held -- a combined flag, NOT 'left or right' "
      "(joykey, DISP.SRC:316-322)", "lr")
field("ud", 1, "$FF when SELECT OR START is held -- also not 'up or down'",
      "ud")
field("sel_state", 1, "$0032, the individual state of whichever button the "
      "source calls Select", "sta")
field("start_state", 1, "$0033, the individual state of whichever button the "
      "source calls Start", "sel")
field("fireb_state", 1, "$0034, named `fireb` by the source but written from "
      "hardware read 4", "fireb")
field("firea_state", 1, "$0035, named `firea` by the source but written from "
      "hardware read 5", "firea")
field("deb_fire", 1, "$0036 -- debounced edge of `lr`, i.e. A or B changed",
      "dlr", "fs")
field("deb_ss", 1, "$0037 -- debounced edge of `ud`, i.e. Select or Start changed",
      "dud")
field("deb_sta", 1, "$0038 -- the source calls this `dsta`; which button it is is "
      "MEASURED (see select_edge)", "dsta")
field("deb_sel", 1, "$0039 -- the source calls this `dsel`; the other of the "
      "SELECT/START pair (see start_edge)", "dsel")
field("deb_fireb", 1, "$003A -- the source calls this `dfireb`", "dfireb")
field("deb_firea", 1, "$003B -- the source calls this `dfirea`", "dfirea")

# The measured button order, filled in by the recon. `None` means "not measured
# yet", and every accessor that depends on it raises rather than choosing.
BUTTON_ORDER: dict[str, str] | None = None


def select_edge() -> Field:
    """The debounced byte that carries SELECT.

    Raises until the recon has measured it. Picking a side on the strength of
    the name `dsta` looking like "START" is precisely the mistake this module
    exists to prevent, and the mistake is invisible: the byte is a real byte and
    the predicate is a real predicate.
    """
    if BUTTON_ORDER is None:
        raise AssertionError(
            "which debounced byte carries SELECT has not been measured. The "
            "source's names (dsta/dsel/dfireb/dfirea) and the order joykey writes "
            "them in disagree, and both readings produce a plausible, wrong "
            "answer. Run src/play/milestones/m1_first_town.py, which presses one "
            "button at a time and reads $0028-$003B back.")
    return f(BUTTON_ORDER["select"])


def start_edge() -> Field:
    if BUTTON_ORDER is None:
        raise AssertionError("see select_edge()")
    return f(BUTTON_ORDER["start"])


def a_edge() -> Field:
    if BUTTON_ORDER is None:
        raise AssertionError("see select_edge()")
    return f(BUTTON_ORDER["a"])


def b_edge() -> Field:
    if BUTTON_ORDER is None:
        raise AssertionError("see select_edge()")
    return f(BUTTON_ORDER["b"])


def record_button_order(order: dict[str, str], evidence: str) -> None:
    """Record the measurement.

    `order` maps a logical button name to a FIELD NAME (not an address -- the
    address has to come through a declared field or the whole point is lost).
    `evidence` is required: a button order recorded without the observation that
    established it is a guess wearing a measurement's clothes.
    """
    global BUTTON_ORDER
    need = {"select", "start", "a", "b", "up", "down", "left", "right"}
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
    "band": lambda a, b: (a % b == 0) if b > 0 else False,
    "bne": lambda a, b: (a % b != 0) if b > 0 else False,
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
        return f"{self.field.addr:x}:{self.field.length:x}:{self.op}:{self.value:x}"

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