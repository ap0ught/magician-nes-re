#!/usr/bin/env python3
"""Where N emulator windows go on a screen. Pure arithmetic, no display, no KWin.

    python3 tools/bizhawk/tile_geometry.py 3
    python3 tools/bizhawk/tile_geometry.py --json 5 1920 1080

WHY THIS IS A SEPARATE FILE
--------------------------
The brief for the window wall asked for a KWin script that "finds the EmuHawk
windows, tiles them into a grid, [and is] scaled to NES aspect", and separately
asked that the tiling decision be unit-testable *without a live KWin*. Those two
requirements cannot both be met in one file: `org.kde.kwin.Scripting` is only
answerable by a running compositor, so any assertion that needs it is a test
that cannot run in CI and, worse, a test whose result depends on whether the
developer's desktop happens to be up.

So the *decision* lives here and the *mechanisms* live elsewhere:

    tile_geometry.py   decide the grid            <- pure, tested here
    x11_tile.py        apply it (X11 configure)   <- needs a display
    kwin-tile.js       report/verify (KWin DBus)  <- needs a compositor

`src/testing/test_kwin_tile.py` exercises this file with no X server, no
emulator and no compositor, including N=1, N=3, N=5 and the property that
matters most -- that no cell ever extends past the screen it was given.

WHAT "SCALED TO NES ASPECT, NOT UPSCALED" MEANS HERE
----------------------------------------------------
Two separate rules, and they pull in opposite directions, so they are separated
rather than averaged:

  * **NES aspect** (`--aspect`, default 256/240) is the shape of the *cell* a
    window occupies. A grid of NES-aspect cells puts equal margins around every
    picture and makes the wall read as one image. It does not distort anything:
    BizHawk renders the emulated picture itself and letterboxes whatever window
    it is given, so the window's shape cannot skew the game.
  * **Never upscaled** (`--max-scale`, default 1.0) caps the *height* at the
    measured native EmuHawk window height. Scaling a NES picture beyond native
    adds no information and makes the emulator's own menus unusable, so the wall
    shrinks windows and never enlarges them.

`NATIVE_H = 503` is measured, not guessed: `tools/bizhawk/display.sh`'s header
records `586x503+22+22` for `magician-rebuilt [NES] - BizHawk` on this machine,
which is the window including its ~28px title bar. The cell width at scale 1 is
therefore `503 * 256/240 = 536`, NOT 586 -- that difference is the whole point of
the aspect rule, and getting it backwards is what produces a wall whose pictures
are 9% too wide.

HOW THE SHAPE IS CHOSEN
-----------------------
Every `(cols, rows)` with `cols * rows >= n` is tried, and the one that maximises
the scale wins, tie-broken towards more columns (a wide wall reads better than a
tall one).

The scale is computed from the **uncapped** best shape and only then capped.
That ordering matters, and it is the reason N=3 is stable: with the cap active,
`3x1` and `2x2` for N=3 both clamp to 1.0 and become indistinguishable, so the
shape would be decided by tie-break order alone and could come out 2x2 with an
empty quadrant. Ranking first and capping afterwards keeps the choice stable for
every `--max-scale`. (Measured: 3x1's uncapped scale is 1.1928 against 2x2's
1.0736 on 1920x1080, so the ranking is what picks 3x1 -- the tie-break is not
consulted. The tie-break still decides other cases; see `grid_shape`.)

THE INVARIANT
-------------
`assert_fits()` re-derives, from the returned cells alone, that every cell lies
inside the screen rect. The tiler calls it before it moves anything, and the
tests call it for every shape they check. A tiler that puts a window partly off
the right-hand edge of the screen is the exact "clutter" this was written to
remove, so the property is asserted rather than assumed -- integer rounding is
what would break it, since a cell of `round(1919.6)` is 1920 wide and starts at
x=0.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from typing import NamedTuple

# 256x240. The NES picture, and the default shape of a cell in the wall.
NES_ASPECT = 256.0 / 240.0

# Measured on this machine, 2026-10-06, by EmuHawk mapping a real window:
# `586x503+22+22` for "magician-rebuilt [NES] - BizHawk". 503 is the full window
# height INCLUDING the title bar; the picture is smaller than that. The height is
# the cap rather than the width because the aspect rule fixes the width from it.
NATIVE_H = 503


class Cell(NamedTuple):
    """One window's placement, in absolute screen coordinates.

    The field is `slot`, not `index`: `NamedTuple` already has a method called
    `index`, and shadowing a tuple method with an `int` is both a type error and
    a trap -- `cells.index(c)` would stop working for every caller downstream.
    """

    slot: int
    x: int
    y: int
    w: int
    h: int
    scale: float

    def as_dict(self) -> dict:
        return {"slot": self.slot, "x": self.x, "y": self.y,
                "w": self.w, "h": self.h, "scale": round(self.scale, 6)}


def grid_shape(n: int, screen_w: int, screen_h: int, cell_w: float, cell_h: float
               ) -> tuple[int, int, float]:
    """The `(cols, rows, uncapped_scale)` that fits `n` cells on the screen best.

    "Best" is the largest uniform scale, because a larger scale means more pixels
    per NES picture, which is the only thing the wall is trying to maximise.
    """
    if n < 1:
        raise ValueError(f"grid_shape needs at least one cell, got n={n}")
    if screen_w < 1 or screen_h < 1:
        raise ValueError(f"screen must be positive, got {screen_w}x{screen_h}")
    if cell_w <= 0 or cell_h <= 0:
        raise ValueError(f"cell must be positive, got {cell_w}x{cell_h}")

    best: tuple[int, int, float] | None = None
    best_key: tuple[float, int] | None = None
    for rows in range(1, n + 1):
        cols = -(-n // rows)                      # ceil
        if cols > n:
            continue
        scale = min(screen_w / (cols * cell_w), screen_h / (rows * cell_h))
        # Rank by uncapped scale, then prefer MORE columns on an exact tie.
        #
        # The scale term is what decides almost every case, and the claim that
        # the tie-break is what makes N=3 a 3x1 is WRONG. Measured on this
        # project's 1920x1080: N=3's two candidates are 3x1 at uncapped scale
        # 1.1928 and 2x2 at 1.0736, so the scale alone picks 3x1 and `cols` never
        # engages. (And N=3 is 2x2 on 800x600 and on 1280x1024, so "N=3 is a row
        # of three" is a property of this screen, not of the rule.)
        #
        # The tie-break is not decoration, though. It decides real cases: on
        # 1920x1080, N=9 ties EXACTLY at 0.7157058 between 5x2 and 3x3, and N=10
        # ties at the same value between 5x2 and 4x3. Without the second term
        # those two shapes are indistinguishable and a 3x3 wall is chosen for N=9.
        key = (scale, cols)
        if best_key is None or key > best_key:
            best_key = key
            best = (cols, rows, scale)
    assert best is not None                      # n >= 1 always yields rows=1
    return best


def tile_grid(n: int, screen: tuple[int, int, int, int] = (0, 0, 1920, 1080), *,
              aspect: float = NES_ASPECT,
              max_scale: float = 1.0,
              native_h: int = NATIVE_H) -> list[Cell]:
    """Place `n` cells on `screen`, centred, never larger than `max_scale`.

    `screen` is `(x, y, w, h)` in absolute coordinates so the caller can hand in
    the usable area rather than the whole output -- Plasma puts a panel across
    the top, and a wall tucked under it is not a wall.
    """
    if n < 0:
        raise ValueError(f"tile_grid needs n >= 0, got {n}")
    sx, sy, sw, sh = screen
    if n == 0:
        return []
    if sw < 1 or sh < 1:
        raise ValueError(f"screen must be positive, got {sw}x{sh}")

    base_h = float(native_h)
    base_w = base_h * aspect                      # NES aspect, not 586
    cols, rows, uncapped = grid_shape(n, sw, sh, base_w, base_h)
    scale = min(float(max_scale), uncapped)
    if scale <= 0:
        raise ValueError(f"max_scale must be positive, got {max_scale}")

    cell_w = int(round(base_w * scale))
    cell_h = int(round(base_h * scale))
    # Rounding is the one place the invariant can break: `round()` can add a
    # pixel, and `cols * cell_w` can then exceed `sw` by up to cols-1. Clamping
    # the cell, not the grid, keeps every cell identical in size, which matters
    # because a wall with one window a pixel narrower than its neighbour looks
    # like a mistake.
    cell_w = max(1, min(cell_w, sw // cols if cols <= sw else 1))
    cell_h = max(1, min(cell_h, sh // rows if rows <= sh else 1))

    block_w = cols * cell_w
    block_h = rows * cell_h
    x0 = sx + (sw - block_w) // 2
    y0 = sy + (sh - block_h) // 2

    cells: list[Cell] = []
    for i in range(n):
        r, c = divmod(i, cols)                   # row-major, left to right
        cells.append(Cell(slot=i, x=x0 + c * cell_w, y=y0 + r * cell_h,
                          w=cell_w, h=cell_h, scale=scale))
    assert_fits(cells, screen)
    return cells


def assert_fits(cells: list[Cell], screen: tuple[int, int, int, int]) -> None:
    """Raise unless every cell lies inside `screen`. Re-derived from the cells.

    Deliberately takes only the output and the screen: it does not trust any
    variable from the function that produced them. A check that reads the same
    locals as the code under test cannot catch that code mis-computing them.
    """
    sx, sy, sw, sh = screen
    for c in cells:
        if c.w <= 0 or c.h <= 0:
            raise AssertionError(f"cell {c.slot} has non-positive size {c.w}x{c.h}")
        if c.x < sx or c.y < sy:
            raise AssertionError(
                f"cell {c.slot} at ({c.x},{c.y}) starts outside the screen "
                f"({sx},{sy},{sw},{sh})")
        if c.x + c.w > sx + sw or c.y + c.h > sy + sh:
            raise AssertionError(
                f"cell {c.slot} at ({c.x},{c.y}) {c.w}x{c.h} overruns the screen "
                f"({sx},{sy},{sw},{sh})")


def overlap_count(cells: list[Cell]) -> int:
    """How many unordered pairs of cells intersect. Must be 0 for a grid."""
    bad = 0
    for i, a in enumerate(cells):
        for b in cells[i + 1:]:
            if (a.x < b.x + b.w and b.x < a.x + a.w
                    and a.y < b.y + b.h and b.y < a.y + a.h):
                bad += 1
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Place N emulator windows on a screen (see the module docstring)")
    ap.add_argument("n", type=int, help="how many windows")
    ap.add_argument("--screen", default="0,0,1920,1080",
                    help="x,y,w,h (default 0,0,1920,1080 = HDMI-A-2)")
    ap.add_argument("--aspect", type=float, default=NES_ASPECT)
    ap.add_argument("--max-scale", type=float, default=1.0,
                    help="1.0 = never enlarge past native (the default)")
    ap.add_argument("--native-h", type=int, default=NATIVE_H)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    try:
        screen = tuple(int(v) for v in a.screen.split(","))
    except ValueError:
        print(f"tile_geometry: --screen must be x,y,w,h, got {a.screen!r}",
              file=sys.stderr)
        return 2
    if len(screen) != 4:
        print(f"tile_geometry: --screen needs 4 numbers, got {a.screen!r}",
              file=sys.stderr)
        return 2
    try:
        cells = tile_grid(a.n, screen, aspect=a.aspect, max_scale=a.max_scale,
                          native_h=a.native_h)
    except ValueError as e:
        print(f"tile_geometry: {e}", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps({"n": a.n, "screen": list(screen),
                          "cells": [c.as_dict() for c in cells]}))
        return 0
    cols, rows, uncapped = grid_shape(a.n, screen[2], screen[3],
                                      a.native_h * a.aspect, float(a.native_h))
    print(f"tile_geometry: {a.n} window(s) on {screen[2]}x{screen[3]}"
          f" -> {cols}x{rows} grid, uncapped scale {uncapped:.3f},"
          f" using scale {cells[0].scale:.3f}" if cells else
          f"tile_geometry: {a.n} window(s) -> nothing to place")
    for c in cells:
        print(f"  {c.slot}: {c.w}x{c.h}+{c.x}+{c.y}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
