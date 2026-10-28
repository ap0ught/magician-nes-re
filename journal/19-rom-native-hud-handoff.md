# 19 — ROM-native HUD research and handoff (2026-10-27)

## User-confirmed scope

Issue [#2](https://github.com/ap0ught/magician-nes-re/issues/2) is an **in-ROM NES HUD**, not an emulator overlay or mockup. Target the rebuilt ROM and preserve the existing custom hexadecimal debug text in its message box; it is intentional and out of scope. Reuse the original yellow-green ornate border from the game's actual Book/Backpack UI, not invented artwork. The user provided screenshots of Backpack, Book/Inspect, world map and town gameplay to establish visual fidelity. The gameplay screen has a bordered message area; **do not overwrite or repurpose it** without a separate explicit decision.

## Inputs and evidence

- User supplied retail `Magician (USA).nes`, Beta 1 `Magician (USA) (Beta 1) (1990-03-02).nes`, and the locally rebuilt `magician-rebuilt.nes` (binary assets are **not** committed).
- Previously measured Beta 1 vs retail: 94,956 PRG differing bytes, 29,369 CHR differing bytes, nine identical 4 KiB CHR banks. See `docs/hud-beta1-rom-comparison.md`.
- Previously measured rebuilt vs Beta 1: **676 PRG differing bytes, zero CHR differing bytes**, identical header. Treat rebuilt as baseline for future patch diff; do not assume the 676 differences are all explained without checking the manifest.
- Screenshots show gold/green filigree framing Book, Backpack, map, and the message box; Backpack already labels RATING/HEALTH/LEVEL and MANA/GOLD/FOOD/WATER/VENOM; Book uses Hand/Elements/Runes icon groupings.
- CHR is dynamically loaded: raw cartridge tile indices are not automatically runtime CHR-RAM tile IDs. See `docs/capture-analysis.md`.

## Branch artifacts

Work is on `feat/rom-native-hud`, draft PR [#7](https://github.com/ap0ught/magician-nes-re/pull/7), targeting default `fix/boot-from-source`.

- `docs/rom-native-hud.md`: design/engineering plan.
- `tools/hud_audit.py`: read-only module manifest inventory; not a HUD.
- `tools/hud_chr_atlas.py`: 2bpp CHR asset-to-PPM atlas and inventory; not a runtime capture. Its latest newline fix was committed.
- `docs/hud-beta1-rom-comparison.md`: ROM difference measurements.
- `tools/hud_rom_diff.py`: diff candidate against rebuilt baseline; code committed, not tested here.
- `prototypes/hud/HUD.PDS`: initial **unregistered, unassembled** 6502 status sampler. This is **not in the ROM** and does **not render**. Its raw /32 food/water/venom and high-byte mana placeholders must not be presented as valid percentages. `hud_sample` currently uses `$0748-$074C` as proposed RAM slots; **verify their availability and lifetime before registration**.

Existing integration research: `src/magician/INSPHOOK.PDS` retargets a `jsr tickmana` at CPU `$DFF0` to fixed-bank `inspdraw` at `$FB52`, which calls `tickmana` then optional Inspect marker; preserve this chain. `INSPTITL.PDS` uses title-entry hook to clear Inspect latch. `DRAW.PDS` at `$FAFF` writes repeated tiles to `$2007` but requires caller-controlled safe PPU address/timing. Fixed-bank `$FAFF-$FB7F` padding is already partially occupied by DRAW, AGGRO, HEALTH, WEAK, INSPECT; **do not overwrite**. `INSPBODY.PDS` uses measured slot-1 padding at `$B92D`. `asm/build.py:OUR_MODULES` must explicitly register any new `src/magician/` module, with final ROM readback assertion. Never modify pinned `vendor/`.

Verified source symbol examples: `obhel+pi` (player health), `manacur` 16-bit, `manatop` 16-bit, `food`, `water`, `obven+pi`; confirm exact game display semantics before drawing.

## Next developer actions, in order

1. **Inspect actual rendered gameplay/menu screens** and trace the filigree CHR tiles + palettes from the Beta 1 cartridge to runtime CHR RAM; screenshots are reference, not proof of live tile residency.
2. Audit PPU/NMI/scrolling and nametable layout to find a safe background update path and HUD region that does not cover the intentional debug/message box. Avoid direct unscheduled `$2006/$2007` writes.
3. Verify spare RAM and correct display scaling; replace placeholder `HUD.PDS` fields. Check max health semantics (the game stores 0..255, and `obhbar` returns 0..7), mana normalization, food/water/venom counter meanings.
4. Register a **small actual PDS module** in `src/magician/` and `asm/build.py` with safe bank/origin and `expect_bytes` readback; hook rendering without breaking Inspect. Use original CHR filigree if verified. Produce the rebuilt ROM, inspect an emulator screenshot, and iterate visually.
5. Run `make rom`, `make check-py`, relevant tests, and `tools/hud_rom_diff.py magician-rebuilt.nes <new-rom>`; document actual test results and changed bytes. The code in this branch was committed via GitHub connector and **not locally built/tested** in this session.
6. Implement expanded HUD for modifiers, shields, death risks, flags and equipped spells per Issue #2 only after compact mode works. Keep a hide/toggle control for recording.

## Definition of done

Actual ROM displays correct, measured values in original NES visual style, remains stable during gameplay/scroll/menu transitions, does not alter intentional debug text, and passes ROM/build/test verification. **Current state: research and an unintegrated status-sampler prototype; no visible HUD yet.**
