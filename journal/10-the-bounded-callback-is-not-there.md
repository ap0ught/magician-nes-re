# 10 — Read the API, measure the origin, and do not fill what is not there

*2026-10-04. Tasks: (1) is there an address-bounded memory callback in BizHawk
2.11.1; (2) X5's intra-slot origin and the first divergent MMC3 bank frame;
(3) the ~606 bytes of absent scene descriptors and the release's `unrun`.*

Three questions, three answers, and two of them are negative results worth more
than the positive one they replace. Written during the work, not reconstructed.

---

## 1. The bounded memory callback does not exist. Do not retry it.

### What went wrong before

`tools/bizhawk/writes.lua` registered `event.onmemorywrite(cb)` with **no
address** and BizHawk logged

```
quickerNES does not implement wildcard memory callbacks
```

The word *wildcard* reads like a claim that bounded callbacks are implemented, and
two sessions were spent on that assumption. `bankprobe.lua` then tried six
registration shapes and none fired — but none of the six was the documented one,
so that was a negative result about six guesses, not about the API.

### The fix was to read the shipped documentation

BizHawk ships its own Lua API definitions **inside the install**:

```
~/code/games/aibeatszelda/BizHawk-2.11.1-win-x64/Lua/_docs_luacats/event.d.lua
```

which says

```lua
function event.on_bus_write(luaf, address, name?, scope?) end
function event.onmemorywrite(luaf, address, name?, scope?) end   -- deprecated
```

Four arguments, **address second, scope fourth**. None of `bankprobe.lua`'s shapes
matched that. Two sessions of guessing a signature that was written down, in a
file, in the same directory as the game's own Lua scripts.

### But the bounded form does not work either, and here is why

`tools/bizhawk/buswrite.lua` registers the documented form plus seven variants and
decides registration from the **return value**, not a fire count. That works
because of two things in BizHawk's own IL (`ikdasm dll/BizHawk.Client.Common.dll`,
`dll/BizHawk.Emulation.Cores.dll`):

* `EventsLuaLibrary.OnBusWrite` calls
  `EmulatorExtensions.MemoryCallbacksAvailable(debuggable)`, which calls
  `IDebuggable.MemoryCallbacks` inside a `try` that catches
  `NotImplementedException` and returns **false**.
* On false it falls through to `LogMemoryCallbacksNotImplemented(isWildcard)` and
  `return EMPTY_UUID_STR`. `EMPTY_UUID_STR` is `Guid.Empty.ToString("D")`, the
  literal `00000000-0000-0000-0000-000000000000`.

So each registration returns a real GUID or that exact sentinel, and **the return
value alone says whether the callback exists**. That is a far better test than
"did it fire", which cannot distinguish "no hook" from "the game never wrote
there".

Measured, all three agreeing:

```
availableScopes()    -> {}
  bus_write  $8000 sysbus          REFUSED (Guid.Empty)
  bus_write  $8001 sysbus          REFUSED (Guid.Empty)
  bus_write  $8000 noscope         REFUSED (Guid.Empty)
  memorywrite $8000 sysbus         REFUSED (Guid.Empty)
  memorywrite $8000 noscope        REFUSED (Guid.Empty)
  bus_write  $2007 sysbus          REFUSED (Guid.Empty)     <- positive control
  memorywrite $2007 sysbus         REFUSED (Guid.Empty)
  memorywrite <no address>         REFUSED (Guid.Empty)
8 attempted -- 0 REGISTERED, 8 REFUSED, 0 threw; 0 fires
```

and, in the log window, the message appeared **seven times without the word
"wildcard"** — the seven bounded calls — and once with it, for the wildcard one.

**That last observation is the whole finding.** The word `wildcard ` is
interpolated into the message by `LogMemoryCallbacksNotImplemented` *only when no
address was supplied*. It describes **the call**, not the core's capability. In
`dll/BizHawk.Emulation.Cores.dll`, `QuickNES.get_MemoryCallbacks()` is

```il
.method public hidebysig newslot specialname virtual final instance IMemoryCallbackSystem get_MemoryCallbacks()
{
  .custom ... FeatureNotImplementedAttribute::.ctor()
  IL_0000: newobj instance void System.NotImplementedException::.ctor()
  IL_0005: throw
}
```

— unconditional. QuickNES is the only NES core in this build, so there is no
alternative.

**Conclusion: there is no per-instruction `$8000`/`$8001` write trace from this
BizHawk build, in any form.** Option 1 of the task list is closed, permanently.
Option 2 (`memory`-domain watching) is closed with it: `IMemoryCallbackSystem` is
the only mechanism and it throws.

### What does work: option 3, per-frame polling

`bankprobe.lua` polls the `System Bus` domain once per frame and reads the game's
**own** copy of MMC3 registers R0–R7 from zero page `$00-$07`
(`X0.PDS:363-370`), plus `bnksel` (`$2D`) and the per-level bank table
`mapbnk70`..`mapbnk7f` (`$65-$74`). That is legitimate: `journal/07` retraction 3
established the zero-page layout is byte-identical on both images, so the same
address means the same name on both sides.

Its coverage is asserted, not assumed, and it is worth noting *how*, because three
sessions were lost to instruments that printed plausible numbers for the wrong
thing:

* the domain that answers is identified by **reading back after writing**, because
  `memory.usememorydomain` with an unknown name is not an error in 2.11.1 — it
  silently leaves the previous selection in place;
* zero page is read through **two** domains and cross-checked byte for byte
  (`WRAM` is `$FF` in every byte until frame 45; the real work RAM is the
  2048-byte `RAM` domain — this was `journal/07`'s finding and it has already
  cost one false result here);
* the frame loop's coverage is counted and printed, and the file ends with a
  `done` sentinel `run.sh` waits for;
* `bankdiff.py` **refuses to compare** two reports that are truncated, have a gap
  in their frame numbering, disagree with their own sentinel, or whose zero-page
  coverage is not 256/256. It exits non-zero rather than printing "no divergence".

`$8000`/`$8001` read as **PRG through the R6 window**, not MMC3 registers — proven
against the file, and the log says so. There is no register read-back on this
core. So the poll reads the game's mirror, not the mapper.

---

## 2. X5's intra-slot origin is 0, and it was never wrong. The table was SEQ.SRC's.

`journal/09` reported one table — the 32-byte ascending tile counter, at slot-5
offset `$1471` here and `$14CE` in the cartridge — and concluded "the placement of
X5 within slot 5 is wrong". Two separate errors.

### Error one: the module

`mag.sym` has `d43 = $B471`. `$B471` is in MMC3 **register 7's** window. X5.PDS:15
has its own `org $c000` and `asm/build.py` puts X5 in **fixed slot 14**. The
ascending table is **SEQ.SRC's**, at slot 5. `tools/seqtab.py` says so, and
`tools/slotalign.py` measures it.

### Error two, and the one that matters: a displacement is not an origin

An origin is a property of a whole slot. The displacement at one offset is the
**running sum of every length difference before it**. One table cannot distinguish
those.

Measured across slot 5 (`tools/slotalign.py --slot 5`), the displacement is
piecewise and non-monotone:

```
+0x0000  +0x001e  +0x0009  +0x0022  +0x0018  +0x000f  +0x0003  +0x000a
+0x0002  +0x003a  +0x0045  +0x0056  +0x004a  +0x005a  +0x004f  +0x0072
+0x005d  +0x007f  +0x0075  +0x006b  +0x0081  +0x0073  +0x0086
```

carried by equal runs of 24 to 574 bytes, and changed by **45 inserts and 56
deletes** inside the slot (1389 cartridge-only bytes, 1417 rebuild-only).

`+0x5d` is the **mode** of the 293 single-hit tables because those tables cluster
*late* in the slot. It is not an origin. Two independent checks:

* slot 5's best-agreement shift is **0** — 1312 bytes, against 1025 at `+0x5d`;
* the first 90 bytes of slot 5 are byte-identical.

**SEQ.SRC's origin is 0, and that is what the build already does.**

### X5's own origin, now measured

`tools/slotalign.py --module sql`:

```
symbol      $C000  ->  fixed slot 14 ($C000 window)
identical run from intra-slot offset 0: 768 bytes
VERDICT: intra-slot origin is 0, and it is MEASURED
```

768 bytes from slot-14 offset 0 agree exactly, and that range contains X5's
256-byte `sql` table, which occurs **exactly once** in the whole 131072-byte PRG —
at slot-14 offset 0. `asm/out/build.log` still prints `X5.PDS ... ASSUMED, NOT
MEASURED`; that line is stale.

### A trap in the origin test itself, recorded so it is not repeated

Slot 14's *modal displacement* is `-0x0664`, and an exhaustive scan agrees
(`-0x0664` scores 2009 bytes against 1179 at shift 0). That is **wrong**, and it
is wrong because slot 14 is mostly repeated table data, so a non-zero alignment
scores higher by accident. `slotalign.py` therefore separates the two: the verdict
uses the **identity run from offset 0**, and prints the modal displacement only as
a labelled reference with an explicit note that it is not the origin test. An
earlier draft of the tool used the modal displacement as the verdict and would
have reported X5 as misplaced by 1636 bytes.

---

## 3. The bank sequence: first divergent frame **1166**, and it is a level-data group

`bankdiff.py` on two 1201-frame `bankprobe.lua` reports. Getting there needed
`MAGICIAN_INPUT`: with no input both images sit on the title screen for 600 frames
and R6/R7 only ever take `00, 01, 07`, so the run **cannot** answer the level-group
question — and `bankdiff.py` says exactly that rather than reporting "no
divergence":

```
  R6/R7 values seen: [0, 1, 7, 255]
  <-- no 06..0D: LEVEL GROUPS NOT REACHED
```

`bankprobe.lua` grew a `MAGICIAN_INPUT` schedule (`90:Start,150:Start,200:Up,
260:Start`) applied through `joypad.set`, which clears every button before each
press — otherwise a press inherits and the script holds Start for the rest of the
run. Each press is echoed with the frame it landed on. **A malformed schedule is a
hard failure with a message**, because a run that pressed nothing and looks like a
run that pressed the wrong thing is this project's standing failure mode.

### The result

```
frames logged        601 -> 1201 per image, zero-page coverage 256/256,
                     System Bus vs RAM: 0 differing bytes over every frame
r0-r7                differs at 31/1201 frames, first 1166, in 3 runs:
                     1166, 1169, 1172-1200
```

| frame | cartridge R0–R7 | rebuild R0–R7 |
|---|---|---|
| 1165 | `FF FF 3F 40 41 42 00 01` | `FF FF 3F 40 41 42 00 01` |
| **1166** | `FF FF 3F 40 41 42 00 0A` | `FF FF 3F 40 41 42 00 01` |
| 1169 | `FF FF 3F 40 41 42 00 0A` | `FF FF 3F 40 41 42 00 01` |
| 1172 | `FF FF 3F 40 41 42 00 07` | `FF FF 3F 40 41 42 00 01` |
| 1177 | `FF FF 40 41 42 43 00 01` | `FF FF 3F 40 41 42 00 01` |
| 1200 | `FF FF 40 41 42 43 00 01` | `FF FF 3F 40 41 42 00 01` |

**The MMC3 registers R0–R7 are identical for 1165 frames and first differ at frame
1166**, where the cartridge programs **R7 = `$0A`** — `10`, inside the level-data
group range `b = $8..$d` — and the rebuild keeps `$01`. R7 values seen over the
whole run: cartridge `{01, 07, 0A, FF}`, rebuild `{01, 07, FF}`. **The rebuild
never enters the level-data group range at all.**

From frame 1177 the cartridge has also moved R3/R4/R5 to `$41/$42/$43` and does
not return. And the reason is visible in the same poll: the cartridge's
`curlev`/`oldlev`/third variable reach `0 / 225 / 65` at about frame 1170 and the
rebuild's stay `0 / 0 / 0`. **The cartridge gets into a level and the rebuild
never leaves the title.** The bank-sequence divergence at 1166 is a *symptom*.

Honest limit, stated because it is the limit of the instrument: this is a
once-per-frame poll, so a bank switch issued and replaced inside one frame is
invisible, and the two isolated differences at 1166 and 1169 could each be poll
timing. The continuous divergence from 1172, and the `lev` difference, are not.

### The other divergence, and it is the earliest one in the whole run

`mapbnk72` — the per-level bank value for group 2 — is in the table `initlev`
copies out of `ld10`. Traced frame by frame:

```
cart  f0000=FF  f0002=3F  f1165=34
reb   f0000=FF  f0002=3F  f0003=38
```

Both images agree (`$FF`, then `$3F` from frame 2). At **frame 3** the rebuild
moves it to `$38` and the cartridge leaves it at `$3F` until **frame 1165**, when
it moves to `$34`. So the rebuild performs a level-bank-table write one frame
after init that the cartridge does not perform for 1162 frames, and when the
cartridge finally does write it, it writes a **different value**.

This is a **data** divergence, not a bank-sequence one, it is the earliest
bank-related difference between the two images, and it is still unexplained. It is
almost certainly downstream of the same thing that keeps the rebuild on the title
screen. `ld10` is `MISC.SRC:1104`; `initlev` is `X1.PDS:530-546`.

---

## 4. Task 3: class `e` added; **zero** regions filled, and the reason is the finding

### The retraction the briefing asked for, made

`journal` previously concluded "the picture cannot be made to match the
cartridge". That was overstated and is withdrawn: the delta is not "the artwork is
a different revision and there is nothing to do", it is a specific, bounded set of
absent assets plus one routine. Class `e` now exists in `asm/patches.py` with its
own meaning and its own prose in the manifest.

`b` and `e` are both filled from a cartridge and are **not** the same finding:

* **`b` is a hole.** The source emits nothing at the address. `sam-samples-at-fc40`
  is 864 bytes at `$FC40` where the February 1990 source has neither code nor
  data.
* **`e` is occupied by the wrong asset.** The source *does* emit bytes, they are
  wrong, and no re-placement fixes them, because what belongs there is an asset
  this source does not contain in any form. Calling that `a` or `c` would be a
  lie: there is no slot to move and no assembler bug to fix.

`a` and `c` remain unfillable and the parse-time refusal now names both fillable
classes.

### Why neither scene descriptor was filled

The briefing's premise is that the release's copy of `TIT.DAT`/`PW.DAT` is
*absent in any form* and therefore can be taken from the cartridge. That part
measures true. The part that does not follow is that **the cartridge's bytes at
the addresses our `load` puts them are the release's version of those assets.**
They are not.

`X7.PDS:997-999` loads `tit.dat`, `pw.dat` and `pan.dat` into PRG slot 7. This
build puts them at slot-7 offsets:

| file | size | this build | cartridge |
|---|---|---|---|
| `TIT.DAT` | 314 B | `$0BA7` | not present in any form |
| `PW.DAT` | 292 B | `$0CE1` | not present in any form |
| `PAN.DAT` | 96 B | `$0E05` | **`$11E6`** |

`PAN.DAT` is the one file of the three whose content *is* byte-identical, and it
occurs exactly once in the 131072-byte PRG. The release's copy is **`$3E1` (993)
bytes later**. So the release's bytes at `$0BA7` and `$0CE1` are not scene
descriptors at all — they are 6502 code:

```
$EBA7  25 2C 38 FD AD AE AF AD AD AD ...
$ECE1  A5 39 D0 06 E8 A5 38 D0 01 60 ...   LDA $39 / BNE / INX / LDA $38 / BNE / RTS
```

Overlaying those two ranges would replace this build's descriptors with the
release's code: the byte-match count would rise by 606 and the title screen would
look **exactly the same afterwards**, because the game's init still reads a scene
descriptor from `$ABA7` and would find code. That is a number going up and nothing
being fixed, so it is not done. `asm/patches.manifest` records this so it is not
re-derived wrongly.

### The release's `unrun`, measured

The briefing says 43 bytes longer. Measured: **53**.

| | entry | extent | length | pointer |
|---|---|---|---|---|
| source | `$DC77` | `$DC77-$DCBD` | 71 B | `$13`/`$14` |
| release | `$DB09` | `$DB09-$DB84` | 124 B | `$21`/`$22` |

`$DB08` is `12`, the tail of the preceding routine; the release's routine begins
at `$DB09` with `BIT $06D8 / BMI +3 / STY $2006 / LDY #$00`, and it gates **both**
`$2006` and `$2007` on bit `$06D8` exactly as described. It also adds
`PHP / STA $13 / STX $23 / STY $24 / JSR $F38E / JSR $F946 / … / PLP` around the
non-`$2007` path and moves the loop counters to `$14`/`$15`/`$16`.

It is **not filled either**, and the reason is the same shape: neither address is a
hole.

* this build has a **data table** at `$DB09` (`54 5C 54 40 EC 60 40 1A 18 1D E0 ...`)
  where the release has the routine — 0 of 124 bytes agree;
* this build has `unrun` at `$DC77` where the release has different code — 0 of 71
  bytes agree;
* the release's 124 bytes occur **nowhere** in this PRG (0 of 8 sixteen-byte
  slices);
* **neither image contains a `JSR $DC77` or a `JSR $DB09`**, so there is no call
  site to repoint and no evidence for where the release's displaced table went.

Placing it means re-deriving the layout around it and finding the call site. That
is source work, not a declarative fill.

### The CHR revision, confirmed but not acted on

`TIT0/1/2.CHR` measure as the briefing says — 87% / 78% / 54% of 64-byte slices
present, and the CHR build line still reads `8/32 4 KiB pages identical, 23
differ`. **Zero class-`e` CHR regions were filled**, because the brief's own
condition was "if a correct picture is *still* blocked by CHR **after** Tasks 1–3",
and Task 3 established that the title screen is blocked earlier than CHR: by the
993-byte X7 init-data layout divergence, not by artwork.

---

## What the next attempt needs

1. **The 993 bytes.** X7's slot-7 image diverges progressively through the init
   data block: the release is `$3E1` ahead by our `pan.dat` and `$7DD` ahead by
   slot-7 offset `$1186`. `tools/slotalign.py --slot 7` prints the run map with the
   length of the equal run behind every displacement. Finding what this build does
   not emit is the single highest-value next step; it plausibly unblocks the title
   screen *and* the level load *and* `mapbnk72`.
2. **`mapbnk72`, `$38` vs `$34`, and it moves at frame 3.** The rebuild writes the
   level-bank table one frame after init and the cartridge does not write it again
   until frame 1165 — and writes a different value when it does. `ld10` is
   `MISC.SRC:1104`; `initlev` is `X1.PDS:530-546`. Cheapest open question in the
   project, and the earliest divergence in the entire 1201-frame run.
3. **Why the rebuild never gets into a level.** `lev` stays `0/0/0` while the
   cartridge reaches `0/225/65`. The bank divergence at frame 1166 is downstream of
   this. `tools/nestrace.py` can answer it on the CPU side; it must not be used for
   anything about whether the ROM works.
4. **`unrun`'s call site.** Not a `JSR` to either address in either image.
   `tools/bizhawk/whowrote.py` and `tools/findcode.py` are the tools for it.
5. **Do not reopen:** bounded memory callbacks (Task 1, closed statically and at
   runtime), `b`-vs-`e` for `TIT.DAT`/`PW.DAT` (closed by `PAN.DAT`'s `$3E1`).
