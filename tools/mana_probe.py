#!/usr/bin/env python3
"""Read the game's own mana and phase cells out of a replay's anchor RAM dumps.

    python3 tools/mana_probe.py <replay-dir> [--zp <symbols-file>]

WHY THIS IS A TOOL AND NOT A LINE OF THE REPORT
------------------------------------------------
`tools/replay_check.py` reports "a cell changed by exactly +150" when the movie's
subtitle says `+150 max mana`. That is the wrong kind of evidence and it is worth
being precise about why.

The tool scans *every* RAM address for a delta of exactly +150 between two
consecutive anchors. In the 5000-frame release replay there were 1732 changing
addresses in that window, so a delta of exactly +150 lands on several of them by
coincidence -- and on this run it landed on **`$004D`, which is `mapind`, the
current map index 1..A**. A cell's *name* is not a witness: a scan that finds the
number the annotation was looking for has found a coincidence with a high
probability and no information about mana.

So the address has to come from the source's own `zp` block and the value has to
be read from the address. That is all this file does, and the addresses are read
out of `asm/out/mag.sym` when it is present rather than written down here -- a
literal in this file is a literal that goes stale when the zp block changes.

WHAT IT PRINTS
--------------
For each subtitle anchor the run reached:

    frame    the movie's anchor frame
    manacur  current mana          ($0047-$0048, little-endian)
    manatop  max mana             ($0049-$004A)
    mclock   mana regain timer    ($0057)
    wealth   gold                 ($0045-$0046)
    food     food level           ($005A)
    water    water level          ($005B)
    phase    main game phase      ($005F)
    deltas   the change since the previous anchor, for the mana cells only

and then the reading, which is a question and not a verdict:

  * `manacur` never leaving 0 across every anchor means no spell was cast.
  * `phase` constant across every anchor means the game is in one game phase for
    the whole run. `phase` indexes the `gvl`/`gvh` dispatch table at
    `x5.pds:227-231`, so it is the main loop's state word: it not moving is the
    game not advancing, whatever the screen is doing.
  * `manatop` is in the game's own units. The panel's `+150` is not `+150` here
    unless the units happen to agree, so the comparison has to be made with the
    units established, not assumed.

WHAT IT DOES NOT CLAIM
----------------------
It does not know what the game *should* be showing, and it does not compare
against anything. An `.fm2` carries input, not reference RAM, so "did the run
reproduce the movie" cannot be answered by comparing to the movie. What this
file answers is narrower and checkable: which cells moved, by how much, and
whether the main loop advanced.

WHAT IT NEEDS: Python, and a replay directory with `anchors/f*.ram` -- the
dumps `tools/bizhawk/replay.lua` writes at every subtitle anchor it reaches. No
emulator, no cartridge. `MAGICIAN_ANCHOR_RE` is where those live if the run wrote
them elsewhere.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# name -> (low byte, high byte or None). Derived from the `zp` block in x0.pds by
# asm/pds6502.py and written out by the build to asm/out/mag.sym; `resolve()`
# below reads them from there. The comments restate what the source says at each
# declaration, which is where the meaning comes from.
WANTED = {
    "manacur": "current mana          (zp manacur,2 ; current mana)",
    "manatop": "max mana              (zp manatop,2 ; max. mana)",
    "mclock":  "mana regain timer     (zp mclock,1  ; mana regain timer)",
    "wealth":  "gold                  (zp wealth,2  ; current wealth)",
    "food":    "food level            (zp food,1)",
    "water":   "water level           (zp water,1)",
    "phase":   "main game phase       (zp phase,1  ; main game phase)",
    "mapind":  "current map index     (zp mapind,1 ; current map index 1..A)",
    "curlev":  "current level         (zp curlev,1 ; current level 1..A)",
}
DEFAULT_SYM = ROOT / "asm" / "out" / "mag.sym"


class ProbeError(Exception):
    pass


def resolve(sym_path: pathlib.Path) -> dict[str, int]:
    """`asm/out/mag.sym`'s `name = $ADDR` lines, for the names this file wants.

    The build has not been run when this file is used on a fresh clone, so the
    addresses are derived from the `zp` block itself in that case. That fallback
    is not a guess: `zp name,N` expands to `name equ z / z = z+N` (`x0.pds`), and
    `z` starts at 0, so walking the block in order gives every zero-page address.
    It is also why the text is decoded by splitting on every whitespace
    character: the decoded source has several `zp` declarations on one physical
    line, and a line-oriented parse silently misses the rest and shifts every
    address after it. That happened while writing this file and produced
    `manacur = $47` from one parse and a wrong answer from another.
    """
    wanted = {n.lower() for n in WANTED}
    if sym_path.is_file():
        out: dict[str, int] = {}
        for line in sym_path.read_text(encoding="utf-8",
                                       errors="replace").splitlines():
            name, sep, val = line.partition("=")
            if not sep:
                continue
            name = name.strip().lower()
            val = val.strip()
            if name in wanted and val.startswith("$"):
                try:
                    out[name] = int(val[1:], 16)
                except ValueError:
                    pass
        missing = wanted - set(out)
        if not missing:
            return out
    return from_zp_block(ROOT / "pds-text" / "x0.pds", wanted)


def from_zp_block(pds: pathlib.Path, wanted: set[str]) -> dict[str, int]:
    """Every zero-page address, from the source's own `zp` block."""
    if not pds.is_file():
        raise ProbeError(
            f"neither {DEFAULT_SYM} (run `make assemble`) nor {pds} (run "
            f"`make extract`) is present, so the zero-page addresses cannot be "
            f"derived. They are not written down here on purpose: a literal in "
            f"this file is a literal that goes stale.")
    text = pds.read_text(encoding="utf-8", errors="replace")
    z = 0
    out: dict[str, int] = {}
    for m in re.finditer(r"\bzp\s+([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(\d+)\b", text):
        name, n = m.group(1).lower(), int(m.group(2))
        if name in wanted:
            out[name] = z
        z += n
    for m in re.finditer(r"\bz\s*=\s*z\s*\+\s*(\d+)\b", text):
        z += int(m.group(1))
    missing = wanted - set(out)
    if missing:
        raise ProbeError(
            f"the zp block in {pds} does not define {sorted(missing)}. Either the "
            f"block moved or this file is reading it wrongly, and in both cases "
            f"the addresses it would print are not trustworthy.")
    return out


def w16(b: bytes, a: int) -> int:
    return b[a] | (b[a + 1] << 8)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("replay_dir", type=pathlib.Path,
                    help="a tools/bizhawk/out/replay-* directory")
    ap.add_argument("--anchors", type=pathlib.Path, default=None,
                    help="the anchors/ directory, if it is not <replay_dir>/anchors")
    ap.add_argument("--sym", type=pathlib.Path, default=DEFAULT_SYM)
    a = ap.parse_args()

    anchors = a.anchors or (a.replay_dir / "anchors")
    if not anchors.is_dir():
        print(f"mana_probe: no {anchors}", file=sys.stderr)
        print("             A replay writes one f*.ram per subtitle anchor it "
              "reaches;\n             run tools/bizhawk/replay.sh first.", file=sys.stderr)
        return 2

    addr = resolve(a.sym)
    source = "asm/out/mag.sym" if a.sym.is_file() else "the zp block in pds-text/x0.pds"
    print(f"addresses from {source}")
    for n in WANTED:
        print(f"  ${addr[n.lower()]:02X}  {WANTED[n]}")

    dumps = sorted(anchors.glob("f*.ram"),
                   key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    if not dumps:
        print(f"\nmana_probe: {anchors} holds no f*.ram", file=sys.stderr)
        return 2

    hdr = (f"{'frame':>7} {'manacur':>9} {'manatop':>9} {'mclock':>8} "
           f"{'wealth':>8} {'food':>6} {'water':>7} {'phase':>7} {'mapind':>8}")
    print(f"\n{hdr}")
    print("-" * len(hdr))
    prev = None
    series = []
    for p in dumps:
        b = p.read_bytes()
        if len(b) < 0x60:
            continue
        row = {
            "frame": int(re.sub(r"\D", "", p.stem) or 0),
            "manacur": w16(b, addr["manacur"]),
            "manatop": w16(b, addr["manatop"]),
            "mclock": b[addr["mclock"]],
            "wealth": w16(b, addr["wealth"]),
            "food": b[addr["food"]],
            "water": b[addr["water"]],
            "phase": b[addr["phase"]],
            "mapind": b[addr["mapind"]],
        }
        series.append(row)
        delta = ""
        if prev is not None:
            delta = (f"   dmanacur {row['manacur'] - prev['manacur']:+5d}"
                     f"   dmanatop {row['manatop'] - prev['manatop']:+5d}"
                     f"   dphase {row['phase'] - prev['phase']:+4d}")
        print(f"{row['frame']:7d} {row['manacur']:9d} {row['manatop']:9d} "
              f"{row['mclock']:8d} {row['wealth']:8d} {row['food']:6d} "
              f"{row['water']:7d} {row['phase']:7d} {row['mapind']:8d}{delta}")
        prev = row

    # ------------------------------------------------------------------ reading
    print(f"\n{len(series)} anchors, frames {series[0]['frame']}"
          f"-{series[-1]['frame']}")
    manacur = sorted({r["manacur"] for r in series})
    phases = sorted({r["phase"] for r in series})
    tops = sorted({r["manatop"] for r in series})
    print(f"  manacur took the values {manacur}")
    print(f"  manatop took the values {tops}")
    print(f"  phase  took the values {[f'${v:02X}' for v in phases]}")
    if manacur == [0]:
        print("    -> current mana never left 0: no spell was cast in this window.")
    if len(phases) == 1:
        print(f"    -> the main game phase never changed. `phase` indexes the "
              f"gvl/gvh dispatch\n       table at x5.pds:227-231, so this is the "
              f"main loop's state word and it\n       is not moving: the game is "
              f"not advancing through its phases.")
    changes = [(a["frame"], b["frame"], b["manatop"] - a["manatop"])
               for a, b in zip(series, series[1:]) if b["manatop"] != a["manatop"]]
    print(f"  manatop changed {len(changes)} time(s): "
          + ("; ".join(f"{f0}->{f1} {d:+d}" for f0, f1, d in changes) or "never"))
    print("\nNOTE the units. `manatop` is the game's own counter; the panel's "
          "`+150` is\n      only the same number if the units agree, and nothing "
          "here establishes that.")
    print("NOTE what a `changed by exactly +150` line from tools/replay_check.py "
          "is NOT.\n      That scan covers every RAM address, and on this run it "
          "landed on\n      $%02X = mapind, the current map index." % addr["mapind"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeError as e:
        print(f"mana_probe: {e}", file=sys.stderr)
        raise SystemExit(2)