#!/usr/bin/env python3
"""Find an instruction signature in a raw PRG and print both sides side by side.

    python3 tools/findsig.py <sig-hex> [<other-prg>] [context]

Used to line the rebuild's copy of a routine up against the cartridge's, without
guessing an address: the signature is a run of opcodes that is unlikely to occur
by accident, and the printout shows what each side has around it.
"""
import sys
from pathlib import Path

sig = bytes.fromhex(sys.argv[1].replace(' ', ''))
names = sys.argv[2:] or []
prgs = [Path('/home/cmayfield/code/games/magician-nes/asm/out/prg.bin')]
if names:
    prgs.append(Path(names[0]))
ctx = int(sys.argv[-1]) if len(sys.argv) > 3 and sys.argv[-1].isdigit() else 0x40


def cpu(off):
    return 0x8000 + off


for p in prgs:
    d = p.read_bytes()
    hits = []
    i = d.find(sig)
    while i != -1 and len(hits) < 8:
        hits.append(i)
        i = d.find(sig, i + 1)
    print('== %s : %d hit(s) %s' % (p.name, len(hits),
                                    ['$%04X' % cpu(h) for h in hits]))
    for h in hits:
        lo, hi = max(0, h - ctx), min(len(d), h + len(sig) + ctx)
        print('   $%04X  %s' % (cpu(lo), d[lo:hi].hex(' ')))
    print()