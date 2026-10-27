# Capture analysis (beta1, frame 60 title)

Capture scripts: `tools/capture/capture.lua`, `tools/capture/run_captures*.sh`,
`tools/capture/analyze.py`. Captured data lives under `/tmp/opencode/captures*`
(cartridge-derived bytes are not committed per LEGAL.md).

## Findings

- CHR on this cart is RAM, not ROM. The 64 KB "CHR" domain at any frame is
  whatever is currently loaded. Every captured frame so far (boot, title, and
  several START timings) shows the same title-screen CHR layout, because the
  title screen did not advance (START timing not yet solved; the play harness
  uses a scout with lead-ins 0/30/60/120).
- Of the 16 4 KB banks present during the title, 15 match released DAT/*.CHR
  files exactly. One bank (index 0x0c, sha1 `5803509eade9928e7c760449fa9162513e65c373`,
  2233 non-zero bytes) matches **no** released CHR file. This is the candidate
  missing art bank.
- IN.CHR, TIT1.CHR, TIT2.CHR are not present in any captured bank so far; they
  are loaded on other screens (intro, title sprites) and require separate captures.

## Findings (updated)

- Across title (frame 60), first town (m1, 307 frames) and partial town quests (m2, 433 frames),
  the CHR RAM layout is identical: 15 of 16 banks match released DAT/*.CHR exactly.
- The single unmatched bank (0x0c) is the only missing CHR bank in the released set.
- m2 (shop_door) did not complete; deeper scenes (shops, fighting, spells) still need capture.

## Next steps

1. Solve title START timing (reuse play harness scout) to advance past the title
   and capture intro / password / first-town CHR.
2. Identify the unmatched bank 0x0c (sprite vs background, which screen).
3. Compare captured nt0/palettes/OAM against released DAT to enumerate further gaps.
