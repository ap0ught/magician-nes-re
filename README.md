# Magician (NES) — moddable core

A Rust reconstruction of the machine **Magician** (Taxan Kaga, 1991) runs on, intended
to be built around the original Eurocom source that Chris Shrigley released in 2012,
plus a source-level rebuild of the cartridge from that source. **The Rust core has not
been started** (`crates/` does not exist); what exists is the rebuild, and it does not
yet assemble completely. See *Status*.

Three goals, in the order they pay off:

1. **Rebuild the cartridge from the original source** (`asm/`). Eurocom's PDS 1.26 assembly,
   the binary level data and the CHR artwork, assembled by a PDS-compatible assembler written
   here. It assembles, it loads, and it runs to a black screen: 30.4% of the PRG matches, the
   reset and IRQ vectors match exactly, and the boot address chain is self-consistent. It gets
   as far as frame 14, where one `rts` returns through a destroyed stack frame and everything
   after that is downstream of it. What is left is that the released code differs from this
   February 1990 source — the basis for every hack, and for the two goals below.
2. **A moddable core** (`crates/`). 6502 + software PPU + MMC3, verified frame by frame against
   the real cartridge, with traps that replace cartridge routines address by address. Same shape
   as `~/code/z2rs`.
3. **Co-op and an AI player.** A second player on a shadow machine, and a bot that plays the
   game. Same shape as `~/code/games/aibeatszelda`.

## The cartridge this is built against

Exactly one dump, pinned by digest. **The pin is now enforced in code**, in two
places that read the cartridge for real use: `asm/patches.py` verifies a named
dump's body SHA1 against both its own registry and the file on disk before filling
anything, and refuses the whole manifest if any region fails; `asm/build.py`
verifies before applying. Before that the digest existed only in two `print`
statements, which is the failure mode this repository's own notes warn about — two
dumps, identical headers, different content, and nothing able to tell them apart.

| | |
|---|---|
| file | `/extdrive/backups/SHARE/roms/nes/Magician (USA).nes` |
| size | 262160 bytes (16-byte iNES header + 128 KiB PRG + 128 KiB CHR) |
| mapper | 4 (MMC3), battery-backed PRG RAM, horizontal mirroring |
| body SHA1 (header stripped) | `bd806d7f7c318b8012433250ca10aa8387a962bb` |
| body CRC32 | `91E2E863` |

Two dumps of this title exist on this machine. `Magician (USA) (Beta).nes` has the **same
header shape** — same mapper, same 128 KiB PRG, same 128 KiB CHR, same mirroring — and a
different body (body SHA1 `2a0a444d…`, 23 388 differing PRG bytes). Whether they reach the same
screens was not re-tested on 2026-10-03; the earlier observation was made with a resumed
battery-backed SRAM save (see *Status*), so treat it as unverified. Nothing in the toolchain
can tell the two apart by header, so every address, every label from the source and every
replayed input applies to exactly one of them: the release, pinned above. See
`PROVENANCE.md`.

**Which dump to build against was measured, not assumed** (`GAPMAP.md`). Whole-PRG
the Beta is ahead by 177 bytes — 0.14 percentage points, a tie. But on the two modules whose
placement is actually *measured*, the release is three to six times better (X1: 9.82%
against 1.97%; X2 at its measured placement: 12.12% against 3.98%), and only X7's level data
favours the Beta. **The pin stays on the release.** `asm/patches.py` carries both dumps with
their digests and every manifest region names one, so switching is a one-line change.

The cartridge is the user's own. It is never committed; see `LEGAL.md`.

## The source

`vendor/Magician-NES/` is **Eurocom's source, vendored here** as of 2026-10-03, at upstream
commit `bf653a407cd97e4dfdca665063f25d8b44da130a`. It was a submodule until then; see
`LEGAL.md` for why, and for the terms. The files are unmodified — the build reads the decoded
form in `pds-text/`, not these.

It holds eight `X?.PDS` project files, ten `.SRC` include files, the level data under `DAT/`,
the message text under `TXT/` and the CHR artwork.

The `.PDS` files are binary containers produced by the 1989 Atari ST toolchain PDS 1.26.
`tools/pds_extract.py` decodes them to plain text into `pds-text/` (git-ignored, regenerated).
13,082 logical lines of source, with every routine, RAM variable, macro and data format named.

## Layout

```
vendor/Magician-NES/   Eurocom's source, vendored, unmodified
tools/pds_extract.py   PDS container -> plain text
tools/                 build and analysis helpers
asm/                   PDS-compatible assembler + cartridge build
crates/                the Rust machine and front ends (NOT STARTED - no crates/ dir)
pds-text/              extracted source (generated, git-ignored)
roms/                  git-ignored; does not exist. The cartridge is read in place from
                       /extdrive/backups/SHARE/roms/nes/, or from --cart
```

## Build

```sh
make                            # extract, assemble, wrap, report
make rom                        # just the loadable .nes
make check                      # the same, failing if the output moved
make verbose                    # per-file incbin trace
make gaps                       # rewrite GAPMAP.md: the stock-vs-rebuild gap map
make probe                      # run the rebuilt ROM beside the cartridge in BizHawk
make clean                      # remove asm/out/ and pds-text/
python3 tools/pds_extract.py    # just decode the PDS containers into pds-text/
python3 tools/pds_extract.py --check   # fail if pds-text/ is stale
python3 asm/patches.py --list   # the patch manifest, parsed, touching no cartridge
```

`make` and `make rom` run the assembler **unconditionally**. They used to be file
targets whose only prerequisites were four Python files, so `make` printed
`Nothing to be done for 'all'` whenever `asm/out/prg.bin` happened to be newer
than the tools -- a build that could silently not build. The assembler takes ~20 s,
almost all of it the sixteen-slot search; that is the price of a `make` that means
something, and `make check` is the cheap way to watch for a change. `make check`
also distinguishes the two ways it can fail: an assembler that exits non-zero now
says so and prints the log tail, instead of reporting "the PRG moved".

The build assembles all eight `X?.PDS` modules plus `SEQ.SRC` and exits 0.
**39 209 of 131 072 PRG bytes (29.91%) come from the source and are identical to
the cartridge.** A further 864 bytes (0.66%) are filled from the cartridge by
`asm/patches.manifest` — see *Patch manifest* below — giving 40 059 (30.6%) in the
`.nes`. 8 of 32 CHR 4 KiB pages match and 3 622 symbols are recovered. `reset` and
`irq` land on the cartridge's vectors exactly and `nmi` is 3 bytes early.

The build prints the byte accounting on every run, and the two lines are not
interchangeable:

```
PRG: 40059/131072 bytes identical to the cartridge (30.6%)
  of which from the source alone : 39209 (29.9%)
  of which from the patch manifest: 864 (0.7%) across 1 region(s), 864 bytes
```

Only the first is evidence about the source. A rising total is otherwise
indistinguishable from a rising number of hidden bugs.

**Two of the eight module placements are pinned, and six are ASSUMED.** The build
prints the whole table with the evidence for each. `X6` is pinned "by
elimination" and `X7` from the cartridge's reset vector; `X0`, `X1`, `X2`, `X3`,
`X4` and `X5` are assumed, and the build labels them `ASSUMED, NOT MEASURED` on
every run. `SEQ.SRC` is at slot 5 on a measured single hit — its first 24 bytes
occur once in 131 072, at file `$0A000` — and 29 of its position-independent code
windows land in slot 5 at offset zero.

Per-module byte scoring of code is **not** at chance everywhere, which entry 01 of
the journal got wrong: `X5` scores 11.8% and `X7` 45.3% at their known-good
slots. It is at chance where the placement is *wrong*. `GAPMAP.md` has the
per-slot and per-module tables and the placement evidence.

`--allow-incomplete` and the `INCOMPLETE BUILD` policy still exist for the case
they were written for — a module whose slot cannot be determined is left out and
named, rather than assembled wherever the search happened to end. No module hits it
now. `--x7-vector off` and `--seq-src off` reproduce the older states for
comparison; `--x7-bank-groups off` reproduces the self-overwriting `b`-group bug
that left slot 7 empty; `--no-patches` reports the source-only image.

## Patch manifest

The released source is February 1990 and the cartridge is 1991, so some of what
the cartridge contains is **not in the source at all**. Where that is proven, the
gap is filled from the cartridge and recorded — without committing a single
cartridge byte, which `LEGAL.md` forbids.

`asm/patches.manifest` is a committed, line-oriented, human-readable file naming
each filled range: the PRG file offset and length, the CPU address, which dump it
came from, that dump's body SHA1, a classification code, and free text saying why.
There are **no hex blobs, no `.incbin`, no base64 and no hand-typed machine code**
anywhere in the repository. `asm/patches.py` reads the manifest, resolves the named
dump and **verifies its digest** before filling anything; one mismatch fails the
whole application, because a half-patched image is worse than an unpatched one and
the accounting would then describe bytes from two different places.

The digest check is not decoration. Two dumps of this title sit on this machine
with byte-identical iNES headers and different bodies, so a manifest entry that
said only "the cartridge" could be applied to either without complaint.

One region is shipped, `sam-samples-at-fc40`, class **(b)** genuinely absent from
source: `DAT/MUS/SAM.SAM`'s first 32 bytes occur exactly once in the cartridge, at
file `$1FC40`, where the source's `X7.PDS:997` puts them at `$FB80`. Regions
classified (a), (c) or (d) are refused by the applier and recorded in `GAPMAP.md`
with their evidence instead, because filling those would destroy the evidence of
the bug.

## Status

**The rebuild shows a picture.** Not a black screen and not one flat colour. In
BizHawk 2.11.1's quickerNES at frame 60, unattended, with `NES/SaveRAM/` cleared:

| | cartridge | rebuild |
|---|---|---|
| screenshot | 256 x 224 | 256 x 224 |
| distinct colours | 10 | 10 |
| max luminance | 216 | 207 |
| mean luminance | 51.91 | 54.07 |
| non-zero pixels | 20 807 / 57 344 | 21 580 / 57 344 |

The rebuild's top ~40% is correct -- brick background, the whole `MAGICIAN` logo,
the skeleton's head and shoulders. The lower ~60% is scrambled, and by frame 400
the corruption has spread upwards. Reproduce with

    make rom
    python3 tools/bizhawk/capture.py /tmp/opencode/biz 60
    python3 tools/bizhawk/series.py /tmp/opencode/biz/series cart:200,400 reb:200,400

It is also *not* converging: static from frame 200 to frame 400, and settling on
a different picture than the cartridge's. That corrects a claim three sessions of
this project got wrong. "The rebuild's frame is flat colour, max luminance 0.0,
0 non-zero pixels" came from `tools/nestrace.py` -- a second NES emulator written
for this project -- and not from an emulator whose correctness had been
established. **Every conclusion in
this repository that rests on a `nestrace.py` framebuffer is withdrawn as
evidence about the ROM**, including `PPUMASK=$FE`, "all 32 palette entries `$0F`"
and "659/2048 nametable bytes non-zero". `nestrace.py` is still a legitimate
instrument for *where execution goes*; its framebuffer is not evidence about
whether a ROM works. `journal/05-bizhawk-is-the-instrument.md` has the numbers and
the two harness traps that produce confident nonsense.

BizHawk *is* drivable here, headfully on the live display -- no Xvfb was installed
and the session was not modified. `EmuHawkMono.sh --help` documents
`--lua <path>`, which implies `--luaconsole` and runs a script inside the
emulator: `emu.frameadvance()` is frame-exact, `client.screenshot(path)` writes the
core's own video buffer, and `client.exit()` closes the session so the next launch
is not swallowed by the single-instance pipe. `tools/bizhawk/run.sh` wraps one
such session. Two traps are documented there and in `journal/05`: the ROM path
must be absolute (`EmuHawkMono.sh` cds to its own directory, and a relative path
hangs with nothing in the log, which looks exactly like "BizHawk will not start"),
and `memory.usememorydomain` with an unknown name leaves the *previous* selection
in place rather than falling back to the default, so a typo silently hands you the
wrong region -- that one produced a report of "all four nametables identical" that
was 1024 bytes of the constant `$F2` in both images. Check
`memory.getcurrentmemorydomain()` after selecting. The real domain names are read
from `memory.getmemorydomainlist()`, which returns plain strings: `WRAM`, `CHR`,
`CIRAM (nametables)`, `PRG ROM`, `CHR VROM`, `PALRAM`, `OAM`, `System Bus`,
`CPU registers`.

**Where the rebuild is still wrong.** The corruption is in the picture, so the
remaining bug is a statement about VRAM rather than about control flow. Dumped
from both images at the same frame through BizHawk's own memory domains
(`tools/bizhawk/state.lua`, which verifies the domain name after selecting it):

| region | domain | frame 60 | frame 400 |
|---|---|---|---|
| CHR-RAM $0000-$1FFF | `CHR` | 480 / 8192 differ | 480 / 8192 |
| nametables, 4 KiB | `CIRAM (nametables)` | 969 / 4096 differ | 1006 / 4096 |
| palette, 32 bytes | `PALRAM` | **0 differ** | 2 differ |
| OAM, 256 bytes | `OAM` | **0 differ** | **0 differ** |
| work RAM, 8 KiB | `WRAM` | **0 differ** | **0 differ** |

**Work RAM and OAM are byte-identical.** The screen data is decompressed
correctly; the damage is entirely on the output side. And it is progressive, not a
slow draw: the cartridge is static from frame 60 to 400, the rebuild is static
from frame 200 to 400, and what it settles on is a different picture.

**The routine that writes the nametable is not the release's routine.**
`unrun`/`unrunscn` (`pds-text/x5.pds:106-121`) assembles correctly -- every
`lda (t0),y` in it is `B1 13`, `($13),y` -- but the cartridge's copy at `$DB1D` is
the source's routine plus **one contiguous 36-byte insertion and nothing else**,
verified by disassembling both and comparing mnemonic streams: 71 instructions to
71 instructions once the block is removed.

```
$DB43  2C D8 06  bit  $06D8      ; if bit 7 is set, do not write now
$DB46  30 05     bmi  $DB4D
$DB48  8D 07 20  sta  $2007      ; the source's only instruction here
$DB4B  10 1A     bpl  $DB67
$DB4D  08        php             ; save A/X/Y, queue the byte on the DMA stack
$DB54  20 8E F3  jsr  $F38E      ;   ($F38E pushes data,count=1,lo,hi,token $60)
$DB57  20 46 F9  jsr  $F946      ;   then flush it ($F94F is emptydma)
$DB5A  E6 11     inc  $11        ;   bump the 16-bit VRAM address
```

The release defers the nametable write to the NMI queue; the February 1990 source
has only the synchronous `sta $2007`. Nothing in the source corresponds to
`$F38E` -- in our build that address is `CA / dex`, the tail of an unrelated
routine. This is the same shape of difference already recorded for `reset`, but
here it is inside the one routine that writes the region that is corrupt.

**That is a located difference, not yet a proven cause.** If the tail of the 1 KiB
`$2007` burst were being stolen by `nmi0`, `PALRAM` would differ substantially; it
differs by 0 bytes at frame 60. And the damage is at the *start* of the nametable
(`$08` filling rows 0-3, real data from row 4, against the cartridge's real data
from row 1), not the tail. `journal/06-the-picture-is-wrong-in-vram.md` states
what is proven, what is not, and the next three measurements.

**What the tracer says, and how much of it survives.** `tools/nestrace.py` reports
that at frame 14, cycle 439 272, the `rts` at `$9D6C` pops a destroyed return
address and jumps to `$0000`, which is `brk`, which vectors through `$FFFE` to
`$F9B3` -- the *IRQ* handler -- with `A=$FF`, and `$FF` to `$E001` sets bit 7,
disabling the MMC3 IRQ. That may well be a real defect. It is no longer measured
against an instrument of unknown correctness. Trace the tracer's claim with

    python3 tools/nestrace.py --rom asm/out/magician-rebuilt.nes --frames 300 \
        --halt-at 0xFFCA --last 40

**That one `rts` is not the whole remaining bug, and the claim is withdrawn as
stated.** `tools/nestrace.py` further reports 1 077 scanline IRQs in the rebuild
against the cartridge's 8 107, `sta $2001` 12 times against 198, and a genuine
6502 freeze on `$52` at `$FFCA` by frame 194 while executing animation tables at
`$FFC0` in the fixed bank. A ROM that freezes at `$FFCA` on frame 194 does not
draw a title screen on frame 60 in a correct emulator, so at least one link in
that chain belongs to the tracer. The specific `rts` is re-testable now and is not
re-tested here; the number to beat is the cartridge's own.

The stack was already 144 bytes deep at the bad `rts`, which matters because
`initdma` parks the DMA queue at `$7E` -- the queue and the main stack share page
1, and the main stack has only `$FF`-`$80` before they meet.

**The cartridge is not the control it was assumed to be.** Three claims this file
used to make are refuted by measurement, all of it after the tracer gained MMC3
interrupts, CHR banking and correct CHR-RAM writes:

* It is not true that both images "end spinning at `$8382: jsr rn / lda $40 /
  bne $8382`". `$8382` is `ptlr`, a player-animation table inside X4
  (`x4.pds:202`), reached only because the MMC3 IRQ never fired. With interrupts
  delivered the cartridge takes NMI every frame, takes 8 107 scanline IRQs in 150
  frames, runs its sound engine, does OAM DMA, scans the joypad, calls
  `setchr`/`movepal`, and ends with `curchrpal` holding
  `0F 28 38 30 0F 2A 3A 30 0F 17 27 38 0F 21 31 30` -- `TIT.PAL` with `$0F` in
  the four backdrop slots `movepal` forces. The palette pipeline works end to end.
* It is therefore also not true that an all-`$0F` palette on the cartridge is
  evidence about anything. **The cartridge is black in this instrument too**
  (55/2048 nametable bytes nonzero, `$2007` only ever aimed at `$3F00` and at
  sixteen bytes per nametable block). Something is still missing from the tracer
  on the graphics side. The two candidates the measurements point at: there is no
  APU at all, and `tick` advances the PPU once per instruction so vblank is seen
  up to seven CPU cycles late and sprite-0 hit has no dot. Neither is implemented.
* It is not true that the palette being `$0F` is why the screen is black, in the
  sense that fixing the palette would fix the screen. The palette reaches the PPU
  correctly and the nametable is empty.

Byte-match against the release is **39 832 / 131 072 (30.4%)**, of which
**38 982 (29.7%) is source-only** and 864 bytes (0.7%) come from the one
class-b manifest region. That number went *down* from 29.9%'s 39 204 in an earlier
round of fixes, and that is the right outcome: byte-match against a later release
rewards a wrong-but-self-consistent build, and the fix that lowered it
(`lda (zp),y` was being assembled as `lda (zp,x)`, 54 statements in all eight
modules) is confirmed by the cartridge's own opcode at file `$1F772`.

What the tracer shows the rebuild doing, in order, with `--where`:

    reset -> initdma -> initcols -> setchr/setspr/movepal -> dotitle
          -> unrunscn -> jsr initspr -> NMI -> tnmi

Corrections to what this file used to claim, with the measurements:

* `initcols`'s slot is not wrong. X6 is pinned to slot 15 because `reset`'s
  `jsr initcols` targets the fixed `$E000` window, and the tracer reaches
  `initcols`. (`journal/02-...md` §5.)
* `initspr` is not dropped by the ceiling. It assembles at `$F033`, 307 bytes
  *below* `X6_CEILING = $F166`, so `jsr initspr` lands in X6's own code.
* The zero-page/RAM map differs from the release (`t0` is `$13` here, `$11`
  there; `curchrpal` is `$04D4` here, `$0180` there) and that is **not** a bug.
  Both maps come from the same `zp`/`ram` declarations in `X0.PDS:363+`, every
  module reads the same symbols, and the palette path works under either:
  `nmi0` pushes `curchrpal-$60 .. curchrpal` to `$3F00-$3F60`, and `curchrpal`
  aliases `$3F00` because `$3F60 & $1F == 0`. Copying the release's addresses
  would be matching the cartridge, not fixing the build.
* `PPUMASK` is `$FE`, not `$00` -- background and sprites are enabled with the
  leftmost column masked. The screen is black because the palette is `$0F`, not
  because rendering is off.

What is *not* the reason any more, having been the reason once: the reset vector
used to read `$E84D/$E85D/$E842` against the cartridge's `$F9C1/$F9B3/$F9AB`. The
vectors now match. The boot path is readable side by side, and the release's
`reset` turns out to be **this source's `reset` plus ten bytes** -- two small
insertions -- after which the two are instruction for instruction identical. So the
February 1990 source and the cartridge are much closer on the boot path than a
30.4% whole-image figure suggests.

Note that the cartridge has battery-backed PRG RAM, so any "does it boot"
comparison must clear `NES/SaveRAM/` on both sides or BizHawk resumes a stale save
and shows you last session's game; `tools/bizhawk/run.sh` does this on every run.
And unattended, the cartridge does *not* reach the GAME RESTORE SCREEN: it goes
title -> attract demo. Reaching the restore screen needs a button press. The
earlier claim that no harness could inject one was an artifact of driving BizHawk
by window-scraping; `joypad.*` is on the Lua surface and is the next thing to
close, but it is not needed to answer "does it draw a picture".

`crates/` has not been started, and nothing in this repository depends on it.

## Tracer conformance

`tools/nestrace.py` was, until this session, the only NES instrument wired up on
this machine -- BizHawk is installed at
`$HOME/code/games/aibeatszelda/BizHawk-2.11.1-win-x64` but nothing could drive it,
because nobody had found `--lua`. That is now corrected above, and the tracer is
demoted rather than deleted: it remains a precise instrument for *where execution
diverges*, and it is no longer the instrument for *whether a ROM works*. It is
measured against koute's `nes-testsuite` rather than trusted. `--testsuite DIR`
runs every testcase, **refuses to run a ROM whose md5 does not match the
testcase**, and compares the md5 of the 256x240 greyscale framebuffer against the
reference.

    make testsuite                      # TS=/path/to/nes-testsuite

The suite is third-party and is never committed; `TS` points at a checkout
else on disk. `KNOWN-FAIL` means we reproduced the reference's own failure
screen -- ten testcases expect blargg's failure digest, and pinky fails those
too. `UNSUPPORTED` means the mapper is not modelled and the ROM was *not* run.

Run of 2026-10-03 (`--ts-frames 120`): **68 testcases, PASS 7, KNOWN-FAIL 7,
FAIL 34, ERROR 19, UNSUPPORTED 1.** Of the 41 with a real expected picture, 7
pass.

```text

apu_test  (FAIL 8)
  FAIL         apu_test/1-len_ctr                            got 101b37d83de9, want a6a60165f8a7
  FAIL         apu_test/2-len_table                          got 1dae3e93370e, want e082be73c51e
  FAIL         apu_test/3-irq_flag                           got f558d9957ab7, want df67bf9e0aa4
  FAIL         apu_test/4-jitter                             got d3c9f46c50d9, want 5511965c3880
  FAIL         apu_test/5-len_timing                         got 10520ee16c0d, want 9416144226b8
  FAIL         apu_test/6-irq_flag_timing                    got 5c59c69f5fd7, want b474f7a1ca18
  FAIL         apu_test/7-dmc_basics                         got 171fbab47e7d, want 3d7a08151b0d
  FAIL         apu_test/8-dmc_rates                          got 873a0e8e7184, want c5f193bb4fd2

blargg_apu_2005.07.30  (KNOWN-FAIL 7)
  KNOWN-FAIL   blargg_apu_2005.07.30/01.len_ctr              printed blargg's failure screen, which is what the testcase expects
  KNOWN-FAIL   blargg_apu_2005.07.30/02.len_table            printed blargg's failure screen, which is what the testcase expects
  KNOWN-FAIL   blargg_apu_2005.07.30/03.irq_flag             printed blargg's failure screen, which is what the testcase expects
  KNOWN-FAIL   blargg_apu_2005.07.30/04.clock_jitter         printed blargg's failure screen, which is what the testcase expects
  KNOWN-FAIL   blargg_apu_2005.07.30/05.len_timing_mode0     printed blargg's failure screen, which is what the testcase expects
  KNOWN-FAIL   blargg_apu_2005.07.30/06.len_timing_mode1     printed blargg's failure screen, which is what the testcase expects
  KNOWN-FAIL   blargg_apu_2005.07.30/07.irq_flag_timing      printed blargg's failure screen, which is what the testcase expects

blargg_ppu_tests_2005.09.15b  (ERROR 3)
  ERROR        blargg_ppu_tests_2005.09.15b/palette_ram      halted: illegal opcode $42 at $E0A3 (PC=$E0A3)
  ERROR        blargg_ppu_tests_2005.09.15b/vbl_clear_time   halted: illegal opcode $02 at $E339 (PC=$E339)
  ERROR        blargg_ppu_tests_2005.09.15b/vram_access      halted: illegal opcode $02 at $E413 (PC=$E413)

branch_timing_tests  (PASS 3)
  PASS         branch_timing_tests/1.Branch_Basics           26 frames
  PASS         branch_timing_tests/2.Backward_Branch         32 frames
  PASS         branch_timing_tests/3.Forward_Branch          30 frames

dmc_dma_during_read4  (FAIL 1)
  FAIL         dmc_dma_during_read4/read_write_2007          got 55f42c07e417, want 289318c88b06

holy_diver_batman  (UNSUPPORTED 1)
  UNSUPPORTED  holy_diver_batman/M1_P128K_C128K_W8K          mapper 1

instr_misc  (FAIL 1, PASS 2)
  PASS         instr_misc/01-abs_x_wrap                      22 frames
  PASS         instr_misc/02-branch_wrap                     22 frames
  FAIL         instr_misc/03-dummy_reads                     got d5050ef43fd1, want e4b3faaf5841

oam_read  (PASS 1)
  PASS         oam_read/oam_read                             68 frames

sprite_hit_tests_2005.10.05  (FAIL 10, PASS 1)
  FAIL         sprite_hit_tests_2005.10.05/01.basics         got e93b775d032e, want 388ab951797a
  FAIL         sprite_hit_tests_2005.10.05/02.alignment      got b4e420c08b31, want efe0e20c14ae
  FAIL         sprite_hit_tests_2005.10.05/03.corners        got dd21ba551804, want 7ff7c9bc044a
  FAIL         sprite_hit_tests_2005.10.05/04.flip           got 88ab4314eb46, want c1bc9a362d15
  FAIL         sprite_hit_tests_2005.10.05/05.left_clip      got 9011675b5bcf, want e8ab4d728f70
  FAIL         sprite_hit_tests_2005.10.05/06.right_edge     got dee27043d5bb, want cd8c9e59befa
  FAIL         sprite_hit_tests_2005.10.05/07.screen_bottom  got 5acdd2f8e12e, want 6e946157b0a4
  FAIL         sprite_hit_tests_2005.10.05/08.double_height  got 5d1cdb607b74, want b8cb45dfdf1c
  FAIL         sprite_hit_tests_2005.10.05/09.timing_basics  got 7e161bbc2bb1, want ab27927fd039
  FAIL         sprite_hit_tests_2005.10.05/10.timing_order   got a24647bbd159, want 1fa4915b3c7e
  PASS         sprite_hit_tests_2005.10.05/11.edge_timing    108 frames

sprite_hit_timing  (FAIL 1)
  FAIL         sprite_hit_timing/sprite_hit_timing           got a2c7bac2cb87, want ffe8f3026500

vbl_nmi_timing  (FAIL 7)
  FAIL         vbl_nmi_timing/1.frame_basics                 got a1700661daae, want b6c0c4c43834
  FAIL         vbl_nmi_timing/2.vbl_timing                   got cd5e01fd6b29, want 4390eee80911
  FAIL         vbl_nmi_timing/3.even_odd_frames              got 8da9015293c1, want cfa81bdc8309
  FAIL         vbl_nmi_timing/4.vbl_clear_timing             got d091c4f5152d, want c0e78f389727
  FAIL         vbl_nmi_timing/5.nmi_suppression              got f00fdc89cbd2, want 2ff7a0778343
  FAIL         vbl_nmi_timing/6.nmi_disable                  got 0f5142192bbd, want 45e1c4cf3394
  FAIL         vbl_nmi_timing/7.nmi_timing                   got d6fb79ed2d1a, want bce0c0c99010

mmc3_irq_tests  (FAIL 6)
  FAIL         mmc3_irq_tests/1.Clocking                     $F8=2 (code 2 in the readme)
  FAIL         mmc3_irq_tests/2.Details                      $F8=2 (code 2 in the readme)
  FAIL         mmc3_irq_tests/3.A12_clocking                 $F8=2 (code 2 in the readme)
  FAIL         mmc3_irq_tests/4.Scanline_timing              $F8=2 (code 2 in the readme)
  FAIL         mmc3_irq_tests/5.MMC3_rev_A                   $F8=2 (code 2 in the readme)
  FAIL         mmc3_irq_tests/6.MMC3_rev_B                   $F8=2 (code 2 in the readme)

instr_test-v5  (ERROR 16)
  ERROR        instr_test-v5/01-basics                       no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/02-implied                      no $DE $B0 $47 signature at $6001 (halted: illegal opcode $1A at $03A0)
  ERROR        instr_test-v5/03-immediate                    no $DE $B0 $47 signature at $6001 (halted: illegal opcode $0B at $03A0)
  ERROR        instr_test-v5/04-zero_page                    no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/05-zp_xy                        no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/06-absolute                     no $DE $B0 $47 signature at $6001 (halted: illegal opcode $02 at $03A2)
  ERROR        instr_test-v5/07-abs_xy                       no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/08-ind_x                        no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/09-ind_y                        no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/10-branches                     no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/11-stack                        no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/12-jmp_jsr                      no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/13-rts                          no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/14-rti                          no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/15-brk                          no $DE $B0 $47 signature at $6001 (ran out of frames)
  ERROR        instr_test-v5/16-special                      no $DE $B0 $47 signature at $6001 (ran out of frames)
```

Reading it: `branch_timing_tests` 3/3 and `oam_read` 1/1 are framebuffer-exact
against a reference and cover the two subsystems this game's timing rests on.
`instr_misc` 2/3 bounds the CPU (`03-dummy_reads` wants the 6502's dummy reads,
which the tracer does not do). All eighteen `sprite_hit_*` + `vbl_nmi_timing`
failures are dot-level timing, which is one missing capability -- `tick` advances
the PPU once per instruction -- not eighteen bugs. There is no APU, which is why
every `apu_test` fails. `instr_test-v5` is wired up but not usable at Python
speed and its result-byte convention is not yet re-read from the source, so its
sixteen rows are `ERROR` and mean "not measured". See
`journal/04-measure-the-instrument-against-a-test-suite.md` §5.

