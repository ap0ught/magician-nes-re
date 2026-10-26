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

## Iteration 3 — 2026-10-26: the aggression predicate

Issue #3's next question — "is this object aggressive right now?" — is
answered by the game itself, and the two candidate fields named in the last
iteration both turned out **wrong**, by measurement:

- `obmod` (X0.PDS:543) is the movement/animation mode index fed to the per-type
  mode tables (X1's sprite-scroll path); it starts at 0 and cycles while a
  monster idles. Fresh trolls and fresh peons share modes; it is not
  hostility.
- `obstat` (X0.PDS:544) is a state machine for the interaction *sequence*
  (move / interact / say-line counters), not a property of the monster.

The real field is **`obchr`** (X0.PDS:552), the per-object copy of the
characteristics table X6.PDS:516-549 (`actob`) loads from `obchars`
(MISC.SRC:1425, 44 entries) at activation. X4's interact handler (X4.PDS:353
upwards) reads obchr's attack-control field from the copy — which is why it
tests bits `@3,@2` (`and #$0C / cmp #$0C`) to see `oc_attak`, then bits
`@1,@0` (`and #$03 / bne`) to see the count: ctrl==3 AND cnt==0 means attack,
everything means something when counted through the `oc_*` equates
(MISC.SRC:1405-1424), and `obmod`/`obstat` never appear in that chain.

Placed in this iteration:

- `src/magician/AGGRO.PDS` — `obaggro` — at **$FB0A**, 27 bytes, directly
  after `drawfill`, immediately following DRAW.PDS's 11 bytes at $FAFF. It
  takes X = object index (0..maxob-1) and returns A = 1/0 the way the
  interaction handler answers the same question: bit 7 of `obtyp` (unused
  object) says "no" first, then the obchr ctrl/cnt pair as above, with
  A/X/Y no other register. Its `expect` readback is
  `BD F4 04 30 13 BD 30 05 29 0C C9 0C D0 0A BD 30 05 29 03 D0 03 A9 01 60
  A9 00 60` at entry `obaggro` = $FB0A.
- `asm/build.py`: `AGGRO.PDS` sits immediately after `DRAW.PDS` in the
  manifest, with a `verify()`: the manifest and the final image must both
  match the 27 bytes in `expect` and the same-placed `obaggro` address — a
  verify failure is an error, not a warning. A test runs everything with
  both modules enabled: `set_ours_overrides()` then `manifest()` — the
  predicate is included in the build by default.
- `src/testing/test_aggression.py` — 13 checks. It is not enough to read the
  bytes back; the check executes them. checks 1-6: module present, listed in
  OUR_MODULES, manifest clean, recorded readback, assembly, label resolution
  (asm/out/mag.sym when a build is there). checks 7-8: manifest bytes ==
  assembly output, and the placed bytes disassemble to
  `lda obtyp,x / bmi unused / lda obchr,x / and #$0C / cmp #$0C / bne no /
  lda obchr,x / and #$03 / bne no / lda #$01 / rts / lda #$00 / rts`. check
  9: vendored obchars (44 rows) parses into per-type bytes and pins the 14
  byte-true types (`$05 mushroom, $09 troll, $0C spectre, $14 winged
  serpent, $15 werebat, $16 venworm, $1A skeleton, $1C spider, $1D stoneman,
  $1E rat, $1F fire elemental, $20 demon, $22 abadon, $27 statue` —
  everything that looks "monster" at a glance but fails `ctrl==3 cnt==0`
  is excluded, e.g. $0D beggar is `oc_ignor`). checks 10-11: the built
  image's own table at `obchars` ($D90D, asm/out/mag.sym) is byte-identical
  to that vendor source. checks 12-13: the 27 placed bytes are executed for
  real in the project's own 6502 core (`tools/nestrace.py` Bus/CPU) — once
  per type against the game's own table, and against 8 per-object states
  (unused object, cntr counts, ctrl=0/1 non-attackers, counts) — a mere
  readback would be the iteration-2 mistake again.

Match accounting, WITH DRAW.PDS + AGGRO.PDS in the build:

|                 | after iter 2      | after iter 3   |
|-----------------|------------------:|---------------:|
| cmpbank bank    | 131061/131072     | 131034/131072  |
| differ vs Beta1 | 11 bytes $FAFF-$FB09 | 38 bytes $FAFF-$FB24 |
| distinguishes   | drawfill          | drawfill + obaggro |

No other byte in the build changes. The two modules call themselves a
*placement ledger* in the manifest; the ledger's byte count is now 38 and the
remaining space in the fixed-window `$FF` run is 129 - 38 = **91 bytes**,
ending $FB25.

`src/testing/test_repo_hygiene.py`: the derived sha gate moved from
`7ca9428b...` to `aff6e610...`, as the ledger requires of every iteration.

Order note for the next iteration: `ours` iteration ordering does not
guarantee the slot recreation; the seen-in-build readback keep verifying
byte-for-byte with both `expect` sets applied, and both `drawfill` and
`obaggro` readback confirm the fixed-window order was respected. A recreate
erasing an overlay is still possible — the honest test is to *execute* obaggro
in the built image, which is exactly what tests 12-13 of test_aggression do.

## Remaining for health-bar feature

- ~~Aggression test identification (`obmod` vs `obstat`)~~ — answered in
  iteration 3: neither; it is `obchr`, with `obaggro` placed at $FB0A and
  executed in tests.
- Max health scale measurement (`obhel` 16x).
- Weakness icon source (`obchr` or per-type table).
- Spell slot 27 wiring, four tiers, HUD integration.

## Iteration 4 — 2026-10-26: the health scale, measured then encoded

The measurement ("max health scale measurement, obhel 16x"), taken from the
Beta 1 source before any byte was written:

- `obhel` is one byte per object at $0524 (X0.PDS:550
  `zp obhel,maxob ;health`; asm/out/mag.sym confirms `obhel = $0524`).
- **$FF is maximum, not "full + overflow".** Three independent pieces of
  evidence: actob (X6.PDS:538-539) inits EVERY object's obhel to $FF with
  Eurocom's own comment "start with maximum health" — enemies included, since
  objects are activated to `obhel` by slot type, not per-player; `heal`
  (MISC.SRC:155-161) does `clc / adc obhel+pi / bcc !a / lda #$ff` and
  clamps at $FF, the only upward writer; X7.PDS:261 re-inits the player
  `dex / stx obhel+pi ;max health`.
- **There is no per-type health table.** The 44-row `obchars` table
  (MISC.SRC:1425) and its field equates (MISC.SRC:1405-1424) carry only
  physical/ethereal, searchable, bullet action, interact/attack control and
  inter/attack count — no health field. Every object that exists has the same
  0..$FF scale.
- **Combat writes all go through `harm` (MISC.SRC:9+)**, one signed amount:
  the enemy path is `!d / adc obhel,x / bcc killob ;not dead / sta obhel,x`
  (MISC.SRC:43-45) — underflow past 0 kills, no clamp at the top (correct,
  since combat only lowers health), and losing health turns a monster into a
  PERMANENT attacker (`ora #%00001100` to obchr, MISC.SRC:46-53 — the very
  field obaggro reads). Callers: the venom tick (X4.PDS:91-94, guarded by
  `lda obhel,x ;don't handle venom if ob is already dying`), the bullet hit
  (x3.pds:763, `lda #-32` then harm), the energy-drain charm (X4.PDS:899-903).
  `killob` (MISC.SRC:81) and `killplr` (MISC.SRC:120) write only 0. Full grep
  of obhel across pds-text/ verifies: those are all the writes.
- **The display scale the game defines is /32.** `helind` (X6.PDS:33-39)
  turns health into a message index by five `lsr` instructions
  ("health rating = health / 32", 0..7). That divisor is the only scale the
  game defines on obhel, so it is the scale the bar uses.

Implemented (the smallest piece the measurement supports):

- `src/magician/HEALTH.PDS` — `obhbar` — at **$FB25**, 9 bytes
  (`BD 24 05 4A 4A 4A 4A 4A 60`), directly behind AGGRO.PDS. X = object
  index in, A = obhel/32 (0..7) out, by the game's own helind divisor, so a
  bar is drawn in the game's own units ($FF renders 7 of 8 tiles, 0 none).
- `asm/build.py`: `HEALTH.PDS` in OUR-modules manifest (slot 15, origin
  $FB25, `expect_bytes`/`expect_cpu`/entry, note).
- `src/testing/test_health_scale.py` — 16 checks: manifest/placement/bytes/
  disassembly as in test_aggression; the measurement's load-bearing source
  facts re-checked from vendor/ by regex (X6 init, heal clamp, harm enemy
  path, 44-row table with no health field); and the placed code EXECUTED in
  tools/nestrace.py's 6502 core — a bar-boundary sweep of 15 named health
  values (0, 1, 31..33, 63/64, 86/87, 127, 171/172, 223, 254/255 — helind's
  /32 boundaries) plus 8 random samples, each run against `obhel=h //32`,
  and the game's own harm message bands (MISC.SRC:20-24) rendered and
  compared to what helind's divisor gives.

Match accounting, WITH DRAW + AGGRO + HEALTH in the build:

|                 | after iter 3   | after iter 4    |
|-----------------|---------------:|----------------:|
| cmpbank bank    | 131034/131072  | 131025/131072   |
| differ vs Beta1 | 38 bytes $FAFF-$FB24 | 47 bytes $1FAFF-$1FB2D |
| distinguishes   | drawfill + obaggro | + obhbar (obhel/32) |

A byte-exact sweep of PRG offsets confirms every differing byte against
Beta 1 lies in $1FAFF-$1FB2D — the contiguous placed window, nothing outside
it. Ledger byte count 38 → 47; the fixed-window $FF run has 129 - 47 = **82
bytes** left, ending $FB2E.

- `src/testing/test_repo_hygiene.py`: derived sha gate aff6e610... →
  65d86b4b..., as the ledger requires.

All gates green after the change: make (262,160-byte .nes), cmpbank bank
(38 differ bytes → 47, all ours), make check-py 26 files/26 pass (296 checks
or more), make guard clean, titletest PASS (6 checks).

Remaining for health-bar feature:
- Weakness icon source (obchr or per-type table).
- Spell slot 27 wiring, four tiers, HUD integration.
