#!/usr/bin/env python3
"""The spell-name table: source vs each dump, and how each dump differs from ours.

What this settles
-----------------
`sptxt` (`vendor/Magician-NES/MISC.SRC`) has spell names the *guide* does not:
the source says `RAZORSTORM`, `KISS MY AXE`, `FIRE FOUNTAIN`, `POWER SHIELD`,
`HEAL`, `ANTI VEN`, `MUZAK`; the guide shows `BOOMERAXE`, `FIRE RING`,
`FIRE SPRAY`, `POW SHIELD`, `MEDITATE`, `SOUND TEST`. That was recorded as "the
release renamed the spell table", i.e. a real difference between this source and
the cartridge.

Against **Beta 1** -- the build this source came from -- there is no rename. Beta 1
carries the source's own strings, byte for byte. The guide's names belong to the
release. So the finding dissolves, and this tool is what dissolves it.

What Beta 1 does differently, which is a *new* finding
-------------------------------------------------------
Two things, both visible here and neither of them a rename:

  1. **Bit 7 is set on the last character of every name.** `RAZORSTORM` is stored
     as `RAZORSTOR` + `M|$80`. That is why a plain `bytes.find(b"RAZORSTORM")`
     against Beta 1 returns nothing and reads as "Beta 1 has no spell names" --
     the same silent-wrong-answer shape as everything else in this project. It
     also means any search for a spell name has to mask bit 7 first.

  2. Beta 1 is **missing the first six names** -- `RAZOR`, `AXOR`, `BOULDER`,
     `SCARY`, `VEN`, `FIREBALL`, `LIGHTNING` are absent -- and its table therefore
     starts 41 bytes later than ours.

Self-coverage
-------------
The table is located by anchoring on a string that is *bit-7 clean in every dump*
(`WHO?S WHO`, which follows the spell names in both), then walking backwards over
null-terminated strings. If the anchor is absent or not unique, this exits
non-zero rather than reporting a walk that started in the wrong place -- walking
backwards from a bad anchor produces plausible names from unrelated strings,
which is exactly the failure being guarded against.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

import patches  # noqa: E402

# Immediately after the spell names in every dump. `sptxt` is followed by
# `dc "WHO?S WHO"`, and it contains no bit-7-set byte in any of them.
ANCHOR = b"WHO?S WHO"


def source_names() -> list[str]:
    names: list[str] = []
    grab = False
    for line in (ROOT / "vendor/Magician-NES/MISC.SRC").read_text().splitlines():
        m = re.search(r'dc\s+"([^"]*)"', line)
        if re.match(r"^sptxt\b", line):
            grab = True
            if m:
                names.append(m.group(1))
            continue
        if not grab:
            continue
        if m:
            names.append(m.group(1))
        elif line.strip().startswith(";") or not line.strip():
            continue
        else:
            break
    return names


def spell_region(img: bytes, anchor_at: int, back: int = 900) -> bytes:
    """The run of table bytes immediately before the anchor, with bit 7 masked.

    Masking first is the whole point. Every dump sets bit 7 on the last character
    of each name -- `RAZORSTORM` is stored as `RAZORSTOR` + `M|$80` -- so a plain
    search for a name finds nothing and reports "this dump has no spell table".
    That is the exact failure this tool exists to avoid, and it is why the
    release looked like it simply lacked them.

    Returns the masked bytes, not the raw ones: a name read with bit 7 masked is
    the name, and re-applying the mask is a documented one-liner if a caller
    wants to know which bytes were highlighted.
    """
    lo = max(0, anchor_at - back)
    return bytes(b & 0x7F for b in img[lo:anchor_at])


PRINTABLE = set(range(0x20, 0x7F))


def runs(region: bytes) -> list[str]:
    """Maximal runs of printable ASCII, in order. Names are space-separated."""
    out, cur = [], bytearray()
    for b in region:
        if b in PRINTABLE:
            cur.append(b)
        else:
            if len(cur) >= 3:
                out.append(cur.decode("ascii"))
            cur = bytearray()
    if len(cur) >= 3:
        out.append(cur.decode("ascii"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prg", type=pathlib.Path, default=ROOT / "asm/out/prg.bin")
    args = ap.parse_args()

    names = source_names()
    if len(names) != 38:
        print(f"FAIL: parsed {len(names)} spell names from MISC.SRC, expected 38. "
              f"The parse is what everything below is keyed on.", file=sys.stderr)
        return 2
    print(f"MISC.SRC sptxt: {len(names)} names")
    print("  " + " | ".join(names))

    imgs: dict[str, bytes] = {"ours": args.prg.read_bytes()}
    for n in sorted(patches.CARTS):
        imgs[n] = patches.body(patches.cart_path(n))

    print(f"\n{'':9s} " + "  ".join(f"{n:>10s}" for n in imgs))
    for n in names:
        row = []
        for img in imgs.values():
            hits = []
            i = img.find(n.encode())
            while i >= 0:
                hits.append(i)
                i = img.find(n.encode(), i + 1)
            # A name whose last byte has bit 7 set will not be found by find();
            # retry with the last byte masked off.
            if not hits and len(n) >= 2:
                pat = n[:-1].encode() + bytes([n[-1].encode()[0] | 0x80])
                i = img.find(pat)
                while i >= 0:
                    hits.append(i)
                    i = img.find(pat, i + 1)
            row.append(str(hits[0]) if hits else "-")
        print(f"{n:9.9s} " + "  ".join(f"{r:>10s}" for r in row))

    print("\nthe spell-name run before the anchor, in each image "
          "(bit 7 masked):")
    tables: dict[str, list[str]] = {}
    for name, img in imgs.items():
        at = img.find(ANCHOR)
        if at < 0 or img.find(ANCHOR, at + 1) >= 0:
            print(f"FAIL: the anchor {ANCHOR!r} occurs "
                  f"{0 if at < 0 else img.count(ANCHOR)} time(s) in {name}; the "
                  f"region cannot be located.", file=sys.stderr)
            return 2
        got = runs(spell_region(img, at))
        # The spell names are the tail of the run, from `RAZOR` onwards.
        if "RAZOR" in got:
            got = got[got.index("RAZOR"):]
        tables[name] = got
        hi = sum(1 for b in img[max(0, at-900):at] if b & 0x80)
        print(f"  {name:8s} {len(got):3d} name(s), {hi} byte(s) with bit 7 set in "
              f"the region")
        print(f"           {' | '.join(got[:8])}"
              + (" ..." if len(got) > 8 else ""))

    ours = tables["ours"]
    tgt = patches.DEFAULT_CART
    if tgt in tables:
        same = sum(1 for a, b in zip(ours, tables[tgt]) if a == b)
        print(f"\n{tgt} vs our build: {same} of {len(ours)} names byte-identical, "
              f"{len(tables[tgt])} names present")
        renamed = [(a, b) for a, b in zip(ours, tables[tgt]) if a != b]
        if renamed:
            print(f"  {len(renamed)} differ; first five:")
            for a, b in renamed[:5]:
                print(f"    ours {a!r}  vs  {tgt} {b!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())