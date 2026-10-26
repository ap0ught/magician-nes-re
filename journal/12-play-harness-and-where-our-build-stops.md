# 12 — The play harness, and where our own build stops

*Branch `fix/boot-from-source`. The two loose ends are closed: `dl`/`dh` was
already committed with its test (4db43ee), and `padprobe.lua` is now marked
superseded with its wrong reasoning corrected rather than run.*

## What was set out to do

Replace frame-exact TAS replay with actions driven off RAM predicates, split into
activities, with a snapshot before each, and use the first milestone to find out
what is actually in the first town.

The reason for dropping the TAS is not that the injector was wrong — the
injector was right. The cartridge sees every press (3935/3935, read from its own
bytes) and `phase` ($5F) stays $80 at every anchor, so the main loop is not
advancing. Not localised. An action driven off a RAM predicate does not care
whether the emulator is in sync with a recording, so it works on any build that
behaves correctly — and that turned out to matter, because the harness's first
job turned out to be telling a working build from a broken one.

## What is in the tree

    src/play/emu.py       the only module that talks to BizHawk
    src/play/bridge.lua   the Lua half, inside EmuHawk
    src/play/ram.py       named RAM decode; the single source of truth
    src/play/route.py     ordered segments, checkpoint before each
    src/play/actions/     walk menus shop fight spells talk
    src/play/recon.py     reconnaissance: measure, then report
    src/play/milestones/  m1_first_town.py

Predicates are evaluated **inside the core**. `step_until` ships the predicate
set and the budget together and gets back "held after N frames, or never", because
a socket round trip and a 2 KiB RAM read per frame would move the thing being
measured across a socket in the middle of a predicate. The contract is unchanged:
the assertion is still on a value the core read out of its own memory.

## The measurement that mattered most

Which debounced byte is the SELECT edge. The source's names say $0038 `dsta` =
START; the order `joykey` writes the bytes in says $0038 is the Select edge. Both
readings are self-consistent and produce a plausible answer, so `ram.py` was built
to **refuse** to answer — `select_edge()` and `start_edge()` raised until a
measurement existed.

The measurement is one button at a time, held for 120 frames so the game's own
two-consecutive-equal-reads debounce settles, with eight samples per button to
prove the value is stable. It settled three things:

1. The source's own names are right: `$0032 sta`=START, `$0033 sel`=SELECT,
   `$0034 fireb`=B, `$0035 firea`=A, and `$0038`/`$0039` are the START and SELECT
   *edges*.
2. The write-order reading is wrong **because** `joykey` overwrites `$0030`/`$0031`
   with `lr`/`ud` after the decode loop. A reading that ignores that step puts the
   wrong button in the wrong place.
3. `$0030 lr` is `$FF` for LEFT and `01` for RIGHT — a signed *direction*, not an
   OR. `getdir` (x6.pds:951-957) reads its sign to tell them apart.

Also measured, and all three had been got wrong first:

* **`maxob` is 4, so the player is object slot 3.** `mag.sym` also has a `pi` at
  `$001C` from SUBGAMES.SRC meaning "pointer index". And `plrx` was first declared
  as `obxl` with no offset, which reads slot 0 — the slot `initob` leaves empty.
  "The player is at 65535,65535" was never the player being lost.
* **`manacur` is little-endian.** Read big-endian it reported 12800 mana for a
  character with 50.
* **`jt` is two bytes**, and `joykey` writes `$002E-$0035`, not `$0028-$002F`.

## The town, measured

Route: title → START → map screen at level `$E2` → A → the playing level.

**Beta 1** reaches phase 0 (g00) at logical level `$10`, mapind 1, clock running.
START there opens the inventory, at phase 8 **and curlev `$E0`** — the inventory
is a separate level, not an overlay. Holding a direction for 70 frames moves the
player 68-70 pixels.

* walking — **works**; asserts the position *changed*
* inventory — **works**
* talking — **does not exist.** No object slot ever carries a live `obint`. The
  source defines exactly two interaction messages, `intmsg` (wise man, tree) and
  `begmsg` (beggar), and neither is in this town. `talk.py` raises with that
  finding. It does not press A and call whatever happens "talking".
* shopping / fighting / spells — **not reached.** Each module exists and refuses
  to guess: `fight.player_low()` raises without a threshold, `spells.commit()`
  requires the expected mana cost, `shop.enter()` asserts the caller's target
  level as well as the phase.

## Where OUR build stops

The same route, same assertions, on `asm/out/magician-rebuilt.nes`:

```
ASSERTION FAILED: into_level: the predicate did not hold within 600 frames.
Wanted phase eq 0x0 @$005F.  RAM now: phase=3 (g03 enter level), curlev=$20,
mapind=2, nmiflag=1, bnksel=$07.
```

Beta 1 computes `curlev=$10`, mapind 1, and plays. Our build computes `$20`,
mapind 2, and wedges in g03 with `nmiflag` never cleared — the IRQ main loop's
own re-entry guard saying a phase never returned. PC sat at `$DCXX`, outside
`$8000-$FFFF` where every module lives.

**This is the next thing to work on.** g03 is the level-entry routine: `initlev`,
`putlev`, `scrnon`, `idata`. So the suspects are the level-pointer tables
(`ldatl`/`ldath`, MISC.SRC) and the MMC3 bank juggling around them, in a build
whose X7 DAT bytes are 99.5% and whose overall PRG match is 73.6%.

## The harness's own bugs, which each produced a confident wrong answer first

| bug | what it looked like |
|---|---|
| `conn:settimeout(nil)` is not "no timeout" in this LuaSocket, and `send()` reset it | the loop exited on a perfectly healthy connection |
| the reply terminator set did not include `pong` | `cmd("ping")` blocked forever on a reply that had arrived |
| `usememorydomain()` on an unknown name does not fail — it keeps the previous selection | a nametable dump full of cartridge code |
| `CPU registers` is 12 bytes of which 8 are readable | "all nine domains" could not be dumped, and the natural response was to drop it |
| a domain name contains spaces and parentheses | `domain_read` compared `dom=CIRAM` and rejected a correct reply |
| the input log wrote "nothing pressed" as a blank line and the loader skipped blanks | 636 frames recorded, 392 replayed; the proof reported MISMATCH against a run it had not been given the inputs for |
| `run.sh` backgrounds EmuHawk and returns before the window closes | the replay emulator was diverted into the first through the single-instance pipe |
| a button held for 400 frames makes ONE edge, and the game reads edges | the title was asserted ready 3 frames after power-on and the press landed on a screen that was not listening |

That last one is why `act()` has a `pulse` parameter. A button held is right for
walking, where the level is what matters, and wrong for every menu.

## Byte accounting

Measured by a fresh `make rom` on this machine, not recalled:

    PRG 96413/131072 bytes identical to Beta 1 (73.6%)
      from the source alone : 96413 (73.6%)
      from the patch manifest: 0        (asm/patches.manifest has no regions)

**The 78555/131072 (59.9%) in the brief is stale.** It was the number before
`dl`/`dh` were fixed to one byte each (4db43ee), which was worth 17858 bytes.

## What the next attempt needs

1. The level-pointer path in g03. `convind` computes
   `ldatl[curlev>>4] + (curlev&15)*16` and subtracts `ld10`; a wrong table here
   produces a pointer into the middle of nothing. Compare our `ldatl`/`ldath`
   against Beta 1's directly — both are data tables, so `tools/gapmap.py` should
   classify them.
2. Then re-run the milestone unchanged. The route is correct and verified; the
   only thing standing between it and `MILESTONE 1 COMPLETE AND VERIFIED` on our
   build is `into_level`.