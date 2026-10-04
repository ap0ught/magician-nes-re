#!/usr/bin/env python3
"""What exactly is in nt0 on each side: the fill value, the shift, the holes.

    python3 tools/bizhawk/ntanalyse.py <cartdir> <rebuildir> [frame]

Answers four questions with arithmetic rather than by looking at it:
  1. which bytes are $08 (the PPUCTRL the source's reset leaves in the game)
  2. whether stripping them leaves the cartridge's stream, and at what offset
  3. whether the cartridge's stream has $00 gaps the rebuild does not
  4. how long each run of identical values is
"""
import sys
from pathlib import Path

cart = (Path(sys.argv[1]) / f"ciram_{int(sys.argv[3]) if len(sys.argv) > 3 else 60:04d}.bin").read_bytes()
reb = (Path(sys.argv[2]) / f"ciram_{int(sys.argv[3]) if len(sys.argv) > 3 else 60:04d}.bin").read_bytes()

NT = 960


def runs(b, n=8):
    out, i = [], 0
    while i < n and i < len(b):
        j = i
        while j < n and b[j] == b[i]:
            j += 1
        out.append(('$%04X' % (0x2000 + i), '%02X' % b[i], j - i))
        i = j
    return out


a, b = cart[:NT], reb[:NT]
print('cart: %d zero bytes, %d nonzero; reb: %d zero, %d nonzero; %d equal'
      % (a.count(0), NT - a.count(0), b.count(0), NT - b.count(0),
         sum(1 for x, y in zip(a, b) if x == y)))
print('reb $08 count %d, cart $08 count %d' % (b.count(8), a.count(8)))
print('reb $00 count %d, cart $00 count %d' % (b.count(0), a.count(0)))
print()
print('reb  first runs of equal bytes:', runs(b, 8))
print('cart first runs of equal bytes:', runs(a, 8))
print()

bs = bytes(v for v in b if v != 8)
as_ = bytes(v for v in a if v != 0)
print('reb with $08 removed: %d bytes; cart with $00 removed: %d bytes'
      % (len(bs), len(as_)))
best = None
for off in range(-64, 65):
    n = 0
    for i in range(len(as_)):
        j = i + off
        if 0 <= j < len(bs) and bs[j] == as_[i]:
            n += 1
    if best is None or n > best[1]:
        best = (off, n)
print('best alignment of cart-stream inside reb-stream: offset %+d, %d/%d bytes equal'
      % (best[0], best[1], len(as_)))

# where do the two disagree, in stream order?
off = best[0]
mis = [(0x2000 + i, as_[i], bs[i + off]) for i in range(len(as_))
       if not (0 <= i + off < len(bs)) or bs[i + off] != as_[i]]
print('stream mismatches: %d of %d' % (len(mis), len(as_)))
for k, (addr, cv, rv) in enumerate(mis[:24]):
    print('  #%3d  $%04X  cart %02X  reb %02X' % (k, addr, cv, rv))
if len(mis) > 24:
    print('  ... %d more' % (len(mis) - 24))