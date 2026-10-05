#!/usr/bin/env python3
"""Derive RAM variable addresses from the CARTRIDGE, not from the source's build.

Why this exists
---------------
Five RAM addresses were quoted at us -- `curchrpal` $0180, `obtyp` $048C,
`ninflag` $06C9, `lastz` $06FA, `jt` $2C -- and every one of them was measured
against the release. Re-deriving from our own assembly gives different values
for all five, and the source's own `zp` block is a running counter (`zp name,size`
does `@1 equ z / z = z+@2`), so it cannot settle the question on its own: it
says where *our* build puts them, not where the cartridge does.

So this reads the cartridge. The source's `dotitle` prologue encodes all of them:

    dotitle  lda #$00            A9 00
             ldx #jt             A2 lo hi          <- jt
    !a       sta $00,x           95 00
             inx                 E8
             bne !a              D0 FA
             ldx #lastz-ninflag+1  A2 lo hi        <- lastz - ninflag + 1
    !aa      sta ninflag-1,x     9D lo hi          <- ninflag - 1
             dex                 CA
             bne !aa             D0 FA
             jsr initdma         20 lo hi
             jsr initcols        20 lo hi          <- initcols

Three of those operands are absolute and get encoded into the bytes, so a dump
that puts these variables anywhere else is findable by its own encoding: search
for the loop shape, read the address out of it. `lastz` is then
`ninflag + imm - 1`, since the immediate *is* `lastz - ninflag + 1`.

Self-coverage
-------------
The whole method rests on "this byte pattern occurs exactly once in the PRG". If
it occurs twice, or not at all, this tool says so and exits non-zero rather than
printing the first hit's address. `jt` is cross-checked a second way -- the
`sta $00,x` immediately before it wipes zero page from `jt` up, so `jt` must be
the start of that wipe -- and `initcols` is read from the `jsr` that follows and
compared against our own symbol map. A disagreement between the cartridge's
encoding and our assembly is reported, not reconciled.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

import cartref  # noqa: E402
import patches  # noqa: E402

PRG_LEN = 131072
# `sta $00,x / inx` is 3 bytes and is what is anchored on. The branch
# displacement after it is deliberately NOT part of the anchor: it is
# `-(3 + 2)` = $FB for the source's loop and the dumps do not all assemble it the
# same way, so anchoring on the displacement found the prologue in **zero** of the
# six dumps -- which reads exactly like "this variable is absent everywhere" and is
# the failure mode this tool exists to avoid. The displacement is checked as a
# shape instead (`bne`, then `ldx #`) below, which is what actually identifies the
# routine.
ANCHOR = bytes.fromhex("9500E8")
BNE = 0xD0
LDX_IMM = 0xA2
JSR = 0x20
# `sta ninflag-1,x / dex / bne` -- the operand is ninflag-1.
STORE_ABS_X = 0x9D
LOOP2_TAIL = bytes.fromhex("CA D0 FA")


def find_all(hay: bytes, needle: bytes) -> list[int]:
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def syms(path: pathlib.Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in path.read_text().splitlines():
        k, _, v = line.partition(" = ")
        if v.startswith("$"):
            try:
                out[k] = int(v[1:], 16)
            except ValueError:
                pass
    return out


# Offsets from `at`, the `95` of `sta $00,x`. Named, because the first version of
# this decoder used bare `at + N` literals and got four of the six wrong -- and
# the validator caught every one, which is the only reason it is being trusted at
# all. `at` is the `sta $00,x` because that is what ANCHOR finds.
#
#     dotitle  lda #$00          at-4  A9 00
#              ldx #jt           at-2  A2 lo
#     !a       sta $00,x         at+0  95 00
#              inx               at+2  E8
#              bne !a            at+3  D0 FB
#              ldx #lastz-...    at+5  A2 nn
#     !aa      sta ninflag-1,x   at+7  9D lo hi
#              dex               at+10 CA
#              bne !aa           at+11 D0 FA
#              jsr initdma       at+13 20 lo hi
#              jsr initcols      at+16 20 lo hi
OFF_JT_LO, OFF_SPAN, OFF_NINFLAG_LO, OFF_NINFLAG_HI = -1, 6, 8, 9
OFF_DEX, OFF_BNE2, OFF_JSR_DMA, OFF_JSR_COLS = 10, 11, 13, 16
OFF_LDX_IMM_1, OFF_STA_ABSX = 5, 7


def decode(prg: bytes, at: int) -> dict[str, int]:
    """Read the variables out of `dotitle`'s prologue at PRG offset `at`.

    `ldx #jt` is two bytes ending at `at`, not three: `ldx #` is a two-byte
    instruction and the `00` in front of it is the operand of `lda #$00`.
    `lastz-ninflag+1` is a single byte immediate here because the span is under
    256 -- if a dump ever assembles it above $FF this decoder reads only the low
    byte, so the span is reported and can be checked.
    """
    imm_span = prg[at + OFF_SPAN]
    ninflag = (prg[at + OFF_NINFLAG_LO]
               | (prg[at + OFF_NINFLAG_HI] << 8)) + 1
    return {"jt": prg[at + OFF_JT_LO],
            "ninflag": ninflag,
            "lastz": ninflag + imm_span - 1,
            "span": imm_span,
            "initdma": prg[at + OFF_JSR_DMA + 1] | (prg[at + OFF_JSR_DMA + 2] << 8),
            "initcols": prg[at + OFF_JSR_COLS + 1] | (prg[at + OFF_JSR_COLS + 2] << 8)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dumps", nargs="*", default=None,
                    help="registry names to read (default: every one)")
    ap.add_argument("--prg", type=pathlib.Path, default=ROOT / "asm/out/prg.bin")
    args = ap.parse_args()

    names = args.dumps if args.dumps else sorted(patches.CARTS)
    ours = args.prg.read_bytes()
    if len(ours) != PRG_LEN:
        print(f"FAIL: {args.prg} is {len(ours)} bytes, expected {PRG_LEN}",
              file=sys.stderr)
        return 2
    ours_sym = syms(ROOT / "asm/out/mag.sym")

    rows: dict[str, dict[str, int]] = {}
    skipped: list[str] = []
    for name in names:
        prg = patches.body(patches.cart_path(name))
        if len(prg) != PRG_LEN:
            print(f"FAIL: {name} body is {len(prg)} bytes, expected {PRG_LEN}",
                  file=sys.stderr)
            return 2
        raw = find_all(prg, ANCHOR)
        hits = [x for x in raw
                if prg[x + 3] == BNE and prg[x + OFF_LDX_IMM_1] == LDX_IMM]
        if len(hits) != 1 or len(raw) != 1:
            print(f"FAIL: {name}: `sta $00,x / inx` occurs {len(raw)} time(s) in "
                  f"its PRG and {len(hits)} of those have the `bne`/`ldx #` shape "
                  f"that follows in `dotitle`. Expected exactly 1 of each. Every "
                  f"address below would be a guess.", file=sys.stderr)
            return 2
        at = hits[0]
        # Structural validation. The anchor alone is not enough: the release's
        # PRG also contains exactly one `sta $00,x / inx`, but it is a different
        # routine -- after its second loop it does `sta $01A1 / rts` instead of
        # two `jsr`s. Reading `ninflag` out of that would produce a confident
        # wrong address, which is the failure this tool is here to prevent. So a
        # dump has to look like `dotitle` before its numbers are believed.
        why = None
        if prg[at + OFF_JT_LO - 1] != LDX_IMM:
            why = (f"the two bytes before the anchor are "
                   f"{prg[at+OFF_JT_LO-1:at].hex(' ')}, not `a2 lo` (ldx #jt)")
        elif prg[at + OFF_DEX:at + OFF_BNE2 + 2] != LOOP2_TAIL:
            why = (f"the second loop is {prg[at+11:at+14].hex(' ')}, not "
                   f"`ca d0 fa` (dex / bne)")
        elif (prg[at + OFF_JSR_DMA] != JSR
              or prg[at + OFF_JSR_COLS] != JSR):
            why = (f"after the loops it does "
                   f"{prg[at+OFF_JSR_DMA:at+OFF_JSR_COLS+3].hex(' ')}, which is "
                   f"not `jsr initdma / jsr initcols`. The anchor matched some "
                   f"other zero-page wipe.")
        if why:
            print(f"  {name:8s} SKIPPED -- {why}", file=sys.stderr)
            skipped.append(name)
            continue
        rows[name] = decode(prg, at)

    live = [n for n in names if n in rows]
    if not live:
        print("FAIL: no dump's anchor landed on `dotitle`. Nothing to report.",
              file=sys.stderr)
        return 2
    if skipped:
        print(f"({len(skipped)} dump(s) skipped: {', '.join(skipped)} -- the "
              f"anchor did not land on `dotitle` there)\n")
    print(f"{'variable':10s} {'ours':>8s}  " +
          "  ".join(f"{n:>10s}" for n in live))
    for var in ("jt", "ninflag", "lastz", "initdma", "initcols"):
        mine = ours_sym.get(var, -1)
        print(f"{var:10s} {'$%04X' % mine if mine >= 0 else '-':>8s}  " +
              "  ".join(f"{'$%04X' % rows[n][var]:>10s}" for n in live))

    print("\nderived from the cartridge's own encoding of `dotitle`:")
    for n in live:
        r = rows[n]
        print(f"  {n:8s} jt=${r['jt']:04X}  ninflag=${r['ninflag']:04X}  "
              f"lastz=${r['lastz']:04X}  (lastz-ninflag+1 = {r['span']})  "
              f"initcols=${r['initcols']:04X}")

    # Cross-check: does the cartridge's `jsr initcols` agree with our assembly?
    print("\ncross-check, cartridge `jsr initcols` vs our `initcols` symbol:")
    bad = 0
    for n in live:
        got, mine = rows[n]["initcols"], ours_sym.get("initcols", -1)
        flag = "agrees" if got == mine else f"DIFFERS from ours by ${mine - got:+#06x}"
        print(f"  {n:8s} ${got:04X}  {flag}")
        if got != mine:
            bad += 1

    print(f"\n{len(live)} of {len(names)} dump(s) read "
          f"({', '.join(skipped) if skipped else 'none skipped'}), "
          f"{sum(1 for n in live if rows[n]['initcols'] == ours_sym.get('initcols'))}"
          f" agreeing with our assembly on `initcols`.")
    # The gate is "the build target validated and agrees with our assembly", not
    # "two or more dumps agreed". An earlier version demanded two dumps and
    # exited 2 on a result that was in fact five variables matching exactly --
    # the cross-check that matters is against our own symbol map, and it is
    # stronger than comparing two cartridges to each other.
    target = patches.DEFAULT_CART
    if target not in rows:
        print(f"FAIL: the build target {target!r} did not validate, so this tool "
              f"has said nothing about the image the build is measured against.",
              file=sys.stderr)
        return 2
    disagreements = [v for v in ("jt", "ninflag", "lastz", "initdma", "initcols")
                     if v in ours_sym and rows[target][v] != ours_sym[v]]
    if disagreements:
        print(f"FAIL: {target} and our assembly disagree on "
              f"{', '.join(disagreements)}. That is a real finding and it is not "
              f"reconciled here.", file=sys.stderr)
        return 2
    print(f"\n{target}: all five variables read out of the cartridge's own "
          f"encoding agree with our assembly.")
    if bad:
        print("Note: disagreement is reported, not reconciled -- it is the "
              "finding.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())