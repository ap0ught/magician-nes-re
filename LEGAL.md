# Legal

## The cartridge is never committed

`roms/` is git-ignored, `.gitattributes` marks `*.nes` binary, and no test, fixture or golden
file in this repository contains cartridge bytes. The ROM is read in place from the path in
`README.md` or from `$MAG_ROM`.

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