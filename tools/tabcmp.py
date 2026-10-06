#!/usr/bin/env python3
"""Compare a named table from the build with the cartridge's copy.

    python3 tools/tabcmp.py <symbol> <length>

The level index tables (`ldatl`/`ldath`/`ld10`) decide *which* level the game
loads, so a wrong byte there does not corrupt a picture -- it loads a different
level, and the two screens then differ everywhere with no local fault to find.
That is worth checking directly rather than inferring from the picture.
"""
import sys
from pathlib import Path

ROOT = Path('/home/cmayfield/code/games/magician-nes')
prg = (ROOT / 'asm' / 'out' / 'prg.bin').read_bytes()
cart = Path('/tmp/opencode/cart_prg.bin').read_bytes()

syms = {}
for line in (ROOT / 'asm' / 'out' / 'mag.sym').read_text().splitlines():
    if '=' in line:
        k, _, v = line.partition('=')
        try:
            syms[k.strip()] = int(v.strip().lstrip('$'), 16)
        except ValueError:
            pass

# The build files a byte at slot*0x2000 + (address - window); find a symbol's
# bytes by searching for a distinctive prefix rather than trusting one mapping,
# because a symbol can sit in any of the four windows.
name, length = sys.argv[1], int(sys.argv[2])
if name not in syms:
    raise SystemExit('no symbol %s' % name)
cpu = syms[name]
print('%-8s = $%04X, %d bytes' % (name, cpu, length))

# candidate offsets: every slot, each window
cands = set()
for slot in range(16):
    for win in (0x8000, 0xA000, 0xC000, 0xE000):
        off = slot * 0x2000 + (cpu - win)
        if 0 <= off and off + length <= len(prg):
            cands.add(off)
found = []
for off in sorted(cands):
    blob = prg[off:off + length]
    if len(set(blob)) < 2:
        continue          # a table of one value proves nothing; see below
    if blob in cart:
        found.append((off, cart.find(blob), True))
    else:
        for k in (16, 8):
            if len(blob) >= k and cart.find(blob[:k]) != -1:
                found.append((off, None, False))
                break

if not any(len(set(prg[o:o + length])) >= 2 for o in cands):
    print('  every candidate placement is a single repeated value, so "identical"')
    print('  would be true of any file with that value at that offset. Refusing to')
    print('  report it. The symbol may be wrong, or the table built elsewhere.')
    for o in sorted(cands)[:4]:
        print('  build file $%05X slot %2d off $%04X : %s'
              % (o, o // 0x2000, o % 0x2000,
                 ' '.join('%02X' % v for v in prg[o:o + min(length, 24)])))
elif found:
    for off, hit, full in found:
        print('  build file $%05X (slot %2d off $%04X) -> cartridge %s'
              % (off, off // 0x2000, off % 0x2000,
                 ('file $%05X IDENTICAL' % hit) if full
                 else 'first bytes present but table differs'))
    off = found[0][0]
    print('  build: %s' % prg[off:off + min(length, 48)].hex(' '))
    if found[0][1] is not None:
        h = found[0][1]
        print('  cart : %s' % cart[h:h + min(length, 48)].hex(' '))
else:
    print('  NOT FOUND in the cartridge at any slot/window placement')
    off = sorted(cands)[0]
    print('  build: %s' % prg[off:off + min(length, 48)].hex(' '))