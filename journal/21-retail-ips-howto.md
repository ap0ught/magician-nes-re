# 21 — How to rebuild the retail IPS

`ips/retail-modules.ips` is the difference between Magician (USA).nes and `make rom` on `baseline/release-ips`. This note is the procedure. Which modules were placed, and which were not, is `journal/20-retail-ips.md`.

## Where you run it

The tool is `tools/retail_ips.py` on branch `baseline/release-ips` (`1b87408`). Its parent is tag `release` (`703392e`), also checked out as `baseline/release`. On that parent, `make rom` writes a ROM whose full-file sha1 is `7b0da149c3892b9aec3e97400b79e1089cb8cc47`, the same digest as Magician (USA).nes. The tag does not contain `tools/retail_ips.py` or `ips/retail-modules.ips`. This branch is the next commit: the seven module placements, then the IPS of those bytes.

Run the commands in the checkout that has the tool. That checkout may be a separate worktree from the one you have open. A tree on `fix/boot-from-source`, or a checkout of tag `release` by itself, does not have these files.

## The cart has to be on disk

The cartridge is not in git. `LEGAL.md` says the cart file itself is never committed. `.gitignore` excludes `*.nes` and `roms/`, and `asm/out/` is generated, so neither Magician (USA).nes nor `asm/out/magician-rebuilt.nes` is a committed file. The IPS is the file this procedure produces for the tree.

`asm/patches.py` sets `DEFAULT_CART = "release"`. That registry entry is the file `Magician (USA).nes` under `CART_DIR` (`/extdrive/backups/SHARE/roms/nes`). `make rom` takes the path from `python3 tools/cartref.py` and passes it as `--cart`. On this branch that prints:

    /extdrive/backups/SHARE/roms/nes/Magician (USA).nes

`tools/retail_ips.py` does not read `DEFAULT_CART`. `emit` and `check` open that same path from the `CART` constant in the script. They require the full-file sha1 `7b0da149c3892b9aec3e97400b79e1089cb8cc47`, which is `CARTS["release"]["sha1_full"]`. The registry's `sha1` field is the body digest with the header stripped (`bd806d7f7c318b8012433250ca10aa8387a962bb`). Same file, different number. `emit` and `check` take no cart argument. A missing file or a different sha1 is an exit.

The header has to match the cart before any module byte is considered. `asm/mkrom.py` sets iNES header byte 6 to `$42`, the retail cart's own byte (battery bit set). Tag `beta1` keeps `$40`. Placements in the script are PRG offsets. The file offset is that offset plus `HEADER` (16). The 16 header bytes are outside every placement, so a header that differs from the cart makes `emit` exit with `bytes differ outside the placed modules`.

## Commands

From the root of `baseline/release-ips`:

    make rom
    python3 tools/retail_ips.py emit
    python3 tools/retail_ips.py check

`make rom` runs `assemble`, then `asm/mkrom.py`. It writes `asm/out/magician-rebuilt.nes` and stops. It does not write `ips/retail-modules.ips`.

`emit` does not run the build. It reads the cart and the ROM `make rom` already wrote. The two files have to be the same length. Every byte that differs has to fall inside `PLACEMENTS` in `tools/retail_ips.py`. Any other difference exits. Inside a placement, a byte that already equals the cart is omitted, so one module is often several IPS records. The patch is written to `ips/retail-modules.ips`.

`make rom` prints a fixed note that the rebuild is not expected to be byte-exact against the target's body sha1. On this branch that is true: the ROM is the cart plus the placed modules. The line is not the IPS check.

`check` reads the cart, the rebuilt ROM, and `ips/retail-modules.ips`. It applies the patch in memory and requires those bytes to equal `asm/out/magician-rebuilt.nes`. It does not write a `.nes`. It then builds the delta again and requires the file on disk to be that patch, so a hand-edited IPS fails while the ROM is unchanged. A passing run prints the cart sha1, the patched sha1, and the hunk count.

The third subcommand is `python3 tools/retail_ips.py apply <ips> <src.nes> <dst.nes>`. It writes `<dst.nes>` and prints that file's sha1. Producing `ips/retail-modules.ips` does not use it. With no subcommand, or with anything other than `emit`, `check`, or `apply` plus those three paths, the script prints its usage and exits.

## What passing looks like

`check` exits 0. The bytes it applied are the file `make rom` wrote. Last measured on this tree:

    cart sha1 7b0da149c3892b9aec3e97400b79e1089cb8cc47
    patched sha1 ef8bb81bd6da2af0ee4b82671a636ba3a506e8eb
    hunks 23 bytes 662

`emit` on that same pair writes `ips/retail-modules.ips` (785 bytes on disk) and prints the patched sha1. The 662 is the sum of the record payloads. The 23, the 662, and both sha1s belong to these placements. They change when a placed module's bytes change.

## What the patch contains

`PLACEMENTS` is the list `emit` will accept. The offset is a PRG offset. The length is the window, not the number of bytes that land in the IPS.

| module | PRG | length |
| --- | --- | --- |
| `INSPTITL.PDS` | `$0056` | 3 |
| `INSPBODY.PDS` | `$19C8` | 548 |
| `DRAW.PDS` | `$1FBC1` | 11 |
| `AGGRO.PDS` | `$1FBCC` | 27 |
| `HEALTH.PDS` | `$1FBE7` | 9 |
| `WEAK.PDS` | `$1FBF0` | 25 |
| `INSPECT.PDS` | `$1FFA0` | 56 |

`INSPHOOK.PDS`, `SPNAME.PDS`, and `SPSLOT.PDS` are absent from that tuple, and they are absent from `asm/build.py`'s module list. The comment there says their Beta 1 sites are not the retail instruction, and no free byte is the thing they overwrite. The measured sites are in `journal/20-retail-ips.md`. `emit` has no search for another hole. A differing byte outside these seven windows is an error, so running `emit` again does not invent homes for those three modules.

`src/magician/INSPECT.PDS` still says `jsr tickmana` at `inspdraw`. The bytes the build expects there are `20 56 F3` (`jsr $F356`), the source symbol. `farcall` (`$F8F7`) and `cartinit` (`$F93C`) are the retail entries this file retargets; `tickmana` is not one of them. The hook that would call `inspdraw` was not placed, and journal 20 records that nothing in the retail image calls `inspdraw`. The IPS does not make Inspect run on the retail cart.

## When to emit again

After a placed module's bytes change, run `make rom`, then `emit`, then `check`. `make rom` leaves `ips/retail-modules.ips` where it was. The next `check` then fails, either because the applied patch is not the rebuilt ROM or because the file on disk is not the delta of the two files.

Leave the `.nes` files where they are. The cart stays at the path above. The rebuilt ROM stays under `asm/out/`.
