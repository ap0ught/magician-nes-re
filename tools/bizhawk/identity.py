#!/usr/bin/env python3
"""Derive the ROM identity window that tools/bizhawk/run.sh asks BizHawk to confirm.

    python3 tools/bizhawk/identity.py <rom>          # KEY=VALUE lines, for run.sh
    python3 tools/bizhawk/identity.py <rom> --quiet  # exit code only

WHY THIS IS PYTHON AND NOT MORE BASH
------------------------------------
This is the whole of run.sh's byte-level derivation, and it used to be bash.
Two of the worst failures in this project's history were in it, and both were
*quiet* -- they printed a number and nothing raised:

  * `head -c 1 "$ROM" | tail -c +5` asks a one-byte stream for byte 5. It gets
    nothing. `PRG_LEN` becomes 0, the vectors are read at file offset 10 --
    which is the iNES header's own zero padding -- and the harness reported
    **three zero vectors without complaint**. Header byte 4 is the FIFTH byte,
    so it has to be `head -c 5 | tail -c 1`; a file that gets that wrong cannot
    be told apart from a file that has no header.
  * the iNES vector order is NMI ($FFFA), **RESET** ($FFFC), IRQ/BRK ($FFFE).
    Reading the three little-endian words as NMI, IRQ, RESET swaps two labels
    and produces three numbers that all look perfectly fine.

Reading header bytes with shell arithmetic is a way of writing these bugs. The
arithmetic lives here, in one place, where a wrong value is an exception rather
than a plausible line of output.

WHAT IS DERIVED, AND WHAT IS ASSUMED
------------------------------------
Nothing here is written down as a literal except the 16-byte header size, the
16 KiB/8 KiB PRG/CHR unit sizes, and the 6 bytes of vectors, all of which are
properties of the iNES format rather than of any dump. Everything else comes out
of the file:

  * PRG and CHR sizes from header bytes 4 and 5, then checked against the file
    length. A declared size the file does not have is an error, not a truncated
    read that happens to be in range.
  * CHR = 0 in byte 5 means CHR-*RAM*, not "no graphics". It is legal, and the
    file is then 16 + PRG bytes long.
  * the three vectors from the last six bytes of PRG, in NMI/RESET/IRQ order.
  * the identity window: 48 bytes of CPU address space starting 8 bytes below the
    *lowest* of the three vectors, then checked to actually contain all three.
    That check is the point -- an off-by-16 in the file offset is silent,
    because the bytes read back are still real cartridge bytes, just the ones 16
    earlier.
  * the file offset of that window. CPU addresses in the fixed $E000-$FFFF
    window sit at PRG offset +$10000, and the PRG starts after the 16-byte
    header, so file = address + $10000 + 16.
  * the battery bit from header byte 6 (bit 1), per dump, never assumed.
    Beta 1 is $40 -- clear; the release and the other betas are $42 -- set.
    `NES/SaveRAM/` only applies to the ones where it is set.

WHAT IT DOES NOT DO
-------------------
It does not check the ROM is *the cartridge anyone had in mind* -- only that it
is the file that was pointed at. run.sh's `MAGICIAN_EXPECT_SHA1` is what pins
the former, and a self-consistent tampered file passes this file's checks. It
does not verify the vectors' contents: a dump whose vectors point into open
bus would produce a window of real-but-meaningless cartridge bytes, so each
vector is required to land inside $C000-$FFFF, which is the only window these
MMC3 128 KiB dumps have.

Exit status is 2 on any IdentityError, with the reason on stderr, so run.sh can
fail before the emulator is launched.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

HEADER_SIZE = 16
PRG_UNIT = 16384          # header byte 4 counts 16 KiB units
CHR_UNIT = 8192           # header byte 5 counts 8 KiB units
VECTOR_BYTES = 6          # three little-endian words at the top of PRG
WINDOW_LEN = 48           # the identity window, in bytes of CPU address space
WINDOW_LEAD = 8           # ...starting this far below the lowest vector

# Every dump of this title is 128 KiB of PRG on MMC3. This is a *range*, not an
# expectation: a value outside it means the header was misread, and every offset
# derived from it below would be wrong. It is deliberately a range rather than
# "== 131072" so the test suite can check the rejection as well as the accept.
PRG_MIN, PRG_MAX = 0x8000, 0x100000

# All three vectors of an MMC3 dump with 128 KiB of PRG live in the fixed
# $E000-$FFFF window. A vector outside it means the offset arithmetic is wrong.
VECTOR_LO, VECTOR_HI = 0xC000, 0xFFFF

_HEX = re.compile(r"\A[0-9a-f]+\Z")


class IdentityError(Exception):
    """The file cannot be identified. Never downgraded to a warning."""


def _one_byte(rom: pathlib.Path, offset: int, what: str) -> int:
    """Byte `offset` of `rom`, as an int. Raises if it is not there.

    This is the function whose absence produced the zero-vector report. A byte
    that cannot be read is an error here; it is not a 0 that gets multiplied.
    """
    with rom.open("rb") as fh:
        fh.seek(offset)
        raw = fh.read(1)
    if len(raw) != 1:
        raise IdentityError(
            f"{rom} is too short to hold {what} (byte {offset}, 0x{offset:02X}); "
            f"reading it anyway would have produced a 0 and every offset derived "
            f"from it would have been silently wrong")
    return raw[0]


def _slice(rom: pathlib.Path, offset: int, length: int) -> bytes:
    with rom.open("rb") as fh:
        fh.seek(offset)
        raw = fh.read(length)
    if len(raw) != length:
        raise IdentityError(
            f"{rom} is {rom.stat().st_size} bytes, too short for the {length} "
            f"bytes at file offset 0x{offset:06X}")
    return raw


def is_ines(rom: pathlib.Path) -> bool:
    """Whether the file carries a 16-byte iNES header.

    `data[:4] == b"NES\x1a"` -- the magic is FOUR bytes and the fourth is $1A,
    which the old `head -c 3` comparison never looked at.
    """
    with rom.open("rb") as fh:
        return fh.read(4) == b"NES\x1a"


def prg_chr_lengths(rom: pathlib.Path) -> tuple[int, int, int]:
    """(header_size, prg_len, chr_len) from the file's own header.

    `header_size` is 0 for a raw PRG image with no header, which is how run.sh
    has always accepted one: the file offset of a fixed-window CPU address is
    still address + $10000.
    """
    if not is_ines(rom):
        size = rom.stat().st_size
        if size < PRG_MIN:
            raise IdentityError(
                f"{rom} is {size} bytes and has no iNES header, so its PRG size "
                f"cannot be derived; every dump of this title is at least "
                f"{PRG_MIN} bytes of PRG")
        return 0, size, 0

    size = rom.stat().st_size
    if size < HEADER_SIZE:
        raise IdentityError(
            f"{rom} is {size} bytes: it begins with the iNES magic but cannot "
            f"hold the {HEADER_SIZE}-byte header, so header bytes 4 and 5 -- "
            f"the PRG and CHR sizes -- are not there to be read")
    prg = _one_byte(rom, 4, "header byte 4 (the PRG size)") * PRG_UNIT
    chr_ = _one_byte(rom, 5, "header byte 5 (the CHR size)") * CHR_UNIT
    # The range check, and it is an error rather than a clamp. A PRG length of
    # 0 is the specific value this harness once reported three zero vectors on.
    if prg < PRG_MIN or prg > PRG_MAX:
        raise IdentityError(
            f"{rom} declares a PRG length of {prg} bytes "
            f"(0x{prg:x}). Every dump of this title is {PRG_MIN}-{PRG_MAX:#x} "
            f"bytes; a value outside that means header byte 4 was misread, and "
            f"every offset derived from it would be wrong. A PRG length of 0 is "
            f"what a failed read of that byte looks like.")
    if size != HEADER_SIZE + prg + chr_:
        raise IdentityError(
            f"{rom} is {size} bytes; its header declares {HEADER_SIZE} + {prg} "
            f"PRG + {chr_} CHR = {HEADER_SIZE + prg + chr_}. Reading on past "
            f"the end of the file would report vectors out of the header's zero "
            f"padding.")
    return HEADER_SIZE, prg, chr_


def vectors(rom: pathlib.Path, hdr: int, prg: int) -> tuple[int, int, int]:
    """(nmi, reset, irq) from the last six bytes of PRG.

    iNES order is NMI, **RESET**, IRQ/BRK. RESET is in the middle, which is the
    whole reason this is a named tuple rather than three variables in a row.
    """
    off = hdr + prg - VECTOR_BYTES
    b = _slice(rom, off, VECTOR_BYTES)
    nmi = b[0] | (b[1] << 8)
    reset = b[2] | (b[3] << 8)
    irq = b[4] | (b[5] << 8)
    for name, v in (("nmi", nmi), ("reset", reset), ("irq", irq)):
        if v < VECTOR_LO or v > VECTOR_HI:
            raise IdentityError(
                f"{rom}'s {name} vector is ${v:04X}, outside "
                f"${VECTOR_LO:04X}-${VECTOR_HI:04X}. These dumps are MMC3 with "
                f"128 KiB of PRG, so all three vectors live in the fixed window; "
                f"a value out of range means the offset arithmetic is wrong.")
    return nmi, reset, irq


def identity(rom: pathlib.Path, window_len: int = WINDOW_LEN) -> dict:
    """Every derived fact about `rom`, or raise IdentityError."""
    rom = pathlib.Path(rom)
    if not rom.is_file():
        raise IdentityError(f"no file at {rom}")
    hdr, prg, chr_ = prg_chr_lengths(rom)
    nmi, reset, irq = vectors(rom, hdr, prg)

    win_at = min(nmi, reset, irq) - WINDOW_LEAD
    for name, v in (("nmi", nmi), ("reset", reset), ("irq", irq)):
        if v < win_at or v >= win_at + window_len:
            raise IdentityError(
                f"the identity window ${win_at:04X}+{window_len} does not "
                f"contain {name} = ${v:04X}. Widen the window or move it; do not "
                f"paper over it, because a window that misses a vector reads real "
                f"cartridge bytes that prove nothing.")
    win_off = win_at + 0x10000 + hdr
    win_hex = _slice(rom, win_off, window_len).hex()

    battery = 0
    if hdr:
        # Header byte 6, bit 1 ($02). Derived per dump, never assumed: Beta 1 is
        # $40 (clear) and the release is $42 (set), and `NES/SaveRAM/` only
        # means anything for the latter.
        if _one_byte(rom, 6, "header byte 6 (the mapper/battery flags)") & 0x02:
            battery = 1

    return {
        "path": str(rom),
        "sha1": hashlib.sha1(rom.read_bytes()).hexdigest(),
        "hdr": hdr,
        "prg_len": prg,
        "chr_len": chr_,
        "vec_nmi": nmi,
        "vec_reset": reset,
        "vec_irq": irq,
        "win_at": win_at,
        "win_len": window_len,
        "win_off": win_off,
        "win_hex": win_hex,
        "battery": battery,
        # BizHawk names its save after the ROM's filename with only the last
        # extension removed, so this has to be `rsplit`, not `split`. `split(".")[0]`
        # turns "a.b.nes" into "a", which is not the name BizHawk would use --
        # and the whole point of the name is that a stale NES/SaveRAM entry can be
        # spotted. A wrong stem makes that check silently useless.
        "save_stem": rom.name.rsplit(".", 1)[0] if "." in rom.name else rom.name,
    }


def render(values: dict) -> str:
    """`KEY=VALUE` lines, for run.sh to read without an `eval`."""
    order = ("sha1", "hdr", "prg_len", "chr_len", "vec_nmi", "vec_reset",
             "vec_irq", "win_at", "win_len", "win_off", "win_hex", "battery",
             "save_stem")
    out = [f"path={values['path']}"]
    out += [f"{k}={values[k]}" for k in order]
    return "\n".join(out) + "\n"


def parse_rendered(text: str) -> dict:
    """The inverse of `render`, so a test can check the shell read without bash."""
    out: dict = {}
    for line in text.splitlines():
        k, _, v = line.partition("=")
        out[k] = v
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="derive the identity window run.sh asks BizHawk to confirm",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom", type=pathlib.Path)
    ap.add_argument("--window-len", type=int, default=WINDOW_LEN)
    ap.add_argument("--quiet", action="store_true", help="exit status only")
    args = ap.parse_args(argv)
    try:
        values = identity(args.rom, window_len=args.window_len)
    except IdentityError as exc:
        print(f"identity: {exc}", file=sys.stderr)
        return 2
    if not args.quiet:
        sys.stdout.write(render(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())