# Magician (NES) — moddable core

A Rust reconstruction of the machine **Magician** (Taxan Kaga, 1991) runs on, intended
to be built around the original Eurocom source that Chris Shrigley released in 2012,
plus a source-level rebuild of the cartridge from that source. **The Rust core has not
been started** (`crates/` does not exist); what exists is the rebuild, and it does not
yet assemble completely. See *Status*.

Three goals, in the order they pay off:

1. **Rebuild the cartridge from the original source** (`asm/`). Eurocom's PDS 1.26 assembly,
   the binary level data and the CHR artwork, assembled by a PDS-compatible assembler written
   here. It assembles, it loads, and it runs to a black screen: 29.9% of the PRG matches, the
   reset and IRQ vectors match exactly, and the boot address chain is self-consistent. What is
   left is that the released code differs from this February 1990 source — the basis for every
   hack, and for the two goals below.
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

**The rebuild assembles, loads and runs, and still shows a black screen.** No
emulator has been run since BizHawk was found to be unusable here, so this is the
tracer's verdict (`tools/nestrace.py`), not an emulator's: max luminance over the
256x240 picture area is 0, and the frame is uniformly black. The captured
frame for this state is reproducible with

    python3 tools/nestrace.py --rom asm/out/magician-rebuilt.nes --frames 120 --png out.png

The cartridge does **not** boot in the tracer either, which is the important
caveat: the tracer is a diagnostic, not an acceptance test, and "the rebuild is
black" is not yet a statement about the rebuild. Both images now run 600 frames
without halting and both end in the same place -- the cartridge in
`$8382: jsr rn / lda $40 / bne $8382`, waiting on a zero-page flag that exactly
one `sta $40` in the cartridge can clear, with an all-`$0F` palette. Since the
cartridge demonstrably works on hardware, the palette is not yet evidence about
the rebuild either. What *is* sound is the comparison between the two under the
same instrument, and that comparison is what `journal/03-...md` records.

Byte-match against the release is **39 832 / 131 072 (30.4%)**, of which
**38 982 (29.7%) is source-only** and 864 bytes (0.7%) come from the one
class-b manifest region. That number went *down* from 29.9%'s 39 204 in the last
round of fixes, and that is the right outcome: byte-match against a later release
rewards a wrong-but-self-consistent build, and the fix that lowered it
(`lda (zp),y` was being assembled as `lda (zp,x)`, 54 statements in all eight
modules) is confirmed by the cartridge's own opcode at file `$1F772`.

What the tracer now shows the rebuild doing, in order, with `--where`:

    reset -> initdma -> initcols -> setchr/setspr/movepal -> dotitle
          -> unrunscn -> jsr initspr -> NMI -> tnmi

and then, at cycle 98674, `addmsg`'s `rts` at `$F1A5` finds an empty stack and
returns into RAM at `$0F10`. **That is the remaining bug: a lost stack frame**,
not a missing routine and not the slot-15 ceilings. `im` (`X5.PDS:319`, the
`orgchrpal -> curchrpal` copy) and `nmi0` (`X5.PDS:167`, the per-frame palette
push) are never reached because of it, which is why every one of the 32 palette
entries is still `$0F`. `journal/03-...md` has the trace and the dead ends.

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
and shows you last session's game. And unattended, the cartridge does *not* reach
the GAME RESTORE SCREEN: it goes title -> attract demo. Reaching the restore screen
needs a button press, and BizHawk here takes input from SDL with no way for the
harness to inject one -- so "does it boot" is currently measurable only as "does it
reach the title screen unattended".

`crates/` has not been started, and nothing in this repository depends on it.
