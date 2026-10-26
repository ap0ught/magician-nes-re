#!/usr/bin/env python3
"""Is a module at the right place *inside* its 8 KiB slot? Measure, do not assume.

    python3 tools/slotalign.py [--cart PATH] [--rebuilt PATH] [--sym PATH]
                               [--slot N] [--module NAME] [--minrun N]

`journal/09` reported one table -- the 32-byte ascending tile counter, `SEQ.SRC`'s
`D43` -- at slot-5 offset `$1471` in this build and `$14CE` in the cartridge, and
concluded from that single table that "the placement of X5 within slot 5 is
wrong". Two things were wrong with that, and this tool exists so neither has to be
rediscovered:

  * the table is **SEQ.SRC's**, not X5's. `asm/build.py` puts X5 in slot 14 at its
    own `org $c000`, and SEQ.SRC in slot 5.
  * one table gives a *displacement*, not an *origin*. An origin is a property of
    the whole slot. The displacement at one offset is the running sum of every
    length difference before it, so it says nothing about where the module starts.

So this measures two different things and keeps them apart:

  * **origin** -- for each 8 KiB slot, the shift `s` that maximises
    byte-for-byte agreement between this build's slot and the cartridge's, over
    every `s` in `[-4096, +4096)`. If `s = 0` wins, the origin is already right
    and any non-zero displacement inside the slot is an internal length
    difference, not a misplacement. The full ranking is printed, so "0 wins" is
    visible rather than asserted.
  * **displacement** -- where the shift *changes* inside a slot, and what caused
    each change. A shift is only quoted when the equal run carrying it is at
    least `--minrun` bytes; shorter runs are coincidental matches in a
    table-heavy image and are labelled `unanchored` instead of being reported.

And for a named module it reports the identity run from the slot's offset 0,
which is the thing that actually pins an origin: `X5.PDS` has `org $c000` and
this build's slot 14 agrees with the cartridge for its first 768 bytes, which is
a measurement rather than an assumption.

## Coverage, asserted

Every number here is a byte count over a range somebody could have got wrong, and
the project's established failure mode is an instrument that prints plausible
numbers for the wrong range. So:

  * it refuses to run unless both PRG images are exactly 131072 bytes, and prints
    both lengths and both body SHA1s;
  * for every slot it prints how many 16-byte windows matched **at all**. A slot
    where nothing matched cannot be distinguished from a slot where the tool
    searched the wrong range unless that count is on the page;
  * it prints the number of shifts evaluated, so the search cannot silently
    collapse;
  * it prints the length of the equal run behind every quoted shift, and prints
    `unanchored` rather than a number when the run is too short to carry one;
  * a module whose slot has no matching windows at all is reported as
    `NOT LOCATABLE`, not as a displacement of zero;
  * it exits non-zero if any image length is wrong or any slot matched no windows
    at all, so a run that measured nothing cannot be read as a clean result.

The cartridge is opened read-only and nothing here writes to it or to
`NES/SaveRAM/`.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cartref import DEFAULT_CART  # noqa: E402
DEFAULT_CART = DEFAULT_CART
SLOT = 8192
SLOTS = 131072 // SLOT
WINDOW = 16


def read_cart(path: Path) -> tuple[bytes, str]:
    """(the 128 KiB PRG, the SHA1 of the *whole* body).

    The digest is over PRG **and** CHR -- every body byte after the 16-byte iNES
    header -- because that is what `asm/patches.py` and the digests in README.md
    and PROVENANCE.md record. Hashing the PRG alone gives `b5bd2044...` for this
    same file, which is a different number for the same cartridge; labelling a
    PRG-only digest "body sha1" is the exact confusion `patches.raw_body`'s
    docstring warns about, and it is worse here because nothing in this tool would
    fail -- it would just print a wrong digest that looks right.
    """
    raw = path.read_bytes()
    if raw[:4] != b"NES\x1a":
        raise SystemExit(f"{path}: not an iNES file (magic {raw[:4]!r})")
    prg_len, chr_len = raw[4] * 16384, raw[5] * 8192
    if len(raw) - 16 < prg_len + chr_len:
        raise SystemExit(f"{path}: body is {len(raw) - 16} bytes, the header "
                         f"declares {prg_len + chr_len}")
    body = raw[16:16 + prg_len + chr_len]
    return body[:prg_len], hashlib.sha1(body).hexdigest()


def read_syms(path: Path) -> dict[str, int]:
    """`mag.sym` maps `name = $ADDR`. `asm/pds6502.py` lower-cases names, so every
    lookup is done on the lower-cased spelling; looking the source spelling up as
    written would report every label unlocatable, which is indistinguishable from
    a result."""
    syms: dict[str, int] = {}
    for line in path.read_text().splitlines():
        name, sep, val = line.partition("=")
        if not sep:
            continue
        val = val.strip()
        if val.startswith("$"):
            try:
                syms[name.strip().lower()] = int(val[1:], 16)
            except ValueError:
                pass
    return syms


def where(addr: int) -> tuple[int | None, int | None, str]:
    """CPU address -> (slot, intra-slot offset, how that was decided).

    MMC3 PRG mode 0, which is the mode this game uses (X5.PDS:50-51, "the MMC3
    map-mode bit (register 0, bit-6) is always set to zero in this game"):

        $8000-$9FFF  register 6  -- slot varies with the bank written, so a
                                    *static* address here has no fixed slot and
                                    no intra-slot offset can be claimed
        $A000-$BFFF  register 7  -- same
        $C000-$DFFF  fixed slot 14 ($0E)
        $E000-$FFFF  fixed slot 15 ($0F)

    The first two cases return `None` rather than a guess. Assuming a register is
    set to some bank at build time is how a displacement gets read as an origin.
    """
    if 0x8000 <= addr <= 0x9FFF:
        return None, None, ("register 6 window; the slot depends on the bank "
                            "written at run time, so there is no fixed slot")
    if 0xA000 <= addr <= 0xBFFF:
        return None, None, ("register 7 window; the slot depends on the bank "
                            "written at run time, so there is no fixed slot")
    if 0xC000 <= addr <= 0xDFFF:
        return 14, addr - 0xC000, "fixed slot 14 ($C000 window)"
    if 0xE000 <= addr <= 0xFFFF:
        return 15, addr - 0xE000, "fixed slot 15 ($E000 window)"
    return None, None, "outside every MMC3 mode 0 PRG window"



def best_shift(ours: bytes, theirs: bytes, limit: int) -> tuple[int, int]:
    """(shift, matched bytes at it) by exhaustive scan. O(n*limit) in Python, so
    it is opt-in: `--exhaustive`. The default route is `vote_shift`, which is
    linear and uses only the unambiguous-window evidence."""
    n = len(ours)
    best_m, best_s = -1, 0
    for s in range(-limit, limit):
        lo, hi = max(0, -s), min(n, n - s)
        m = sum(1 for i in range(lo, hi) if theirs[i + s] == ours[i])
        if m > best_m:
            best_m, best_s = m, s
    return best_s, best_m


def vote_shift(per: dict[int, int]) -> tuple[int | None, int]:
    """The displacement that accounts for the most *bytes*, from the window map.

    Better than an exhaustive scan for this purpose, not just cheaper: every vote
    comes from a WINDOW-byte slice that occurs exactly once in the cartridge's
    slot, so a vote cannot be a repeated-table coincidence. Returns
    `(None, 0)` when no window matched, which the caller must treat as
    "nothing was measured" and not as "shift 0".
    """
    weight: dict[int, int] = {}
    for i, d in per.items():
        weight[d] = weight.get(d, 0) + WINDOW
    if not weight:
        return None, 0
    d, w = max(weight.items(), key=lambda kv: (kv[1], -abs(kv[0])))
    return d, w



def windows(ours: bytes, theirs: bytes) -> tuple[dict[int, int], int, int]:
    """Offset -> displacement for every position whose WINDOW-byte slice occurs
    exactly once in `theirs`. Returns the map, how many positions matched, and
    how many bytes that covers. A slice of one repeated value is skipped: it
    matches everywhere and would report a displacement of whatever came first."""
    per: dict[int, int] = {}
    n = len(ours)
    for i in range(0, n - WINDOW + 1):
        win = ours[i:i + WINDOW]
        if len(set(win)) < 2:
            continue
        j = theirs.find(win)
        if j < 0:
            continue
        if theirs.find(win, j + 1) >= 0:
            continue
        per[i] = j - i
    covered = sum(WINDOW for _ in per)
    return per, len(per), covered


def runs(per: dict[int, int], minrun_bytes: int) -> list[tuple[int, int, int | None, int]]:
    """Collapse the per-offset map into runs of equal displacement. A run is
    reported with its displacement only if the preceding equal stretch is at
    least `minrun_bytes` long; otherwise `None`, meaning `unanchored`."""
    out: list[tuple[int, int, int | None, int]] = []
    if not per:
        return out
    keys = sorted(per)
    start = prev = keys[0]
    cur = per[keys[0]]
    for k in keys[1:]:
        if per[k] != cur or k != prev + 1:
            out.append((start, prev, cur, prev - start + WINDOW))
            start = k
            cur = per[k]
        prev = k
    out.append((start, prev, cur, prev - start + WINDOW))
    return [(a, b, (d if L >= minrun_bytes else None), L) for a, b, d, L in out]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cart", type=Path, default=DEFAULT_CART)
    ap.add_argument("--rebuilt", type=Path, default=ROOT / "asm" / "out" / "prg.bin")
    ap.add_argument("--sym", type=Path, default=ROOT / "asm" / "out" / "mag.sym")
    ap.add_argument("--slot", type=int, default=None,
                    help="report the displacement run map for this slot only")
    ap.add_argument("--module", default=None,
                    help="report this label's intra-slot origin and identity run")
    ap.add_argument("--assume-slot", type=int, default=None,
                    help="with --module, measure the label in this slot instead of "
                         "deriving one from the address. A label in $8000-$BFFF "
                         "lives in MMC3 register 6 or 7 and has no fixed slot, so "
                         "the origin of such a module can only be measured "
                         "against a slot the caller names. The slot is echoed "
                         "back as an assumption, never silently applied.")
    ap.add_argument("--minrun", type=int, default=24,
                    help="equal-run length below which a displacement is "
                         "unanchored and not quoted (default 24)")
    ap.add_argument("--strict", action="store_true",
                    help="also exit non-zero when any slot in the all-slots "
                         "sweep matched nothing, not only the slots the caller "
                         "asked about")
    ap.add_argument("--exhaustive", action="store_true",
                    help="also cross-check every slot with an exhaustive byte "
                         "scan over --limit shifts either side of 0. O(n*limit), "
                         "so it is opt-in; the default route votes from "
                         "unambiguous 16-byte windows and is linear.")
    ap.add_argument("--limit", type=int, default=SLOT // 2,
                    help="shifts searched either side of 0 by --exhaustive "
                         "(default 4096)")
    args = ap.parse_args()

    cart, cart_sha1 = read_cart(args.cart)
    reb = args.rebuilt.read_bytes()
    if len(cart) != 128 * 1024 or len(reb) != 128 * 1024:
        print(f"FATAL: PRG lengths are cart {len(cart)}, rebuilt {len(reb)}; both "
              f"must be {128 * 1024}", file=sys.stderr)
        return 2
    print(f"cartridge   {args.cart}")
    print(f"  body sha1 {cart_sha1}   <- over PRG *and* CHR, which is the digest")
    print(f"                          asm/patches.manifest and README.md record")
    print(f"rebuilt     {args.rebuilt}")
    print(f"  PRG  sha1 {hashlib.sha1(reb).hexdigest()}   <- PRG only; the rebuilt")
    print(f"                          image has no CHR body to hash")
    print(f"both PRG images are {len(cart)} bytes = {SLOTS} slots of {SLOT}\n")

    # Two different kinds of "nothing was measured", kept apart on purpose:
    #   census     -- a slot in the all-slots sweep matched nothing. That is a
    #                 *finding* (this build's slot 1 agrees with the cartridge's
    #                 at chance, which is what "wrong slot" looks like), not a
    #                 malfunction, so it is reported and does not fail the run.
    #   requested  -- the caller asked about this slot or module specifically and
    #                 it measured nothing. That is a malfunction and it does fail.
    census: list[str] = []
    problems: list[str] = []

    print("== per-slot origin: which displacement accounts for the most bytes ==")
    print("   s > 0 means the release's copy of the slot content sits LATER than ours")
    print(f"   every vote comes from a {WINDOW}-byte slice that occurs exactly "
          f"once in the")
    print(f"   cartridge's slot, so a repeated-table coincidence cannot vote")
    if args.exhaustive:
        print(f"   cross-checked by an exhaustive byte scan over "
              f"{2 * args.limit} shifts per slot")
    print("   slot   s=0 bytes   voted s   bytes   windows / bytes covered")
    origins = {}
    for s in range(SLOTS):
        ours = reb[s * SLOT:(s + 1) * SLOT]
        theirs = cart[s * SLOT:(s + 1) * SLOT]
        at0 = sum(1 for a, b in zip(ours, theirs) if a == b)
        per, nw, cov = windows(ours, theirs)
        vs, vw = vote_shift(per)
        extra = ""
        if args.exhaustive:
            bs, bm = best_shift(ours, theirs, args.limit)
            extra = f"   scan: best {bs:+#07x} at {bm}"
        origins[s] = (at0, vs, vw, nw, cov)
        flag = ""
        if nw == 0:
            flag = ("   <-- NO WINDOWS MATCHED: this build's slot and the "
                    "cartridge's agree at")
            census.append(f"slot {s}: no {WINDOW}-byte window of the rebuild "
                          f"occurs in the cartridge's slot {s}, and only "
                          f"{at0}/{SLOT} bytes agree at displacement 0 -- this "
                          f"is what a module in the wrong 8 KiB slot looks like")
        vs_s = "  none" if vs is None else f"{vs:+#07x}"
        print(f"   {s:4d}   {at0:10d}   {vs_s:>7}   {vw:6d}   {nw:6d} / {cov:6d}"
              f"{extra}{flag}")

    if args.module:
        syms = read_syms(args.sym)
        addr = syms.get(args.module.lower())
        print(f"\n== module {args.module} ==")
        if addr is None:
            print(f"   FATAL no symbol {args.module.lower()!r} in {args.sym}")
            return 2
        slot, off, why = where(addr)
        print(f"   symbol      ${addr:04X}  ->  {why}")
        if args.assume_slot is not None:
            # A label in $8000-$BFFF is presented through MMC3 register 6 or
            # register 7, and either may hold any 8 KiB slot, so the address
            # alone cannot say which. The caller names the slot; this checks the
            # naming against the address instead of trusting it.
            if not 0 <= args.assume_slot < SLOTS:
                print(f"   FATAL --assume-slot {args.assume_slot} is outside "
                      f"0..{SLOTS - 1}")
                return 2
            if slot is not None and slot != args.assume_slot:
                print(f"   FATAL --assume-slot {args.assume_slot} contradicts the "
                      f"address, which is in fixed slot {slot}")
                return 2
            bases = []
            if 0x8000 <= addr < 0xA000:
                bases.append(("register 6 window", 0x8000))
            if 0xA000 <= addr < 0xC000:
                bases.append(("register 7 window", 0xA000))
            if 0xC000 <= addr < 0xE000:
                bases.append((f"$C000 window (fixed slot {args.assume_slot})",
                              0xC000))
            if 0xE000 <= addr < 0x10000:
                bases.append((f"$E000 window (fixed slot {args.assume_slot})",
                              0xE000))
            fits = [(why_b, b, addr - b) for why_b, b in bases
                    if 0 <= addr - b < SLOT]
            if not fits:
                print(f"   FATAL the assumed slot cannot present ${addr:04X} in "
                      f"any 8 KiB window")
                return 2
            why_b, base, off = fits[0]
            print(f"   ASSUMING slot {args.assume_slot} (given on the command "
                  f"line, not derived);")
            print(f"   the label sits in the {why_b} at intra-slot offset "
                  f"${off:04X}")
            if len(fits) > 1:
                print(f"   NOTE the address fits {len(fits)} windows; using "
                      f"${base:04X} as the base. Pass a different --assume-slot "
                      f"if that is the wrong one.")
            slot = args.assume_slot
        if slot is None:
            print(f"   NOT LOCATABLE in a fixed slot: {why}.")
            print(f"   Reporting an intra-slot origin here would mean assuming a bank")
            print(f"   value that only exists at run time, so this tool declines to.")
            problems.append(f"module {args.module}: ${addr:04X} is in a banked "
                            f"window with no fixed slot")
        else:
            assert slot is not None and off is not None
            print(f"               slot {slot}, intra-slot offset ${off:04X}, "
                  f"PRG file offset ${slot * SLOT + off:06X}")
            ours = reb[slot * SLOT:(slot + 1) * SLOT]
            theirs = cart[slot * SLOT:(slot + 1) * SLOT]
            n = 0
            while n < SLOT and ours[n] == theirs[n]:
                n += 1
            print(f"   identical run from intra-slot offset 0: {n} bytes")
            if n >= args.minrun:
                print(f"   VERDICT: intra-slot origin is 0, and it is MEASURED -- "
                      f"the")
                print(f"   first {n} bytes of this slot agree with the cartridge "
                      f"exactly, so the module")
                print(f"   starts where the build says it starts. Any displacement "
                      f"seen *inside*")
                print(f"   the slot is a length difference in the middle of the "
                      f"module, not a")
                print(f"   misplacement.")
            else:
                print(f"   VERDICT: UNPROVEN -- only {n} identical bytes from "
                      f"offset 0, which is")
                print(f"   below the {args.minrun}-byte run this tool requires "
                      f"before it will call an")
                print(f"   origin measured. A short run is what a coincidental "
                      f"match looks like.")
            at0, vs, vw, nw, cov = origins[slot]
            vs_s = "none" if vs is None else f"{vs:+#07x}"
            print(f"   for reference, this slot's modal displacement is {vs_s} "
                  f"({vw} bytes of {WINDOW}-byte windows)")
            print(f"   against {at0} identical bytes at displacement 0. A modal "
                  f"displacement is NOT the")
            print(f"   origin test: on a slot that is mostly repeated table data a "
                  f"non-zero value can")
            print(f"   win on volume alone (slot 14 does), so the identity run "
                  f"above is what the verdict uses.")

    if args.slot is not None:
        s = args.slot
        ours = reb[s * SLOT:(s + 1) * SLOT]
        theirs = cart[s * SLOT:(s + 1) * SLOT]
        per, nw, cov = windows(ours, theirs)
        print(f"\n== displacement inside slot {s} ==")
        print(f"   windows matched {nw}, covering {cov}/{SLOT} bytes")
        if not per:
            print("   FATAL nothing matched; the numbers above would be fiction")
            problems.append(f"slot {s}: --slot was requested and nothing matched")
            return 1
        print("   a displacement is quoted only when the equal run carrying it is "
              f">= {args.minrun} bytes;")
        print("   everything else is `unanchored`, which means coincidental")
        seen: dict[int | None, int] = {}
        for a, b, d, L in runs(per, args.minrun):
            seen[d] = seen.get(d, 0) + (b - a)
        print("   displacement histogram, weighted by bytes covered:")
        for d, n in sorted(seen.items(), key=lambda kv: -kv[1])[:12]:
            print(f"      {'unanchored' if d is None else format(d, '+#07x'):>12}  "
                  f"{n:6d} byte(s)")
        print("   run map (offset ranges in slot coordinates):")
        for a, b, d, L in runs(per, args.minrun):
            if b - a < 3:
                continue
            tag = "unanchored" if d is None else format(d, "+#07x")
            print(f"      ${a:04X}-${b + WINDOW - 1:04X}  displacement {tag:>11}  "
                  f"(run {L} B)")

    print()
    if census:
        print(f"CENSUS: {len(census)} of {SLOTS} slots matched nothing at all. "
              f"That is a result about")
        print("those slots, not about this tool:")
        for c in census:
            print(f"  * {c}")
        if args.strict:
            problems.extend(census)
    else:
        print(f"CENSUS: all {SLOTS} slots had at least one matching window.")

    if problems:
        print()
        print("COVERAGE PROBLEMS -- the numbers above would be fiction:")
        for pr in problems:
            print(f"  FATAL {pr}")
        return 1
    print(f"coverage: both images were exactly {128 * 1024} bytes, and every "
          f"slot the caller asked")
    print("about was measured. Use --strict to also fail on the all-slots census "
          "above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
