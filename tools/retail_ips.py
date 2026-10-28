#!/usr/bin/env python3
"""IPS of the modules this branch adds on top of the retail cart.

The parent is Magician (USA).nes (sha1 7b0da149…). The patched file is
asm/out/magician-rebuilt.nes from `make rom` on this branch. Every changed
byte has to fall inside one of the placements below. A difference anywhere
else is a mistake and the tool exits.

    python3 tools/retail_ips.py emit
    python3 tools/retail_ips.py apply <ips> <src.nes> <dst.nes>
    python3 tools/retail_ips.py check
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CART = pathlib.Path("/extdrive/backups/SHARE/roms/nes/Magician (USA).nes")
ROM = ROOT / "asm" / "out" / "magician-rebuilt.nes"
IPS_PATH = ROOT / "ips" / "retail-modules.ips"
CART_SHA1 = "7b0da149c3892b9aec3e97400b79e1089cb8cc47"
HEADER = 16

# PRG offsets (no iNES header) and lengths. File offset is PRG + HEADER.
# Measured on the retail image: $FBC1 is a 127-byte $00 run ending at
# SAM.SAM ($FC40); $FFA0 is an 80-byte $00 run ending at the MAGIC-*
# signature ($FFF0); $19C8 is slot 0's zero tail; $0056 is dotitle's
# jsr initdma.
PLACEMENTS = (
    ("INSPTITL.PDS", 0x0056, 3),
    ("INSPBODY.PDS", 0x19C8, 548),
    ("DRAW.PDS", 0x1FBC1, 11),
    ("AGGRO.PDS", 0x1FBCC, 27),
    ("HEALTH.PDS", 0x1FBE7, 9),
    ("WEAK.PDS", 0x1FBF0, 25),
    ("INSPECT.PDS", 0x1FFA0, 56),
)


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def emit(old: bytes, new: bytes) -> tuple[bytes, list[tuple[str, int, int]]]:
    if len(old) != len(new):
        raise SystemExit(f"size {len(old)} vs {len(new)}")
    owned = bytearray(len(old))
    for _name, prg, ln in PLACEMENTS:
        owned[prg + HEADER:prg + HEADER + ln] = b"\x01" * ln
    stray = [i for i, (a, b) in enumerate(zip(old, new)) if a != b and not owned[i]]
    if stray:
        raise SystemExit(
            "bytes differ outside the placed modules, first at "
            + ", ".join(f"${i:06X}" for i in stray[:8]))
    out = bytearray(b"PATCH")
    hunks: list[tuple[str, int, int]] = []
    for name, prg, ln in PLACEMENTS:
        base = prg + HEADER
        i = 0
        while i < ln:
            if old[base + i] == new[base + i]:
                i += 1
                continue
            j = i
            while j < ln and old[base + j] != new[base + j]:
                j += 1
            payload = new[base + i:base + j]
            off = base + i
            if off > 0xFFFFFF or len(payload) > 0xFFFF:
                raise SystemExit(f"{name}: hunk does not fit in an IPS record")
            rec_off = off.to_bytes(3, "big")
            if rec_off == b"EOF":
                raise SystemExit(f"{name}: offset ${off:06X} is the IPS end marker")
            out += rec_off + len(payload).to_bytes(2, "big") + payload
            hunks.append((name, off, len(payload)))
            i = j
    out += b"EOF"
    return bytes(out), hunks


def apply_ips(src: bytes, patch: bytes) -> bytes:
    if not patch.startswith(b"PATCH"):
        raise SystemExit("not an IPS (missing PATCH)")
    out = bytearray(src)
    i = 5
    while i + 3 <= len(patch):
        mark = patch[i:i + 3]
        if mark == b"EOF":
            return bytes(out)
        if i + 5 > len(patch):
            raise SystemExit("truncated IPS record")
        off = int.from_bytes(mark, "big")
        ln = int.from_bytes(patch[i + 3:i + 5], "big")
        i += 5
        if ln == 0:
            if i + 3 > len(patch):
                raise SystemExit("truncated RLE record")
            rle = int.from_bytes(patch[i:i + 2], "big")
            val = patch[i + 2]
            end = off + rle
            if end > len(out):
                raise SystemExit(f"RLE at ${off:06X} runs past the file")
            out[off:end] = bytes([val]) * rle
            i += 3
        else:
            end = off + ln
            if i + ln > len(patch) or end > len(out):
                raise SystemExit(f"record at ${off:06X} does not fit")
            out[off:end] = patch[i:i + ln]
            i += ln
    raise SystemExit("IPS has no EOF")


def cmd_emit() -> None:
    old, new = CART.read_bytes(), ROM.read_bytes()
    if sha1(old) != CART_SHA1:
        raise SystemExit(f"retail file sha1 is {sha1(old)}, expected {CART_SHA1}")
    patch, hunks = emit(old, new)
    IPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    IPS_PATH.write_bytes(patch)
    print(f"wrote {IPS_PATH.relative_to(ROOT)} ({len(patch)} bytes)")
    print(f"patched rom sha1 {sha1(new)}")
    for name, off, ln in hunks:
        print(f"  ${off:06X}  {ln:4d}  {name}")


def cmd_apply(ips: str, src: str, dst: str) -> None:
    out = apply_ips(pathlib.Path(src).read_bytes(), pathlib.Path(ips).read_bytes())
    pathlib.Path(dst).write_bytes(out)
    print(f"wrote {dst} sha1 {sha1(out)}")


def cmd_check() -> None:
    old = CART.read_bytes()
    new = ROM.read_bytes()
    patch = IPS_PATH.read_bytes()
    got_cart = sha1(old)
    if got_cart != CART_SHA1:
        raise SystemExit(f"retail sha1 {got_cart}")
    applied = apply_ips(old, patch)
    if applied != new:
        raise SystemExit("IPS applied to the retail file is not the rebuilt ROM")
    # Re-emit and require the file on disk to be that patch, so a hand edit
    # of the IPS cannot drift from the ROM.
    fresh, hunks = emit(old, new)
    if fresh != patch:
        raise SystemExit("ips/retail-modules.ips is not the delta of these two files")
    print(f"cart sha1 {got_cart}")
    print(f"patched sha1 {sha1(new)}")
    print(f"hunks {len(hunks)} bytes {sum(n for _, _, n in hunks)}")
    for name, off, ln in hunks:
        print(f"  ${off:06X}  {ln:4d}  {name}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == "emit":
        cmd_emit()
    elif cmd == "apply" and len(sys.argv) == 5:
        cmd_apply(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "check":
        cmd_check()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
