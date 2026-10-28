# ROM-native HUD — Issue #2 implementation plan

**Status:** design and engineering plan; ROM code not implemented or tested.
**Branch:** `feat/rom-native-hud` (from `fix/boot-from-source`).

## Decisions
- Render in the actual NES ROM, never a BizHawk Lua or browser overlay.
- Reuse the yellow-green filigree from the original Book, Hand, Powers and Elements screens where actual CHR tiles and palettes permit.
- Compact top strip: verified HP, MP, hunger, thirst, and urgent warnings. Expanded view: all modifiers, shields, death causes, persistent flags, and equipped spells.
- Concept mockups are not pixel-accurate NES tile layouts. Validate 256×240, palette attributes, scrolling and VBlank budgets.

## Source rules and discoveries
- `vendor/Magician-NES/` remains unchanged. All modifications go in `src/magician/`, registered explicitly in `asm/build.py:OUR_MODULES`.
- `src/play/ram.py` resolves symbols from `src/play/zpmap.txt` or `asm/out/mag.sym`. Its warning is critical: `$0496` is `levsav`, not HP. It documents `manacur` as little-endian.
- Existing issue #3 modules occupy measured slot-15 PRG slack; do not assume a free injection location.
- Use real source symbols, measured values, and explicit failures for unknown fields; do not hardcode the mockup's sample numbers.

## Milestones
1. **Asset inventory:** trace original menu filigree through `vendor/Magician-NES/DAT/`, CHR packing, palette attributes and menu draw routines. Record tile IDs, banks, palettes and a tilemap sketch.
2. **Rendering hook:** trace NMI, nametable updates, scrolling and scene transitions. Find a measured safe code/data placement and verify its bank mapping and overwrite budget. Prototype filigree-only background-tile strip.
3. **RAM verification:** identify health representation and maximum, current/max mana, food, water, poison, shields, modifier flags, and persistent flags. Document symbol, units, source line, and BizHawk measurement for every displayed field.
4. **Minimal strip:** background tiles over OAM sprites; bounded dirty-field updates in VBlank; preserve dialogue and gameplay. Show only currently applicable urgent risks.
5. **Expanded panel:** FLEET FOOT, FLY, SLOWMO, LIGHTNING, TWIN; PHYS/VEN/FIRE/POWER shields; sentlet/ringana/amsheeld/ammor; drown, sink, physical causes (distinguish starve/dehydrate/poison/sphinx/spikes), twinkle, skeleton, explode, melt, drunk; equipped spells.
6. **Critical warnings and toggle:** NES palette/tile cycling without transparency; choose a non-conflicting toggle input and verify recording behavior.

## Acceptance and test gates
- `make rom` succeeds; placement/readback assertions catch overwritten or bank-misplaced code.
- `make check-py` stays green.
- BizHawk screenshots prove actual ROM rendering; RAM checks prove displayed numbers and flags.
- Verify Beta 1 where relevant and rebuilt ROM; test movement, scrolling, menus, combat, low health, starvation/dehydration, modifier changes, and toggle.
- No unexplained CHR, nametable, palette or sprite regressions.

## Open technical questions
- Does the game permit a fixed top strip without scrolling artifacts or sacrificing visible playfield?
- Which original filigree tiles are resident in the gameplay CHR bank?
- Where is the safe injection point and what is its exact free-byte budget?
- Which input combination should toggle the HUD?

The first implementation PR should prove the border and VBlank/scroll stability before adding all status logic.

Related: https://github.com/ap0ught/magician-nes-re/issues/2
