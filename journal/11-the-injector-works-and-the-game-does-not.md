# 11 — The injector works. The game does not follow the movie.

**Dated 2026-10-05. Branch `fix/boot-from-source`.**

This is the entry the previous ten were working towards, and it has two halves
with opposite answers.

## The question

`FatRatKnight`'s "NES Magician in 12:12.18" is a verified 44 003-frame FCEUX
`.fm2`, submission 2237S. Its `romChecksum` is
`base64:Vg0xzpx+9fkF4mNR30juYg==`, which decodes to
`md5(PRG+CHR) = 560d31ce9c7ef5f905e26351df48ee62`.

Meased here, not taken from the brief:

| dump | md5(PRG+CHR) |
|---|---|
| **release** `Magician (USA).nes` | `560d31ce9c7ef5f905e26351df48ee62` |
| beta1 `Magician (USA) (Beta 1) (1990-03-02).nes` | `e5c9eed5102e561de76619cfe2135b0a` |
| beta2 | `2a65de1db0eef2227552a8be036e68c6` |
| beta3 = `beta` | `82b9b718d623fc4a75337f9447f18d57` |
| beta4 | `8226913ec080390e3a62f191f1e878da` |

So the movie was recorded on **the release**, not on Beta 1 and not on this
project's rebuild. `Magician (U) [!]` is a GoodTools header-normalised name for
that same file, and the checksum confirms it. The brief's phrasing — "the MD5 of
*our release's* PRG+CHR" — is right about the file and easy to misread as being
about the rebuild. It is not.

Two more measured facts about the file, both different from the brief:

* **108** subtitle anchors, not 120.
* frame 0 is `|0|........|||` — nothing pressed. The brief's example line
  `|0|.L.....A|||` is not in this movie, and read as documented
  (`A B Select Start Up Down Left Right`) it would mean B+Right; in the file's
  real order, `R L D U T S B A`, `.L.....A` would mean Left+A. Neither is what
  frame 0 says. `tools/fm2.py`'s derivation, taken from the file's own
  per-position alphabet, is the one that survives.

## Half one — the injector: proven

Replayed **the release** — the movie's own cartridge, checksum-verified by
`tools/fm2.py` — for 5000 frames:

```
cartridge saw a press on 3935 of the 3935 frames where the movie pressed something
exact cartridge/movie pad agreement 4992 of 5000 frames (99.84%)
both idle (no press asked, none seen) 1057
$4016 readback      $F2 x5000     <- constant, and it says so: open bus
jt8 non-zero but decoded $00: 0 frames (must be 0)
```

3935 of 3935 is the number that matters. It is not `joypad.set` returning
without error — it is the cartridge's *own* decoded joypad bytes at `$002E-$0035`,
which is what `jk0` produced. The eight frames where the game held a button the
movie released are the game walking a menu, and `replay_check.py` labels them as
such.

**The injection convention needs no offset.** Measured over a ±1 frame window,
movie frame +0 wins at 99.84% against +1 at 86.62%, −1 at 86.68%, +2 at 92.82%,
−2 at 92.92%. A one- or two-frame error in the injector's alignment would show up
here as a shift, and there is none.

**Two independent 5000-frame runs are byte-identical.** All 5000 rows, all ten
columns, including both RAM hashes:

```
frames whose row differs at all: 0
```

## Half two — the game: does not follow the movie

This is the half that matters, and the answer is no.

`tools/replay_check.py` flags, at the subtitle `+150 max mana`:

```
** a cell changed by exactly +150: $004D, $084D, $104D, $184D **
```

`$004D` is **`mapind`**, the current map index (`zp mapind,1 ; current map index
1..A` in the `x0.pds` `zp` block, confirmed against `asm/out/mag.sym`). It went
from 50 to 200. That is a coincidence: the scan looks for *any* address whose
delta is exactly +150 across a window in which 1732 addresses changed, so several
of them land on 150 by luck. A cell's name is not a witness, and a scan that
finds the number the annotation was looking for has found nothing about mana.

So the addresses came from the source instead — `tools/mana_probe.py`, reading
`asm/out/mag.sym` — and the values came from the address:

```
 frame   manacur   manatop   mclock   wealth   food   water   phase   mapind
     50         0      1500        0        0     23       0     128       50
    450         0      1500        0        0     38       0     128       50
    935         0      1500      255        0     56       0     128       50
   1511         0      1500      255        0     52       0     128       50
   2083         0      1500      255        0     52       0     128       50
   2832         0      1500      255        0     39       0     128      200
   3120         0      1490        5        0     16       0     128      200
   3498         0      1490        5        0     58       0     128      200
   3806         0      1490        5        0      6       0     128       19
   4250         0      1490        5        0     58       0     128       19
   4611         0      1490        0        0     57       0     128       19
   4870         0      1490        0        0     38       0     128       19
```

Three facts, none of which is an interpretation:

1. **`manacur` (`$47`) is 0 at every one of the 12 anchors.** No spell was cast
   in 81 seconds of NTSC, while the movie's own annotations talk about learning
   spells and using FLEET FOOT.
2. **`phase` (`$5F`) is `$80` at every one of the 12 anchors.** `phase` indexes
   the `gvl`/`gvh` dispatch table at `x5.pds:227-231`
   (`ldx phase / lda gvl,x / sta t0 / lda gvh,x / sta t1 / jsr jmpt0`), so it is
   the main loop's state word. It not moving means the main loop is not
   advancing through its phases.
3. **`mapind` is 50 and then 200 and then 19.** The source says it is "current
   map index **1..A**". 50 is not in 1..A, so at frame 50 the game has not
   initialised into a map at all. 200 and 19 are not in 1..A either.

The screen does change — the nametable at frame 30, 2832 and 4611 is three
different pictures (a filled field, a bordered panel-shaped box, and a nearly
full grid) — so this is not a frozen picture being mistaken for a running game.
Something is drawing and the main loop is not progressing.

## Where it leaves off

The injector is trustworthy, so **every downstream claim about input is now
made on a proven instrument.** That is what was needed and it is done.

What is *not* answered is why the release does not follow a movie whose
checksum it matches. The obvious suspects, none of them excluded yet:

* **Emulator timing.** The movie is FCEUX v3. `quickerNES` in BizHawk 2.11.1
  reads the controller at a different point in the frame, so the button lands on
  the right *frame number* and the wrong *cycle*. That would produce exactly
  this: input delivered, alignment at +0, game not following. It is testable by
  injecting on alternate frames and seeing whether any offset ever makes the
  annotations land — and the ±1/±2 sweep above says a whole-frame offset does
  not.
* **A frame-count convention.** FCEUX's `.fm2` frame 0 is the frame *after* the
  reset; `emu.framecount()` at load is 0 here. A constant shift would show up as
  a nonzero best offset, and it is zero.
* **The game is not where the movie starts.** `mapind` = 50 says it never
  initialised a map. If the movie begins past the title and the replay never got
  there, everything downstream follows from that — and the anchor frames 2832
  and 4611 were "reached" only in the sense that the run reached those frame
  numbers, not that anything the subtitle describes happened.

The third is the cheapest to test and is not yet tested: take a screenshot at
frames 0..200 and read the title screen's state, rather than inferring it from
`mapind`. **That is the next step.**

## Corrections to earlier notes

* Anything that said the movie has 120 subtitle anchors is wrong; it has 108.
* The brief's example frame line `|0|.L.....A|||` is not in this movie, and its
  button mapping was the documented-but-wrong one.
* `romChecksum` identifies the **release**. A note that treats it as "our
  rebuild's" checksum is wrong.

## Reproducing all of it

The `.fm2` is third-party and is never committed; it lives at
`/tmp/opencode/tas/Magician (U)-FatRatKnight GoodEnd+subtitle.fm2`.

No path is written down, because a literal in a document is how this project
came to measure the wrong cartridge for five sessions. `tools/cartref.py <name>`
prints a registered dump's path and the default is still the build's target.

```sh
MOVIE="/tmp/opencode/tas/Magician (U)-FatRatKnight GoodEnd+subtitle.fm2"
RELEASE="$(python3 tools/cartref.py release)"     # the movie's own cartridge
BUILD="$(python3 tools/cartref.py)"               # Beta 1, the build's target

# 1. the movie verifies against the release, and says which one it is
python3 tools/fm2.py "$MOVIE" --rom "$RELEASE" --expect-frames 44003

# 2. the injector, twice, then the two runs compared frame for frame
OUTDIR=/tmp/opencode/replay-rel5000   tools/bizhawk/replay.sh "$RELEASE" 5000
OUTDIR=/tmp/opencode/replay-rel5000b  tools/bizhawk/replay.sh "$RELEASE" 5000
python3 tools/replay_check.py /tmp/opencode/replay-rel5000 /tmp/opencode/replay-rel5000b

# 3. the game's own mana and phase cells at every anchor reached
python3 tools/mana_probe.py /tmp/opencode/replay-rel5000

# 4. the control: the same 5000 frames with no input at all. OFFSET is negative
#    enough that every PAD index is nil, and replay.lua substitutes the released
#    state -- the same code path, no second movie.
OUTDIR=/tmp/opencode/replay-noinput OFFSET=-44003 \
        tools/bizhawk/replay.sh "$RELEASE" 5000
```

Step 4 is the one that says what the input is *doing*: against it, `ramh1` and
`ramh2` differ from frame 21 — the first frame the movie presses anything — and
stay different. The game is responding to the input and still not following the
movie, so this is a desync and not a dead injector.