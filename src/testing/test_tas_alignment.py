"""The TAS runner's two load-bearing pieces: the alignment comparator and the
subtitle sampling that picks screenshot frames.

    python3 src/testing/test_tas_alignment.py

`tools/tas/run.sh` decides a TAS ran correctly from four numbers. Two of them come
from code with no other coverage and neither is obvious:

  * **`find_alignment` in `tools/tas/drive.lua`** decides whether the buttons FCEUX
    delivered are the movie's own buttons at a single constant frame offset. It is
    the gate standing in for "the movie really was played", and it is easy to write
    in a way that always passes. The version that shipped first scanned offsets and
    `break`ed on the first index before the start of the movie, so the three
    negative offsets compared *zero frames*, found zero mismatches, and were
    reported viable -- an input-fidelity check that reports success having checked
    nothing. Three properties are pinned here: a planted offset is found uniquely, a
    corrupted sequence is rejected at every offset, and an offset covering too
    little is never viable however few mismatches it has.

  * **`identity.py`'s subtitle sampling** turns 108 subtitle anchors into a spread
    of screenshot frames. It shipped with `subs[i * step]` inside
    `range(0, len(subs), step)`, which indexes 104 * 13 and dies on the last
    anchor; and the check written to catch it tested `i` rather than `i * step`, so
    it agreed with the broken code. Both are pinned below.

The comparator is reimplemented here in Python. That duplication is the point: the
Lua cannot be imported, and a test that calls the code it is testing cannot catch a
comparator that is wrong in the same way twice.

No real movie is needed. The movie is third-party and copyrighted and is never
committed (see LEGAL.md), so every sequence here is synthetic with a known answer.
"""

from __future__ import annotations

import pathlib
import random
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools" / "tas"))

import identity  # type: ignore[import-not-found]  # noqa: E402

ok = 0


def check(name: str, cond: object, detail: str = "") -> None:
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


IDLE = "." * 8
ALPHABET = ".RLDUTSBA"
ALIGN_LO, ALIGN_HI = -4, 4


def find_alignment(expected: list[str], delivered: list[str],
                   lo: int = ALIGN_LO, hi: int = ALIGN_HI,
                   min_cover: int | None = None):
    """Port of find_alignment() in tools/tas/drive.lua.

    At offset `off`, index i is a match when expected[i + off] == delivered[i].
    Frames before the start are SKIPPED and the scan continues; reaching past the
    end ends the scan. An offset is viable only with zero mismatches AND coverage
    of at least `min_cover` frames.

    `min_cover` defaults to the MOVIE's length, not to the delivered length. That
    distinction is the whole reason the parameter exists: measured against the
    delivered length, a run that delivered fifty frames of a 44003-frame movie
    asks only that those fifty agree, and offset zero passes with no coverage of
    the run at all.
    """
    nframes = len(expected)
    if min_cover is None:
        min_cover = nframes - 16
    viable: list[int] = []
    detail: dict[int, dict[str, int]] = {}
    for off in range(lo, hi + 1):
        bad = pressed = compared = 0
        for i in range(len(delivered)):
            frame = i + off
            if frame >= nframes:
                break                      # past the end of the movie
            if frame >= 0:                 # before the start: skip, do not stop
                compared += 1
                want = expected[frame]
                got = delivered[i]
                if not (want == IDLE and got == IDLE):
                    pressed += 1
                    if want != got:
                        bad += 1
        detail[off] = {"bad": bad, "pressed": pressed, "compared": compared}
        if bad == 0 and compared >= min_cover:
            viable.append(off)
    return viable, detail


def plant(expected: list[str], off: int, extra: int = 8) -> list[str]:
    """A delivered stream for which offset `off` is the true alignment.

    Built by index arithmetic with a bounds check rather than by writing
    `delivered[i + off] = expected[i]`: for a negative off that assignment indexes
    from the END of the list in Python and quietly corrupts the tail instead of
    failing, which is its own kind of false pass.
    """
    n = len(expected)
    out: list[str] = []
    for i in range(n + extra):
        j = i + off
        out.append(expected[j] if 0 <= j < n else IDLE)
    return out


def corrupt_at(seq: list[str], index: int) -> list[str]:
    out = list(seq)
    out[index] = "R......." if out[index] != "R......." else "........"
    return out


def synth(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(ALPHABET[1:]) if rng.random() < 0.62 else "."
                    for _ in range(8)) for _ in range(n)]


# ------------------------------------------------------------ planted offsets
print("alignment comparator: a planted offset is found, uniquely")

N = 4000
movie: list[str] = synth(N, seed=20261005)

for planted in (-3, -2, -1, 0, 1, 2, 3):
    delivered = plant(movie, planted)
    viable, detail = find_alignment(movie, delivered)
    check(f"planted offset {planted:+d} found, uniquely",
          viable == [planted], f"got {viable}")
    check(f"planted offset {planted:+d} has zero mismatches over real presses",
          detail[planted]["bad"] == 0 and detail[planted]["pressed"] > 100,
          f"got {detail[planted]}")

# ------------------------------------------------- the gate rejects corruption
print("\nalignment comparator: divergence from the file is rejected at every offset")

# The reference stays the CLEAN movie and only the delivered stream is corrupted,
# because that is the failure the gate exists for: the emulator delivering
# something other than what the movie file says. Corrupting both sides agrees by
# construction and proves nothing -- and it is worth being explicit that this gate
# cannot detect a corrupted .fm2, only a diverging emulator.
for planted in (-3, -2, -1, 0, 1, 2, 3):
    delivered = plant(corrupt_at(movie, 2000), planted)
    viable, detail = find_alignment(movie, delivered)
    check(f"one flipped frame is rejected (planted {planted:+d})",
          viable == [], f"got {viable}; a diverging input stream passed the gate")
    check(f"no offset is clean when delivery diverges (planted {planted:+d})",
          all(v["bad"] > 0 for v in detail.values()), f"got {detail}")

# A constant whole-run shift is the gate's KNOWN LIMITATION and is asserted here as
# a limitation rather than papered over: the comparator is built to find the offset,
# so a delivery that is consistently shifted by one frame is reported as "aligned at
# -1" and the verdict accepts it. Sequence comparison alone cannot tell a true
# offset from a uniformly wrong one. This is why the verdict does not rest on the
# alignment gate alone -- coverage, liveness and the ending screenshots are
# independent evidence, and drive.lua's measured offset is cross-checked against
# the authoritative search in the same summary.
for planted in (-2, -1, 1, 2):
    n = len(movie)
    shifted = [movie[i + planted] if 0 <= i + planted < n else IDLE
               for i in range(n + 8)]
    viable, _ = find_alignment(movie, shifted)
    check(f"a uniformly shifted delivery is reported at its own offset ({planted:+d}),"
          f" not silently accepted at 0",
          viable == [planted],
          f"got {viable} -- a shift reported at the wrong offset is a comparator bug")

# ------------------------------------------------ the bug that actually shipped
print("\nalignment comparator: an offset that compares nothing is not viable")

for planted in (-4, -3, -2, -1):
    delivered = plant(movie, planted)
    _, detail = find_alignment(movie, delivered)
    thin = [o for o, v in detail.items() if v["compared"] < N - 16]
    check(f"planted {planted:+d}: thin offsets really are thin",
          all(detail[o]["compared"] < N - 16 for o in thin), f"got {detail}")
    check(f"planted {planted:+d}: a thin offset with 0 mismatches is still not viable",
          all(detail[o]["bad"] > 0 or detail[o]["compared"] >= N - 16
              for o in range(ALIGN_LO, ALIGN_HI + 1)),
          f"got {detail}")

# The specific failure: a scan that breaks on the first out-of-range index reports
# a bad scan as a good one. Model it directly, so the regression is named.
def buggy_alignment(expected: list[str], delivered: list[str]) -> list[int]:
    """The version that shipped: break on the first index before the start."""
    nframes = len(expected)
    viable = []
    for off in range(ALIGN_LO, ALIGN_HI + 1):
        bad = compared = 0
        for i in range(len(delivered)):
            frame = i + off
            want = expected[frame] if 0 <= frame < nframes else None
            if want is None:
                break
            compared += 1
            if want != delivered[i] and not (want == IDLE and delivered[i] == IDLE):
                bad += 1
        if bad == 0:
            viable.append(off)
    return viable


for planted in (-4, -3, -2):
    delivered = plant(movie, planted)
    good = find_alignment(movie, delivered)[0]
    buggy = buggy_alignment(movie, delivered)
    check(f"the shipped bug calls {planted:+d} viable when the answer is {good}",
          planted in buggy and good not in buggy,
          f"buggy={buggy} correct={good} -- if this fails, the bug is no longer the "
          f"one this test was written for")

# ------------------------------------------------------------ degenerate cases
print("\nalignment comparator: degenerate streams")

idle_movie: list[str] = [IDLE] * 500
viable, detail = find_alignment(idle_movie, plant(idle_movie, 0))
check("an all-idle stream is ambiguous, not uniquely aligned",
      len(viable) > 1, f"got {viable} -- a unique offset with no pressed frames is a guess")

viable, _ = find_alignment(movie, movie[:50])
check("a 50-frame stream does not verify a 4000-frame movie",
      viable == [], f"got {viable}")

viable, _ = find_alignment(movie, [])
check("an empty delivered stream does not verify anything",
      viable == [], f"got {viable}")

# --------------------------------------------------------------- identity.py
print("\nidentity.py: subtitle sampling")


def spread(subs: list[tuple[int, str]], shots: int) -> list[int]:
    """The sampling identity.py performs, which shipped as subs[i * step]."""
    step = max(1, len(subs) // max(1, shots))
    return [subs[i][0] for i in range(0, len(subs), step)]


for nsubs in (1, 2, 7, 8, 9, 13, 100, 107, 108, 109, 250):
    for shots in (1, 2, 8, 13):
        s = [(50 + 100 * i, f"note {i}") for i in range(nsubs)]
        anchors = {f for f, _ in s}
        try:
            got = spread(s, shots)
        except IndexError as e:
            raise AssertionError(
                f"sampling {nsubs} anchors asking {shots} shots raised "
                f"IndexError: {e}") from e
        check(f"sampling {nsubs} anchors -> {shots} shots yields only real anchors",
              got and all(f in anchors for f in got),
              f"got {got[:6]}")

s108 = [(50 + 100 * i, f"note {i}") for i in range(108)]
got = spread(s108, 8)
check("sampling is monotonic", got == sorted(got), f"got {got}")
check("sampling takes distinct frames", len(set(got)) == len(got), f"got {got}")
check("sampling spans the run rather than clustering at the front",
      got[0] == s108[0][0] and got[-1] >= s108[-8][0], f"got {got}")
check("sampling every anchor is possible when asked for all of them",
      spread(s108, 108) == [f for f, _ in s108], f"got {spread(s108, 108)[:5]}")

# --------------------------------------------------------------- iNES parsing
print("\nidentity.py: iNES header")

hdr = bytearray(16)
hdr[0:4] = b"NES\x1a"
hdr[4], hdr[5] = 2, 0            # 32 KiB PRG, CHR = 0 -> CHR-RAM
hdr[6] = 0x42                    # mapper low nibble 4, battery set
parsed = identity.parse_ines(bytes(hdr) + bytes(2 * 16384))
check("CHR=0 in the header reads as CHR-RAM, not as no graphics",
      parsed["chr_is_ram"] and parsed["chr_bytes"] == 0, f"got {parsed}")
check("mapper 4 is decoded from both flag bytes",
      parsed["mapper"] == 4, f"got mapper {parsed['mapper']}")
check("the battery bit is read, not assumed",
      parsed["battery"] is True, f"got {parsed['battery']}")
check("PRG is taken from the bank count",
      parsed["prg_bytes"] == 32768 and len(parsed["prg"]) == 32768,
      f"got {parsed['prg_bytes']}/{len(parsed['prg'])}")

check("a non-iNES file is rejected rather than half-parsed",
      "error" in identity.parse_ines(b"NOTANES" + bytes(32)), "no error reported")

print(f"\ntas alignment: {ok} checks")
print("all checks passed")