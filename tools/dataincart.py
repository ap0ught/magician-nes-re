#!/usr/bin/env python3
"""Is a data blob from the build present, byte for byte, in the cartridge PRG?

    python3 tools/dataincart.py <symbol> [<symbol> ...]

`incbin`'d data is the one part of the build that cannot be wrong by
mis-assembly: it is copied verbatim from DAT/. So if a blob is *absent* from
the cartridge, the difference is in the release's data or in where the build
puts it -- and if it is *present*, the blob is right and the code reading it is
where to look.
"""
import sys
from pathlib import Path

ROOT = Path('/home/cmayfield/code/games/magician-nes')
PRG = ROOT / 'asm' / 'out' / 'prg.bin'
CART = Path('/tmp/opencode/cart_prg.bin')
if not CART.exists():
    CART = Path('/tmp/opencode/cart_prg.bin')

syms = {}
for line in (ROOT / 'asm' / 'out' / 'mag.sym').read_text().splitlines():
    if '=' in line:
        k, _, v = line.partition('=')
        try:
            syms[k.strip()] = int(v.strip().lstrip('$'), 16)
        except ValueError:
            pass

prg = PRG.read_bytes()
cart = CART.read_bytes()
LONGEST = 24


def label_for(cpu):
    best = None
    for k, v in syms.items():
        if v <= cpu and (best is None or v > best[1]):
            best = (k, v)
    return best


for name in sys.argv[1:]:
    if name not in syms:
        print('%-12s  no such symbol' % name)
        continue
    cpu = syms[name]
    off = cpu - 0x8000
    lbl, _ = label_for(cpu)
    blob = prg[off:off + LONGEST]
    print('%-12s $%04X  file $%05X  next %s' % (name, cpu, off, syms.get(name)))
    print('   first %d bytes: %s' % (LONGEST, blob.hex(' ')))
    hits = []
    i = cart.find(blob)
    while i != -1 and len(hits) < 5:
        hits.append(i)
        i = cart.find(blob, i + 1)
    if hits:
        for h in hits:
            nxt = label_for(0x8000 + h)
            print('   FOUND in cartridge at file $%05X (cpu $%04X in a flat map)%s'
                  % (h, 0x8000 + h, ('  near %s' % nxt[0]) if nxt else ''))
    else:
        # try a shorter prefix, then a suffix, so "not present" is not confused
        # with "present but with one byte different"
        for k in (16, 12, 8):
            if cart.find(blob[:k]) != -1:
                print('   first %d bytes ARE in the cartridge, the rest are not' % k)
                break
        else:
            print('   NOT FOUND in the cartridge PRG (checked all 131072 bytes)')
    print()