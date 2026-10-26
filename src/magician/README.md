# `src/magician/` — our source

Everything in this directory is **ours**. Everything in `vendor/Magician-NES/` is
**Eurocom's**, is read-only, and is pinned at `bf653a407cd97e4dfdca665063f25d8b44da130a`
so it can be re-synced from upstream at any time. If you want to change the game,
change it here. That is the whole point of the split.

Both trees are assembled by the same assembler (`asm/pds6502.py`) in the same PDS
dialect, from one `asm/build.py`, so a change we make is a source edit rather
than a patch applied to someone else's file.

## What is where

| | |
|---|---|
| `vendor/Magician-NES/` | Eurocom's released source. **Do not edit.** `X0.PDS`–`X7.PDS` are PDS containers; the `.SRC` files and `DAT/` (art, palettes, packed scenes) are plain files. Pinned, byte-identical, `git status` must stay clean. |
| `pds-text/` | The same containers decoded to text, by `tools/pds_extract.py`. Generated, git-ignored. Line numbers in comments refer to these. |
| `src/magician/` | **Ours.** Assembled after the vendor tree, so every symbol Eurocom defines is already resolved when we run. |
| `asm/build.py` | The build. `OUR_MODULES` is the manifest of our modules and where each one goes. |
| `asm/out/ours/` | Generated packed scene data. Git-ignored, rebuilt every time. Never committed. |

## The manifest, and why it is not a glob

`asm/build.py` has `OUR_MODULES`, which names each of our files and where it goes.
`check_our_manifest()` makes it an error to have a file in `src/magician/` that the
manifest does not list, or a manifest entry with no file. A glob would mean that
dropping a file in here silently changes the ROM with no record of why — the same
class of quiet change as a module assembled at a slot nobody chose. A module with
no entry has no known placement, and a placement is something that has to be argued
for.

To add a module: write it, add it to `OUR_MODULES` with its slot/origin, and check
that `git status vendor/` is still empty.

## How our modules get placed

Our modules are assembled **last**, in the same project pass as Eurocom's, for two
reasons:

* Every symbol the vendor tree defines is already known, so our code can name
  `titdat`, `dotitle`, or any of the 40 macros in `X0.PDS`.
* Nothing of ours runs while the vendor modules are being placed, so a module of
  ours cannot perturb a vendor module's layout. It can only overwrite bytes at
  addresses it names on purpose.

## `TITLE.SRC` — overlaying rather than patching

`src/magician/TITLE.SRC` is one `org` and one `incbin`:

        org titdat
titscn  incbin ..\..\asm\out\ours\TIT.DAT

`titdat` is Eurocom's own label. `X7.PDS:997` says `titdat load tit.dat`, and
`X0.PDS:624-625` does `lda #<titdat / ldx #>titdat / jsr unrunscn`. So the title
screen is described **entirely** by the packed bytes at `titdat`, and replacing
those bytes replaces the screen. No call is added, no label moves, and not one
line of `vendor/` changes.

The overlay has no slack. `X7.PDS:997-999` lays the three scene blobs end to end —
`titdat`, `pwdat`, `pandat` — so there are exactly 314 bytes between `titdat` and
`pwdat` and not one spare. A scene that packed one byte longer would put its tail
on top of the password screen's data and fail as *a wrong password screen*, not as
a failed build. So the build measures the budget from the two symbols the
assembler actually resolved, refuses to go over it, and then reads the finished
image back to confirm the ROM really carries our scene:

    scene fits: 314 of the 314 bytes between `titdat` ($ABA7) and `pwdat` ($ACE1)
    round trip through the image: 1024 bytes decode back to the grid in src/magician/title/

## `title/` — the readable source

The scene descriptor is a run-length stream with a four-byte minimum run
(`tools/datcodec.py` has the derivation and the proof). Hand-editing packed bytes
is not a thing anyone should do, so **the packed stream is generated and the grid
is the source**:

| file | what it is |
|---|---|
| `title/nametable.txt` | 32 rows × 32 hex bytes. The screen. |
| `title/attributes.txt` | 4 rows × 16 hex bytes, the 64 attribute bytes at `$23C0`. |
| `title/token.txt` | The stream's token byte, `$E0`. |
| `title/notes.md` | What the screen is, and how the layout was arrived at. |

`asm/build.py:pack_our_scenes()` reads the grid, packs it with
`tools/datcodec.py`, writes `asm/out/ours/TIT.DAT`, and the module incbins that.
Nothing packed is committed, so there is exactly one copy of the title screen and
it is the readable one.

To move a tile, edit `nametable.txt` and rebuild. `make check` fails if the PRG
moves when it should not; `tools/titletest.py` fails if the grid names a tile the
art does not have.

## Proving the wiring changed nothing

`TITLE.SRC` was landed holding Eurocom's own decoded grid first. Our packer turned
that grid back into Eurocom's 314 bytes exactly, the module overlaid them at
`titdat`, and the PRG came out **byte-identical** to a build made before
`src/magician/` existed. That is the only way to know the wiring itself is inert
rather than quietly rewriting something: a live module, a real assembly, a real
overlay, and no change in the output.

    sha1(prg.bin) before src/magician/ existed
      57fb2365a2783947edb6ce5e5686a5e7957f0a4c
    sha1(prg.bin) with src/magician/TITLE.SRC assembling Eurocom's own grid
      57fb2365a2783947edb6ce5e5686a5e7957f0a4c

`python3 asm/build.py --no-ours` skips our tree entirely, which is the other half
of the check.

## Editing Eurocom's code without editing Eurocom's files

`vendor/` stays pristine, so a change to existing behaviour is made by overlaying
the label, the way `TITLE.SRC` does, or by adding a module of our own that the
build places alongside. Two things to keep in mind:

* **The spell tables are the trap.** `sptxt` (`MISC.SRC:764-800`) and `spells`
  (`MISC.SRC:819+`, "packed spell rune nybbles") are the spell name, order and MP
  tables, and the source's *index order is the source's own*. The release renamed
  and reordered the spells — the source has `SCARY`/`RAZORSTORM`/`KISS MY AXE`/
  `FIRE FOUNTAIN`/`POWER SHIELD`/`HEAL`/`ANTI VEN`/`MUZAK` where the release has
  `SPEAR`/`BOOMERAXE`/`FIRE RING`/`FIRE SPRAY`/`POW SHIELD`/`MEDITATE`/
  `SOUND TEST`. So **any index-based edit to a spell needs the source-index →
  release-index mapping established first**, or the change lands on the wrong
  spell. This is called out again in `PROVENANCE.md`.
* Nothing here may contain cartridge-derived bytes. The cartridge is read-only,
  never committed, and no hex blobs, base64 or `incbin` of it belong in this tree.
  See `LEGAL.md`.