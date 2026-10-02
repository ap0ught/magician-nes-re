# Magician (NES) — moddable core

A Rust reconstruction of the machine **Magician** (Taxan Kaga, 1991) runs on, intended
to be built around the original Eurocom source that Chris Shrigley released in 2012,
plus a source-level rebuild of the cartridge from that source. **The Rust core has not
been started** (`crates/` does not exist); what exists is the rebuild, and it does not
yet assemble completely. See *Status*.

Three goals, in the order they pay off:

1. **Rebuild the cartridge from the original source** (`asm/`). Eurocom's PDS 1.26 assembly,
   the binary level data and the CHR artwork, assembled by a PDS-compatible assembler written
   here. Intended to give a working ROM with real symbol names instead of a disassembly — the
   basis for every hack and for the two goals below. Not there yet: six of eight modules have
   no determined slot and the rebuild does not boot.
2. **A moddable core** (`crates/`). 6502 + software PPU + MMC3, verified frame by frame against
   the real cartridge, with traps that replace cartridge routines address by address. Same shape
   as `~/code/z2rs`.
3. **Co-op and an AI player.** A second player on a shadow machine, and a bot that plays the
   game. Same shape as `~/code/games/aibeatszelda`.

## The cartridge this is built against

Exactly one dump, pinned by digest. **Not enforced in code** — nothing in `asm/`
or `tools/` compares the cartridge against it; `asm/mkrom.py:65-66` only prints it in
a message. The pin is recorded here and in `PROVENANCE.md`, and the place it
belongs is wherever the cartridge is first read for real use:

| | |
|---|---|
| file | `/extdrive/backups/SHARE/roms/nes/Magician (USA).nes` |
| size | 262160 bytes (16-byte iNES header + 128 KiB PRG + 128 KiB CHR) |
| mapper | 4 (MMC3), battery-backed PRG RAM, horizontal mirroring |
| body SHA1 (header stripped) | `bd806d7f7c318b8012433250ca10aa8387a962bb` |
| body CRC32 | `91E2E863` |

Two dumps of this title exist on this machine. `Magician (USA) (Beta).nes` has the **same
header shape** — same mapper, same 128 KiB PRG, same 128 KiB CHR, same mirroring — and a
different body (body SHA1 `2a0a444d…`, 23 545 differing bytes). Whether they reach the same
screens was not re-tested on 2026-10-02; the earlier observation was made with a resumed
battery-backed SRAM save (see *Status*), so treat it as unverified. Nothing in the toolchain
can tell the two apart by header, so every address, every label from the source and every
replayed input applies to exactly one of them: the release, pinned above. See
`PROVENANCE.md`.

The cartridge is the user's own. It is never committed; see `LEGAL.md`.

## The source

`vendor/Magician-NES` is a git submodule pinned to Chris Shrigley's release
(`ap0ught/Magician-NES`, forked from `tkcn568/Magician-NES`). It holds eight `X?.PDS` project
files, seven `.SRC` include files, the level data under `DAT/`, the message text under `TXT/`
and the CHR artwork.

The `.PDS` files are binary containers produced by the 1989 Atari ST toolchain PDS 1.26.
`tools/pds_extract.py` decodes them to plain text into `pds-text/` (git-ignored, regenerated).
6 351 lines of source, with every routine, RAM variable, macro and data format named.

## Layout

```
vendor/Magician-NES/   upstream source (submodule)
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
make -C asm                           # assemble the cartridge from source
make -C asm rom                       # wrap the result in an iNES header
make -C asm check                     # the same, failing if the output moved
make -C asm verbose                   # per-file incbin trace
make -C asm clean                     # remove asm/out/
python3 tools/pds_extract.py          # decode the PDS containers into pds-text/
```

**The build does not succeed, and that is deliberate.** Six of the eight modules
have no determined 8 KiB slot, so they are not assembled at all, and the build
exits non-zero:

```
*** INCOMPLETE BUILD: 6 of 8 modules have no determined 8 KiB slot and were NOT assembled:
***   X0.PDS, X1.PDS, X2.PDS, X3.PDS, X4.PDS, X5.PDS
***   The PRG above is missing them. Pass --allow-incomplete to exit 0 anyway.
```

That is `python3 asm/build.py` exiting 1. **As the working tree stands you do not
reach that message at all** — an uncommitted change to `asm/pds6502.py` raises
first, and `make -C asm` exits 2 instead. The message above is what you get with
that one change set aside, and it is the behaviour the build is written to have.
See *Status*.

`--allow-incomplete` makes it exit 0 anyway, for a diagnostic run only.
Before this policy existed, an undetermined slot silently fell back to whatever
slot the sixteen-slot search happened to end on — slot 15 — so six modules were
assembled into one 8 KiB window, overwrote each other, and produced a ROM that
loaded and showed a black screen. A hole that prints is better than a guess.

Two consequences worth knowing before reading the output. `asm/out/prg.bin` is
missing six banks, and because X0 defines the whole zero-page and RAM map, the
project pass cannot resolve anything the other modules reference and reports a
long list of undefined symbols. And `make -C asm extract` does not work: the
Makefile runs it from `asm/`, while `tools/pds_extract.py` defaults `--src` to a
path relative to the repository root, so it exits 2. Run it as above.
`make -C asm check` and `make -C asm rom` both depend on a successful build, so
they fail for the same reason until the modules are placed.

The cartridge is read, never written, and never committed. Where the rebuilt
image and the cartridge do agree, the build reports by how much rather than
failing on the difference: this source is a February 1990 development build and
the cartridge is a later one, so exact agreement is not the target — a
characterised difference is. See `PROVENANCE.md` sections 3 and 6 for what
currently matches, what does not, and the measurements behind both.

## Status

**The rebuild does not boot, and does not currently build.** Two separate things
are true and both matter:

- *The last uncommitted change to `asm/pds6502.py` does not compile the project.*
  It raises `unknown instruction '0,1'` at `X7.PDS:184` and `make` exits 2
  before the incomplete-build message can print. The change fixes `X4.PDS:160`
  and breaks X7, and the two failures are mutually exclusive — see
  `journal/01-six-modules-with-no-slot.md`.
- *With that change set aside*, six of eight modules are unplaced, the build
  exits 1, and 886 symbol names are recovered rather than the 3 028 that figure
  came from — those 3 028 were recovered while six modules were still being
  stacked into slot 15.

`asm/out/magician-rebuilt.nes` loads in BizHawk 2.11.1 and reports mapper 4, but
shows a black screen: its reset vector is `$E89B` where the cartridge's is
`$F9C1`. That comparison is the clearest single measurement of how far the
rebuild is from the shipped build. Note that the cartridge has battery-backed
PRG RAM, so any "does it boot" comparison must clear SRAM on both sides or
BizHawk resumes a stale save and shows you last session's game instead.

`crates/` has not been started, and nothing in this repository depends on it.