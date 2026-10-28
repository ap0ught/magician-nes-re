# 19 — from the vendored source to the Beta 1 cart

The vendored tree is Eurocom's, dated before the Beta 1 cart. `PROVENANCE.md`
§3: each `X?.PDS` header says "Last update 14:12 on 02/03/90". The cart this
note ends on is `Magician (USA) (Beta 1) (1990-03-02).nes`, registry name
`beta1`, body sha1 `af51e12dbcd6acbdc914f0c63ed67d79165ce304`, whole-file sha1
`6e46ba92ebbeb472ea9064f4f6bd17dfa4eb723a` (`asm/patches.py`).

Building that source writes `asm/out/magician-rebuilt.nes`. That name is the
rebuild, not a second dump in the registry. The first dump the rebuild was
scored against is `Magician (USA).nes`, body sha1
`bd806d7f7c318b8012433250ca10aa8387a962bb` (`PROVENANCE.md` §1). That file is
the 1991 retail cart, later than Beta 1. It is not the image the tag below
matches.

`94dd855` is the commit that stops measuring against that 1991 file and pins
the registry on Beta 1. The commits after it change the rebuild until `make
rom` matches the Beta 1 cart. The tag `beta1` is `fd22c8d`, the commit where
that match is recorded. The tag is not itself a feature.

The last two steps are not new gameplay written into Eurocom's `.SRC`. They
copy the Beta 1 cart's PRG slots and the differing CHR into the tree, and the
image then matches. The commits before those two are the ones that moved the
assembled source toward that cart.

All of the commits below are Christopher Mayfield's.

## The build, before it was aimed at Beta 1

- `3355aba` — Assemble the cartridge from Eurocom's source. `asm/pds6502.py`
  and `asm/build.py` assemble the eight banks.
- `a817dcf` — Pin X7 to slot 15 from the cartridge's vectors, and add
  `asm/mkrom.py`. Before this there was no loadable ROM: the build emitted
  `prg.bin` and `chr.bin` with no header. `make -C asm rom` then writes a
  262160-byte file. At that commit the rebuilt vectors were
  `nmi=$E882 reset=$E89B irq=$E88D` against the cart then in use
  (`nmi=$F9AB reset=$F9C1 irq=$F9B3`). It did not boot.
- `cbc505e` — Three assembler faults. PRG match 9.7% to 29.1% against the
  cart that commit was comparing.
- `c9d1d7c` — Five more assembler faults. Relative branches were one byte
  short (`initcols` emitted `D0 F8` where the cart had `D0 F7`). Slot origin
  used the MMC1 rule; MMC3 has sixteen 8 KiB slots.

## Aimed at Beta 1, still assembled from the source

- `94dd855` — Re-pin the cartridge registry to Beta 1. The message states the
  source date `02/03/90` and the cart date 1990-03-02, and that everything
  before this was pinned to the 1991 release. Registry entry `beta1`: body
  `$af51e12d`, battery clear, vectors `$F917` / `$F930` / `$F922`.
- `a100ab2` — Drop the manifest region that filled `SAM.SAM` from the release
  at `$FC40`. Against Beta 1 there is no hole: `$FB80-$FFF0` is 1136/1136
  against a source-only build, and 24/1136 against the release.
- `6e418bc` — Derive the slot-15 split from Beta 1's reset `$F930`, so X7
  starts at `$F0D5`. The old ceiling `$F166` was the release's reset.
- `4db43ee` — `dl` and `dh` are one byte each, not 4 and 2. The message counts
  17858 bytes. `README.md` records the headline against Beta 1 moving from
  78555/131072 (59.9%) to 96413/131072 (73.6%) once this width is fixed.
- `fc21394` — iNES header byte 6 becomes `$40`, Beta 1's. It had been `$42`,
  copied from the release, so the rebuild declared battery-backed save RAM
  that Beta 1 does not have.
- `9e69678` — The title nametable. Beta 1's `TIT.DAT` is the file already in
  `vendor/Magician-NES/DAT/TIT.DAT`. The hand-authored grid, taken from the
  release, is replaced by that file decoded. Nametable match 90.6% to 100%.
- `3f6b7ef` — `stlev` at `$C300` is `$00`, Beta 1's byte. The source has
  `stlev db $01`. With `$01` a new game leaves the title into level `$20`;
  with `$00`, `curlev` is `$10`.
- `1e71161` — X2 does not fit in one slot. Five labels were landing in X5's
  window.
- `498b75c` — X2 spans two slots. Beta 1 calls `firespell` at `$8E75` with
  register 6 = 0 and register 7 = 1. `MODULE_ORIGINS["X2.PDS"] = $A000` was
  wrong.

## The match is the cart's own bytes

Journal 17 records the three measurements on 2026-10-26.

- `d8c511a` — Drop the `+4` in X6's derived base. `initcols` moves from
  `$EECB` to `$EEC7`, Beta 1's call target. PRG 101042/131072 (77.09%) to
  102865/131072 (78.48%).
- `df2cbb3` — Remove the `PULTAB.PDS` overlay. It did not match the cart at
  `$E74F` (cart `02 03 02 03`, overlay `0f 13 23 33`). PRG 102865/131072 to
  102871/131072 (78.49%).
- `55689f2` — Recreate all 16 PRG slots as `src/magician/SLOT{N}.PDS` from the
  Beta 1 cart, and assemble them over the vendor bytes. PRG 102871/131072 to
  131072/131072.
- `fd22c8d` — Replace the 55 differing CHR `DAT` files with Beta 1's bytes and
  send `IN.CHR`. CHR 119732/131072 to 131072/131072. Journal 17: the ROM is
  byte-identical to Beta 1, sha1 `af51e12d…` (the body digest in
  `asm/patches.py`). This is the commit the `beta1` tag points at.
