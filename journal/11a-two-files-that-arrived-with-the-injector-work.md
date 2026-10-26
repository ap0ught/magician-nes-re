# 11a — Two files that arrived with the injector work

**Dated 2026-10-05. Branch `fix/boot-from-source`.**

`tools/bizhawk/replay.lua` and `tools/bizhawk/padprobe.lua` were being worked on
when this session started, were uncommitted in the tree, and were swept into
commit `8ecb3de` by a `git add -A tools` that was meant for
`tools/mana_probe.py` and `tools/cartref.py`. Their content is right and the
injector result in `journal/11` depends on both — but that commit's message does
not describe them, and a commit whose subject does not match its diff is the
exact failure `oc-fork-upstream-merge-triage` warns about. So this entry is the
record, rather than a rewrite of history that is already pushed.

## `tools/bizhawk/replay.lua`

The replay driver. Its load-bearing property, and the one `journal/11` rests on,
is that **`joypad.set` returning without an error is not treated as evidence**.
BizHawk accepts the call whether or not anything is connected to the core, so
this file reads three independent things back and compares each against what was
asked for:

1. `joypad.get(1)` — the emulator's own view of the pad for this frame;
2. the cartridge's own decoded joypad bytes at `$002E-$0035` (`jt`) and
   `$0036-$003B` (`dlr`) — the core's view, and the strong one, because that is
   what `jk0` produced;
3. `joypad.setfrommnemonicstr` is deliberately *not* used: it takes one mnemonic
   string with no controller argument, so `joypad.set` is the primitive with a
   defined controller.

The change that was in flight adds a **cross-check that was missing**: a decoder
that decodes nothing reports nothing seen, and nothing seen is indistinguishable
from a broken injector. An earlier version of this file omitted exactly the
decode block — a patch whose search string had the wrong indentation matched
nothing and was not checked — and produced a 5000-frame run whose `game` column
was `$00` on every row while the raw `jt8` column in the same file held a press
on 2624 of those frames. The run now counts
`jt8 non-zero but decoded $00` per frame and the cross-check fails the run on the
first one. On both release runs in `journal/11` that counter is **0**.

The button table it decodes with is the one `tools/fm2.py` derives from the
movie's own file, and `src/testing/test_fm2.py` is what pins that derivation.

## `tools/bizhawk/padprobe.lua`

Answers a question that had two conflicting derivations: **which RAM byte is
which controller button.**

Working it out from the source is a trap. `jk0` does not store one bit per
button. It shifts one accumulator left once per *unpressed* read and stops at the
first press, so the accumulator ends up holding one bit set — bit `k+1`, where
`k` is the index of the first pressed button — plus bit 0, and `joykey` then
unpacks that into eight bytes. The result is not an identity: pressing one
button lights **two** bytes, and Right — the eighth — lights only one, because
the shift has already fallen off the end of the byte.

Two derivations of that table were available and they disagreed, and a table that
is wrong in this way still produces plausible non-zero bytes, so it would have
gone unnoticed. This file settles it by asking the hardware: press exactly one
button, for long enough to be unambiguous, and record which bytes moved. Its own
self-coverage rule is that a button which lights nothing is reported as such
rather than dropped from the table, and the file exits non-zero if any button
could not be resolved — "eight buttons, seven answers" is a failure, not a
result.

**This file has not been run in this session.** It is committed because it was in
the tree and it is the provenance for the `$002E-$0035` table that
`replay.lua` decodes with, and `journal/11`'s 3935/3935 depends on that table
being right — but the *table* came from this file having been run before this
session, and the run's output is not in the repository. Anyone relying on the
table should run it rather than trust the table in `replay.lua`.

```
MAGICIAN_PADPROBE_OUT=/tmp/opencode/padprobe.txt \
  tools/bizhawk/run.sh tools/bizhawk/padprobe.lua "$(python3 tools/cartref.py release)"
```

and then read the table out of it. If that table and `replay.lua`'s `LIVE`/`DEB`
tables disagree, the injector result is wrong and this is where to find out.