#!/usr/bin/env python3
"""Check every entry in `asm/patches.py`'s cartridge registry against the disk.

Why this exists
---------------
Four sessions of this project were lost to tools that printed plausible numbers
about the wrong thing. The specific failure this one prevents: the registry in
`asm/patches.py` is the single place that names which dump is which, and six
dumps of this title sit on this machine with **byte-identical 16-byte headers**
where five of them agree on mapper, 128 KiB PRG, 128 KiB CHR and horizontal
mirroring. A digest typo, a stale path, or a `sha1_full` copied from the wrong
row would produce a build that measures itself against the wrong cartridge and
reports a confident percentage.

So every field that *can* be checked against the bytes is checked, and any
mismatch is a hard failure:

  * the file exists and is a whole number of header + PRG + CHR bytes
  * body SHA1 (header stripped) matches the registry
  * full-file SHA1 matches the registry, when the registry records one
  * the battery bit in header byte 6 matches the registry -- Beta 1 has it CLEAR
    and every other dump has it SET, so this is the one field where a
    hard-coded assumption is provably wrong for one dump
  * the three vectors read out of the last 6 PRG bytes match the registry, which
    is what the X7 anchor is derived from

Self-coverage
-------------
The point of the exercise is to be sure the *whole* registry was looked at, so
the tool asserts its own coverage: it fails if it examined fewer dumps than the
registry names, and it fails if a dump is missing a digest it is supposed to
have. "0 dumps checked, all fine" is not an answer this tool is able to give.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "asm"))
import patches  # noqa: E402


def check(name: str, entry: dict, cart_dir: pathlib.Path) -> list[str]:
    """Return a list of problems. Empty means this dump agrees with the registry."""
    bad: list[str] = []
    path = cart_dir / str(entry["file"])
    if not path.is_file():
        return [f"{path} is not there"]

    data = path.read_bytes()
    if data[:4] != b"NES\x1a":
        return [f"{path} has no iNES magic ({data[:4]!r})"]

    prg_len, chr_len = data[4] * 16384, data[5] * 8192
    if len(data) != 16 + prg_len + chr_len:
        bad.append(f"{path} is {len(data)} bytes; header declares "
                   f"{16 + prg_len + chr_len} (16 + {prg_len} PRG + {chr_len} CHR)")
    prg = data[16:16 + prg_len]

    got_body = hashlib.sha1(data[16:]).hexdigest()
    if got_body != str(entry["sha1"]).lower():
        bad.append(f"body sha1 is {got_body}, registry says {entry['sha1']}")
    got_full = hashlib.sha1(data).hexdigest()
    if entry.get("sha1_full") and got_full != str(entry["sha1_full"]).lower():
        bad.append(f"file sha1 is {got_full}, registry says {entry['sha1_full']}")
    if not entry.get("sha1_full"):
        bad.append("registry records no sha1_full, so BizHawk's "
                   "MAGICIAN_EXPECT_SHA1 cannot be derived from it")

    want_batt = bool(entry["battery"])
    got_batt = bool(data[6] & 0x02)
    if want_batt != got_batt:
        bad.append(f"header byte 6 is ${data[6]:02X}, battery bit is "
                   f"{'SET' if got_batt else 'CLEAR'}; registry says "
                   f"{'SET' if want_batt else 'CLEAR'}")

    def vec(off: int) -> int:
        return prg[off] | (prg[off + 1] << 8)

    got_vec = (vec(0x1FFFA), vec(0x1FFFC), vec(0x1FFFE))
    want_vec = tuple(entry["vectors"])
    if got_vec != want_vec:
        bad.append(f"vectors are nmi=${got_vec[0]:04X} reset=${got_vec[1]:04X} "
                   f"irq=${got_vec[2]:04X}; registry says "
                   f"${want_vec[0]:04X}/${want_vec[1]:04X}/${want_vec[2]:04X}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cart-dir", type=pathlib.Path, default=patches.CART_DIR)
    args = ap.parse_args()

    names = sorted(patches.CARTS)
    # Coverage assertion, before any work: a registry that shrank to one entry
    # would otherwise pass silently.
    if len(names) < 5:
        print(f"FAIL: the registry names only {len(names)} dump(s) ({names}); "
              f"this tool exists to check all of them, so a shrunken registry is "
              f"itself the bug.", file=sys.stderr)
        return 2
    if patches.DEFAULT_CART not in patches.CARTS:
        print(f"FAIL: DEFAULT_CART is {patches.DEFAULT_CART!r}, which is not in "
              f"the registry.", file=sys.stderr)
        return 2

    print(f"cartridge registry: {len(names)} dumps in {args.cart_dir}")
    print(f"build target: {patches.DEFAULT_CART} "
          f"({patches.CARTS[patches.DEFAULT_CART]['file']})\n")

    failures = 0
    for name in names:
        entry = patches.CARTS[name]
        bad = check(name, entry, args.cart_dir)
        if bad:
            failures += 1
            print(f"FAIL {name}")
            for b in bad:
                print(f"       {b}")
        else:
            print(f"  ok {name:8s} ${entry['sha1'][:8]}  "
                  f"battery={'yes' if entry['battery'] else 'NO ':>3}  "
                  f"nmi/reset/irq=${entry['vectors'][0]:04X}/"
                  f"${entry['vectors'][1]:04X}/${entry['vectors'][2]:04X}  "
                  f"{entry['note']}")

    # The coverage assertion again, after the work: prove the loop ran.
    checked = len(names) - failures
    print(f"\n{checked}/{len(names)} dumps agree with the registry.")
    if checked != len(names):
        print("FAIL: not every registered dump was verified.", file=sys.stderr)
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())