"""The build's target cartridge, resolved from `asm/patches.py`'s registry.

Every tool in `tools/` used to carry its own literal
`"/extdrive/backups/SHARE/roms/nes/Magician (USA).nes"`. That was the release,
which is a cartridge a year newer than the source this project builds -- see
`asm/patches.py`'s `DEFAULT_CART`. Worse, a literal in eleven files is eleven
places to forget when the target changes, and forgetting one produces a tool
that quietly measures a different cartridge and prints a confident number.

So there is one definition, here, and it comes from the registry rather than
from a path written down twice:

    from cartref import DEFAULT_CART          # the build's target
    from cartref import cart                 # any registered dump, by name

`tools/carts.py` checks that registry against the bytes on disk. This module
only resolves names to paths; it deliberately does not verify anything, so that
"which file" and "is it the file you think" stay separate questions.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "asm") not in sys.path:
    sys.path.insert(0, str(ROOT / "asm"))

import patches  # noqa: E402

# The dump this project builds against: Beta 1, 1990-03-02.
DEFAULT_CART_NAME: str = patches.DEFAULT_CART
DEFAULT_CART: pathlib.Path = patches.cart_path(patches.DEFAULT_CART)


def cart(name: str) -> pathlib.Path:
    """Path of a registered dump, by registry name (`beta1`, `release`, ...)."""
    return patches.cart_path(name)


def full_sha1(name: str) -> str:
    """Whole-file SHA1 of a registered dump, for `MAGICIAN_EXPECT_SHA1`."""
    entry = patches.CARTS[name]
    digest = entry.get("sha1_full")
    if not digest:
        raise SystemExit(f"the registry records no sha1_full for {name!r}; run "
                         f"tools/carts.py to see why")
    return str(digest)


def battery(name: str = DEFAULT_CART_NAME) -> bool:
    """Whether *name*'s header has the battery bit SET.

    Derived per dump, never assumed. Beta 1's is CLEAR, so a stale
    `NES/SaveRAM/` cannot affect it and a tool that relies on battery semantics
    rather than merely wiping it is wrong for it.
    """
    return bool(patches.CARTS[name]["battery"])


if __name__ == "__main__":
    print(DEFAULT_CART)