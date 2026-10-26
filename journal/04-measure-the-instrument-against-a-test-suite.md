# 04 - Measure the instrument against a test suite

2026-10-03

Append-only, in the style of `03-the-tracer-was-the-instrument-and-the-instrument-was-wrong.md`.
Where this entry contradicts an earlier one it says so and gives the measurement.

**Where things stood.** HEAD `0c06440`, branch `fix/boot-from-source`, PR #1.
Byte-match baseline is unchanged by this session and by every session before it:
**39 832 / 131 072 (30.4%)**, of which **38 982 source-only** and **864 from one
class-b manifest region**. Nothing here moves that number, because nothing here
touches the assembler. This entry is entirely about whether the tracer can be
believed, and the answer changed from "no" to "mostly".

**The deliverable is still not met.** The rebuild does not show a picture. What
is new is that the reason is now known to a specific instruction, and the
cartridge -- the control -- is now measured rather than assumed.

---

## 1. The instrument had three faults that produced confident wrong answers

`03-...md` said the tracer could not be trusted and that until it was, every
measurement taken with it was uninterpretable. That was right, and the reason was
three concrete defects rather than one vague one. All three are found by
koute's `nes-testsuite`, checked out read-only under `/tmp/opencode/pinky` and
never committed: `--testsuite` loads a testcase JSON, **refuses to run a ROM whose
md5 does not match the testcase**, runs it for `elapsed_frames * 2` frames, md5s
the 256x240 framebuffer and prints a per-suite table. The expected framebuffer is
one byte per pixel, and that byte is the 6-bit palette RAM entry
(`FramebufferPixel::base_color_index`), so the comparison depends on palette RAM,
the nametables, the attribute table and CHR, and on nothing else.

### 1a. INC and DEC on memory set no flags at all

```python
elif n == "inc":
    bus.write(addr, (bus.read(addr) + 1) & 0xFF)
elif n == "dec":
    bus.write(addr, (bus.read(addr) - 1) & 0xFF)
```

No N, no Z. Real INC/DEC set N and Z from the memory result and leave C alone.
Invisible in a straight-line test, lethal in a loop:

```
$E23C: ldy #$00
$E240: sta $F2        ; A = $3B
$E242: ldx #$08
$E246: sta $2007      ; 8 zero bytes
$E249: dex / bne
$E24C: ldx #$08
$E24E: lda ($F0),y
$E250: iny
$E251: sta $2007      ; 8 data bytes
$E254: dex / bne
$E257: tya
$E258: bne $E25C
$E25C: dec $F2
$E25E: bne $E242
```

That is `branch_timing_tests/1.Branch_Basics` uploading its font. The inner
loops leave Z=0 while Y is non-zero, so `bne` holds for 32 iterations -- and then
`ldy #$00 / iny`-style code somewhere upstream leaves Z=1, `bne` falls through,
and the loop exits **after 32 of 59 iterations**. 512 bytes of a 944-byte font.
The nametable then holds `BRANCH TIMING BASICS / PASSED` as tile numbers 32, 65,
66, 67 ... and every tile from 64 up points into CHR that was never written. The
screen is not wrong-looking; it is uniformly `$0F`, because all 61 440 pixels read
back as the backdrop.

Nothing errored. `branch_timing_tests` 1-3 and `oam_read` all pass after the fix,
and all four failed before it.

### 1b. Every CHR-RAM cartridge was treated as CHR-ROM

`load_rom` substitutes 8 KiB of CHR-RAM when the header's CHR count is zero, so
that the array exists. `Bus.__init__` then asked `len(chr_) != 0` whether CHR was
ROM, and the answer was always yes. `ppu_write` returned before storing, so
**every tile a game uploaded to CHR-RAM vanished**: a nametable full of tile
numbers pointing at nothing. Four of the suites are CHR-RAM cartridges and all
four rendered one flat colour. `chr_ram` now comes from the header byte.

This one also applied to **both Magician images**, which are MMC3 with CHR-RAM.

### 1c. The MMC3 scanline counter did not exist

`_mmc3_irq_check` was `pass`. Nothing set `irq_pending`, and nothing ever handed
it to the CPU, so `cpu.irq_pending` stayed false for the whole run and **the IRQ
vector was never taken by either image**. That is the direct cause of the
"$8382 / lda $40 / bne" deadlock reported in `03-...md` §2 -- not a wrong flag, a
missing interrupt.

It is now clocked on the rising edge of PPU address bit 12: once per rendering
scanline at dot 260 of scanlines 0-239 and the pre-render line (241 per frame,
the number `mmc3_irq_tests/2.Details` checks), plus every CPU access to `$2006`
and `$2007`, which is how the MMC3 ROMs drive the counter by hand. Per-clock
behaviour is the one Shay Green measured on real cartridges and wrote up in
`roms/mmc3_irq_tests/readme.txt`: reload pending reloads without decrementing,
counter 0 reloads, otherwise decrement, and raise only if the result is zero and
IRQs are enabled. `$C001` acknowledges, zeroes and arms the reload; it does not
raise an IRQ itself.

### 1d. Three smaller ones, all in the PPU

- `$2000` bit 4 is the *background* pattern table select. The code used bit 5 and
  took its complement, so every background tile came from the wrong table and the
  nametable address was built out of it as well.
- `_inc_v` stepped by 32 or 1 on `$2000` **bit 2**, which is unused. Every game
  that uses the increment bit filled its nametable one byte at a time down a
  column.
- Vertical scroll was read out of `t`, the write-only register, so a game that
  scrolls with `$2005` and never `$2006` got no vertical scroll at all. It now
  snapshots `v`, copies the vertical bits from `t` once per frame and the
  horizontal bits once per scanline, and walks the real increment-Y including the
  coarse-Y-29 wrap that flips nametable Y.

Also: the mapper is now dispatched on the iNES header instead of assumed MMC3
(NROM's 16/32 KiB shapes; MMC3's eight 1 KiB CHR windows and CHR A12 inversion,
which Magician needs and which had never been implemented at all); an unmodelled
mapper raises rather than running, because a ROM on the wrong bank map produces a
confident wrong answer; sprite priority and OAM order are honoured; sprite-0 hit
and overflow set their status bits; OAM DMA costs 513 cycles.

---

## 2. Corrections to earlier entries, with the measurement

**"The cartridge and the rebuild both spin at `$8382: jsr rn / lda $40 / bne
$8382`, waiting on a ZP flag that exactly one `sta $40` can clear."** Wrong, and
wrong twice over. `$8382` is `ptlr`, a player-animation table inside module X4
(`x4.pds:202`), and the tracer reached it only because `sta $40` never happened
because the MMC3 IRQ never fired (§1c). With the interrupt delivered, `$8382` is
the sound engine's `eor $07A0 / bne` and the main loop runs normally.

**"the ceiling dropped `initspr`"** — already corrected in `03-...md`; recorded
here only so the two entries agree.

**"A 256-byte stack underflow in the rebuild."** I measured SP reaching `$00` and
was about to write it up as a fault. It is not. `emptydma` (`x7.pds:871`) begins
with `swapstk 0,1`, and `swapstk` (`x0.pds:208`) is `tsx / stx stk0 / ldx stk1 /
txs` with `stk1` set to `$7e` by `initdma` (`x7.pds:867`). **SP = `$7E` inside
`emptydma` is the design**, not an underflow; my threshold was wrong. Annotated
here because the wrong number is exactly the kind that gets quoted later.

---

## 3. The cartridge boots. Here is what it does, measured

150 frames, `tools/nestrace.py --rom "<cart>" --frames 150`:

| | cartridge | rebuild |
|---|---|---|
| runs without halting | yes | halts, frame 194 |
| NMI raised | 145 | 150 |
| MMC3 IRQ taken | 8 107 | 1 077 |
| MMC3 latch programmed | `$51`, 147 times, every frame | `$5F`, **11 times** |
| `sta $2001` (rendering on) | 198 | 12 |
| `setchr` reached (`$F75E`) | once | -- |
| `curchrpal` after 200 frames | `0F 28 38 30 0F 2A 3A 30 0F 17 27 38 0F 21 31 30` | -- |
| nametable bytes nonzero | 55 / 2048 | 57 / 2048 |
| picture | **no** | **no** |

So the cartridge is a long way further along than `03-...md` claimed, and it is
still not drawing. What it *does* now do, all of it measured rather than assumed:
it takes NMI, takes MMC3 scanline IRQs, runs its sound engine (writing `$4000`
and `$4002`), does OAM DMA, scans the joypad, calls `setchr`/`movepal` once and
`curchrpal` ends up holding exactly the title palette -- `TIT.PAL`'s
`28 38 30 2A 3A 30 17 27 38 21 31 30` with `$0F` in the four backdrop slots that
`movepal`'s `and #$03 / cmp #$01` forces. The palette pipeline works end to end.

It still draws nothing, and `$2007` is only ever aimed at `$3F00` and at sixteen
bytes per nametable block. **The cartridge is not the control it was assumed to
be.** "The real cartridge shows a title screen, therefore a black rebuild screen
is a statement about the rebuild" does not hold: the cartridge is black too, in
this instrument. Something is still missing from the tracer, and it is on the
graphics side.

The two candidates the measurements actually point at:

1. **There is no APU.** `$4015` reads 0 and nothing else in `$4000-$4017` exists.
   The sound engine is entered 41 000 times per 150 frames and polls three RAM
   bytes at `$07FC-$07FE`; whether anything upstream of that depends on `$4015`
   is unknown and unmeasured.
2. **Cycle-granular PPU timing.** The sprite-hit and vbl/NMI suites -- 18 of the
   19 failures in §4 -- are all timing tests, and `tick` advances the PPU once per
   instruction, so vblank is detected up to seven CPU cycles late and sprite-0
   hit has no dot at all.

Neither is implemented. Saying so is cheaper than guessing.

---

## 4. Where the rebuild actually dies

Not "at `$8382`", and not a palette problem. At **frame 14, cycle 439 272**:

```
439266 $9D6C: rts   A=28 X=FF Y=FF S=$70 R6=6 R7=1
439272 $0000: brk   A=28 X=FF Y=FF S=$72 R6=6 R7=1
439279 $F9B3: pha   ...                       <- BRK vector, not IRQ
```

`$9D6C` is `rts`; it popped a return address of `$FFFF` and went to `$0000`,
which is `brk`, which vectors to `$F9B3` and runs the *IRQ* handler with A=`$FF`.
That handler writes `$FF` to `$E000` and `$E001`, and `$E001` bit 7 **disables**
the MMC3 IRQ -- which is why the rebuild takes 1 077 IRQs where the cartridge
takes 8 107, and why `sta $2001` happens 12 times instead of 198. From there the
handler's `jmp $000B` lands mid-instruction and the rebuild executes data; by
frame 194 it is at `$FFC0` in the fixed bank, where there is **no `jsr $FFC0`
anywhere in the rebuild's PRG and no symbol at all** -- sprite/animation tables,
with a `$52` (`LXD`, a genuine 6502 freeze) at `$FFCA`. The tracer halts there,
correctly.

The stack was already 144 bytes deep at the bad `rts`. That is not the
`emptydma` queue (§2); it is the main stack, and it matters because `initdma`
parks the DMA queue at `$7E`, i.e. the queue and the main stack share page 1 and
the main stack has only `$FF`-`$80` before they meet. The rebuild is executing
something that pushes without a matching pop long before frame 14.

So: **the first divergence is a destroyed return address at `$9D6C` at frame 14**,
and everything after it -- the disabled IRQ, the black screen, the `$FFCA` freeze
-- is downstream of that one instruction. That is a much smaller target than
"30.4% of bytes match".

---

## 5. What the suite says, per subsystem

Full table in README.md §"Tracer conformance". **68 testcases: PASS 7, KNOWN-FAIL
7, FAIL 34, ERROR 19, UNSUPPORTED 1.** Of the 41 that carry a real expected
picture, 7 pass.

| subsystem | result |
|---|---|
| CPU branches and timing | `branch_timing_tests` **3/3** |
| CPU addressing | `instr_misc` **2/3** (`03-dummy_reads` wants the 6502's dummy reads) |
| OAM | `oam_read` **1/1** |
| sprite 0 hit | `sprite_hit_tests` **1/11**, `sprite_hit_timing` 0/1 |
| vblank / NMI timing | `vbl_nmi_timing` **0/7** |
| APU | `apu_test` **0/8**; `blargg_apu` 0/7 but both agree with pinky (see below) |
| PPU registers | `blargg_ppu_tests` **0/3**, and 3 ERRORs on KIL opcodes |
| MMC3 IRQ | `mmc3_irq_tests` **0/6**, all reporting failure code 2 |
| CPU, everything | `instr_test-v5` **0 usable** -- see below |
| mapper coverage | 1 UNSUPPORTED (`holy_diver_batman`'s single MMC1 case) |

Three of these need reading carefully rather than counting.

**`blargg_apu` and `blargg_ppu_tests` are expected to fail.** Ten of the 46 JSON
testcases expect `0941a56e4c62c6026264952a9bfaea35`, which is blargg's own failure
screen; pinky recorded those as failures too, because pinky has no APU either.
Those rows are reported `KNOWN-FAIL` -- "we reproduced the reference's failure" --
and a `PASS` there would mean we disagree with pinky. The seven `blargg_apu`
rows are all of this kind. `blargg_ppu_tests` is *not*: its three testcases
expect real pictures, and we produce none, and two of them now halt on KIL
opcodes (`$42` at `$E0A3`, `$02` at `$E339`/`$E413`) that they should never
execute. Unfixed.

**`mmc3_irq_tests` 0/6, all with `$F8 = 2`.** Code 2 is each ROM's *first*
sub-test, so the counter is failing at the most basic level, not on a timing
detail. The likely cause is these ROMs being built for blargg's devcart --
`runtime_swapcart.asm` and `prefix_swap.asm` are in their source tree and expect
the program to rewrite its own `$8000` bank mid-run -- but I have not verified
that, so it stays a hypothesis. The scanner it needs *is* implemented, and §1c's
clocking rule is taken from these ROMs' own readme.

**`instr_test-v5` is not usable here, and I am reporting that rather than
rounding it to a pass.** Its sixteen ROMs want thousands of frames of real time;
at ~660 000 cycles/s in Python that is hours per ROM, so the 120-frame budget
expires with the suite still `$80` (running). Worse, my result-byte check is
itself wrong: I look for `$DE $B0 $47` at `$6001`, but no ROM in the set carries
it at the point I sample, and three of them halt on KIL opcodes inside page 0
(`$02` at `$03A2`), which means they ran off their own code long before. So the
`instr_test-v5` rows are `ERROR`, not `INCOMPLETE`, and the honest reading is
"not yet measured", not "measured and failing". The wiring reads `$6000` and
honours the documented signature; it needs the actual convention re-read from the
source before it means anything.

**What is trustworthy now.** `branch_timing_tests` 3/3 and `oam_read` 1/1 are
strong: they are framebuffer-exact against a reference, and they are the two
subsystems the game's own timing depends on. `instr_misc` 2/3 puts a bound on the
CPU. The eighteen failures in `sprite_hit_tests` + `vbl_nmi_timing` are all
dot-level timing, which `tick` cannot express -- it advances the PPU once per
instruction, so vblank is seen up to seven CPU cycles late and sprite-0 hit has no
dot at all. That is one missing capability, not eighteen bugs.
