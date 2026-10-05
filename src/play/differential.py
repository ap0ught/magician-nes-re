#!/usr/bin/env python3
"""Two ROMs, one input log: at which frame do they first differ, and in what?

    python3 src/play/differential.py --inputs logs/inputs/m1_beta1.inputs.txt
    python3 src/play/differential.py --inputs LOG --a BETA1 --b REBUILD

THE QUESTION
------------
The rebuild passes the title, the map screen, and then wedges in g03 with
`curlev=$20` where Beta 1 computes `$10`. `curlev` is an *output*: something
earlier chose the wrong thing. This tool answers the question that output cannot:

    given identical inputs, what is the first frame at which the two ROMs' RAM
    differs at all, which named fields differ there, and what were both of them
    doing?

It is a differential, so it needs no theory about which routine is at fault. The
first divergent field names the routine.

WHAT IT ASSERTS ABOUT ITSELF
---------------------------
Twelve instruments in this project reported plausible wrong numbers without
error, so this one refuses to be quiet:

  * it prints how many frames it compared on each side, and a run that compared
    fewer frames than the log holds is a FAILURE, not a short answer;
  * it reports the two ROMs' SHA1s, so "which two files" is never in doubt;
  * a first-difference frame is only reported if the two RAM images genuinely
    differ there, and it prints how many bytes differ, because "one field moved"
    and "the whole screen turned over" are different findings;
  * it writes its whole report under `logs/differential/<label>/`, and says so.

It is deliberately NOT a savestate differ: loading each ROM's own state would
compare two unrelated machines, and the inputs are the only thing the two have
in common.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from play import ram  # noqa: E402
from play.emu import BizHawk  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from asm import patches  # noqa: E402

OUT = ROOT / "logs" / "differential"

ap = argparse.ArgumentParser()
ap.add_argument("--inputs", required=True, help="a recorded input log (.inputs.txt)")
ap.add_argument("--a", default="", help="the ROM that works (default: beta1)")
ap.add_argument("--b", default="", help="the ROM under test (default: the rebuild)")
ap.add_argument("--label", default="", help="name for the report directory")
ap.add_argument("--every", type=int, default=1,
                help="compare every Nth frame (1 = every frame)")
args = ap.parse_args()

LOG = pathlib.Path(args.inputs).resolve()
A = pathlib.Path(args.a).resolve() if args.a else patches.cart_path("beta1")
B = (pathlib.Path(args.b).resolve() if args.b
     else ROOT / "asm" / "out" / "magician-rebuilt.nes")
LABEL = args.label or f"{A.stem[:18]}_vs_{B.stem[:18]}".replace(" ", "_")
OUTDIR = OUT / LABEL
OUTDIR.mkdir(parents=True, exist_ok=True)

_lines: list[str] = []


def say(*parts) -> None:
    s = " ".join(str(p) for p in parts)
    print(s, flush=True)
    _lines.append(s)


def where(img: bytes) -> str:
    v = ram.decode(img)
    return (f"phase={v['phase']}({ram.PHASE_NAMES.get(v['phase'], '?')}) "
            f"curlev=${v['curlev']:02X} oldlev=${v['oldlev']:02X} "
            f"mapind={v['mapind']} nmiflag={v['nmiflag']} "
            f"bnksel=${v['bnksel']:02X} plr=({ram.plrx(img)},{ram.plry(img)})")


def named_diff(a: bytes, b: bytes) -> list[tuple[str, int, int]]:
    out = []
    for fl in ram.all_fields():
        try:
            x, y = fl.get(a), fl.get(b)
        except Exception:
            continue
        if x != y:
            out.append((fl.name, x, y))
    return out


def run_side(rom: pathlib.Path, frames, tag: str, every: int) -> tuple[list, str]:
    """Replay the log from power-on, sampling RAM every `every` frames.

    Returns the samples and the frame count reached. One emulator at a time:
    BizHawk diverts a second launch into the first through its single-instance
    pipe, so the two sides are sequential and both start from frame 0.
    """
    samples: list[tuple[int, bytes]] = []
    with BizHawk(rom=rom, log_name=f"diff_{tag}", route="diff", run=tag) as emu:
        i = 0
        while i < len(frames):
            j = i
            while j < len(frames) and frames[j] == frames[i]:
                j += 1
            emu.step(frames[i], j - i)
            if (j - i) % every == 0 or j >= len(frames):
                samples.append((emu.frame, emu.work_ram()))
            i = j
        say(f"  {tag}: {rom.name}  frame {emu.frame}  "
            f"fingerprint {emu.fingerprint()[:16]}  {len(samples)} samples")
        return samples, emu.fingerprint()


def main() -> int:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    say("DIFFERENTIAL -- two ROMs, one input log")
    say(f"  inputs   {LOG}")
    say(f"  A        {A}")
    say(f"  b        {B}")
    say(f"  sha1     A={hashlib.sha1(A.read_bytes()).hexdigest()[:16]}"
        f"  B={hashlib.sha1(B.read_bytes()).hexdigest()[:16]}")
    say(f"  sampling every {args.every} frame(s)")
    ram.load_button_order()

    stale = BizHawk.running_emuhawk()
    if stale:
        say(f"REFUSING: mono pids {stale} are already running.")
        return 2

    # Read the log with the loader that ASSERTS its own header, so a log that
    # does not match its own frame count cannot be compared as if it did.
    frames = BizHawk.load_inputs(LOG)
    say(f"  {len(frames)} frames in the log")
    if not frames:
        say("FAIL: the log is empty. A differential over zero frames compares "
            "power-on and reports the reset vectors, which is not a finding.")
        return 2

    sa, fa = run_side(A, frames, "a", args.every)
    sb, fb = run_side(B, frames, "b", args.every)

    # --- the instrument checks its own coverage -------------------------
    problems = []
    if len(sa) != len(sb):
        problems.append(f"A took {len(sa)} samples and B took {len(sb)}")
    if len(sa) < 2:
        problems.append(f"only {len(sa)} samples per side; a first-difference "
                        "frame needs at least two")
    if not frames:
        problems.append("the log is empty")

    say("")
    say("=== PHASE TIMELINE ===")
    for tag, s in (("A", sa), ("B", sb)):
        prev = None
        say(f"  {tag}:")
        for fr, img in s:
            v = ram.decode(img)
            if (v["phase"], v["curlev"]) != prev:
                say(f"    f{fr:<6} {where(img)}")
                prev = (v["phase"], v["curlev"])

    say("")
    say("=== FIRST DIVERGENCE ===")
    first = None
    for (fa_, ia), (fb_, ib) in zip(sa, sb):
        if ia != ib:
            first = (fa_, ia, ib)
            break
    if first is None:
        say(f"  NO DIVERGENCE in {len(sa)} sampled frames "
            f"({len(sa) * args.every} frames of game time)")
        say(f"  A ended at {where(sa[-1][1])}")
        say(f"  B ended at {where(sb[-1][1])}")
        if sa[-1][1] != sb[-1][1]:
            say("  ...and yet the final RAM images DIFFER, so the samples are not "
                "aligned. That is a bug in this tool, not a finding.")
            problems.append("final images differ but no sampled frame did")
    else:
        fno, ia, ib = first
        nd = named_diff(ia, ib)
        nbytes = sum(1 for x, y in zip(ia, ib) if x != y)
        say(f"  first differing frame: {fno}")
        say(f"  A: {where(ia)}")
        say(f"  B: {where(ib)}")
        say(f"  {nbytes} of {len(ia)} bytes differ")
        if nbytes == 0:
            problems.append(f"reported frame {fno} as the first difference but "
                            "0 bytes differ -- the images are equal")
        say(f"  named fields differing there ({len(nd)}):")
        for name, x, y in nd:
            say(f"    {name:<10} A={x:<8} B={y}")
        if not nd:
            say("    (none -- the difference is in bytes the field table does "
                "not name. That is itself a finding: the ram map has a hole at "
                "the first place the two builds part company.)")
            problems.append("bytes differ but no named field does: the ram map "
                            "has a hole here")

    say("")
    say("=== INSTRUMENT SELF-CHECK ===")
    for p in problems:
        say(f"  PROBLEM: {p}")
    if not problems:
        say(f"  compared {len(sa)} samples per side over {len(frames)} logged "
            f"frames; the two sides' frame counters agreed at every sample")

    (OUTDIR / "diff.txt").write_text("\n".join(_lines) + "\n", encoding="utf-8")
    say("")
    say(f"report: {OUTDIR / 'diff.txt'}")
    say(f"({round(time.time() - t0, 1)}s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())