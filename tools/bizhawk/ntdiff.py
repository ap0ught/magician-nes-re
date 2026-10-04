#!/usr/bin/env python3
"""Compare the four nametables of two frames.lua dumps, cell by cell.

    python3 tools/bizhawk/ntdiff.py <cartdir> <rebuildir> [frame] [--dump N]

CIRAM is 4096 bytes: four 960-byte nametables, then $2800-$2FFF, which is the
attribute/palette mirror and is reported separately because "all 969 differing
bytes are in nt0" is a claim about where, and where is the whole question.
"""
from __future__ import annotations

import sys
from pathlib import Path

NTS = [('$2000 nt0', 0x0000), ('$2400 nt1', 0x0400),
       ('$2800 nt2', 0x0800), ('$2C00 nt3', 0x0C00)]
MIRROR = 0x1000


def load(d: Path, frame: int) -> bytes:
    return (d / f"ciram_{frame:04d}.bin").read_bytes()


def hexrow(b: bytes, base: int, n: int) -> str:
    return ' '.join('%02X' % v for v in b[base:base + n])


def main() -> int:
    cart, reb = Path(sys.argv[1]), Path(sys.argv[2])
    frame = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    dumpn = None
    if '--dump' in sys.argv:
        dumpn = int(sys.argv[sys.argv.index('--dump') + 1])

    a, b = load(cart, frame), load(reb, frame)
    print(f"frame {frame}: {len(a)} vs {len(b)} bytes")
    # CIRAM is 4096: four 960-byte nametables with a 64-byte attribute table
    # after each. Reporting "nt0" as 1024 bytes would fold the attributes in and
    # make a nametable diff and an attribute diff indistinguishable.
    tot = 0
    for name, off in NTS:
        n = sum(1 for i in range(960) if a[off + i] != b[off + i])
        na = sum(1 for i in range(960, 1024) if a[off + i] != b[off + i])
        tot += n + na
        print('  %-12s tiles %4d / 960 differ   attributes %2d / 64 differ'
              % (name, n, na))

    if dumpn is not None:
        off = NTS[dumpn][1]
        print()
        print('first 48 differing cells of %s (cart / rebuild):' % NTS[dumpn][0])
        shown = 0
        for i in range(0x400):
            if a[off + i] != b[off + i]:
                print('  $%04X  cart %02X   reb %02X' % (0x2000 + off + i,
                                                          a[off + i], b[off + i]))
                shown += 1
                if shown >= 48:
                    break
        print()
        print('rebuild nt bytes $2000-$203F:')
        for r in range(0, 0x40, 16):
            print('  $%04X  %s' % (0x2000 + r, hexrow(b, off + r, 16)))
        print('cart    nt bytes $2000-$203F:')
        for r in range(0, 0x40, 16):
            print('  $%04X  %s' % (0x2000 + r, hexrow(a, off + r, 16)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())