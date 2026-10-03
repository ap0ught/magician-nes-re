#!/usr/bin/env python3
"""Wrap the rebuilt PRG and CHR images into a loadable iNES file.

The assembler emits `out/prg.bin` and `out/chr.bin` separately, because that is
how the cartridge is actually laid out: a 128 KiB PRG image and a 128 KiB CHR
image behind one mapper. An emulator wants them behind an iNES header, so this
joins the three.

Before joining, `asm/patches.py`'s manifest is applied: every range of the PRG
that Eurocom's released source does not contain at all is filled from the
cartridge there, digest-verified against the dump the manifest names. That is
the only step in the whole build that reads cartridge bytes into the ROM, and it
is why this file -- not `build.py` -- is where the manifest is applied: this is
the thing that produces the loadable image. `build.py` also applies it, so the
byte accounting it prints describes the same image this writes; `apply()` is
idempotent, so the second call is a no-op.

    python3 asm/mkrom.py                    # out/magician-rebuilt.nes
    python3 asm/mkrom.py --rom out/x.nes
    python3 asm/mkrom.py --no-patches       # the source-only image, for comparison
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import patches  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "asm" / "out"

# The cartridge is read here for the manifest's digest check and nothing else.
# `option 0,0` in the source's `memchk`/`load` macros shows the author was
# driving the same mapper, so this header is the right one to reuse, but it is a
# *choice* of revision, not a derivation: the two dumps on this machine share it
# byte for byte and differ only in the body.
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
    ap.add_argument("--cart-dir", type=pathlib.Path, default=patches.CART_DIR,
                    help="where the cartridge dumps are read from")
    ap.add_argument("--no-patches", action="store_true",
                    help="do not apply asm/patches.manifest")
    args = ap.parse_args()

    prg = args.prg.read_bytes()
    chr_rom = args.chr.read_bytes()
    if len(prg) != PRG_SIZE:
        raise SystemExit(f"{args.prg} is {len(prg)} bytes, expected {PRG_SIZE}")
    if len(chr_rom) != CHR_SIZE:
        raise SystemExit(f"{args.chr} is {len(chr_rom)} bytes, expected {CHR_SIZE}")

    image = bytearray(prg)
    if not args.no_patches:
        regions = patches.load()
        if regions:
            resolved = patches.verify(regions, args.cart_dir)
            prgs = {name: patches.body(p) for name, p in resolved.items()}
            cart = prgs[regions[0].cart] if len(prgs) == 1 else patches._mixed(
                prgs, regions)
            report = patches.apply(image, cart, regions, args.cart_dir)
            print(patches.summarise(report))

    args.rom.parent.mkdir(parents=True, exist_ok=True)
    body = INES_HEADER + bytes(image) + chr_rom
    args.rom.write_bytes(body)
    print(f"{args.rom}  {len(body)} bytes")
    print(f"  body sha1 {hashlib.sha1(bytes(image) + chr_rom).hexdigest()}")
    print(f"  (the cartridge's own body sha1 is "
          f"{patches.CARTS['release']['sha1']} -- a development build built "
          f"against later source, so it does not match; see PROVENANCE.md "
          f"section 6)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except patches.PatchError as exc:
        print(f"mkrom: {exc}", file=sys.stderr)
        raise SystemExit(2)
