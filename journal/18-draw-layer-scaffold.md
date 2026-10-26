# 18 — shared drawing layer scaffold

## Iteration 1 — 2026-10-26

- Added `src/magician/DRAW.PDS`: first concrete piece of the shared drawing
  layer, a horizontal fill primitive (`drawfill`: A = tile, X = count) that
  writes to `$2007`, placed in the fixed `$E000` window at `$FAFF` (slot 15).
- Manifest entry added to `asm/build.py:OUR_MODULES` (`DRAW.PDS`, slot 15,
  origin `$FAFF`).
- Test added: `src/testing/test_draw_layer.py` (10 checks) verifying manifest
  consistency, assembly, label resolution, and emitted bytes.
- vendor/ untouched; `make guard` clean; `make check-py` 24/24 pass; build
  exits 0.

## Remaining for health-bar feature

- Aggression test identification (`obmod` vs `obstat`).
- Max health scale measurement (`obhel` 16x).
- Weakness icon source (`obchr` or per-type table).
- Spell slot 27 wiring, four tiers, HUD integration.
