#!/usr/bin/env python3
"""Extract plain source text from PDS 1.26 project files.

The Eurocom Magician source ships as eight ``X?.PDS`` files. Each is a binary
container produced by the Atari ST toolchain "PDS" (P.D.Systems Ltd 1985-88):
a fixed header holding the project's symbol dictionary, then at a fixed offset
the source text itself, with every line terminated by ``CR NUL`` instead of
``CRLF``. Trailing binary closes the file.

This tool rewrites each container as the plain text it contains, so the source
can be read, diffed and fed to an assembler. It is a straight decode: no line
is reordered, dropped or rewrapped.

    python3 tools/pds_extract.py --src vendor/Magician-NES --out pds-text

The ``--check`` mode re-extracts and diffs against what is on disk, so a stale
copy cannot go unnoticed.
"""

from __future__ import annotations

import argparse
import difflib
import pathlib
import sys

# Offset of the source text inside every X?.PDS container. Verified against
# all eight files: each one's first long printable run starts here, and every
# byte from here to the closing footer is text (no other control characters).
BODY_OFFSET = 0x200

# Every file ends with a short binary footer after the final source line.
FOOTER_LEN = 2


def decode(container: bytes) -> str:
    """Return the plain-text source held by one PDS container."""
    body = container[BODY_OFFSET:]
    if body.endswith(b"\x00" * FOOTER_LEN):
        body = body[:-FOOTER_LEN]
    text = body.decode("latin-1")
    # CR NUL is this toolchain's line terminator.
    text = text.replace("\r\x00", "\n")
    stray = [i for i, ch in enumerate(text) if ch == "\x00"]
    if stray:
        raise ValueError(f"{len(stray)} stray NUL byte(s) in body, first at {stray[0]}")
    return text


def sources(src_dir: pathlib.Path) -> list[pathlib.Path]:
    return sorted(src_dir.glob("X?.PDS"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=pathlib.Path, default=pathlib.Path("vendor/Magician-NES"))
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("pds-text"))
    ap.add_argument("--check", action="store_true", help="fail if the output on disk is stale")
    args = ap.parse_args()

    files = sources(args.src)
    if not files:
        print(f"no X?.PDS under {args.src}", file=sys.stderr)
        return 2

    rc = 0
    for path in files:
        text = decode(path.read_bytes())
        out = args.out / (path.stem.lower() + ".pds")
        if args.check:
            have = out.read_text() if out.exists() else ""
            if have != text:
                print(f"STALE {out}", file=sys.stderr)
                diff = difflib.unified_diff(
                    have.splitlines(), text.splitlines(), "on disk", "extracted", lineterm="", n=1
                )
                for i, line in enumerate(diff):
                    if i >= 20:
                        print("  ...", file=sys.stderr)
                        break
                    print("  " + line, file=sys.stderr)
                rc = 1
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text)
            print(f"{path.name}: {len(text.splitlines())} lines -> {out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())