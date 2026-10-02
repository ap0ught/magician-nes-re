#!/usr/bin/env python3
"""Wrap the rebuilt PRG and CHR images into a loadable iNES file.

The assembler emits `out/prg.bin` and `out/chr.bin` separately, because that is
how the cartridge is actually laid out: a 128 KiB PRG image and a 128 KiB CHR
image behind one mapper. An emulator wants them behind an iNES header, so this
joins the three.

The header is the cartridge's own, read off `Magician (USA).nes` and recorded in
PROVENANCE.md section 1 -- not synthesised from the mapper number, because the
two dumps on this machine share this header byte for byte and differ only in
the body. `option 0,0` in the source's `memchk`/`load` macros shows the author
was driving the same mapper, so this is the header to reuse, but it is a *choice*
of revision, not a derivation.

    python3 asm/mkrom.py                    # out/magician-rebuilt.nes
    python3 asm/mkrom.py --rom out/x.nes
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "asm" / "out"

# 'NES\x1a', 8x16 KiB PRG, 16x8 KiB CHR, f6=0x42 f7=0x00, then eight zero bytes.
# f6: battery-backed PRG RAM, horizontal mirroring. f7: mapper 4 (MMC3).
INES_HEADER = bytes([
    0x4E, 0x45, 0x53, 0x1A,   # 'NES' + EOF
    0x08,                     # 8 x 16 KiB PRG  = 128 KiB
    0x10,                     # 16 x 8 KiB CHR  = 128 KiB, non-zero so CHR-ROM
    0x42,                     # mapper low, battery, horizontal mirroring
    0x00,                     # mapper high
    0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00,
])

PRG_SIZE = 128 * 1024
CHR_SIZE = 128 * 1024


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prg", type=pathlib.Path, default=OUT / "prg.bin")
    ap.add_argument("--chr", type=pathlib.Path, default=OUT / "chr.bin")
    ap.add_argument("--rom", type=pathlib.Path, default=OUT / "magician-rebuilt.nes")
    args = ap.parse_args()

    prg = args.prg.read_bytes()
    chr_rom = args.chr.read_bytes()
    if len(prg) != PRG_SIZE:
        raise SystemExit(f"{args.prg} is {len(prg)} bytes, expected {PRG_SIZE}")
    if len(chr_rom) != CHR_SIZE:
        raise SystemExit(f"{args.chr} is {len(chr_rom)} bytes, expected {CHR_SIZE}")

    args.rom.parent.mkdir(parents=True, exist_ok=True)
    args.rom.write_bytes(INES_HEADER + prg + chr_rom)
    body = INES_HEADER + prg + chr_rom
    print(f"{args.rom}  {len(body)} bytes")
    print(f"  body sha1 {hashlib.sha1(prg + chr_rom).hexdigest()}")
    print(f"  (the cartridge's own body sha1 is "
          f"bd806d7f7c318b8012433250ca10aa8387a962bb -- a development build, "
          f"so it does not match; see PROVENANCE.md section 6)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())