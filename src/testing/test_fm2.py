"""The `.fm2` parser: the button order, the checksum, and the frame ordinal.

    python3 src/testing/test_fm2.py

`tools/fm2.py` turns a 709 847-byte FCEUX movie into a table of `$4016` bytes
and replays them into BizHawk. Three properties of that file are not in the
format's documentation and all three are load-bearing, so each is a test here
rather than a comment in the tool:

  * **The button field is `R L D U T S B A`, not `A B Select Start Up Down Left
    Right`.** It is written most-significant-bit first, and `T` -- not `A` --
    is FCEUX's mnemonic for Start. Reading the field the documented way injects
    the wrong button on the very first frame and desyncs a replay that nothing
    downstream can attribute to the injector. Position `i` is `$4016` bit
    `7 - i`.
  * **The frame-number field is literally `0` on every one of the 44 003
    lines.** The v3 writer left it zeroed. The frame number is therefore the
    *ordinal of the line*, and a subtitle at frame 2832 must be compared
    against line 2832 -- not against the field, which would send it to
    subtitle 0.
  * **`romChecksum base64:` is md5(PRG+CHR) with the 16-byte header
    stripped.** Hashing the whole file, or PRG alone, gives a different answer
    for the same cartridge, and the tool would then refuse a correct ROM.

Everything below is checked against movies this file writes, from arithmetic in
this file. No real movie is required: the movie is third-party and copyrighted,
is never committed, and its length and subtitle count are properties of that
file rather than of this parser.

Twenty-eight checks:

   1-6.  the button field maps to the right `$4016` bits, per position
   7-9.  a letter in the wrong position, a short field, and a wrong field count
          all RAISE rather than being reinterpreted
  10-13. the frame number is the line ordinal, not the declared field
  14-17. CRLF and LF both parse and are reported; no trailing newline and a NUL
          byte both RAISE
  18-22. romChecksum is md5(PRG+CHR): not the whole file, not PRG alone, and
          `verify_cart` refuses a mismatch naming both digests
  23-25. a v2 movie, an unaccounted-for line, and a subtitle past the end all
          RAISE
  26-27. emit_lua / emit_bin round-trip the frame table
  28.   every position must be pressed somewhere, or the order was never
          exercised

WHAT IT DOES NOT CLAIM

  * **No real movie is required, and none is read.** The conditional at the end
    is the one place in this suite that can skip, and it says so in its output
    rather than quietly: `MAGICIAN_MOVIE`, or `fm2.DEFAULT_MOVIE` if it exists,
    is parsed and put through the same consistency checks, and the line says
    which happened.
  * **Nothing here proves a replay stays in sync.** That is a property of the
    emulator and the cartridge, not of the parser. What the parser guarantees is
    that a byte in the table means the button it names.
  * **The declared-index check does not prove the real movie's field is zero.**
    It proves the parser ignores it, which is the property that survives either
    way.

WHAT IT NEEDS: python3. Under a second.

Run:  python3 src/testing/test_fm2.py
"""

import base64
import binascii
import hashlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

os.chdir(ROOT)

import fm2                    # noqa: E402  (tools/fm2.py)
import synthcart as S         # noqa: E402  (src/testing/synthcart.py)

TMP = Path("/tmp/opencode/magician-testing/fm2")
TMP.mkdir(parents=True, exist_ok=True)

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


def raises(name, fn, exc=fm2.MovieError, must_say=()):
    global ok
    try:
        got = fn()
    except exc as e:
        missing = [m for m in must_say if m not in str(e)]
        if missing:
            raise AssertionError(
                f"{name}: raised, but not with {missing!r}.\n         It said: {e}"
            ) from None
        ok += 1
        print(f"  ok {name}")
        return
    raise AssertionError(
        f"{name}: returned {got!r} instead of raising. A wrong button injected on "
        f"frame 0 desyncs a 44 003-frame replay with no error anywhere, so the "
        f"parser has to refuse rather than guess.")


# =====================================================================================
# A synthetic movie. The header is the shape of the real file's header with no
# third-party content in it, and the frames are chosen so that every one of the
# eight positions is pressed at least once -- `_coverage` refuses a movie where
# the order could not have been confirmed.
# =====================================================================================
# The four probe frames, one per NES-order button, written in the POSITION order
# the file actually uses. So frame 0 presses position 0 ('R' = Right) and
# position 7 ('A'); read as A B Select Start Up Down Left Right it would press A
# and Left, which is the bug this file exists for.
PROBES = [
    "........",     # position -            : nothing pressed
    "R......A",     # positions 0 and 7     : Right + A
    ".L....B.",     # positions 1 and 6     : Left + B
    "..D..S..",     # positions 2 and 5     : Down + Select
    "...UT...",     # positions 3 and 4     : Up + Start
]
SUBTITLE_FRAME = 2


def build_movie(path, frames=PROBES, newline="\r\n", declared="0",   # noqa: ANN001
                header_extra=(), rom_b64=None, version="3",
                trailing_newline=True, append_after_frames=()):
    """A minimal valid `.fm2`. Everything the parser looks at is a parameter, so
    each check below can build the *wrong* movie rather than editing the file."""
    body = []
    if rom_b64 is None:
        rom_b64 = base64.b64encode(bytes(16)).decode()
    for k, v in (("version", version), ("emuVersion", "20100"),
                 ("rerecordCount", "2534"), ("palFlag", "0"),
                 ("romFilename", "synthetic (test) [!]"),
                 ("romChecksum", f"base64:{rom_b64}")):
        body.append(f"{k} {v}")
    body.extend(header_extra)
    body.append(f"subtitle {SUBTITLE_FRAME} a synthetic anchor")
    for i, f in enumerate(frames):
        idx = declared(i) if callable(declared) else declared
        body.append(f"|{idx}|{f}|||")
    body.extend(append_after_frames)
    text = newline.join(body)
    if trailing_newline:
        text += newline
    path.write_bytes(text.encode("latin-1"))
    return path


MOVIE = build_movie(TMP / "synth.fm2")
m = fm2.parse(MOVIE)

# =====================================================================================
# 1-6. The button order, one position at a time.
# =====================================================================================
# `$4016` bit layout, which is the canonical NES order and is derived rather than
# assumed: position i of the field is bit 7-i, so position 0 is bit 7 = Right.
check("the position order is R L D U T S B A",
      fm2.POS_LETTERS == "RLDUTSBA", fm2.POS_LETTERS)
check("position i maps to $4016 bit 7-i, for all eight positions",
      [fm2.BIT[name] for name in fm2.POS_ORDER] == [0x80, 0x40, 0x20, 0x10,
                                                     0x08, 0x04, 0x02, 0x01],
      str([hex(fm2.BIT[n]) for n in fm2.POS_ORDER]))
check("frame 0 presses nothing", m.buttons(0) == [], m.buttons(0))
check("frame 1 `R......A` is Right+A = $81, NOT A+Left",
      m.buttons(1) == ["A", "Right"] and m.frames[1] == 0x81,
      f"{m.buttons(1)} ${m.frames[1]:02X}")
check("frame 2 `.L....B.` is Left+B = $42",
      m.buttons(2) == ["B", "Left"] and m.frames[2] == 0x42,
      f"{m.buttons(2)} ${m.frames[2]:02X}")
# `buttons()` lists in canonical $4016 bit order (A, B, Select, Start, Up, Down,
# Left, Right), which is NOT the order the field is written in. Asserting that
# here is what stops a future reader from "fixing" the probe expectations below
# into the position order.
check("frame 3 `..D..S..` is Down+Select = $24 -- positions 2 and 5",
      m.buttons(3) == ["Select", "Down"] and m.frames[3] == 0x24,
      f"{m.buttons(3)} ${m.frames[3]:02X}")
check("buttons() reports in $4016 bit order, not field order",
      m.buttons(1) == ["A", "Right"] and fm2.BUTTONS.index("A") < fm2.BUTTONS.index("Right")
      and fm2.BUTTONS.index("Select") < fm2.BUTTONS.index("Down"),
      f"field order is {fm2.POS_LETTERS}; reported order is {list(m.buttons(3))}")
check("frame 4 `...UT...` is Up+Start = $18 -- `T` is Start, `S` is Select",
      m.buttons(4) == ["Start", "Up"] and m.frames[4] == 0x18,
      f"{m.buttons(4)} ${m.frames[4]:02X}")
check("the subtitle anchor landed on frame 2, which is `.L....B.`",
      [s.text for s in m.subtitles] == ["a synthetic anchor"]
      and m.subtitles[0].frame == SUBTITLE_FRAME, m.subtitles)

# =====================================================================================
# 7-9. A field the parser cannot read. Each of these used to be reinterpretable.
# =====================================================================================
raises("a letter in the wrong position RAISES, naming the slot's real letter",
       lambda: fm2.parse(build_movie(TMP / "wrongletter.fm2",
                                      frames=["........", "...T....", "R......A",
                                              "..D..S..", "...UT..."])),
       must_say=("position 3", "order is wrong"))
raises("a 7-character button field RAISES",
       lambda: fm2.parse(build_movie(TMP / "short7.fm2",
                                      frames=[".......", "R......A", ".L....B.",
                                              "..D..S..", "...UT..."])),
       must_say=("7 characters, not 8",))
# `|0|R......A||` is a frame line with FIVE pipe-separated fields, not six: the
# v3 format has four port columns and this line has three. Written by hand here
# rather than through `build_movie`, which can only make well-formed ones.
def _five_fields():
    p = TMP / "fields.fm2"
    p.write_bytes(b"version 3\nromChecksum base64:AAAAAAAAAAAAAAAAAAAAAA==\n"
                  b"|0|R......A||\n")
    return fm2.parse(p)


raises("a frame line with five pipe-separated fields RAISES",
       _five_fields, must_say=("fields, not 6",))

# =====================================================================================
# 10-13. The frame number is the ORDINAL of the line, not the declared field.
# =====================================================================================
# The real movie declares `0` on all 44 003 lines, so a parser that reads the
# field produces 44 003 frames all numbered 0 and every subtitle comparison lands
# on subtitle 0. Here the declared field is a *wrong* index, which is the
# stronger test: if the parser used it at all, nothing below would hold.
wrong = build_movie(TMP / "wrongindex.fm2", declared=lambda i: 1000 + i * 7)
mw = fm2.parse(wrong)
check("a movie whose declared index is 1000, 1007, 1014... still indexes by "
      "ordinal", mw.buttons(1) == ["A", "Right"], mw.buttons(1))
check("the declared index is reported, not silently dropped",
      list(mw.declared_index)[:3] == ["1000", "1007", "1014"],
      f"{sorted(mw.declared_index)[:3]!r}")
def _assert_count(mov, n):
    assert mov.n_frames == n, f"expected {n} frames, got {mov.n_frames}"
    return mov.n_frames


check("a movie whose every frame declares 0 still has one frame per line",
      _assert_count(fm2.parse(build_movie(TMP / "zeroindex.fm2", declared="0")), 5) == 5)
raises("buttons() refuses a frame past the end rather than wrapping",
       lambda: m.buttons(5), must_say=("outside 0..4",))
raises("buttons() refuses a negative frame",
       lambda: m.buttons(-1), must_say=("outside",))

# =====================================================================================
# 14-17. Line endings and file shape.
# =====================================================================================
crlf = fm2.parse(MOVIE)
lf = fm2.parse(build_movie(TMP / "lf.fm2", newline="\n"))
check("CRLF and LF parse to the same table", crlf.frames == lf.frames)
check("...and the difference is reported rather than guessed at",
      (crlf.endings, lf.endings) == ("CRLF", "LF"), f"{crlf.endings}/{lf.endings}")
raises("a movie with no trailing newline RAISES, because it may be truncated",
       lambda: fm2.parse(build_movie(TMP / "notrail.fm2", trailing_newline=False)),
       must_say=("truncated",))
nul = TMP / "nul.fm2"
nul.write_bytes(b"version 3\n\x00\n")
raises("a movie containing a NUL byte RAISES -- it is not a text movie",
       lambda: fm2.parse(nul), must_say=("NUL",))

# =====================================================================================
# 18-22. romChecksum is md5(PRG + CHR), header stripped.
# =====================================================================================
# A synthetic cartridge, so the digests below are md5s of bytes this suite wrote
# and are recomputed here independently of fm2.py.
rom = S.make_rom(TMP / "cand.nes")
body = rom.read_bytes()[16:]
check("nes_body_md5 is md5 of PRG+CHR with the 16-byte header stripped",
      fm2.nes_body_md5(rom)[0] == hashlib.md5(body).hexdigest(),
      f"{fm2.nes_body_md5(rom)[0]} vs {hashlib.md5(body).hexdigest()}")
check("...and is NOT the md5 of the whole file",
      fm2.nes_body_md5(rom)[0] != hashlib.md5(rom.read_bytes()).hexdigest())
check("...and is NOT the md5 of PRG alone",
      fm2.nes_body_md5(rom)[0] != hashlib.md5(rom.read_bytes()[16:16 + 131072]).hexdigest())
check("nes_body_md5 reports the declared sizes",
      fm2.nes_body_md5(rom)[1:] == (131072, 131072),
      repr(fm2.nes_body_md5(rom)[1:]))
notarom = TMP / "notarom.bin"
notarom.write_bytes(b"this is not a cartridge\n")
raises("a file that is not an iNES image RAISES",
       lambda: fm2.nes_body_md5(notarom), must_say=("no iNES magic",))
raises("a header declaring more than the file holds RAISES",
       lambda: fm2.nes_body_md5(S.make_rom(TMP / "trunc.nes", truncate=40)),
       must_say=("header declares",))
chrram = S.make_rom(TMP / "chrram.nes", chr_units=0)
check("CHR = 0 is legal for nes_body_md5 -- CHR-RAM, not 'no graphics'",
      fm2.nes_body_md5(chrram) == (hashlib.md5(chrram.read_bytes()[16:]).hexdigest(),
                                    131072, 0),
      repr(fm2.nes_body_md5(chrram)[1:]))

# The two RomErrors that matter, on real digest comparisons.
good_b64 = base64.b64encode(binascii.unhexlify(fm2.nes_body_md5(rom)[0])).decode()
matched = build_movie(TMP / "match.fm2", rom_b64=good_b64)
check("verify_cart accepts the cartridge the checksum names, and returns the digest",
      fm2.verify_cart(fm2.parse(matched), rom) == fm2.nes_body_md5(rom)[0])
other = S.make_rom(TMP / "other.nes", prg_units=8, chr_units=15)
raises("verify_cart refuses a different cartridge, naming both digests",
       lambda: fm2.verify_cart(fm2.parse(matched), other),
       must_say=(fm2.nes_body_md5(rom)[0], fm2.nes_body_md5(other)[0],
                 "desyncs silently"))
build_movie(TMP / "badb64.fm2", rom_b64="!!!not base64!!!")


def _checksum_of(p):
    return fm2.parse(p).rom_checksum_md5


raises("a romChecksum that is not valid base64 RAISES, naming the string",
       lambda: _checksum_of(TMP / "badb64.fm2"), must_say=("base64",))

# =====================================================================================
# 23-25. Refusals that protect the whole file.
# =====================================================================================
raises("a version 2 movie RAISES rather than being misread as version 3",
       lambda: fm2.parse(build_movie(TMP / "v2.fm2", version="2")),
       must_say=("version", "2"))
raises("a line after the first frame line that is not a frame RAISES",
       lambda: fm2.parse(build_movie(TMP / "junk.fm2",
                                      append_after_frames=["oops, a stray line"])),
       must_say=("neither a", "not looking at the whole file"))
far = SUBTITLE_FRAME + 99
raises("a subtitle past the end of the movie RAISES",
       lambda: fm2.parse(build_movie(TMP / "farsub.fm2",
                                      header_extra=[f"subtitle {far} too late"])),
       must_say=("past the movie",))

# =====================================================================================
# 26-27. The generated artefacts round-trip. These are what the replay actually
#         reads, so a mismatch here is a replay of the wrong buttons.
# =====================================================================================
lua = TMP / "movie.lua"
fm2.emit_lua(m, lua)
text = lua.read_text(encoding="utf-8")
nums = []
inside = False
for ln in text.splitlines():
    if ln.startswith("MAGICIAN_PAD = {"):
        inside = True
        continue
    if inside:
        if ln.startswith("}"):
            break
        nums += [int(x) for x in ln.strip().rstrip(",").split(",") if x.strip()]
check("emit_lua wrote one $4016 byte per frame, in order", nums == m.frames,
      f"{nums} vs {m.frames}")
check("emit_lua wrote the movie's own md5(PRG+CHR) into the header comment",
      m.rom_checksum_md5 in text)
binp = TMP / "movie.padbin"
fm2.emit_bin(m, binp)
check("emit_bin wrote the same bytes", binp.read_bytes() == bytes(m.frames))
check("emit_bin is exactly one byte per frame, so its length IS the frame count",
      len(binp.read_bytes()) == m.n_frames)

# =====================================================================================
# 28. `_coverage`: an order that is never exercised is not a confirmed order.
# =====================================================================================
never = build_movie(TMP / "never.fm2", frames=["........", "........",
                                               ".L....B.", "..D..S..", "...UT..."])
raises("a movie that never presses Right RAISES: the order cannot have been "
       "confirmed against it", lambda: fm2.parse(never), must_say=("Right", "never"))
check("the synthetic movie used above DOES press all eight, so its own parse "
      "proves the order rather than asserting it",
      len({i for i in range(8) if any(f[i] != "." for f in PROBES)}) == 8)

# =====================================================================================
# The real movie, if it happens to be on this machine. Only properties that hold
# for ANY valid movie are checked, so no constant from anyone else's file ends
# up in this repository.
# =====================================================================================
movie_path = Path(os.environ.get("MAGICIAN_MOVIE", fm2.DEFAULT_MOVIE))
if movie_path.is_file():
    extra = 0
    real = fm2.parse(movie_path)
    assert real.n_frames == len(real.frames)
    assert real.rom_checksum_md5 and len(real.rom_checksum_md5) == 32
    assert real.subtitles and list(s.frame for s in real.subtitles) == \
        sorted(s.frame for s in real.subtitles)
    assert max(s.frame for s in real.subtitles) < real.n_frames
    # Every position is exercised -- `_coverage` already enforced it, so this is
    # belt and braces on the *real* file.
    for i, name in enumerate(fm2.POS_ORDER):
        assert any(r[i] != "." for r in real.raw), f"position {i} ({name}) unused"
    extra += 6
    ok += extra
    print(f"  ok 6 more checks on {movie_path.name}: "
          f"{real.n_frames} frames, {len(real.subtitles)} subtitle anchors, "
          f"every button position exercised, checksum decodes to 16 bytes")
else:
    print(f"  -- 6 checks NOT RUN: no movie at {movie_path}.")
    print("        That is the expected state of a fresh clone and of CI: the "
          "movie is third-party and")
    print("        is never committed (see LEGAL.md and tools/guard_staged.sh). "
          "Everything above is")
    print("        checked against synthetic movies instead, so the parser is "
          "pinned without it.")

print(f"fm2: {ok} checks")
print("all checks passed")