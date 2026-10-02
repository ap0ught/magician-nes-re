#!/usr/bin/env python3
"""Move two BizHawk windows apart and screenshot them side by side.

    python3 tools/side_by_side.py out.png <title-substring> [<title-substring> ...]

python-xlib, not xdotool: there is no xdotool or wmctrl on this machine, and
BizHawk rewrites its own window position from the last session, so configuring it
is unreliable -- the windows are moved directly over X11 instead.

ImageMagick's `import` is not used: version 7 here rejects its own filename
argument. The grab is done with XGetImage through python-xlib and written with
PIL.
"""
import sys
import time

from PIL import Image
from Xlib import display, X

# Left-hand slot for each of these, in order.
LEFT_X = 20
RIGHT_X = 760
TOP_Y = 60
WIDTH, HEIGHT = 740, 680


def find(d, needle):
    hits = []
    for w in d.screen().root.query_tree().children:
        try:
            name = w.get_wm_name() or ""
        except Exception:
            continue
        if needle.lower() in name.lower():
            hits.append((w, name, w.get_geometry()))
    return hits


def grab(win, geo):
    raw = win.get_image(0, 0, geo.width, geo.height, X.ZPixmap, 0xFFFFFFFF)
    return Image.frombytes("RGB", (geo.width, geo.height), raw.data,
                           "raw", "BGRX")


def main():
    out = sys.argv[1]
    needles = sys.argv[2:]
    d = display.Display()
    shots = []
    for n, needle in enumerate(needles):
        hits = find(d, needle)
        if not hits:
            print(f"no window matching {needle!r}", file=sys.stderr)
            continue
        win, name, geo = hits[0]
        x = LEFT_X if n == 0 else RIGHT_X
        win.configure(x=x, y=TOP_Y, width=WIDTH, height=HEIGHT)
        try:
            win.configure(stack_mode=X.Above)
        except Exception:
            pass
        d.sync()
        time.sleep(1)
        win, name, geo = find(d, needle)[0]
        img = grab(win, geo)
        shots.append((name, img))
        print(f"{name!r} -> ({x},{TOP_Y}) {img.width}x{img.height}")
    if not shots:
        return 1
    if len(shots) == 1:
        shots[0][1].save(out)
        print(f"wrote {out}")
        return 0
    gap = 20
    w = sum(i.width for _, i in shots) + gap * (len(shots) - 1)
    h = max(i.height for _, i in shots)
    canvas = Image.new("RGB", (w, h), (24, 24, 28))
    x = 0
    for _, img in shots:
        canvas.paste(img, (x, 0))
        x += img.width + gap
    canvas.save(out)
    print(f"wrote {out} {canvas.width}x{canvas.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())