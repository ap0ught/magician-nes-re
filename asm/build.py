#!/usr/bin/env python3
"""Assemble the Magician cartridge from Eurocom's source and compare it with
the real thing.

The eight `X?.PDS` files are assembled in order into one 128 KiB PRG image,
sharing a symbol table: x0 defines every macro and the whole zero-page and RAM
map, and x1-x7 use them. The CHR image is packed from the binary artwork under
`DAT/` in the order the development system's `SENDG` script sends it.

A module's *initial* 8 KiB slot is not recorded anywhere in the source -- x5
begins `org $c000` with no `bank` -- so it is recovered by assembling the module
in each of the sixteen slots and keeping the one whose output matches the
cartridge most. That is a measurement, not a guess: the data files in the source
are byte-identical to the ones in the cart, so the right slot matches thousands
of bytes and every wrong slot matches almost none.

    python3 asm/build.py                      # build and report
    python3 asm/build.py --verbose            # per-file incbin trace
    python3 asm/build.py --check              # non-zero exit if anything moved

Everything written here is derived: `out/` is git-ignored. The cartridge is
only ever read.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import patches  # noqa: E402
from pds6502 import SOURCE_GAPS, Assembler  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "vendor" / "Magician-NES"
OUT = ROOT / "asm" / "out"

PRG_SIZE = 128 * 1024
CHR_SIZE = 128 * 1024

MODULES = [f"X{i}.PDS" for i in range(8)]
# Every file a macro can be defined in. All 40 macros are in X0.PDS, but the
# collection pass follows `include` anyway so this is not load-bearing.
ALL_SOURCES = [SRC / m for m in MODULES] + sorted(SRC.glob("*.SRC"))

# ---------------------------------------------------------------------------
# The bank map, and what is known about each module's slot.
# ---------------------------------------------------------------------------
#
# The game's own MMC3 documentation is in the source, X5.PDS:10-21:
#
#     ;The MMC3 map-mode bit (register 0,bit-6) is always set to zero
#     ;in this game giving the following PRG memory map :-
#     ;$8000-$9FFF : from bank in MMC3 register 6 ($00..$0F)
#     ;$A000-$BFFF : from bank in MMC3 register 7 ($00..$0F)
#     ;$C000-$DFFF : from bank $0E
#     ;$E000-$FFFF : from bank $0F
#
# So `bnk 6,#N` means *8 KiB slot N is at $8000* and `bnk 7,#N` means *slot N is
# at $A000*, and slots 14 and 15 are the fixed windows whether the program likes
# it or not. That is the whole placement vocabulary, and it is read off the
# cartridge's own documentation rather than searched for.
MMC3_MAP = {
    0x8000: "MMC3 register 6 (switchable, 8 KiB slot $00..$0F)",
    0xA000: "MMC3 register 7 (switchable, 8 KiB slot $00..$0F)",
    0xC000: "fixed 8 KiB slot $0E (14)",
    0xE000: "fixed 8 KiB slot $0F (15)",
}

# `SEQ.SRC` is not one of the eight `X?.PDS` modules and nothing pulls it in:
# its only `include` edge is `DISP.SRC:130`, spelled `include \zdev\seq.src`,
# a DOS path from the Atari ST machine that cannot resolve on this filesystem,
# and `DISP.SRC` itself is included by no bank -- the edge is dead at both ends.
# It is 907 lines of the game's animation tables and nothing else.
#
# `$A000` is not a guess, and neither is slot 5:
#
#  * `$A000` is the only address the tree gives it (`DISP.SRC:129`), and both
#    `frame` routines agree independently: `ANIM.SRC:282-284` computes
#    `$A000 | 6*(frame+fbase)` by `ora #>$a000` and then indexes `(te),y` down
#    from y=5, which is exactly a table of 272 six-byte entries at `$A000`.
#  * Slot 5 is measured. `SEQ.SRC:4-6` begins
#        ANIMTAB  HEX 094000000000 / HEX 094001010100 / HEX 084002020200
#    and those 24 bytes occur **once** in the whole 128 KiB cartridge, at file
#    `$0A000` -- slot 5, offset 0, CPU `$A000`. One hit in 131 072 bytes is not
#    a statistical argument.
#
# Assembled there it defines 497 symbols and collides with none of the symbols
# the eight banks define, so nothing already resolved moves.
#
# Its logical span is `$A000`-`$C676`, so it fills the `$A000` window and then
# runs 1 654 bytes past it into the fixed `$C000` window. That part addresses
# bank `$0E` -- slot 14, X5's slot, which X5's own comment says is for "common
# routines & data ... used from any other bank". Nothing in the tree says how
# the two share it, so it is left unplaced and reported, not guessed at.
SEQ_MODULES = [("SEQ.SRC", 5)]
SEQ_ORIGIN = 0xA000
SEQ_WINDOW_SLOTS = {0xA000: 5, 0xC000: None}

# What is known about each module's 8 KiB slot, and how it is known. `None`
# means the sixteen-slot search could not decide, and the build says so rather
# than guessing (see `ASSUMED_SLOTS`).
#
# The evidence is the game's own bank-switching calls. `farjsr67`
# (`X7.PDS:828-848`) takes a slot for register 6 and a slot for register 7,
# loads them, and `jsr`s the target; so every `farjsr67` call site states the
# window its target lives in. `bnk 6,#N` / `bnk 7,#N` do the same without the
# call. That is declarative, it is in the source, and it does not depend on the
# byte-match percentage, which is at chance for code.
MODULE_NOTES = {
    "X0.PDS": ("slot 0", "X0.PDS:601 `org $8000`, so `start` is its $8000. "
               "`X7.PDS:reset` ends `bnk 6,#$0` / `bnk 7,#$1` (\"set initial PRG "
               "banks\") and then `jmp start`, so slot 0 is at $8000."),
    "X1.PDS": ("slot 0, chained onto X0",
               "no `org`, so it continues from X0's end. `pob01` (X1.PDS:687) is "
               "reached by `farjsr67` with R6=0,R7=1 (\"do bank 0/1 plr ob "
               "routines\"), so it is in slot 0 or 1. Slot 0's bottom is X0's but "
               "X0 emits only 1294 of 8192 bytes and X1 emits 2433, so both fit; "
               "slot 1 is X2's. Result: initvars=$850E, pob01=$8D78."),
    "X2.PDS": ("slot 1", "`firespell` (X2.PDS:9) is its first code and is reached "
               "by `farjsr67` with R6=0,R7=1. Slot 0 is X0's, so it must be slot "
               "1 -- which only works if X2 starts at $A000, not $8000."),
    "X3.PDS": ("slot 4", "`X3.PDS:777 memchk a000,4` and, independently, "
               "`X5.PDS:515-516` `bnk 6,#4` then `jsr dobullets`, which is "
               "X3.PDS:9 -- its $8000."),
    "X4.PDS": ("slot 2 for its $8000 part", "`handleobs` (X4.PDS:17) is reached "
               "by `farjsr67` with R6=2 (`X5.PDS:329-331`, `X1.PDS:676-678`) and "
               "is at $8108, in the register-6 window. Its `memchk c000,$2` "
               "(X4.PDS:1213) says the same. The `$A000` part -- ANIM.SRC and "
               "PROBS.SRC, whose `frame` is `fbnk 7,#bfra` with `bfra equ $5` -- "
               "claims slot 5, which SEQ.SRC's tables also hold. UNRESOLVED."),
    "X5.PDS": ("slot 14", "X5.PDS:4 `org $c000`, and the cartridge has its `sql` "
               "table byte-identically at file $0C000+$10000."),
    # Slot 15, pinned -- not slot 3 by elimination. This string said slot 3
    # while PINNED_SLOTS["X6.PDS"] was 15, so every build log contradicted
    # itself: the placement line said 15 and this note said 3.
    "X6.PDS": ("slot 15, pinned", "no `org` of its own. `reset` does `jsr "
               "initcols`, and `initcols` is in X6; in the cartridge that call "
               "target is $F04A and in this source's build $EFEE, both inside "
               "the fixed $E000 window, which MMC3 gives to slot 15 whatever the "
               "registers say. Elimination over the free slots is not evidence: "
               "the search's only hit was a false positive (slot 12 is level "
               "5's slot by X7's own `b = $c`, and the 64 bytes are inv.col, "
               "which the cartridge does not contain). See PINNED_SLOTS."),
    "X7.PDS": ("slot 15", "the cartridge's vectors read nmi=$F9AB irq=$F9B3 "
               "reset=$F9C1 and `reset`/`nmi`/`irq` are X7.PDS:922/903/914."),
}

# Modules whose 8 KiB slot is *known* rather than searched, with the evidence.
#
# The search scores the bytes that came out of a `DAT` file, and x7's own data
# does not discriminate: 6.0% at slot 12 against 5.1% at slot 9, with no winner.
# But the cartridge's reset vector is ground truth and it settles the question.
# `reset` and `nmi` are defined in X7.PDS (lines 922 and 903) and the vectors at
# file 0x1FFFA read `nmi=$F9AB irq=$F9B3 reset=$F9C1`, all inside slot 15's
# $E000-$FFFF window. X7's `org $fffa` reaches file 0x1FFFA only from slot 15 --
# from slot 12 it lands at 0x19FFA, which is why the rebuilt ROM's reset vector
# came out as $8681 and could not boot.
PINNED_SLOTS = {"X7.PDS": 15}

# Slot 3 is **X4's**, not X6's, and the measurement that pinned it to X6 was
# wrong in a way worth writing down.
#
# `X4.PDS:1210` says, in the source's own words, `include probs.src` / `** must
# reside above $9FFF`. X4 has one `org $8000` (line 4) and no other, so its
# `anim.src`/`probs.src` tail runs on past $9FFF into the **register 7**
# window. Which slot is in register 7 when that tail is called is stated by the
# caller: `X1.PDS:678` calls `scnevents` -- a PROBS.SRC label -- with
# `ldx #$02 / ldy #$03 / jsr farjsr67`, and `farjsr67`
# (`X7.PDS:828-835`) puts X in register 6 ($8000) and Y in register 7 ($A000).
# So PROBS.SRC is in **slot 3, at $A000**, while X4's code proper is slot 2 at
# $8000. That is the same fact `X5.PDS:329-331` gives for X4's other half:
# `bnk 6,#0 / bnk 7,#1` then `farjsr67` with X=$02,Y=$03 to reach `handleobs`
# in register 6 = slot 2.
#
# It used to be pinned to X6, by elimination, on the reasoning that slot 3 was
# "the only 8 KiB slot nothing else claims". That reasoning is circular once
# X4's second window is accounted for, and it cost the boot: X6 has no `org`, so
# its whole address came from `slot_origin(3)`, and the resulting `jsr initcols`
# at $E9E9 read slot 15 (which is what $E000-$FFFF always is) instead of X6.
PINNED_SLOTS["X4.PDS"] = 2

# X6: no `org`, and `initcols` is the one routine in it that the boot path
# cannot do without, so its window is *measured* rather than searched.
#
# `initcols` is X6's own routine (`X6.PDS:791`) and `reset` calls it before it
# does anything else. In the cartridge that call is `jsr $F04A`, and in the Beta
# `jsr $F00D` -- both inside $E000-$FFFF, the window that is slot 15 whatever
# the bank registers say. The caller is in that same window, so the callee has
# to be too: **X6 is in slot 15.** That is not a search result and not an
# elimination; it is two jsr displacements in the ROM.
#
# X6 shares slot 15 with the tail of X5 and with X7, and that is not a
# workaround: `X5.PDS:14-21` says so in the source --
#   "Any processor accesses of memory between $C000-$FFFF will always get data
#    from banks $0E/$0F, thus common routines & data should reside here ...
#    ** Dev sys note :- any downloads to $C000-$FFFF automatically go to banks
#    $0E/$0F, thus no BANK commands are needed for this area."
# So X5 (`org $c000`), X6 and X7 are one continuous address stream that the
# hardware files into slots 14 and 15 by address alone. X6 and X7 have no `org`
# of their own for exactly that reason, and both are CHAINED below.
PINNED_SLOTS["X6.PDS"] = 15

# Why each of those is pinned rather than searched, for the build log. A pin with
# no reason is an assumption wearing a pin's clothes.
PINNED_WHY = {
    "X7.PDS": "PINNED from the cartridge's reset vector (not searched)",
    "X6.PDS": "PINNED: reset's `jsr initcols` is $F04A in the cartridge and "
              "$F00D in the Beta, both in the fixed $E000 window = slot 15",
    "X4.PDS": "PINNED from `X1.PDS:678` farjsr67(X=$02,Y=$03) to a PROBS.SRC "
              "label, and `X4.PDS:1210`'s own \"must reside above $9FFF\"",
}

# X4 spans two windows, and which slot is in each is stated by its callers, so
# it is declared rather than searched. Without this its `$A000`-`$BFFF` tail
# computes the same file offsets as its `$8000`-`$9FFF` head (`prg_offset` keys
# on the window, and both would resolve through `self.slot = 2`) and the tail
# silently overwrites the head.
#
#   $8000 window -> slot 2  `X5.PDS:329-331`: `bnk 6,#0 / bnk 7,#1` then
#                            `farjsr67` X=$02,Y=$03 to reach `handleobs`
#   $A000 window -> slot 3  `X1.PDS:678`: `farjsr67` X=$02,Y=$03 to reach
#                            `scnevents`, a PROBS.SRC label; and
#                            `X4.PDS:1210` "must reside above $9FFF"
#
# The `$C000` window is not in the map on purpose: `X4.PDS:1213` is
# `memchk c000,$2`, the source's own assertion that X4's code ends below $C000,
# so anything past it is a fault to report rather than file.
X4_WINDOW_SLOTS = {0x8000: 2, 0xA000: 3}

# The same map, keyed by module, for the search pass and the project pass.
MODULE_WINDOW_SLOTS = {"X4.PDS": X4_WINDOW_SLOTS}

# Slot 15 has 8 KiB and three modules want to be in it, and they do not fit.
#
# Nothing in this comment is a number the build does not print. The byte counts
# come from `Assembler.ceiling_drops`, which `prg_offset` fills one entry per
# *distinct* byte address the source assembles at or above a module's ceiling,
# and which the build log prints as "CEILING DROPS" immediately above the byte
# accounting. At the time of writing, with the ceilings set as below:
#
#     X5.PDS    517 byte(s), $E605-$E809
#     X6.PDS    150 byte(s), $F166-$F1FB
#     total     667 byte(s)
#
# `tools/modrange.py` reports the other half: X5 assembles $C000-$E809 (10 250
# bytes, so 2 058 of them past the end of slot 14), X6 assembles $E605-$F1FB
# (3 063), and X7's own `last` plus SAM.SAM want the rest of the window.
#
# Why a ceiling at all, and why these two. `initcols` is at $EFEE in this build
# and at $F04A in the cartridge, and it is on the path from `reset` to anything
# visible -- `reset` calls it. X6 is pinned to slot 15 because that call is a
# jump into the fixed $E000 window. X6 has no `org` of its own, so it starts
# wherever X5 stops, and X5 has no choice either: its MISC.SRC tail runs 2 058
# bytes past $DFFF. X7's start is pinned by the cartridge's reset vector
# ($F9C1 => $F166). Three modules, 8 KiB.
#
# So the shortfall is taken out of the two modules' *tails*, and the split is
# chosen so that `initcols` survives whole: X6 must be able to emit through
# $F165, immediately below X7, and with MODULE_ORIGINS putting X6 at X5's
# ceiling that fixes X5_CEILING at $E605. The cost of that choice is the 667
# bytes printed above, which is larger than the raw over-subscription because
# the ceilings are a judgement about which code matters, not a division of a
# shortfall. Adding a ceiling to make the number smaller would move the loss
# somewhere that runs.
#
# What the loss costs at run time is a separate question, and `tools/nestrace.py`
# answers it rather than this comment. Measured with the ceilings as they stand:
# `reset` -> `initdma` -> `initcols` -> `dotitle` -> `unrunscn` -> `jsr initspr`
# are all reached, and `initspr` assembles at $F033 -- 307 bytes *below* X6's
# ceiling -- so `jsr initspr` lands in X6's own code and not in X7's. What is
# never reached is `im` (X5.PDS:319, the orgchrpal -> curchrpal copy) and
# `nmi0` (X5.PDS:167, the per-frame palette push), because execution leaves the
# program's own code first: at cycle 98674 `addmsg`'s `rts` at $F1A5 finds an
# empty stack and returns into RAM at $0F10. That is a lost stack frame, not a
# missing routine, and it is the next thing to fix.
#
X5_CEILING = 0xE605
X6_CEILING = 0xF166
MODULE_CEILINGS = {"X5.PDS": X5_CEILING, "X6.PDS": X6_CEILING}

# Modules that carry their starting address from the module before them, because
# they have no `org` of their own.
#
# Three are chained, and each for a reason that is in the source:
#
#   X1  `X1.PDS:687` `pob01` is reached by `farjsr67` with R6=0,R7=1 -- "do bank
#       0/1 plr ob routines" -- so it is in the window register 6 shows when
#       register 6 holds slot 0. Slot 0's bottom is X0's (`X0.PDS:601 org
#       $8000`, `start` at $8000); X0 emits 1 294 bytes and X1 emits 2 433, so
#       both fit in one slot with 4 465 to spare, and X1 goes where X0 stops.
#
#   X6  no `org`, and `X5.PDS:14-21` says downloads to $C000-$FFFF go to slots
#       $0E/$0F automatically. X5 is `org $c000`, so X6 continues its address
#       stream out of slot 14 and into slot 15 by address alone. It is pinned to
#       slot 15 so `prg_offset` files it there; being chained is what puts it at
#       $E809 rather than $E000, so it cannot overwrite X5's tail.
#
# X7 is NOT chained, although it has no `org` either: its address is *measured*,
# by the two-pass loop in `assemble_prg` that fits X7 so `reset` lands on the
# cartridge's reset vector. Chaining it would put it wherever X6 happens to stop,
# which is a function of X6's size rather than of anything in either file.
#
# X2 is NOT chained: it has no `org` either, but `firespell` (X2.PDS:9, its own
# first code) is reached by `farjsr67` with R6=0,**R7=1** (`X4.PDS:452-455`) --
# so it lives in the register 7 window at $A000, which is where `MODULE_ORIGINS`
# puts it. It is the one module whose window is named by a caller rather than
# implied by the fixed-window rule.
CHAINED = ["X1.PDS"]

# Placement for the modules whose slot is neither measured nor provable, used
# when --assume-banks is on (the default). This is a *hypothesis* and is labelled
# as one in the output. The order was NOT tuned to raise the byte-match
# percentage: per-module byte scoring of X0-X5 is at or below chance at every
# one of the sixteen slots (0.3-2.1% against a 0.39% chance level), so that
# number carries no signal about placement and optimising against it would be
# fitting noise.
ASSUMED_SLOTS = {
    "X0.PDS": 0, "X1.PDS": 1, "X2.PDS": 1, "X3.PDS": 4,
    "X5.PDS": 14,
}

# Which CPU address each module is *assembled* at, where that is not the same
# thing as which 8 KiB file slot it lands in.
#
# `Assembler.slot_origin` is now a statement about the hardware and nothing
# else: 14 -> $C000, 15 -> $E000, everything else -> $8000. So a module with no
# `org` that is pinned to a switchable slot is assembled at $8000 unless this
# table says otherwise, and there is exactly one entry.
#
# X2: `X4.PDS:452-455` is
#
#     ldx #<firespell / lda #>firespell / stx ma
#     ldx #$00 / ldy #$01 / jsr farjsr67
#
# and `farjsr67` (`X7.PDS:828-835`) loads X into register 6 ($8000) and Y into
# register 7 ($A000). `firespell` is X2's first code (X2.PDS:9), so X2 is in
# register 7's window: slot 1 at $A000. `X1.PDS:678` reaches `scnevents`
# (PROBS.SRC, i.e. X4's tail) the same way with X=$02,Y=$03, which is where the
# second entry in `X4_WINDOW_SLOTS` comes from.
MODULE_ORIGINS: dict[str, int] = {"X2.PDS": 0xA000, "X6.PDS": X5_CEILING}

ORIGIN_WHY = {
    "X2.PDS": "$A000 from `X4.PDS:452-455`: farjsr67(X=$00,Y=$01) to `firespell`, "
              "so X2 is in register 7's window",
    # $E605, not $E77C: this string said $E77C for several builds and that
    # value appears nowhere else in the tree. It is X5_CEILING, and the reason
    # is the ceiling comment above.
    "X6.PDS": "$E605 = X5_CEILING: where X5's slot-15 spill has to stop so that "
              "X6's `initcols` (at $EFEE here) lands below X7's $F166. See "
              "X5_CEILING.",
}


# The sanity check that should have caught it. Printed on every build, because a
# module silently assembled into a window it cannot occupy is the kind of thing
# that reads as "the source drifted" for a long time.
#
# A module that carries its own leading `org` is exempt: `X4.PDS:4` and `X3.PDS:4`
# both say `org $8000`, so their address comes from the source and the slot only
# decides where the bytes are filed. Only a module that has *no* `org` is at the
# mercy of `slot_origin`.
def _leads_with_org(module: str) -> int | None:
    """The address a module's own `org` gives it, or None if it has none."""
    try:
        # The PDS container terminates lines with `CR NUL`, not `LF`, so a plain
        # `split("\n")` on the raw bytes yields one enormous "line" and the walk
        # below never reaches the `org`. Normalise first.
        text = (SRC / module).read_bytes()[0x200:].decode("latin-1", "replace")
    except OSError:
        return None
    for line in text.replace("\r\x00", "\n").split("\n"):
        for seg in line.split("\r"):
            body = seg.split(";")[0].strip()
            if not body:
                continue
            head = body.split()[0].lower()
            if head in ("org", "bank", "load", "include", "endm", "macro"):
                if head != "org":
                    return None
                tok = body.split()[1] if len(body.split()) > 1 else ""
                m = re.fullmatch(r"\$?([0-9A-Fa-f]{1,4})", tok)
                return int(m.group(1), 16) if m else None
            # `radix`/`option`/`send` and a macro definition may precede the org.
            if head in ("radix", "option", "send", "name", "page", "space"):
                continue
            return None
    return None


def all_slots() -> dict[str, int]:
    """Every module's slot: pinned where measured, assumed otherwise, chained
    modules inheriting the slot of the window they land in."""
    out: dict[str, int] = {}
    for m in MODULES:
        if m in PINNED_SLOTS:
            out[m] = PINNED_SLOTS[m]
        elif m in ASSUMED_SLOTS:
            out[m] = ASSUMED_SLOTS[m]
    return out


def window_faults() -> list[str]:
    """Modules assembled at an address the hardware cannot give them.

    A module whose own `org` supplies the address is exempt -- `X3.PDS:4` and
    `X4.PDS:4` both say `org $8000`, so there the slot only decides which file
    offsets the bytes are filed at, and `X4_WINDOW_SLOTS` says which. Only a
    module that reaches a fixed window by inference is at risk.
    """
    out = []
    for module, slot in sorted(all_slots().items()):
        origin = MODULE_ORIGINS.get(module)
        if origin is None and _leads_with_org(module) is not None:
            continue
        if origin is None and module in CHAINED:
            # A chained module starts wherever the previous one stopped, and the
            # fixed-window rule in `prg_offset` files it by address. Chaining into
            # $C000-$FFFF is what `X5.PDS:14-21` describes, so it is not a fault.
            continue
        if origin is None:
            origin = Assembler.slot_origin(slot)
        if origin in (0xC000, 0xE000) and slot not in (14, 15):
            fixed = 14 if origin == 0xC000 else 15
            how = ("pinned " + PINNED_WHY.get(module, "")) if module in PINNED_SLOTS \
                else "assumed"
            out.append(f"{module}: slot {slot} ({how}) assembled at ${origin:04X}, "
                       f"which MMC3 fixes to slot {fixed}")
    return out

# X7's start address *inside* slot 15, fitted to the cartridge's reset vector.
#
# Slot 15 is `$E000-$FFFF` and its bottom is not the start of X7: something
# occupies the first 0x1166 bytes. The cartridge says what is there -- X7.PDS:6
# gives X5's code and X7.PDS:8 gives the SAM.SAM sample data, both loaded at
# explicit addresses inside that window -- and the fit below lands X7 at $F166,
# with SAM.SAM ending at $FB80 and X7's own `if last>$fb80` guard overrunning by
# 16 bytes. Sixteen bytes out of 0x1166 is the honest size of the residual: this
# source emits slightly more code before `last` than the release did. Nothing
# here is hard-coded -- `reset`'s offset inside X7 is measured by a first project
# pass and subtracted from the cartridge's vector -- so the anchor follows the
# assembler if X7's internal layout ever changes.
X7_VECTOR_NOTE = (
    "start fitted so that `reset` lands on the cartridge's reset vector")

# X7's `if last>$fb80 / error ">$FB80!"` (X7.PDS:974-979). Downgraded, not fixed:
# see the note above. If this fires, SAM.SAM is written over `last - $FB80` bytes
# of X7's code, which is the real cost and is printed when it happens.
X7_DEMO_ERRORS = (">$FB80!",)

# The CHR image, in the order `SENDG` sends it to the development system, with
# the 8 KiB slot each file occupies as written there. Only the order matters for
# packing; the slot column is what the cartridge is checked against.
CHR_SEND_ORDER = [
    ("10.chr", 0x00 * 4), ("20.chr", 0x01 * 4), ("21.chr", 0x02 * 4),
    ("30.chr", 0x03 * 4), ("40.chr", 0x04 * 4), ("50.chr", 0x05 * 4),
    ("53.chr", 0x06 * 4), ("60.chr", 0x07 * 4), ("70.chr", 0x08 * 4),
    ("73.chr", 0x09 * 4), ("80.chr", 0x0A * 4), ("su.chr", 0x0B * 4),
    ("spr/inv.spr", 0x0C * 4), ("map.chr", 0x0D * 4), ("tit0.chr", 0x0E * 4),
    ("tit1.chr", 0x1D * 4), ("tit2.chr", 0x1E * 4), ("sh.chr", 0x0F * 4),
]
# `spr\bank40.dat` .. `spr\bank73.dat`, the per-object sprite artwork.
SPRITE_FIRST, SPRITE_LAST = 0x40, 0x73


def read_cart(path: pathlib.Path) -> tuple[bytes, bytes]:
    """Read the cartridge's real header and return (prg, chr)."""
    data = path.read_bytes()
    if data[:4] != b"NES\x1a":
        raise SystemExit(f"{path} has no iNES header")
    prg_len = data[4] * 16384
    chr_len = data[5] * 8192
    body = data[16:]
    if len(body) < prg_len + chr_len:
        raise SystemExit(f"{path} is short: header declares "
                         f"{prg_len + chr_len} body bytes, file has {len(body)}")
    return body[:prg_len], body[prg_len:prg_len + chr_len]


def match_score(image: bytearray, cart: bytes, offsets) -> tuple[int, int]:
    """How many of `offsets` agree with the cartridge.

    Called with `asm.data_offsets` -- the bytes that came out of a `DAT` file.
    Those are byte-identical to the cartridge's whatever the code does, so they
    are the only trustworthy evidence for which slot a module belongs in. Scoring
    the whole output instead measures mostly wrong code: during this search the
    cross-bank forward references are still unresolved, so every `lda label`
    picks zero-page over absolute and the opcode bytes are noise.
    """
    hits = total = 0
    for off in offsets:
        if not 0 <= off < len(cart):
            continue
        total += 1
        if cart[off] == image[off]:
            hits += 1
    return hits, total


def assemble_prg(cart_prg: bytes, verbose: bool,
                  x7_table: bool = True, assume_banks: bool = True,
                  bank_groups: bool = True, seq_src: bool = True,
                  x7_vector: bool = True, split_banks: bool = True,
                  asm_cls=None
                  ) -> tuple[bytearray, Assembler, list[str], list[str]]:
    # `asm_cls` exists so tools/whowrote.py can subclass the Assembler and keep a
    # per-module footprint of which PRG offsets each bank wrote. The build itself
    # always uses the real class.
    #
    # `bank_groups` compensates for x7's `memchk c000,b` banking the group *after*
    # the one it closes -- see `Assembler.maybe_prebank`, which carries the
    # measurement. With it on, x7's level data lands in the slots its own `b`
    # counters name, which is where the cartridge has it.
    cls = asm_cls or Assembler
    banks = frozenset({"b"}) if bank_groups else frozenset()
    image = bytearray(PRG_SIZE)
    asm = cls(image, SRC, [SRC], verbose=verbose)
    asm.force_conditions = {"0=1": x7_table}
    asm.prebank_symbols = banks
    asm.prebank_split = split_banks
    asm.prescan(ALL_SOURCES)
    asm.collect_macros(ALL_SOURCES)
    log: list[str] = []

    # Pass 1: find each module's initial slot by matching its output against the
    # cartridge. The data files in the source are byte-identical to the ones in
    # the cart, so the right slot matches thousands of bytes.
    asm.tolerate = True
    slots: list[int | None] = []
    unplaced: list[str] = []
    for module in MODULES:
        path = SRC / module
        snap = asm.snapshot()
        if module in PINNED_SLOTS:
            slot = PINNED_SLOTS[module]
            asm.restore(snap)
            asm.emitted = {}
            asm.data_offsets = set()
            # Guarded like the searched path. A pinned module that will not
            # assemble must be reported, not raised: an exception here escapes
            # before the INCOMPLETE BUILD summary prints, so `make` dies with a
            # traceback and never says which modules are missing.
            try:
                asm.run_file(path, slot=slot,
                             origin=MODULE_ORIGINS.get(module),
                             window_slots=MODULE_WINDOW_SLOTS.get(module))
            except Exception as exc:                    # noqa: BLE001
                log.append(f"{module}: slot {slot:2d} (pinned) DID NOT ASSEMBLE: "
                           f"{type(exc).__name__}: {exc}")
                unplaced.append(module)
                slots.append(None)
                continue
            slots.append(slot)
            hits, total = match_score(image, cart_prg, asm.data_offsets)
            why = PINNED_WHY.get(module, "PINNED (not searched)")
            log.append(f"{module}: 8 KiB slot {slot:2d}  {hits:6d}/{total:6d} DAT "
                       f"bytes match ({100.0 * hits / total if total else 0:5.1f}%)"
                       f"  {why}")
            continue
        best = None
        # Why each slot was rejected. Without this the search silently discards
        # the reason and a module that fails at *every* slot looks like one that
        # merely matches nowhere -- which is how x7's `error ">$FB80!` hid for
        # hours behind "slot not determined".
        rejected: dict[str, list[int]] = {}
        scores: list[tuple[int, int, int]] = []
        for slot in range(16):
            asm.restore(snap)
            asm.emitted = {}
            asm.data_offsets = set()
            try:
                asm.run_file(path, slot=slot)
            except Exception as exc:                    # noqa: BLE001
                rejected.setdefault(f"{type(exc).__name__}: {exc}", []).append(slot)
                continue
            hits, total = match_score(image, cart_prg, asm.data_offsets)
            scores.append((hits, total, slot))
            if total and (best is None or hits > best[0]):
                best = (hits, total, slot)
        if best is None:
            for reason, bad in sorted(rejected.items(), key=lambda kv: -len(kv[1])):
                log.append(f"{module}: slot {bad[0]:2d} rejected: {reason}"
                           + (f"  (and {len(bad) - 1} more)" if len(bad) > 1 else ""))
            # An undetermined slot is a hole, not a detail to paper over. Guessing
            # here is what produced a ROM that booted to a black screen: six
            # modules were all assembled at whatever slot the search happened to
            # end on and overwrote each other in one 8 KiB window. So the module
            # is left out and the build says so, loudly, and exits non-zero.
            if assume_banks and module in ASSUMED_SLOTS:
                slot = ASSUMED_SLOTS[module]
                asm.restore(snap)
                asm.emitted = {}
                asm.data_offsets = set()
                try:
                    asm.run_file(path, slot=slot,
                                 origin=MODULE_ORIGINS.get(module),
                                 window_slots=MODULE_WINDOW_SLOTS.get(module))
                except Exception as exc:                # noqa: BLE001
                    log.append(f"{module}: ASSUMED slot {slot:2d} DID NOT "
                               f"ASSEMBLE: {type(exc).__name__}: {exc}")
                    unplaced.append(module)
                    slots.append(None)
                    continue
                slots.append(slot)
                # A chained module is assembled at a provisional slot here only so
                # the search has something to score; the project pass continues it
                # from the previous module instead. Say so, or the two lines of the
                # log look like they disagree.
                tail = ("and is CHAINED onto the previous module in the project "
                        "pass" if module in CHAINED else "")
                log.append(f"{module}: 8 KiB slot {slot:2d}  *** ASSUMED, NOT "
                           f"MEASURED *** (search found no winner) {tail}")
                continue
            log.append(f"{module}: *** SLOT NOT DETERMINED - not assembled ***")
            unplaced.append(module)
            slots.append(None)
            continue
        hits, total, slot = best
        asm.restore(snap)
        asm.emitted = {}
        asm.data_offsets = set()
        asm.run_file(path, slot=slot, origin=MODULE_ORIGINS.get(module),
                     window_slots=MODULE_WINDOW_SLOTS.get(module))
        slots.append(slot)
        pct = 100.0 * hits / total if total else 0.0
        log.append(f"{module}: 8 KiB slot {slot:2d}  {hits:6d}/{total:6d} DAT bytes "
                   f"match the cartridge ({pct:5.1f}%)")
        for h, t, sl in sorted(scores, key=lambda x: -x[0])[:4]:
            log.append(f"{module}:     slot {sl:2d}  {h:6d}/{t:6d} "
                       f"({100.0 * h / t if t else 0.0:5.1f}%)")
        for reason, bad in sorted(rejected.items(), key=lambda kv: -len(kv[1])):
            log.append(f"{module}:   rejected slots {bad}: {reason}")

    # Pass 2: the banks are one program, so references across them only resolve
    # once every module has been assembled at least once. Only the placed modules
    # take part -- an unplaced one would need a slot to have been found for it,
    # and inventing one is what this build stopped doing.
    #
    # Run twice when x7 is anchored to the cartridge's reset vector. The first
    # run measures how far into x7 `reset` sits; the second puts x7 where the
    # cartridge's vector says that offset lands. Nothing is hard-coded: if x7's
    # internal layout moves, the anchor moves with it.
    chained = set(CHAINED)
    x7_off = x7_base = None
    project_error: Exception | None = None
    for attempt in range(2 if x7_vector else 1):
        image = bytearray(PRG_SIZE)
        asm = cls(image, SRC, [SRC], verbose=verbose)
        asm.force_conditions = {"0=1": x7_table}
        asm.prebank_symbols = banks
        asm.prebank_split = split_banks
        # X7's own `if last>$fb80 / error` guard. Once x7 is anchored to the
        # vector the guard fires, and that is *information*: it means this
        # source emits more bytes before `last` than the release did. Letting it
        # raise would abandon the ~50 000 bytes of level data that follow it, so
        # it is downgraded and printed. See `X7_VECTOR_NOTE`.
        asm.demo_errors = X7_DEMO_ERRORS
        asm.prescan(ALL_SOURCES)
        asm.collect_macros(ALL_SOURCES)
        placed = [(m, None if m in chained else s)
                  for m, s in zip(MODULES, slots) if s is not None]
        if seq_src:
            placed += [(m, s) for m, s in SEQ_MODULES]
        origins = {"SEQ.SRC": SEQ_ORIGIN} if seq_src else {}
        origins.update(MODULE_ORIGINS)
        window_slots = {"SEQ.SRC": SEQ_WINDOW_SLOTS} if seq_src else {}
        window_slots["X4.PDS"] = X4_WINDOW_SLOTS
        ceilings = dict(MODULE_CEILINGS)
        if x7_base is not None:
            origins["X7.PDS"] = x7_base
        try:
            asm.run_all([SRC / m for m, _ in placed], [s for _, s in placed],
                        origins, window_slots, ceilings)
        except Exception as exc:                        # noqa: BLE001
            # The per-module slot log above is the context that makes this
            # failure legible, so it is kept -- and then the failure is raised
            # rather than swallowed.
            #
            # It used to `break`, which left `image` as the zero-filled bytearray
            # allocated at the top of this loop and returned it as a build. Every
            # downstream number was then a measurement of an image with no code
            # in it: tools/modrange.py's per-module byte ranges came out 0%,
            # and its output is where the ceiling arithmetic in this file gets
            # its numbers. A tool whose Assembler subclass has a stale run_file
            # signature lands here as a plain TypeError, indistinguishable from
            # a genuine assembly error, and produces a plausible all-zero table.
            log.append(f"project pass failed: {type(exc).__name__}: {exc}")
            project_error = exc
            break

        if not x7_vector or "X7.PDS" not in dict(placed):
            break
        reset, slot15 = asm.sym.get("reset"), PINNED_SLOTS["X7.PDS"]
        if reset is None:
            break
        want = cart_prg[0x1FFFC] | (cart_prg[0x1FFFD] << 8)
        if attempt == 0:
            x7_off = reset - Assembler.slot_origin(slot15)
            x7_base = want - x7_off
            log.append(f"X7.PDS: reset is ${x7_off:04X} bytes into the module and "
                       f"the cartridge's reset")
            log.append(f"         vector is ${want:04X}, so the cartridge's X7 "
                       f"begins at ${x7_base:04X} --")
            log.append(f"         not ${Assembler.slot_origin(slot15):04X}. "
                       f"Re-running with x7 anchored there.")
            continue
        last = asm.sym.get("last")
        log.append(f"X7.PDS: anchored at ${x7_base:04X}; reset now "
                   f"${asm.sym['reset']:04X} "
                   f"(cartridge ${want:04X}), nmi ${asm.sym.get('nmi', -1):04X}, "
                   f"irq ${asm.sym.get('irq', -1):04X}")
        if last is not None and last > 0xFB80:
            log.append(f"X7.PDS: *** x7's code ends at ${last:04X}, "
                       f"{last - 0xFB80} byte(s) past the $FB80 where its own "
                       f"`if last>$fb80 / error`")
            log.append(f"         guard says code must stop, so SAM.SAM at $FB80 "
                       f"overwrites ${last - 0xFB80} byte(s) of it. ***")

    # What the ceilings cost, counted by the assembler rather than asserted in a
    # comment. `overflow` is cleared per file, which is why the numbers in the
    # ceiling comment above used to disagree with each other: nothing printed
    # them. These are *distinct* addresses, so the several passes `run_all` makes
    # before the symbols settle do not multiply the count.
    drops: dict[str, list[int]] = {}
    for name, p, _slot in sorted(asm.ceiling_drops):
        drops.setdefault(name, []).append(p)
    if drops:
        log.append("")
        log.append("CEILING DROPS: bytes the source assembles that are not in the "
                   "image, because the")
        log.append("module ran past MODULE_CEILINGS. Counted by "
                   "Assembler.prg_offset, not asserted:")
        total = 0
        for name, ps in sorted(drops.items()):
            lo, hi = min(ps), max(ps)
            log.append(f"  {name:<22} {len(ps):5d} byte(s), ${lo:04X}-${hi:04X}")
            total += len(ps)
        log.append(f"  {'total':<22} {total:5d} byte(s)")
        log.append("  Every one of these is reachable from `reset` in principle; "
                   "GAPMAP.md names which")
        log.append("  of them the tracer actually reaches. Do not add a ceiling "
                   "to make this smaller:")
        log.append("  the shortfall is three modules competing for slot 15's8 KiB.")

    # After the loop, not inside it: the handler above `break`s, so a check
    # written there is never reached by the one case it exists for.
    if project_error is not None:
        print("\n".join(log), file=sys.stderr)
        raise RuntimeError(
            f"the project pass failed and would otherwise return an image with "
            f"nothing in it: {type(project_error).__name__}: "
            f"{project_error}") from project_error
    return image, asm, log, unplaced


def dat_file(name: str) -> pathlib.Path:
    """Resolve a `DAT/` name to the file on disk, ignoring case.

    `SENDG` and the source write these in lower case (`10.chr`, `spr/inv.spr`)
    while the released tree stores them upper case (`10.CHR`, `SPR/INV.SPR`).
    The Atari ST toolchain was case-insensitive; this filesystem is not.
    """
    want = name.replace("\\", "/").lower()
    for path in (SRC / "DAT").rglob("*"):
        if path.is_file() and str(path.relative_to(SRC / "DAT")).lower() == want:
            return path
    raise SystemExit(f"no DAT file for {name!r} under {SRC / 'DAT'}")


def build_chr(cart_chr: bytes, verbose: bool) -> tuple[bytearray, list[str]]:
    image = bytearray(CHR_SIZE)
    entries: list[tuple[pathlib.Path, int | None]] = []
    for name, _slot in CHR_SEND_ORDER:
        entries.append((dat_file(name), None))
    for bank in range(SPRITE_FIRST, SPRITE_LAST + 1):
        entries.append((dat_file(f"SPR/BANK{bank:02X}.DAT"), None))

    log: list[str] = []
    cursor = 0
    for path, declared in entries:
        data = path.read_bytes()
        # Placement is sequential, in the order `SENDG` sends the files. The slot
        # column above is the development system's CHR bank number, and it does
        # not index this 128 KiB image: it runs to $1E, which is past the last
        # 4 KiB page. It is kept only as a record of what SENDG says, and the
        # result below is checked against the cartridge rather than assumed.
        off = declared * 4096 if declared is not None else cursor
        if off + len(data) > CHR_SIZE:
            log.append(f"{path.name:14s} does not fit at {off:#06x}, skipped")
            continue
        image[off:off + len(data)] = data
        cursor = off + len(data)
        if verbose:
            same = cart_chr[off:off + len(data)] == data
            log.append(f"  chr {path.name:14s} at {off:#06x} "
                       f"{'exact' if same else 'differs'}")

    # Report how much of the rebuilt CHR agrees with the cartridge, page by page.
    exact = differ = 0
    for page in range(CHR_SIZE // 4096):
        a = bytes(image[page * 4096:(page + 1) * 4096])
        b = cart_chr[page * 4096:(page + 1) * 4096]
        if a == b:
            exact += 1
        elif a.strip(b"\x00") and b.strip(b"\x00"):
            differ += 1
    log.append(f"CHR: {exact:2d}/32 4 KiB pages identical to the cartridge, "
               f"{differ:2d} differ")
    return image, log


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cart", type=pathlib.Path,
                    default=pathlib.Path("/extdrive/backups/SHARE/roms/nes/Magician (USA).nes"))
    ap.add_argument("--cart-dir", type=pathlib.Path, default=patches.CART_DIR,
                    help="where the manifest's named dumps are read from")
    ap.add_argument("--no-patches", action="store_true",
                    help="do not apply asm/patches.manifest; report the source-only "
                         "image. Useful for asking what the source alone achieves.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--x7-level-table", choices=("on", "off"), default="on",
                    help="x7's `if 0=1` branch. On by default: it holds 27640 bytes "
                         "of real level data and the cartridge was built with it "
                         "enabled. Off reproduces the development-build layout.")
    ap.add_argument("--x7-bank-groups", choices=("on", "off"), default="on",
                    help="bank each x7 data group when its `b = $N` counter is "
                         "assigned, rather than at the `memchk c000,b` that closes "
                         "it. On by default: it is what puts the level data in the "
                         "slots the cartridge has it in (6.4%% -> 28.8%% of the "
                         "cartridge matched). Off is the literal reading of the "
                         "released source.")
    ap.add_argument("--seq-src", choices=("on", "off"), default="on",
                    help="assemble SEQ.SRC at org $a000, slot 5. On by default: it "
                         "is the only definition site of the eight tables "
                         "SOURCE_GAPS names, and the cartridge carries ANIMTAB "
                         "byte-identically at file $0A000. Off leaves them "
                         "assembling as zero.")
    ap.add_argument("--x7-vector", choices=("on", "off"), default="on",
                    help="fit X7's start address inside slot 15 so that `reset` "
                         "lands on the cartridge's reset vector ($F9C1). On by "
                         "default: off leaves X7 starting at $E000, which puts "
                         "reset at $E85B and cannot boot. " + X7_VECTOR_NOTE)
    ap.add_argument("--x7-bank-split", choices=("on", "off"), default="on",
                    help="let a `b = $N` group name the slot in the *next* window "
                         "too ($8000 -> N, $A000 -> N+1). On by default: X7.PDS:1001"
                         "-1005 defines bmus=b and bshop=btit=bpw=bpan=bev=b+1, and "
                         "the code banks register 7 -- the $A000 window -- to "
                         "b+1. Off leaves one slot for the whole group, so its "
                         "$A000 half is written over its own $8000 half and slot "
                         "7 comes out empty; see Assembler.maybe_prebank.")
    ap.add_argument("--no-assume-banks", action="store_true",
                    help="refuse to place a module whose slot the search could "
                         "not determine, instead of using ASSUMED_SLOTS")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="exit 0 even though some modules have no determined slot")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    cart_prg, cart_chr = read_cart(args.cart)

    prg, asm, prg_log, unplaced = assemble_prg(
        cart_prg, args.verbose,
        x7_table=(args.x7_level_table == "on"),
        assume_banks=not args.no_assume_banks,
        bank_groups=(args.x7_bank_groups == "on"),
        seq_src=(args.seq_src == "on"),
        x7_vector=(args.x7_vector == "on"),
        split_banks=(args.x7_bank_split == "on"))
    chr_rom, chr_log = build_chr(cart_chr, args.verbose)

    # `prg` is the assembled image; `source_image` is a copy taken before the
    # manifest touches it, so the accounting below compares like with like.
    source_image = bytes(prg)

    # The patch manifest, and the byte accounting that goes with it.
    #
    # Without this the headline percentage is unreadable: filling a range from the
    # cartridge raises it, and a rising percentage is exactly what a rising number
    # of hidden bugs also looks like. So the source-only image is measured first,
    # then the manifest is applied, and both numbers are printed with the regions
    # that produced the difference. `asm/mkrom.py` applies the same manifest, so
    # the ROM and these numbers describe the same image.
    source_only = sum(1 for a, b in zip(prg, cart_prg) if a == b)
    prg_log = list(prg_log)
    patch_report = None
    if args.no_patches:
        prg_log.append("patch manifest: NOT applied (--no-patches); every byte "
                       "below came from the source")
    else:
        try:
            regions = patches.load()
            resolved = patches.verify(regions, args.cart_dir)
            prgs = {name: patches.body(p) for name, p in resolved.items()}
            cart_for_regions = (prgs[regions[0].cart] if len(prgs) == 1
                                else patches._mixed(prgs, regions))
            patch_report = patches.apply(prg, cart_for_regions, regions, args.cart_dir)
        except patches.PatchError as exc:
            print(f"*** PATCH MANIFEST REJECTED, and nothing was filled: {exc}\n")
            return 2

    (OUT / "prg.src.bin").write_bytes(bytes(source_image))
    (OUT / "prg.bin").write_bytes(bytes(prg))
    (OUT / "chr.bin").write_bytes(bytes(chr_rom))
    with (OUT / "mag.sym").open("w") as fh:
        for key in sorted(asm.sym):
            fh.write(f"{key} = ${asm.sym[key]:04X}\n")

    hits = sum(1 for a, b in zip(prg, cart_prg) if a == b)
    print("\n".join(prg_log))
    print("\n".join(chr_log))
    print(f"\nPRG: {hits}/{len(prg)} bytes identical to the cartridge "
          f"({100.0 * hits / len(prg):.1f}%)")
    print(f"  of which from the source alone : {source_only} "
          f"({100.0 * source_only / len(prg):.1f}%)")
    if patch_report is not None and patch_report.applied:
        filled = patch_report.matched_after
        print(f"  of which from the patch manifest: {filled} "
              f"({100.0 * filled / len(prg):.1f}%) across "
              f"{len(patch_report.applied)} region(s), "
              f"{patch_report.total_bytes} bytes")
        for r in patch_report.applied:
            print(f"    {r.region.name}: class {r.region.klass}, "
                  f"{r.region.length}B at file ${r.region.offset:05X}, "
                  f"+{r.matched_after - r.matched_before} matched; the source had "
                  f"{r.already_correct} of these bytes right already")
        print("  the two lines above are the whole accounting: only the first is "
              "evidence about the source.")
    else:
        print("  of which from the patch manifest: 0 "
              + ("(--no-patches)" if args.no_patches
                 else "(asm/patches.manifest has no regions)"))
    print(f"symbols: {len(asm.sym)}  ->  {OUT / 'mag.sym'}")

    # The bank map and what is known about each module's place in it. Printed
    # with every build because it is the thing most worth being wrong about:
    # `MODULES` are eight files and the PRG has sixteen 8 KiB slots, and only
    # five of the eight placements are pinned by anything.
    print("\nbank map (X5.PDS:10-21):")
    for base, what in MMC3_MAP.items():
        print(f"  ${base:04X}-${base + 0x1FFF:04X}  {what}")
    print("placement:")
    for m in MODULES + ["SEQ.SRC"]:
        where, why = MODULE_NOTES.get(m, ("slot ?", "no evidence either way"))
        slot = (SEQ_MODULES[0][1] if m == "SEQ.SRC"
                else (f"{ASSUMED_SLOTS[m]:2d}" if m in ASSUMED_SLOTS else " --"))
        tag = "chained" if m in CHAINED else f"slot {slot}"
        print(f"  {m:9s} {tag:9s} ({where})")
        print(f"            {why}")
        if m in ORIGIN_WHY:
            forced = MODULE_ORIGINS.get(m)
            where = (f"assembled at ${forced:04X} (forced)" if forced is not None
                     else f"assembled at "
                          f"${Assembler.slot_origin(ASSUMED_SLOTS[m]):04X} "
                          f"(inherited from the slot)")
            print(f"            {ORIGIN_WHY[m]}; {where}")
    faults = window_faults()
    if faults:
        print("\n*** FIXED-WINDOW COLLISION: a module is assembled at an address "
              "MMC3 cannot")
        print("*** give it. A slot can only be presented at $8000 or $A000 unless "
              "it is 14 or 15, so for")
        print("*** a module with no `org` of its own the slot and the origin have "
              "to agree and here")
        print("*** they do not. Read MODULE_ORIGINS before changing anything here.")
        for line in faults:
            print(f"***   {line}")
        print("***   For X6 specifically the measured answer is that its SLOT is "
              "wrong, not its")
        print("***   address: both dumps' `reset` call `initcols` at $F04A/$F00D, "
              "inside the fixed")
        print("***   $E000 window, so X6 is in slot 15. It cannot be moved there "
              "without an offset")
        print("***   within the slot, and X6's code is in neither dump at any "
              "address (class b), so")
        print("***   nothing measured pins that offset down.")
    unproven = [m for m in MODULES if m not in PINNED_SLOTS]
    print(f"  SLOT evidence: {len(MODULES) - len(unproven)} of {len(MODULES)} "
          f"modules are pinned "
          f"({', '.join(m for m in MODULES if m in PINNED_SLOTS)}); "
          f"{len(unproven)} are ASSUMED and labelled as such above "
          f"({', '.join(unproven)}).")
    print("  SEQ.SRC is placed at slot 5 by a measured single hit: its first 24 "
          "bytes occur")
    print("  once in 131072, at file $0A000. The `no evidence either way` line "
          "above is a")
    print("  MODULE_NOTES omission and is wrong; the evidence is in the comment on "
          "SEQ_MODULES.")
    print("  With X0=0, X4=2, X3=4, SEQ=5, X5=14, X7=15 and x7's own data owning "
          "6..13, only")
    print("  slots 1 and 3 are free -- and both `pob01` and `firespell` are "
          "reached with only")
    print("  slots 0 and 1 mapped. Three modules, two slots: the release layout "
          "is NOT a")
    print("  bijection over the eight files. Do not read the byte-match percentage "
          "as evidence")
    print("  about this; it is at chance for code. Per-module scoring and the "
          "placement table")
    print("  are in GAPMAP.md.")
    if any("ASSUMED" in line for line in prg_log):
        print("\n*** PLACEMENT ASSUMED, NOT MEASURED: the slot search found no "
              "winner for the modules marked ASSUMED above. ***")
        print("*** Byte-match numbers below therefore say as much about the "
              "placement as about the code. ***")
    print(f"rebuilt prg sha256 {hashlib.sha256(bytes(prg)).hexdigest()[:16]}  "
          f"chr sha256 {hashlib.sha256(bytes(chr_rom)).hexdigest()[:16]}")

    # The statement scanner's own audit. Every entry is a place where a logical
    # line had to be cut in a way that was not the trivially expected one, or a
    # symbol was defined twice. None of them are errors -- most are `memchk`
    # emitting a group into both slots of a 16 KiB bank -- but they are the only
    # record of where the rebuild is resting on an interpretation, so they are
    # printed rather than left in a list nobody reads.
    seen: dict[str, int] = {}
    for entry in asm.report:
        seen[entry] = seen.get(entry, 0) + 1
    if unplaced and not args.allow_incomplete:
        print(f"\n*** INCOMPLETE BUILD: {len(unplaced)} of {len(MODULES)} modules have "
              f"no determined 8 KiB slot and were NOT assembled:")
        print(f"***   {', '.join(unplaced)}")
        print("***   The PRG above is missing them. Pass --allow-incomplete to exit 0 "
              "anyway.")
        return 1
    if unplaced:
        print(f"\n*** INCOMPLETE BUILD: {len(unplaced)} module(s) not assembled: "
              f"{', '.join(unplaced)}")

    if asm.gaps_used:
        print(f"\nsource gaps: {len(asm.gaps_used)} of {len(SOURCE_GAPS)} tables "
              f"assembled as ZERO because SEQ.SRC was left out of this build:")
        for name, where in sorted(asm.gaps_used.items()):
            print(f"  {name:10s} first read at {where}")
        print("  They are not a gap in the 2012 release -- SEQ.SRC defines all "
              "eight (lines 784/801/816/")
        print("  839/847/864/879/902). They assemble as zero only when "
              "--seq-src off, which is")
        print("  a diagnostic reading. The default build includes SEQ.SRC and "
              "gaps_used is empty.")

    if asm.prebank_log:
        banks_used = sorted({(f, s) for f, _, s in asm.prebank_log})
        print(f"\nbank groups: {len(banks_used)} `b = $N` counter(s) banked on "
              f"assignment (memchk banks the group after it, not its own):")
        for fname, slotno in banks_used:
            print(f"  {fname:<22} -> 8 KiB slot {slotno:>2}")
        print("  with --x7-bank-groups off this is fewer bank switches and the "
              "level data")
        print("  lands one 8 KiB slot low; see Assembler.maybe_prebank.")

    if asm.overflow:
        by_file: dict[str, list[int]] = {}
        for f, p, s in asm.overflow:
            by_file.setdefault(f, []).append(p)
        print(f"\n*** SLOT OVERFLOW: {len(asm.overflow)} byte(s) written past a "
              f"module's 8 KiB window with no `bank` to say which slot is there. "
              f"They are NOT in the image:")
        for f, ps in sorted(by_file.items()):
            lo, hi = min(ps), max(ps)
            print(f"***   {f:<12} {len(ps):5d} byte(s), ${lo:04X}-${hi:04X}")
        print("***   This is a hole that prints. Folding them back over the "
              "module's own start is what")
        print("***   silently destroyed X5's `sql` table; see "
              "Assembler.prg_offset.")

    print(f"\nscanner report: {len(asm.report)} entries, {len(seen)} distinct")
    for entry, count in sorted(seen.items(), key=lambda kv: -kv[1])[:40]:
        print(f"  {count:5d}x {entry}")
    if len(seen) > 40:
        print(f"  ... and {len(seen) - 40} more distinct entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())