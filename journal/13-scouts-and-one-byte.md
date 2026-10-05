# 13 — Scouts, and the first town proved on the cartridge

*Branch `fix/boot-from-source`. This entry replaces the "suspects are
`ldatl`/`ldath`" line at the end of journal 12: that guess is now measured and
it is wrong.*

## What was set out to do

Three corrections to how we work, and all three changed the shape of the tree:

1. **Prove everything on the stock ROM first.** `src/play/` is developed and
   proven against Beta 1. The rebuild is a second, clearly-labelled report.
2. **Add scouts.** Port `aibeatszelda`'s MAIN/SCOUT split and express each
   activity as a *segment* — a factory and a success test — not a script.
3. **No throwaway scripts.** Everything that produced a finding is either a test
   in `src/testing/` or a committed tool writing to `logs/`.

## What the scouts found that a straight-line script would not

The first Beta 1 run of the scout route took **769 frames**. The second, with
`first_success` turned off on the transitions, took **307**. Nothing about the
game changed; the difference is entirely what the scouts were allowed to ask.

| segment | 1st run | 2nd run | what the scout found |
|---|---|---|---|
| `new_game` | 160f | **42f** | attempt 1 used a 120-frame lead-in and worked. Attempt 2 used **0** frames and also worked. The lead-in was 118 frames of standing still. |
| `into_level` | 160f | **42f** | same |
| `inventory` | 160f | **42f** | same |
| `inventory_close` | 160f | **42f** | same |
| `walk` | 60f | **70f** | a survey, below |
| total | 769f | **307f** | |

**The 30-frame lead-in is not rejected, it is cut off.** Four attempts per
transition drew 30 frames and reported `held=False`, which reads as "that
approach does not work". It does not: `into_level`'s ended at `phase=2 curlev=$10
mapind=1` — the A had been accepted and the screen was still fading. The ledger
now says so in words (`that is the CUT, not a verdict on the approach`) because
`held=False` is exactly the note this project spent a day being misled by.

**`walk` is a map, and the scout is the only thing that can draw it.** Twelve
attempts, four successes, and the survey is unambiguous:

    Down  (60,140) -> (60,140)   wall, 5 attempts
    Left  (60,140) -> (18,140)   works, 42 px in 70 frames
    Right (60,140) -> (128,140)  works, 68 px in 70 frames
    Up    (60,140) -> (60,140)   wall, 2 attempts

Two findings in one line each. The town is open east and west and closed north
and south; and **Left only has 42 px of room** — 1 px/frame confirmed on the
Right leg, so the player is stopped by the map edge, not by a shorter animation.
A straight-line script that walked Left would have concluded the town was 42 px
wide.

Two stop rules had to be fixed to get that survey, and both were found by
reading the ledger rather than by the run failing:

* `walk` needed `accept_after = tries`. On the first run the search stopped at 6
  of 8 attempts with the accept-after rule satisfied, having sampled Down three
  times and Left once. Right and Up were never tried.
* `p_walk`'s hold had to become a **fixed 70 frames**. With it drawn at random,
  the first success set the cutoff and every later attempt that drew longer was
  cut at 0 frames — so an attempt that would have said "Up is open" said nothing.
  One number, the measured one, makes every attempt cost the same and the ledger
  becomes a survey instead of a survey of the rng.

**The scout killed a hypothesis about the rebuild.** 8 attempts on `into_level`,
four different lead-ins (0/30/60/120), every one ending
`phase=3 curlev=$20 mapind=2 nmiflag=1`. "Press A differently" is dead as an
explanation; it is a data/branch difference in our build, and the ledger is the
evidence.

## Beta 1: MILESTONE 1 COMPLETE AND VERIFIED

    fingerprint a95fe5355baff0c3189df0c0ed27718ac5292fd8
    replayed from power-on in a fresh emulator -- MATCH

    title            68f   1/1   first success
    new_game         42f   2/6   capped
    into_level       42f   2/6   capped
    walk             70f   4/12  budget exhausted
    inventory        42f   2/6   capped
    inventory_close  42f   2/6   capped
    deferred          1f   1/1   first success

## The rebuild's g03 wedge: the suspects in journal 12 are refuted

Same route, same assertions, `asm/out/magician-rebuilt.nes`:

    into_level: no attempt in 8 reached the success test.
    phase=3(g03) curlev=$20 mapind=2 nmiflag=1 bnksel=$07

`src/play/differential.py --inputs logs/inputs/m1_first_town_beta1.inputs.txt
--every 1` replays Beta 1's own verified 307-frame log into both ROMs, one RAM
sample per frame:

    first differing frame: 81
    A: phase=1(g01) curlev=$00 oldlev=$E2 mapind=0 hilev=0
    B: phase=1(g01) curlev=$00 oldlev=$E2 mapind=1 hilev=1
    8 of 2048 bytes differ

    $001F  A=$FF B=$FE    scratch zp (piaa/si75/tc)
    $0020  A=$FF B=$06    scratch zp (pi53/s08/spc/td/tok1)
    $0021  A=$FF B=$1E    scratch zp (dblank/pi41/pi61/si53/te)
    $004D  A=$00 B=$01    mapind
    $01FA  A=$82 B=$64    (no symbol)
    $01FB  A=$30 B=$F4    (no symbol)
    $01FC  A=$6F B=$8A    (no symbol)
    $0719  A=$00 B=$01    hilev

Six of those are scratch and a CPU at a different point in the same routine. Two
are durable: **`mapind` ($004D) and `hilev` ($0719), both 1 on ours and 0 on
Beta 1.** `--watch mapind,hilev` says *when*:

    frame   Beta 1                       rebuild
       1    mapind=255 hilev=255         mapind=255 hilev=255
       2    mapind=0   hilev=255         mapind=0   hilev=255
      81    mapind=0   hilev=0           mapind=1   hilev=1     <-- diverge
     111    mapind=1   hilev=0           mapind=2   hilev=1

**The phase timeline is frame-for-frame identical on both sides** — f1, f2, f81,
f82, f84, f92, f93, f107, f108, f111, f112, f123 — with the same `curlev`,
`oldlev` and `bnksel` at every one of them. Every phase transition the milestone
walks through runs on our build, on the same frame, with the same values.

So the whole divergence is **one byte**. `mapind` is 1 on our build at frame 81,
when the map screen at level `$E2` is being entered. Beta 1 leaves it 0. Then at
frame 111 both ROMs run the same `inc mapind` — "start game at next main level"
(x6.pds:624-625) — and Beta 1 goes 0→1 (`curlev` `$10`, the first level) while
ours goes 1→2 (`curlev` `$20`, the second). The wedge in g03 is what a level-2
load looks like when the level-2 data is not right; it is downstream, and it is
not where the bug is.

### What this refutes

**`ldatl`/`ldath` (journal 12's suspects) are not it.** They are read *after*
`mapind` is correct, to turn the level index into a pointer. Our `g03` runs on
the same frames as Beta 1's and with the same `curlev` until `mapind` is wrong.
The level-pointer tables cannot be the first thing to differ, because the thing
that decides which pointer to read differs first.

**`stlev` is not it either — and the cartridge says so.** The source writes
`stlev db $01` (x5.pds:8), and `initvars` does `lda stlev / sta hilev / sta
mapind` (x1.pds:27-29) — the only place in the source that writes that pair from
one value, which made it the obvious suspect. Disassembling our `initvars` at
$850E finds it, and it is the same code as the cartridge's:

    ours   prg+$000553:  AD 00 C3 8D 19 07 85 4D      lda $C300 / sta hilev / sta mapind
    Beta 1 prg+$000540:  AD 00 C3 8D 19 07 85 4D      (identical operands)

`stlev` is at `$C300` in both, and **`$C300` is `$7A` filler in both** — not
`$01`. So `lda $C300` returns `$7A` on the cartridge that works, which means the
C=0 branch is never taken there, and no hypothesis that depends on `$C300`
holding `$01` can be true. Recorded because it was the most plausible wrong
answer available, and because the instrument that refuted it
(`--pattern 8d1907 854d`) is now a committed mode rather than a shell command.

Note for anyone repeating this: `sta mapind` assembles as `85 4D`
(zero-page), not `8D 4D 00`. Searching for the absolute form finds nothing in
either ROM and reads as "the routine is not there".

## Byte accounting

Recounted on this machine, source-derived and manifest-derived kept apart:

    PRG 96413/131072 (73.56%, reported as 73.6%)
      from the source alone : 96413
      from the patch manifest: 0        (asm/patches.manifest has no regions)

Unchanged from journal 12. `dl`/`dh` (4db43ee) are still in, and the 78555 /
59.9% figure is still stale.

## The harness's own bugs, all of which a check caught

| bug | what it looked like |
|---|---|
| `Recorder.__exit__` returned `True` | the context manager *swallowed* `OverBudget`, which is the one thing it must never do |
| `note != "over budget"` decided whether an attempt was a failure | the moment the note grew a frame count, "over budget at 4 frames" stopped matching and **every cut-off attempt was scored a success**. A boolean now. |
| the winner's inputs were APPENDED to MAIN's log | the log length was right and the frame counter was not — a log that will not replay |
| `Run.segment` dropped the FAILED checkpoint for exceptions it did not itself raise | caught immediately by `test_play_actions.py` check 10c, and it was right the first time |
| `_answers` filed `save` with the integer replies | `save` answers `ok state=<n> <path>`; a correct reply was rejected. The third time this file was bitten by that shape after `snapshot` and `screenshot` |
| `load`'s reply was accepted with no `frame=` | a bare `ok` from a `load` is a bridge that did not say where it put the machine |
| the route was installed after the emulator started | every snapshot in the run recorded `segment_list_sha1=-` and the checkpoint guard was inert for the whole run. Nothing said so. |
| `p_title`/`p_walk`/`p_notes` returned a policy, not a factory | `factory() got an unexpected keyword argument 'rng'`, on the first live run |
| the differential sampled at input-run boundaries | 39 samples of a 307-frame log, and "first differing frame 84" was really the first *sampled* difference. True answer: **81**. |
| my own offset arithmetic for `$C000+` | `a - 0x8000` is wrong above `$C000`: the fixed window is at prg+`$10000`. It made `newlev` look like a hole of zeroes. `tools/cmpbank.py:cpu()` has it right. |

## What the next attempt needs

1. **Who writes `$004D` between frame 80 and frame 81.** The whole bug is one
   byte and one frame. `tools/nestrace.py` against the
   `logs/inputs/m1_first_town_beta1.inputs.txt` log, watching $004D, is the
   measurement. The four writers in the source are `x1.pds:28`, `x0.pds:951`,
   `x6.pds:625` and `initob`'s neighbourhood; the frame is inside
   `newlev $E2`, so it is the first of those.
2. **`$01FA-$01FC`**, three bytes with no symbol that differ at the same frame.
   They may be the same write's side effect or they may be the cause. The ram
   map has a hole there and adding fields for it is a prerequisite for
   anything that reads that region.
3. Then re-run the milestone **unchanged**. The route is correct and verified on
   the cartridge; `into_level` is the only thing standing between our build and
   the same verified run.