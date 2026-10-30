# 20 — MagicianEdit editor port (pointer)

Editor-facing research for the RobinEdit → MagicianEdit port lives in the sibling repo, not duplicated here:

**https://github.com/ap0ught/magicianedit/tree/main/journal**

| MagicianEdit journal | This tree's related notes |
|----------------------|---------------------------|
| `10-scaffold-dump-policy.md` | Cart pin / Beta 1 — `asm/patches.py`, journal `19` |
| `11-dat-packs-mmc3.md` | `GAPMAP.md`, `vendor/Magician-NES/DAT/`, MMC3 slot notes in README |
| `12-map-blk-col-pal.md` | `pds-text/x1.pds` `initlev`, `tools/scenerender.py`, `tools/datcodec.py` (scenes ≠ maps) |
| `13-level-chr-object-pointers.md` | `src/play/ram.py`, `convind`/`newlev`, journal `18` |
| `14-objects-events-shops.md` | `MISC.SRC` `obchars`, `OB2.SRC`, `PROBS.SRC`/`PROBDAT.SRC`, `SHOPDAT.SRC`, `tools/spelltab.py` |
| `15-play-ci-releases.md` | `tools/bizhawk/`, journals `05`, `12` |

MagicianEdit must not commit `.nes` dumps. Prefer **Beta 1** as the first editor pin (`DEFAULT_CART` in `asm/patches.py`).
