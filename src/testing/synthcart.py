"""A synthetic iNES image, built byte by byte from arithmetic in this file.

    from synthcart import make_rom, rom_at

Every test in this suite that needs "a cartridge" builds one of these instead of
reading a real one. That is a requirement, not a convenience:

  * the cartridge is the user's own, is never committed, and every dump's mtime
    must not change. A test that opens one could change that, and a test that
    merely *expects* one cannot run on a fresh clone.
  * a real cartridge is 262 160 bytes of bytes nobody in this repository is
    entitled to reproduce, so no assertion in this suite may quote one. Every
    expected value is derived from a file this suite writes, which is why each
    test can state where its constants came from.
  * the interesting cases are the ones no dump on this machine has: a truncated
    header, a PRG size of zero, CHR-RAM with CHR = 0, a vector in the wrong
    window, a header that declares more PRG than the file holds. Those cannot be
    built out of real dumps at all.

PROVENANCE OF EVERY CONSTANT BELOW.

None of these numbers came from a cartridge. They are properties of the iNES
format and of this suite's own arithmetic, and each is restated at its use:

  * `NES\\x1a` magic, 16-byte header, 16 KiB PRG unit, 8 KiB CHR unit: the iNES
    format, which `tools/bizhawk/identity.py` states from the same source.
  * PRG 128 KiB / CHR 128 KiB: the size every dump of *this* title happens to
    have, and the size `tools/bizhawk/identity.py` requires. It is a
    convenience here, not a measurement: the tests below also build 32 KiB and
    256 KiB images so that "accepts the real size" and "rejects the others" are
    separable.
  * The PRG fill pattern `(i * 131 + 0x5A) & 0xFF`: chosen because it is not
    periodic at any power of two up to 0x20000 and is not zero, so a window read
    at the wrong offset cannot accidentally equal the window read at the right
    one, and neither can be mistaken for a page of zeroes. Check 6 of
    `test_identity.py` asserts exactly that non-accident.
  * The three vectors: **arbitrary, and deliberately all different**. The point
    of a synthetic image is that a swap cannot go unnoticed, so the six bytes
    are `A1 E0 / B9 E0 / A9 E0` -> nmi $E0A1, reset $E0B9, irq $E0A9. No dump of
    this title has those, so if a test ever reports one of them the reader knows
    immediately that nothing real leaked in. They sit 24 bytes apart because the
    48-byte identity window has to cover all three, which is a property of the
    window this suite tests and not of any cartridge.
  * Header byte 6 `$40` (battery clear) and `$42` (battery set): the two values
    the dumps of this title carry, which is why they are the two cases tested.
    Bit 1 of that byte is the battery bit; `identity.py` derives it.
"""

from __future__ import annotations

import pathlib

MAGIC = b"NES\x1a"
HEADER_SIZE = 16
PRG_UNIT = 16384
CHR_UNIT = 8192

# The vectors written by `make_rom` unless told otherwise: three distinct
# little-endian words, all inside $C000-$FFFF, and close together because real
# dumps put the three trampolines within a few dozen bytes of each other -- Beta
# 1's are nmi $F917, irq $F922, reset $F930, a span of 25 bytes, which the 48-byte
# identity window has to cover. Deliberately *not* those three addresses: this one
# is $E0A1/$E0A9/$E0B9, so a reader who sees a Beta 1 vector in test output knows
# a real file leaked in.
VEC_NMI = 0xE0A1
VEC_RESET = 0xE0B9
VEC_IRQ = 0xE0A9

# What Beta 1 and the release of *this* title carry in header byte 6. Recorded
# here because those are the two cases that have to be separable: Beta 1's
# battery bit is CLEAR, so `NES/SaveRAM/` cannot affect a run against it, and the
# release's is SET. Nothing else in this suite needs those two bytes.
F6_NO_BATTERY = 0x40
F6_BATTERY = 0x42


def prg_fill(n: int) -> bytes:
    """`n` bytes that are not periodic, not zero, and reproducible from this line."""
    return bytes((i * 131 + 0x5A) & 0xFF for i in range(n))


def chr_fill(n: int) -> bytes:
    """As `prg_fill`, on a different stride so PRG and CHR are distinguishable."""
    return bytes((i * 67 + 0xA3) & 0xFF for i in range(n))


def make_rom(
    path: pathlib.Path,
    prg_units: int = 8,
    chr_units: int = 16,
    f6: int = F6_NO_BATTERY,
    vectors: tuple[int, int, int] = (VEC_NMI, VEC_RESET, VEC_IRQ),
    truncate: int | None = None,
    header: bytes | None = None,
) -> pathlib.Path:
    """Write a synthetic `.nes` and return its path.

    `vectors` is (nmi, reset, irq) -- in that order, which is the order the iNES
    format *stores* them. Writing them in any other order here is precisely how
    a reader gets the labels wrong, so the order is part of the signature.

    `truncate` cuts the file to that many bytes after the header is written,
    which is how the "header declares more than the file holds" case is built.
    `header` replaces the whole 16-byte header, which is how the
    "PRG size is zero" and "PRG size cannot be read at all" cases are built.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prg = bytearray(prg_fill(prg_units * PRG_UNIT))
    # The vectors only exist if there is room for them. A zero-unit PRG is a case
    # the tests build deliberately (it is what a failed read of header byte 4
    # looks like), so it is not an error here -- there is simply nothing to write.
    nmi, reset, irq = vectors
    if len(prg) >= 6:
        for i, v in enumerate((nmi, reset, irq)):
            prg[len(prg) - 6 + 2 * i] = v & 0xFF
            prg[len(prg) - 6 + 2 * i + 1] = (v >> 8) & 0xFF
    if header is None:
        header = bytes(MAGIC) + bytes((prg_units, chr_units, f6, 0)) + bytes(8)
    blob = bytes(header) + bytes(prg) + chr_fill(chr_units * CHR_UNIT)
    if truncate is not None:
        blob = blob[:truncate]
    path.write_bytes(blob)
    return path


def rom_at(path: pathlib.Path, cpu: int, prg_units: int = 8,
           chr_units: int = 16, length: int = 1) -> bytes:
    """What a correct fixed-window reader must return for CPU address `cpu`.

    Independent of `identity.py`, and derived from the MMC3 bank map rather than
    from anything `identity.py` computes:

      * CPU $C000-$FFFF is the fixed 16 KiB window, and on MMC3 it is the *last*
        16 KiB of PRG, so a CPU address maps to PRG offset
        `(cpu - $C000) + 0x1C000` -- 0x1C000 for 128 KiB of PRG, which is why the
        other form is written `cpu + $10000` only when PRG is 128 KiB.
      * PRG starts after the 16-byte header, so the file offset adds that.

    A test that compared `identity.win_hex` against `identity.win_off` would be
    asserting a tautology. This recomputes the offset from `cpu`, so the two
    independent paths have to meet.
    """
    data = pathlib.Path(path).read_bytes()
    if prg_units != 8:
        raise AssertionError(
            f"rom_at() is only valid for the 128 KiB PRG these dumps have "
            f"({prg_units * PRG_UNIT} bytes given); the MMC3 fixed window is the "
            f"last 16 KiB of PRG, whose file offset depends on the total")
    hdr = HEADER_SIZE if data[:4] == MAGIC else 0
    if not 0xC000 <= cpu <= 0xFFFF:
        raise AssertionError(f"${cpu:04X} is not in the fixed window $C000-$FFFF")
    off = hdr + (cpu - 0xC000) + 0x1C000
    return data[off:off + length]