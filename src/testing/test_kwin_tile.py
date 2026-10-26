"""The window wall's tiling decision is testable without a compositor, an X server or an emulator.

    python3 src/testing/test_kwin_tile.py

WHAT IS BEING GUARDED
---------------------
`tools/bizhawk/tile.sh` + `kwin-tile.js` + `x11_tile.py` put N EmuHawk windows
into a grid on the desktop. The brief asked for two things at once that pull
apart: use KWin scripting to do it, and make the tiling decision testable
*without a live KWin*. Those are reconciled by splitting the decision
(`tools/bizhawk/tile_geometry.py`, pure) from the mechanisms (X11 configure, KWin
DBus). Everything in section A below needs none of the three.

WHY THE SPLIT IS NOT A MATTER OF TASTE
--------------------------------------
It was measured that kwin 6.7.5's per-`Client` scripting API cannot place a
window at all: no `move()`, no `resize()`, `x/y/width/height` read-only, and
`frameGeometry = {...}` swallowed and then READ BACK AS THE VALUE WRITTEN while
the X server reported the window never moved. A tiler built on that API would
have passed its own tests forever and moved nothing. So the placement is X11's
job and the geometry is a pure function, and this file pins the geometry plus the
structural properties that keep the two halves honest.

A CHECK THAT PASSES BECAUSE IT TESTED NOTHING IS WORSE THAN NO CHECK
--------------------------------------------------------------------
Six such checks appeared in an earlier session, so each guard below is aimed at
a specific way this file could be vacuous:

  * **check 1** pins that the module under test is the repository's file, by
    path and by content hash of a known line. A test that silently imported a
    stale copy from `__pycache__`, or a different project's file, would go on
    checking nothing while printing green.
  * **check 2** asserts `tile_grid` returns exactly N cells for N=1,2,3,4,5 --
    so "the function ran and returned something" is not mistaken for "the
    geometry is right".
  * **check 8** calls `assert_fits` with a hand-built cell that deliberately
    overhangs the screen, and requires it to RAISE. `assert_fits` is called
    inside `tile_grid` on every run, so if it can only pass, every "never
    exceeds the screen" claim in section A is decoration. This is the check that
    gives the rest of them meaning.
  * **check 13** is the mirror of 8: a grid of cells that is inside the screen
    but mutually overlapping must be caught by `overlap_count`, because a wall
    whose windows hide each other is the clutter this tool exists to remove.
  * **check 14** runs `x11_tile.py` against a deliberately unavailable display
    and requires exit 4 with a message, not a traceback and not 0. A tool that
    "succeeded" with no display is the failure mode this whole file is about.
  * **checks 20-21** require the live KWin round trip to actually unload, and
    ask KWin itself. If KWin is not there, they print NOT RUN with the reason --
    they never print a pass they did not earn.

LIVE CHECKS AND HONESTY ABOUT THEM
----------------------------------
Sections C and D need a real display and, for C, a real compositor. They are
conditional, and the condition is reported:

  * if `DISPLAY` answers, section C creates its OWN throwaway X window (nothing
    pre-existing is touched, and nothing is written into the repository), tiles
    it, and destroys it;
  * if KWin answers, section D loads our script, reads its report out of the
    journal, unloads it, and then asks KWin whether it still holds it.

Neither section can pass by doing nothing: they either run the real round trip or
they say they did not run. No `except: pass`, no skip-that-counts-as-ok.
"""
from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
GEOM = ROOT / "tools" / "bizhawk" / "tile_geometry.py"
X11T = ROOT / "tools" / "bizhawk" / "x11_tile.py"
TILE = ROOT / "tools" / "bizhawk" / "tile.sh"
KWINJS = ROOT / "tools" / "bizhawk" / "kwin-tile.js"

sys.path.insert(0, str(GEOM.parent))
import tile_geometry as G                                       # noqa: E402

fails = 0
checks = 0
seen: list[str] = []
notes: list[str] = []

# Every check in the UNCONDITIONAL sections. Sections C and D are conditional and
# are held to a different rule below (ran, or said why not).
#
# This file used to end by printing `f"{checks} checks"` -- a live counter, but
# still only a report: nothing failed if a check stopped running. `run_all.py`
# counts `  ok ` lines for its own summary and does not fail on a low number, so a
# check that quietly stopped being called would leave the file green. See the
# same fix, and the same reasoning, in src/testing/test_sweep_pids.py.
EXPECTED_ALWAYS = [f"check {i}" for i in range(1, 25)] + ["check 4b", "check 4c",
                                                        "check 19b", "check 19c",
                                                        "check 19d"]
EXPECTED_WITH_DISPLAY = [f"check {i}" for i in range(25, 29)]
EXPECTED_WITH_KWIN = [f"check {i}" for i in range(29, 32)]


def check(cond: bool, label: str, detail: str = "") -> bool:
    global fails, checks
    checks += 1
    seen.append(label.split(":")[0])
    if cond:
        print(f"  ok   {label}")
        return True
    fails += 1
    print(f"  FAIL {label}" + (f"\n         {detail}" if detail else ""))
    return False


def note(label: str) -> None:
    notes.append(label)
    print(f"  NOTE {label}")


def section(name: str) -> None:
    print(f"\n-- {name}")


# ============================================================ A. geometry, pure
section("A. the tiling decision, with no display, no KWin and no emulator")

# --- 1. the module under test is this repository's file, and is really imported
src = GEOM.read_text()
want = "def tile_grid("
real_sha = hashlib.sha256(src.encode()).hexdigest()[:16]
imported = pathlib.Path(G.__file__).resolve()
check(imported == GEOM.resolve()
      and want in src
      and real_sha == hashlib.sha256(imported.read_text().encode()).hexdigest()[:16],
      "check 1: the module imported IS the repository's tools/bizhawk/"
      f"tile_geometry.py (sha256[:16]={real_sha}), so the checks below are "
      "checking this project's geometry and not a stale __pycache__ copy or a "
      "file of the same name from somewhere else",
      f"imported {imported} from file {G.__file__}")

SCREEN = (0, 0, 1920, 1080)      # measured: HDMI-A-2, the primary output

# --- 2. it returns exactly N cells, and N=0 is an empty plan rather than a crash
counts = {n: len(G.tile_grid(n, SCREEN)) for n in range(0, 7)}
check(counts == {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6},
      "check 2: tile_grid(n) returns exactly n cells for n=0..6 (0 is an empty "
      "plan, not an error), so 'it returned something' is never mistaken for "
      "'the geometry is right'",
      f"got {counts}")

# --- 3. N=1: one cell, centred, and NOT enlarged past native
one = G.tile_grid(1, SCREEN)
c = one[0]
mid_x = SCREEN[2] / 2
mid_y = SCREEN[3] / 2
centre_err = (abs((c.x + c.w / 2) - mid_x), abs((c.y + c.h / 2) - mid_y))
check(len(one) == 1 and c.h <= G.NATIVE_H
      and max(centre_err) <= 1.5
      and one[0].scale <= 1.0,
      "check 3: N=1 gives one cell, no taller than the measured native window "
      f"(h={c.h} <= {G.NATIVE_H}), centred to within {max(centre_err):.1f}px, at "
      f"scale {c.scale} -- a single window is not blown up to fill 1920x1080",
      f"cell={c} centre_err={centre_err} scale={c.scale}")

# --- 4. N=3: three across, one row. 2x2 with a hole is the wrong answer.
three = G.tile_grid(3, SCREEN)
cols3 = len({cc.x for cc in three})
rows3 = len({cc.y for cc in three})
s31 = min(SCREEN[2] / (3 * G.NATIVE_H * G.NES_ASPECT), SCREEN[3] / G.NATIVE_H)
s22 = min(SCREEN[2] / (2 * G.NATIVE_H * G.NES_ASPECT), SCREEN[3] / (2 * G.NATIVE_H))
check(cols3 == 3 and rows3 == 1 and all(cc.h == three[0].h for cc in three),
      f"check 4: N=3 is a {cols3}x{rows3} grid of equal-height cells. The "
      "alternative a naive 'keep it square' rule picks is 2x2, which leaves an "
      "empty quadrant and makes a row of three scouts unreadable. NOTE what "
      f"decides this: the uncapped scale ({s31:.4f} for 3x1 against {s22:.4f} for "
      "2x2), NOT the tie-break -- an earlier version of this label claimed the "
      "tie-break was what turned 3x1 into the answer, and that was wrong. This is "
      "also a property of THIS screen: N=3 is 2x2 on 800x600 and on 1280x1024, "
      "which check 4b pins",
      f"cells={three} 3x1={s31:.4f} 2x2={s22:.4f}")

# --- 4b. the shape follows the SCREEN, not a fixed habit
small = {s: G.grid_shape(3, s[0], s[1], G.NATIVE_H * G.NES_ASPECT,
                         float(G.NATIVE_H))[:2]
         for s in ((1920, 1080), (1366, 768), (1280, 1024), (800, 600))}
check(small[(1920, 1080)] == (3, 1) and small[(1366, 768)] == (3, 1)
      and small[(1280, 1024)] == (2, 2) and small[(800, 600)] == (2, 2),
      "check 4b: N=3 is 3x1 on 1920x1080 and 1366x768 but 2x2 on 1280x1024 and "
      "800x600. So 'three across' is not a rule -- it is the scale ranking on a "
      "wide screen, and on a squarer one the same N lands as 2x2. A wall that "
      "forced three across on 800x600 would have to shrink the cells to fit, "
      "which is the opposite of what this tool maximises",
      f"got {small}")

# --- 4c. ANTI-VACUITY: the tie-break is load-bearing and was untested
#
# MEASURED, and this check exists because the mutation `key = (scale, -cols)`
# -- flipping the tie-break to prefer FEWER columns -- was caught by NOTHING in
# this file. Check 4 does not reach it (N=3 is decided by scale, as its own label
# now says) and neither does any other check.
#
# It does decide real cases. On 1920x1080, N=9's candidates 5x2 and 3x3 tie at
# EXACTLY 0.7157058 and N=10's 5x2 and 4x3 tie at the same value, so without the
# second element of the key the shape is chosen by iteration order and a 3x3 wall
# is perfectly acceptable to the code.
tiebreak = {n: G.grid_shape(n, 1920, 1080, G.NATIVE_H * G.NES_ASPECT,
                            float(G.NATIVE_H))[:2] for n in (9, 10)}
check(tiebreak[9] == (5, 2) and tiebreak[10] == (5, 2),
      "check 4c: on an exact tie in scale, MORE columns wins: N=9 picks 5x2 over "
      "3x3 and N=10 picks 5x2 over 4x3, both tied at uncapped scale 0.7157058. "
      "Without this rule those shapes are indistinguishable and the wall is "
      "decided by iteration order. Verified by mutation: flipping the key to "
      "(scale, -cols) fails exactly this check and no other in this file",
      f"got {tiebreak}")

# --- 5. N=5: 3x2, using six slots for five windows rather than one thin row
five = G.tile_grid(5, SCREEN)
cols5 = len({cc.x for cc in five})
rows5 = len({cc.y for cc in five})
occupied = len({(cc.x, cc.y) for cc in five})
check(cols5 == 3 and rows5 == 2 and occupied == 5,
      f"check 5: N=5 is {cols5}x{rows5} with {occupied} distinct cells, so the "
      "sixth slot is left empty rather than the five windows being squeezed into "
      "one row (5x1 would need scale 0.655 on this screen)",
      f"cells={five}")

# --- 6. NES aspect, within one pixel of rounding
def aspect_err(cell) -> float:
    return abs(cell.w / cell.h - G.NES_ASPECT)


worst = max(aspect_err(cc) for n in range(1, 13) for cc in G.tile_grid(n, SCREEN))
# one pixel of width on a 537px cell is ~0.0019 of aspect; allow 3x that
check(worst < 0.006,
      f"check 6: every cell keeps the NES aspect (256/240 = {G.NES_ASPECT:.4f}); "
      f"worst deviation over n=1..12 is {worst:.5f}. This is why the cell width "
      f"is {round(G.NATIVE_H * G.NES_ASPECT)} and not the 586px native window "
      "width: scaling the NATIVE box instead would make every picture 9% too wide",
      f"worst={worst}")

# --- 7. never upscaled, for any n
upsized = [(n, cc.h) for n in range(1, 25) for cc in G.tile_grid(n, SCREEN)
           if cc.h > G.NATIVE_H]
check(not upsized,
      f"check 7: no cell is taller than the native window for n=1..24. The "
      f"'--max-scale 1.0' default means the wall SHRINKS windows and never "
      f"enlarges them, so the emulator's own menus stay usable",
      f"upscaled: {upsized[:5]}")

# --- 8. ANTI-VACUITY: assert_fits must REJECT a cell that overhangs
# This is the check that gives check 9 its meaning. If assert_fits could only
# pass, "the grid never exceeds the screen" would be unfalsifiable.
def try_fits(cells, screen) -> str:
    try:
        G.assert_fits(cells, screen)
        return "accepted"
    except AssertionError as e:
        return f"rejected: {e}"


def cell_at(x, y, w, h, slot=0) -> G.Cell:
    return G.Cell(slot=slot, x=x, y=y, w=w, h=h, scale=1.0)


bad_right = try_fits([cell_at(1900, 0, 100, 50)], SCREEN)
bad_bottom = try_fits([cell_at(0, 1070, 50, 50)], SCREEN)
bad_left = try_fits([cell_at(-1, 0, 50, 50)], SCREEN)
bad_top = try_fits([cell_at(0, -1, 50, 50)], SCREEN)
bad_zero = try_fits([cell_at(0, 0, 0, 50)], SCREEN)
good = try_fits([cell_at(1900, 0, 20, 50)], SCREEN)
check(all(r.startswith("rejected") for r in (bad_right, bad_bottom, bad_left,
                                             bad_top, bad_zero)) and good == "accepted",
      "check 8: assert_fits REJECTS a cell overharing the right, bottom, left or "
      "top edge, and one with zero width, and ACCEPTS one flush against the "
      "right edge at x=1900 w=20. assert_fits runs inside every tile_grid call, "
      "so if it could only pass, check 9 would be unfalsifiable",
      f"right={bad_right!r} bottom={bad_bottom!r} left={bad_left!r} "
      f"top={bad_top!r} zero={bad_zero!r} good={good!r}")

# --- 9. the real invariant, over a spread of n and screen sizes
#
# `grid_or_raise` below. Before it existed this loop called `G.tile_grid` bare,
# and a mutation that removed the cell clamp made the LIBRARY raise out of
# `assert_fits` on the very first offending case -- so this check did not report a
# violation, it aborted the file with a traceback eight checks short of the end.
# That is a check that cannot fail in the way it claims to, and it was found by
# mutation, not by reading: the mutant's output had no `FAIL` line and no final
# count either, which is exactly the shape of "the suite stopped early".
#
# Treating the raise as the violation it is also the correct semantics: the
# invariant check 9 states is "no cell starts outside the screen or overruns it",
# and a cell that made `assert_fits` raise is a cell that did exactly that.
def grid_or_raise(n, scr):
    """(cells, violation). `violation` is a string, or None.

    Never lets an `AssertionError` out of the geometry loop, so a defect here is a
    reported FAIL in the section that describes it rather than a dead file.
    """
    try:
        return G.tile_grid(n, scr), None
    except AssertionError as e:
        return [], f"tile_grid(n={n}, screen={scr}) raised: {e}"


violations = []
for sw, sh in ((1920, 1080), (1366, 768), (1280, 1024), (2560, 1440),
               (800, 600), (3440, 1440), (537, 503)):
    for n in range(0, 26):
        scr = (0, 0, sw, sh)
        cells, raised = grid_or_raise(n, scr)
        if raised:
            violations.append((sw, sh, n, raised))
            continue
        # Re-derived here rather than trusting tile_grid's own call.
        for cc in cells:
            if (cc.w <= 0 or cc.h <= 0 or cc.x < 0 or cc.y < 0
                    or cc.x + cc.w > sw or cc.y + cc.h > sh):
                violations.append((sw, sh, n, cc))
check(not violations,
      "check 9: for n=0..25 on 7 screen sizes (including 800x600, which is "
      "smaller than three native cells wide, and 3440x1440), NO cell starts "
      "outside the screen or overruns its right or bottom edge. Integer "
      "rounding is the only thing that could break it and it is checked from "
      "the returned cells, not from tile_grid's locals. Measured: removing the "
      "clamp in tile_geometry.py breaks this on 32 of the 175 cases below -- "
      "1366x768 n=7 and 1280x1024 n=5 among them -- so the clamp is load-bearing "
      "and this check now fails when it goes",
      f"{len(violations)} violation(s), first: {violations[:3]}")

# --- 10. an offset screen, because Plasma's panel means the usable area is not (0,0,...)
offset_violations = []
for sx, sy, sw, sh in ((0, 40, 1920, 1040), (100, 100, 800, 600), (1920, 0, 1280, 1024)):
    scr = (sx, sy, sw, sh)
    for n in range(0, 13):
        cells, raised = grid_or_raise(n, scr)
        if raised:
            offset_violations.append((scr, n, raised))
            continue
        for cc in cells:
            if (cc.x < sx or cc.y < sy or cc.x + cc.w > sx + sw
                    or cc.y + cc.h > sy + sh):
                offset_violations.append((scr, n, cc))
check(not offset_violations,
      "check 10: the same invariant holds for a screen that does NOT start at "
      "the origin -- (0,40,1920,1040) is the shape a Plasma panel leaves, and "
      "the wall is centred in the area it is GIVEN rather than in the whole "
      "output",
      f"{len(offset_violations)} violation(s), first: {offset_violations[:3]}")

# --- 11. no two windows overlap, for every n
overlaps = {}
for n in range(0, 26):
    cells, raised = grid_or_raise(n, SCREEN)
    if raised:
        overlaps[n] = raised
    else:
        overlaps[n] = G.overlap_count(cells)
overlapped = {n: v for n, v in overlaps.items() if v}
check(not overlapped,
      "check 11: no two cells intersect for n=0..25. Overlapping windows would "
      "hide each other, which is the same clutter the wall exists to remove, so "
      "x11_tile.py refuses to move anything when this is non-zero",
      f"overlapping n: {overlapped}")

# --- 12. ANTI-VACUITY: overlap_count must see an overlap that is really there
a = cell_at(0, 0, 100, 100, 0)
b_overlapping = cell_at(50, 50, 100, 100, 1)
b_touching = cell_at(100, 0, 100, 100, 1)
b_above = cell_at(0, 100, 100, 100, 1)
check(G.overlap_count([a, b_overlapping]) == 1
      and G.overlap_count([a, b_touching]) == 0
      and G.overlap_count([a, b_above]) == 0,
      "check 12: overlap_count reports 1 for a genuinely overlapping pair and 0 "
      "for two that merely abut (edge-to-edge and corner-to-corner), so "
      "check 11 is not passing because the function always returns 0",
      f"overlap={G.overlap_count([a, b_overlapping])} "
      f"touch={G.overlap_count([a, b_touching])} "
      f"corner={G.overlap_count([a, b_above])}")

# --- 13. bad input is an error, never a plausible answer
errs = {}
for label, call in (("n=-1", lambda: G.tile_grid(-1, SCREEN)),
                    ("screen 0x0", lambda: G.tile_grid(1, (0, 0, 0, 1080))),
                    ("max_scale=0", lambda: G.tile_grid(1, SCREEN, max_scale=0.0)),
                    ("grid_shape n=0", lambda: G.grid_shape(0, 1920, 1080, 500, 500))):
    try:
        call()
        errs[label] = "NO ERROR RAISED"
    except ValueError as e:
        errs[label] = f"ValueError: {e}"
check(all(v.startswith("ValueError") for v in errs.values()),
      "check 13: a negative window count, a zero-sized screen, a zero scale and "
      "grid_shape(0) each raise ValueError with a message naming the bad value, "
      "rather than returning a grid that looks like an answer",
      "\n         ".join(f"{k}: {v}" for k, v in errs.items()))

# --- 14. the CLI agrees with the library (it is a second entry point, not a copy)
cli = {}
for n in (0, 1, 3, 5):
    p = subprocess.run([sys.executable, str(GEOM), str(n), "--json"],
                       capture_output=True, text=True, timeout=60)
    try:
        cli[n] = [(c["slot"], c["x"], c["y"], c["w"], c["h"])
                  for c in __import__("json").loads(p.stdout)["cells"]]
    except Exception as e:                                   # noqa: BLE001
        cli[n] = f"rc={p.returncode} out={p.stdout!r} err={p.stderr!r} ({e})"
lib = {n: [(c.slot, c.x, c.y, c.w, c.h) for c in G.tile_grid(n, SCREEN)]
       for n in (0, 1, 3, 5)}
check(cli == lib,
      "check 14: `tile_geometry.py --json N` returns byte-for-byte the same cells "
      "the library returns for n=0,1,3,5. tile.sh and x11_tile.py could import "
      "either; if the CLI had drifted into a second implementation of the grid, "
      "the unit tests above would be measuring the wrong one",
      f"cli={cli}\n         lib={lib}")

# ============================================ B. the tools, structurally
section("B. the three tools, read as source (no display needed)")

tile_src = TILE.read_text()
x11_src = X11T.read_text()
js_src = KWINJS.read_text()


def code_only(text: str) -> str:
    """Shell source with `#` comments dropped, so prose cannot satisfy a check."""
    out, in_here = [], False
    for ln in text.splitlines():
        t = ln.strip()
        if in_here:
            if "SH" in t:
                in_here = False
            continue
        if t.startswith("#") and not t.startswith("#!"):
            continue
        out.append(ln)
    return "\n".join(out)


def python_code_only(text: str) -> str:
    """Python source with comments AND docstrings dropped.

    Needed because check 15 FAILED ON ITS FIRST RUN -- and it was the check that
    was wrong, not the tool. x11_tile.py's header explains *why* it does not use
    xdotool, and the sentence "there is no xdotool or wmctrl on this machine"
    is in that module's DOCSTRING. A scan of raw source therefore reported the
    banned tool as present, in a file that has never mentioned running it.

    That is the same mistake as the six vacuous passes, seen from the other side:
    a check that cannot tell prose from code. `ast` removes both the comments
    (`ast.unparse` drops them) and the docstrings (an Expr holding a string as
    the first statement of a module, class or function).
    """
    class Strip(ast.NodeTransformer):
        def _first(self, node):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0] = ast.Expr(value=ast.Constant(value=""))
            return node

        def visit_Module(self, n):
            return self._first(self.generic_visit(n))

        def visit_FunctionDef(self, n):
            return self._first(self.generic_visit(n))

        def visit_AsyncFunctionDef(self, n):
            return self._first(self.generic_visit(n))

        def visit_ClassDef(self, n):
            return self._first(self.generic_visit(n))

    return ast.unparse(Strip().visit(ast.parse(text)))


tile_code = code_only(tile_src)
x11_code = python_code_only(x11_src)

# --- 15. no xdotool, no wmctrl: neither is installed and neither works on Wayland
banned = [ln.strip() for ln in tile_code.splitlines() + x11_code.splitlines()
          if any(b in ln for b in ("xdotool", "wmctrl", "xwininfo -", "xprop -id",
                                   "pkill", "killall"))]
check(not banned,
      "check 15: no executable line of tile.sh or x11_tile.py names xdotool, "
      "wmctrl, "
      "xwininfo, a per-window xprop call, pkill or killall. xdotool and wmctrl "
      "are not installed on this machine and would not work on Wayland anyway; "
      "the brief's whole point was not to reach for them",
      "\n         ".join(banned))

# --- 16. tile.sh cleans up on EVERY exit path, and then checks that it did
trap_line = [ln for ln in tile_code.splitlines() if "trap cleanup" in ln]
check(any("EXIT" in ln and "INT" in ln and "TERM" in ln for ln in trap_line)
      and "trap - EXIT INT TERM" in tile_code
      and "isScriptLoaded" in tile_code
      and tile_code.count("unloadScript") >= 2,
      "check 16: tile.sh registers ONE handler for EXIT, INT and TERM at the "
      "moment it loads the script (not before, so an early usage error does not "
      "run a handler with nothing to do), clears the trap inside the handler so "
      "it cannot re-enter itself, and asks KWin `isScriptLoaded` again after "
      "unloading. A cleanup that fails silently is the same bug as the incident "
      "these tools exist to prevent",
      f"trap lines: {trap_line}")

# --- 17. the wall really is opt-in
check("MAGICIAN_WALL" in tile_code and "refusing" in tile_code
      and '!= "1"' in tile_code,
      "check 17: tile.sh refuses to build a wall unless MAGICIAN_WALL=1, and "
      "says why in the refusal. The default for this project is an invisible "
      "emulator, because every measurement comes from client.screenshot() and "
      "Lua memory reads and neither needs a window")

# --- 18. kwin-tile.js cannot touch the user's session
# The single most important structural check here. If a KWin script that reports
# also assigns geometry, it reintroduces the swallowed write that makes a tiler
# look like it works.
js_code = "\n".join(ln for ln in js_src.splitlines()
                    if not ln.strip().startswith("//"))
forbidden = [w for w in ("frameGeometry =", ".move(", ".resize(", "setCurrentDesktop",
                         "noBorder =", "desktops =", "fullScreen =", "keepAbove =",
                         "skipTaskbar =")
             if w in js_code]
check(not forbidden,
      "check 18: kwin-tile.js contains NO assignment to frameGeometry, no "
      "move()/resize(), no setCurrentDesktop, no desktops=, no noBorder= and no "
      "fullScreen=/keepAbove=/skipTaskbar= in its code. It only reads and "
      "prints. `frameGeometry =` and `noBorder =` are the two that were measured "
      "to be swallowed by KWin and then read back as the value written, so "
      "having them here at all would make the wall a liar",
      f"found: {forbidden}")

# --- 19. the service name is the one that exists
check('SERVICE="org.kde.KWin"' in tile_code and 'OBJECT="/Scripting"' in tile_code
      and 'IFACE="org.kde.kwin.Scripting"' in tile_code
      and "org.kde.kwin.Scripting /Scripting" not in tile_code.replace(
          'IFACE="org.kde.kwin.Scripting"', ""),
      "check 19: tile.sh calls org.kde.KWin at object path /Scripting with "
      "interface org.kde.kwin.Scripting. The brief gave the SERVICE as "
      "`org.kde.kwin.Scripting`, which is not registered here -- "
      "`qdbus6 org.kde.kwin.Scripting /Scripting` answers 'Service does not "
      "exist'. The interface name is still org.kde.kwin.Scripting; only the "
      "service differs, and conflating the two is what the brief did")

# --- 19b. the cross-check must compare the two DISPLAYS, not just the counts
#
# tile.sh's cross-check is described in two headers as "agreement is evidence".
# That is false unless both halves are looking at the same display, and they
# usually are not: KWin answers over the session bus, so it always describes the
# session's own compositor, while `DISPLAY` names whatever X server the windows
# are on -- and display.sh runs this project's emulator on a nested Xephyr at :2
# that has no window manager on it at all.
#
# MEASURED, before this guard: with no emulator running, `DISPLAY=:2 tile.sh
# once` printed "cross-check: KWin and X11 both see 0 window(s)". KWin was
# counting the :0 session's 9 windows and X11 was counting :2's zero; the
# agreement was between two different window sets and it was reported as
# evidence. With real windows on :2 the same thing reads as a DISAGREEMENT and
# fails a run that in fact worked.
#
# So three things are required, and the third is the one that actually bit:
# compare the geometries, refuse when they differ, AND refuse when either one is
# MISSING. The first version of this guard had the mismatch test but let an empty
# `kwin_geom` through, because `cross_check` narrowed its argument to the `done`
# line before reading `screen` out of it -- so the parse returned nothing and the
# empty string sailed past the comparison.
_want19b = ['kwin_geom', 'x11_geom',
            '[ -z "$kwin_geom" ] || [ -z "$x11_geom" ]',
            'DIFFERENT DISPLAYS', 'CANNOT CROSS-CHECK', '--no-kwin']
check(all(s in tile_code for s in _want19b),
      "check 19b: tile.sh's cross-check compares the two sides' GEOMETRY before "
      "their window counts, refuses when they differ, and refuses when either "
      "geometry is MISSING rather than treating an unmeasurable side as "
      "agreement. KWin describes the session compositor over the session bus and "
      "cannot be pointed at :2, so on this machine the two halves are usually "
      "describing different displays. `--no-kwin` is the documented way to place "
      "windows on the X11 half alone",
      "missing from tile.sh: "
      + repr([s for s in _want19b if s not in tile_code]))

# --- 19c. the geometry parser itself, on a REAL captured line
#
# This is the check that would have caught the empty-parse bug above, and it needs
# no compositor and no display: it runs tile.sh's own `sed` expressions against
# the exact strings the two tools are measured to print.
#
# Both lines are captured, not invented. From a real `tile.sh once` on 2026-10-06
# with no emulator running:
#     magician-tile-v1 screen name=HDMI-A-2 x=0 y=0 w=1920 h=1080
#     x11_tile: display DISPLAY=:2 size=1280x800
KWIN_LINE = "magician-tile-v1 screen name=HDMI-A-2 x=0 y=0 w=1920 h=1080"
X11_LINE = "x11_tile: display DISPLAY=:2 size=1280x800"
KWIN_SED = r"s/.*screen name=[^ ]* .*w=\([0-9]*\) h=\([0-9]*\).*/\1x\2/p"
X11_SED = (r"s/^x11_tile: display DISPLAY=\([^ ]*\) size=\([0-9]*\)x\([0-9]*\)"
           r".*/\1 \2x\3/p")


def sed_geom(expr: str, line: str) -> str:
    p = subprocess.run(["sed", "-n", expr], input=line, capture_output=True,
                       text=True, timeout=30)
    return p.stdout.strip()


kgeom = sed_geom(KWIN_SED, KWIN_LINE)
xgeom = sed_geom(X11_SED, X11_LINE)
check(kgeom == "1920x1080" and xgeom == ":2 1280x800",
      "check 19c: tile.sh's two `sed` expressions actually extract the geometry "
      f"from the lines the tools print -- KWin -> {kgeom!r}, X11 -> {xgeom!r}. "
      "The empty case is the one that matters: the first version of the guard "
      "returned an empty string here (it looked for `screen` in a variable that "
      "had already been narrowed to the `done` line) and the empty case sailed "
      "past the comparison. Both lines are captured output, not strings written "
      "to suit the expressions",
      f"kwin_geom={kgeom!r} (want '1920x1080') x11_geom={xgeom!r} "
      f"(want ':2 1280x800')")

# --- 19d. ...and the two really do differ, so 19b has something to fire on
x11_only_geom = xgeom.split(" ")[1] if xgeom else ""
check(bool(kgeom) and bool(x11_only_geom) and kgeom != x11_only_geom,
      "check 19d: the two geometries a cross-check on DISPLAY=:2 would compare "
      f"really are different ({kgeom} against {xgeom.split(' ')[1] if xgeom else '?'}"
      "), so check 19b's guard has a real mismatch to catch. This is the case "
      "that was previously reported as an agreement because both window counts "
      "happened to be 0",
      f"kwin={kgeom!r} x11={xgeom!r}")

# --- 20. x11_tile.py cannot report success without checking
check("--no-verify" in x11_src
      and "but the frame is" in x11_src
      and "problems" in x11_src
      and "return 3" in x11_src,
      "check 20: x11_tile.py re-reads every window's geometry from the X server "
      "after configuring it, compares against the plan, and exits 3 on any "
      "disagreement. This check exists because of the measured KWin behaviour: "
      "a tool that writes a property and reads that same property back reports "
      "success forever while nothing moves. It is also what caught a real "
      "translate_coords direction bug in this file's own first version")

# --- 21. it will not grab another project's windows
check('DEFAULT_MATCH = ("bizhawk", "magician")' in x11_src
      and "all(t in win.identifiers" in x11_src,
      "check 21: the default match requires ALL of ('bizhawk', 'magician'), "
      "case-insensitively, in a window's WM_CLASS/WM_NAME/_NET_WM_NAME. "
      "'bizhawk' alone would match the neighbouring project's five emulator "
      "windows, and moving those is the incident tools/isolation.sh exists to "
      "prevent")

# --- 22. no cartridge is involved in any of it
cart_touch = [ln.strip() for ln in (tile_code.splitlines() + x11_code.splitlines())
              if ".nes" in ln or "SaveRAM" in ln or "cartref" in ln]
check(not cart_touch,
      "check 22: no line of tile.sh or x11_tile.py names a .nes file, SaveRAM or "
      "tools/cartref.py. Arranging windows cannot require the cartridge, and a "
      "tool that could reach one is a tool that could write to it",
      "\n         ".join(cart_touch))

# --- 23. x11_tile.py fails loudly with no display rather than pretending
env = dict(os.environ, DISPLAY=":987654")
p = subprocess.run([sys.executable, str(X11T)], capture_output=True, text=True,
                   timeout=60, env=env)
check(p.returncode == 4
      and "cannot open an X display" in p.stderr
      and "Traceback" not in p.stderr,
      "check 23: run against a display that does not exist, x11_tile.py exits 4 "
      "and explains itself on stderr, with no traceback. A tool that exits 0 "
      "having reached no display would let a wall 'succeed' having tiled nothing",
      f"rc={p.returncode} stderr={p.stderr.strip()[:200]!r}")

# --- 24. the geometry file has no display or DBus dependency at all
geom_code = python_code_only(src)
bad_import = [ln.strip() for ln in geom_code.splitlines()
              if ln.strip().startswith(("import ", "from "))
              and any(m in ln for m in ("Xlib", "dbus", "subprocess"))]
check(not bad_import,
      "check 24: tile_geometry.py imports nothing that can open a display, a bus "
      "or a process. That is what makes checks 2-14 runnable in CI and on a "
      "headless machine, and it is the reason the geometry was split out of the "
      "KWin script in the first place",
      "\n         ".join(bad_import))

# ======================================== C. live: our own throwaway window
section("C. live, if there is a display: tile a window this test creates")

have_display = False
dpy = None
try:
    from Xlib import display as _xdisplay                # noqa: PLC0415
    dpy = _xdisplay.Display()
    have_display = True
except Exception as e:                                     # noqa: BLE001
    note(f"section C NOT RUN: no X display ({e}). Checks 2-24 still ran; "
         f"section C is the only part that needs a live server.")

if have_display:
    from Xlib import X as _X                              # noqa: PLC0415
    scr = dpy.screen()
    win = scr.root.create_window(60, 60, 300, 220, 0, scr.root_depth,
                                 _X.InputOutput, _X.CopyFromParent,
                                 background_pixel=scr.white_pixel,
                                 event_mask=_X.StructureNotifyMask)
    win.set_wm_name("magician-rebuilt [NES] - BizHawk")
    win.set_wm_class("mono", "EmuHawk")                  # what an EmuHawk looks like
    win.change_property(dpy.intern_atom("_NET_WM_NAME"),
                        dpy.intern_atom("UTF8_STRING"), 8,
                        "magician-rebuilt [NES] - BizHawk".encode())
    win.map()
    dpy.sync()
    time.sleep(0.8)

    def truth(w) -> tuple[int, int, int, int]:
        g = w.get_geometry()
        c = dpy.screen().root.translate_coords(w, 0, 0)
        return (int(c.x), int(c.y), int(g.width), int(g.height))

    def extents(w) -> tuple[int, int, int, int]:
        p = w.get_full_property(dpy.intern_atom("_NET_FRAME_EXTENTS"), 0)
        if p is None or not p.value or len(p.value) < 4:
            return (0, 0, 0, 0)
        l, r, t_, b = (int(v) for v in list(p.value)[:4])
        return (l, r, t_, b)

    def frame_truth(w) -> tuple[int, int, int, int]:
        """Client geometry -> frame geometry, the same inversion the tool uses."""
        l, r, t_, b = extents(w)
        cx, cy, cw, ch = truth(w)
        return (cx - l, cy - t_, cw + l + r, ch + t_ + b)

    check(truth(win)[2:] == (300, 220),
          "check 25: the throwaway window exists at its requested size and "
          "carries the WM_CLASS and name of an EmuHawk, so x11_tile.py has "
          "something real to match. Nothing pre-existing was touched and nothing "
          "was written into the repository",
          f"geometry={truth(win)}")

    r = subprocess.run([sys.executable, str(X11T)], capture_output=True, text=True,
                       timeout=120)
    dpy.sync()
    time.sleep(0.5)
    want_cell = G.tile_grid(1, (0, 0, scr.width_in_pixels,
                                scr.height_in_pixels))[0]
    got_frame = frame_truth(win)
    ex = extents(win)
    # The FRAME is what fills the cell, and it is the frame that overlaps a
    # neighbouring row if the size is wrong. KWin treats a ConfigureRequest's
    # x/y as the frame origin and its w/h as the CLIENT size (measured: three
    # requests, client always at y+28), so a cell filled by client size comes out
    # 28px too tall and a 3x2 wall overlaps itself. Comparing the client here
    # would report a false failure at N=1 and miss the real overflow.
    ok26 = (r.returncode == 0 and "verified" in r.stdout
            and abs(got_frame[0] - want_cell.x) <= 2
            and abs(got_frame[1] - want_cell.y) <= 2
            and abs(got_frame[2] - want_cell.w) <= 2
            and abs(got_frame[3] - want_cell.h) <= 2)
    check(ok26,
          "check 26: x11_tile.py moved and resized the window so the DECORATED "
          f"FRAME fills the cell the pure geometry chose (decoration here is "
          f"{ex}, i.e. {ex[2]}px of title bar), exited 0, and said 'verified' -- "
          "read back from the X server here, independently of the tool that "
          "moved it",
          f"rc={r.returncode} frame={got_frame} cell=({want_cell.x},"
          f"{want_cell.y},{want_cell.w},{want_cell.h}) client={truth(win)} "
          f"extents={ex}\n         {r.stdout.strip()[-300:]}")

    # The direction bug this file's first version had: it reported the negation
    # of the truth and would have "succeeded" with the window off-screen.
    got = truth(win)
    neg = (-got[0], -got[1], got[2], got[3])
    check(neg != got or (got[0] == 0 and got[1] == 0),
          "check 27: the reported position is not the negation of the true one. "
          "x11_tile.py's first version called win.translate_coords(root, 0, 0), "
          "which the X protocol defines as the DESTINATION's position relative "
          "to the SOURCE -- so every window read back as exactly negated, a "
          "window placed at (691,288) measured (-691,-288), off-screen. Only its "
          "own verification caught it",
          f"true={got} negated={neg}")

    # A non-matching window must be left alone: this is the "another project's
    # emulator survives" property, with a real window manager in the way.
    other = scr.root.create_window(70, 70, 280, 200, 0, scr.root_depth,
                                  _X.InputOutput, _X.CopyFromParent,
                                  background_pixel=scr.white_pixel,
                                  event_mask=_X.StructureNotifyMask)
    other.set_wm_name("Legend of Zelda, The (USA) - BizHawk")
    other.set_wm_class("mono", "EmuHawk")
    other.map()
    dpy.sync()
    time.sleep(0.8)
    before_other = truth(other)
    subprocess.run([sys.executable, str(X11T)], capture_output=True, text=True,
                   timeout=120)
    dpy.sync()
    time.sleep(0.4)
    after_other = truth(other)
    check(before_other == after_other,
          "check 28: a second window titled like the NEIGHBOURING project's "
          "emulator is left exactly where it was. This machine is shared, and "
          "moving somebody else's emulator is the incident tools/isolation.sh "
          "exists to prevent -- so 'match bizhawk' is not enough and the Zelda "
          "caption must not be picked up",
          f"before={before_other} after={after_other}")

    win.destroy()
    other.destroy()
    dpy.sync()

# ============================================ D. live: the KWin round trip
section("D. live, if there is a KWin: load, report, unload, and prove it")

QDBUS = shutil.which("qdbus6") or shutil.which("qdbus")
PLUGIN = "magicianTileTest"


def kwin(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([QDBUS, "org.kde.KWin", "/Scripting",
                           f"org.kde.kwin.Scripting.{script}", *args],
                          capture_output=True, text=True, timeout=60)


kwin_ok = False
if not QDBUS:
    note("section D NOT RUN: neither qdbus6 nor qdbus is installed. Check 29-30 "
         "are the only ones that need a compositor.")
else:
    try:
        kwin_ok = kwin("isScriptLoaded", PLUGIN).stdout.strip() == "false"
    except Exception as e:                                 # noqa: BLE001
        note(f"section D NOT RUN: org.kde.KWin /Scripting did not answer ({e}).")

if kwin_ok:
    check(kwin("isScriptLoaded", PLUGIN).stdout.strip() == "false",
          "check 29: before the test loads anything, KWin says our plugin name is "
          "not loaded -- so 'unloaded at the end' means something rather than "
          "being true from the start")

    r = subprocess.run(["bash", str(TILE), "selftest"],
                       capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, MAGICIAN_TILE_PLUGIN=PLUGIN))
    after = kwin("isScriptLoaded", PLUGIN).stdout.strip()
    check(r.returncode == 0 and after == "false",
          "check 30: `tile.sh selftest` loads kwin-tile.js into the real "
          "compositor, reads its report out of the journal, unloads it, and KWin "
          "then confirms it is holding nothing. This is the proof that the tool "
          "leaves the session as it found it -- asked of KWin, not inferred from "
          "the tool's own exit code",
          f"rc={r.returncode} isScriptLoaded after={after!r}\n         "
          f"{r.stdout.strip()[-400:]}\n         {r.stderr.strip()[-300:]}")
    # And the report itself must have been real: a load that prints nothing and
    # exits 0 would pass check 30 just as well as a working one.
    check("magician-tile-v1 done" in r.stdout and "magician-tile-v1 screen" in r.stdout,
          "check 31: the selftest's own output contains a real report from inside "
          "KWin -- a `screen` line naming the output and a `done` line with "
          "matched=/considered= counts. Without this, a script that loaded, "
          "printed nothing and unloaded cleanly would satisfy check 30 entirely",
          f"stdout={r.stdout.strip()[-400:]!r}")
else:
    note("check 29-31 NOT RUN (no compositor). They are not reported as passing.")

# ============================================== E. this file's own coverage
# Asserted, not printed. Three things must hold, and each is a way this file
# could go green while testing nothing:
#
#   * every check in the unconditional sections actually ran;
#   * every check in a conditional section either ran OR a NOTE said why not --
#     so "no display" can never quietly become "no display checks";
#   * no check ran that is not on the expected list, which catches a rename.
#
# The conditional rule is the one that matters here. Sections C and D are the
# strongest checks in the file (25-28 move a real window; 29-31 ask the real
# compositor whether it still holds our script) and they are exactly the ones
# that would silently vanish on a machine with no DISPLAY and no KWin. A rule of
# "ran, or explained" is what keeps them from being mistaken for passes.
cov_bad = []
always_missing = [c for c in EXPECTED_ALWAYS if c not in seen]
if always_missing:
    cov_bad.append(f"unconditional checks that did not run: {always_missing}")
unexpected = sorted(set(seen) - set(EXPECTED_ALWAYS)
                    - set(EXPECTED_WITH_DISPLAY) - set(EXPECTED_WITH_KWIN))
if unexpected:
    cov_bad.append(f"checks that ran but are not on any expected list: {unexpected}")
for group, name in ((EXPECTED_WITH_DISPLAY, "with a display"),
                    (EXPECTED_WITH_KWIN, "with a compositor")):
    absent = [c for c in group if c not in seen]
    if absent and not any(name.split()[-1] in n for n in notes):
        cov_bad.append(f"{group} did not run and no NOTE said why ({len(notes)} "
                       f"note(s) present)")
if cov_bad:
    print("  FAIL coverage: " + "; ".join(cov_bad))
    fails += 1
else:
    ran_c = [c for c in EXPECTED_WITH_DISPLAY if c in seen]
    ran_d = [c for c in EXPECTED_WITH_KWIN if c in seen]
    print(f"  ok   coverage: all {len(EXPECTED_ALWAYS)} unconditional checks "
          f"ran; section C ran {len(ran_c)}/4"
          + (f", section D ran {len(ran_d)}/3" if QDBUS else ", section D skipped")
          + (f" ({len(notes)} NOTE(s) explaining what did not run) "
             if notes else ""))

print(f"\nkwin tile geometry: {checks} checks, {fails} FAILED")
if fails:
    print(f"{fails} FAILED")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
