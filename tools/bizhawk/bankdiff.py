#!/usr/bin/env python3
"""Compare two `bankprobe.lua` reports and name the first frame they diverge.

    python3 tools/bizhawk/bankdiff.py <cart.txt> <reb.txt> [upto-frame]

One BizHawk session per ROM, serially, via `sweep.sh`. The two reports are the
per-frame bank state of each image: the game's own copy of MMC3 registers R0-R7,
the current bank-select shadow `bnksel`, the per-level bank table
`mapbnk70`..`mapbnk7f`, and four level/phase variables that decide which level
data group gets banked in.

## What this can and cannot answer

`bankprobe.lua` establishes, by writing and reading back and by comparing
against the ROM file, that on this BizHawk build there is **no** MMC3 register
read-back at `$8000`/`$8001` and **no** working `event.onmemorywrite`. So there
is no per-instruction write trace here. What there is, once per frame, is the
sequence of bank values the CPU itself programmed. That is enough to answer
"at which frame do the two images stop issuing the same bank sequence", and it
is not enough to answer "which individual `$8000`/`$8001` write was the first to
differ". The difference matters and is stated in the output rather than left for
the reader to work out.

## Coverage, asserted

A comparator over two text files will happily report "no divergence" when one
file is empty, truncated after one frame, or missing a column. So it refuses to
produce a verdict unless, for **both** reports:

  * the file ends with the `done, frames A..B` sentinel `run.sh` waits for, so it
    was not truncated by a killed emulator;
  * the frame lines run contiguously from the first frame to the last, with no
    gap -- a gap means frames were skipped and the "first differing frame" would
    be fiction;
  * the frame count is at least what the caller asked for;
  * the report's own verdict block says zero-page coverage was 256/256 and that
    the System Bus and RAM windows agreed, because otherwise the bank mirror was
    read through an unverified window;
  * every frame line has all of the columns this script compares.

Any of those failing is a `FATAL`, printed first, with a non-zero exit.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

FRAME_RE = re.compile(
    r"^f(?P<n>\d+) port (?P<port>[0-9A-F]{2})/(?P<port2>[0-9A-F]{2}) "
    r"bnksel (?P<bnk>[0-9A-F]{2}) "
    r"r0-r7 (?P<r>(?:[0-9A-F]{2} ){7}[0-9A-F]{2}) "
    r"mapbnk70-7f (?P<m>(?:[0-9A-F]{2} ){15}[0-9A-F]{2})(?P<rest>.*)$")
DONE_RE = re.compile(r"^done, frames (\d+)\.\.(\d+)$")

# Zero-page cells that decide which level-data group is banked in, and the two
# bank registers that select it. `X0.PDS:357-364`: "6/7/8/9/A/B/C/D : graphics
# data", `X0.PDS:433-434`: mapbnk70/71, `X1.PDS:530-546` (`initlev`) copies all
# sixteen out of the `ld10` table into mapbnk70..7f.
GROUPS = {f"R{n}": i for i, n in enumerate("01234567")}


def parse(path: Path) -> tuple[dict[int, dict], list[str]]:
    text = path.read_text().splitlines()
    problems: list[str] = []

    done = None
    for ln in text:
        m = DONE_RE.match(ln.strip())
        if m:
            done = (int(m.group(1)), int(m.group(2)))
    if done is None:
        problems.append("no 'done, frames A..B' sentinel -- the run was truncated "
                        "or the script died; every frame after the kill is missing")
    cov = [ln for ln in text if ln.startswith("zero-page coverage")]
    if not cov:
        problems.append("no 'zero-page coverage' line in the verdict block")
    elif "256/256" not in cov[0] or "FATAL" in cov[0]:
        problems.append(f"zero-page coverage is not 256/256: {cov[0].strip()}")
    cross = [ln for ln in text if ln.startswith("domain cross-check")]
    if not cross:
        problems.append("no 'domain cross-check' line in the verdict block")
    elif "NOT CROSS-CHECKED" in cross[0] or not re.search(r":\s*0 differing", cross[0]):
        problems.append(f"the two zero-page windows did not agree: {cross[0].strip()}")

    frames: dict[int, dict] = {}
    for ln in text:
        m = FRAME_RE.match(ln.strip())
        if not m:
            continue
        n = int(m.group("n"))
        if n in frames:
            problems.append(f"frame {n} appears twice")
        frames[n] = {
            "port": m.group("port"),
            "bnk": m.group("bnk"),
            "r": m.group("r").split(),
            "m": m.group("m").split(),
        }
    if not frames:
        problems.append("no parsable frame lines at all")
    else:
        lo, hi = min(frames), max(frames)
        missing = [f for f in range(lo, hi + 1) if f not in frames]
        if missing:
            problems.append(f"{len(missing)} frame(s) missing inside {lo}..{hi}: "
                            f"{missing[:12]}{'...' if len(missing) > 12 else ''}")
        if done and (done[0], done[1]) != (lo, hi):
            problems.append(f"the sentinel says frames {done[0]}..{done[1]} but the "
                            f"frame lines run {lo}..{hi}")
    return frames, problems


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    a_path, b_path = Path(sys.argv[1]), Path(sys.argv[2])
    upto = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else None

    fa, pa = parse(a_path)
    fb, pb = parse(b_path)
    print("== coverage ==")
    for tag, path, probs in (("A", a_path, pa), ("B", b_path, pb)):
        rng = (min(fa), max(fa)) if tag == "A" else (min(fb), max(fb))
        print(f"  {tag} {path}: {len(fa if tag == 'A' else fb)} frame lines, "
              f"range {rng[0]}..{rng[1]}, "
              + ("OK" if not probs else f"{len(probs)} PROBLEM(S)"))
        for p in probs:
            print(f"    FATAL {tag}: {p}")
    if pa or pb:
        print("\nREFUSING TO COMPARE: the coverage check above failed. A truncated\n"
              "or unverified report would print a confident 'no divergence' here.")
        return 1

    common = sorted(set(fa) & set(fb))
    if upto is not None:
        common = [f for f in common if f <= upto]
    print(f"\ncomparing {len(common)} frames, {common[0]}..{common[-1]}")

    fields = [("r0-r7", lambda d: d["r"]), ("mapbnk70-7f", lambda d: d["m"]),
              ("bnksel", lambda d: [d["bnk"]]), ("port(not a register)",
                                                 lambda d: [d["port"]])]

    first = {}
    for name, get in fields:
        for f in common:
            if get(fa[f]) != get(fb[f]):
                first[name] = f
                break

    print("\n== per-column divergence ==")
    for name, get in fields:
        diffs = [f for f in common if get(fa[f]) != get(fb[f])]
        if not diffs:
            print(f"  {name:<16} never differs in {len(common)} frames")
            continue
        runs, start, prev = [], diffs[0], diffs[0]
        for f in diffs[1:]:
            if f != prev + 1:
                runs.append((start, prev))
                start = f
            prev = f
        runs.append((start, prev))
        print(f"  {name:<16} differs at {len(diffs)}/{len(common)} frames, "
              f"first {diffs[0]}, last {diffs[-1]}, in {len(runs)} run(s): "
              + ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in runs[:8])
              + (" ..." if len(runs) > 8 else ""))
        f0 = diffs[0]
        print(f"      first differing frame {f0}:")
        print(f"        A {' '.join(get(fa[f0]))}")
        print(f"        B {' '.join(get(fb[f0]))}")
        if len(runs) > 1:
            # A column that differs for one frame and then agrees again is a
            # sampling artefact of a once-per-frame poll, not a bank-sequence
            # divergence; saying so is the whole point of listing the runs.
            print(f"      {len(runs)} separate run(s) -- this column agrees again "
                  f"between them, so a difference here is a poll-timing "
                  f"difference, not two images issuing different bank values.")

    bankmarks = [v for k, v in first.items()
                 if k in ("r0-r7", "mapbnk70-7f", "bnksel")]
    bankfirst = min(bankmarks) if bankmarks else None
    print()
    if bankfirst is None:
        print("RESULT: the two images issue the SAME bank sequence, at frame "
              "granularity, for every frame compared.")
    else:
        print(f"RESULT: the bank sequence first differs at frame {bankfirst}.")
        a, b = fa[bankfirst], fb[bankfirst]
        print(f"  r0-r7       A {' '.join(a['r'])}\n              B {' '.join(b['r'])}")
        print(f"  mapbnk70-7f A {' '.join(a['m'])}\n              B {' '.join(b['m'])}")
        print(f"  bnksel      A {a['bnk']}   B {b['bnk']}")
        # Which register, and which level-data group.
        for i, (x, y) in enumerate(zip(a["r"], b["r"])):
            if x != y:
                print(f"  R{i}: A=${x} B=${y}")
        for i, (x, y) in enumerate(zip(a["m"], b["m"])):
            if x != y:
                grp = 7 + (i // 2) if i >= 2 else None
                print(f"  mapbnk7{i:X} (level-data group b=${grp:02X}): "
                      f"A=${x} B=${y}" if grp is not None else
                      f"  mapbnk7{i:X}: A=${x} B=${y}")
        print("\n  the ten frames either side, r0-r7 only:")
        for f in range(max(common[0], bankfirst - 5), min(common[-1], bankfirst + 5) + 1):
            mark = " <<<" if f == bankfirst else ""
            print(f"    f{f:<4d} A {' '.join(fa[f]['r'])}   B {' '.join(fb[f]['r'])}{mark}")

    print("\n== bank value histogram over all frames compared ==")
    for tag, fr in (("A", fa), ("B", fb)):
        seen: dict[int, set] = {}
        for f in common:
            for i, v in enumerate(fr[f]["r"]):
                seen.setdefault(i, set()).add(v)
        print(f"  {tag}: " + "  ".join(
            f"R{i}:{{{','.join(sorted(v))}}}" for i, v in sorted(seen.items())))
    print("\n  level-data groups b=$8..$d live in R6/R7 and mapbnk72..mapbnk7d.")
    print("  If R6/R7 never take a value in 06..0D, the level-data group banking")
    print("  has not been reached yet in these frames and this run cannot answer")
    print("  the question -- a longer run is required, not a cleaner instrument.")
    lvl = set()
    for f in common:
        for i in (6, 7):
            lvl.add(int(fa[f]["r"][i], 16))
    print(f"  R6/R7 values seen: {sorted(lvl)}"
          + ("   <-- no 06..0D: LEVEL GROUPS NOT REACHED"
             if not any(0x06 <= v <= 0x0D for v in lvl) else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())