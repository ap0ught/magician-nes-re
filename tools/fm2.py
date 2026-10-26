#!/usr/bin/env python3
"""Parse an FCEUX `.fm2` movie and refuse to be used against the wrong cartridge.

Why this exists
---------------
An `.fm2` names its cartridge by *name* and *checksum*, and the name is
worthless: six dumps of Magician sit on this machine, five of them with
byte-identical 16-byte iNES headers, and the one this movie was recorded on is
`Magician (USA) [!]` -- a GoodTools header-normalised name for **the release**,
which is not the cartridge this project builds (see `asm/patches.py`'s
`DEFAULT_CART`). So the only thing in the file that identifies the ROM is
`romChecksum base64:<b64 of md5(PRG+CHR)>`, and this tool checks exactly that,
against bytes read off a file, before it will emit a single button state.

Replaying a verified 44 003-frame movie against the wrong cartridge desyncs
*silently*: every frame advances, nothing crashes, and the picture goes wrong at
some unknown frame. This project has lost five sessions to instruments that
reported plausible numbers about the wrong thing, so "is this the right ROM" is
checked here rather than inferred later from a screenshot.

What the file actually contains (measured, not assumed)
------------------------------------------------------
Three things about this movie differ from what the brief said, and each is a
trap that would quietly corrupt a replay:

1. **The frame-index field is literally `0` on all 44 003 lines.** The v3
   format has a frame-number field and this writer left it zeroed. So the frame
   number is the *ordinal of the line*, and nothing else -- reading the declared
   field would produce 44 003 frames all numbered 0 and a comparison against a
   subtitle at frame 2832 would land on subtitle 0. The parser asserts the
   declared field's actual distribution and prints it, rather than trusting it.

2. **There are 108 `subtitle` lines, not 120.**

3. **The eight-character button field is `R L D U T S B A`, not
   `A B Select Start Up Down Left Right`.** It is written most-significant bit
   first. Each position only ever holds its own letter across all 44 003 frames
   (position 0 only ever `R`, position 7 only ever `A`), which is what confirms
   the order -- a 45th letter in a position would mean the order is wrong, so
   that is asserted per position rather than per character.

   The `$4016` byte
---------------
Derived, not guessed, from that order: the field's position *i* is bit
`7 - i`, giving the standard NES latch layout `A=bit0 .. Right=bit7`.
`tools/bizhawk/replay.lua` then *checks* it -- it reads `$4016` back out of the
running core and compares, so if BizHawk's own pad order differs from the
assumption here the run fails loudly on the first frame instead of desyncing.

Self-coverage
-------------
Every line of the file must be accounted for: header key, subtitle, frame line,
or the single trailing empty line. A line that is none of those is a hard error,
because "the parser read 44 000 of 44 003 lines and did not notice" is exactly
the failure this file exists to prevent. The frame count, the per-position
alphabet, the declared-index distribution and the checksum are all asserted.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import pathlib
import sys
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

# The NES controller, in *bit* order: this is the `$4016` bit layout, and it is
# the canonical order for reporting.
BUTTONS = ("A", "B", "Select", "Start", "Up", "Down", "Left", "Right")
BIT = {name: 1 << i for i, name in enumerate(BUTTONS)}

# The `.fm2` character field, in *position* order -- measured, not assumed, and
# it is NOT the bit order.
#
# The brief this tool was written against said the eight characters were in NES
# order "A B Select Start Up Down Left Right". Every position in this movie
# disagrees: position 0 holds only `R` (22 908 frames) and never `A`, position 7
# holds only `A` (433 frames) and never `R`. The field is written
# most-significant-bit first, so the real order is the reverse:
#
#     position  0 1 2 3 4 5 6 7
#     letter    R L D U T S B A
#     $4016    7 6 5 4 3 2 1 0
#
# Note `T`, not `A`, for Start: that is FCEUX's mnemonic, and it is a second
# reason not to guess the table from button names. Position 5 is `S` = Select
# and position 4 is `T` = Start, so the letters are not even alphabetically
# suggestive.
#
# Taking the brief at face value would have injected **B+Right** on frame 0
# (`|0|.L.....A|||`) where the movie says **A+Left** -- the wrong direction on the
# very first frame, which is a desync no later assertion would attribute to the
# injector. `tools/bizhawk/replay.lua` reads `$4016` back out of the core and
# compares, so this table is confirmed against hardware at run time too.
POS_ORDER = ("Right", "Left", "Down", "Up", "Start", "Select", "B", "A")
POS_LETTERS = "RLDUTSBA"

# Default location of the movie. It is third-party and copyrighted and is NEVER
# committed -- see LEGAL.md -- so it lives outside the tree and the harness takes
# the path as an argument.
DEFAULT_MOVIE = pathlib.Path(
    "/tmp/opencode/tas/Magician (U)-FatRatKnight GoodEnd+subtitle.fm2")


class MovieError(Exception):
    """The movie cannot be trusted. Never downgraded to a warning."""


@dataclass
class Subtitle:
    frame: int
    text: str


@dataclass
class Movie:
    path: pathlib.Path
    header: dict[str, list[str]] = field(default_factory=dict)
    frames: list[int] = field(default_factory=list)      # $4016 byte per frame
    raw: list[str] = field(default_factory=list)         # the 8-char field
    subtitles: list[Subtitle] = field(default_factory=list)
    declared_index: dict[str, int] = field(default_factory=dict)
    emu_version: str = ""
    endings: str = ""
    # Set only by an explicit `--replay-anyway LABEL`. Carried into the generated
    # Lua so that every replay run's log line says, unmissably, that it is
    # replaying a movie onto a cartridge the movie was not recorded on.
    cart_note: str = ""

    @property
    def n_frames(self) -> int:
        return len(self.frames)

    @property
    def rom_checksum_b64(self) -> str:
        return self.header.get("romChecksum", [""])[0].partition(":")[2].strip()

    @property
    def rom_checksum_md5(self) -> str:
        b64 = self.rom_checksum_b64
        try:
            return binascii.hexlify(base64.b64decode(b64, validate=True)).decode()
        except (binascii.Error, ValueError) as exc:
            raise MovieError(f"romChecksum {b64!r} is not valid base64: {exc}") from exc

    def buttons(self, frame: int) -> list[str]:
        if not 0 <= frame < self.n_frames:
            raise MovieError(f"frame {frame} is outside 0..{self.n_frames - 1}")
        return [b for b in BUTTONS if self.frames[frame] & BIT[b]]


def nes_body_md5(path: pathlib.Path) -> tuple[str, int, int]:
    """MD5 of PRG+CHR with the 16-byte iNES header stripped, plus the sizes.

    This is what an `.fm2` `romChecksum` is over. Hashing the whole file, or the
    PRG alone, gives a different answer for the same cartridge and would make a
    correct ROM look like a mismatch -- so the range is derived from header bytes
    4 and 5 and then *checked* against the file length.
    """
    data = path.read_bytes()
    if data[:4] != b"NES\x1a":
        raise MovieError(f"{path} has no iNES magic ({data[:4]!r})")
    prg, chr_ = data[4] * 16384, data[5] * 8192
    if len(data) != 16 + prg + chr_:
        raise MovieError(f"{path} is {len(data)} bytes; header declares "
                         f"{16 + prg + chr_} (16 + {prg} PRG + {chr_} CHR)")
    if prg == 0:
        raise MovieError(f"{path} declares no PRG; every dump of this title is "
                         f"128 KiB, so the offset arithmetic here would be wrong")
    return hashlib.md5(data[16:16 + prg + chr_]).hexdigest(), prg, chr_


def verify_cart(movie: Movie, rom: pathlib.Path) -> str:
    """Return the ROM's md5(PRG+CHR), or raise. The movie must name this ROM."""
    got, prg, chr_ = nes_body_md5(rom)
    want = movie.rom_checksum_md5
    if got != want:
        name = movie.header.get("romFilename", ["<none>"])[0]
        raise MovieError(
            f"{rom} has md5(PRG+CHR) {got}\n"
            f"  but {movie.path.name} was recorded on {name!r}, "
            f"md5(PRG+CHR) {want}.\n"
            f"  Replaying it against a different cartridge desyncs silently, so "
            f"this tool refuses. Point --rom at the dump the movie names.")
    return got


def parse(path: pathlib.Path) -> Movie:
    """Parse the whole file, asserting that nothing was skipped."""
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise MovieError(f"{path} contains a NUL byte; it is not a text movie")

    # CRLF is the norm for .fm2. Both are accepted, and which one it was is
    # reported, because a parser that silently accepts LF can hide a truncated
    # file behind a shorter line count.
    if b"\r\n" in raw:
        endings, lines = "CRLF", raw.split(b"\r\n")
    elif b"\n" in raw:
        endings, lines = "LF", raw.split(b"\n")
    else:
        endings, lines = "none", [raw]
    if lines and lines[-1] == b"":
        lines.pop()          # the file's one trailing newline
    else:
        raise MovieError(f"{path} does not end in a newline; it may be truncated")

    mov = Movie(path=path, endings=endings)  # type: ignore[call-arg]
    in_frames = False
    unaccounted: list[tuple[int, str]] = []

    for lineno, bl in enumerate(lines, 1):
        line = bl.decode("latin-1").rstrip("\r")
        if in_frames:
            if not line.startswith("|"):
                unaccounted.append((lineno, line))
                continue
        elif line.startswith("|"):
            in_frames = True
        else:
            if line.startswith("subtitle "):
                rest = line[len("subtitle "):]
                head, _, text = rest.partition(" ")
                if not head.isdigit():
                    raise MovieError(f"line {lineno}: subtitle frame {head!r} is "
                                     f"not a number")
                mov.subtitles.append(Subtitle(int(head), text.strip()))
                continue
            key, _, value = line.partition(" ")
            if not key or key.startswith(";") or key.startswith("#"):
                continue
            mov.header.setdefault(key.strip(), []).append(value.strip())
            continue
        mov.frames.append(_frame_byte(line, lineno))
        mov.raw.append(line.split("|")[2])
        mov.declared_index[line.split("|")[1]] = \
            mov.declared_index.get(line.split("|")[1], 0) + 1

    if unaccounted:
        raise MovieError(
            f"{len(unaccounted)} line(s) after the first frame line are neither a "
            f"frame line nor a subtitle; the first is line {unaccounted[0][0]}: "
            f"{unaccounted[0][1]!r}. A parser that ignores them is a parser that "
            f"is not looking at the whole file.")

    if mov.header.get("version", [""])[0] != "3":
        raise MovieError(f"{path} is fm2 version "
                         f"{mov.header.get('version', ['<none>'])[0]!r}; this "
                         f"parser reads version 3 and would misread anything else")
    _coverage(mov)
    return mov


def _frame_byte(line: str, lineno: int) -> int:
    """One `|idx|state|p1|p2|p3|` line -> the byte the pad register should see."""
    parts = line.split("|")
    if len(parts) != 6:
        raise MovieError(f"line {lineno}: a frame line has {len(parts)} "
                         f"pipe-separated fields, not 6: {line!r}")
    state = parts[2]
    if len(state) != 8:
        raise MovieError(f"line {lineno}: the button field is {len(state)} "
                         f"characters, not 8: {state!r}")
    value = 0
    for i, ch in enumerate(state):
        if ch == ".":
            continue
        want = POS_ORDER[i]
        if ch != POS_LETTERS[i]:
            # Every position in this movie holds exactly one letter. A different
            # letter in a position means the position order above is wrong, and a
            # wrong order replays a completely different game.
            raise MovieError(
                f"line {lineno}: position {i} holds {ch!r} but this movie's field "
                f"is {POS_LETTERS}, where that slot is {POS_LETTERS[i]!r} "
                f"({want}). The character-to-button order is wrong, so this "
                f"movie would replay a different game.")
        value |= BIT[want]
    return value


def _coverage(mov: Movie) -> None:
    """Assert the parser looked at everything it claims to have looked at."""
    if mov.n_frames == 0:
        raise MovieError("no frame lines were found")
    # Each position must have been seen with its own letter at least once,
    # otherwise the order was never actually exercised and is merely asserted.
    for i, name in enumerate(POS_ORDER):
        if not any(r[i] != "." for r in mov.raw):
            raise MovieError(f"position {i} ({name}, {POS_LETTERS[i]!r}) is never "
                             f"pressed in this movie, so the {POS_LETTERS} order "
                             f"cannot have been confirmed against it")
    subs = mov.subtitles
    if not subs:
        raise MovieError("no subtitle lines were found; this parser's whole "
                         "point is the anchors they carry")
    if any(s.frame >= mov.n_frames for s in subs):
        bad = [s for s in subs if s.frame >= mov.n_frames][0]
        raise MovieError(f"a subtitle is at frame {bad.frame}, past the movie's "
                         f"{mov.n_frames} frames")
    if list(s.frame for s in subs) != sorted(s.frame for s in subs):
        raise MovieError("subtitle frames are not in ascending order")


def emit_lua(mov: Movie, path: pathlib.Path) -> None:
    """A flat Lua table of the $4016 byte per frame, plus the subtitle anchors.

    Lua has no cheap way to read 44 003 separate lines at startup, and a
    generated table is both faster and impossible to misparse at run time.
    """
    out = ["-- GENERATED by tools/fm2.py. Do not edit, do not commit.",
           f"-- from {mov.path.name}: {mov.n_frames} frames, "
           f"{len(mov.subtitles)} subtitle anchors",
           f"-- md5(PRG+CHR) of the cartridge it was recorded on: "
           f"{mov.rom_checksum_md5}",
           f"-- button order derived from the file: {POS_LETTERS} "
           f"({' '.join(POS_ORDER)}), position i = $4016 bit {7}-i",]
    if mov.cart_note:
        out.append(f"-- !! {mov.cart_note}")
    out += [f"MAGICIAN_FRAMES = {mov.n_frames}",
           "MAGICIAN_PAD = {",
           _lua_bytes(mov.frames),
           "}",
           "MAGICIAN_SUBS = {",
           ]
    for s in mov.subtitles:
        out.append(f"  {{ frame = {s.frame}, text = {_lua_str(s.text)} }},")
    out.append("}")
    out.append("")
    path.write_text("\n".join(out))


def emit_bin(mov: Movie, path: pathlib.Path) -> None:
    path.write_bytes(bytes(mov.frames))


def _lua_bytes(values: list[int]) -> str:
    line: list[str] = []
    out: list[str] = []
    for i, v in enumerate(values):
        line.append(f"{v}")
        if len(line) == 32:
            out.append("  " + ",".join(line) + ",")
            line = []
    if line:
        out.append("  " + ",".join(line) + ",")
    return "\n".join(out)


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("movie", nargs="?", type=pathlib.Path, default=DEFAULT_MOVIE)
    ap.add_argument("--rom", type=pathlib.Path, default=None,
                    help="the cartridge to check the movie's checksum against; "
                         "required unless --no-verify")
    ap.add_argument("--no-verify", action="store_true",
                    help="parse and report without checking any cartridge "
                         "(for inspecting a movie whose ROM is not on this machine)")
    ap.add_argument("--replay-anyway", metavar="LABEL", default=None,
                    help="REQUIRED and free text: the reason for replaying this "
                         "movie onto a cartridge its checksum does not match. "
                         "The default is to refuse, because that desyncs "
                         "silently; this records the intent instead. The label "
                         "is copied into the generated Lua, so every downstream "
                         "run log carries it.")
    ap.add_argument("--expect-frames", type=int, default=None,
                    help="fail unless the movie has exactly this many frames")
    ap.add_argument("--lua", type=pathlib.Path, default=None,
                    help="write the frame table as Lua here")
    ap.add_argument("--bin", type=pathlib.Path, default=None,
                    help="write the $4016 bytes as a flat binary here")
    ap.add_argument("--at", type=int, action="append", default=[],
                    help="print what is pressed on these frames (repeatable)")
    ap.add_argument("--subs", action="store_true", help="print every subtitle")
    args = ap.parse_args()

    if not args.movie.is_file():
        print(f"fm2: no movie at {args.movie}", file=sys.stderr)
        return 2
    mov = parse(args.movie)

    if args.expect_frames is not None and mov.n_frames != args.expect_frames:
        print(f"fm2: FAIL -- {mov.path.name} has {mov.n_frames} frames, "
              f"expected {args.expect_frames}", file=sys.stderr)
        return 2

    print(f"{mov.path.name}: fm2 v{mov.header['version'][0]} "
          f"(emuVersion {mov.header.get('emuVersion', ['?'])[0]}), "
          f"{mov.endings} line endings")
    h = mov.header
    print(f"  cartridge named   : {h.get('romFilename', ['<none>'])[0]!r}")
    print(f"  romChecksum       : {mov.rom_checksum_b64}")
    print(f"    = md5(PRG+CHR)  : {mov.rom_checksum_md5}")
    print(f"  rerecordCount     : {h.get('rerecordCount', ['?'])[0]}")
    print(f"  fourscore/palFlag : {h.get('fourscore', ['?'])[0]} / "
          f"{h.get('palFlag', ['?'])[0]}")
    print(f"  ports 0/1/2       : {h.get('port0', ['?'])[0]}/"
          f"{h.get('port1', ['?'])[0]}/{h.get('port2', ['?'])[0]}")
    for c in h.get("comment", []):
        print(f"  comment           : {c}")
    print(f"  frames            : {mov.n_frames}")
    print(f"  subtitle anchors  : {len(mov.subtitles)}")
    # The declared frame-index field, printed because reading it instead of the
    # line ordinal would give 44 003 frames all numbered 0.
    idx_summary = ", ".join(f"{k!r}x{v}" for k, v in
                            sorted(mov.declared_index.items(), key=lambda kv: -kv[1])[:4])
    print(f"  declared index    : {idx_summary}")
    if list(mov.declared_index) == ["0"]:
        print("                      ^ every line declares 0. This writer left the "
              "v3 frame-number")
        print("                        field zeroed, so the frame number is the "
              "LINE ORDINAL and")
        print("                        nothing else. Do not read it from the file.")

    # The button field's character order, derived from the file: each position's
    # letter is whatever this movie actually holds there, and if a position held
    # two different letters the parse above would already have failed.
    print("  button field      : position  " +
          " ".join(f"{i}" for i in range(8)) +
          "   ($4016 bit " + " ".join(str(7 - i) for i in range(8)) + ")")
    print("                     " +
          " ".join(POS_LETTERS) +
          f"   full: {' '.join(POS_ORDER)}")

    if args.no_verify:
        print("  cartridge check   : SKIPPED (--no-verify). Nothing downstream may "
              "trust this parse.")
    elif args.rom is None:
        print("  cartridge check   : NOT DONE (no --rom). A parse with no "
              "checksum check is not a verified movie.", file=sys.stderr)
        return 2
    else:
        want = mov.rom_checksum_md5
        got = nes_body_md5(args.rom)[0]
        try:
            got = verify_cart(mov, args.rom)
        except MovieError as exc:
            if not args.replay_anyway:
                raise
            _, prg, chr_ = nes_body_md5(args.rom)
            mov.cart_note = (f"REPLAYING {args.rom.name} (md5(PRG+CHR) {got}) "
                             f"onto a movie recorded on md5 {want}. "
                             f"REASON: {args.replay_anyway}")
            print(f"  cartridge check   : ** MISMATCH, ON PURPOSE **")
            print(f"    {args.rom}")
            print(f"    {prg} PRG + {chr_} CHR bytes -> md5(PRG+CHR) {got}")
            print(f"    the movie was recorded on            md5(PRG+CHR) {want}")
            print(f"    reason given: {args.replay_anyway}")
            print(f"    This movie WILL desync unless that is the point. Any "
                  f"divergence below")
            print(f"    is evidence about the two cartridges, not about the "
                  f"injector, until the")
            print(f"    same injector is shown to hold the movie on the cartridge "
                  f"it was made for.")
        else:
            prg, chr_ = nes_body_md5(args.rom)[1:]
            print(f"  cartridge check   : OK -- {args.rom}")
            print(f"                      md5(PRG+CHR) {got} over {prg} PRG + "
                  f"{chr_} CHR bytes")

    if args.subs:
        for s in mov.subtitles:
            print(f"    frame {s.frame:6d}  {s.text}")
    for f in args.at:
        if not 0 <= f < mov.n_frames:
            print(f"fm2: frame {f} is outside the movie", file=sys.stderr)
            return 2
        print(f"  frame {f:6d}: ${mov.frames[f]:02X} "
              f"{'+'.join(mov.buttons(f)) or '(nothing)'}")

    if args.lua:
        emit_lua(mov, args.lua)
        print(f"  wrote {args.lua} ({args.lua.stat().st_size} bytes)")
    if args.bin:
        emit_bin(mov, args.bin)
        print(f"  wrote {args.bin} ({args.bin.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MovieError as exc:
        print(f"fm2: {exc}", file=sys.stderr)
        raise SystemExit(2)