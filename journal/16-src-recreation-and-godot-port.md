# 16 — SRC recreation progress and Godot port

*2026-10-21. Status journal for the two-goal push: recreate the missing SRC and
beat the game, plus the Godot 4.7 port.*

## What exists now

- Capture infrastructure in `tools/capture/` (capture.lua, replay_dump.lua,
  run_captures*.sh, analyze.py). CHR is RAM, not ROM.
- Across title (frame 60), m1 (307 frames) and m2 (433 frames partial) the CHR
  RAM layout is identical: 15 of 16 banks match released DAT/*.CHR exactly.
- One unmatched bank (0x0c, sha1 `5803509e...`, 2233 non-zero bytes) recovered
  to `recovered/bank_0c.chr`.
- IN.CHR / TIT1.CHR / TIT2.CHR are not present in any captured bank so far;
  they are loaded on other screens and need separate captures.
- `magician-src/` (mirrored to `~/code/AIBeats/magician/src/`) seeded with the
  title source (TITLE.SRC, title/, STARTLEV.PDS). No new .SRC added yet.
- Godot 4.7 port at `~/code/games/magician-godot`: title uses the authentic
  captured cartridge frame, main menu, placeholder town. NES render layer verified
  byte-identical to scenerender.py.
- AIBeats magician workspace set up (harness/, runs/m1_first_town.inputs.txt,
  make_bk2.py).

## What is still open

- m2 (shop_ask) still fails: p_icon moves the cursor but ynflag is not set.
  Likely needs a shop with a `yn` icon (church, shop 4) or different A timing.
- Deeper scenes (intro, dungeons, bosses, shops beyond first town) not captured.
- Disassembly of uncovered PRG regions (slots 1, 3, 4, 9, 12, 14, 15) not
  started; no new .SRC files beyond the title seed.
- Godot port gameplay (password, overworld, NPCs, combat, spells, inventory,
  shops, audio, save) not implemented.

## Next concrete steps

1. Fix p_icon: test A-press timing against the church shop (icon 1 is `yn`).
2. Complete m2, then capture its CHR RAM and check for new unmatched banks.
3. Extend the route to dungeons/bosses; capture each scene.
4. Disassemble uncovered PRG regions; produce .SRC files in magician-src/.
5. Mirror magician-src to AIBeats/magician/src and update the Godot port art.

## Verification

- `src/testing/run_all.py`: 23/23 pass.
- `make`: builds magician-rebuilt.nes.
- `tools/titletest.py`: PASS.
- `godot --headless --path . --quit`: clean.
