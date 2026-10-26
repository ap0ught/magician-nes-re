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

## Iteration 2 — 2026-10-26: the honest placement check

The scaffold's claim to "place" DRAW.PDS at $FAFF was **wrong**. Measured:

- `make` rebuilt and `asm/build.py` reported DRAW.PDS "11 byte(s) written
  $1FAFF-$1FB09", while the finished `out/prg.bin` read `FF x11` at those
  offsets. The module was assembled and then **overwritten**: `our_modules()`
  sorted `DRAW.PDS` alphabetically in front of `SLOT15.PDS`, and the full-slot
  recreate wrote Beta 1's bytes back over it. `cmpbank.py bank` kept showing
  131072/131072 identical, because both sides carry Beta 1's own `$FF`
  end-of-code padding at $FAFF-$FB7F. A 100.00% match over a module that was
  not in the ROM.
- The "Beta 1 holds real non-zero bytes at $FAFF-$FB7F" fear, from the same
  authority, is also measured false. `cmpbank.py runs --kind missing` prints
  nothing for the fixed window: the ONLY >=16-byte `$FF` run in Beta 1's
  $E000-$FFFF window is exactly $FAFF-$FB7F (129 bytes), and X7.PDS:1716-1719
  pads the same range with `$00` in a build whose pads run after ours. It is
  genuine cartridge padding, so $FAFF is a sound placement; the size of the
  claim was the problem, not the address.

Fixes:

- `asm/build.py`: `OUR_SLOT_RECREATES` + a reordered `our_modules()` — slot
  recreates assemble FIRST, everything ours that adds to what they wrote
  after. A recreate erasing an overlay is the mechanism; the fix is the role
  rule, not a rename.
- `asm/build.py`: DRAW.PDS gets an `expect_bytes` readback (STARTLEV.PDS's
  `expect` idea extended to code): the finished image must hold the 11 placed
  bytes `E0 00 F0 06 8D 07 20 CA D0 FA 60` at the recorded `drawfill` entry.
  Simulated the iteration-1 clobber against it — SystemExit fired with the
  two byte strings.
- `src/testing/test_draw_layer.py`: 15 checks now (was 10): manifest bytes ==
  assembly output == final readback; disassembling the placed $FAFF gives
  `cpx #$00 / beq $FB09 / sta $2007 / dex / bne $FB03 / rts`; and the
  recreate-before-overlay order is pinned.
- `src/testing/test_repo_hygiene.py`: the "matches beta1 byte for byte"
  assertion moved to a derived sha (Beta 1 except the placed bytes recorded in
  OUR_MODULES). Whole-file equality to Beta 1 would once more have meant the
  code vanished silently.

Match accounting, WITH DRAW.PDS in the build:

|                 | before (clobbered) | after (placed) |
|-----------------|-------------------:|---------------:|
| cmpbank bank    | 131072/131072      | 131061/131072  |
| differ vs Beta1 | 0 bytes            | 11 bytes, `$FAFF-$FB09` only |
| nz delta        | 0                  | 1 (our `cpx #$00` immediate against Beta 1's `FF`) |

The only differ bytes are the module's own placed bytes. Everything else is
byte-identical to Beta 1.

## Remaining for health-bar feature

- Aggression test identification (`obmod` vs `obstat`).
- Max health scale measurement (`obhel` 16x).
- Weakness icon source (`obchr` or per-type table).
- Spell slot 27 wiring, four tiers, HUD integration.
