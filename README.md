# Magician (NES) — moddable core

A Rust reconstruction of the machine **Magician** (Taxan Kaga, 1991) runs on, intended
to be built around the original Eurocom source that Chris Shrigley released in 2012,
plus a source-level rebuild of the cartridge from that source. **The Rust core has not
been started** (`crates/` does not exist); what exists is the rebuild, and it does not
yet assemble completely. See *Status*.

## The build target is Beta 1, and that changed the headline. A `dl`/`dh` bug changed it again

**Everything below that quotes a percentage, an address, or a "the source is
missing this" finding was measured against the release
(`Magician (USA).nes`, 1991-02). That was the wrong cartridge.**

The source's own last-modified date is `02/03/90`. `Magician (USA) (Beta 1)
(1990-03-02).nes` is dated 1990-03-02. They are the same build. The release is a
year newer, and re-pinning to Beta 1 moved the headline from **30.4% to 59.9%**
with no bytes borrowed from any cartridge:

```
PRG: 78555/131072 bytes identical to the cartridge (59.9%)
  of which from the source alone : 78555 (59.9%)
  of which from the patch manifest: 0
```

**59.9% was itself measured with an assembler bug in the tree, so 59.9% is not
the current number.** `dl`/`dh` were implemented as 4 and 2 bytes -- the Atari
MACRO reading of "define long" -- when the source uses them as the low and high
halves of a pointer list, one byte each, read back by `lda ijvl,x / sta t2 /
lda ijvh,x / sta t3 / jmp (t2)` (`x5.pds:755-760`) into two adjacent zero-page
bytes. Every pointer table in the game was being emitted two and a half times too
long. Fixing it, with a test that fails loudly against the old widths
(`src/testing/test_pointer_widths.py`), moves the headline a second time:

```
PRG: 96413/131072 bytes identical to the cartridge (73.6%)
  of which from the source alone : 96413 (73.6%)
  of which from the patch manifest: 0 (asm/patches.manifest has no regions)
```

**+17 858 bytes, every one of them source-derived, with the manifest still
empty.** Both figures were measured by building both ways on this machine and
reading the build's own byte accounting, not recalled from a report; the build is
what moves the number, in one direction or the other. The anchor, the three
vectors, the title screen and the CHR page count are unmoved by this -- it is a
placement-neutral bug, which is why nothing else in the table changed.

| | release | beta1, `dl`/`dh` at 4/2 (was "current") | beta1, widths fixed (current) |
|---|---|---|---|
| PRG match | 39 832 (30.4%) | 78 555 (59.9%) | **96 413 (73.6%)** |
| X7 anchor | `$F166` (from reset `$F9C1`) | `$F0D5` (from reset `$F930`) | **`$F0D5`** (unchanged) |
| `reset` vs cartridge | 27 instructions, none identical | 31 of 31 byte-identical | **31 of 31** (unchanged) |
| nmi / reset / irq | `$F9A8` / `$F9C1` / `$F9B3`, first two suspect | `$F917` / `$F930` / `$F922`, all exact | **unchanged** |
| title screen (BizHawk, frame 60) | not measured | nametable 1024/1024, CIRAM 4096/4096 | **unchanged** |
| X7's DAT bytes matching | 52.3% | 98.4% | **99.5%** |
| CHR 4 KiB pages matching | 8/32 | 15/32 | **15/32** (unchanged) |
| patch manifest regions | 1 | 0 | **0** |

The six dumps are named in `asm/patches.py`'s registry and **verified against the
bytes on disk** by `tools/carts.py` -- body SHA1, whole-file SHA1, the battery
bit and the three vectors. `tools/cartref.py` resolves names to paths, so there
is no literal cartridge path anywhere in `tools/`, `asm/` or the `Makefile`.

Two of those dumps have facts worth knowing before anything else is measured:

- **Beta 1's header byte 6 is `$40`, not `$42`. Its battery bit is CLEAR** and
  every other dump's is set. Whether a stale save can affect a run is therefore
  a per-dump question, read out of the header; `tools/bizhawk/run.sh` reads it
  and refuses (rather than deleting anything) when a battery-backed dump has a
  save it would resume.
- **Beta 1's vectors are `nmi $F917`, `reset $F930`, `irq $F922`.** (The iNES
  order is NMI, RESET, IRQ — RESET is in the middle.) Every other dump is at
  least `$30` away from that except beta3/beta, and beta4 and the release share
  the release's vectors exactly.

### Findings that dissolved once Beta 1 became the target

Each of these was a real, carefully measured conclusion. Each was an artifact of
pinning to a cartridge a year newer than the source.

- **The class-`e` "absent work" regions.** `TIT.DAT`, `PW.DAT` and `PAN.DAT` were
  recorded as assets the source did not contain, with a 993-byte layout
  divergence. Beta 1 contains **all three, byte-identical to
  `vendor/Magician-NES/DAT/`**. The release contains only `PAN.DAT`. The
  manifest region for `PAN.DAT` is retired; the manifest is now empty, which is
  a finding rather than a gap.
- **The `sam-samples-at-fc40` manifest region**, class `b`, filled from the
  release because the release has SAM.SAM at `$FC40` while `X7.PDS:997` says
  `$FB80`. Beta 1 has it at **`$FB80`** — the address the source states. Over
  SAM.SAM's full 1136 bytes the source-only build matches Beta 1 **1136/1136**
  and the release 24/1136. The region was overwriting 864 bytes that were
  already right: a net **−850**.
- **The renamed spell table.** The source says `RAZORSTORM`, `KISS MY AXE`,
  `FIRE FOUNTAIN`, `POWER SHIELD`, `HEAL`, `ANTI VEN`, `MUZAK`; the guide shows
  `BOOMERAXE`, `FIRE RING`, `FIRE SPRAY`, `POW SHIELD`, `MEDITATE`,
  `SOUND TEST`. **Beta 1 carries the source's own names, in the source's order.**
  The guide documents the release. See `tools/spelltab.py`.
- **The X7 `$FB80` overrun.** With the release's anchor, X7's code ran 16 bytes
  past its own `if last>$fb80` guard. Re-anchored, it does not.
- **All five quoted RAM addresses.** `curchrpal` `$0180`, `obtyp` `$048C`,
  `ninflag` `$06C9`, `lastz` `$06FA`, `jt` `$2C` are the release's. Read out of
  Beta 1's own encoding of `dotitle`, they are `$04D4`, `$04F4`, `$071A`,
  `$0742`, `$002E` — and `initdma`/`initcols` come out at `$F8BD`/`$EEC7`, which
  is exactly where our assembly puts them. See `tools/rammap.py`.
- **The CHR revision problem and the "missing" title artwork.** `TIT0.CHR` sits
  at CHR offset 57344 in Beta 1, byte-identical to where our own build puts it.

### Findings that survived

- **`initcols` is reached through the fixed `$E000` window**, so X6 must be in
  slot 15. Now measured rather than assumed: `reset`'s `jsr` operand is `$EEC7`
  in both images and `initcols` assembles at `$EEC7`.
- **X5, X6 and X7 compete for slot 15's 8 KiB.** Still true, and now *derived*
  rather than hand-placed: X7's start comes from the cartridge's reset vector,
  X6's length is measured with no ceiling applied, and X6 fills exactly the gap.
  `X5_CEILING $E605 → $E4DE`, `X6_CEILING $F166 → $F0D5`, both computed.
- **The title screen's PPU state.** Palette, attributes and the pattern table in
  use are byte-identical to Beta 1; see *Status* for the one scanline that is not.
- **The `ijvl`/`ijvh` table before the scene descriptors is still unexplained.**
  Our build emits 108 bytes of word/byte tables where Beta 1 has 36 bytes of
  different content, and that is the whole of the **+72-byte** offset still
  putting `TIT.DAT`/`PW.DAT`/`PAN.DAT` 72 bytes late. Bounded, not fixed.

Three goals, in the order they pay off:

1. **Rebuild the cartridge from the original source** (`asm/`). Eurocom's PDS 1.26 assembly,
   the binary level data and the CHR artwork, assembled by a PDS-compatible assembler written
   here. It assembles, it loads, and it runs to a black screen: **59.9%** of the PRG
   matches beta1 (it was 30.4% against the release -- see above), the
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

**Which dump to build against is settled by the vectors, not by a byte count.** Our rebuild's
`reset=$F9C1` and `irq=$F9B3` match the release *byte-exactly* and match the Beta on neither;
the Beta sits a uniform `$3D` (61) below the release on all three. Our `nmi` is `$F9A8`, three
bytes below the release's `$F9AB`, and the reason is exact: our trampoline carries one extra
`LDA $2002` (`AD 02 20`) that the release dropped — delete those three bytes and the two
handlers are identical. So **the source is on the release's branch**, and the pin stays on the
release for a stated reason rather than a scored one. See `PROVENANCE.md` §7.

> **RETRACTED (2026-10-04).** This paragraph used to read: *"Whole-PRG the Beta is ahead by 177
> bytes — 0.14 percentage points, a tie"* and *"on the two modules whose placement is actually
> measured, the release is three to six times better"*. The first measurement is
> arithmetically correct and answers a different question: a byte count over 131 072 bytes is
> dominated by X7's 60 618 bytes of level data, which is precisely what changed most between a
> 02/03/90 development build and a 02/1991 cartridge. The second is still true and is what
> `tools/slotalign.py` measures. Neither says anything about lineage; the vectors do.
> `asm/patches.py` carries both dumps with their digests and every manifest region names one,
> so switching is a one-line change.

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

## The source is split in two: Eurocom's, and ours

This is a maintainable source tree for the game, not a reproduction of one cartridge. The two
halves are kept apart on purpose:

| | |
|---|---|
| `vendor/Magician-NES/` | **Eurocom's, read-only**, pinned at `bf653a407cd97e4dfdca665063f25d8b44da130a` and byte-identical to it. `git status vendor/` must stay empty. Re-syncable from upstream at any time. |
| `src/magician/` | **Ours.** Everything we author. Assembled by the same assembler (`asm/pds6502.py`), in the same PDS dialect, in the same build. |
| `asm/build.py` → `OUR_MODULES` | The manifest of what we add and where each module goes. A file in `src/magician/` with no entry is a hard error, not a silent no-op. |

**To do QoL work, edit `src/magician/`.** Not `vendor/`. Our modules are assembled *last*, so
every symbol Eurocom defines is resolved when they run and nothing of ours can perturb a vendor
module's layout. `src/magician/README.md` has the details; `src/magician/TITLE.SRC` is a worked
example that replaces the title screen by overlaying one label, with no change to `vendor/` at
all.

The split was proved inert rather than assumed: `TITLE.SRC` first shipped holding Eurocom's own
decoded scene, and the PRG came out byte-identical to a build made before `src/magician/` existed
(`sha1 57fb2365a2783947edb6ce5e5686a5e7957f0a4c` both ways).

One trap before you index anything: `sptxt` (`MISC.SRC:765`) and `spells` (`MISC.SRC:819`) are
the spell name/order/MP tables and they are index-addressed, but **the source's index order is
the source's own** — the release renamed and reordered the spells. Establish the
source-index → release-index mapping before any index-based spell edit, or you will edit a
different spell than you meant to. See `PROVENANCE.md` §8.

## Layout

```
vendor/Magician-NES/   Eurocom's source, vendored, READ-ONLY, pinned bf653a4
src/magician/          OURS. Everything we author; assemble this, not vendor/
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
**78 555 of 131 072 PRG bytes (59.9%) come from the source and are identical to
beta1** — that is the whole of it. `asm/patches.manifest` is empty and fills
nothing; see *Patch manifest* below for why that is a finding rather than a gap.
15 of 32 CHR 4 KiB pages match and 3 623 symbols are recovered. `nmi`, `reset` and
`irq` all land on beta1's vectors exactly, and `reset` is byte-identical to it
for its first 31 instructions.

Against the release — the old target — the same build is 39 832 (30.4%), of which
38 982 is source-only and 864 bytes came from one class-`b` manifest region. The
numbers are kept side by side throughout this file because the difference between
them is the most useful thing in it.

The build prints the byte accounting on every run, and the two lines are not
interchangeable:

```
PRG: 96413/131072 bytes identical to the cartridge (73.6%)
  of which from the source alone : 96413 (73.6%)
  of which from the patch manifest: 0 (asm/patches.manifest has no regions)
```

Only the first is evidence about the source. A rising total is otherwise
indistinguishable from a rising number of hidden bugs — which is exactly why
`src/testing/test_pointer_widths.py` exists: the 59.9% above it was a total that
had risen for the wrong reason, and it rose again for the right one only after
the `dl`/`dh` widths were pinned by a test.

**Three of the eight module placements are pinned, and five are ASSUMED.** The build
prints the whole table with the evidence for each. `X6` is pinned "by
elimination" and `X7` from the cartridge's reset vector; `X0`, `X1`, `X2`, `X3`
and `X4`'s `$8000` part are assumed, and the build labels them
`ASSUMED, NOT MEASURED` on every run. `SEQ.SRC` is at slot 5 on a measured single
hit — its first 24 bytes occur once in 131 072, at file `$0A000` — and 29 of its
position-independent code windows land in slot 5 at offset zero.

**`X5` is now measured too, and was not before 2026-10-04.** It sits in fixed slot
14 at its own `org $c000`, and `tools/slotalign.py --module sql` shows the first
**768 bytes** of PRG slot 14 agreeing with the cartridge exactly — which contains
X5's 256-byte `sql` table at slot-14 offset 0 and nowhere else. So X5's origin
*within* its slot is 0 and is now a measurement. `asm/out/build.log` still prints
`X5.PDS ... ASSUMED, NOT MEASURED`; that line is stale and the placement table in
the same log already counts X4, X6 and X7 as pinned.

**The same run retracted a placement claim in the opposite direction.**
`journal/09` reported one table — the 32-byte ascending tile counter `D43`, at
slot-5 offset `$1471` here and `$14CE` in the cartridge — and concluded "the
placement of X5 within slot 5 is wrong". Both halves were wrong. `D43` is
**SEQ.SRC's** (`mag.sym`: `d43 = $B471`, in MMC3 register 7's window; X5 is slot
14 at `$C000`). And one table gives a *displacement*, not an *origin*: measured
across slot 5 the displacement is piecewise and non-monotone — `+0x00`, `+0x09`,
`+0x0a`, `+0x39`, `+0x5a`, `+0x5d`, `+0x6b`, `+0x75`, `+0x7f`, `+0x81` — carried
by equal runs of 24 to 574 bytes and changed by 45 inserts and 56 deletes inside
the slot. `+0x5d` is the *mode* of 293 single-hit tables because those tables
cluster late in the slot, not because the module starts `$5D` late. Slot 5's
best-agreement shift is **0** (1312 bytes, against 1025 at `+0x5d`) and its first
90 bytes are identical, so SEQ.SRC's origin is 0 and is already what the build
does. See `journal/10`.

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

**Our authored title screen renders, and the proof is in bytes.** A BizHawk 2.11.1 quickerNES run
of our build at frame 60 has a live CIRAM nametable that is **960/960 tile bytes and 64/64
attribute bytes identical to `src/magician/title/nametable.txt` and `attributes.txt`**. That is
the grid in this repository, through our packer, through our overlay on `titdat`, through
Eurocom's own `dotitle` and `unrun`, into the PPU — measured in the emulator's own memory
domain rather than inferred from pixels.

    python3 tools/titletest.py                        # the gate; must pass
    python3 tools/scenerender.py ours /tmp/t.png      # draw the grid
    MAGICIAN_FRAMES=60 tools/bizhawk/run.sh tools/bizhawk/title.lua asm/out/magician-rebuilt.nes

Frame-60 pixel match against the cartridge is **40.8%** overall (23394/57344): rows 0-3 88.2%,
rows 12-19 58.1%, rows 24-29 29.5%, left half 52.8%, right half 28.8%. That number is the weak
one and the nametable comparison is the strong one; the bottom rows match worst because the
cartridge carries two copyright text lines there and our CHR bank has no tiles for them. The two
screens are different revisions of the artwork (`PROVENANCE.md` §8) and no authoring closes that.
`src/magician/title/notes.md` has the full account.

### The tools this added

| tool | what it is for |
|---|---|
| `tools/datcodec.py` | The `.DAT` scene format, decoded and re-encoded, with `selftest` proving a byte-identical round trip of Eurocom's own three files. Start here. |
| `tools/titletest.py` | The gate. Fails if the ROM does not carry our scene, if it will not decode back to the grid, if a named tile has no art, or if the attributes are degenerate. |
| `tools/scenerender.py` | Draws a scene, because a 32x30 grid of hex bytes cannot be looked at. |
| `tools/bizhawk/title.lua` | Dumps nt0, attributes, PALRAM and CHR through memory **domains**. `tools/bizhawk/frame.lua`'s `$2006`/`$2007` poke route silently returns `$00` on this core and reports it as `0 of 1024 non-zero` — a failed read wearing the costume of a measurement. |

### The rebuild as a whole

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

**Where the rebuild is still wrong — CORRECTED 2026-10-03.** The table below was
measured with two harness faults and its conclusion does not survive. It is kept
only so the correction is legible against it. `tools/bizhawk/frames.lua` plus
`tools/bizhawk/regionbisect.py` now snapshot six domains at *every* frame and report the
first frame each one differs:

| region | domain | size | 06's claim | first differing frame | correct figure there |
|---|---|---|---|---|---|
| CHR image | `CHR` | **131072** | 480 / 8192 differ | 0 | **71 558 / 131 072 (54.6%)** |
| nametables | `CIRAM (nametables)` | 4096 | 969 / 4096 | 2 | 661 |
| work RAM | **`RAM`** | 2048 | — | **1** | 1 |
| `WRAM` | `WRAM` | 8192 | **0 differ** | 46 | 8192 |
| OAM | `OAM` | 256 | **0 differ** | 46 | 256 |
| palette | `PALRAM` | 32 | 0 differ | 15 | 24 |

Three claims are withdrawn:

1. **"work RAM, 8 KiB, 0 differ" read an unpopulated domain.** `WRAM` is `$FF` in
   every byte until frame 45 — the "agreement" was `$FF == $FF`. The real work
   RAM is the 2048-byte `RAM` domain, and it differs by **1767 of 2048 bytes** at
   frame 60.
2. **"480 / 8192 CHR bytes" was 6% of the CHR.** The `CHR` domain is 131072 bytes.
   Over all of it, 71 558 differ. "The best constant offset explains 63 of 480,
   which is chance" describes the 8 KiB sample, not the image.
3. **"OAM 0 differ"** — re-measured 2026-10-03 with the guarded harness, OAM has
   **0 of 256** bytes differing at every frame from 0 to 90. `WRAM` has **0 of
   8192** differing at every frame too. The claim that "every domain changes at
   once at frame 46 -- CIRAM 969, OAM 0->256, WRAM 0->8192" does not reproduce and
   is withdrawn: there is no frame-46 memory event.

So **"the game logic is correct and the damage is entirely on the PPU output side"
is withdrawn.** The CPU side differs from frame 1.

**The `RAM` figure was wrong too.** Re-measured 2026-10-03 with the guarded
harness: 396 of 2048 bytes differ at frame 60, and between 282 and 409 at every
frame from 2 to 90. The previously recorded "1767 / 2048" is not the answer at any
frame.

**The first divergent frame is 1, and it is one byte.** `p0`, zero page `$0008`:

| frame | cartridge | rebuild |
|---|---|---|
| 0 | `$F7` | `$F7` |
| 1 | `$00` | `$08` |

`p0` is `x0.pds:371`, "copy of PPU register 0". `reset` (`x7.pds:922`, CPU `$F9C1`
in both images) does `lda #$08 / sta $2000 / sei / ldx #$00 / stx $2001 / cld /
sta p0` — and the cartridge's `reset` at the *same* `$F9C1` does `stx $08` instead,
plus a 5-byte insertion `lda #$40 / sta $A001` at `$F9C7` that the source does not
have. That is a real source-versus-release difference and it is class (b).

**It is not the cause.** A scratch ROM with that one byte changed from `$85` to
`$86` was run in BizHawk and its frame-60 screenshot is identical to the
unpatched rebuild, pixel for pixel. `x0.pds:608` overwrites `p0` with `#$88` on the
way into the game, so whatever `reset` left there does not survive. Frame 1 is a
symptom, not the bug.

**The two builds do NOT allocate zero page differently.** 06 says the cartridge
has `t0` at `$21` and `curchrpal` at `$0180`. It does not: the cartridge contains
our `setmmc3` byte for byte, and that routine names `ldx $0067` (`mapbnk0`) and
`ldx $0002..$0005` (`r2..r5`) at absolute addresses that are the same in both. The
`RAM` domain agrees at runtime. This is the third instance of the same fault --
an address resolved from the wrong image, like `$9D6C`.

**There is no `$2007` write hook.** `event.onmemorywrite` installs and **never
fires** on quickerNES 2.11.1; the callback-object form is rejected by NLua
(`tools/bizhawk/writes.lua`). So the `$2006` diff 06 asks for needs BizHawk's own
trace logger driven over X11 XTEST. Per-frame snapshots are the instrument.

**The CHR artwork differs from the cartridge and is not fixable from source.** Both
ROMs are 128 KiB PRG + 128 KiB CHR-ROM (header `08 10 42 00`) and at frame 0 the
`CHR` domain equals each ROM's own CHR image exactly. The two images have
identical byte multisets — 0 bytes present in one and absent from the other — but
different content at the same offsets. The first eight files are byte-identical
or nearly so; from `60.chr` at `$7000` onward agreement decays to 8% on the
sprite banks. That is a different revision of the artwork in the shipped
cartridge, not a packing order this build got wrong, and the earlier "8/32 pages,
order is wrong" reading was the 8 KiB sample again. It is class (d), it is not
fixable from Eurocom's source, and it must not be "fixed" by pasting cartridge
bytes into `chr.bin`.

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
routine. This is the same shape of difference already recorded for `reset`.

**That insertion is inert, and is not the cause.** It is gated on `bit $06D8` /
`bmi`, three times in the whole cartridge and nowhere else, and a scan of all
262 144 bytes of its PRG finds **zero** writes to `$06D8`. Read out of BizHawk at
frame 60: cartridge `$06D8` = `$00`. So `bmi` is never taken and the cartridge
writes `$2006` and streams `$2007` exactly as the source does. The deferred path
is for a state this ROM never enters.

So the difference is real and it is not the bug. What is left is **not** "the
corruption is on the PPU output side" — that rested on the `$FF` domain and is
withdrawn above. What is left, measured: the nametable first differs at frame 2
and the rebuild's rows are displaced and full of `$08` where the cartridge has
`$00` holes; `$08` is a real tile index in the cartridge, which is what a
*mis-addressed* stream looks like rather than a *wrong-data* one. Everything
changes at once at frame 46 (CIRAM 969 → 4001, OAM 0 → 256, CHR 71 558 → 86 704)
and both images are static thereafter. In the cartridge at frame 46 the MMC3
bank shadows `r0..r7` hold `$0F $28 $38 $30 $0F $2A $3A $30`; in the rebuild `r0`
and `r1` have **never been written** (`$FF`). Those are written by the `fbnk`
fast-bank-select macro, so from frame 46 the two images are in different code, and
the cartridge rewrites 89 772 bytes of CHR where the rebuild rewrites none.

`journal/07-the-headline-evidence-was-ff.md` has the retractions, the bisect, the
`reset` diff, and the next three measurements in order. The first of them: the
release's `nmi0` is **3 bytes longer** than the source's, it writes `$2006` four
times and `$2007` 33 times per frame (`x5.pds:174-190`), and those three bytes
are unaccounted for.

**What the tracer says, and how much of it survives.** `tools/nestrace.py` reports
that at frame 14, cycle 439 272, the `rts` at `$9D6C` pops a destroyed return
address and jumps to `$0000`, which is `brk`, which vectors through `$FFFE` to
`$F9B3` -- the *IRQ* handler -- with `A=$FF`, and `$FF` to `$E001` sets bit 7,
disabling the MMC3 IRQ. That may well be a real defect. It is no longer measured
against an instrument of unknown correctness. Trace the tracer's claim with

    python3 tools/nestrace.py --rom asm/out/magician-rebuilt.nes --frames 300 \
        --halt-at 0xFFCA --last 40

**That one `rts` is not the whole remaining bug, and the claim is withdrawn
entirely.** There is no `rts` at `$9D6C` in either image: in the assembled ROM the
byte there is `$31`, inside a character-code table (`... 2f 29 ff 30 ff 31 32 38
39 2b 2b 3a 3a ...`, `$FF` terminators, `31 32 38 39` spelling "1289"), and no
symbol exists anywhere in `$9D00`-`$9E00`. BizHawk's `emu.disassemble(0x9D6C)`
returns `BRK` for both images because it reads through the `System Bus` domain with
whatever bank is mapped. The address the tracer resolved does not match the ROM it
was given, so the chain cannot be pursued as written. For the record: `tools/nestrace.py` further reports 1 077 scanline IRQs in the rebuild
against the cartridge's 8 107, `sta $2001` 12 times against 198, and a genuine
6502 freeze on `$52` at `$FFCA` by frame 194 while executing animation tables at
`$FFC0` in the fixed bank. A ROM that freezes at `$FFCA` on frame 194 does not
draw a title screen on frame 60 in a correct emulator, so at least one link in
that chain belongs to the tracer. The specific `rts` is re-testable now and is not
re-tested here; the number to beat is the cartridge's own.

Also the tracer's, and equally withdrawn: the stack was 144 bytes deep at the bad
`rts`, and `initdma` parks the DMA queue at `$7E`, so the queue and the main stack
would share page 1 with only `$FF`-`$80` of stack between them. Worth keeping in
mind if a stack fault ever does turn up, but it is not evidence of one.

**The tracer is not the control, and three claims this file used to make about the
*cartridge* are refuted by measurement** -- all of it from the tracer, all of it
after the tracer gained MMC3 interrupts, CHR banking and correct CHR-RAM writes.
Every number in this subsection comes from `tools/nestrace.py` and is superseded by
the BizHawk measurements above; none of it is evidence about either ROM.

* It is not true that both images "end spinning at `$8382: jsr rn / lda $40 /
  bne $8382`". `$8382` is `ptlr`, a player-animation table inside X4
  (`x4.pds:202`), reached only because the MMC3 IRQ never fired. With interrupts
  delivered the cartridge takes NMI every frame, takes 8 107 scanline IRQs in 150
  frames, runs its sound engine, does OAM DMA, scans the joypad, calls
  `setchr`/`movepal`, and ends with `curchrpal` holding
  `0F 28 38 30 0F 2A 3A 30 0F 17 27 38 0F 21 31 30` -- `TIT.PAL` with `$0F` in
  the four backdrop slots `movepal` forces. The palette pipeline works end to end.
* It is therefore also not true that an all-`$0F` palette on the cartridge is
  evidence about anything. **The cartridge renders no picture in the tracer
  either** (55/2048 nametable bytes nonzero, `$2007` only ever aimed at `$3F00`
  and at sixteen bytes per nametable block) -- while in BizHawk the same cartridge
  draws a complete title screen at 20 807 non-zero pixels. Something is missing
  from the tracer on the graphics side. The two candidates the measurements point
  at: there is no APU at all, and `tick` advances the PPU once per instruction so
  vblank is seen up to seven CPU cycles late and sprite-0 hit has no dot. Neither
  is implemented.
* It is not true that the palette being `$0F` is why the screen is black, in the
  sense that fixing the palette would fix the screen. The palette reaches the PPU
  correctly and the nametable is empty.

Byte-match against beta1 is **78 555 / 131 072 (59.9%)**, all of it source-only,
with no manifest regions at all. Against the release — the previous target — the
same build is **39 832 / 131 072 (30.4%)**, of which **38 982 (29.7%) is
source-only** and 864 bytes (0.7%) came from the one class-`b` manifest region,
which turned out to be beta1's absence rather than the source's. That number went *down* from 29.9%'s 39 204 in an earlier
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
* `PPUMASK` is `$FE`, not `$00`, in the tracer's reading -- background and sprites
  enabled with the leftmost column masked. Withdrawn with the rest of the tracer's
  framebuffer: BizHawk's `nes.getdispbackground()` and `nes.getdispsprites()` are
  both true in **both** images, so rendering is on in both, and neither image has
  an all-`$0F` palette. `PALRAM` in the cartridge and the rebuild is
  `0F 28 38 30 0F 2A 3A 30 0F 17 27 38 0F 21 31 30 00 28 38 30 ...`, and those
  first sixteen bytes are identical in both.

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

### The three unaccounted bytes, 2026-10-03

They were never in `nmi0`. `nmi0` is in X5.PDS and, in the cartridge, at `$DBEF`;
the three bytes are in the **NMI trampoline** in the fixed window, which is X7's
`nmi`, and they are `lda $2002` -- present in the source at `X7.PDS:908`, absent
from the release:

    == F9AB: 48        pha           | F9A8: 48        pha   <nmi>   ; identical
    == F9AC: 8A        txa           | F9A9: 8A        txa          ; identical
    == F9AD: 48        pha           | F9AA: 48        pha          ; identical
    == F9AE: 98        tya           | F9AB: 98        tya          ; identical
    == F9AF: 48        pha           | F9AC: 48        pha          ; identical
    +                            | F9AD: AD 02 20  lda $2002  ; DELETION, source only
    == F9B0: 6C 09 00  jmp ($0009)   | F9B0: 6C 09 00  jmp ($0009)  ; identical

`tools/align6502.py` produces this; the alignment is held across 42 byte-identical
instructions that straddle the insertion and continues through `irq` and `reset`.

**This is class (b), not an assembler bug.** The source states the instruction and
`asm/pds6502.py` emitted it correctly; the release removed it after February 1990.
**It is also not the cause of the scrambled nametable**, and aligning `nmi0`
properly across its $112 address difference shows why: 63 instructions
byte-identical, 63 with only an operand moved, and the release's is 10 bytes
longer from a 43-byte insertion plus a 5-byte one against 7 bytes it dropped. The
`$2006` dance is inside the release's insertion -- code the source does not have.

**`PPUCTRL` is `$88` in both images at the end of `nmi0`**, which retires the
"pattern table" theory: ours computes `$F8 & p0 | $88` and the release loads
`$88` outright, and both store `$88`. That is also why patching `reset`'s `sta p0`
to `stx p0` changed nothing.

The rebuild's nametable 0 at frame 60 holds **one unbroken tile counter from `$00`
to `$E5`**, with `$08` where the cartridge has `$00`. All 969 differing bytes are
in nametable 0; `$2400`/`$2800`/`$2C00` are byte-identical.
`journal/08-the-three-bytes-were-never-in-nmi0.md` has the full alignment.

**Both of those are the source working, not a fault.** `tools/unrun.py`
transcribes `unrun` (`X5.PDS:111-127`) literally and runs it offline over the
source's own data:

    TIT.DAT   314 compressed bytes -> 1024 screen bytes   distinct 1   08 x 1024
    PW.DAT    292 compressed bytes -> 1024 screen bytes   distinct 1   20 x 1024
    PAN.DAT    96 compressed bytes ->  256 screen bytes   distinct 1   20 x 256

1024 = 960 tiles + 64 attributes, and 256 for the panel -- the length fields
decode exactly. **The source's title data is a solid fill of tile `$08`**, which
is the 158 bytes of `$08` in the rebuild's nt0 (the cartridge has 3). The
ascending run that follows is the *level* build drawing over the fill, and it is
present on **both** sides: the cartridge's nt0 rows 1-8 are the same unbroken
ascending sequence. `journal/09-pointer-or-increment-is-neither.md` has the rest.

**"Is the pointer wrong or the increment wrong?" -- neither.** `t2`, the
increment the question named, differs at exactly one frame out of 96 and never
again. `p0` (`$08`) is the first byte to differ, from frame 1, and patching the
build to the cartridge's value (`sta p0` -> `stx p0`, one byte at `$F9D1`) moves
the nametable divergence by **zero bytes**: 661 differing at frame 2 and 969 from
frame 3 either way, measured both sides in BizHawk.

**The data needed for this screen is not in the cartridge and not in the source.**
`DAT/TIT.DAT`, `DAT/PW.DAT` and `DAT/MUS/MUS.MUS` do not occur anywhere in the
131 072-byte cartridge PRG; `DAT/PAN.DAT` is present but identical for only its
first 16 bytes. The release's `unrun` is also a different routine, at `$DB08`
with the pointer in `$21/$22` and the `$2007` write gated on `bit $06D8`, where
the source's is at `$DC77` with the pointer in `$13/$14`. So the picture cannot be
made to match the cartridge from this source's data, and this is **not** a
class-(b) manifest fill: class (b) is "the release has bytes at an address the
source lacks", and here the bytes are not there in any form.

Frame 60, unattended, `NES/SaveRAM/` cleared both sides: **44.3 %** of pixels
identical (25 408 / 57 344). Per region: rows 0-3 88.2 %, rows 4-11 44.8 %, rows
12-19 58.1 %, rows 20-23 44.8 %, rows 24-29 29.5 %.

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

