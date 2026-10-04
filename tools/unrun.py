#!/usr/bin/env python3
"""Run the source's `unrun` decompressor over a DAT/ file, offline.

    python3 tools/unrun.py TIT.DAT [--hex] [--rows]

`unrun` (X5.PDS:111) is the only thing in the source that fills the nametable at
boot: `dotitle` banks in the title data and calls `unrunscn`, which aims $2006
at $2000 and hands the compressed blob to `unrun`. Modelling it here answers a
question no amount of emulator measurement can: what the source's own data and
the source's own algorithm produce *between themselves*. If that is a sensible
title screen, the rebuild is failing at runtime and the emulator is the right
place to look; if it is not, the data or the algorithm is wrong and no amount of
running it will help.
"""
import sys
from pathlib import Path

ROOT = Path('/home/cmayfield/code/games/magician-nes')


def unrun(data: bytes) -> bytes:
    """A literal transcription of X5.PDS:111-127, including its quirks."""
    out = bytearray()
    p = 0                      # t0/t1 as a flat offset into `data`
    t2 = data[0]               # lda (t0),y / sta t2,y for y = 2,1,0
    t3 = data[1]
    t4 = data[2]               # the token byte
    y = 3
    guard = 0
    while True:
        guard += 1
        if guard > 4000000:
            raise SystemExit('unrun: no terminator after %d bytes' % len(out))
        x = 1
        while True:
            a = data[p + y]
            if a == t4:
                y += 1
                cnt = data[p + y]
                x = (cnt & 0x7F) + 3
                carry = (cnt >> 7) & 1
                y += 1
                a = data[p + y]
            else:
                carry = 0
            # sta $2007 / php / adc #$00 / plp
            out.append(a & 0xFF)
            a = (a + carry) & 0xFF
            # inc t2 / bne / inc t3
            t2 = (t2 + 1) & 0xFF
            if t2 == 0:
                t3 = (t3 + 1) & 0xFF
            x -= 1
            if x == 0:
                break
        # sec / tya / adc t0 / sta t0 / bcc / inc t1, then ldy #$00
        p += y
        if p > 0xFF:
            p -= 0x100      # t1 carries the high byte; irrelevant in a flat model
        y = 0
        if t3 == 0:
            return bytes(out)


def main() -> int:
    arg = sys.argv[1]
    p = ROOT / 'vendor' / 'Magician-NES' / 'DAT' / arg
    if not p.exists():
        p = Path(arg)
    data = p.read_bytes()
    out = unrun(data)
    print('%s: %d compressed bytes -> %d screen bytes (0x%X)'
          % (p.name, len(data), len(out), len(out)))
    print('  count lo/hi = $%02X/$%02X, token = $%02X' % (data[0], data[1], data[2]))
    if '--hex' in sys.argv:
        for r in range(0, min(len(out), 0x400), 16):
            print('  $%04X  %s' % (0x2000 + r,
                                    ' '.join('%02X' % v for v in out[r:r + 16])))
    if '--rows' in sys.argv:
        for row in range(min(30, len(out) // 32)):
            cells = out[row * 32:row * 32 + 32]
            print('%2d %s' % (row, ' '.join('%02X' % v for v in cells)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())