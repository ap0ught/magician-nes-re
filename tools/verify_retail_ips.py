#!/usr/bin/env python3
"""Validate the retail-only IPS asset without requiring copyrighted ROM bytes."""
from __future__ import annotations
import hashlib
import sys
from pathlib import Path

EXPECTED_SHA256 = "11f0b4e51e41086a4e8f1c3cd261df9b8da831f8f24ba3369abf338abb9911a5"
EXPECTED_RECORDS = 23
EXPECTED_BYTES = 662
EXPECTED_PATCHED_SHA1 = "ef8bb81bd6da2af0ee4b82671a636ba3a506e8eb"
EXPECTED_RETAIL_SHA1 = "7b0da149c3892b9aec3e97400b79e1089cb8cc47"
EXPECTED_WINDOWS = (
    (0x0056, 3), (0x19C8, 548), (0x1FBC1, 11),
    (0x1FBCC, 27), (0x1FBE7, 9), (0x1FBF0, 25), (0x1FFA0, 56),
)

def parse(patch: bytes) -> list[tuple[int, bytes]]:
    if not patch.startswith(b"PATCH"):
        raise ValueError("not an IPS patch")
    pos = 5
    records = []
    while True:
        if patch[pos:pos + 3] == b"EOF":
            if pos + 3 != len(patch):
                raise ValueError("trailing data after EOF")
            return records
        if pos + 5 > len(patch):
            raise ValueError("truncated record")
        offset = int.from_bytes(patch[pos:pos + 3], "big")
        size = int.from_bytes(patch[pos + 3:pos + 5], "big")
        pos += 5
        if size == 0:
            if pos + 3 > len(patch):
                raise ValueError("truncated RLE")
            length = int.from_bytes(patch[pos:pos + 2], "big")
            payload = patch[pos + 2:pos + 3] * length
            pos += 3
        else:
            if pos + size > len(patch):
                raise ValueError("truncated payload")
            payload = patch[pos:pos + size]
            pos += size
        if not payload:
            raise ValueError("empty record")
        if not any(start + 16 <= offset and offset + len(payload) <= start + 16 + length
                   for start, length in EXPECTED_WINDOWS):
            raise ValueError(f"patch record at 0x{offset:06x} outside retail placements")
        records.append((offset, payload))

def main() -> None:
    patch = Path(sys.argv[1]).read_bytes()
    digest = hashlib.sha256(patch).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(f"wrong IPS SHA-256: {digest}")
    records = parse(patch)
    total = sum(len(data) for _, data in records)
    if len(records) != EXPECTED_RECORDS or total != EXPECTED_BYTES:
        raise SystemExit(f"unexpected records/payload: {len(records)}, {total}")
    print(f"Verified retail IPS: {len(patch)} bytes, {len(records)} records, {total} payload bytes")
    if len(sys.argv) > 2:
        retail = Path(sys.argv[2]).read_bytes()
        if hashlib.sha1(retail).hexdigest() != EXPECTED_RETAIL_SHA1:
            raise SystemExit("wrong retail ROM SHA-1; refusing to patch")
        result = bytearray(retail)
        for offset, data in records:
            if offset + len(data) > len(result):
                raise SystemExit("record beyond retail ROM")
            result[offset:offset + len(data)] = data
        if hashlib.sha1(result).hexdigest() != EXPECTED_PATCHED_SHA1:
            raise SystemExit("patched ROM SHA-1 mismatch")
        print("Retail ROM + IPS matches expected patched SHA-1")
if __name__ == "__main__":
    main()
