#!/usr/bin/env python3
"""Deprecated shim: `unrun` now lives in tools/datcodec.py.

    python3 tools/unrun.py TIT.DAT [--hex] [--rows]

This tool used to carry its own transcription of `unrun` (x5.pds:111-121). That
transcription was wrong in two places, and both errors pointed the same way --
at a title screen made of solid brick, which an earlier session took to mean the
source's title data was missing. It is not. The data is fine; the transcription
was not.

  1. The input pointer. The epilogue is

         DCAD: 38      sec
         DCAE: 98      tya
         DCAF: 65 13   adc $13

     `sec` is not clearing a borrow, it is supplying the +1 that `adc` adds. The
     pointer advance is `t0 += y + 1`. This shim modelled it as `t0 += y`, which
     lands on the last byte of the record just read and re-reads it forever.

  2. The inner loop. `dex / bne $DC9D` branches back to the `sta $2007`, not to
     the `lda (t0),y` / `cmp t4` pair, so the token test happens once per record.
     This shim looped to the top and re-tested every byte, which misparses any
     run whose value byte happens to equal the token.

Together those made `unrun.py TIT.DAT` print "1024 screen bytes" that were all
`$08`. tools/datcodec.py fixes both, adds the run-length correction documented
there, and proves the format round-trips Eurocom's own three files
byte-identically.

Kept as a shim so old command lines and notes keep working. Use datcodec.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datcodec  # noqa: E402


def unrun(data: bytes) -> bytes:
    """The format as packed. See tools/datcodec.py for the derivation."""
    return datcodec.decode(data)


def main() -> int:
    arg = sys.argv[1]
    p = Path(arg)
    if not p.exists():
        p = datcodec.DAT / arg
    data = p.read_bytes()
    out = unrun(data)
    _, _, token, target = datcodec.header(data)
    print('%s: %d compressed bytes -> %d screen bytes (0x%X)'
          % (p.name, len(data), len(out), len(out)))
    print('  counter lo/hi = $%02X/$%02X, token = $%02X' % (data[0], data[1], token))
    if '--source' in sys.argv:
        src = datcodec.decode_source(data, pad=bytes(4096))
        print('  shipped +3 run length would emit %d distinct values; use '
              '`datcodec.py source` for the detail' % len(set(src)))
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