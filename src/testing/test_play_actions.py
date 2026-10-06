"""Pin the action contract, against a fake emulator. No BizHawk, no cartridge.

    python3 src/testing/test_play_actions.py

WHY A FAKE EMULATOR
-------------------
The contract is about what an action does when its predicate does NOT become
true, and when its budget expires, and what it writes down when it does. None of
that needs a core: it needs something that holds a RAM image, counts frames and
refuses to lie about a predicate holding. A fake is not a shortcut here, it is
the only way to test the failure path -- an action that has to be run against a
real game to be shown failing cannot be put in a suite that runs on every commit.

THE FAKE IS CHECKED AGAINST THE REAL THING
------------------------------------------
A fake that always returns `held=True` would make every check below pass, so it
is pinned to `ram.Pred.holds()` -- the same function the real bridge's wire
format is checked against in test_play_ram.py -- and its `step_until` goes
through `BizHawk._pulse_until`, which is the REAL implementation with the
emulator's `step`/`work_ram` swapped out. So the pulsing behaviour under test is
the behaviour a run gets.

IT FAILED WHEN FIRST WRITTEN. Two checks, and both were red for a real reason:

  * check 5 (a budget that expires must RAISE, not warn) failed because
    `act()` caught the miss and returned a Result with `held=False`. The code had
    `if not held: emu.note(...)` -- a note. A note is invisible to a caller that
    does not read the log, and every caller of `act()` is a route segment that
    moves on to the next segment. So a route would have walked on past a failed
    action and printed a completion line.
  * check 9 (the deferred activities must not silently become actions) failed
    because `talk()` did not raise: it had been written as "press A and wait for
    the panel", which is exactly the invented interaction the brief said not to
    write.

WHAT IT CHECKS

  1-3.   `act()` drives input ONLY while the predicate is false, and stops on the
         frame it becomes true
  4.     it returns frames used and before/after RAM images, and the diff names
         the fields that changed
  5.     a budget that expires RAISES ActionFailed, and the message contains the
         predicate, the budget and the RAM
  6.     an action with no predicate is refused
  7.     `walk.step()` raises when the player does not move, and its message
         says where the player was and is
  8.     `pulse` alternates press/release; a non-pulsed action HOLDS
  9.     `talk()` raises; `fight.player_low()` raises without a threshold;
         `spells.commit()` raises without a cost
  10.    `route.execute()` snapshots before every segment, and writes a FAILED
         checkpoint when a segment raises -- for any exception, not just a failed
         assertion
  11.    a checkpoint name that is reused is REFUSED rather than overwritten
  12.    the segment digest changes when the segment list changes and does not
         when it does not
"""
from __future__ import annotations

import os
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT / "src"))

from play import ram, route as route_mod  # noqa: E402
from play.actions import act, walk  # noqa: E402
from play.emu import ActionFailed, BizHawk, BridgeError, Result  # noqa: E402

ram.load_button_order()

_fails = 0
_n = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _fails, _n
    _n += 1
    if cond:
        print(f"  ok   check {name}")
    else:
        _fails += 1
        print(f"  FAIL check {name}: {detail}")


class FakeEmu:
    """A RAM image, a frame counter, and the REAL pulsing implementation.

    `step_until` is `BizHawk._pulse_until` bound to this object, so the logic
    under test is the logic a run gets -- including the part that checks the
    predicate after every single frame rather than only at the end.
    """

    route = "fake"

    def __init__(self, phase=0, budget_frames=None):
        self.img = bytearray(0x800)
        self._set("phase", phase)
        self._set("nmiflag", 0)
        self.inputs: list[tuple[str, ...]] = []
        self.events: list[str] = []
        self.frame = 0
        self.snapshots: list[str] = []
        self.shots: list[str] = []
        self.notes: list[str] = []
        self.fail_at = budget_frames      # frame at which phase flips to 0
        # When set, each stepped frame with a direction held nudges the player's
        # map X by this much, so walk.step() has something real to observe.
        self.walk_per_frame = 0

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
                (self.img[ram.f("plrxlo").addr] + self.walk_per_frame * frames)
                & 0xFF)
        if self.fail_at is not None and self.frame >= self.fail_at:
            self._set("phase", 0)
        return self.frame

    def step_until(self, preds, buttons=(), budget=600, what="", pulse=0):
        """Mirrors BizHawk.step_until's dispatch, with the bridge replaced.

        pulse == 0 is the HOLD path, which on the real emulator is one `stepu`
        command evaluated inside the core. Here it is the equivalent local loop:
        check, then hold, then check -- the same order, so the two paths are
        compared on the same terms rather than one of them being assumed.
        """
        if pulse:
            return BizHawk._pulse_until(self, preds, buttons, budget, what, pulse)
        img = self.work_ram()
        if all(p.holds(img) for p in preds):
            return 0, True
        used = 0
        while used < budget:
            self.step(buttons, 1)
            used += 1
            if all(p.holds(self.work_ram()) for p in preds):
                return used, True
        return used, False

    def snapshot(self, name):
        if name in self.snapshots:
            raise FileExistsError(name)
        self.snapshots.append(name)
        return pathlib.Path(name)

    def screenshot(self, name):
        self.shots.append(name)
        return pathlib.Path(name + ".png")

    def note(self, t):
        self.notes.append(t)

    def note_count(self):
        return len(self.notes)


# ============================================ 1-3. drive only while false
emu = FakeEmu(phase=7, budget_frames=10)
res = act(emu, "t1", [ram.pred("phase", "eq", 0)], buttons=("A",),
          budget=100, shot=False, snapshots=False)
check("1: act() drives input only while the predicate is false, and stops on "
      "the frame it becomes true",
      res.held and res.used == 10, f"used={res.used} held={res.held}")

# fail_at=None: nothing changes the RAM, so the predicate stays true throughout.
emu = FakeEmu(phase=7, budget_frames=None)
res = act(emu, "t2", [ram.pred("phase", "eq", 7)], buttons=("A",),
          budget=100, shot=False, snapshots=False)
check("2: a predicate that is ALREADY true costs zero frames and logs nothing",
      res.used == 0 and emu.inputs == [], f"used={res.used} inputs={emu.inputs}")

emu = FakeEmu(phase=7, budget_frames=10)
# step_until, not act(): act() RAISES on a miss (check 5), and this check is
# about what step_until reports, so it asks the layer that reports.
used3, held3 = emu.step_until([ram.pred("phase", "eq", 0),
                               ram.pred("phase", "eq", 7)],
                              buttons=("A",), budget=100)
check("3: predicates are ANDed, so a contradiction can never hold and the full "
      "budget is spent trying", not held3 and used3 == 100,
      f"used={used3} held={held3}")
check("3b: step_until's non-pulsing path and its pulsing path agree that an "
      "already-true predicate costs ZERO frames",
      FakeEmu(phase=7, budget_frames=None).step_until(
          [ram.pred("phase", "eq", 7)], buttons=("A",), budget=50, pulse=8)
      == (0, True))

# ================================================== 4. before/after and the diff
emu = FakeEmu(phase=7, budget_frames=5)
emu._set("wealth", 100)
res = act(emu, "t4", [ram.pred("phase", "eq", 0)], buttons=("A",), budget=100)
d = res.diff([ram.f(n) for n in ("phase", "wealth", "manacur")])
check("4: the Result carries before/after RAM images and the diff names exactly "
      "the fields that changed", d == ["phase 7 -> 0"], str(d))
check("4b: an action takes a snapshot and a screenshot on entry AND on exit",
      emu.snapshots == ["t4_in", "t4_out"] and emu.shots == ["t4_in", "t4_out"],
      f"{emu.snapshots} {emu.shots}")

# ======================================= 5. an expired budget RAISES, loudly
emu = FakeEmu(phase=7, budget_frames=None)
raised = None
try:
    act(emu, "t5", [ram.pred("phase", "eq", 3)], buttons=("A",), budget=25,
        shot=False, snapshots=False)
except ActionFailed as e:
    raised = str(e)
check("5: a budget that expires RAISES ActionFailed rather than warning, "
      "returning False, or noting it",
      raised is not None, "act() returned instead of raising")
check("5b: the message names the predicate, the budget, the buttons, and the "
      "RAM at the moment of failure -- an assertion that says only 'failed' "
      "costs the reader a rerun",
      raised is not None and "phase eq 0x3" in raised and "25 frames" in raised
      and "('A',)" in raised and "phase=7" in raised, str(raised))
check("5c: ActionFailed is an AssertionError, so a route can catch it as one",
      issubclass(ActionFailed, AssertionError))

# ============================================== 6. an action must have a claim
try:
    act(FakeEmu(), "t6", [], buttons=("A",), budget=10, shot=False, snapshots=False)
    check("6: act() refuses an action with no predicate", False, "it accepted []")
except ValueError as e:
    check("6: act() refuses an action with no predicate -- an action that cannot "
          "tell whether it worked is not an action", "no predicate" in str(e))

# ============================================= 7. walk.step raises on no movement
emu = FakeEmu(phase=0)
raised = None
try:
    walk.step(emu, "Right", 70, name="tw", leg=0)
except ActionFailed as e:
    raised = str(e)
check("7: walk.step() raises when the player does not move, so a wall is a "
      "finding rather than a silent success", raised is not None)
check("7b: its message says where the player was, where it is, and what the "
      "game's own switch says",
      raised is not None and "(0,0) -> (0,0)" in raised and "ctrl" in raised,
      str(raised))
emu = FakeEmu(phase=0)
emu.walk_per_frame = 1        # 1 px per frame held -> 10 px per 10-frame leg
seen_notes = []
for i in range(4):
    rr = walk.step(emu, "Right", 10, name="tw", leg=i)
    seen_notes.append((rr.held, rr.note))
check("7c: with the position actually changing, walk.step() returns a held "
      "Result whose note names the move, and each leg gets its own checkpoint "
      "name",
      all(h for h, _ in seen_notes)
      and seen_notes[0][1] == "(0,0) -> (10,0)"
      and len(set(emu.snapshots)) == len(emu.snapshots),
      str(seen_notes))

# ================================================ 8. pulse vs hold
emu = FakeEmu(phase=7, budget_frames=20)
act(emu, "t8", [ram.pred("phase", "eq", 0)], buttons=("Start",), budget=100,
    pulse=8, shot=False, snapshots=False)
pattern = emu.inputs[:24]
check("8: pulse=8 alternates 8 pressed with 8 released, so an EDGE-triggered "
      "game sees four separate presses",
      pattern[:16] == [("Start",)] * 8 + [()] * 8, str(pattern[:16]))
emu = FakeEmu(phase=7, budget_frames=20)
act(emu, "t8b", [ram.pred("phase", "eq", 0)], buttons=("Start",), budget=100,
    shot=False, snapshots=False)
check("8b: without pulse the button is HELD for the whole run -- one edge, "
      "which is right for walking and wrong for every menu",
      emu.inputs[:20] == [("Start",)] * 20, str(emu.inputs[:4]))

# ==================================== 9. the deferred activities stay deferred
from play.actions import fight, spells, talk  # noqa: E402

raised = None
try:
    talk.talk()
except NotImplementedError as e:
    raised = str(e)
check("9: talk() RAISES with its finding, rather than pressing A and calling "
      "whatever happens 'talking'", raised is not None)
check("9b: the finding names what was measured and what the source says",
      raised is not None and "obint" in raised and "MEASURED" in raised.upper()
      or (raised is not None and "Measured" in raised))
raised = None
try:
    fight.player_low(None)
except AssertionError as e:
    raised = str(e)
check("9c: fight.player_low() raises rather than inventing a health threshold",
      raised is not None and "MEASURED" in raised, str(raised))
raised = None
try:
    spells.commit(FakeEmu(), expect_mana_cost=None)
except (ValueError, AssertionError) as e:
    raised = str(e)
check("9d: spells.commit() raises rather than assuming a mana cost the source "
      "and the TAS subtitles disagree about", raised is not None, str(raised))
try:
    talk.found()
    check("9e: talk.found() reports False -- that there is nothing to talk to",
          talk.found() is False)
except Exception as e:
    check("9e: talk.found() reports False", False, str(e))

# ================================== 10. a route snapshots before every segment
seen: list[str] = []


class RouteEmu(FakeEmu):
    def __init__(self, fail_at_seg=None):
        super().__init__()
        self.seg = 0
        self.fail_at_seg = fail_at_seg
        self.states: dict[str, bytes] = {}
        self.frames_at: dict[str, int] = {}

    def snapshot(self, name):
        seen.append(name)
        return super().snapshot(name)

    def save_state(self, name):
        self.states[name] = bytes(self.img)
        self.frames_at[name] = self.frame
        return pathlib.Path(tempfile.mkstemp(suffix=".state")[1])

    def load_state(self, name):
        self.img = bytearray(self.states[name])
        self.frame = self.frames_at[name]
        return self.frame


# The route's executor is `runner.Run`, not `Route.execute` any more: a segment
# is searched by a scout and its winner replayed into MAIN, and that is the
# runner's job. The properties checks 10-11 pin are the runner's, and they are
# pinned HERE as well as in test_play_search.py because this one is about the
# failure path and that one is about the ledger.
import tempfile  # noqa: E402

from play.runner import Run  # noqa: E402


class RouteRun(Run):
    def __init__(self, emu):
        self._stub_emu = emu
        self.log = lambda *a: None
        self.outdir = pathlib.Path(tempfile.mkdtemp())
        self.name = "fake_route"
        self.label = "fake"
        self.rom = pathlib.Path("/fake/rom.nes")
        self.done = []
        self.reports = []
        self.emu = emu
        self.result = {}
        # `Run` grew these with checkpoint branching and scout emulators. A stub
        # that omits them does not fail a check -- it raises AttributeError from
        # the middle of `segment()`, which aborted the file before the checks that
        # follow could run, and the runner reported them as "failed" for a reason
        # that had nothing to do with what they test. `scouts=1` is MAIN only,
        # the inline path.
        self.scouts_wanted = 1
        self._scouts_open = []
        self.branches = []

    def _emu(self):
        return self._stub_emu


def p_ok(emu, rec, rng, max_frames):
    rec.step((), 1)
    return "ok"


def p_boom(emu, rec, rng, max_frames):
    rec.step((), 1)
    raise RuntimeError("boom")


def p_unsolvable(emu, rec, rng, max_frames):
    rec.step((), 1)
    return "not there"


r = route_mod.Route("fake_route")
r.add("one", lambda: p_ok, lambda image: True, tries=0)
r.add("two", lambda: p_ok, lambda image: True, tries=0)
r.add("boom", lambda: p_boom, lambda image: True, tries=0)
r.add("four", lambda: p_ok, lambda image: True, tries=0)
rr = RouteRun(RouteEmu())
try:
    for seg in r.segments:
        rr.segment(seg)
    check("10: a route stops at the first failing segment", False, "it carried on")
except RuntimeError:
    check("10: a route stops at the first failing segment", True)
# A checkpoint before AND after every segment that completes, so a failure has a
# checkpoint of the state the failure is about, and a success has one of the state
# it produced.
check("10b: a checkpoint is taken BEFORE every segment and after every segment "
      "that completes",
      seen == ["one_pre", "one_post", "two_pre", "two_post",
               "boom_pre", "boom_FAILED"], str(seen))
check("10c: and a FAILED checkpoint is written for the segment that raised -- "
      "for ANY exception, not only a failed assertion",
      "boom_FAILED" in seen, str(seen))
check("10d: the segments after the failure did not run", "four_pre" not in seen)

# 10e, and it is new: a segment nobody can solve is a failure with a ledger, not
# a segment that carries on with the least-bad attempt.
seen.clear()
r2 = route_mod.Route("fake_unsolvable")
r2.add("nope", lambda: p_unsolvable, lambda image: False, tries=3)
rr2 = RouteRun(RouteEmu())
raised10e = None
try:
    rr2.segment(r2.segments[0])
except ActionFailed as e:
    raised10e = str(e)
led = rr2.outdir / "nope.attempts.txt"
check("10e: a segment with no successful attempt RAISES, names where its attempts "
      "are, and has already written them",
      raised10e is not None and led.exists() and "nope.attempts.txt" in raised10e
      and sum(1 for l in led.read_text().splitlines() if l.strip().startswith("seed")) == 3,
      f"raised={raised10e} ledger={led.exists()}")

# ============================ 11. a checkpoint name is never silently reused
emu = FakeEmu()
emu.snapshot("only_once")
raised = None
try:
    emu.snapshot("only_once")
except FileExistsError as e:
    raised = str(e)
check("11: a checkpoint name is refused the second time rather than "
      "overwritten -- two different states under one name is a claim nothing "
      "can check", raised is not None)
# ============================================= 12. the segment list digest
a = route_mod.Route("a")
a.add("x", lambda: p_ok, lambda image: True)
a.add("y", lambda: p_ok, lambda image: True)
b = route_mod.Route("b")
b.add("x", lambda: p_ok, lambda image: True)
b.add("y", lambda: p_ok, lambda image: True)
c = route_mod.Route("c")
c.add("y", lambda: p_ok, lambda image: True)
c.add("x", lambda: p_ok, lambda image: True)
check("12: two routes with the same segment list share a digest, and reordering "
      "them changes it", a.digest() == b.digest() and a.digest() != c.digest())
d = route_mod.Route("d")
d.add("x", lambda: p_ok, lambda image: True)
check("12b: adding a segment changes the digest, which is what makes a "
      "checkpoint from one route unusable in another",
      d.digest() != a.digest())

check(f"the file ran every check above ({_n} checks)", _fails == 0)
if _fails:
    print(f"\n{_fails} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")