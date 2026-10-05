#!/usr/bin/env python3
"""Turn a replay's frame series into an answer: did the injector work, and how
far did the cartridge stay in sync?

    tools/replay_check.py <run-dir> [<run-dir> ...] [--ref <run-dir>]

Why this is separate from replay.lua
------------------------------------
replay.lua runs inside BizHawk and can only see what it saw at the time. It cannot
compare two runs, and it cannot decide whether "the cartridge agreed with the movie
on 96% of frames" is good or bad. Both need measuring, and both are the difference
between "a number" and "a finding".

Question 1 -- did the input reach the cartridge?
-----------------------------------------------
Not "did `joypad.set` return", which it does whether or not anything is connected
to the core. The cartridge's own decoded joypad bytes are at $002E-$0035 (the map
is derived in replay.lua, and confirmed three independent ways) and replay.lua
records them per frame as the `game` column. Comparing those to what the movie
asked for is the only end-to-end proof available.

Exact agreement is not expected to be 100%: `jk0` returns as soon as it sees a
press, so the cartridge decodes the FIRST pressed button of a frame and stops. A
frame with Right+Down reads as one of them. That is cartridge behaviour, not an
injector fault, so "every button asked for is present" is reported next to
"exactly what was asked for".

Question 2 -- how many frames in sync?
--------------------------------------
Two runs, compared on the RAM hash triple. The first frame at which they differ is
the sync-loss frame. A count alone hides *where* it broke, and where is the
actionable part: a loss on a frame the movie presses something is an input
problem; a loss on a frame nothing happens on is a timing problem.

The honest baseline is the *other run*, not zero.

Question 3 -- did the movie's own annotations come true?
-------------------------------------------------------
The `.fm2` carries 108 subtitle anchors. This reports which ones the run reached
and, for each, the RAM delta against the previous anchor: which addresses changed
and by how much.

**What is deliberately not done here.** The subtitles name game concepts ("max
mana"), and the tempting move is to find the zero-page cell holding max mana and
assert it went up by 150 at frame 2832. A cell's *name* is not a witness -- and an
assertion built from a guess that then "passes" is worse than no assertion, because
in the output it is indistinguishable from a real one. So the delta is reported as
*evidence*, needing no prior belief, and the judgement is left to the reader. The
one hard check that IS made is structural: the anchor frame must be inside the run.

Self-coverage
-------------
Every column this file reads is counted and asserted present before any conclusion
is drawn. A series without the `game` column is refused outright rather than
falling back to the RAM hash, which is the one signal that cannot tell a working
injector from a dead memory domain.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "asm"))

BITS = {  # the cartridge's own pad order; derived, see tools/bizhawk/replay.lua
    "Right": 0x80, "Left": 0x40, "Down": 0x20, "Up": 0x10,
    "Start": 0x08, "Select": 0x04, "B": 0x02, "A": 0x01,
}
MOVIE_DEFAULT = pathlib.Path(
    "/tmp/opencode/tas/Magician (U)-FatRatKnight GoodEnd+subtitle.fm2")

NCOL = 11
COLS = ("frame", "want", "got", "joy", "jt8", "dlr6", "game", "deb",
        "ramh1", "ramh2", "ramnz")


class CheckError(Exception):
    pass


@dataclass
class Frame:
    n: int
    want: int
    got: int | None
    joy: int
    jt8: str
    dlr6: str
    game: int
    deb: int
    h1: int
    h2: int
    nz: int

    @property
    def state(self) -> tuple[int, int, int]:
        return (self.h1, self.h2, self.nz)


def load(run: pathlib.Path) -> list[Frame]:
    path = run / "series.tsv"
    if not path.is_file():
        raise CheckError(f"{path} does not exist -- that run produced no series")
    lines = [l for l in path.read_text().splitlines() if l and not l.startswith("#")]
    if not lines:
        raise CheckError(f"{path} has no data rows (only comments or nothing)")

    rows = [l.split("\t") for l in lines]
    ncol = len(rows[0])
    if ncol < NCOL:
        raise CheckError(
            f"{path} has {ncol} columns, not {NCOL}. Expected {' '.join(COLS)}. "
            f"A short row means the series came from a replay.lua that did not "
            f"record the cartridge's decoded pad, which is the whole point of this "
            f"file. Re-run the replay. It must NOT fall back to the RAM hash alone: "
            f"that is the one signal which cannot tell a working injector from a "
            f"dead memory domain.")
    ragged = [i for i, r in enumerate(rows) if len(r) != ncol]
    if ragged:
        raise CheckError(f"{path}: {len(ragged)} of {len(rows)} rows have {ncol} "
                         f"columns instead of a consistent count (first at row "
                         f"{ragged[0]}); the file is truncated or interleaved")

    out: list[Frame] = []
    for r in rows:
        try:
            out.append(Frame(int(r[0]), int(r[1], 16),
                             None if r[2] == "??" else int(r[2], 16),
                             int(r[3], 16), r[4], r[5], int(r[6], 16),
                             int(r[7], 16), int(r[8], 16), int(r[9], 16),
                             int(r[10])))
        except ValueError as exc:
            raise CheckError(f"{path}: row {r[:2]} does not parse: {exc}") from exc

    # The frame column must be 0..n-1. A gap means a lost frame and a repeat means
    # the writer restarted; either would make every "frame N" below point at the
    # wrong thing while every number here still looked reasonable.
    got = [f.n for f in out]
    if got != list(range(len(out))):
        i = next(i for i, (a, b) in enumerate(zip(got, range(len(out)))) if a != b)
        raise CheckError(f"{path}: the frame column is not 0..{len(out)-1}; row {i} "
                         f"says frame {got[i]}, expected {i}")
    return out


def decode(v: int) -> str:
    return "+".join(k for k, b in BITS.items() if v & b) or "-"


def load_movie(path: pathlib.Path) -> tuple[list[int], list[tuple[int, str]]]:
    import fm2
    m = fm2.parse(path)
    return list(m.frames), [(s.frame, s.text) for s in m.subtitles]


def pad_agreement(frames: list[Frame], movie: list[int]) -> int:
    print("\n== 1. did the input reach the cartridge? ==")
    asked = seen = exact = contains = 0
    for f in frames:
        if f.game:
            seen += 1
        if not f.want:
            continue
        asked += 1
        if f.game == f.want:
            exact += 1
        if f.game and (f.game & f.want) == f.want:
            contains += 1
    idle_both = sum(1 for f in frames if not f.want and not f.game)
    idle_mismatch = sum(1 for f in frames if not f.want and f.game)

    print(f"  frames in the run                      {len(frames)}")
    print(f"  frames the movie pressed something     {asked}")
    print(f"  frames the CARTRIDGE saw a press       {seen}")
    print(f"    (the cartridge's own bytes at $002E-$0035, not joypad.get)")
    print(f"  asked and every button was seen        {contains}"
          + (f"  ({100.0*contains/asked:.2f}%)" if asked else ""))
    print(f"  asked and exactly matched              {exact}"
          + (f"  ({100.0*exact/asked:.2f}%)" if asked else ""))
    print(f"  nothing asked, nothing seen            {idle_both}")
    print(f"  nothing asked, cartridge saw a press   {idle_mismatch}"
          + ("   <-- the game is holding a button" if idle_mismatch else ""))

    if asked and seen == 0:
        raise CheckError(
            "the movie pressed something on "
            f"{asked} frames and the cartridge's own joypad bytes were zero on all "
            f"of them. joypad.set was accepted by the emulator and the core advanced "
            f"every frame, and the game saw none of it: the input is not reaching the "
            f"cartridge. Nothing downstream of this run means anything.")
    if idle_mismatch:
        print("    NOTE: a button held by the game when the movie released it is the")
        print("          movie's business (a menu the TAS walks through), not a fault.")
    return exact


def offset_scan(frames: list[Frame], movie: list[int]) -> None:
    print("\n  injection convention, measured over a +/-1 frame window:")
    print("    (which movie frame best explains what the cartridge saw)")
    best, best_rate = None, -1.0
    for o in (-2, -1, 0, 1, 2):
        ok = tot = 0
        for f in frames:
            m = movie[f.n + o] if 0 <= f.n + o < len(movie) else None
            if m is None:
                continue
            tot += 1
            if f.game == m:
                ok += 1
        rate = 100.0 * ok / tot if tot else 0.0
        mark = ""
        if tot and rate > best_rate:
            best, best_rate, mark = o, rate, "   <-- best"
        print(f"    movie frame (run frame {o:+d})  {ok:6d}/{tot:<6d} {rate:6.2f}%{mark}")
    print(f"    convention: movie frame {best:+d} (if 0 wins, the default offset is"
          f" right and nothing needs shifting)")


def sync_loss(a: list[Frame], b: list[Frame], label_a: str, label_b: str) -> None:
    print(f"\n== 2. sync between {label_a} and {label_b} ==")
    n = min(len(a), len(b))
    print(f"  frames available: {label_a} {len(a)}, {label_b} {len(b)}, compared {n}")
    if len(a) != len(b):
        print("  NOTE: the runs are different lengths, so this is only the overlap")
    first = None
    for i in range(n):
        if a[i].state != b[i].state:
            first = i
            break
    if first is None:
        print(f"  IDENTICAL for all {n} compared frames -- no divergence to report")
        return
    print(f"  first divergence at frame {first} of {n} compared "
          f"({100.0*first/n:.2f}% in)")
    fa, fb = a[first], b[first]
    print(f"    at frame {first}: {label_a} ram {fa.h1:08X}/{fa.h2:08X}/nz{fa.nz}"
          f"   {label_b} ram {fb.h1:08X}/{fb.h2:08X}/nz{fb.nz}")
    print(f"    pad asked at frame {first}: {decode(fa.want)}"
          f"   cartridge saw {decode(fa.game)} / {decode(fb.game)}")
    # Where it went: 12 frames either side, so the shape of the break is visible.
    print("    frames around the break (a=state, b=state, same=?):")
    for i in range(max(0, first - 4), min(n, first + 9)):
        same = "same" if a[i].state == b[i].state else "DIFF"
        print(f"      f{i:<6d} a {a[i].h1:08X} b {b[i].h1:08X} {same}")
    # What was happening when it broke.
    if first > 0:
        prev_same = a[first - 1].state == b[first - 1].state
        print(f"    the frame before was {'identical' if prev_same else 'ALREADY different'}"
              f"  -> the break is at this frame, not a continuation")


def anchors(frames: list[Frame], movie_frames: list[int],
            subs: list[tuple[int, str]], run: pathlib.Path) -> int:
    print("\n== 3. the movie's own subtitle anchors ==")
    reached = missed = 0
    by_frame = {f.n: f for f in frames}
    prev_ram = None
    prev_sub = None
    for sf, text in subs:
        if sf >= len(frames):
            missed += 1
            continue
        reached += 1
        f = frames[sf]
        line = (f"  f{sf:<6d} pad {decode(f.want):<18s} cartridge {decode(f.game):<18s}"
                f" ram {f.h1:08X}  {text}")
        print(line)
        # The RAM delta against the previous anchor, from the full dumps.
        cur = run / "anchors" / f"f{sf:06d}.ram"
        prv = (run / "anchors" / f"f{prev_sub:06d}.ram") if prev_sub is not None else None
        if cur.is_file() and prv is not None and prv.is_file():
            a, b = prv.read_bytes(), cur.read_bytes()
            if len(a) == len(b):
                diffs = [(i, b[i] - a[i]) for i in range(len(a)) if a[i] != b[i]]
                print(f"          RAM changed at {len(diffs)} address(es) since f{prev_sub}: "
                      + ", ".join(f"${i:04X}{d:+d}" for i, d in diffs[:12])
                      + (" ..." if len(diffs) > 12 else ""))
                # A witness worth looking for: a 16-bit cell that rose by 150.
                hits = [(i, d) for i, d in diffs if d == 150]
                if hits:
                    print(f"          ** a cell changed by exactly +150: "
                          + ", ".join(f"${i:04X}" for i, _ in hits) + " **")
        prev_sub = sf
    print(f"\n  {reached} of {len(subs)} anchors are inside a {len(frames)}-frame run;"
          f" {missed} were not reached")
    if not reached:
        raise CheckError("no subtitle anchor falls inside this run, so the run "
                         "cannot be checked against the movie's own commentary at all")
    return reached


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=pathlib.Path)
    ap.add_argument("--ref", type=pathlib.Path, default=None,
                    help="compare the first run against this one for the sync curve")
    ap.add_argument("--movie", type=pathlib.Path, default=MOVIE_DEFAULT)
    ap.add_argument("--no-movie", action="store_true",
                    help="skip the anchor section (the movie is not on this machine)")
    args = ap.parse_args()

    movie_frames: list[int] = []
    subs: list[tuple[int, str]] = []
    if not args.no_movie:
        if not args.movie.is_file():
            print(f"replay_check: no movie at {args.movie}; --no-movie to skip",
                  file=sys.stderr)
            return 2
        movie_frames, subs = load_movie(args.movie)
        print(f"movie: {args.movie.name}, {len(movie_frames)} frames, "
              f"{len(subs)} subtitle anchors (checksum NOT re-checked here -- "
              f"tools/fm2.py did that when it built the table this run used)")

    loaded: list[tuple[str, pathlib.Path, list[Frame]]] = []
    for run in args.runs:
        frames = load(run)
        loaded.append((run.name, run, frames))
        print(f"\n########## {run} ##########")
        print(f"  summary: {(run / 'summary.txt').is_file() and 'present' or 'MISSING'}")
        if (run / "summary.txt").is_file():
            for line in (run / "summary.txt").read_text().splitlines():
                if line.startswith(("verdict", "FAIL", "FATAL")):
                    print(f"    {line}")
        pad_agreement(frames, movie_frames)
        if movie_frames:
            offset_scan(frames, movie_frames)

    if args.ref is not None:
        reff = load(args.ref)
        for name, _, frames in loaded:
            sync_loss(frames, reff, name, args.ref.name)

    for name, run, frames in loaded:
        if subs:
            anchors(frames, movie_frames, subs, run)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CheckError as exc:
        print(f"replay_check: {exc}", file=sys.stderr)
        raise SystemExit(2)
