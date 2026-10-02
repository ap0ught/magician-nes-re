# Provenance

Where every fact and every file in this repository came from, and which measurements back them.

## 1. The cartridge dumps on this machine

Two NES files named Magician exist under `/extdrive/backups/SHARE/roms/nes/`:

| file | size | body SHA1 | body CRC32 |
|---|---|---|---|
| `Magician (USA).nes` | 262160 | `bd806d7f7c318b8012433250ca10aa8387a962bb` | `91E2E863` |
| `Magician (USA) (Beta).nes` | 262160 | `2a0a444dae8b5b02f4e5f1b789e16356f3ab08f0` | `2D1FEE70` |

"Body" = the file with the 16-byte iNES header stripped. Both headers are **byte-identical**:

```
4e 45 53 1a  08 10  42 00  00 00 00 00  00 00 00 00
'NES'  \x1a  PRG=8 CHR=16  f6=0x42 f7=0x00
```

Decoded: 8 × 16 KiB PRG = 128 KiB, 16 × 8 KiB CHR = 128 KiB (CHR-ROM, not CHR-RAM — the count
is non-zero), mapper `(f7 & 0xF0) | (f6 >> 4)` = **4** (MMC3), battery-backed PRG RAM
(`f6 & 2`), no trainer, horizontal mirroring (`f6 & 1` = 0).

The two bodies differ by **23 545 bytes**. 16 + 131072 = 131088, so both files are header plus
body with nothing extra.

Both are the release-lineage title; `(Beta)` is a pre-release build. The three NESMagician dumps
that appear as *box art* under `roms/nes/boxarts/` (`Taxan (US) (proto)`, three betas) have no
ROM file on this machine.

**Decision: develop against `Magician (USA).nes`.** It is the retail cartridge, it is the one
that boots to the release title screen, and every address, source label and input file in this
repository describes it. The `(Beta)` dump is a separate revision: same header, different code.
The header alone cannot distinguish them, which is why the digest is pinned in source next to the
addresses instead of being inferred from the mapper.

## 2. The source

`vendor/Magician-NES` — git submodule, `https://github.com/ap0ught/Magician-NES`, pinned at
`bf653a4` ("Added source code"), which is itself a fork of
`https://github.com/tkcn568/Magician-NES`. Two commits, no tags, no releases.

Contents: eight `X?.PDS` (25–32 KB each), seven `.SRC`, `DAT/` (level data, palettes, CHR
artwork, sprites, music), `TXT/` (message text), `SENDG` (the CHR download script for the
development system).

The `.PDS` files are binary PDS 1.26 project containers, not text. Decoded by
`tools/pds_extract.py`; see that file for the container layout. The decode is mechanical:
the source text starts at offset `0x200` in every one of the eight files and runs to a short
binary footer, lines are terminated `CR NUL` rather than `CRLF`, and a lone `CR` is the editor's
soft wrap inside one logical source line.

Decoded total: **6 351 text lines** across the eight banks. Re-measured
2026-10-02 from `tools/pds_extract.py decode()` over all eight containers:
6 351 confirmed exactly.

An earlier version of this file claimed 13 104 lines. That number counted every `CR NUL`-delimited
chunk to the end of the container, including the symbol table and other binary trailer the
container keeps after the source — roughly half of it is not text. Measured against the old
decode, the two agree exactly on the text-bearing lines (6 351 either way); the difference is
entirely trailer. `tools/pds_extract.py` now stops at the first chunk containing a byte that could
not be source, which is why the two totals of *all* lines differ.

The same sentence used to read "(7 321 counting blank lines)". **That secondary
figure could not be reproduced and is not reproduced now.** Measured: 978 blank
lines, so 7 329 elements from splitting each decoded file on `\n`, or 7 323 if
one trailing empty element per file is excluded. Neither is 7 321. The text-line
count above is the one that matters and it is right; treat the blank-inclusive
number as unestablished rather than as 7 321.

## 3. Does the source build this cartridge? (measured, and no)

Eurocom's source is dated. Each `X?.PDS` header carries "Last update 14:12 on 02/03/90" and a
project version in the form `24.33`. The cartridge was released in February 1991.

Two measurements put the source and the retail cartridge apart, before any code is assembled:

- **The version string differs.** `x7.pds` ends its bank with

  ```
  org $fff0
  db "MAGIC1+"
  hex 25 02 90
  ```

  The retail cartridge has `4d 41 47 49 43 2d 2a 20` = **`"MAGIC-* "`** at `$FFF0`
  (PRG file offset `0x1FFF0`, the last 16 bytes of the last bank). `"MAGIC1+"` does not occur
  anywhere in the retail PRG.

- **X5's page-aligned tables land exactly where the source says.** `x5.pds` begins
  `org $c000` followed by `sql`/`sqh`/`cos` tables beginning `00 01 04 09 10 19 24 31 …`. That
  sequence is at PRG file offset **0x1C000**, which is the start of the 128 KiB image's final
  16 KiB bank. It also appears at `0x1C080`, because the source repeats the table.

So the bank layout, the assembly dialect and the data files are the real ones, and the retail
cartridge is a **later build of the same program**. The rebuild therefore produces a ROM that
differs from the retail cartridge in the changed code, and the difference is the interesting
artifact: it is a diff between a development build and the shipped one. Exact agreement is not a
target; a *characterised* difference is.

**Consequence for addresses:** label addresses are correct for code the source contains. Where
the retail cartridge differs, an address from this repository's label map must be checked against
the cartridge bytes before use.

**Correction, 2026-10-02.** This section ended by pointing at
`crates/mag-core/src/rom.rs` as the place the digest is pinned "so a mismatch is
detected, never silently applied to another revision". **There is no `crates/`
directory and no such file; nothing in this repository enforces the digest.**
Grepped `asm/` and `tools/` for the pinned SHA1 and CRC32: the only hits are in
`asm/mkrom.py:65-66`, which *prints* the expected value in a message and does not
compare. Until that check exists, the pin is documentation, and every address
here applies to `Magician (USA).nes` by decision, not by detection.

## 4. Address translation: CPU address is not file offset

**The mapping, stated once.** MMC3 presents 8 KiB slot `n` at CPU address
`$8000 + (n & 3) * $2000`. `& 3`, not `& 7`: the `$8000-$FFFF` window is four 8 KiB
slots wide, so only four are visible there and the other twelve are reached by
writing the mapper. Slot `n` occupies PRG file `n * $2000`. With a 128 KiB PRG
image the mapper's fixed bank is at PRG file offset `+$10000`.

MMC3 fixes `$C000-$FFFF` to 8 KiB slots 14 and 15, which are PRG file offsets `0x1C000` and
`0x1E000`. So **file offset = CPU address + `$10000`** across the whole fixed window — equivalently
`+$8000` for `$C000-$DFFF` and `+$F000` for `$E000-$FFFF` — and the bank-selectable window at
`$8000-$BFFF` is `+$0`–`+$FFFF`. Measured, all three agree:

| CPU | file | read from the cartridge |
|---|---|---|
| `$C000` | `0x1C000` | x5's `org $c000` `sql` table, `00 01 04 09 10 19 24 31 …` |
| `$FFF0` | `0x1FFF0` | `"MAGIC-* "` — the version string |
| `$FFFA` | `0x1FFFA` | vectors: `nmi=$F9AB irq=$F9B3 reset=$F9C1` |

Implemented in `Assembler.slot_origin()` and inverted by `Assembler.prg_offset()`,
both in `asm/pds6502.py`.

**Corrections, 2026-10-02.** Two numbers in this section were wrong before today.
Recorded rather than quietly replaced, because both produced confidently wrong
addresses:

- **Slot 15 is at `0x1E000`, not `0x1D000`.** The old figure was off by one 8 KiB
  slot and put the fixed window 4 KiB out, which is enough to make every
  comparison in the window look like noise.
- **`+$10000` holds across the whole fixed window**, not `+$8000`. `+$8000` is
  right only for `$C000-$DFFF`; applied to `$E000-$FFFF` it lands 16 KiB low, in
  the bank-selectable region.

The `slot & 3` rule is also a correction. Before today this section stated only
that MMC3 fixes the top two slots, with no general rule, and `slot & 7` had been
assumed — which yields logical addresses above `$FFFF`.

X7's bank constants are 8 KiB slot numbers (`b = $6` … `b = $d` at X7.PDS:996-1088;
"always to bank $F" is the comment at X7.PDS:997),
which is 16 KiB banks 3–7. Do not read them as 16 KiB bank numbers.

## 5. Files written here

| file | origin |
|---|---|
| `tools/pds_extract.py` | new, this repository |
| `asm/` (PDS-compatible assembler, cartridge build) | new, this repository |
| `crates/` | **does not exist.** Intended as new work here; the CPU/PPU models would start from the user's own MIT-licensed `~/code/z2rs` crates, adapted and recorded here when that happens |
| `journal/01-six-modules-with-no-slot.md` | new, this repository; the session log for the rebuild work |

Nothing else is copied. `roms/` (which does not exist), `pds-text/`, build output and captured frames
are generated and git-ignored.

### Reference material deliberately not in this repository

Two downloads sit outside the repository on purpose. Neither may be committed,
and this section is the record of what they are and what was taken from them.

| file | what it is | what it is for |
|---|---|---|
| `~/Downloads/Magician-Game-Manual.pdf` | the official Taxan manual, 22 scanned pages, **no text layer** | the oracle for validating data tables |
| `~/Downloads/lpwb-magician-nes-walkthrough.txt` | auto-generated transcript of a third-party YouTube walkthrough, `https://www.youtube.com/watch?v=Kp3BWWMXApo` (Video Games 101) | gameplay rates and rules not stated in the source |

The manual must be OCR'd before it can be read — `tesseract --psm 6` at 300 dpi
was used, into scratch under `/tmp/opencode/magman/`, which is not durable. A
durable copy belongs in a private git-ignored location with the OCR text beside
it; it does not belong here, and it does not belong in `vendor/`, which is a
submodule of someone else's release. The walkthrough transcript is a third-party
copyrighted work: keep the URL and the extracted facts, never the text.

Facts taken from the manual, for the data-table validation they are meant to
drive: HEAL +20%, MANA +500, AMULET OF SHIELD +15% to all four shields, RING OF
ANA sets food and water to 100% once, AMULET OF MOR +50% once; bread 20%,
chicken 30%, ham 25%, vegetables 40%; water flask 5 drinks × 20%; the venom rule
("roughly 1/12th of this value is periodically subtracted from your health"); the
Magician rating thresholds keyed to max mana (APPRENTICE 0-499 … MASTER
4000-9999, MAGICIAN 10000+); and a per-level item and spell checklist for levels
1-8.

## 6. The rebuild: how it is built, and what it currently produces

`make -C asm` runs `asm/build.py`, which does four things.

1. **Decode** the eight `X?.PDS` containers (`tools/pds_extract.py`), 6 351 text lines.
2. **Assemble** them with `asm/pds6502.py`, a PDS 1.26 compatible assembler written for this
   source. `asm/build.py` finds each module's initial 8 KiB slot by assembling it in all sixteen
   slots and keeping the one whose output matches the cartridge, then re-assembles the whole
   project until no symbol moves, because the eight banks are one program.
3. **Pack CHR** from the `DAT/` artwork, in the order the development system's `SENDG` script
   sends it, resolving names case-insensitively (the Atari ST filesystem was case-insensitive and
   this one is not).
4. **Report** how much of the result agrees with the cartridge, plus the statement scanner's
   audit trail.

### It does not reproduce the retail cartridge, and is not trying to

Per section 3 the source is a development build and the cartridge is a later one, so exact
agreement was never the target — a *characterised* difference is.

**The table below was true of the state at `a817dcf` and is no longer.** It is left
as it was, because the numbers it records are the ones that came from an
assembler working, and it is worth being able to see what changed. Corrections
and current measurements follow it.

| measurement | value |
|---|---|
| modules assembled | 8 of 8 |
| PRG bytes identical to the cartridge | 14 887 / 131 072 (11.4%) |
| CHR 4 KiB pages identical | 8 / 32 |
| symbols recovered | 3 028 |

**Corrections, 2026-10-02.** Re-measured, with the same command, in three states:

| state | how reached | modules assembled | PRG identical | symbols |
|---|---|---|---|---|
| `a817dcf`, as committed | `python3 asm/build.py` | 8 of 8, **but six of them stacked into slot 15** and overwriting each other | 14 418 / 131 072 (11.0%) | 3 028 |
| deliberate "do not place an undetermined module" | one uncommitted change reverted | 2 of 8 — X6 and X7 only | 14 912 / 131 072 (11.4%) | 886 |
| working tree as it stands | `make -C asm` | **0 of 8** — the build raises before assembling anything | — | — |

So: **"8 of 8" was never a success.** It was the silent fallback: a module whose
slot the search could not determine was assembled at whatever slot the
sixteen-slot loop happened to finish on, slot 15, so six modules shared one 8 KiB
window. That is the ROM that boots to a black screen. And **3 028 symbols is
part of the same artefact** — it includes the symbols of the six modules that were
overwriting each other. With them correctly excluded the figure is 886.

14 887 (the old PRG figure) was not reproducible in any state measured on
2026-10-02; the nearest are 14 418 and 14 912. CHR 8/32 is unchanged in every
state, and is the one figure here still worth quoting.

### The build's current behaviour

An undetermined slot is now a hole that prints, not a guess:

```
*** INCOMPLETE BUILD: 6 of 8 modules have no determined 8 KiB slot and were NOT assembled:
***   X0.PDS, X1.PDS, X2.PDS, X3.PDS, X4.PDS, X5.PDS
***   The PRG above is missing them. Pass --allow-incomplete to exit 0 anyway.
```

`python3 asm/build.py` exits 1; `--allow-incomplete` exits 0 (both measured).
`make -C asm` therefore exits 2 as well, and `make -C asm check` and
`make -C asm rom` fail with it.

**As the working tree stands that message is unreachable.** An uncommitted change
to `asm/pds6502.py` raises `unknown instruction '0,1'` at `X7.PDS:184` from the
`PINNED_SLOTS` branch of `asm/build.py`, which is the one `run_file` call there
without a `try` around it, and `make` exits 2 before the message prints. The
mechanism and the fix it needs are in
`journal/01-six-modules-with-no-slot.md`.

Two side effects of leaving modules unplaced are worth stating because they look
like unrelated breakage in the log:

- The project pass then assembles X6 and X7 only, and since **X0 defines the whole
  zero-page and RAM map**, every symbol the other modules reference is
  undefined. `run_all` reports ~120 of them (`autodel, bookcur, botbuf, …`).
  That is a consequence of X0 being unplaced, not a new fault.
- In the working-tree state the six unplaced modules are rejected at **all sixteen
  slots for assembler reasons**, not for lack of evidence — `X0.PDS:188 lda
  needs an operand`, `X1.PDS:379 unknown instruction '7'`,
  `BUL.SRC:59 unknown instruction '03'`, `X4.PDS:133 expression ended early`,
  `MISC.SRC:194 unexpected token '$'` — and both cases print the same
  `SLOT NOT DETERMINED`. The message cannot currently distinguish them.

### What is anchored, and how it is known

- **X5 → slot 14.** Its `org $c000` lands on the `sql` table, which is at file `0x1C000`.
  Independently, scoring *all* of X5's emitted bytes picks slot 14 at 14.65%,
  the highest single module score measured. Note the tension: the slot is known
  by measurement, but the build does not place it, because the search's criterion
  only scores `incbin`'d bytes and X5 has none. Knowing a slot is not the same as
  the search being able to find it.
- **X7 → slot 15.** `reset` and `nmi` are defined in `X7.PDS` (lines 922 and 903), and the
  cartridge's vectors are `reset=$F9C1 nmi=$F9AB irq=$F9B3` — all inside slot 15's `$E000-$FFFF`
  window. This is also the only slot at which X7 assembles without tripping its own
  `error ">$FB80!"` consistency check.
- **CHR file identity.** The artwork in `DAT/` is byte-identical to the cartridge's; only the
  packing order is in question.

### Measured differences from the cartridge

- X7's own labels land **$1126 below** where the cartridge has them (`nmi` at `$E882`, cart
  `$F9AB`). The *relative* spacing is nearly right: `reset`−`nmi` is 25 bytes for us against 22
  in the cartridge, so 3 bytes are emitted too many between X7.PDS:903 and :922.
- The retail cartridge's slot 15 is non-zero all the way to `$FB80` and the bytes there
  (`38 30 28 38 30 0f 27 37 …`) are **not** `DAT/MUS/SAM.SAM`, which is what the source says is
  loaded at `$fb80`. `DAT/MUS/SAM.SAM` begins `aa 2a 1f e3 c7 18 01 00`. Slot 15 is non-zero for
  6 865 of the 7 040 bytes from `$E000` to `$FB80`. So the shipped X7 has different content in
  that region — the source-versus-cartridge difference section 3 predicts, not an assembler
  fault.

### Slot determination: what has been ruled out

**Ruled out — scoring all emitted bytes.** Not a discriminator for X0–X5. Best
slot per module, every emitted byte against the cartridge:

| module | best slot | score |
|---|---|---|
| X0 | 12 | 52/1294 (4.02%) |
| X1 | 9 | 156/2378 (6.56%) |
| X2 | 0 | 1626/8192 (19.85%) |
| X3 | 9 | 261/3208 (8.14%) |
| X4 | 0 | 1704/8192 (20.80%) |
| X5 | 14 | 1200/8192 (14.65%) |

Flat or self-contradictory: X2 and X4 both peak at slot 0, X1 and X3 both at slot
9, and X0 peaks at slot 12, which is X6's slot. Two modules claiming one slot is
the metric disagreeing with itself. The cause is understood and is not a bug to
chase: while cross-bank forward references are unresolved every `lda label`
chooses zero-page over absolute, so most bytes are noise at *every* slot. Do not
re-run this without the other banks' addresses known first.

**Ruled out — "X0, X6 and X7 are the only data modules (1, 1 and 79 uses)".** The
shape and two of the three counts are right; X0's is off by one. Measured over
the decoded source — logical lines split on the soft wrap, comments stripped,
counting `load` as a token rather than only where it starts a line, since most
calls are `label<TAB>load file`, and separating the macro definition from calls:

| module | `load` calls | `load macro` defs | raw `dat\` lines |
|---|---|---|---|
| X0 | **0** | 1 | 2 |
| X1–X5 | 0 each | 0 | 0 |
| X6 | 1 | 0 | 0 |
| X7 | **79** | 0 | 0 |

X0 has no calls: the macro is *defined* at `X0.PDS:75`, its body is
`incbin dat\@1` at `X0.PDS:86`, and X0's two `dat\` mentions are that body plus
the comment at `X0.PDS:72`. X0 defines the macro X6 and X7 call. The load-bearing
half of the old claim is right: **X1–X5 are pure code**, which is why a
byte-scoring search has nothing to score for them.

### Open, in the order they pay off

1. **Give `scan_statements` a segment index, then re-run the build.** Everything
   else is downstream. `Word.col0` is recorded per *physical segment* and
   `Word.line` per *logical* line, so the scanner cannot tell "this token starts a
   new physical line" from "this token is later on the same one". Both readings
   are needed and neither is right: with the column test reverted, `X4.PDS:160`
   loses `pca1`/`pca2`; with it kept, `X7.PDS:184`'s `swapstk 0,1` loses its
   operand and the build crashes. They are mutually exclusive today.
2. **Distinguish "no evidence" from "did not assemble"** in the slot-search log.
   Both print `SLOT NOT DETERMINED` today, and failure mode 1 is invisible in it.
3. **Place X0–X5.** The live hypothesis: X0–X4 fill 16 KiB banks 0–4 (initial
   slots 0/2/4/6/8, or the odd parity 1/3/5/7/9), X6 at slot 12, X5 at slot 14 and
   X7 at slot 15 sharing bank 7 as the fixed window. **Untested** — the probe that
   tries it fails with `undefined symbol(s): pca1, pca2`, which is item 1.
4. **Close the `$1126` vector gap** (see below). Until it closes the CPU is sent
   to the wrong address and nothing else matters.
5. **Then the CHR packing order.** The artwork is byte-identical; only the order is open.
6. **Only then `crates/`.** Nothing about it is started.

`exitm` is called from the `st` macro (`X0.PDS:34`) and defined nowhere. It
assembles as a no-op label, which is byte-neutral, but it is an unverified
reading. Still true, still open, and lower down the list than it was.

The project-pass item "`a`, `bpw`, `btit` are unresolved" from the previous
revision of this section is **closed**. `a` was the accumulator in `asl a`; `bpw`
and `btit` were `undefinable` only because an unterminated string in `memchk`
stopped the scanner closing a conditional, so the rest of X7 assembled inside a
false `if`. Both are fixed in `asm/pds6502.py`. The project pass still fails, for
the entirely different reason given above.

### Does the rebuilt ROM run?

`asm/mkrom.py` joins the two images behind the cartridge's own iNES header (PROVENANCE section 1)
into `asm/out/magician-rebuilt.nes`, 262 160 bytes with a byte-identical header. Verified in
BizHawk 2.11.1 under Mono: it loads and the core reports `Mapper #4 "mmc3"`,
`BoardID: "mmc3"`, with the BootGod hash matching the rebuilt body.

**It does not boot to the title screen — a black screen — and the reset vector says why:**

| | rebuilt | cartridge |
|---|---|---|
| `nmi` | `$E882` | `$F9AB` |
| `reset` | `$E89B` | `$F9C1` |
| `irq` | `$E88D` | `$F9B3` |

Two emulator findings, both measured, that a future session should not re-derive:

- **`--gdi` works; `--dump-type png` and `--chromeless` do not.** BizHawk's Mono
  build needs a GL context to run a core, and there is **no Xvfb on this machine**,
  so PNG frame dumping produces nothing and a Lua RAM probe dies at `client.cpu`
  because the client loop never starts. `--chromeless` still constructs the OpenGL
  control. `--gdi` (Mono/libgdiplus) is the way to see a window. There is no
  `xdotool` and no `wmctrl`; `python-xlib` and `PIL` are available and windows can
  be moved over X11 directly. ImageMagick 7's `import` rejects its own filename
  argument on this box — use `python-xlib` plus PIL for screen grabs.
- **The stock cartridge's session was not a cold boot.** It showed `LEVEL CAVERNS`
  and `GOLD 1500` because the cartridge has battery-backed PRG RAM (`f6 & 2`) and
  BizHawk resumed a stale SRAM save. Any future "does it boot" comparison must
  clear SRAM on **both** sides or it compares a resumed save against a cold boot.

X7's slot is pinned to 15 from this vector evidence rather than searched (see
`asm/build.py: PINNED_SLOTS`) — it is the only slot whose `org $fffa` reaches file `0x1FFFA`. Before
that pin, x7 was assembled at slot 12, `org $fffa` landed at file `0x19FFA`, and the rebuilt ROM's
reset vector was `$8681`, pointing into a different bank entirely.

Pinning it fixed the bank but not the offset: the vectors are now in slot 15, `$1126` below the
cartridge's. The relative spacing is preserved — `reset`-`nmi` is 25 bytes against the cartridge's
22 — so 3 bytes are emitted too many in one small span, and the remaining gap is accumulated
earlier in X7. That gap is item 4 in the list above; until it closes the CPU is sent to the wrong
address and nothing else matters.

Confirming "it reaches the title screen" on this machine would need Xvfb or a headless NES core;
neither is installed. That is the only part of the boot question still unmeasured — the black
screen is not, and the vectors are the reason for it.

One approach that has not been tried and is worth keeping: the cartridge's reset vector is a known
2-byte value and `start` is the first routine in `X0.PDS:602`, so placing X0 by matching `start`
against the vector is a way in. The search has to score code with the *other* banks held fixed,
iterating rather than assuming.

### The artefacts under `asm/out/` are stale and disagree with each other

`asm/out/magician-rebuilt.nes` (15:45) carries the vectors BizHawk was tested
against, `nmi=$E882 reset=$E89B irq=$E88D`. `asm/out/prg.bin` (16:06, sha256
`f91dead1111b74f9`) is a *later* run whose vectors are `$29E8/$42E8/$34E8`, and it
is not the PRG inside that `.nes`. `make -C asm rom` would produce a third ROM
from a fourth state. Everything under `asm/out/` is git-ignored, and none of it
should be trusted as a measurement without re-running the build first.

### The one content-based drift check in this repository is red

`tools/pds_extract.py --check` exits 1 and reports **all eight** `pds-text/*.pds`
as `STALE`. `pds-text/` is generated and git-ignored, so this costs nothing today,
but it is the only check here that compares content rather than timestamps, and
nothing runs it. There is no generated documentation in this repository, so there
is no documentation gate to add; if one is ever added it must be content-based,
not mtime-based.
