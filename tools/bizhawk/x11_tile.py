#!/usr/bin/env python3
"""Put this project's EmuHawk windows into a grid, over X11.

    python3 tools/bizhawk/x11_tile.py                 # find, place, verify
    python3 tools/bizhawk/x11_tile.py --dry-run       # print the plan only
    python3 tools/bizhawk/x11_tile.py --desktop 4     # also move to virtual desktop 4

WHY X11 AND NOT KWIN, WHICH THE BRIEF ASKED FOR
------------------------------------------------
The brief said to use `org.kde.kwin.Scripting` and that per-`Client` it has
`.move()`, `.resize()`, `.desktops` and `.noBorder`. On this machine, kwin 6.7.5
under Plasma 6 on Wayland, that is not the API. Measured, not inferred, by
loading scripts through `qdbus6 org.kde.KWin /Scripting` and reading the journal:

  * `Client.move` and `Client.resize` are **boolean properties, both `false`**,
    not methods. There is no `doMove`, `doResize`, `setGeometry`,
    `interactiveMove` or `setQuickTileMode` either.
  * `Client.x`, `.y`, `.width`, `.height` are readable numbers and
    **read-only**: assigning throws `Cannot assign to read-only property "x"`.
  * `Client.frameGeometry = {x:40,y:40,width:640,height:480}` **does not throw
    and does not move anything.** KWin then reports
    `frameGeometry = KWin::RectF(40, 40, 640, 480)` while the X server still
    reports `x=840 y=425 w=320 h=240`. That is a perfect lie: the write is
    swallowed and the read-back returns the value that was written.
  * `Client.noBorder = true` is the same story -- accepted, reported back as
    `true`, and `_NET_FRAME_EXTENTS` stays `0, 0, 28, 0`.

`tools/bizhawk/kwin-tile.js` is still the KWin half of this feature, because
KWin *can* enumerate and report, and because the independent second opinion is
what catches a placement that did not happen. But placement is done here, over
X11, because that is the only mechanism measured to move the window. The
neighbouring project already relies on the same thing: `tools/side_by_side.py`
moves BizHawk windows with `win.configure(...)` and says why -- "there is no
xdotool or wmctrl on this machine".

X11 is not a degraded path under Wayland either: BizHawk is a WinForms app under
mono, so it is an X11 client on XWayland, and XWayland's windows are normal KWin
clients that accept `ConfigureRequest`.

VERIFYING THE MOVE ACTUALLY HAPPENED
------------------------------------
`--verify` (on by default) re-reads every window's real geometry from the X
server after configuring it and compares against the plan, and exits 3 on any
mismatch. That check exists because of the KWin result above: a tool that moves
windows by assigning properties and then reads those same properties back will
report success forever while the windows never move. Here the read comes from a
different subsystem than the write, which is the only reason it means anything.

WHOSE WINDOWS
-------------
This project shares the machine with at least one other BizHawk user, so the
default match is NOT "any BizHawk window". It requires **all** of `--match`
(defaults `bizhawk` AND `magician`, case-insensitively) to appear in the
window's WM_CLASS, WM_NAME or `_NET_WM_NAME`. A Zelda window is not matched,
and `--match` is there so a caller can widen or narrow it deliberately instead
of inheriting a guess.

EXIT CODES
----------
  0  done -- or there was nothing to tile, which is the normal case here
  2  bad arguments (an unparseable `--screen`, n<0, a zero-sized screen)
  3  the windows did not end up where the plan said, read back from the X server
  4  no X display could be opened
  5  refusing to move anything: the computed grid overlaps itself, or the
     geometry handed back a grid that does not fit the screen

`0` for "nothing to tile" is deliberate and load-bearing: the default for this
project is an INVISIBLE emulator, so most runs genuinely have no window, and
that is not a failure. It is not also a failure to *not* have found one -- the
matching is printed with the count before it is used.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from tile_geometry import Cell, assert_fits, overlap_count, tile_grid  # noqa: E402

DEFAULT_MATCH = ("bizhawk", "magician")


class Window:
    def __init__(self, d, xid: int):
        self.d = d
        self.xid = xid
        self.w = d.create_resource_object("window", xid)
        self.identifiers = self._read_identifiers()

    def _prop(self, atom: str):
        try:
            p = self.w.get_full_property(self.d.intern_atom(atom), 0)
        except Exception:                                  # noqa: BLE001
            return None
        return p

    def _read_identifiers(self) -> str:
        """Everything a window says it is, lowercased, for matching."""
        bits = []
        try:
            n = self.w.get_wm_name()
            if n:
                bits.append(n)
        except Exception:                                  # noqa: BLE001
            pass
        p = self._prop("_NET_WM_NAME")
        if p is not None and p.value:
            v = p.value
            bits.append(v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v))
        try:
            cls = self.w.get_wm_class()
            if cls:
                bits.extend(x for x in cls if x)
        except Exception:                                  # noqa: BLE001
            pass
        try:
            r = self.w.get_full_property(self.d.intern_atom("WM_CLASS"), 0)
            if r is not None and r.value:
                bits.append(r.value.decode("latin-1", "replace")
                            if isinstance(r.value, bytes) else str(r.value))
        except Exception:                                  # noqa: BLE001
            pass
        return " ".join(bits).lower()

    def label(self) -> str:
        try:
            n = self.w.get_wm_name()
        except Exception:                                  # noqa: BLE001
            n = None
        p = self._prop("_NET_WM_NAME")
        if p is not None and p.value:
            v = p.value
            n = v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
        return n or f"0x{self.xid:x}"

    def geometry(self) -> tuple[int, int, int, int] | None:
        """Absolute `(x, y, w, h)`, or None if the window has gone away.

        `get_geometry` reports x/y relative to the PARENT, and a reparenting
        window manager makes that parent a decoration frame rather than the
        root, so the raw value is off by the frame offset. An absolute position
        needs `translate_coords` -- **and the direction matters.**

        The X protocol's TranslateCoordinates answers with the position of the
        DESTINATION window relative to the SOURCE window. So to ask "where is
        this window on the root", the SOURCE is the root and the DESTINATION is
        the window:

            root.translate_coords(win, 0, 0)     <- correct, absolute
            win.translate_coords(root, 0, 0)     <- backwards, and NEGATED

        The second spelling is the natural-looking one and it was the first
        version of this function. It returns `root - win`, so every window was
        reported at exactly the negation of where it actually was: a window
        placed at (691, 288) measured (-691, -288), i.e. off the top-left of the
        screen. `--verify` is what caught it -- it compares this read-back
        against the plan and refuses to call the run a success. Without it this
        would have "worked" and left every window off-screen, which is a worse
        version of the clutter this tool exists to remove.
        """
        try:
            g = self.w.get_geometry()
            root = self.d.screen().root
            c = root.translate_coords(self.w, 0, 0)
            return (int(c.x), int(c.y), int(g.width), int(g.height))
        except Exception:                                  # noqa: BLE001
            return None

    def place(self, x: int, y: int, w: int, h: int, desktop: int | None) -> None:
        self.w.configure(x=x, y=y, width=w, height=h)
        if desktop is not None:
            self.w.change_property(self.d.intern_atom("_NET_WM_DESKTOP"),
                                   self.d.intern_atom("CARDINAL"), 32,
                                   [desktop])

    def stack_above(self) -> None:
        try:
            from Xlib import X
            self.w.configure(stack_mode=X.Above)
        except Exception:                                  # noqa: BLE001
            pass

    def frame_extents(self) -> tuple[int, int, int, int]:
        """`(left, right, top, bottom)` decoration thickness, from EWMH.

        Needed because KWin's interpretation of a `ConfigureRequest` is neither
        of the two obvious ones. MEASURED on this machine with three successive
        requests on a window KWin was decorating 28px deep at the top:

            requested (100,100,400,300)  ->  client (100,128,400,300)
            requested (200,600,500,200)  ->  client (200,628,500,200)
            requested (0,0,200,100)      ->  client (0,28,200,100)

        So `x, y` name the position of the DECORATED FRAME -- the client lands at
        `(x, y + top)` -- while `width, height` name the size of the CLIENT. A
        window asked for 503px tall therefore came out with a 531px frame.

        That matters for a wall and only shows up once there are two rows. A
        3x2 grid on this screen has rows 503px apart, so a frame that is 28px
        taller than its cell overlaps the row below it by 28px -- the windows
        hiding each other, which is the clutter this tool exists to remove. It
        is invisible at N=1, which is why the first version shipped with it.

        A missing `_NET_FRAME_EXTENTS` means "no decoration", so zeros are the
        right answer, and `has_extents` lets the caller say so out loud rather
        than guessing silently.
        """
        p = self._prop("_NET_FRAME_EXTENTS")
        if p is None or not p.value or len(p.value) < 4:
            return (0, 0, 0, 0)
        try:
            l, r, t, b = (int(v) for v in list(p.value)[:4])
        except (TypeError, ValueError):
            return (0, 0, 0, 0)
        return (l, r, t, b)

    def has_extents(self) -> bool:
        p = self._prop("_NET_FRAME_EXTENTS")
        return p is not None and bool(p.value) and len(list(p.value)) >= 4

    def request_for(self, cell: Cell) -> tuple[int, int, int, int]:
        """The `configure()` arguments that make the FRAME fill `cell`.

        `x, y` pass through (KWin wants the frame origin) and the size has the
        decoration subtracted (KWin wants the client size). With no decoration
        this is the identity, so an undecorated window is placed exactly.
        """
        l, r, t, b = self.frame_extents()
        w = cell.w - l - r
        h = cell.h - t - b
        if w < 1 or h < 1:
            raise ValueError(
                f"cell {cell.w}x{cell.h} is smaller than this window's "
                f"decoration ({l}+{r} across, {t}+{b} down); the frame cannot "
                f"fit, and shrinking the client to 0px would hide the emulator")
        return (cell.x, cell.y, w, h)

    def frame_of(self, geo: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """Turn an observed CLIENT geometry into the FRAME geometry.

        The inverse of `request_for`, and what `--verify` compares against the
        plan. Comparing the client geometry to the cell instead -- which is what
        this tool's first version did -- reports a 28px "failure" for a window
        that is in fact perfectly placed, and would equally miss a real 28px
        overflow.
        """
        l, r, t, b = self.frame_extents()
        return (geo[0] - l, geo[1] - t, geo[2] + l + r, geo[3] + t + b)


def client_windows(d) -> list[int]:
    """Managed client window ids.

    `_NET_CLIENT_LIST` is the EWMH list of windows a window manager is actually
    managing, which is exactly the set we should touch. Falling back to a
    recursive tree walk would also return override-redirect windows, menus and
    tooltips -- things no WM manages and that must not be moved.
    """
    root = d.screen().root
    p = root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), 0)
    if p is not None and p.value:
        return [int(v) for v in (p.value if isinstance(p.value, (bytes, bytearray))
                                 else p.value)]
    return [int(c) for c in root.query_tree().children]


def find(d, tokens: list[str]) -> tuple[list[Window], list[str]]:
    hits, skipped = [], []
    for xid in client_windows(d):
        try:
            win = Window(d, xid)
        except Exception as e:                             # noqa: BLE001
            skipped.append(f"0x{xid:x}: {type(e).__name__}")
            continue
        if all(t in win.identifiers for t in tokens):
            hits.append(win)
    return hits, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tile this project's EmuHawk windows")
    ap.add_argument("--match", action="append", default=None,
                    help="token that must appear in the window's identity; "
                         "repeatable, ALL must match (default: bizhawk, magician)")
    ap.add_argument("--screen", default=None,
                    help="x,y,w,h usable area (default: the X screen's full size)")
    ap.add_argument("--desktop", type=int, default=None,
                    help="also send each window to this virtual desktop (0-based)")
    ap.add_argument("--max-scale", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true", help="print the plan, move nothing")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the post-move read-back (not recommended)")
    ap.add_argument("--settle", type=float, default=1.5,
                    help="seconds to wait before reading geometry back")
    a = ap.parse_args(argv)

    tokens = [t.lower() for t in (a.match if a.match else DEFAULT_MATCH)]

    try:
        from Xlib import display                            # noqa: PLC0415
        d = display.Display(os.environ.get("DISPLAY") or None)
    except Exception as e:                                 # noqa: BLE001
        print(f"x11_tile: cannot open an X display "
              f"(DISPLAY={os.environ.get('DISPLAY')!r}): {e}", file=sys.stderr)
        print("x11_tile: this tool moves X11 windows. Under Wayland that still"
              " covers EmuHawk, which is a mono/WinForms X11 client on XWayland.",
              file=sys.stderr)
        return 4

    wins, skipped = find(d, tokens)
    for s in skipped:
        print(f"x11_tile: note -- could not read {s}", file=sys.stderr)

    # WHICH DISPLAY THIS IS, printed before anything is placed.
    #
    # Not decoration. `tools/bizhawk/tile.sh` cross-checks this tool's window
    # count against `kwin-tile.js`'s, and presents the agreement as evidence. That
    # is only evidence if both halves are looking at the SAME display -- and KWin
    # always answers about the session's own compositor, which is not necessarily
    # the one `DISPLAY` names. `display.sh` puts this project's emulator on a
    # nested Xephyr at :2 with no window manager on it; KWin, reached over the
    # session bus, is the :0 compositor.
    #
    # MEASURED with no EmuHawk running: `DISPLAY=:2 tile.sh once` reported
    # "cross-check: KWin and X11 both see 0 window(s)" -- KWin counting the :0
    # session's 9 windows, X11 counting :2's 0. The agreement was between two
    # different window sets and it proved nothing. With real windows on :2 it
    # would have read as a DISAGREEMENT and failed a run that worked.
    # This line is what makes that distinguishable instead of silent.
    print(f"x11_tile: display DISPLAY={os.environ.get('DISPLAY', '')} "
          f"size={d.screen().width_in_pixels}x{d.screen().height_in_pixels}")

    print(f"x11_tile: matching {' AND '.join(repr(t) for t in tokens)}"
          f" -> {len(wins)} window(s)")
    for w in wins:
        print(f"  0x{w.xid:x} {w.geometry()} {w.label()!r}")

    if not wins:
        print("x11_tile: nothing to tile. That is not an error -- the default is"
              " an INVISIBLE wall, so most runs have no windows at all. See the"
              " header of tools/bizhawk/kwin-tile.js.", file=sys.stderr)
        return 0

    if a.screen:
        try:
            screen = tuple(int(v) for v in a.screen.split(","))
        except ValueError:
            print(f"x11_tile: --screen must be x,y,w,h, got {a.screen!r}",
                  file=sys.stderr)
            return 2
        if len(screen) != 4:
            print(f"x11_tile: --screen needs 4 numbers, got {a.screen!r}",
                  file=sys.stderr)
            return 2
    else:
        sw = d.screen().width_in_pixels
        sh = d.screen().height_in_pixels
        screen = (0, 0, sw, sh)
    print(f"x11_tile: usable area {screen}")

    try:
        cells: list[Cell] = tile_grid(len(wins), screen, max_scale=a.max_scale)
    except ValueError as e:
        print(f"x11_tile: {e}", file=sys.stderr)
        return 2
    except AssertionError as e:
        # `tile_grid` ends by calling `assert_fits` on its own output, so this is
        # the geometry refusing to hand back a grid that leaves the screen. It is
        # a bug in the geometry, not bad input, and it must not reach the operator
        # as a traceback: `return 2` is the wrong answer (the arguments were
        # fine) and `return 0` is a lie (nothing was tiled).
        print(f"x11_tile: REFUSING -- the geometry produced a grid that does not "
              f"fit the screen, and said so: {e}", file=sys.stderr)
        print("x11_tile: nothing was moved. This is a defect in "
              "tile_geometry.py; src/testing/test_kwin_tile.py check 9 should "
              "catch it.", file=sys.stderr)
        return 5
    try:
        assert_fits(cells, screen)
    except AssertionError as e:                              # belt, and braces
        print(f"x11_tile: REFUSING -- {e}", file=sys.stderr)
        return 5
    bad = overlap_count(cells)
    if bad:
        print(f"x11_tile: REFUSING -- the computed grid has {bad} overlapping"
              f" cell pair(s). Moving windows to those positions would hide"
              f" windows behind each other, which is the clutter this avoids.",
              file=sys.stderr)
        return 5
    print(f"x11_tile: {cells[0].w}x{cells[0].h} cells at scale {cells[0].scale:.3f}")
    for c in cells:
        print(f"  plan {c.slot}: {c.w}x{c.h}+{c.x}+{c.y}")

    if a.dry_run:
        print("x11_tile: --dry-run, nothing moved")
        return 0

    decorated = 0
    for w, c in zip(wins, cells):
        if w.has_extents() and any(w.frame_extents()):
            decorated += 1
        rx, ry, rw, rh = w.request_for(c)
        w.place(rx, ry, rw, rh, a.desktop)
        w.stack_above()
    d.sync()
    if decorated:
        print(f"x11_tile: {decorated} of {len(wins)} window(s) are decorated; the "
              f"decoration is subtracted from the cell so the FRAME fills it and "
              f"rows cannot overlap. See Window.request_for.")
    if a.desktop is not None:
        print(f"x11_tile: asked the window manager for virtual desktop {a.desktop}"
              f" (0-based) for {len(wins)} window(s). The WM owns desktops; this"
              f" is a request, so it is verified below like everything else.")

    if a.no_verify:
        print("x11_tile: --no-verify given; NOT confirming the windows moved.")
        return 0

    time.sleep(a.settle)
    d.sync()
    problems = []
    for w, c in zip(wins, cells):
        g = w.geometry()
        if g is None:
            problems.append(f"0x{w.xid:x} {w.label()!r}: window vanished")
            continue
        # Compare the FRAME against the cell, not the client: KWin places a
        # ConfigureRequest's x/y at the frame origin, so a decorated window that
        # is exactly on its cell still reads 28px lower in client coordinates.
        # Verifying the client would report a false failure here and, worse,
        # would not notice a genuine 28px frame overflow.
        fr = w.frame_of(g)
        if abs(fr[0] - c.x) > 2 or abs(fr[1] - c.y) > 2 \
                or abs(fr[2] - c.w) > 2 or abs(fr[3] - c.h) > 2:
            problems.append(f"0x{w.xid:x} {w.label()!r}: cell "
                            f"({c.x},{c.y},{c.w},{c.h}) but the frame is {fr} "
                            f"(client {g}, decoration {w.frame_extents()})")
    if problems:
        print(f"x11_tile: FAIL -- {len(problems)} window(s) did not end up where"
              f" the plan said:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("x11_tile: read back from the X server, not from the property that"
              " was written, so this is a real disagreement.", file=sys.stderr)
        return 3
    print(f"x11_tile: verified -- all {len(wins)} window(s) are where the plan"
          f" said, read back from the X server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
