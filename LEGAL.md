# Legal

## The cartridge is never committed

`roms/` is git-ignored (and does not exist), and `.gitignore` excludes `*.nes`
repository-wide, so no test, fixture or golden file can pick up cartridge bytes by
accident. There is no `.gitattributes`; an earlier version of this file claimed one
marked `*.nes` binary, which was not true.

The ROM is read in place from the path recorded in `README.md`, which is
`asm/build.py --cart` with that value as its default. **There is no `$MAG_ROM`
environment variable** — an earlier version of this file mentioned one and no code
in this repository reads it.

The cartridge is the user's own. It stays on this machine.

## The source

`vendor/Magician-NES` is a git submodule pointing at Chris Shrigley's public release of the
Eurocom source code. It is not vendored into this repository, only referenced. Shrigley's own
terms, from the package's `_READ_ME.TXT`:

> This package is the complete project for the NES version of Magician, released by Taxan Kaga
> in 1991. […] You are free to play with it and do with it as you will, but you are NOT allowed
> to make money out of it or profit in any way, shape or form.

So: study, modify, redistribute the source. Do not sell it, and do not sell anything derived from
it.

## Derived work in this repository

The assembler, the Rust machine and the tools here are new work by this repository's author and
carry the same terms as the source they read: free to use and modify, not for profit.

## The game

Magician was designed by Eurocom Entertainment Software and published by Taxan Kaga. It is
released for home play only. Nothing here unlocks, decrypts or circumvents anything; the
cartridge is read as a plain 8-bit program.