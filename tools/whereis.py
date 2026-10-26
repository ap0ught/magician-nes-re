#!/usr/bin/env python3
"""Where did an incbin'd DAT/ file actually land in the build?

    python3 tools/whereis.py TIT.DAT [PAN.DAT ...]

`load` in PDS is a macro that takes an optional origin and an optional label, so
`titdat load tit.dat` has neither: the label is whatever the assembler's
own rule for a label on a macro line makes it, and the data lands wherever the
bank cursor was. Both facts are checked here rather than assumed, because a
symbol that points into code produces a *loadable* ROM that reads its scene from
the wrong place and then draws a plausible-looking wrong picture.
"""
import sys
from pathlib import Path

ROOT = Path('/home/cmayfield/code/games/magician-nes')
prg = (ROOT / 'asm' / 'out' / 'prg.bin').read_bytes()
syms = {}
for line in (ROOT / 'asm' / 'out' / 'mag.sym').read_text().splitlines():
    if '=' in line:
        k, _, v = line.partition('=')
        try:
            syms[k.strip()] = int(v.strip().lstrip('$'), 16)
        except ValueError:
            pass


def where(blob):
    hits, i = [], prg.find(blob)
    while i != -1 and len(hits) < 6:
        hits.append(i)
        i = prg.find(blob, i + 1)
    return hits


for arg in sys.argv[1:]:
    p = ROOT / 'vendor' / 'Magician-NES' / 'DAT' / arg
    if not p.exists():
        cand = list((ROOT / 'vendor').rglob(arg))
        if not cand:
            print('%-10s not found' % arg)
            continue
        p = cand[0]
    blob = p.read_bytes()
    hits = where(blob)
    cpu = ['$%04X' % (0x8000 + h) for h in hits]
    print('%-10s %5d bytes  in build at %s' % (p.name, len(blob),
                                               ', '.join(cpu) or 'NOWHERE'))
    # Is the symbol that names it pointing at those bytes?
    lbl = p.stem.lower()
    for cand in (lbl, lbl + 'at'):
        if cand in syms:
            off = syms[cand] - 0x8000
            ok = prg[off:off + len(blob)] == blob
            print('   symbol %-8s = $%04X  -> data there: %s'
                  % (cand, syms[cand], 'YES' if ok else 'NO'))
    print('   first 16 bytes: %s' % blob[:16].hex(' '))