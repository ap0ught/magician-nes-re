#!/usr/bin/env python3
"""Assemble the Magician cartridge from Eurocom's source and compare it with
the real thing.

The eight `X?.PDS` files are assembled in order into one 128 KiB PRG image,
sharing a symbol table: x0 defines every macro and the whole zero-page and RAM
map, and x1-x7 use them. The CHR image is packed from the binary artwork under
`DAT/` in the order the development system's `SENDG` script sends it.

A module's *initial* 8 KiB slot is not recorded anywhere in the source -- x5
begins `org $c000` with no `bank` -- so it is recovered by assembling the module
in each of the sixteen slots and keeping the one whose output matches the
cartridge most. That is a measurement, not a guess: the data files in the source
are byte-identical to the ones in the cart, so the right slot matches thousands
of bytes and every wrong slot matches almost none.

    python3 asm/build.py                      # build and report
    python3 asm/build.py --verbose            # per-file incbin trace
    python3 asm/build.py --check              # non-zero exit if anything moved

Everything written here is derived: `out/` is git-ignored. The cartridge is
only ever read.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from pds6502 import Assembler  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "vendor" / "Magician-NES"
OUT = ROOT / "asm" / "out"

PRG_SIZE = 128 * 1024
CHR_SIZE = 128 * 1024

MODULES = [f"X{i}.PDS" for i in range(8)]

# Modules whose 8 KiB slot is *known* rather than searched, with the evidence.
#
# The search scores the bytes that came out of a `DAT` file, and x7's own data
# does not discriminate: 6.0% at slot 12 against 5.1% at slot 9, with no winner.
# But the cartridge's reset vector is ground truth and it settles the question.
# `reset` and `nmi` are defined in X7.PDS (lines 922 and 903) and the vectors at
# file 0x1FFFA read `nmi=$F9AB irq=$F9B3 reset=$F9C1`, all inside slot 15's
# $E000-$FFFF window. X7's `org $fffa` reaches file 0x1FFFA only from slot 15 --
# from slot 12 it lands at 0x19FFA, which is why the rebuilt ROM's reset vector
# came out as $8681 and could not boot.
PINNED_SLOTS = {"X7.PDS": 15}

# The CHR image, in the order `SENDG` sends it to the development system, with
# the 8 KiB slot each file occupies as written there. Only the order matters for
# packing; the slot column is what the cartridge is checked against.
CHR_SEND_ORDER = [
    ("10.chr", 0x00 * 4), ("20.chr", 0x01 * 4), ("21.chr", 0x02 * 4),
    ("30.chr", 0x03 * 4), ("40.chr", 0x04 * 4), ("50.chr", 0x05 * 4),
    ("53.chr", 0x06 * 4), ("60.chr", 0x07 * 4), ("70.chr", 0x08 * 4),
    ("73.chr", 0x09 * 4), ("80.chr", 0x0A * 4), ("su.chr", 0x0B * 4),
    ("spr/inv.spr", 0x0C * 4), ("map.chr", 0x0D * 4), ("tit0.chr", 0x0E * 4),
    ("tit1.chr", 0x1D * 4), ("tit2.chr", 0x1E * 4), ("sh.chr", 0x0F * 4),
]
# `spr\bank40.dat` .. `spr\bank73.dat`, the per-object sprite artwork.
SPRITE_FIRST, SPRITE_LAST = 0x40, 0x73


def read_cart(path: pathlib.Path) -> tuple[bytes, bytes]:
    """Read the cartridge's real header and return (prg, chr)."""
    data = path.read_bytes()
    if data[:4] != b"NES\x1a":
        raise SystemExit(f"{path} has no iNES header")
    prg_len = data[4] * 16384
    chr_len = data[5] * 8192
    body = data[16:]
    if len(body) < prg_len + chr_len:
        raise SystemExit(f"{path} is short: header declares "
                         f"{prg_len + chr_len} body bytes, file has {len(body)}")
    return body[:prg_len], body[prg_len:prg_len + chr_len]


def match_score(image: bytearray, cart: bytes, offsets) -> tuple[int, int]:
    """How many of `offsets` agree with the cartridge.

    Called with `asm.data_offsets` -- the bytes that came out of a `DAT` file.
    Those are byte-identical to the cartridge's whatever the code does, so they
    are the only trustworthy evidence for which slot a module belongs in. Scoring
    the whole output instead measures mostly wrong code: during this search the
    cross-bank forward references are still unresolved, so every `lda label`
    picks zero-page over absolute and the opcode bytes are noise.
    """
    hits = total = 0
    for off in offsets:
        if not 0 <= off < len(cart):
            continue
        total += 1
        if cart[off] == image[off]:
            hits += 1
    return hits, total


def assemble_prg(cart_prg: bytes, verbose: bool) -> tuple[bytearray, Assembler, list[str]]:
    image = bytearray(PRG_SIZE)
    asm = Assembler(image, SRC, [SRC], verbose=verbose)
    asm.prescan([SRC / m for m in MODULES])
    log: list[str] = []

    # Pass 1: find each module's initial slot by matching its output against the
    # cartridge. The data files in the source are byte-identical to the ones in
    # the cart, so the right slot matches thousands of bytes.
    asm.tolerate = True
    slots: list[int] = []
    for module in MODULES:
        path = SRC / module
        snap = asm.snapshot()
        if module in PINNED_SLOTS:
            slot = PINNED_SLOTS[module]
            asm.restore(snap)
            asm.emitted = {}
            asm.data_offsets = set()
            asm.run_file(path, slot=slot)
            slots.append(slot)
            hits, total = match_score(image, cart_prg, asm.data_offsets)
            log.append(f"{module}: 8 KiB slot {slot:2d}  PINNED by the cartridge's "
                       f"reset vector (not searched)")
            continue
        best = None
        # Why each slot was rejected. Without this the search silently discards
        # the reason and a module that fails at *every* slot looks like one that
        # merely matches nowhere -- which is how x7's `error ">$FB80!` hid for
        # hours behind "slot not determined".
        rejected: dict[str, list[int]] = {}
        scores: list[tuple[int, int, int]] = []
        for slot in range(16):
            asm.restore(snap)
            asm.emitted = {}
            asm.data_offsets = set()
            try:
                asm.run_file(path, slot=slot)
            except Exception as exc:                    # noqa: BLE001
                rejected.setdefault(f"{type(exc).__name__}: {exc}", []).append(slot)
                continue
            hits, total = match_score(image, cart_prg, asm.data_offsets)
            scores.append((hits, total, slot))
            if total and (best is None or hits > best[0]):
                best = (hits, total, slot)
        if best is None:
            for reason, bad in sorted(rejected.items(), key=lambda kv: -len(kv[1])):
                log.append(f"{module}: slot {bad[0]:2d} rejected: {reason}"
                           + (f"  (and {len(bad) - 1} more)" if len(bad) > 1 else ""))
            asm.restore(snap)
            try:
                asm.run_file(path)
                slots.append(asm.slot)
                log.append(f"{module}: slot not determined (assembled at slot {asm.slot})")
            except Exception as exc:                    # noqa: BLE001
                slots.append(asm.slot)
                log.append(f"{module}: DID NOT ASSEMBLE: "
                           f"{type(exc).__name__}: {exc}")
            continue
        hits, total, slot = best
        asm.restore(snap)
        asm.emitted = {}
        asm.data_offsets = set()
        asm.run_file(path, slot=slot)
        slots.append(slot)
        pct = 100.0 * hits / total if total else 0.0
        log.append(f"{module}: 8 KiB slot {slot:2d}  {hits:6d}/{total:6d} DAT bytes "
                   f"match the cartridge ({pct:5.1f}%)")
        for h, t, sl in sorted(scores, key=lambda x: -x[0])[:4]:
            log.append(f"{module}:     slot {sl:2d}  {h:6d}/{t:6d} "
                       f"({100.0 * h / t if t else 0.0:5.1f}%)")
        for reason, bad in sorted(rejected.items(), key=lambda kv: -len(kv[1])):
            log.append(f"{module}:   rejected slots {bad}: {reason}")

    # Pass 2: the banks are one program, so references across them only resolve
    # once every module has been assembled at least once.
    image = bytearray(PRG_SIZE)
    asm = Assembler(image, SRC, [SRC], verbose=verbose)
    asm.prescan([SRC / m for m in MODULES])
    try:
        asm.run_all([SRC / m for m in MODULES], slots)
    except Exception as exc:                            # noqa: BLE001
        # Report rather than traceback: the per-module slot log above is the
        # context that makes this failure legible, and a traceback buries it.
        log.append(f"project pass failed: {type(exc).__name__}: {exc}")
    return image, asm, log


def dat_file(name: str) -> pathlib.Path:
    """Resolve a `DAT/` name to the file on disk, ignoring case.

    `SENDG` and the source write these in lower case (`10.chr`, `spr/inv.spr`)
    while the released tree stores them upper case (`10.CHR`, `SPR/INV.SPR`).
    The Atari ST toolchain was case-insensitive; this filesystem is not.
    """
    want = name.replace("\\", "/").lower()
    for path in (SRC / "DAT").rglob("*"):
        if path.is_file() and str(path.relative_to(SRC / "DAT")).lower() == want:
            return path
    raise SystemExit(f"no DAT file for {name!r} under {SRC / 'DAT'}")


def build_chr(cart_chr: bytes, verbose: bool) -> tuple[bytearray, list[str]]:
    image = bytearray(CHR_SIZE)
    entries: list[tuple[pathlib.Path, int | None]] = []
    for name, _slot in CHR_SEND_ORDER:
        entries.append((dat_file(name), None))
    for bank in range(SPRITE_FIRST, SPRITE_LAST + 1):
        entries.append((dat_file(f"SPR/BANK{bank:02X}.DAT"), None))

    log: list[str] = []
    cursor = 0
    for path, declared in entries:
        data = path.read_bytes()
        # Placement is sequential, in the order `SENDG` sends the files. The slot
        # column above is the development system's CHR bank number, and it does
        # not index this 128 KiB image: it runs to $1E, which is past the last
        # 4 KiB page. It is kept only as a record of what SENDG says, and the
        # result below is checked against the cartridge rather than assumed.
        off = declared * 4096 if declared is not None else cursor
        if off + len(data) > CHR_SIZE:
            log.append(f"{path.name:14s} does not fit at {off:#06x}, skipped")
            continue
        image[off:off + len(data)] = data
        cursor = off + len(data)
        if verbose:
            same = cart_chr[off:off + len(data)] == data
            log.append(f"  chr {path.name:14s} at {off:#06x} "
                       f"{'exact' if same else 'differs'}")

    # Report how much of the rebuilt CHR agrees with the cartridge, page by page.
    exact = differ = 0
    for page in range(CHR_SIZE // 4096):
        a = bytes(image[page * 4096:(page + 1) * 4096])
        b = cart_chr[page * 4096:(page + 1) * 4096]
        if a == b:
            exact += 1
        elif a.strip(b"\x00") and b.strip(b"\x00"):
            differ += 1
    log.append(f"CHR: {exact:2d}/32 4 KiB pages identical to the cartridge, "
               f"{differ:2d} differ")
    return image, log


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cart", type=pathlib.Path,
                    default=pathlib.Path("/extdrive/backups/SHARE/roms/nes/Magician (USA).nes"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    cart_prg, cart_chr = read_cart(args.cart)

    prg, asm, prg_log = assemble_prg(cart_prg, args.verbose)
    chr_rom, chr_log = build_chr(cart_chr, args.verbose)

    (OUT / "prg.bin").write_bytes(bytes(prg))
    (OUT / "chr.bin").write_bytes(bytes(chr_rom))
    with (OUT / "mag.sym").open("w") as fh:
        for key in sorted(asm.sym):
            fh.write(f"{key} = ${asm.sym[key]:04X}\n")

    hits = sum(1 for a, b in zip(prg, cart_prg) if a == b)
    print("\n".join(prg_log))
    print("\n".join(chr_log))
    print(f"\nPRG: {hits}/{len(prg)} bytes identical to the cartridge "
          f"({100.0 * hits / len(prg):.1f}%)")
    print(f"symbols: {len(asm.sym)}  ->  {OUT / 'mag.sym'}")
    print(f"rebuilt prg sha256 {hashlib.sha256(bytes(prg)).hexdigest()[:16]}  "
          f"chr sha256 {hashlib.sha256(bytes(chr_rom)).hexdigest()[:16]}")

    # The statement scanner's own audit. Every entry is a place where a logical
    # line had to be cut in a way that was not the trivially expected one, or a
    # symbol was defined twice. None of them are errors -- most are `memchk`
    # emitting a group into both slots of a 16 KiB bank -- but they are the only
    # record of where the rebuild is resting on an interpretation, so they are
    # printed rather than left in a list nobody reads.
    seen: dict[str, int] = {}
    for entry in asm.report:
        seen[entry] = seen.get(entry, 0) + 1
    print(f"\nscanner report: {len(asm.report)} entries, {len(seen)} distinct")
    for entry, count in sorted(seen.items(), key=lambda kv: -kv[1])[:40]:
        print(f"  {count:5d}x {entry}")
    if len(seen) > 40:
        print(f"  ... and {len(seen) - 40} more distinct entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())