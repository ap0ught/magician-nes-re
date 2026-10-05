"""The `.DAT` scene format, proved against streams this file packs itself.

    python3 src/testing/test_scene_format.py

`tools/datcodec.py` decompresses the `.DAT` scene files `unrun` fills into the
nametable at boot, and the transcription of that routine was wrong twice, both
times in the same direction -- at a title screen made of solid brick, which an
earlier session read as "the source's title data is missing". It was not
missing; the transcription was wrong:

  1. **The input pointer advances by `y + 1`, not `y`.** The epilogue is
     `sec / tya / adc $13`. `sec` is not clearing a borrow, it is *supplying* the
     +1 that `adc` adds. A transcription that reads `t0 += y` lands on the last
     byte of the record just consumed and re-reads it forever.
  2. **The inner loop branches back to the `sta $2007`, not to the `lda (t0),y` /
     `cmp t4` pair**, so the token test happens once per record rather than once
     per byte. A run whose value byte happens to equal the token is still just a
     value.

`tools/unrun.py` is a shim over `datcodec.decode`, so this file also covers it.

This file does NOT re-run `datcodec.py selftest`. That is 20 checks against
Eurocom's own three shipped files, and `test_datcodec.py` invokes it and fails
the suite if it fails -- a regression there must be caught by the suite, not only
by remembering to run it.

What is tested here is the format against streams packed by hand, so the answer
is known by construction rather than by comparison with a file. Twelve checks:

   1-4.  a hand-packed stream decodes to exactly the scene that was packed, and
          re-encodes to exactly the bytes that were packed
   5-6.  the run length is `(count & $7F) + 4` -- the +1 that `sec` supplies is
          real, and 3 is wrong. Both are computed and the difference is named.
   7-8.  the token test happens ONCE per record: a run whose value byte equals
          the token is still a value, and `decode_source` walks the same record
          boundaries as `decode`
   9-11. the header is a statement of length: a truncated stream, a stream
          longer than its header says, and a counter that starts at $0000 all
          RAISE
  12.   `unrun.unrun` is `datcodec.decode`, and `unrun.py`'s own module docstring
          no longer tells a reader the old transcription is current

WHAT IT DOES NOT CLAIM

  * **No `.DAT` file is read.** The three shipped ones live in
    `vendor/Magician-NES/DAT/`, which is read-only and pinned; this suite leaves
    that to `test_datcodec.py`, which checks them without modifying anything.
  * **`decode_source` is a transcription, not the truth.** It is deliberately
    kept *incorrect* relative to `decode` in exactly the way the shipped code is:
    run length +3, so it overruns the packed data. Its value is that it is
    independent of `decode`, so agreement on the record stream is evidence
    rather than a tautology. Check 8 is what makes that comparison meaningful.
  * **Nothing here says the rebuilt ROM's nametable is right.** That is a
    framebuffer measurement and needs the emulator.

WHAT IT NEEDS: python3, and the committed `pds-text/` decode of the vendor
submodule only if `import datcodec` needs it -- it does not. Under a second.

Run:  python3 src/testing/test_scene_format.py
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

os.chdir(ROOT)

import datcodec  # noqa: E402  (tools/datcodec.py)

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


def raises(name, fn, must_say=()):
    global ok
    try:
        got = fn()
    except datcodec.CodecError as e:
        missing = [m for m in must_say if m not in str(e)]
        if missing:
            raise AssertionError(
                f"{name}: raised, but not with {missing!r}.\n         It said: {e}"
            ) from None
        ok += 1
        print(f"  ok {name}")
        return
    raise AssertionError(
        f"{name}: returned {got!r} instead of raising. A decoder that silently "
        f"stops early returns a short scene, and a short scene is a nametable "
        f"that is right for the first rows and brick for the rest -- which is "
        f"exactly how the old `t0 += y` bug was mistaken for missing data.")


def pack(scene, token, runs):
    """A `.DAT` stream, assembled here rather than by `encode`.

    `runs` is a list of (kind, ...) describing each record in order, so a test
    can pack a stream whose *declared* length and whose records disagree -- which
    `encode` will not do, and which is what checks 9-11 need.

      ('lit', value)            one literal byte
      ('run', count, value)     token, count, value
    """
    declared = len(scene)
    start = 0x10000 - declared
    out = bytearray((start & 0xFF, start >> 8, token))
    for rec in runs:
        if rec[0] == "lit":
            out.append(rec[1])
        else:
            _kind, count, value = rec
            out += bytes((token, count, value))
    return bytes(out)


# The scene, and the record stream, written out by hand.
#
# `token = $E0` and $E0 never appears literally below, because a literal equal to
# the token is unrepresentable -- the format has no escape for it. That is stated
# in `encode`'s docstring and asserted by check 4's sibling in test_datcodec.py.
#
# The records are: a 4-byte flat run of $08, a 4-byte incrementing run from $40,
# then six literals. `sec/tya/adc` is what supplies the +1, so the flat run is
# packed as count $00 -> (0 & $7F) + 4 = 4. At +3 it would be 3, and the scene
# would be one byte shorter with everything after it shifted -- which is what the
# brick screen was.
TOKEN = 0xE0
LITERALS = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66]
RUNS = [("run", 0x00, 0x08),                    # 4 x $08
        ("run", 0x80, 0x40),                    # 4 x $40 $41 $42 $43
        ("lit", LITERALS[0]), ("lit", LITERALS[1]), ("lit", LITERALS[2]),
        ("lit", LITERALS[3]), ("lit", LITERALS[4]), ("lit", LITERALS[5])]
SCENE = bytes([0x08] * 4 + [0x40, 0x41, 0x42, 0x43] + LITERALS)
STREAM = pack(SCENE, TOKEN, RUNS)

# =====================================================================================
# 1-2. The header is a length statement, and decode() hits it exactly.
# =====================================================================================
lo, hi, token, declared = datcodec.header(STREAM)
check("the declared length is 0x10000 minus the counter",
      declared == len(SCENE) == 14, f"declared {declared}, scene {len(SCENE)}")
check("the counter is stored little-endian as two bytes",
      (lo, hi) == (0x10000 - len(SCENE) & 0xFF, (0x10000 - len(SCENE)) >> 8),
      f"{lo:#04x} {hi:#04x}")
check("the token comes from header byte 2 and is $E0",
      token == TOKEN == STREAM[2], f"{token:#04x}")
check("decode() of the hand-packed stream is exactly the scene that was packed",
      datcodec.decode(STREAM) == SCENE,
      f"{datcodec.decode(STREAM).hex(' ')}\n         wanted  {SCENE.hex(' ')}")
recs = datcodec.records(STREAM)
check("the records account for every input byte after the 3-byte header",
      sum(3 if r[1] == "run" else 1 for r in recs) == len(STREAM) - 3,
      f"records cover {sum(3 if r[1] == 'run' else 1 for r in recs)} of "
      f"{len(STREAM) - 3}")

# 3. ...and re-encodes to the same bytes. A codec that round-trips its own output
# proves nothing; one that reproduces a stream packed by a different routine does.
check("encode(decode(stream)) is byte-identical to the packed stream",
      datcodec.encode(datcodec.decode(STREAM), TOKEN) == STREAM,
      f"{datcodec.encode(datcodec.decode(STREAM), TOKEN).hex(' ')}\n"
      f"         wanted  {STREAM.hex(' ')}")

# =====================================================================================
# 4-6. The run length. +4 is the format; the assembled code says +3, and the
#      difference is one byte per run.
# =====================================================================================
check("there are exactly 2 runs and 6 literals in the record stream",
      sum(1 for r in recs if r[1] == "run") == 2
      and sum(1 for r in recs if r[1] == "lit") == 6,
      f"{[(r[1], r[2]) for r in recs]}")
check("a count of $00 is a run of 4, i.e. (count & $7F) + 4",
      recs[0][2] == 4 == datcodec.MIN_RUN, f"run length {recs[0][2]}")
check("count bit 7 is 'repeat, +1 each', not part of the length",
      recs[1][2] == 4 and recs[1][4] is True and recs[0][4] is False,
      f"{[(r[2], r[4]) for r in recs if r[1] == 'run']}")

# The +3 reading, computed rather than asserted -- and this is the shape of the
# defect, not just its size. With `and #$7F / adc #$03 / tax` every run is one
# byte shorter than it was packed to be, so after the same records the decoder
# has emitted fewer bytes than the header declares AND has run off the end of the
# packed data looking for the rest. The header is the cruncher's own statement of
# how long its stream is, so a reading that cannot satisfy it is the wrong
# reading; the +3 shipped code is recorded in PROVENANCE.md as a defect rather
# than worked around silently.
short_by_3, i, pos, ran_out = bytearray(), 3, 0, False
while pos < declared:
    if i >= len(STREAM):
        ran_out = True
        break
    b = STREAM[i]
    if b == TOKEN:
        if i + 2 >= len(STREAM):
            ran_out = True
            break
        c = STREAM[i + 1]
        run = (c & 0x7F) + 3          # the assembled `and #$7F / adc #$03 / tax`
        step = 1 if c & 0x80 else 0
        short_by_3 += bytes((STREAM[i + 2] + k * step) & 0xFF for k in range(run))
        i += 3
    else:
        short_by_3.append(b)
        i += 1
        run = 1
    pos += run
check("the +3 reading exhausts the packed data and has still not reached the "
      "declared length -- the defect, demonstrated",
      ran_out and len(short_by_3) == len(SCENE) - 2 and pos < declared,
      f"+4 emits {len(SCENE)} of {declared}; +3 emits {len(short_by_3)}, ran out of "
      f"input at offset {i} of {len(STREAM)}, and stopped at {pos} of {declared}")
check("the shortfall is exactly one byte per run record",
      len(SCENE) - len(short_by_3) == sum(1 for r in recs if r[1] == "run"),
      f"{len(SCENE) - len(short_by_3)} short, "
      f"{sum(1 for r in recs if r[1] == 'run')} runs")
check("the first record is 4 bytes, not 3, so the scene starts at the right place",
      datcodec.decode(STREAM)[:4] == b"\x08\x08\x08\x08"
      and datcodec.decode(STREAM)[4] == 0x40,
      f"{datcodec.decode(STREAM)[:5].hex(' ')}")

# 6. And the shipped transcription, which is +3 by design, agrees about everything
#    EXCEPT that it runs long. This is check 8's other half.
trace3 = []
datcodec.decode_source(STREAM, pad=bytes(64), trace=trace3)
check("decode_source walks the same record boundaries as decode()",
      len(trace3) >= len(recs)
      and all(a[0] == b[0] and a[1] == b[1] and a[3] == b[3] and a[4] == b[4]
              for a, b in zip(trace3, recs)),
      f"decode: {[r[:2] for r in recs]}\n         source: {[r[:2] for r in trace3]}")
check("decode_source overruns, because its run length is one shorter per record",
      len(trace3) > len(recs),
      f"decode found {len(recs)} records, decode_source found {len(trace3)}. If it "
      f"did not overrun, the +3/+4 finding has been re-measured and needs "
      f"revisiting.")

# =====================================================================================
# 7-8. The token test happens once per RECORD, not once per byte.
# =====================================================================================
# A run whose VALUE byte is the token. The token test runs once per record, at a
# record boundary, so `$E0` sitting in the value column is just a value. A decoder
# that tested every byte would read the run's own count as the start of a new
# record and produce a completely different scene with no error anywhere.
#
# (A *literal* equal to the token is a different matter and is unrepresentable --
# the format has no escape for it. That is `encode`'s error, asserted in
# test_datcodec.py.)
tok_value = TOKEN
tricky_scene = bytes([0x77] * 4 + [tok_value] * 4 + [0x88] * 4)
tricky = pack(tricky_scene, TOKEN,
              [("run", 0x00, 0x77),        # 4 x $77
               ("run", 0x00, tok_value),  # 4 x $E0 -- the token, as a VALUE
               ("run", 0x00, 0x88)])      # 4 x $88
check("a run whose value byte equals the token decodes as a run of that value",
      datcodec.decode(tricky) == tricky_scene,
      f"{datcodec.decode(tricky).hex(' ')} wanted {tricky_scene.hex(' ')}")
check("...and the record stream shows THREE records, not a dozen bytes",
      [r[1] for r in datcodec.records(tricky)] == ["run", "run", "run"]
      and [r[3] for r in datcodec.records(tricky)] == [0x77, tok_value, 0x88],
      f"{[(r[1], r[3]) for r in datcodec.records(tricky)]}")
t3 = []
datcodec.decode_source(tricky, pad=bytes(64), trace=t3)
check("decode_source agrees about those record boundaries too",
      len(t3) >= len(datcodec.records(tricky))
      and all(a[0] == b[0] and a[3] == b[3] for a, b in zip(t3, datcodec.records(tricky))),
      f"{[(r[0], r[3]) for r in t3]} vs "
      f"{[(r[0], r[3]) for r in datcodec.records(tricky)]}")

# =====================================================================================
# 9-11. The header is a statement, and a stream that contradicts it is an error.
# =====================================================================================
raises("a stream that runs out before its declared length RAISES",
       lambda: datcodec.decode(STREAM[:-3]),
       must_say=("input exhausted",))
raises("a stream with bytes past its declared length RAISES",
       lambda: datcodec.decode(STREAM + b"\x99"),
       must_say=("trailing byte",))
raises("a counter that starts at $0000 RAISES, naming what that means",
       lambda: datcodec.header(bytes((0, 0, TOKEN, 0x08, 0x00, 0x77))),
       must_say=("exits before emitting",))
raises("a file too short to hold a header and one record RAISES",
       lambda: datcodec.header(bytes((0, 0, TOKEN))),
       must_say=("3-byte header",))
raises("a run record cut off mid-way RAISES",
       lambda: datcodec.decode(bytes((0x00, 0xFC, TOKEN)) + STREAM[:4]),
       must_say=("truncated run record",))

# =====================================================================================
# 12. tools/unrun.py is a shim, and its docstring must not still describe the old
#     transcription as if it were current -- it once did, and a reader who trusted
#     it would re-derive the brick screen.
# =====================================================================================
sys.path.insert(0, str(ROOT / "tools"))
import unrun  # noqa: E402

check("unrun.unrun IS datcodec.decode, not a second transcription",
      unrun.unrun(STREAM) == datcodec.decode(STREAM) == SCENE)
udoc = (ROOT / "tools" / "unrun.py").read_text(encoding="utf-8")
check("unrun.py's docstring still records BOTH of its transcription errors",
      "t0 += y" in udoc and "dex / bne" in udoc,
      "a docstring that has lost the error it documents will let someone "
      "re-transcribe it the same way")

print(f"scene format: {ok} checks")
print("all checks passed")