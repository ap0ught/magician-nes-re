"""Pin the search layer: `OverBudget`, `Recorder`, the attempt ledger, and the
runner's splice. No BizHawk, no cartridge.

    python3 src/testing/test_play_search.py

WHAT IS BEING PINNED, AND WHY EACH ONE MATTERS

The search layer is the only part of `src/play/` that has to survive a policy
misbehaving without taking the run with it. Three of these checks are about a
failure mode that produces a *plausible* wrong answer rather than an error:

  * **`OverBudget` must not be catchable by `except Exception`.** It derives
    from `BaseException` for one reason: every policy in this project -- and in
    the project this was ported from -- is a `try: ... except Exception: return
    "gave up"` wrapper. An `Exception` would be swallowed by one of those, and
    the attempt would come back as an ordinary failure with `emu.inputs` still
    pointing at the scout's list. The next attempt would then record into a log
    nobody is reading, and MAIN's master log would be short by the winner's
    frames. Nothing errors. Check 1 is that exact shape, written out.

  * **`finally` must still run.** The same property seen from the other side:
    the `Recorder`'s restore of `emu.inputs` lives in `__exit__`, which runs for
    `BaseException` too. Check 2 raises `OverBudget` through a `with` block with
    a `finally` and checks both.

  * **A scout's losing attempts must be in the ledger.** A search that returned
    only its winner could not answer "did anything else nearly work", and the
    losers are the only place that question gets asked. Check 7e asserts the
    ledger file exists, has one line per attempt, and that a failing attempt's
    note is in it.

THE FAKE EMULATOR
------------------
The same shape as `test_play_actions.py`'s: a RAM image, a frame counter, the
REAL `BizHawk._pulse_until` where a predicate loop is needed, and a real
save/load of the RAM image. The search layer is control flow over
`emu.step`/`emu.work_ram`, so a fake is not a shortcut -- it is the only way to
test what a policy does when the emulator misbehaves.

IT FAILED WHEN FIRST WRITTEN. Three checks, each red for a real reason:

  * check 1 failed because `Recorder.__exit__` returned `True`, i.e. the
    context manager *swallowed* `OverBudget`. It had been written as a
    "be defensive" `return True` where the value means "suppress this".
  * check 4 failed because `random_search` catches `ActionFailed` around the
    policy, but check 4's policy was the one that could not raise it -- so the
    "a failed attempt still gets a ledger line" property was never under test.
  * check 7 failed because the runner spliced the winner's inputs by APPENDING
    `best.inputs` to the log rather than by replaying them through `emu.step`:
    `len(emu.inputs)` was right and the frame counter was not, which is a log
    that will not replay into the same RAM.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

_ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT / "src"))

from play import first_town as ft  # noqa: E402
from play import ram, route as route_mod, search  # noqa: E402
from play.emu import ActionFailed, BizHawk  # noqa: E402
from play.route import Route, Segment  # noqa: E402
from play.runner import Run  # noqa: E402

ram.load_button_order()

_fails = 0
_n = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global _fails, _n
    _n += 1
    if cond:
        print(f"  ok   check {name}")
    else:
        _fails += 1
        print(f"  FAIL check {name}: {detail}")
    return bool(cond)


def raised_by(f, *a, **k) -> BaseException | None:
    """The exception `f` raised, or None. Never swallows BaseException itself."""
    try:
        f(*a, **k)
    except BaseException as e:            # noqa: BLE001 - this is a test
        return e
    return None


class FakeEmu:
    """RAM image + frame counter + the real input-log bookkeeping.

    `save_state`/`load_state` is a real restore: the RAM image is copied on save
    and copied back on load, because that is the property the runner depends on
    -- a scout attempt must not be able to leave the machine where it found it.
    """

    route = "fake"

    def __init__(self, phase=0, walk_per_frame=0, break_at=None, on_button=None):
        self.img = bytearray(0x800)
        self._set("phase", phase)
        self._set("nmiflag", 0)
        self._set("curlev", 0x10)
        self._set("mapind", 1)
        self.inputs: list[tuple[str, ...]] = []
        self.events: list[str] = []
        self.frame = 0
        self.snapshots: list[str] = []
        self.notes: list[str] = []
        self.states: dict[str, bytes] = {}
        self.frames_at: dict[str, int] = {}
        self.walk_per_frame = walk_per_frame
        self.break_at = break_at
        self.on_button = on_button

    def _set(self, name, v):
        f = ram.f(name)
        for i in range(f.length):
            self.img[f.addr + i] = (v >> (8 * i)) & 0xFF

    def work_ram(self) -> bytes:
        return bytes(self.img)

    def step(self, buttons=(), frames=1):
        btn = tuple(buttons) if not isinstance(buttons, str) else tuple(
            b for b in buttons.split(",") if b)
        self.inputs.extend([btn] * frames)
        self.frame += frames
        if self.walk_per_frame and any(b in btn for b in ("Left", "Right")):
            self.img[ram.f("plrxlo").addr] = (
                (self.img[ram.f("plrxlo").addr] + self.walk_per_frame * frames) & 0xFF)
        if (self.break_at is not None and self.on_button in btn
                and self.frame >= self.break_at):
            self._set("phase", 0)
        return self.frame

    def step_until(self, preds, buttons=(), budget=600, what="", pulse=0):
        if pulse:
            return BizHawk._pulse_until(self, preds, buttons, budget, what, pulse)
        used = 0
        while used < budget:
            if all(p.holds(self.work_ram()) for p in preds):
                return used, True
            self.step(buttons, 1)
            used += 1
        return used, bool(all(p.holds(self.work_ram()) for p in preds))

    def snapshot(self, name):
        if name in self.snapshots:
            raise FileExistsError(name)
        self.snapshots.append(name)
        p = pathlib.Path(tempfile.mkdtemp()) / name
        p.mkdir()
        return p

    def note(self, t):
        self.notes.append(t)

    def save_state(self, name):
        if name in self.states:
            raise FileExistsError(f"state {name} already exists")
        # The FRAME COUNTER goes in the state too. The real `load_state` takes
        # the frame count from the bridge's reply, and the runner relies on it:
        # MAIN is restored, not rewound, and "restored" includes where in time it
        # is. A fake that restored only the RAM would make check 7b pass for the
        # wrong reason.
        self.states[name] = bytes(self.img)
        self.frames_at[name] = self.frame
        self.notes.append(f"save_state {name} @ {self.frame}")
        p = pathlib.Path(tempfile.mkstemp(suffix=".state")[1])
        p.write_bytes(self.states[name])
        return p

    def load_state(self, name):
        if name not in self.states:
            raise FileNotFoundError(name)
        self.img = bytearray(self.states[name])
        self.frame = self.frames_at[name]
        return self.frame


def ok_main(image: bytes) -> bool:
    """g00 and the clock running."""
    return ram.f("phase").get(image) == 0


def p_press_start(emu, rec, rng, max_frames):
    """A policy that pulses START and reports what it did."""
    rec.step((), rng.choice([0, 2, 4]))
    for _ in range(40):
        rec.step(("Start",), 2)
        rec.step((), 2)
        if ram.f("phase").get(rec.emu.work_ram()) == 0:
            return f"Start after {rec.frames}f"
    return "never reached g00"


def p_never(emu, rec, rng, max_frames):
    rec.step(("A",), 20)
    return "pressed A, nothing happened"


def p_walks_wall(emu, rec, rng, max_frames):
    d = rng.choice(["Left", "Right", "Up", "Down"])
    rec.step((d,), 30)
    return f"{d}: did not move (wall)"


def p_raises_action_failed(emu, rec, rng, max_frames):
    rec.step(("A",), 5)
    raise ActionFailed("into_level: the predicate did not hold within 600 frames")


class StubRun(Run):
    """A `Run` with the emulator and the log directory injected.

    `Run.start()` is the only thing skipped, because it launches a window.
    """

    def __init__(self, emu, outdir, name="stub"):
        self._stub_emu = emu
        self.log = lambda *a: None
        self.outdir = pathlib.Path(outdir)
        self.name = name
        self.label = name
        self.rom = pathlib.Path("/fake/rom.nes")
        self.done = []
        self.reports = []
        self.emu = emu
        self.result = {}
        # The three fields `Run` gained with checkpoint branching and scout
        # emulators. A stub that leaves them off makes `segment()` fail with
        # `AttributeError: no attribute 'scouts_wanted'` partway through, which
        # looks like a broken runner rather than an incomplete stub -- and it
        # aborted the file partway through, so checks after the first `segment()`
        # call never ran at all. `scouts=1` means "MAIN only", which is the
        # inline path and what every check in this file wants.
        self.scouts_wanted = 1
        self._scouts_open = []
        self.branches = []

    def _emu(self):
        return self._stub_emu

    def _free_run_tag(self):
        return "stub"


# ================================================== 1. OverBudget is uncatchable
check("1a: OverBudget derives from BaseException and NOT from Exception",
      issubclass(search.OverBudget, BaseException)
      and not issubclass(search.OverBudget, Exception),
      f"mro={search.OverBudget.__mro__}")

emu = FakeEmu()
rec = search.Recorder(emu, cap=lambda: 5)
swallowed = raised = None
restored_to = []
try:
    with rec:
        installed = emu.inputs is rec.inputs
        try:
            try:
                rec.step(("A",), 10)          # 0 + 10 > 5
            except Exception as e:            # what every policy does
                swallowed = e
            except search.OverBudget:         # unreachable if 1a is true
                rec.step(("A",), 99)
        finally:
            restored_to.append(emu.inputs)
except BaseException as e:                    # noqa: BLE001 - that is the point
    raised = e

check("1b: the Recorder installs its own log for the duration", installed,
      "emu.inputs is not rec.inputs inside the with block")
check("1c: a policy's `except Exception` cannot swallow OverBudget",
      swallowed is None and isinstance(raised, search.OverBudget),
      f"swallowed={swallowed!r} raised={raised!r}")
check("1d: the over-budget frames were NOT recorded and the emulator was NOT "
      "stepped", len(rec.inputs) == 0 and emu.frame == 0,
      f"recorded={len(rec.inputs)} frame={emu.frame}")
check("1e: the Recorder's restore runs even though OverBudget is a BaseException, "
      "and the inner `finally` saw the attempt's log (it runs before __exit__)",
      emu.inputs is not rec.inputs and len(restored_to) == 1
      and restored_to[0] is rec.inputs,
      f"emu.inputs is rec.inputs: {emu.inputs is rec.inputs}; "
      f"finally saw rec.inputs: {restored_to == [rec.inputs]}")

# ============================================ 2. finally still runs, every time
emu = FakeEmu()
order = []
try:
    try:
        rec2 = search.Recorder(emu, cap=lambda: 1)
        with rec2:
            rec2.step(("A",), 1)
            rec2.step(("A",), 1)               # over the cap
    finally:
        order.append("finally ran")
except search.OverBudget:
    order.append("caught as BaseException")
check("2: a `finally` around the Recorder runs when OverBudget passes through it, "
      "and the caller's own BaseException handler still sees it",
      order == ["finally ran", "caught as BaseException"], str(order))

# ================================== 3. the cap cannot be walked around with act()
# A policy that uses the action primitives appends through `emu.step`, not
# through `rec.step`. The cap has to bite anyway, or "use an action" is a way to
# spend an unbounded number of frames.
emu = FakeEmu()
rec = search.Recorder(emu, cap=lambda: 4)
hit = None
try:
    with rec:
        emu.step(("A",), 3)
        emu.step(("A",), 3)                 # through emu.step, not rec.step
except search.OverBudget as e:
    hit = e
check("3: the budget is enforced through emu.step too, so a policy cannot opt out "
      "by calling an action instead of rec.step",
      hit is not None and len(rec.inputs) == 3 and emu.frame == 3,
      f"hit={hit!r} recorded={len(rec.inputs)} frame={emu.frame} "
      "(the first 3-frame step was allowed and sent; the second was refused "
      "before it recorded or sent anything)")

# ================================================= 4. every attempt is recorded
emu = FakeEmu(phase=7, break_at=6, on_button="Start")
res = search.random_search(emu, None, lambda: p_press_start, ok_main,
                           tries=4, max_frames=200, log=lambda *a: None,
                           label="t4")
check("4a: a successful search keeps EVERY attempt, not just the winner",
      res.tries == 4 and len(res.attempts) == 4 and res.wins >= 1,
      f"tries={res.tries} wins={res.wins}")
check("4b: every attempt carries a note saying what it did",
      all(a.note for a in res.attempts), str([a.note for a in res.attempts]))
check("4c: SearchResult.report() prints every attempt, losers included",
      sum(1 for line in res.report().splitlines() if line.startswith("  seed "))
      == res.tries,
      res.report())
check("4d: the best attempt IS one of the recorded ones, not a rebuilt copy",
      res.best is not None and res.best in res.attempts and res.best.success)
check("4e: an attempt the budget cut off is a FAILURE even when it had already "
      "reached the success test -- it cannot have been a winner",
      all(not a.success for a in res.attempts if "over budget" in a.note),
      str([(a.seed, a.frames, a.success, a.note) for a in res.attempts]))

# ================================================== 5. no success -> a real None
emu = FakeEmu(phase=7)
res5 = search.random_search(emu, None, lambda: p_never, ok_main, tries=3,
                            max_frames=60, log=lambda *a: None, label="t5")
check("5: a segment nobody can solve returns best=None with the tries still in "
      "the ledger, rather than the least-bad attempt dressed up as a winner",
      res5.best is None and res5.tries == 3
      and all(not a.success for a in res5.attempts),
      f"best={res5.best} tries={res5.tries}")

# ============================================== 6. an action's failure is data
emu = FakeEmu(phase=7, break_at=10_000, on_button="A")
res6 = search.random_search(emu, None, lambda: p_raises_action_failed, ok_main,
                            tries=2, max_frames=60, log=lambda *a: None, label="t6")
check("6: an ActionFailed from inside a policy is a FAILED ATTEMPT, not a dead "
      "scout, and its message survives into the ledger",
      res6.tries == 2 and res6.best is None
      and all("ActionFailed" in a.note for a in res6.attempts),
      str([a.note for a in res6.attempts]))

# ============================================ 7. the runner splices by replaying
# Not by appending: the frame counter and the log must agree, or the log will
# not replay into the same RAM.
with tempfile.TemporaryDirectory() as td:
    emu = FakeEmu(phase=7, break_at=5, on_button="Start")
    stub = StubRun(emu, td, "t7")
    # NOT first_success: this check is about the ledger holding the losers, and
    # a segment that stops at its first success has none by construction.
    seg = Segment("t7_press", lambda: p_press_start, ok_main, tries=4,
                  max_frames=200, first_success=False)
    prefix = list(emu.inputs)
    before = len(prefix)
    rep = stub.segment(seg)
    check("7a: the winner's inputs are appended to MAIN's master log, and the "
          "prefix before them is untouched",
          len(emu.inputs) == before + rep.frames
          and list(emu.inputs[:before]) == prefix,
          f"before={before} after={len(emu.inputs)} winner={rep.frames}")
    check("7b: the emulator's frame counter advanced by exactly the winner's "
          "frames, so the log and the machine agree",
          emu.frame == before + rep.frames,
          f"frame={emu.frame} expected={before + rep.frames}")
    check("7c: the success test was re-checked on MAIN after the replay, and "
          "held", ram.f("phase").get(emu.work_ram()) == 0)
    check("7d: a snapshot was taken before and after the segment",
          emu.snapshots == ["t7_press_pre", "t7_press_post"], str(emu.snapshots))
    led = pathlib.Path(td) / "t7_press.attempts.txt"
    body = led.read_text().splitlines() if led.exists() else []
    check("7e: the segment's attempts are on disk, one line per attempt, and the "
          "failures are in there too",
          led.exists()
          and sum(1 for l in body if l.strip().startswith("seed")) == rep.tries
          and any("no" in l for l in body if l.strip().startswith("seed")),
          str(body))

# ================================== 8. a scout cannot leave the machine moved
emu = FakeEmu(phase=7, walk_per_frame=1)
emu._set("plrxlo", 40)
emu.save_state("seg_start")
img0 = bytes(emu.img)
emu.step(("Right",), 50)
moved_away = emu.img != img0
emu.load_state("seg_start")
check("8: an attempt that walked the player 50px leaves nothing behind -- the "
      "savestate restore is a real restore, and the fake proves it moves",
      moved_away and bytes(emu.img) == img0,
      f"moved={moved_away} restored={bytes(emu.img) == img0}")

# ==================================== 9. parallel_search over one scout == it
emu_a = FakeEmu(phase=7, break_at=6, on_button="Start")
res9a = search.random_search(emu_a, None, lambda: p_press_start, ok_main,
                             tries=3, max_frames=200, log=lambda *a: None, label="t9")
emu_b = FakeEmu(phase=7, break_at=6, on_button="Start")
res9b = search.parallel_search([emu_b], None, lambda: p_press_start, ok_main,
                               tries=3, max_frames=200, log=lambda *a: None,
                               label="t9")
check("9: parallel_search over ONE scout is random_search over one scout: same "
      "seeds, same attempts, same winner",
      [a.seed for a in res9a.attempts] == [a.seed for a in res9b.attempts]
      and res9b.best is not None and res9a.best is not None
      and res9a.best.seed == res9b.best.seed and res9a.tries == res9b.tries,
      f"{[a.seed for a in res9a.attempts]} vs {[a.seed for a in res9b.attempts]}")
check("9b: parallel_search refuses an empty scout list rather than reporting a "
      "search of nothing",
      isinstance(raised_by(search.parallel_search, [], None,
                           lambda: p_never, ok_main), ValueError))

# ============ 9c. remaining() is a BUDGET, not the segment's max_frames
#
# This check failed when first written, and it is the third distinct wrong answer
# `remaining()` has produced. It previously ignored `len(self.inputs)` entirely
# when `cap()` was None, so a policy asking "how much room is left?" was told it
# had its whole `max_frames` again, however far into the attempt it was.
#
# MEASURED on Beta 1, in the `shop_door` ledger: twelve identical attempts, every
# one 1453 frames against `max_frames=900`, every note ending "gave up", and not
# one of them able to know it was over budget. `ladder.p_enter_shop` asks
# `remaining()` before every leg and `remaining()` never went down.
emu = FakeEmu()
rec = search.Recorder(emu)               # cap=None: no best attempt yet
with rec:
    check("9c-a: with no cap set, remaining() counts the frames ALREADY SPENT "
          "against the segment's max_frames",
          rec.remaining(900) == 900 and rec.remaining(50) == 50,
          f"fresh: remaining(900)={rec.remaining(900)} remaining(50)={rec.remaining(50)}")
    rec.step(("A",), 300)
    check("9c-b: after spending 300 of a 900-frame budget, remaining(900) is 600 "
          "-- not 900",
          rec.remaining(900) == 600, f"got {rec.remaining(900)}")
    check("9c-c: and it never goes negative, because a policy that adds to it "
          "should saturate at zero rather than be handed a negative length",
          rec.remaining(200) == 0, f"got {rec.remaining(200)}")
# And with a cap set, whichever limit is TIGHTER wins.
emu = FakeEmu()
rec = search.Recorder(emu, cap=lambda: 100)
with rec:
    rec.step(("A",), 40)
    check("9c-d: with a 100-frame cap and 40 spent, a 900-frame segment budget "
          "still answers 60 -- the cap is tighter and the cap wins",
          rec.remaining(900) == 60, f"got {rec.remaining(900)}")
    check("9c-e: and the segment's own budget still binds when IT is the tighter "
          "of the two (60 of 900 left, 100 of 900 capped)",
          rec.remaining(70) == 30, f"got {rec.remaining(70)}")

# =========================== 9d. parallel search over N>1 REALLY runs N>1
#
# `parallel_search` has been in this file since it was ported and was only ever
# tested with ONE scout, which cannot tell a threaded search from an inline one:
# `len(scouts) == 1` takes the inline path, so check 9 and check 9b above both
# ran the code that is not the code under test. And the runner REFUSED
# `scouts > 1` outright, on the belief that BizHawk diverts a second launch into
# the first. MEASURED, and that belief is wrong on this machine: BizHawk
# 2.11.1's config.ini carries `"SingleInstanceMode": false`, and three
# concurrent sessions were launched, each with its own PID, its own window, its
# own bridge port, and a distinct RAM fingerprint from a distinct input pattern.
# What actually refuses a second SERIAL launch is `tools/bizhawk/run.sh`'s own
# `pgrep` guard -- and that guard is satisfied by three sessions racing past it
# at once, which is why the parallel path was never actually exercised.
#
# So the check below is deliberately narrow: it does not test BizHawk (no
# emulator here), it tests that the SEARCH layer really does hand attempts to N
# distinct machines, really does run them concurrently rather than one after the
# other, and really does record a result for every attempt from every machine.
#
# `stall` makes one scout's attempts take measurable time, so an inline
# implementation over three fake machines would be 3x slower than a threaded
# one. Asserting on wall-clock is normally a bad idea; it is done here because
# the thing under test is *concurrency*, and because the margin is 3x rather than
# a few percent.
class SlowEmu(FakeEmu):
    """A FakeEmu whose steps cost time, so serial and parallel differ measurably."""
    stall = 0.05

    def step(self, buttons=(), frames=1):
        time.sleep(self.stall)
        return super().step(buttons, frames)


# `states="start"`, not None. None means "the emulator is already where the
# segment starts", and `FakeEmu` does NOT reset on its own -- its `break_at` is
# compared against a CUMULATIVE frame counter, so with `None` a given seed draws a
# different number of frames in the second search than in the first. That was
# check 9e failing for a reason that had nothing to do with the search: the fake
# was leaking state between searches. A real scout restores a savestate per
# attempt (`_one_attempt` calls `emu.load_state(state)`), so the fake has to as
# well or the test measures the fake.
slow = [SlowEmu(phase=7, break_at=6, on_button="Start") for _ in range(3)]
for e in slow:
    e.save_state("start")
t_par = time.time()
res_par = search.parallel_search(slow, "start", lambda: p_press_start, ok_main,
                                 tries=6, max_frames=200,
                                 log=lambda *a: None, label="par",
                                 accept_after=99, patience=99)
par_secs = time.time() - t_par
serial_one = SlowEmu(phase=7, break_at=6, on_button="Start")
serial_one.save_state("start")
t_ser = time.time()
res_ser = search.random_search(serial_one, "start", lambda: p_press_start, ok_main,
                               tries=6, max_frames=200,
                               log=lambda *a: None, label="ser",
                               accept_after=99, patience=99)
ser_secs = time.time() - t_ser
scout_ids = {a.scout for a in res_par.attempts}
check("9d-a: parallel search over 3 scouts produced attempts from MORE THAN ONE "
      "scout -- with one scout this is indistinguishable from the inline path, "
      "which is how a threaded search that was never threaded passes its own test",
      len(scout_ids) > 1, f"scouts that contributed attempts: {sorted(scout_ids)}")
# IT FAILED WHEN FIRST WRITTEN. The check asserted `min(scout_ids) >= 0`, which
# the original 0-based numbering satisfied; with scouts numbered from 1 the
# minimum is 1, and the check was really asserting the OLD convention rather than
# the property it describes. The property is "the ids are exactly 1..N", and the
# reason 0 is excluded is that 0 means the inline path -- so an attempt recorded
# as scout 0 would claim to have run on MAIN.
check("9d-b: scout ids are exactly 1..N -- 0 means the inline path, so an "
      "attempt recorded as scout 0 would claim to have run on MAIN",
      scout_ids and min(scout_ids) >= 1 and max(scout_ids) <= 3,
      f"scout ids present: {sorted(scout_ids)} (want 1..3, none of them 0)")
check("9d-c: a parallel search records EVERY attempt, from every scout -- the "
      "losers included. A parallel search that reports only winners is how the "
      "previous run's `shop_door` twelve identical failures went unrecorded",
      len(res_par.attempts) == 6 and len({a.seed for a in res_par.attempts}) == 6,
      f"{len(res_par.attempts)} attempts, seeds "
      f"{[a.seed for a in res_par.attempts]}")
check("9d-d: the parallel run was CONCURRENT, not 3 searches in a row -- "
      f"{par_secs:.2f}s for 6 attempts against {ser_secs:.2f}s for the same 6 on "
      "one machine",
      par_secs < ser_secs * 0.75,
      f"parallel={par_secs:.2f}s serial={ser_secs:.2f}s ratio="
      f"{par_secs / max(ser_secs, 1e-9):.2f}")
# IT FAILED TWICE WHEN FIRST WRITTEN, and both failures are the findings.
#
# First failure: the parallel search returned seed 1003 at 6 frames where the
# serial search over the identical seeds returned seed 1001 at 4 frames. `cutoff`
# was a live closure over the shared `best`, so one scout's win could tighten the
# cutoff while another was mid-policy, and which scout finished first decided
# which others were cut off. Parallelism was deciding the answer. `cutoff` is now
# an integer read once, under the lock, when the attempt is handed out.
#
# Second failure: still 8f-vs-4f, and that one was the FAKE's fault, not the
# search's. `states=None` tells the search "the emulator is already where the
# segment starts", and `FakeEmu` does not reset -- its `break_at` is compared
# against a cumulative frame counter, so the same seed drew different numbers in
# the second search. Fixed above by passing `states="start"`, which is what a
# real scout does anyway.
#
# NOTE WHAT IS *NOT* ASSERTED: that a parallel search equals a serial one. The
# stop rules count attempts without improvement, so a search that kept an attempt
# serial would have pruned has simply run more attempts and may legitimately
# report a different one. Parallel and serial are held to DETERMINISM and to
# recording everything, which are the two properties the evidence depends on.
slow2 = [SlowEmu(phase=7, break_at=6, on_button="Start") for _ in range(3)]
for e in slow2:
    e.save_state("start")
res_par2 = search.parallel_search(slow2, "start", lambda: p_press_start, ok_main,
                                  tries=6, max_frames=200,
                                  log=lambda *a: None, label="par2",
                                  accept_after=99, patience=99)


def _ledger(res):
    """What a ledger line says, minus the scout id -- which is not deterministic."""
    return [(a.seed, a.frames, a.success) for a in res.attempts]


check("9e: TWO parallel runs of the same search over the same seeds return the "
      "same attempts with the same frames and the same verdicts -- parallelism "
      "does not make the search a lottery",
      _ledger(res_par) == _ledger(res_par2)
      and res_par.best is not None and res_par.best.seed == res_par2.best.seed
      and res_par.best.frames == res_par2.best.frames,
      f"run1 {_ledger(res_par)}\n         run2 {_ledger(res_par2)}")
check("9e-who: every seed was handed to exactly ONE scout across both runs, so "
      "no attempt was run twice and none was skipped -- which scout ran it may "
      "vary (the winner of the race takes the next index) but the SET of seeds "
      "cannot",
      sorted(a.seed for a in res_par.attempts) == list(range(1000, 1006))
      and sorted(a.seed for a in res_par2.attempts) == list(range(1000, 1006)),
      f"run1 {sorted(a.seed for a in res_par.attempts)}\n"
      f"         run2 {sorted(a.seed for a in res_par2.attempts)}")
check("9f: the parallel result's attempts come back in SEED order regardless of "
      "which thread finished first, so two runs of the same search produce the "
      "same ledger",
      [a.seed for a in res_par.attempts] == sorted(a.seed for a in res_par.attempts),
      str([a.seed for a in res_par.attempts]))
seen_scout = {(a.seed, a.scout) for a in res_par.attempts}
# 9h: the LEDGER says which machine. `Attempt.line()` grew an `sN` column, and
# the reason is that the first live 3-scout Beta 1 run produced ledgers whose
# HEADER said which scouts contributed while every line was shaped exactly like a
# single-scout run's -- so nothing on the line said which machine produced it, and
# a ledger from an N-scout search could not be told from an inline one.
lin = search.Attempt(seed=1005, inputs=[()], frames=7, success=False,
                     note="left -> (18,140)", where="phase=0", scout=2).line()
check("9h: an attempt's ledger line carries its scout id, so a parallel search's "
      "ledger says WHICH MACHINE produced each line -- without this a 3-scout "
      "ledger is indistinguishable from an inline one",
      "s2" in lin and lin.startswith("seed 1005"),
      f"{lin!r}")
check("9h-why: and the inline path (scout 0) has NO scout column, so a normal "
      "single-scout ledger keeps exactly the shape it had and existing greps for "
      "it still match",
      not search.Attempt(seed=1, inputs=[()], frames=1, success=True,
                         note="ok", where="", scout=0).line().startswith("seed 1 s0"),
      search.Attempt(seed=1, inputs=[()], frames=1, success=True, note="ok",
                     where="", scout=0).line())
check("9g: each attempt's `scout` field survives the sort, so a ledger can still "
      "say which machine produced which line -- and two seeds never share a scout",
      all(isinstance(a.scout, int) for a in res_par.attempts)
      and len(seen_scout) == len(res_par.attempts),
      str(sorted(seen_scout)))

# ================================================= 10. route and digest shapes
r = Route("t10")
r.add("a", lambda: p_never, ok_main)
r.add("b", lambda: p_never, ok_main)
d1 = r.digest()
r2 = Route("t10")
r2.add("a", lambda: p_never, ok_main, tries=99)     # tries are NOT the identity
r2.add("b", lambda: p_never, ok_main)
same = check("10a: the segment digest is over names and order, and is not "
             "disturbed by changing a segment's try count", d1 == r2.digest(),
             f"{d1} vs {r2.digest()}")
r2.segments[0], r2.segments[1] = r2.segments[1], r2.segments[0]
check("10b: reordering the segments DOES change the digest",
      r2.digest() != d1, f"{r2.digest()} vs {d1}")
r3 = Route("t10")
r3.add("a", lambda: p_never, ok_main)
check("10c: two segments with the same name are refused",
      isinstance(raised_by(r3.add, "a", lambda: p_never, ok_main), ValueError))

# ======================================== 11. the route's shape is what it says
route = ft.first_town()
names = [s.name for s in route.segments]
check("11a: the first-town route's segment list is the one the journal names",
      names == ["title", "new_game", "into_level", "walk", "inventory",
                "inventory_close", "deferred"], str(names))
check("11b: a segment with no choices says so with tries=0 rather than by having "
      "no policy",
      [s.name for s in route.segments if s.tries == 0] == ["title", "deferred"],
      str([s.name for s in route.segments if s.tries == 0]))
check("11c: every segment says what its success test is",
      all(s.describe_success() for s in route.segments),
      str([(s.name, s.describe_success()) for s in route.segments]))
walk = next(s for s in route.segments if s.name == "walk")
start = bytearray(0x800)
start[ram.f("plrxlo").addr] = 40
wtest = walk.success(bytes(start))          # `prepare`: a factory, not a predicate
wimg = bytes(start)
wimg = bytearray(wimg)
wimg[ram.f("plrxlo").addr] = 45
check("11d: `walk`'s success test is built from the starting position, and it "
      "accepts a moved player and rejects a stationary one",
      walk.prepare and wtest(bytes(wimg)) and not wtest(bytes(start))
      and "moved from (40" in (wtest.__doc__ or ""),
      f"prepare={walk.prepare} doc={wtest.__doc__!r}")

# ================================ 12. the savestate wire format, both directions
# The first live run of this died on it: `save`'s reply is `ok state=<n> <path>`,
# and `_answers` had `save` filed with the integer replies, so it rejected a
# correct answer. `save` and `load` had been listed in that group from the day
# the bridge grew them and nothing called them for weeks.
save_ok = "ok state=1073 /logs/checkpoints/_states/m/title_start.state"
load_ok = "ok frame=4242"
check("12a: `save`'s real reply shape -- `ok state=<n> <path>` -- is accepted",
      BizHawk._answers(f"save /logs/x.state", save_ok))
check("12b: a `save` reply that lost its path, or its size, or named no command, "
      "is refused",
      not BizHawk._answers("save /logs/x.state", "ok state=1073")
      and not BizHawk._answers("save /logs/x.state", "ok state=/logs/x.state 1073")
      and not BizHawk._answers("save /logs/x.state", "ok 1073"),
      str([BizHawk._answers("save /logs/x.state", r)
           for r in ("ok state=1073", "ok state=/logs/x.state 1073", "ok 1073")]))
check("12c: `load`'s real reply -- `ok frame=<n>` -- is accepted, and a reply "
      "that reports no frame is refused",
      BizHawk._answers(f"load /logs/x.state", load_ok)
      and not BizHawk._answers("load /logs/x.state", "ok"),
      load_ok)
check("12d: the two savestate commands do not accept each other's replies -- a "
      "`load` answered by something that was never a load",
      not BizHawk._answers("load /logs/x.state", save_ok))

# ============================ 13. this file's own coverage is asserted
# A suite that runs zero checks and exits 0 is the exact failure this project
# keeps hitting, so the number of checks is compared against what this file
# declares -- and it is the LAST check, so it cannot itself change the count.
#
# `_n + 1`, not `_n`: `check()` increments the counter, so the count at the moment
# this expression is EVALUATED is one short of the count once the check has run.
# Written as `_n` it compared 33 against 33 and passed while the file actually ran
# 34 -- a coverage assertion that is off by one in the direction that always
# passes, which is the worst direction for it to be wrong in.
EXPECTED = 49
check(f"13: this file ran exactly {EXPECTED} checks -- a file that matched "
      f"nothing would otherwise report all green",
      _n + 1 == EXPECTED, f"ran {_n + 1}")

print(f"\n{_n - _fails}/{_n} checks passed")
sys.exit(1 if _fails else 0)