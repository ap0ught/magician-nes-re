# 15 — The `$E0` wedge is a dangling label, and it is not the same shape as `$20`

*2026-10-06. Appendix to 14. Every number here was measured this session; the
commands are named so each can be re-run rather than believed.*

## The question 14 left open

Journal 14 fixed `stlev` and closed journal 13's wedge. Then, newly reachable:

    [5/7] inventory   600f   0/8   phase=3(g03) curlev=$E0 mapind=1
                                  plr=(18,140) nmiflag=1 bnksel=$07

So `g03` is a *symptom*, not a bug, and `stlev` was one of at least two causes.
14's own note said so. The instruction for this session was to find out whether
`$E0`'s failure has the same mechanism as `$20`'s. **It does not.** `$20` was a
wrong data *value* in a correct layout. `$E0` is a correct-looking value in a
wrong *layout*: five data labels point into a slot another module owns.

## The differential first, because it costs ten seconds and settles the shape

`src/play/differential.py` over the whole 307-frame Beta 1 route, `--every 1`,
both sides sampled every frame:

    f235  phase=3 curlev=$E0 curlevind=63 | phase=3 curlev=$E0 curlevind=63
    f243  phase=4 ...                    | (unchanged)   <- first disagreement

Both sides agree on `curlev=$E0`, on `curlevind=$3F`, on `oldlev=$10`, on
`newphase=8` and on `mapind=1` right up to the frame where Beta 1 leaves `g03`
and our build does not. **So `curlev` and the physical level index are not the
problem.** That killed the hypothesis 14 suggested — "start from `newlev`'s
level-descriptor read for `$E0`" — before any of it was written down.

From `MISC.SRC:1080-1096`, `svi[$3F]` is 1 and `svl[1]` is `ishop`, so `$E0`'s
`idata` runs:

    g03 -> initlev / putlev / scrnon -> idata -> ishop -> isa -> addcmsg

and `isa` calls `addcmsg`, which is the first thing in this game that
**decompresses text**. That is `getmsg` -> `decompchr`, and `decompchr` reads
five tables by absolute address:

    x5.pds:9-14   decompchr  ldy r7 / sty t2 / fbnk 7,#1,n    <- bank slot 1 in
    x5.pds:31     !b        cmp codesl,x
    x2.pds:1118   CODESL    HEX 06010B0908060100...

## What our build actually did

    $ grep -E '^(codesl|codesh|orbyte|mptrl|mptrh)' asm/out/mag.sym
    codesh = $CA0B   codesl = $C9EA   mptrh = $CA5D
    mptrl = $CA4D    orbyte = $CA2C

Every one of them is in `$C000-$DFFF`, which on this cartridge is **fixed to slot
$0E** — X5's own window. `fbnk 7,#1,n` banks *slot 1* into `$A000`, so the only
addresses `decompchr` can reach are `$8000`-`$BFFF`. Beta 1's five are
`$B8AA $B8CB $B8EC $B90D $B91D`. Not one of ours is in that window.

And the bytes at the address the label names are not the table:

    ours  $C9EA -> 4F 54 49 4F 4E 20 B8 20   "OTION " + $B8 + ' '  (message text)
    beta1 $B8AA -> 06 01 0B 09 08 06 01 00   the real code-length table

`codesl` was a label pointing at a message string. `decompchr` searched it for a
3-bit code eight times, found none, fell through to `orbyte,x` — which was the
text `" AXEFIRE..."`. **That is `phase=3(g03) curlev=$E0`.**

Searched for the tables' own bytes across all 131 072 of our PRG:

    CODESL  ours 0 occurrences   beta1 1 at file $038AA (slot 1)
    ORBYTE  ours 0 occurrences   beta1 1 at file $038EC (slot 1)

Two of the three literal tables are **not in our image at all**.

## Why: X2 does not fit in one slot

Instrumenting `Assembler.run_file` (patched in-process, nothing written):

    X2.PDS slot=1 origin=$A000: 10861 bytes emitted, per-slot {1: 8192, 14: 2669}

**X2.PDS assembles to 10 861 bytes. An MMC3 slot is 8 192.** Its base is `$A000`,
so the last 2 669 bytes run past `$BFFF` into `$C000-$DFFF`, and
`Assembler.prg_offset` files that spill in the *fixed* window — slot 14, X5's.
All five table labels are in the spill. X5 is assembled after X2 and overwrites
the spill with its own bytes, which is why:

  * `asm/out/mag.sym` is entirely self-consistent and looks reasonable, and
  * the `$C000` window is only **137 bytes** off the cartridge out of 8 192.

The labels are *dangling*, and nothing in the build asks whether a symbol's
address holds what the symbol names. That is the entire bug, and it is
invisible by construction: the build is consistent, the byte-match percentage is
73.6%, the symbol file is plausible, and the game boots to a title screen and a
walkable town.

## The experiment, and why it is NOT the fix

Removing `MODULE_ORIGINS["X2.PDS"] = 0xA000` (what the *slot search* already
does — it calls `run_file(path, slot=slot)` with no origin) gives:

    PRG: 102712/131072 (78.4%)   was 96414 (73.6%)     +6298 bytes
    codesl = $B8AA  codesh = $B8CB  orbyte = $B8EC  mptrl = $B90D  mptrh = $B91D

**All five land on Beta 1's measured addresses exactly, and both tables appear
once each at Beta 1's offsets.** That is five independent confirmations and it is
not a coincidence.

And `m1_first_town --rebuild` on that ROM: segments 1-4 still pass,
`inventory` still ends at `phase=3(g03) curlev=$E0`. **The dangling tables are a
real bug and they are not this wedge.**

So the change was reverted, because it also moves X2's **code** to `$8000`:

    firespell = $8000    (was $A000)

and `X7.PDS:1445-1459` settles what X's address means:

    farjsr67  sta ma+1 / lda r6 / pha / stx r6 ... stx $8001   <- X -> $8000 window
              lda r7 / pha / sty r7 ... sty $8001              <- Y -> $A000 window

`X4.PDS` reaches `firespell` with `farjsr67(X=$00, Y=$01)`, so slot 0 is at
`$8000` and slot 1 is at `$A000`, so X2's code is called at an `$A000` address.
At `$8000` it would read slot 0, which is X0+X1. Five tables right and the spell
casting entry point wrong, on the strength of a byte-match percentage the build
itself says is at chance for code — that is not a fix, it is a trade with the
worse side hidden. **Reverted; `asm/build.py` is unmodified.**

## What is left, stated as the next question

Beta 1, base `$A000`, has `codesl` at `$B8AA` — an internal offset of `$18AA`
into X2. Our build puts it at `$29EA`. So **our X2 emits 4 416 bytes too much
before its own data tables.** That is the thing to localise, and the instrument
is the `per-slot` byte census above, per module, compared against the cartridge's
slot contents.

The likely shape is that X2 occupies **two** slots — its head in slot 1 at
`$A000` and its tail in one of the two the build says are free (slots 1 and 3).
The build already has the mechanism for this: `MODULE_WINDOW_SLOTS`, which X4
uses for its own `$A000` part. **This was not attempted**, because a guessed
second slot is the same class of unverified move as the origin change, and the
brief's rule is that a branch is a convenience and the replay is the proof.

## What was added

`src/testing/test_cross_module_tables.py`, which asks the question nothing else
asked: *does the address this symbol names hold the bytes this symbol names?*
11 checks of instrument-pinning first (1a-1d, 2a-2d, 3, 4, 5a, 5b), then 6-8 on our own
build. **It is RED**, and it should be: the ROM is wrong.

Red on first write is the point, and this file earned its keep twice before it
found anything real:

  * its own coverage guard stopped the first run with exit 2 because `mptrl`/
    `mptrh` are `DL`/`DH` pointer lists and have no literal bytes;
  * `CODESH` is 30 bytes of which 27 are zero and the rest is `01 01 01`, so
    "found the signature" matched two of X5's own `sql`/`sqh` tables and the
    first run called that a hit. That exclusion is now asserted (check 1c), so
    the file cannot keep skipping a check that has become possible.

## The untracked files

`tools/isolation.sh`, `tools/bizhawk/{display,doctor,setup}.sh`,
`tools/bizhawk/install_units.sh` and `tools/systemd/` sat untracked through two
sessions. All seven files reviewed and **committed**, with
`src/testing/test_shell_tools.py` (21 checks) pinning the review.

`isolation.sh` is sound and its four load-bearing properties are checked
*behaviourally*, against throwaway directories, because three of them are the
ones a naive implementation gets wrong: a content change (sha256 half), a
**rename** (listing half only — checksums call it unchanged), an **mtime-only**
touch (listing half only), and a **missing directory as exit 2, never exit 0**.

Two findings from the review, neither of them in the five new files:

  * `tools/bizhawk/run.sh:126` gates `pkill -f '[m]ono EmuHawk'` behind
    `MAGICIAN_KILL_STALE=1`. A named opt-in; acceptable.
  * **`tools/bizhawk/sweep.sh:25` kills every EmuHawk on the machine
    unconditionally**, before each job, including another project's. Its own
    comment records the burn: *"the next job's pkill took out an EmuHawk that was
    still two frames from finishing."* `display.sh` refuses to do this and says
    so in as many words. **Not fixed this session** — it is pre-existing,
    committed, and out of scope; `test_shell_tools.py` prints it on every run as
    a `NOTE` so it cannot be lost, and check 14 is scoped to the five files this
    session actually reviewed rather than widened to fail on unrelated history.

Also: two orphaned `tools/bizhawk_probe.sh` runs of this project's own were found
hung for 1 h 57 m with Beta 1 (battery) and the rebuild loaded in two EmuHawk
windows — a live battery-save risk. Killed **by PID**, as the rule requires. No
dump's mtime moved (verified before and after).

## What the next attempt needs

1. **Localise X2's 4 416 excess bytes.** Per-module, per-slot emitted-byte census
   against the cartridge's slot contents. This is the one thing standing between
   here and all seven segments, and it is a placement question, not a typo.
2. **`$01FA-$01FC`.** Still no symbol, still the first *named-field-free*
   divergence with Beta 1 — frame 81, alongside three scratch zero-page bytes
   (`$001F-$0021`). Unchanged by anything here. `src/play/ram.py` should record
   the hole as unknown rather than leave it blank.
3. **Fix `sweep.sh`.** One line, and it is the difference between a tool and a
   hazard.
4. **Tasks 2-4 of the brief — the input remap, the shared drawing layer, spell
   slot 27 — are NOT STARTED.** All three remain unblocked and all three remain
   deliberately unstarted: a QoL feature on a ROM that cannot open its inventory
   cannot be verified, and unverified is how this project has misled itself.
