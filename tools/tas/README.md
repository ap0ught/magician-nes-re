# tools/tas — run an FCEUX `.fm2` TAS and decide whether it ran correctly

    tools/tas/run.sh --movie <movie.fm2> --rom <cartridge.nes> [--outdir DIR]

Exit codes, which are the whole point of the script:

| code | meaning |
|---|---|
| `0` | **verified** — every movie frame was played, the buttons FCEUX delivered were the movie's own at a single consistent frame offset, and the machine was demonstrably alive and progressing |
| `2` | **identity** — the ROM is not the cartridge the movie was recorded on, or the movie is unreadable. Nothing was emulated. |
| `3` | **movie** — FCEUX ran and the movie did not verify |
| `4` | **harness** — the run could not be completed or judged: FCEUX missing, a Lua parse error, a timeout, a closed window |

`2`, `3` and `4` are deliberately distinct. "The TAS is broken" and "I could not
find out" are different answers, and a runner that reports them the same way is
lying to whoever is reading the result.

## Why FCEUX and not BizHawk

The movie's own header says `emuVersion 20100`: it was recorded in FCEUX 2.1.0.
This project already has a BizHawk replay path (`tools/bizhawk/replay.sh`), and
journal 11 records what it does with this movie — the injector is proven (the
cartridge's own decoded joypad bytes show 3935 of 3935 presses arriving) and the
game still does not follow it, because quickerNES reads the controller at a
different point in the frame. An input-injection harness cannot fix that. Letting
FCEUX play its own movie removes the variable entirely.

## What is actually proven, and what cannot be

An `.fm2` records **input, not state**. All 44 003 frame lines of FatRatKnight's
Magician run carry an 8-character button field and empty state columns, so there is
no per-frame checksum anywhere in the file. **No emulator can tell you from the
movie alone whether the game behaved.** FCEUX reporting no complaint is not
evidence of anything.

So the verdict is assembled from four things that *can* mean only one thing:

1. **The cartridge is the one the movie names.** `romChecksum base64:` is
   base64(MD5(PRG+CHR)) over the headerless cartridge image. Five Magician dumps
   sit in this user's collection, all mapper 4, all 128 KiB PRG, four of them
   battery-backed; Beta 3 in particular differs from the release in a handful of
   bytes and nothing else. This is checked before a single frame is emulated, and
   it is the one check that must never be optional. Beta 1 and Beta 3 are both
   rejected with exit `2`, measurably.

2. **The delivered input is the movie's input.** For every frame, the sequence
   `joypad.get(1)` reports is compared against the movie's own input sequence at
   every constant offset in −4…+4. Exactly one offset must agree, with zero
   mismatches, over all 44 003 frames. Measured on the working run: offset `-1`,
   27 327 pressed frames, **0 mismatches**. This also catches a wrong port, dropped
   frames, and a misparsed movie.

3. **Coverage.** The run must reach the movie's last frame. FCEUX's
   `movie.framecount()` cannot be used for this — it counts frames *elapsed* and
   runs past `movie.length()`, reading 44 010 for a 44 003-frame movie — so
   coverage is counted from `series.tsv` in the shell.

4. **The machine was alive and the run progressed.** RAM must change on a majority
   of frames and `curlev` must take several values. Measured: RAM differs on
   44 007 of 44 011 frames, `curlev` climbs through 33 distinct levels
   (`$10 → $20 → … → $91`), and the run reaches a complete ending screen.

### The things this deliberately does not claim

* **A uniform one-frame shift would pass.** The alignment gate *finds* the offset,
  so a delivery that is consistently shifted is reported as "aligned at −1" and
  accepted. Sequence comparison cannot distinguish a true offset from a uniformly
  wrong one. Pinned as a known limitation in `src/testing/test_tas_alignment.py`.
* **A corrupted `.fm2` would pass.** Both sides of the input comparison derive
  from the same file. The gate catches a diverging *emulator*, not a bad movie.
* **It cannot prove the game followed the route.** The cartridge never had a
  checksum to compare against. What it can show is that the right cartridge ran
  the right input in the right emulator to its last frame and produced a coherent
  ending.
* **`phase` ($5F) is not a liveness signal on this cartridge.** It sits at `$80`
  for 44 092 of 44 123 frames while the run is plainly progressing. An earlier
  version of `drive.lua` gated on it and would have failed a correct run.
* **The watched RAM cells are Beta-1 semantics applied to release bytes.**
  Addresses resolve through `src/play/ram.py`, whose symbol table is the one this
  project assembles — Beta 1. `curlev` behaves exactly as documented on the
  release; `manacur` ($47) does **not**, sitting at 0 for most of the run and
  reading 43 008 as a little-endian word near frame 12 000. So a cell that looks
  wrong is much more likely to be the wrong address than a game that stopped
  casting spells. **No verdict is gated on an individual cell**, and every cell is
  reported with its distinct-value count so it is visible which moved.

## Evidence

The run writes, into `--outdir`:

    summary.txt      every number the verdict is made from
    series.tsv       per frame: movie position, delivered input, watched cells, RAM hash
    shots/           PNGs at the movie's own subtitle anchors and at the tail
    subtitles.srt    the author's 108 commentary lines as a subtitle track
    identity.txt     the cartridge that was actually measured

Screenshots are timed to *movie* frames using a 400-frame calibration pass whose
offset is measured, not a constant in the source. FCEUX's `gui.savescreenshot`
ignores the path it is given and writes `$HOME/.fceux/snaps/<romstem>-<n>.png`, so
the driver records the mapping in `shots.map` and `run.sh` renames afterwards;
`HOME` is redirected into the run directory so nothing touches the real config.

## Determinism

`ramhash.fold` is a digest of the whole run's per-frame RAM fingerprint. Three
independent runs on this machine produced `f6944980` each time, and `series.tsv`
was byte-identical across them. Diffing two `summary.txt` files is a cheap real
check, not a rerun:

    diff <(grep ramhash.fold A/summary.txt) <(grep ramhash.fold B/summary.txt)

## Running it unattended

The Arch package has no headless mode — it is a Qt build, and
`QT_QPA_PLATFORM=offscreen` segfaults on `QOpenGLWidget`. **The FCEUX window is
the run.** Closing it kills the driver mid-flight; `run.sh` detects that (no
`verdict` line) and reports exit `4` rather than claiming the movie failed.

    TIMEOUT=900 tools/tas/run.sh --movie M --rom R --outdir D

A full 44 003-frame run takes roughly 90 seconds at the ~660 fps this manages
with `--sound 0`.

## The cartridge this movie needs

    python3 tools/cartref.py release     # the movie's own cartridge
    python3 tools/cartref.py             # this build's target (Beta 1) -- NOT it

No path is written down here on purpose. A literal in a document is how this
project came to measure the wrong cartridge for five sessions.

## Tests

    python3 src/testing/test_tas_alignment.py

Pins the alignment comparator (a planted offset is found uniquely; a diverging
delivery is rejected at every offset; an offset covering too little is never
viable) and the subtitle sampling. The comparator is reimplemented in Python on
purpose: a test that calls the code it is testing cannot catch a comparator that
is wrong the same way twice.

## Files

    run.sh        the runner; the verdict is computed in the shell
    identity.py   pre-flight: cartridge identity, frame count, input extraction
    drive.lua     the FCEUX half; measures, never injects