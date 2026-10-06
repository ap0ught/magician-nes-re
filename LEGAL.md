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

`vendor/Magician-NES/` is **Eurocom's source code, vendored into this repository as of
2026-10-03.** It was a git submodule until then, pointing at
`https://github.com/ap0ught/Magician-NES.git` (itself a fork of `tkcn568/Magician-NES`), at
commit **`bf653a407cd97e4dfdca665063f25d8b44da130a`**. That commit is recorded here so the
vendored copy can be traced back to the exact upstream state it came from, and so a future
re-sync has a known base.

It was vendored rather than kept as a submodule because the work on this cartridge is
modifying the source — the assembler, the build and the analysis all read it directly, and a
submodule makes "the source we are editing" and "the source upstream has" two different things
at the moment anyone runs a build.

The files are **unmodified**. Do not edit them in place: `pds-text/` is the decoded,
git-ignored form that the build reads, regenerated from the `.PDS` containers by
`tools/pds_extract.py`. A change that needs to survive a re-sync belongs in the assembler, the
build or a documented patch, not in a vendored file.

Shrigley's terms, from the vendored package's own `_READ_ME.TXT`:

> This package is the complete project for the NES version of Magician, released by Taxan Kaga
> in 1991. […] You are free to play with it and do with it as you will, but you are NOT allowed
> to make money out of it or profit in any way, shape or form.

So: study, modify, redistribute the source — which is what vendoring it does. Do not sell it,
and do not sell anything derived from it.

Shrigley asks that links point at <https://shrigley.com/source_code_archive/> rather than at
the files directly, and that the material be kept to educational use. Both are honoured here.
The canonical announcement of the package is that page; the vendored copy came from the
Internet Archive mirror `shrigleysource`, which is where it was retrieved from.

## Derived work in this repository

The assembler, the Rust machine and the tools here are new work by this repository's author and
carry the same terms as the source they read: free to use and modify, not for profit.

## The game

Magician was designed by Eurocom Entertainment Software and published by Taxan Kaga. It is
released for home play only. Nothing here unlocks, decrypts or circumvents anything; the
cartridge is read as a plain 8-bit program.