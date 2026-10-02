# Magician (NES) — moddable core

A from-scratch Rust reconstruction of the machine **Magician** (Taxan Kaga, 1991) runs on,
built around the original Eurocom source that Chris Shrigley released in 2012, plus a
source-level rebuild of the cartridge.

Three goals, in the order they pay off:

1. **Rebuild the cartridge from the original source** (`asm/`). Eurocom's PDS 1.26 assembly,
   the binary level data and the CHR artwork, assembled by a PDS-compatible assembler written
   here. Gives a buildable ROM with real symbol names instead of a disassembly — the basis for
   every hack and for the two goals below.
2. **A moddable core** (`crates/`). 6502 + software PPU + MMC3, verified frame by frame against
   the real cartridge, with traps that replace cartridge routines address by address. Same shape
   as `~/code/z2rs`.
3. **Co-op and an AI player.** A second player on a shadow machine, and a bot that plays the
   game. Same shape as `~/code/games/aibeatszelda`.

## The cartridge this is built against

Exactly one dump, pinned by digest in `crates/mag-core/src/rom.rs` next to every address that
depends on it:

| | |
|---|---|
| file | `/extdrive/backups/SHARE/roms/nes/Magician (USA).nes` |
| size | 262160 bytes (16-byte iNES header + 128 KiB PRG + 128 KiB CHR) |
| mapper | 4 (MMC3), battery-backed PRG RAM, horizontal mirroring |
| body SHA1 (header stripped) | `bd806d7f7c318b8012433250ca10aa8387a962bb` |
| body CRC32 | `91E2E863` |

Two dumps of this title exist on this machine. `Magician (USA) (Beta).nes` has the **same
header shape** — same mapper, same 128 KiB PRG, same 128 KiB CHR, same mirroring — and a
different body (body SHA1 `2a0a444d…`, 23 545 differing bytes). Both boot to the same screens.
Nothing in the toolchain can tell them apart, so every address, every label from the source and
every replayed input applies to exactly one of them: the release, pinned above. See
`PROVENANCE.md`.

The cartridge is the user's own. It is never committed; see `LEGAL.md`.

## The source

`vendor/Magician-NES` is a git submodule pinned to Chris Shrigley's release
(`ap0ught/Magician-NES`, forked from `tkcn568/Magician-NES`). It holds eight `X?.PDS` project
files, seven `.SRC` include files, the level data under `DAT/`, the message text under `TXT/`
and the CHR artwork.

The `.PDS` files are binary containers produced by the 1989 Atari ST toolchain PDS 1.26.
`tools/pds_extract.py` decodes them to plain text into `pds-text/` (git-ignored, regenerated).
13 104 lines of source, with every routine, RAM variable, macro and data format named.

## Layout

```
vendor/Magician-NES/   upstream source (submodule)
tools/pds_extract.py   PDS container -> plain text
tools/                 build and analysis helpers
asm/                   PDS-compatible assembler + cartridge build
crates/                the Rust machine and front ends
pds-text/              extracted source (generated)
roms/                  the cartridge (git-ignored, never committed)
```

## Build

```sh
python3 tools/pds_extract.py          # decode the PDS containers
make -C asm                           # assemble the cartridge from source
cargo build                           # the Rust machine
```