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
SRC_MAG = ROOT / "src" / "magician"
OUT = ROOT / "asm" / "out"
# Where our generated packed data lands. Never committed: it is derived from
# src/magician/ by tools/datcodec.py every build, so committing it would create
# a second copy of the title screen that could disagree with the first.
SRC_MAG_OUT = OUT / "ours"

PRG_SIZE = 128 * 1024
CHR_SIZE = 128 * 1024

MODULES = [f"X{i}.PDS" for i in range(8)]

# ---------------------------------------------------------------------------
# Our own source: src/magician/.
# ---------------------------------------------------------------------------
#
# vendor/Magician-NES/ is Eurocom's, pinned at bf653a40, and it stays read-only
# and byte-identical so it can be re-synced. Everything we author lives in
# src/magician/ instead, in the same PDS dialect and assembled by the same
# assembler, so a change we make is a source edit rather than a patch to
# someone else's file.
#
# The manifest is explicit rather than a glob, and `check_our_manifest` below
# makes an unlisted file a hard error. A glob would mean that dropping a file
# into src/magician/ silently changes the ROM with no record of why -- which is
# the same class of silent change as a module assembled at a slot nobody chose.
# A file with no manifest entry has no known placement, and a placement is
# something that has to be argued for.
#
# Each entry may set:
#   slot          the 8 KiB slot, when the module needs one
#   origin        the start address, when the module has no `org` of its own
#   window_slots  which slot is in each $8000 window the module reaches
#   ceiling       highest address the module may write
# `titdat` sits in the $A000 window of X7's `b = $6` data group, and that is slot
# 7, not 6. X7.PDS:1001-1003 sets
#
#     bmus   equ b          ; b = $6
#     bshop  equ b+1
#     btit   equ b+1
#
# and `dotitle` banks register 7 -- the $A000 window -- to `btit`. This build's
# `--x7-bank-split` (on by default) reads the same thing the other way: a group's
# `b = $N` names the slot in the $8000 window and `N+1` the one in $A000. See
# Assembler.maybe_prebank.
#
# Getting it wrong is not subtle but it is quiet: at slot 6 the overlay lands at
# prg.bin $CBA7, which is real code, and `verify_our_scenes()` catches it because
# the bytes it finds there are not the scene it packed. The build did that rather
# than reporting success.
OUR_TITLE_WINDOW_SLOTS = {0x8000: 6, 0xA000: 7, 0xC000: 14, 0xE000: 15}

OUR_MODULES: dict[str, dict] = {
    "TITLE.SRC": {
        "window_slots": OUR_TITLE_WINDOW_SLOTS,
        "note": "our title screen: the packed scene descriptor overlaid on X7's "
                "`titdat`, so Eurocom's own `dotitle` loads it with no change to "
                "vendor/ at all",
    },
    "STARTLEV.PDS": {
        # `expect` is the build's assertion that the overlay actually changed the
        # image, which is a different question from "did it assemble". A module
        # that emitted its bytes and was then overwritten reports a clean build
        # and a ROM with somebody else's value in it -- and this byte is the
        # whole difference between entering level 1 and entering level 2. So the
        # bytes are named here and read back out of the finished image.
        "expect": {"stlev": "00"},
        "note": "the level a new game starts on: X5.PDS:8's `stlev db $01` "
                "marked `** tmp`, overlaid with the value the cartridge we "
                "target actually ships. See STARTLEV.PDS for the measurement",
    },
}

# Every file a macro can be defined in, ours included, so a module of ours can
# use a macro Eurocom defined. All 40 macros are in X0.PDS, but the collection
# pass follows `include` anyway so this is not load-bearing for them; it *is*
# load-bearing for src/magician/, which is allowed to use them.
ALL_SOURCES = ([SRC / m for m in MODULES] + sorted(SRC.glob("*.SRC"))
               + sorted(p for p in SRC_MAG.glob("*") if p.suffix.upper() in (".SRC", ".PDS")))


def check_our_manifest() -> list[str]:
    """Every file in src/magician/ must have a manifest entry. Returns problems.

    A module with no entry has no known slot, origin or window map, so there is
    no honest way to place it. Assembling it anyway would put it somewhere the
    build cannot explain; skipping it silently would mean a file someone wrote
    and thought was being built was not being built at all.
    """
    if not SRC_MAG.is_dir():
        return []
    problems = []
    on_disk = {p.name for p in SRC_MAG.iterdir()
               if p.is_file() and p.suffix.upper() in (".SRC", ".PDS")}
    for name in sorted(on_disk - set(OUR_MODULES)):
        problems.append(f"src/magician/{name} has no entry in OUR_MODULES "
                        f"(asm/build.py), so nothing says where it goes")
    for name in sorted(set(OUR_MODULES) - on_disk):
        problems.append(f"OUR_MODULES lists src/magician/{name}, which is not there")
    return problems


def our_modules() -> list[str]:
    """Our module names, in assembly order, verified against what is on disk."""
    return sorted(OUR_MODULES)

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
               "slot 1 is X2's $A000 half. Result: initvars=$850E, pob01=$8D78."),
    "X2.PDS": ("slot 0 head, slot 1 tail, chained onto X1",
               "`firespell` (X2.PDS:9) is X2's first code and Beta 1 calls it at "
               "$8E75 with R6=0,R7=1, so its head is in register 6's window, in "
               "slot 0, at the end of X1's chain. Its `$A000` half is slot 1: "
               "X2.PDS:467 pads to `$a000` and X2.PDS:469's `M00` is the first "
               "byte of that slot, and `decompchr` (`X5.PDS:27`) reads `CODESL` "
               "at $B8AA = slot 1 offset $18AA with `fbnk 7,#1,n`. Two slots in "
               "one module; see `X2_WINDOW_SLOTS` for the full chain."),
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
    "X6.PDS": "PINNED: `reset` is in the fixed $E000 window and its `jsr initcols` "
              "names an address in that same window, so X6 must be in slot 15. "
              "Re-derived against beta1: that `jsr` is $EEC7 in both images and "
              "`initcols` assembles at $EEC7, so the operand is byte-identical. "
              "See tools/align6502.py --from 0xF930 --length 0x40 --prg "
              "asm/out/prg.bin.",
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

# X2 spans the $8000 and $A000 windows, and this is the measurement that says so.
# It replaces `MODULE_ORIGINS["X2.PDS"] = 0xA000`, which put X2's whole stream in
# register 7's window and made 2 669 bytes of it spill out of the $BFFF end into
# $C000-$DFFF -- where MMC3 gives slot 14 whatever the registers say, so X5
# overwrote them and five data labels came to point at message text.
#
# The chain of evidence, each step checked against Beta 1 and not reasoned from
# the next one. `tools/x2_windows.py --explain` re-derives all of it and fails if
# any step stops holding; `src/testing/test_x2_windows.py` pins the answers.
#
# 1. **The macro moves both registers.** `farjsr67` is at $F878 in Beta 1 (PRG
#    $1F878, slot 15). Its body is
#
#        A5 06 48 86 06 A9 06 85 2D 8D 00 80 8E 01 80
#        A5 07 48 84 07 A9 07 85 2D 8D 00 80 8C 01 80
#
#    i.e. `lda r6 / pha / stx r6 / lda #$06 / sta $2D / sta $8000 / stx $8001`
#    then the same with `$07` and `sty`. $2D is `bnksel`. So X goes to MMC3
#    register 6 and Y to register 7 -- confirmed, and `bnksel` is $2D, not $00.
#
# 2. **The one call site names its target in an operand, and that operand is
#    $8E75.** There are five `jsr $F878` in Beta 1. The one from X4's `casting`
#    (PRG $00460B, slot 2) is
#
#        A2 75 A9 8E 86 27 A2 00 A0 01 20 78 F8
#
#    `ldx #$75 / lda #$8E / stx $27` writes $8E75 into `ma`, then `ldx #$00 /
#    ldy #$01 / jsr $F878`. `farjsr67` ends in `jmp ($0027)`. **So Beta 1 calls
#    `firespell` at $8E75 with R6=0 and R7=1.** $8E75 is in register 6's window.
#    Reading R7=1 as "X2 is at $A000" is the step that was wrong: the same call
#    makes slot 0 visible at $8000 *and* slot 1 at $A000, and X2 has bytes in both.
#
# 3. **$8E75 is `firespell`.** Beta 1's slot 0 offset $0E75 carries X2's own
#    opening: `jmp $F284` where X2.PDS:25 says `jmp upmana`, `jmp $F0E9` where
#    X2.PDS:27 says `jmp miscmsg`, and then the 27 bytes of `spelmana`
#    (`X2.PDS:33-36`: twenty-five $01 then two $00). That is four independent
#    agreements, and it is the same address step 2 read out of the caller.
#
# 4. **Slot 1, offset 0 is X2's `$A000` half.** X2.PDS:467-469 is
#
#        if *<$a000 / ds $a000-*,$0 / endif
#        M00  HEX 0B4EB7FA03937F1AB57F12AC ...
#
#    so X2 pads to $A000 and continues. Beta 1's slot 1 offset $0000 is
#    `0B 4E B7 FA 03 93 7F 1A B5 7F 12 AC 0E 5F 9C 9B ...` -- `M00`, byte for
#    byte, at the very first byte of the slot. And `decompchr` (`X5.PDS:27-31`)
#    reaches for these tables with `fbnk 7,#1,n`, banks slot 1 into $A000, and
#    reads `CODESL` at $B8AA -- which is slot 1 offset $18AA, and holds
#    `06 01 0B 09 08 06 01 00` exactly as `X2.PDS:486` writes it. Same slot, from
#    the code's own bank switch and from the table's own bytes.
#
# 5. **Neither window is $C000.** X2's last byte is $B8E2, because `firespell` at
#    $8E75 plus the 10 861 bytes the assembler emits lands there. $C000 is absent
#    from the map on purpose: like X4's, a window the map does not mention is
#    recorded in `overflow` and reported rather than filed somewhere plausible.
X2_WINDOW_SLOTS = {0x8000: 0, 0xA000: 1}

# The same map, keyed by module, for the search pass and the project pass.
MODULE_WINDOW_SLOTS = {"X4.PDS": X4_WINDOW_SLOTS, "X2.PDS": X2_WINDOW_SLOTS}

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
# These two are **placeholders**. Slot 15 has 8 KiB and three modules want to be in
# it, and the split between them is fully determined once two things are measured:
# where X7 has to start (from the target cartridge's reset vector) and how long X6
# is. Both are measured on the first pass and applied on the second, in
# `assemble_prg`; see DERIVED_CEILINGS there. Nothing here is a judgement about
# which code matters any more.
#
# What the numbers used to be, and why they were wrong, is worth keeping:
#
#     X5_CEILING = $E605   X6_CEILING = $F166   (both release-derived)
#
# $F166 was X7's start under the *release's* reset vector. Re-anchored to Beta 1
# the cartridge's reset is $F930, X7's start is $F0D5, and leaving $F166 in place
# meant X6 kept writing 145 bytes ($F0D5-$F165) that X7 then overwrote -- not a
# visible fault, because the overwritten bytes were X7's and X7's are right, but
# the build was doing work and then throwing it away and reporting 150 dropped
# bytes instead of the 295 that are actually unavailable.
#
# The `initcols` evidence, which is what fixes the split rather than merely
# tidying it: `reset` (now byte-identical to Beta 1 for 30 of its first 31
# instructions) does `jsr $EEC7` there and `jsr $EFEE` here -- a displacement of
# -295. Since `initcols` sits $9E9 into X6, X6 has to start 295 bytes lower than it
# does, which puts its natural end at exactly one byte below X7's new start:
#
#     $F0D5 - 3063 = $E4DE      initcols = $E4DE + $9E9 = $EEC7   <- Beta 1's
#
# So $E4DE and $F0D5 are not chosen, they are what the two measurements give.
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
#   X2  no `org`, and `firespell` (X2.PDS:9, its own first code) is reached by
#       `farjsr67` with R6=0,R7=1. What that pair of registers makes addressable is
#       *both* windows at once, not "the $A000 window": `farjsr67` banks X into
#       register 6 and Y into register 7, and X2 has bytes in each. So X2 is
#       chained onto X1 in slot 0, and its $A000 half is filed by
#       `X2_WINDOW_SLOTS` rather than by where the chain started. See that table
#       for the measurement; this entry is only where the *address* comes from.
CHAINED = ["X1.PDS", "X2.PDS"]

# Placement for the modules whose slot is neither measured nor provable, used
# when --assume-banks is on (the default). This is a *hypothesis* and is labelled
# as one in the output. The order was NOT tuned to raise the byte-match
# percentage: per-module byte scoring of X0-X5 is at or below chance at every
# one of the sixteen slots (0.3-2.1% against a 0.39% chance level), so that
# number carries no signal about placement and optimising against it would be
# fitting noise.
ASSUMED_SLOTS = {
    "X0.PDS": 0, "X1.PDS": 1, "X2.PDS": 0, "X3.PDS": 4,
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
# X2 is NOT in this table, and its absence is the point. It used to say
# `$A000 from X4.PDS:452-455: farjsr67(X=$00,Y=$01) to firespell, so X2 is in
# register 7's window`, and the reasoning was sound right up to its last step.
# `farjsr67` (`X7.PDS:828-835`) really does load X into register 6 ($8000) and Y
# into register 7 ($A000) -- that is measured, see `X2_WINDOW_SLOTS` -- but it
# does both, and the one call site names a target in register 6's window while
# passing both registers. So "R7=1" says what is in $A000 during the call; it
# does not say where `firespell` is. `firespell` is at $8E75, in register 6's
# window, and the $A000 half of X2 is what slot 1 holds. That is two facts about
# two windows, and a single `origin` can only state one of them.
#
# The remaining entry is X6's, which really is an origin: no `org`, chained in
# name only, and its address has to come from somewhere.
MODULE_ORIGINS: dict[str, int] = {"X6.PDS": X5_CEILING}

ORIGIN_WHY = {
    # $E605, not $E77C: this string said $E77C for several builds and that
    # value appears nowhere else in the tree. It is X5_CEILING, and the reason
    # is the ceiling comment above.
    "X6.PDS": "= X5_CEILING, and both are derived: X7's start comes from the "
              "target cartridge's reset vector, X6's length is measured with no "
              "ceiling applied, and X6 fills exactly the gap between them. Under "
              "beta1 that is $E4DE..$F0D4 with X7 at $F0D5, which puts "
              "`initcols` at $EEC7 -- the address `reset` calls. See "
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
                  ours: bool = True, asm_cls=None
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
    asm = cls(image, SRC, [SRC, SRC_MAG, SRC_MAG_OUT], verbose=verbose)
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
    # Filled in on the first pass from two measurements, used on the second. See
    # DERIVED_CEILINGS at X5_CEILING.
    derived: dict[str, int] | None = None
    project_error: Exception | None = None
    for attempt in range(2 if x7_vector else 1):
        image = bytearray(PRG_SIZE)
        asm = cls(image, SRC, [SRC, SRC_MAG, SRC_MAG_OUT], verbose=verbose)
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
        # Ours go last, and deliberately so. Every symbol Eurocom's tree defines
        # is known by the time we are assembled, so a module of ours can name
        # `titdat`, `dotitle`, a macro, anything -- and nothing of ours can
        # perturb a vendor module's own layout, because nothing of ours has run
        # yet when the vendor modules are placed.
        if ours:
            placed += [(m, OUR_MODULES[m].get("slot")) for m in our_modules()]
        origins = {"SEQ.SRC": SEQ_ORIGIN} if seq_src else {}
        origins.update(MODULE_ORIGINS)
        for m in our_modules():
            if OUR_MODULES[m].get("origin") is not None:
                origins[m] = OUR_MODULES[m]["origin"]
        window_slots = {"SEQ.SRC": SEQ_WINDOW_SLOTS} if seq_src else {}
        # `MODULE_WINDOW_SLOTS`, not a hand-written `X4_WINDOW_SLOTS` line. The
        # pass-1 search was given the whole table and this pass was given one
        # entry from it, so a module added to the table was silently placed by
        # the search and placed differently by the project -- and the project is
        # the one whose bytes are in the ROM. X2 was the casualty: it ran here
        # with an empty map and `self.slot` 0, so its `$A000` half was filed at
        # slot 0 offset $18AA instead of slot 1, which is a hole X4's `$A000`
        # half then filled. Every label resolved and the build printed 73.6%.
        window_slots.update(MODULE_WINDOW_SLOTS)
        for m in our_modules():
            if OUR_MODULES[m].get("window_slots") is not None:
                window_slots[m] = OUR_MODULES[m]["window_slots"]
        # The measurement pass runs with **no ceilings at all**. `ceiling_drops`
        # works by discarding what a module assembles past its ceiling, so with a
        # ceiling in place X6's recorded footprint is the truncated one and its
        # true length is not observable -- which is the length the whole slot-15
        # split is computed from. On the second pass the derived values are used.
        ceilings: dict[str, int] = ({} if derived is None
                                    else dict(derived))
        for m in our_modules():
            if OUR_MODULES[m].get("ceiling") is not None:
                ceilings[m] = OUR_MODULES[m]["ceiling"]
        # Deliberately no ceiling for our modules. A ceiling *drops* bytes past it
        # and counts them, which for a module that is supposed to overwrite a
        # label would mean silently emitting half a scene. The space our scene
        # has to fit in is checked before assembly, by size, with the neighbour's
        # address in the message -- see pack_our_scenes().
        if x7_base is not None:
            origins["X7.PDS"] = x7_base
        if derived is not None:
            # X6 starts exactly where X5's spill is cut off, and that point is
            # derived, not the literal $E605 the module table carries.
            origins["X6.PDS"] = derived["X5.PDS"]
        try:
            # Our modules live in src/magician/, not beside Eurocom's. Resolving
            # every name against SRC/ found vendor's directory and reported a
            # missing file for a module that exists -- the build was right to
            # refuse, and the fix is to ask the manifest where each module is.
            paths = [((SRC_MAG if m in OUR_MODULES else SRC) / m) for m, _ in placed]
            for p in paths:
                if not p.is_file():
                    raise FileNotFoundError(f"{p} is in the build's module list but "
                                            f"is not there")
            asm.run_all(paths, [s for _, s in placed],
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
            # --- the slot-15 split, derived -------------------------------------
            # X6 has no `org`, so it starts where X5's spill is cut, and X5's
            # spill is cut wherever X6 starts. X7's start is fixed by the
            # cartridge's reset vector. So the only free quantity is X6's length,
            # and it is a measurement: on this pass there were no ceilings, so
            # what X6 emitted is its natural extent, untruncated.
            x6 = asm.writes_by_file.get("X6.PDS") or ()
            if not x6:
                log.append("         *** X6 emitted nothing, so the slot-15 split "
                           "cannot be derived; keeping the literal ceilings. ***")
            else:
                x6_len = max(x6) - min(x6) + 1
                x6_base = x7_base - x6_len
                derived = {"X5.PDS": x6_base, "X6.PDS": x7_base}
                log.append(f"X6.PDS:  emitted ${min(x6):04X}-${max(x6):04X}, "
                           f"{x6_len} bytes with no ceiling applied, so that is")
                log.append(f"         its natural length. X7 starts at "
                           f"${x7_base:04X}, so X6 starts at")
                log.append(f"         ${x7_base:04X} - ${x6_len} = ${x6_base:04X} "
                           f"and ends at ${x7_base - 1:04X}, immediately below it.")
                log.append(f"         X5_CEILING = ${x6_base:04X}, "
                           f"X6_CEILING = ${x7_base:04X} (both derived; the "
                           f"literals were")
                log.append(f"         ${X5_CEILING:04X} and ${X6_CEILING:04X}, "
                           f"which were release-derived).")
                # The prediction is the whole point of doing this by
                # measurement: `reset`'s `jsr initcols` encodes the address, so
                # there is an independent check on the arithmetic. It has to be
                # done in one address space -- `writes_by_file` holds PRG *file*
                # offsets and `asm.sym` holds CPU addresses, and subtracting one
                # from the other printed a nonsense "$-1139" until it was fixed.
                for want_label in ("initcols",):
                    got = asm.sym.get(want_label)
                    if got is None:
                        continue
                    got_file = got + 0x10000        # slot 15 -> PRG file offset
                    if not min(x6) <= got_file <= max(x6):
                        continue
                    moved = x6_base + (got_file - min(x6))
                    log.append(f"         Prediction: {want_label} moves "
                               f"${got:04X} -> ${moved:04X}.")
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

    # What our own modules contributed, counted from the writes themselves
    # rather than asserted. A module of ours overlays a label Eurocom's tree
    # already emitted, so "did it build" is not the same question as "did it
    # change anything", and a module that assembled to zero bytes while still
    # resolving every symbol would look like success.
    if ours:
        log.append("")
        log.append("OUR SOURCE (src/magician/), %d module(s):" % len(our_modules()))
        for m in our_modules():
            offs = sorted(asm.writes_by_file.get(m, ()))
            span = (f"${offs[0]:04X}-${offs[-1]:04X}" if offs else "nothing")
            log.append(f"  {m:<14} {len(offs):5d} byte(s) written  {span}  "
                       f"[{OUT.name}/ours] {OUR_MODULES[m].get('note', '')}")
        if not any(asm.writes_by_file.get(m) for m in our_modules()):
            log.append("  *** none of them emitted a byte. The wiring is live and "
                       "the image is")
            log.append("      byte-identical to a build without it, which is the "
                       "point at which")
            log.append("      the wiring can be trusted to change something. ***")

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


def pack_our_scenes(log: list[str]) -> bytes:
    """Pack src/magician/'s readable scene sources into .DAT files to assemble.

    The editable artefact is the tile grid, not the packed stream: Eurocom's
    cruncher is gone and the format's minimum run is four bytes, so hand-editing
    a packed scene is not a thing anyone should do. So the grid in
    `src/magician/title/` is the source, tools/datcodec.py packs it, and the
    packed bytes land in `asm/out/ours/` for TITLE.SRC to `incbin`. Nothing
    packed is committed, so there is exactly one copy of the title screen and it
    is the readable one.

    Whether the result *fits* is checked in verify_our_scenes(), against the
    assembled image -- the space available is `pwdat - titdat` in the symbol
    table the assembler actually resolved, and that is not known until after the
    tree has been assembled.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import datcodec  # noqa: PLC0415  -- only needed when our scenes are packed

    SRC_MAG_OUT.mkdir(parents=True, exist_ok=True)
    title = SRC_MAG / "title"
    tiles = _read_grid(title / "nametable.txt", 32, 30, "nametable")
    attrs = _read_grid(title / "attributes.txt", 16, 4, "attributes")
    scene = bytes(tiles) + bytes(attrs)
    if len(scene) != datcodec.NAMETABLE + datcodec.ATTRS:
        raise SystemExit(f"src/magician/title/ is {len(scene)} bytes; a full scene is "
                         f"{datcodec.NAMETABLE + datcodec.ATTRS} "
                         f"({datcodec.NAMETABLE} tile + {datcodec.ATTRS} attribute)")
    token = _read_int(title / "token.txt", 0xE0)
    packed = datcodec.encode(scene, token)
    # Read back what we are about to hand the assembler. The encoder verifies
    # this internally, but the check belongs next to the thing that depends on it.
    if datcodec.decode(packed) != scene:
        raise SystemExit("the packed title scene does not decode back to the grid "
                         "src/magician/title/ describes")
    check_scene_against_art(scene, log)
    (SRC_MAG_OUT / "TIT.DAT").write_bytes(packed)
    log.append(f"src/magician/title/: {len(scene)} scene bytes -> {len(packed)} packed "
               f"(token ${token:02X}) -> {(SRC_MAG_OUT / 'TIT.DAT').relative_to(ROOT)}")
    return packed


def verify_our_scenes(asm, prg: bytearray, packed: bytes, log: list[str]) -> None:
    """Check our scene against the image the build actually produced.

    Three things can go wrong that packing cannot see, and all three are silent:

      * the scene does not fit between `titdat` and `pwdat`, and its tail has
        overwritten the password screen's data;
      * it landed at the wrong file offset, because the $A000 window's slot was
        not the one we told the assembler;
      * the bytes at `titdat` are not the bytes we packed, because something
        wrote over them afterwards.

    So this reads the bytes back out of the finished image at the offsets our
    own module is recorded as having written, and decodes them. If the image does
    not contain our scene, the build says so rather than shipping a ROM whose
    title screen is somebody else's.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import datcodec  # noqa: PLC0415

    for m in our_modules():
        offs = sorted(asm.writes_by_file.get(m, ()))
        if not offs:
            log.append(f"  *** {m} emitted no bytes; nothing to verify ***")
            continue
        expect = OUR_MODULES[m].get("expect")
        if expect:
            # A data overlay, not a scene: check the finished image byte for
            # byte against the values the manifest names, at the offsets this
            # module is recorded as having written.
            want = bytes.fromhex("".join(expect.values()))
            if len(offs) != len(want):
                raise SystemExit(
                    f"{m}: the manifest names {len(want)} byte(s) but the module "
                    f"wrote {len(offs)} (${offs[0]:05X}-${offs[-1]:05X}). The "
                    f"overlay's size and its declaration have to agree; a "
                    f"one-byte value that quietly became two is a different "
                    f"memory layout, not a typo.")
            for i, (symname, hexv) in enumerate(expect.items()):
                addr = asm.sym.get(symname)
                if addr is None:
                    raise SystemExit(
                        f"{m}: the manifest expects a byte at `{symname}` but the "
                        f"build resolved no such symbol. An overlay that orgs an "
                        f"address the vendor tree does not define would go "
                        f"somewhere the build cannot name.")
                off = offs[i]
                got = prg[off]
                if got != int(hexv, 16):
                    raise SystemExit(
                        f"{m}: the image holds ${got:02X} at ${addr:04X} "
                        f"(PRG ${off:05X}) and the manifest says ${hexv.upper()}. "
                        f"The overlay was assembled and then something wrote over "
                        f"it, or it did not land where it says it did.")
                log.append(f"  {m}: `${symname}` = ${addr:04X} (PRG ${off:05X}) is "
                           f"${got:02X} in the image, as declared")
            continue
        first, last = offs[0], offs[-1]
        if first != offs[-1] - len(packed) + 1:
            log.append(f"  *** {m} wrote {len(offs)} byte(s) that are not "
                       f"{len(packed)} contiguous from ${first:04X} ***")
        got = bytes(prg[first:first + len(packed)])
        if got != packed:
            log.append(f"  *** {m}: the image at ${first:04X} is not the scene we "
                       f"packed ***")
            continue
        log.append(f"  {m}: ${first:04X}-${last:04X} in the image is byte-for-byte "
                   f"the scene src/magician/title/ describes")

    titdat, pwdat = asm.sym.get("titdat"), asm.sym.get("pwdat")
    if titdat is None or pwdat is None:
        raise SystemExit("src/magician/TITLE.SRC overlays `titdat`, but the build "
                         "resolved no `titdat`/`pwdat`; refusing to guess the space")
    budget = pwdat - titdat
    if len(packed) > budget:
        raise SystemExit(
            f"the packed title scene is {len(packed)} bytes and only {budget} fit "
            f"between `titdat` (${titdat:04X}) and `pwdat` (${pwdat:04X}).\n"
            f"  Its last {len(packed) - budget} byte(s) overwrite the password "
            f"screen's data.\n"
            f"  Eurocom's own TIT.DAT is {budget} bytes for the same 1024-byte "
            f"scene, so this one packs worse than\n  theirs; the usual cause is a "
            f"noisy tile grid that will not run-length.")
    log.append(f"  scene fits: {len(packed)} of the {budget} bytes between `titdat` "
               f"(${titdat:04X}) and `pwdat` (${pwdat:04X})")

    # And the strongest statement available: the finished image, decoded through
    # the format, is the grid in src/magician/.
    title = SRC_MAG / "title"
    scene = bytes(_read_grid(title / "nametable.txt", 32, 30, "nametable")) + \
        bytes(_read_grid(title / "attributes.txt", 16, 4, "attributes"))
    start = sorted(asm.writes_by_file.get("TITLE.SRC", ()))[0]
    rebuilt = datcodec.decode(bytes(prg[start:start + len(packed)]))
    if rebuilt != scene:
        raise SystemExit("decoding the assembled ROM's titdat region does not give "
                         "back src/magician/title/. The title screen in this ROM is "
                         "not ours.")
    log.append(f"  round trip through the image: {len(rebuilt)} bytes decode back to "
               f"the grid in src/magician/title/")


def _read_grid(path: pathlib.Path, width: int, height: int, what: str) -> list[int]:
    """Read a fixed-width hex grid. Blank lines and `;` comments are ignored."""
    if not path.is_file():
        raise SystemExit(f"missing {path.relative_to(ROOT)}: our {what} source")
    rows: list[list[int]] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        vals = [int(tok, 16) for tok in line.replace(",", " ").split()]
        if len(vals) != width:
            raise SystemExit(f"{path.relative_to(ROOT)}:{lineno}: {len(vals)} value(s), "
                             f"expected {width} ({what} row)")
        rows.append(vals)
    if len(rows) != height:
        raise SystemExit(f"{path.relative_to(ROOT)}: {len(rows)} row(s), expected "
                         f"{height} ({what})")
    return [v for row in rows for v in row]


def _read_int(path: pathlib.Path, default: int) -> int:
    if not path.is_file():
        return default
    text = path.read_text().split(";", 1)[0].strip().lstrip("$")
    return int(text, 16) if text else default


def check_scene_against_art(scene: bytes, log: list[str]) -> None:
    """Fail the build on a tile the scene names but cannot address.

    A scene descriptor holds 8-bit tile indices. `dotitle` selects the pattern
    table with `lda #s0e / sta mapbnk0` (X0.PDS:629) and the three title CHR
    banks are placed contiguously at 4 KiB banks 14, 15 and 16 (TIT0/1/2.CHR at
    chr.bin $E000/$F000/$10000), so the base bank alone answers all 256 indices
    and the upper two are reached by switching, not by a wider index. An index
    above $FF therefore cannot be addressed at all.

    The failure this guards is silent: the PPU does not care what a nametable
    byte means, it fetches pattern-table address $index*16 and draws whatever is
    there. The screen comes up and the logo is wrong, and nothing says so.

    Attributes get no range check here because there is nothing to check. A
    background attribute byte is four 2-bit quadrant selectors, so every one of
    its 256 values names one of the four sub-palettes -- which is exactly what
    TIT.PAL's 12 bytes (4 sub-palettes x 3 colours) define. An earlier version of
    this function compared the low six bits against TIT.PAL's length and failed
    20 of Eurocom's own attribute bytes, which is what a range check that cannot
    fail is worth. tools/titletest.py checks the attributes where there is
    something to say: that the screen is not attribute-uniform, and that the
    palettes it selects are the ones loaded at boot.
    """
    tiles = scene[:0x3C0]
    bad = sorted({v for v in tiles if v > 0xFF})
    if bad:
        raise SystemExit(
            "src/magician/title/nametable.txt names tile(s) outside $00-$FF: "
            + ", ".join("$%02X" % v for v in bad)
            + "\n  A scene descriptor holds 8-bit tile indices, so these cannot be "
              "addressed at all\n  and would draw as pattern-table garbage.")
    art = (SRC / "DAT" / "TIT0.CHR")
    blanks = set()
    if art.is_file():
        data = art.read_bytes()
        blanks = {i for i in range(256) if not any(data[i * 8:(i + 1) * 8])}
    used = {v for v in tiles}
    named_blank = sorted(used & blanks)
    log.append(f"src/magician/title/ checked: {len(used)} distinct tiles, all < $100; "
               f"{len(named_blank)} of them are blank tiles in TIT0.CHR "
               f"(a backdrop is legitimate -- tools/titletest.py is the gate)")


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
                    default=patches.cart_path(patches.DEFAULT_CART),
                    help="which dump to build against and measure against. "
                         f"Defaults to the registry's target, {patches.DEFAULT_CART!r} "
                         f"-- see asm/patches.py's CARTS. This is Beta 1, the build "
                         "this source came from, and not the release.")
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
    ap.add_argument("--no-ours", action="store_true",
                    help="do not assemble src/magician/. Exists so the wiring can "
                         "be shown to be inert: with our modules contributing "
                         "nothing the PRG must be byte-identical to a build "
                         "without the flag, which is the only way to know the "
                         "wiring itself changed nothing.")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="exit 0 even though some modules have no determined slot")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    cart_prg, cart_chr = read_cart(args.cart)

    ours = not args.no_ours
    problems = check_our_manifest() if ours else []
    if problems:
        for p in problems:
            print(f"build: {p}", file=sys.stderr)
        raise SystemExit("src/magician/ and asm/build.py's OUR_MODULES disagree; "
                         "fix the manifest rather than letting the build guess")

    # Packed before assembly, because TITLE.SRC incbins the result. Whether the
    # result *fits* is checked after, against the assembled image.
    prep_log: list[str] = []
    packed = pack_our_scenes(prep_log) if ours else b""

    prg, asm, prg_log, unplaced = assemble_prg(
        cart_prg, args.verbose,
        x7_table=(args.x7_level_table == "on"),
        assume_banks=not args.no_assume_banks,
        bank_groups=(args.x7_bank_groups == "on"),
        seq_src=(args.seq_src == "on"),
        x7_vector=(args.x7_vector == "on"),
        split_banks=(args.x7_bank_split == "on"),
        ours=ours)
    if ours:
        prg_log = prep_log + prg_log
        verify_our_scenes(asm, prg, packed, prg_log)
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
            if not regions:
                # Nothing to fill. `apply` still runs so the Report exists and the
                # "0 from the patch manifest" line below is measured rather than
                # assumed -- there is no image to measure against, so it is passed
                # as empty and nothing is copied.
                patch_report = patches.apply(prg, b"", regions, args.cart_dir)
            else:
                cart_for_regions = (prgs[regions[0].cart] if len(prgs) == 1
                                    else patches._mixed(prgs, regions))
                patch_report = patches.apply(prg, cart_for_regions, regions,
                                             args.cart_dir)
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
    print("  With X0=0, X2-head=0, X2-tail=1, X4=2, X3=4, SEQ=5, X5=14, X7=15 and "
          "x7's own data owning")
    print("  6..13, only slot 3 is unused. Note X2 is in two slots: its head is "
          "chained into slot 0")
    print("  and its `$A000` half is slot 1, so the release layout is NOT a "
          "bijection over the")
    print("  eight files. Do not read the byte-match percentage as evidence "
          "about this; it is at chance for code.")
    print("  Per-module scoring and the placement table are in GAPMAP.md.")
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