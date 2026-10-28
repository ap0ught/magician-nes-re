#!/usr/bin/env python3
"""Whether a vendored .SRC edit is comments only.

LEGAL.md allows a `;` comment in the plain-text `.SRC` files under
`vendor/Magician-NES/`. The code and the data on those lines stay as they
were vendored. `tools/guard_staged.sh` and `src/testing/test_repo_hygiene.py`
both ask this module, so the two checks cannot drift.

A comment is a line whose first non-whitespace character is `;`, or the tail
of a line from the first `;` that is not inside a double quote. Blank lines
are ignored. Everything before that `;` is the code, and the code of the file
has to come out in the same order.
"""
from __future__ import annotations

import subprocess
import sys


def code_lines(text: str) -> list[str]:
    """The non-comment text of `text`, one entry per line that has any."""
    out: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith(";"):
            continue
        buf: list[str] = []
        quoted = False
        for ch in line:
            if ch == '"':
                quoted = not quoted
            elif ch == ";" and not quoted:
                break
            buf.append(ch)
        code = "".join(buf).rstrip()
        if code.strip():
            out.append(code)
    return out


def same_code(old: str, new: str) -> bool:
    return code_lines(old) == code_lines(new)


def _show(spec: str) -> str | None:
    p = subprocess.run(["git", "show", spec], capture_output=True, timeout=60)
    if p.returncode != 0:
        return None
    return p.stdout.decode("latin-1")


def staged_is_comment_only(path: str) -> bool:
    """Index versus HEAD. A missing blob is not a comment."""
    old = _show(f"HEAD:{path}")
    new = _show(f":{path}")
    if old is None or new is None:
        return False
    return same_code(old, new)


def worktree_is_comment_only(path: str) -> bool:
    """Working tree versus HEAD."""
    old = _show(f"HEAD:{path}")
    if old is None:
        return False
    try:
        new = open(path, encoding="latin-1").read()
    except OSError:
        return False
    return same_code(old, new)


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("--staged", "--worktree"):
        print("usage: vendor_comments.py --staged|--worktree PATH", file=sys.stderr)
        return 2
    ok = staged_is_comment_only(argv[2]) if argv[1] == "--staged" \
        else worktree_is_comment_only(argv[2])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
