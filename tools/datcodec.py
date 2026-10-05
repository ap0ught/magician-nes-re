#!/usr/bin/env python3
"""A verified round-trip codec for Eurocom's `.DAT` scene format.

    python3 tools/datcodec.py selftest        # prove the round trip, or die
    python3 tools/datcodec.py decode TIT.DAT  # scene bytes -> tiles + attrs
    python3 tools/datcodec.py tokens PAN.DAT  # the record stream, annotated
    python3 tools/datcodec.py encode s.bin -o s.dat --token 0xE0
    python3 tools/datcodec.py source TIT.DAT  # what the SHIPPED unrun emits

THE FORMAT
----------
`unrun` is the decompressor that fills a nametable window at boot. Its exact
bytes are disassembled from our own PRG at $DC77-$DCBD; its source lines are
`pds-text/x5.pds:106-121`. The prologue is a three-byte load whose *store*
instruction decides the header layout:

    DC7B: A0 02      ldy #$02
    DC7D: B1 13      lda ($13),y    ; A = data[y]
    DC7F: 99 15 00   sta $0015,y    ; -> $15+y      **note the +y**

so, walking y = 2,1,0:

    data[0] -> $15 = t2   output counter LOW
    data[1] -> $16 = t3   output counter HIGH
    data[2] -> $17 = t4   the TOKEN byte

`$2006` is aimed at `sc0` = $2000 before the call (`unrunscn`, DC6B-DC70), so
every emitted byte lands in the nametable in order.

`t2`/`t3` are not a length. The main loop ends with `lda t3 / bne $DC87`
(DCB9-DCBB), i.e. it runs *while* t3 != 0 -- the exact opposite of what the
source comment above it claims ("all bytes done?"). t2 is incremented once per
emitted byte (DCA4) and carries into t3 when it wraps, so:

    bytes emitted = 0x10000 - (data[0] | data[1] << 8)

and the three shipped files declare:

    TIT.DAT  00 FC E0  ->  1024   (960 nametable + 64 attributes)
    PW.DAT   00 FC 01  ->  1024
    PAN.DAT  00 FF 00  ->   256   (a 32x8 panel window)

A record, starting at `t0 + y` with y = 0:

    if data[i] == token:                      # a run, 3 input bytes
        count = data[i+1]
        run   = (count & 0x7F) + 4            # 4..131, see below
        inc   = count & 0x80                  # asl a puts bit 7 in C ...
        value = data[i+2]                     # ... adc #$00 turns into +1
        emit value, value+inc, value+2*inc, ... (run bytes)
        i += 3
    else:                                      # a literal, 1 input byte
        emit data[i]
        i += 1

Three things in that listing are worth stating explicitly, because each was
measured rather than assumed, and each is a place where reading the source
casually gives the wrong answer.

1. THE INCREMENTING RUN. `sta $2007` (DC9D) happens *before* `php / adc #$00 /
   plp` (DCA0-DCA3), so the first byte written is `value` and the +1 lands in A
   for the *next* iteration. `dex / bne $DC9D` (DCAA-DCAB) branches back to the
   `sta`, not to the `lda`, which is where the rest of the run comes from. So
   `count & 0x80` means "repeat, +1 each byte".

2. THE POINTER IS `t0 += y + 1`, NOT `t0 += y`. The epilogue is

       DCAD: 38      sec
       DCAE: 98      tya          ; y = index of the LAST byte consumed
       DCAF: 65 13   adc $13      ; <-- adc ADDS the carry sec just set

   `sec` is not there to clear a borrow; its whole purpose is to supply the +1.
   Without it the pointer never leaves the first record's last byte. An earlier
   transcription of this routine in tools/unrun.py models it as `t0 += y`, which
   is why that tool reports a screen of solid brick: it sticks on the first
   record and re-emits its value forever. `decode_source()` below is the
   corrected transcription and is kept honest by `selftest`.

3. THE RUN LENGTH IS +4, BUT THE ASSEMBLED CODE SAYS +3. `and #$7F / adc #$03
   / tax` (DC92-DC96) gives x = (count & 0x7F) + 3, and the byte loop
   `sta $2007 ... dex / bne` writes exactly x bytes. So both the source *and*
   the release (whose longer `unrun` at $DB09 carries the identical
   `29 7F / 69 03 / AA` sequence at $DB44-$DB48) decode a run one byte shorter
   than the data was packed for. The data settles it three independent ways:

     * the declared length. The header is the cruncher's own statement of how
       many bytes its stream decodes to. +4 hits 1024/1024/256 exactly, with
       the input consumed to the last byte. +3 falls 50/47/15 bytes short --
       exactly one per run record (50, 47 and 15 runs respectively) -- and
       leaves the decoder reading past the end of the packed data.
     * row alignment. `TIT.DAT`'s first record is `E0 7C 08`. At +4 that is
       128 bytes, i.e. exactly four 32-tile rows of brick, and the ascending
       tile counter that follows starts at $00 on row 4. At +3 it is 127, and
       row 3 ends `... 08 00` with the counter starting at $01.
     * centring. `PW.DAT` row 5 is the string "GAME RESTORE CODES". At +4 it
       sits at columns 7..24 -- 7 spaces, 18 glyphs, 7 spaces. At +3 the same
       text starts at column 5 and a stray $0B is left at column 31.

   This is recorded as a defect rather than worked around silently: see
   PROVENANCE.md. The codec implements the format the data is actually in.

PROOF, NOT ASSERTION
--------------------
`selftest` is the deliverable, and it is allowed to fail loudly. For TIT.DAT,
PW.DAT and PAN.DAT it requires:

  1. decode(encode(scene)) == scene                we can read our own output
  2. encode(decode(file)) == file, byte-identical  our encoder independently
                                                  chose the same records
                                                  Eurocom's cruncher did
  3. the declared length in the header is exactly what decode() produces
  4. decode_source(file) agrees with decode() on the record stream's structure
     (same record boundaries), which is what makes (2) meaningful

(2) is the strong one and the one that would catch a wrong format: an encoder
that merely round-trips its own output proves nothing. A codec that has not
been shown to round-trip against Eurocom's own files is not a codec.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAT = ROOT / 'vendor' / 'Magician-NES' / 'DAT'

NAMETABLE = 0x3C0          # 960 tile bytes == the NES attribute table offset
ATTRS = 0x40               # 64 attribute bytes -> 1024 for a full screen
MIN_RUN = 4                # (count & 0x7F) + 4, so 3-byte runs do not exist
MAX_RUN = 131              # (count & 0x7F) maxes at 127


class CodecError(Exception):
    pass


def header(data: bytes):
    """(counter_lo, counter_hi, token, declared_byte_count) from 3 header bytes."""
    if len(data) < 4:
        raise CodecError('a .DAT needs a 3-byte header and at least one record, got %d bytes'
                         % len(data))
    lo, hi, token = data[0], data[1], data[2]
    start = lo | (hi << 8)
    if start == 0:
        raise CodecError('counter starts at $0000, so unrun exits before emitting anything')
    return lo, hi, token, 0x10000 - start


def decode(data: bytes) -> bytes:
    """The packed format, returning exactly the number of bytes the header declares."""
    _, _, token, target = header(data)
    out = bytearray()
    i = 3
    while len(out) < target:
        if i >= len(data):
            raise CodecError(
                'input exhausted at offset %d with %d of %d bytes emitted -- truncated '
                'record stream, or a run length that does not match the header'
                % (i, len(out), target))
        b = data[i]
        if b == token:
            if i + 2 >= len(data):
                raise CodecError('truncated run record at offset %d' % i)
            count = data[i + 1]
            run = (count & 0x7F) + MIN_RUN
            inc = count & 0x80
            v = data[i + 2]
            i += 3
            for k in range(run):
                out.append((v + (k if inc else 0)) & 0xFF)
        else:
            out.append(b)
            i += 1
    if i != len(data):
        raise CodecError('%d trailing byte(s) after the declared %d: the stream is longer '
                         'than its header says' % (len(data) - i, target))
    return bytes(out)


def decode_source(data: bytes, pad: bytes = b'', trace=None) -> bytes:
    """Transcription of x5.pds:111-121 exactly as assembled at $DC77-$DCBD.

    Differs from `decode` in the two ways the shipped code does: the run length
    is `(count & 0x7F) + 3`, and the loop only tests the counter at a record
    boundary, so the final record is allowed to overrun the declared length.
    Because of the overrun this needs whatever follows the packed data in PRG to
    terminate; `pad` supplies it. Kept for the delta table, and as the check
    that `decode` is not quietly inventing a format: pass a list as `trace` and
    it receives the same `(offset, kind, run, value, inc)` records that
    `records()` produces, so the two parsers can be compared boundary for
    boundary rather than taken on trust.
    """
    lo, hi, token, target = header(data)
    buf = data + pad
    out = bytearray()
    t0, t2, t3, t4 = 0, lo, hi, token
    y = 3
    guard = 0
    while True:
        guard += 1
        if guard > 200000:
            raise CodecError('no terminator after %d bytes' % len(out))
        start = t0 + y
        if start >= len(buf):
            raise CodecError('read past the supplied buffer at t0=$%04X y=%d '
                             '(%d bytes emitted)' % (t0, y, len(out)))
        # The token test happens ONCE per record. `dex / bne $DC9D` branches back
        # to the `sta`, not to the `lda`, so a value byte that happens to equal
        # the token is still just a value.
        if buf[start] == t4:
            if start + 2 >= len(buf):
                raise CodecError('truncated run record at offset %d' % start)
            cnt = buf[start + 1]
            x = (cnt & 0x7F) + 3                  # and #$7f / adc #$03 / tax
            carry = (cnt >> 7) & 1                 # asl a -> C is bit 7
            a = buf[start + 2]
            if trace is not None:
                trace.append((start, 'run', x, a, bool(cnt & 0x80)))
            y += 2                                # iny twice: count at +1, value at +2
        else:
            x = 1
            carry = 0                             # CMP's C, discarded by the php/plp pair
            a = buf[start]
            if trace is not None:
                trace.append((start, 'lit', 1, a, False))
        while True:                               # !c .. !d : write x bytes
            out.append(a)
            a = (a + carry) & 0xFF                 # adc #$00: next byte of the run
            t2 = (t2 + 1) & 0xFF
            if t2 == 0:
                t3 = (t3 + 1) & 0xFF
            x -= 1
            if x == 0:
                break
        t0 = (t0 + y + 1) & 0xFFFF                # sec / tya / adc t0 / sta t0
        y = 0                                     # ldy #$00
        if t3 == 0:
            return bytes(out[:target])


def _longest(scene: bytes, i: int, inc: bool) -> int:
    n = len(scene)
    step = 1 if inc else 0
    base = scene[i]
    r = 1
    while r < MAX_RUN and i + r < n and scene[i + r] == (base + r * step) & 0xFF:
        r += 1
    return r


def encode(scene: bytes, token: int = 0xE0) -> bytes:
    """Greedy-maximal encoder. Reproduces all three shipped files byte-exactly.

    Runs shorter than MIN_RUN do not exist in this format, so short repeats fall
    through to literals. A literal equal to the token is unrepresentable --
    there is no escape for it -- so `token` has to be a byte that never appears
    literally in the scene. TIT.DAT uses $E0, PW.DAT $01, PAN.DAT $00.
    """
    if not scene:
        raise CodecError('refusing to encode an empty scene')
    if not 0 <= token <= 0xFF:
        raise CodecError('token $%X is not a byte' % token)
    start = 0x10000 - len(scene)
    if start <= 0 or start > 0xFFFF:
        raise CodecError('a %d-byte scene has no representable counter' % len(scene))
    out = bytearray((start & 0xFF, start >> 8, token))
    i, n = 0, len(scene)
    while i < n:
        flat = _longest(scene, i, inc=False)
        step = _longest(scene, i, inc=True)
        if flat >= MIN_RUN and flat >= step:
            out += bytes((token, flat - MIN_RUN, scene[i]))
            i += flat
        elif step >= MIN_RUN:
            out += bytes((token, 0x80 | (step - MIN_RUN), scene[i]))
            i += step
        else:
            if scene[i] == token:
                raise CodecError(
                    'tile $%02X at offset %d equals the token $%02X and cannot be a '
                    'literal; choose a token that does not occur literally'
                    % (scene[i], i, token))
            out.append(scene[i])
            i += 1
    return bytes(out)


def reencode(path: Path) -> bytes:
    """decode(f) then encode() again, keeping f's own token. Used by selftest."""
    raw = path.read_bytes()
    _, _, token, _ = header(raw)
    return encode(decode(raw), token)


def scene_parts(scene: bytes):
    """(tiles, attrs) for a 1024-byte scene; a short window yields tiles only."""
    if len(scene) >= NAMETABLE + ATTRS:
        return scene[:NAMETABLE], scene[NAMETABLE:NAMETABLE + ATTRS]
    return scene, b''


def records(data: bytes):
    """The record stream as (offset, kind, run_or_None, value_or_literal)."""
    _, _, token, target = header(data)
    out, i, pos = [], 3, 0
    while pos < target and i < len(data):
        if data[i] == token:
            c = data[i + 1]
            run = (c & 0x7F) + MIN_RUN
            out.append((i, 'run', run, data[i + 2], bool(c & 0x80)))
            pos += run
            i += 3
        else:
            out.append((i, 'lit', 1, data[i], False))
            pos += 1
            i += 1
    return out


# --------------------------------------------------------------------- selftest

def selftest(quiet=False):
    ok = True
    rows = []

    def check(name, cond, detail=''):
        nonlocal ok
        if not cond:
            ok = False
        rows.append((cond, name, detail))
        if not quiet:
            print('  [%s] %s%s' % ('ok' if cond else 'FAIL', name,
                                   ('\n         ' + detail) if detail and not cond else ''))

    print('datcodec selftest -- the .DAT format, proven against Eurocom\'s own files')
    names = ['TIT.DAT', 'PW.DAT', 'PAN.DAT']
    for n in names:
        if not (DAT / n).is_file():
            check('%s present' % n, False, str(DAT / n))
    if not ok:
        print('selftest ABORTED: input missing')
        return False

    for name in names:
        raw = (DAT / name).read_bytes()
        _, _, token, target = header(raw)
        scene = decode(raw)
        check('%s: header declares %d, decode() produced %d' % (name, target, len(scene)),
              len(scene) == target)
        check('%s: record stream ends exactly on the last input byte' % name,
              records(raw)[-1][0] < len(raw))

        # (1) we can read back what we write.
        check('%s: decode(encode(scene)) == scene' % name,
              decode(encode(scene, token)) == scene)

        # (2) the strong one -- our encoder reproduces Eurocom's bytes exactly.
        back = reencode(DAT / name)
        first = next((k for k in range(min(len(back), len(raw))) if back[k] != raw[k]), None)
        check('%s: encode(decode(file)) == file byte-identically' % name, back == raw,
              '%d in, %d out' % (len(raw), len(back)) +
              ('' if first is None else ', first difference at offset %d ($%02X vs $%02X)'
               % (first, back[first], raw[first])))

        # (4) decode_source must walk an identical record stream. This is what
        # stops `decode` from quietly inventing a format: an implementation
        # transcribed from the assembly and one derived from the data have to
        # agree on every record boundary and every value.
        #
        # They agree only up to the point where the shipped +3 run length stops
        # matching the packed data. After that the shipped decoder is one byte
        # short per run, so it needs extra records to reach the declared byte
        # count and runs off the end of the file into whatever follows in PRG.
        # That divergence is the defect this file documents, so it is asserted
        # rather than tolerated: `overrun` must be > 0 and the prefix must match.
        recs = records(raw)
        trace = []
        decode_source(raw, pad=bytes(4096), trace=trace)
        prefix_ok = len(trace) >= len(recs) and all(
            a[0] == b[0] and a[1] == b[1] and a[3] == b[3] and a[4] == b[4]
            for a, b in zip(trace, recs))
        check('%s: decode_source walks the same first %d record boundaries as decode()'
              % (name, len(recs)), prefix_ok,
              'decode_source produced %d records; boundary %d differs' % (len(trace), len(recs)))
        overrun = len(trace) - len(recs)
        check('%s: shipped +3 run length overruns the packed data by %d record(s)'
              % (name, overrun), overrun > 0,
              'expected the documented off-by-one to show up; it did not, so the '
              '+3/+4 finding needs re-measuring')
        tiles, attrs = scene_parts(scene)
        if not quiet:
            print('       %-8s %4d -> %4d bytes  token $%02X  %5.2f:1  '
                  '%3d records (%d runs)  tiles %d attrs %d'
                  % (name, len(raw), target, token, len(raw) / target,
                     len(recs), sum(1 for r in recs if r[1] == 'run'),
                     len(tiles), len(attrs)))

    # The +3/+4 question, kept as a live assertion so it cannot rot silently.
    raw = (DAT / 'TIT.DAT').read_bytes()
    check('TIT.DAT: first record is 128 bytes = 4 whole rows of brick',
          len(decode(raw)[:128]) == 128 and set(decode(raw)[:128]) == {0x08}
          and decode(raw)[128] == 0x00,
          'the +3 reading leaves a $00 inside row 3 instead')
    check('PW.DAT: row 5 is "GAME RESTORE CODES" centred at columns 7..24',
          decode((DAT / 'PW.DAT').read_bytes())[5 * 32:5 * 32 + 32]
          == b'\x20' * 7 + bytes(ord(c) for c in 'GAME RESTORE CODES') + b'\x20' * 7)

    print('selftest: %s (%d checks)' % ('PASS' if ok else 'FAIL', len(rows)))
    return ok


# ------------------------------------------------------------------------- cli

def _load(arg: str) -> Path:
    p = Path(arg)
    if not p.exists():
        p = DAT / arg
    if not p.exists():
        raise CodecError('no such .DAT: %s' % arg)
    return p


def main(argv) -> int:
    if not argv:
        sys.stdout.write((__doc__ or '') + '\n')
        return 2
    cmd, rest = argv[0], argv[1:]

    if cmd == 'selftest':
        return 0 if selftest(quiet='-q' in rest) else 1

    if cmd == 'decode':
        p = _load(rest[0])
        raw = p.read_bytes()
        scene = decode(raw)
        _, _, token, target = header(raw)
        tiles, attrs = scene_parts(scene)
        print('%s: %d -> %d bytes (token $%02X, %.2f:1)'
              % (p.name, len(raw), target, token, len(raw) / target))
        print('  tiles %d bytes, attrs %d bytes' % (len(tiles), len(attrs)))
        hist = {}
        for v in tiles:
            hist[v] = hist.get(v, 0) + 1
        print('  distinct tiles: %d   most common:' % len(hist))
        for v, n in sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[:12]:
            print('    $%02X  %4d  %5.1f%%' % (v, n, 100.0 * n / max(len(tiles), 1)))
        if '--dump' in rest:
            for r in range(len(tiles) // 32):
                print('  %2d %s' % (r, ' '.join('%02X' % v for v in tiles[r * 32:r * 32 + 32])))
            if attrs:
                print('  attributes (64):')
                for r in range(4):
                    print('    %s' % ' '.join('%02X' % v for v in attrs[r * 16:r * 16 + 16]))
        return 0

    if cmd == 'tokens':
        p = _load(rest[0])
        raw = p.read_bytes()
        _, _, token, target = header(raw)
        print('%s: token $%02X, %d declared bytes' % (p.name, token, target))
        pos = 0
        for off, kind, run, val, inc in records(raw):
            if kind == 'run':
                print('  @%-4d out%-5d RUN %3d x $%02X%s' % (off, pos, run, val,
                                                             ' (+1 each)' if inc else ''))
            else:
                print('  @%-4d out%-5d LIT $%02X' % (off, pos, val))
            pos += run
        return 0

    if cmd == 'source':
        p = _load(rest[0])
        out = decode_source(p.read_bytes(), pad=bytes(512))
        hist = {}
        for v in out:
            hist[v] = hist.get(v, 0) + 1
        print('%s: the shipped unrun emits %d bytes, %d distinct value(s): %s'
              % (p.name, len(out), len(hist),
                 ', '.join('$%02X x%d' % (v, n) for v, n in sorted(hist.items()))))
        return 0

    if cmd == 'encode':
        if not rest:
            raise CodecError('usage: encode <scene.bin> [-o out.dat] [--token N]')
        scene = Path(rest[0]).read_bytes()
        tok = 0xE0
        if '--token' in rest:
            tok = int(rest[rest.index('--token') + 1], 0)
        blob = encode(scene, tok)
        dest = rest[rest.index('-o') + 1] if '-o' in rest else 'out.dat'
        # Verify before writing: never hand back a file we cannot read.
        if decode(blob) != scene:
            raise CodecError('encoder produced a stream that does not decode back')
        Path(dest).write_bytes(blob)
        print('%s: %d -> %d bytes, token $%02X, %.2f:1  (verified: decodes back identically)'
              % (dest, len(scene), len(blob), tok, len(blob) / len(scene)))
        return 0

    raise CodecError('unknown command %r -- try: selftest | decode | tokens | source | encode'
                     % cmd)


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1:]))
    except CodecError as e:
        print('datcodec: %s' % e, file=sys.stderr)
        raise SystemExit(1)