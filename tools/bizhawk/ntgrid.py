#!/usr/bin/env python3
"""Print the four nametables of one frames.lua dump as 32x30 tile grids."""
import sys
from pathlib import Path

d = Path(sys.argv[1])
frame = int(sys.argv[2]) if len(sys.argv) > 2 else 60
which = sys.argv[3] if len(sys.argv) > 3 else '0'
off = {'0': 0x000, '1': 0x400, '2': 0x800, '3': 0xC00}[which]
b = (d / f"ciram_{frame:04d}.bin").read_bytes()
print(f"{d.name} frame {frame} nametable ${0x2000 + off:04X}")
for row in range(30):
    cells = b[off + row * 32: off + row * 32 + 32]
    txt = ''.join('.' if v == 0 else ' ' for v in cells)
    print('%2d %-64s |%s|' % (row, ' '.join('%02X' % v for v in cells), txt))