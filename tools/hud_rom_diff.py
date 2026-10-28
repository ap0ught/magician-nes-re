#!/usr/bin/env python3
"""Compare two iNES ROMs and audit exact byte ranges changed by a HUD build.

Usage: python3 tools/hud_rom_diff.py magician-rebuilt.nes hud-build.nes --json diff.json
The baseline is the user's rebuilt ROM, not retail or Beta 1.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def layout(data: bytes) -> tuple[int, int]:
    if len(data) < 16 or data[:4] != b"NES\x1a":
        raise ValueError("not an iNES ROM")
    if data[7] & 0x0c == 0x08:
        raise ValueError("NES 2.0 header: use a NES 2.0-aware parser")
    if data[6] & 0x04:
        raise ValueError("trainer present: unsupported in this comparison")
    prg, chr_ = data[4] * 16384, data[5] * 8192
    if len(data) != 16 + prg + chr_:
        raise ValueError("ROM length does not match header")
    return prg, chr_


def compare(base: bytes, candidate: bytes) -> dict:
    prg, chr_ = layout(base)
    if layout(candidate) != (prg, chr_):
        raise ValueError("ROM PRG/CHR geometry changed")
    if base[:16] != candidate[:16]:
        raise ValueError("iNES header changed")
    ranges = []
    start = None
    for i, (a, b) in enumerate(zip(base, candidate)):
        if a != b and start is None:
            start = i
        if a == b and start is not None:
            ranges.append((start, i))
            start = None
    if start is not None:
        ranges.append((start, len(base)))
    sections = {"header": (0, 16), "prg": (16, 16 + prg),
                "chr": (16 + prg, 16 + prg + chr_)}
    counts = {name: sum(a != b for a, b in zip(base[lo:hi], candidate[lo:hi]))
              for name, (lo, hi) in sections.items()}
    details = []
    for lo, hi in ranges:
        region = "prg" if lo < 16 + prg else "chr"
        item = {"file_start": lo, "file_end_exclusive": hi, "length": hi-lo,
                "region": region}
        if region == "prg":
            offset = lo - 16
            item["prg_slot_8k"] = offset // 8192
            item["slot_offset"] = offset % 8192
            item["cpu_address_if_slot_15"] = (0xe000 + offset % 8192) if offset // 8192 == 15 else None
        details.append(item)
    return {"baseline_sha256": hashlib.sha256(base).hexdigest(),
            "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
            "different_bytes": counts, "ranges": details}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("baseline", type=Path)
    p.add_argument("candidate", type=Path)
    p.add_argument("--json", type=Path, dest="json_path")
    args = p.parse_args()
    report = compare(args.baseline.read_bytes(), args.candidate.read_bytes())
    print(json.dumps(report, indent=2))
    if args.json_path:
        args.json_path.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
