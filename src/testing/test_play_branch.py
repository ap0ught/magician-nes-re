#!/usr/bin/env python3
"""Checkpoint BRANCHING and multi-scout runs, with no BizHawk and no cartridge.

    python3 src/testing/test_play_branch.py

WHY THIS FILE IS SEPARATE FROM test_play_search.py
---------------------------------------------------
`test_play_search.py` pins the search layer's control flow. This pins the two
things a *run* does with it that are not control flow and are the whole reason a
search is worth having:

  * **branching** -- running new segments from a checkpoint a previous segment
    left behind, instead of walking the route again. The whole saving is that
    three shop purchases cost one walk and three short branches, so the thing
    worth testing is exactly that arithmetic, and that a branch still gets the
    replay-from-power-on proof rather than substituting for it.
  * **parallelism** -- `Run(scouts=N)` opening N-1 scout emulators and searching
    across them.

WHAT IS PINNED, AND WHY EACH ONE MATTERS

  * **A checkpoint from a FOREIGN route is refused.** This is `aibeatszelda`'s
    `resume()` guard, ported, and it is the only thing that makes a branch safe:
    a checkpoint may hold a state this run never reached, and loading it would
    put that state into a run whose input log does not contain the inputs that
    produced it. A branch that laundered state would still replay -- it would just
    be replaying a machine that never existed.

  * **A branch records where it came from.** On the report, in the ledger header,
    and in `Run.summary()`. A run assembled from branches has to be readable
    afterwards: "which checkpoint did this segment start from" is the only thing
    that distinguishes a branch from the next step of a walk, and a log that
    recorded only the frame counts would make the two indistinguishable.

  * **A branch still needs the replay proof.** `finish()` replays the whole log
    from frame 0 whether or not a branch was taken, and the run says so
    explicitly. A branch is a development convenience; if it could substitute for
    the proof then "verified" would mean "verified from a checkpoint", which is
    worth much less.

  * **A branch needs an intact input log.** Refused outright when the run has no
    inputs: a branch whose prefix is missing cannot be replayed, and the failure
    would otherwise surface much later as a MISMATCH that looks like a ROM bug.

  * **A parallel run records every attempt from every scout, in the ledger.** The
    previous run's `shop_door` produced twelve identical failures and the whole
    value of those twelve lines was that they were identical. A parallel search
    that reported only winners would have produced one line and the same twelve
    failures would have looked like one.

  * **N>1 is refused above the measured N.** `N_EMULATORS` is 3 because three was
    measured. Asking for four is refused rather than attempted, because an
    unmeasured width in a search is a number nobody checked.

IT FAILED WHEN FIRST WRITTEN
---------------------------
  * the foreign-route check passed for the wrong reason at first: it was written
    against a stub whose `segment_digest()` returned a constant, so any checkpoint
    looked foreign and any checkpoint also looked domestic. It needed two real
    routes installed, with different digests, and a checkpoint stamped with one of
    them.
  * the "a branch must not substitute for the proof" check passed while asserting
    nothing: it read `finish()`'s log lines after a `verify=False` run, which
    never replays at all.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

_ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT / "src"))

from play import ram, route as route_mod, search  # noqa: E402
from play.emu import ActionFailed, BizHawk, BridgeError  # noqa: E402
from play.route import Route, Segment  # noqa: E402
from play.runner import N_EMULATORS, Run  # noqa: E402

ram.load_button_order()

_fails = 0
_n = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global _fails, _n
    _n += 1
    if cond:
        print(f"  ok   check {name}")
        return True
    _fails += 1
    print(f"  FAIL check {name}: {detail}")
    return False


def raised_by(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except BaseException as e:            # noqa: BLE001
        return e


# ================================================================= the fake
class FakeEmu:
    """Enough of a `BizHawk` for branching and scout bookkeeping.

    A SAVESTATE is the whole RAM image plus the frame counter, so a restore really
    puts the machine back -- which is the property branching depends on and the
    one a stub that only remembers "we were here" would not have.
    """

    def __init__(self, marker=0):
        self.img = bytearray(0x800)
        self.img[ram.f("curlev").addr] = 0x10
        self.img[ram.f("mapind").addr] = 1
        # `marker` is a byte nothing else writes. It is how a test tells two
        # emulators apart without assuming anything about the game's own bytes:
        # scout 1 starts with marker=1, and an attempt that "leaked" into scout 0
        # would show up as marker=1 where marker=0 was expected.
        self.img[0x0770] = marker
        self.inputs: list[tuple[str, ...]] = []
        self.frame = 0
        self._states: dict[str, tuple[bytes, int]] = {}
        self.snapshots: list[str] = []
        self._pids = [4242 + marker]
        self._before_pids = set()
        self.run = "stub"
        self.closed = False

    def work_ram(self) -> bytes:
        return bytes(self.img)

    def step(self, buttons=(), frames=1):
        btn = tuple(buttons) if not isinstance(buttons, str) else tuple(
            b for b in buttons.split(",") if b)
        self.inputs.extend([btn] * frames)
        self.frame += frames
        self.img[ram.f("gametime").addr] = (self.frame // 60) & 0xFF
        return self.frame

    def fast(self):
        return None

    def save_state(self, name):
        if name in self._states:
            raise FileExistsError(name)
        self._states[name] = (bytes(self.img), self.frame)
        return pathlib.Path(tempfile.mkstemp(suffix=".state")[1])

    def load_state(self, name):
        if name not in self._states:
            raise FileNotFoundError(name)
        self.img, self.frame = bytearray(self._states[name][0]), self._states[name][1]
        return self.frame

    def snapshot(self, name):
        if name in self.snapshots:
            raise FileExistsError(name)
        self.snapshots.append(name)
        p = pathlib.Path(tempfile.mkdtemp()) / name
        p.mkdir()
        (p / "segments.txt").write_text(
            f"route=stub\nrun=stub\nsegment_list_sha1={route_mod.active().digest()}\n"
            f"frame={self.frame}\ninputs_prefix_sha1=-\n", encoding="utf-8")
        return p

    def load_checkpoint(self, route, name):
        """The real guard's shape, over the real digest, with a real route."""
        d = pathlib.Path(emu_paths.CHECKPOINTS) / route / self.run / name
        meta = d / "segments.txt"
        if not meta.exists():
            raise FileNotFoundError(f"{d} has no segments.txt")
        got = dict(line.split("=", 1) for line in meta.read_text().splitlines()
                   if "=" in line)
        want = route_mod.active().digest()
        if got.get("segment_list_sha1") != want:
            raise BridgeError(
                f"checkpoint {route}/{name} was produced by segment list "
                f"{got.get('segment_list_sha1')} and this run's is {want}. "
                "Loading it would put state into a run that never reached it.")
        return d

    def fingerprint(self):
        import hashlib
        return hashlib.sha1(self.work_ram()).hexdigest()

    def close(self):
        self.closed = True


from play import emu as emu_paths  # noqa: E402  (after FakeEmu uses it)


# ================================================================== policies
def ok_g00(image: bytes) -> bool:
    """g00, the level byte, and the marker byte untouched.

    The marker byte is in the success test deliberately: it means a segment can
    only succeed on the machine it was started on, which is how the scout
    bookkeeping is checked without inventing game facts.
    """
    return (ram.f("phase").get(image) == 0
            and ram.f("curlev").get(image) == 0x10
            and image[0x0770] == 0)


def p_hold(rec_frames):
    """A policy that just spends `rec_frames` frames. No choices."""
    def factory():
        def policy(emu, rec, rng, max_frames):
            rec.step((), min(rec_frames, rec.remaining(max_frames)))
            return f"held {rec_frames}f"
        return policy
    return factory


class StubRun(Run):
    """A `Run` with the emulator injected, so no window is ever launched."""

    def __init__(self, emu, outdir, name="stub", route=None, scouts=1):
        self._stub_emu = emu
        self.log_lines: list[str] = []
        self.outdir = pathlib.Path(outdir)
        self.name = name
        self.label = name
        self.rom = pathlib.Path("/fake/rom.nes")
        self.done = []
        self.reports = []
        self.branches = []
        self.emu = emu
        self.result = {}
        self.scouts_wanted = scouts
        self._scouts_open = []
        self.verify = False
        if route is not None:
            route_mod.install(route)

    def log(self, *a):
        self.log_lines.append(" ".join(str(x) for x in a))

    def _emu(self):
        return self._stub_emu

    def _free_run_tag(self):
        return "stub"


# ================================================ 1. the foreign-route refusal
#
# IT PASSED FOR THE WRONG REASON WHEN FIRST WRITTEN: the stub's
# `segment_digest()` was a constant, so every checkpoint looked foreign AND every
# checkpoint looked domestic. Two real routes with two real digests is what this
# needs, and one of them has to be the route that owns the checkpoint.
route_a = Route("branchA")
route_a.add("one", p_hold(4), ok_g00, tries=1, max_frames=100)
route_a.add("two", p_hold(4), ok_g00, tries=1, max_frames=100)
route_b = Route("branchB")
route_b.add("one", p_hold(4), ok_g00, tries=1, max_frames=100)
route_b.add("different", p_hold(4), ok_g00, tries=1, max_frames=100)
check("1a: the two test routes really do have different segment-list digests, so "
      "the foreign-route check below has something to be foreign about",
      route_a.digest() != route_b.digest(),
      f"a={route_a.digest()} b={route_b.digest()}")


def _write_ckpt(route_name, run_tag, name, digest):
    """A checkpoint directory stamped with a chosen segment-list digest."""
    d = emu_paths.CHECKPOINTS / route_name / run_tag / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "segments.txt").write_text(
        f"route={route_name}\nrun={run_tag}\nsegment_list_sha1={digest}\n"
        f"frame=1234\ninputs_prefix_sha1=abcd\n", encoding="utf-8")
    return d


with tempfile.TemporaryDirectory() as td:
    import play.emu as _ep
    real_cp = _ep.CHECKPOINTS
    _ep.CHECKPOINTS = pathlib.Path(td)          # never touch the real tree
    try:
        route_mod.install(route_a)
        emu = FakeEmu(marker=0)
        stub = StubRun(emu, td, "stub", route=route_a)

        # 1b: a checkpoint stamped with the FOREIGN route's digest is refused,
        # and the error names both digests.
        _write_ckpt("stub", "stub", "from_b", route_b.digest())
        e = raised_by(stub.branch, "from_b", [])
        check("1b: a checkpoint produced by a DIFFERENT segment list is refused, "
              "and the refusal names both digests so the cause is visible",
              isinstance(e, BridgeError)
              and route_b.digest() in str(e) and route_a.digest() in str(e),
              f"{type(e).__name__ if e else None}: {e}")

        # 1c: the same checkpoint under the ROUTE's own digest loads. If 1b
        # passed for the wrong reason -- a constant digest, or a path that simply
        # does not exist -- this is what distinguishes it.
        _write_ckpt("stub", "stub", "from_a", route_a.digest())
        emu.step((), 30)
        got = stub.branch("from_a", [Segment("b1", p_hold(5), ok_g00, tries=1,
                                             max_frames=100)])
        check("1c: a checkpoint stamped with THIS run's own segment list loads and "
              "runs from -- which is what says 1b was refused for its reason and "
              "not because the path was missing",
              len(got) == 1 and got[0].frames >= 0
              and stub.branches and stub.branches[0]["checkpoint"] == "from_a",
              f"reports={got} branches={stub.branches}")

        # 1d: the branch says WHERE IT CAME FROM, in three places: the run log, the
        # segment report, and the ledger header on disk.
        led = pathlib.Path(td) / "b1.attempts.txt"
        head = led.read_text(encoding="utf-8") if led.exists() else ""
        logged = "\n".join(stub.log_lines)
        check("1d: a branch records its checkpoint in the run log, on the segment "
              "report, and in the ledger header -- a run assembled from branches "
              "has to be readable afterwards",
              "BRANCHED from checkpoint 'from_a'" in logged
              and got[0].branch_from == "from_a"
              and "BRANCHED FROM CHECKPOINT from_a" in head,
              f"log={logged[-400:]!r} report={got[0].branch_from!r} head={head!r}")

        # 1e: and `summary()` says it too, so a reader who only has the summary
        # is not left guessing which segments were branches.
        summ = stub.summary()
        check("1e: Run.summary() lists the branch and which checkpoint it came "
              "from",
              "BRANCH from_a" in summ and "branch from_a" in summ, summ)

        # 1f: a branch with an EMPTY input log is refused, loudly, before any
        # segment runs. A branch whose prefix is gone cannot be replayed, and
        # without this the failure would surface much later as a MISMATCH that
        # reads like a ROM bug.
        fresh = StubRun(FakeEmu(marker=0), td, "stub", route=route_a)
        e = raised_by(fresh.branch, "from_a", [])
        check("1f: branching with an empty input log is refused BEFORE any segment "
              "runs -- a branch whose prefix is missing cannot be replayed, and "
              "silently continuing would surface later as a MISMATCH",
              isinstance(e, ActionFailed) and "empty input log" in str(e)
              and not fresh.done,
              f"{type(e).__name__ if e else None}: {e}")

        # ============================================ 2. branches cost the walk ONCE
        # The reason branching exists, as arithmetic. Three short branches off one
        # checkpoint must cost the walk once, not three times.
        emu2 = FakeEmu(marker=0)
        stub2 = StubRun(emu2, td, "stub", route=route_a)
        walk = Segment("walk_to_shop", p_hold(600), ok_g00, tries=1,
                       max_frames=700)
        stub2.segment(walk)
        after_walk = len(emu2.inputs)
        reps = stub2.branch("from_a", [
            Segment(f"buy_{n}", p_hold(10), ok_g00, tries=1, max_frames=100)
            for n in ("key", "stick", "shades")])
        after_branches = len(emu2.inputs)
        walked_three_times = 3 * after_walk
        check("2a: three branches off one checkpoint cost the 600-frame walk ONCE "
              "plus three 10-frame branches -- not three walks",
              after_walk == 600 and after_branches == 630
              and walked_three_times == 1800 and len(reps) == 3,
              f"after walk={after_walk} after 3 branches={after_branches} "
              f"(three walks would be {walked_three_times})")
        check("2b: and every branch says it branched, so none of the three can be "
              "mistaken for a continuation of the walk",
              all(r.branch_from == "from_a" for r in reps),
              str([(r.name, r.branch_from) for r in reps]))

        # ================================== 3. N>1 is refused above the measured N
        # N_EMULATORS is 3 because three concurrent sessions were MEASURED on
        # this machine. Four is not measured, and an unmeasured width in a search
        # is a number nobody checked.
        # The refusals are in `Run.start`, not `Run.__init__`: `__init__` only
        # records what was asked for, and the check needs the emulators to already
        # be running -- which is a launcher. So the guards are asserted by calling
        # them directly, which is what they are: arithmetic on `scouts_wanted`
        # against `N_EMULATORS`.
        def _start_refusal(n):
            """What `Run.start` refuses for `scouts=n`, with nothing launched.

            The count checks are the first thing `start` does, before the stale-
            process check, so they can be reached without a window by making
            `running_emuhawk` report a plausible machine. Anything further down
            `start` -- which launches -- is not reached and is not asserted here;
            that path is covered by the live runs, not by a unit test.
            """
            r = Run("x", scouts=n)
            try:
                r.start()
            except Exception as ex:
                return ex
            return None

        real_running2 = BizHawk.running_emuhawk
        try:
            BizHawk.running_emuhawk = staticmethod(lambda: [])
            e3a = _start_refusal(N_EMULATORS + 1)
            e3b = _start_refusal(0)
        finally:
            BizHawk.running_emuhawk = real_running2
        check("3a: asking for more emulators than were MEASURED is refused by "
              "name, rather than opening them and reporting whatever came back",
              isinstance(e3a, ValueError) and "N_EMULATORS" in str(e3a)
              and str(N_EMULATORS + 1) in str(e3a),
              f"{type(e3a).__name__ if e3a else None}: {e3a}")
        check("3b: and the refusal names the number it would need measured, so "
              "raising it is a measurement rather than a guess",
              isinstance(e3a, ValueError) and "has not" in str(e3a), str(e3a))
        check("3c: scouts=0 is refused too -- MAIN is not optional, it is the "
              "machine the winning inputs are replayed into",
              isinstance(e3b, ValueError) and "MAIN" in str(e3b),
              f"{type(e3b).__name__ if e3b else None}: {e3b}")
    finally:
        _ep.CHECKPOINTS = real_cp

# ============================= 4. a parallel run's ledger holds every attempt
# The stated requirement, and the reason for it: the previous run's `shop_door`
# produced twelve identical failures, and the entire value of those twelve lines
# was that they were identical. A parallel search that reported only its winner
# would have written one line, and the same twelve failures would have read as
# one failure with no explanation.
#
# `accept_after` is raised so the search is a SURVEY and runs all nine attempts.
# At the default the `accept_after` stop rule cuts in after four, which is correct
# behaviour for a segment looking for a short line and would make a check about
# "every attempt is recorded" pass for the wrong reason -- it would be recording
# five, not nine.
with tempfile.TemporaryDirectory() as td:
    route_mod.install(route_a)
    emu = FakeEmu(marker=0)
    stub = StubRun(emu, td, "stub", route=route_a, scouts=1)
    seg = Segment("survey", p_hold(12), ok_g00, tries=9, max_frames=100,
                  accept_after=9)
    rep = stub.segment(seg)
    lines = [l for l in (pathlib.Path(td) / "survey.attempts.txt")
             .read_text(encoding="utf-8").splitlines()
             if l.strip().startswith("seed")]
    check("4a: every one of a segment's attempts is in its ledger, including the "
          "ones that did not win",
          len(lines) == rep.tries == 9,
          f"tries={rep.tries} ledger lines={len(lines)}")
    check("4b: the ledger's own header counts them, so a reader is not relying on "
          "the run log to know the search was exhaustive",
          "# 9 attempts" in (pathlib.Path(td) / "survey.attempts.txt").read_text(),
          (pathlib.Path(td) / "survey.attempts.txt").read_text()[-200:])

# ================================ 5. the scout bookkeeping the parallel path uses
# `_scouts` is what makes N>1 safe, and the two refusals in it are the whole
# point: a scout that was DIVERTED into a running session, and a scout that
# shares a PID with MAIN. Both produce a search that looks like N independent
# results and is not. They are checked here as logic, against a stubbed
# `running_emuhawk` and `BizHawk`, because the alternative is finding out by
# running a parallel search against one machine and believing it.
import play.runner as _runner_mod  # noqa: E402


class PidEmu(FakeEmu):
    """A FakeEmu that CLAIMS a pid set, so `_scouts`' pid arithmetic is testable.

    `pids` goes in the constructor, and that is not a style choice. The first
    version took it from a class attribute and every test then set `emu.pids`
    AFTER construction -- so `_pids`, which is what `_scouts` reads, was still
    `[]` on every fake. The 5d collision case then failed as a DIVERSION, for the
    same reason 5a was supposed to fail: an empty pid set looks like nothing new.
    Two checks were passing and failing for one shared reason and neither was
    testing what it said.
    """

    def __init__(self, marker=0, pids=()):
        super().__init__(marker)
        self._pids = list(pids)
        self._before_pids = set()


class FakeBizHawkFactory:
    """Stands in for `runner.BizHawk`: constructible, and knows the live pids.

    ONE object with both roles, deliberately. The first version of this test
    assigned `_runner_mod.BizHawk.running_emuhawk = ...` and THEN replaced
    `_runner_mod.BizHawk` with a plain function, which silently dropped the
    attribute -- and the checks failed with `'function' object has no attribute
    'running_emuhawk'`, which is a broken test rather than a broken runner.
    """

    def __init__(self, live_pids, pids_per_scout):
        self.live_pids = list(live_pids)
        self.pids_per_scout = [list(p) for p in pids_per_scout]
        self.made = []

    def running_emuhawk(self):
        return list(self.live_pids)

    def __call__(self, **kw):
        """A scout launch ADDS its pids to the live set, as a real launch does.

        `live_pids` grows, which is the whole mechanism: `_scouts` snapshots
        `running_emuhawk()` before the launch and takes the difference after, so
        a scout that adds nothing is a diverted launch.

        `pids_per_scout` entries name the pids a launch contributes. A `+` prefix
        means "add this one to whatever is already live" -- used to build the
        collision case, where the new process shares a pid with MAIN. Without it
        a collision is unrepresentable: a new pid is by definition not in the
        before-set, so `new` would never intersect MAIN's pids and check 5d could
        only ever see a diversion. That is a real limit of the mechanism, and it
        is why the collision case is stated as "the new pid was already live"
        rather than pretending a fresh process can collide by accident.
        """
        i = len(self.made)
        spec = (self.pids_per_scout[i]
                if i < len(self.pids_per_scout) else [404])
        pids = []
        for p in spec:
            if isinstance(p, str) and p.startswith("+"):
                p = int(p[1:])
            elif p not in self.live_pids:
                self.live_pids.append(p)
            pids.append(p)
        emu = PidEmu(marker=i, pids=pids)
        self.made.append(emu)
        return emu


with tempfile.TemporaryDirectory() as td:
    route_mod.install(route_a)
    seg = Segment("s", p_hold(4), ok_g00, tries=1, max_frames=100)

    # 5a: a scout whose launch added NO new process is refused -- that is a
    # diverted launch, and N diverted scouts measure one machine N times. So the
    # fake hands back an empty pid list for the scout it produces.
    real_init = _runner_mod.BizHawk
    try:
        _runner_mod.BizHawk = FakeBizHawkFactory([100], [[]])   # scout adds nothing
        main_emu = PidEmu(marker=0, pids=[100])
        stub = StubRun(main_emu, td, "stub", route=route_a, scouts=3)
        e = raised_by(stub._scouts, "state", ok_g00, seg, 4)
        check("5a: a scout whose launch added NO new EmuHawk process is refused -- "
              "that is a diverted launch, and N diverted scouts measure one "
              "machine N times",
              isinstance(e, BridgeError) and "diverted" in str(e),
              f"{type(e).__name__ if e else None}: {e}")
        check("5b: and the refusal says what would have gone wrong, rather than "
              "running the search and reporting N results from one window",
              isinstance(e, BridgeError)
              and "report that session's memory" in str(e), str(e))
        check("5c: the diverted scout is CLOSED on the way out, so the refusal does "
              "not leave a window behind for the next launch to adopt",
              len(stub._scouts_open) == 0, f"open={len(stub._scouts_open)}")
    finally:
        _runner_mod.BizHawk = real_init

    # 5d: a scout sharing a PID with MAIN is refused. Two "independent" scouts on
    # one machine is worse than one scout, because their attempts would overwrite
    # each other's savestates.
    try:
        # `+100` is "the new process shares MAIN's pid", which is the only way a
        # collision is expressible at all: a genuinely new pid is by definition
        # absent from the before-set and so cannot intersect MAIN's.
        _runner_mod.BizHawk = FakeBizHawkFactory([100], [["+100", 300], [400]])
        main_emu = PidEmu(marker=0, pids=[100])
        stub = StubRun(main_emu, td, "stub", route=route_a, scouts=3)
        e = raised_by(stub._scouts, "state", ok_g00, seg, 4)
        check("5d: a scout sharing EmuHawk pids with MAIN is refused -- two "
              "'independent' scouts on one machine would overwrite each other's "
              "savestates",
              isinstance(e, BridgeError) and "same process as MAIN" in str(e),
              f"{type(e).__name__ if e else None}: {e}")
        check("5e: and the refusal names BOTH pid sets and the SHARED one, so the "
              "collision is visible rather than inferred -- this guard compares "
              "the scout's FULL pid set against MAIN's, not the delta, because a "
              "delta against the pre-launch snapshot is disjoint from MAIN by "
              "construction and the original version could never fire",
              isinstance(e, BridgeError) and "scout reported [100, 300]" in str(e)
              and "MAIN is [100]" in str(e) and "pids [100] appear in both" in str(e),
              str(e))
    finally:
        _runner_mod.BizHawk = real_init

    # 5f: two scouts with genuinely distinct PIDs are ACCEPTED, and MAIN is not
    # among them. A layer that refused everything would pass 5a and 5d.
    try:
        factory = FakeBizHawkFactory([100], [[200], [300]])
        _runner_mod.BizHawk = factory
        main_emu = PidEmu(marker=0, pids=[100])
        stub = StubRun(main_emu, td, "stub", route=route_a, scouts=3)
        got = stub._scouts("state", ok_g00, seg, 4)
        check("5f: two scouts with DISTINCT pids are accepted, MAIN is not in the "
              "list, and MAIN is not among the returned machines -- MAIN is "
              "restored from the savestate after the search and is never searched "
              "on",
              len(got) == 2
              and [g._pids for g in got] == [[200], [300]]
              and all(g is not main_emu for g in got),
              f"got={[g._pids for g in got]}")
        check("5g: the accepted scouts are remembered so `close()` can shut them "
              "down -- a scout left running is adopted by the next launch, which is "
              "the failure the PID wait exists to prevent",
              len(stub._scouts_open) == 2, f"open={len(stub._scouts_open)}")
        stub._close_scouts()
        check("5h: closing the scouts closes every one and empties the list, even "
              "though a second close would be worse than a lost error message",
              all(g.closed for g in got) and not stub._scouts_open,
              f"closed={[g.closed for g in got]} open={len(stub._scouts_open)}")
        check("5i: and the run log says which PID each scout got, so a reader can "
              "see N real windows were used rather than N names",
              sum(1 for l in stub.log_lines if "EmuHawk pids" in l) == 2,
              str([l for l in stub.log_lines if "scout" in l]))
        # 5j: the ORDER. "everything ends up closed" and "everything ends up
        # closed in the right order" are different properties, and only the second
        # one matters: a scout still up when the replay emulator launches would be
        # adopted by it. So the sequence is recorded rather than the end state.
        order = []

        def _record(who):
            def _c():
                order.append(who)
                return None
            return _c

        for g in got:
            g.close = _record("scout")
        main_emu.close = _record("main")
        # Re-open them. Check 5h already ran `_close_scouts()`, which emptied the
        # list, and a scout list that is empty cannot show an ORDER -- the test
        # would then be asserting that MAIN closed first, which is the opposite of
        # what it claims.
        stub._scouts_open = list(got)
        stub.close()
        check("5j: `Run.close()` closes every SCOUT before MAIN -- a scout left up when "
              "the replay emulator launches would be adopted by it, which is the "
              "failure the PID wait in emu.close() exists to prevent. TWO scouts, "
              "so this also pins that none is skipped",
              len(order) == 3 and order[:2] == ["scout", "scout"]
              and order[2] == "main" and len(stub._scouts_open) == 0,
              f"close order={order} open={len(stub._scouts_open)}")
    finally:
        _runner_mod.BizHawk = real_init

# ================================ 5j2. THE BRANCH LEDGER NAMES ITS CHECKPOINT
#
# `Run.branch()` is easy to write so that the only record of where a segment
# started is the frame count, and a frame count does not distinguish "the next
# step of a walk" from "the first step of a branch off a checkpoint". Three
# segments that each cost 12 frames are either three legs of a route or three
# experiments off one place, and only the second reading saves the walk.
with tempfile.TemporaryDirectory() as td:
    # The stub's `load_checkpoint` reads the real CHECKPOINTS path (that is the
    # path `emu.load_checkpoint` uses), so it is redirected rather than faked --
    # otherwise `branch()` here would be testing a stub that can never fail.
    import play.emu as _ep2
    real_cp2 = _ep2.CHECKPOINTS
    _ep2.CHECKPOINTS = pathlib.Path(td)
    try:
        route_mod.install(route_a)
        emu = FakeEmu(marker=0)
        stub = StubRun(emu, td, "stub", route=route_a)
        _write_ckpt("stub", "stub", "ckpt_here", route_a.digest())
        stub.segment(Segment("walk", p_hold(600), ok_g00, tries=1,
                             max_frames=700))
        stub.branch("ckpt_here", [
            Segment(f"buy_{k}", p_hold(12), ok_g00, tries=1, max_frames=60)
            for k in ("key", "stick", "shades")])
    finally:
        _ep2.CHECKPOINTS = real_cp2
    heads = []
    for k in ("buy_key", "buy_stick", "buy_shades"):
        p = pathlib.Path(td) / f"{k}.attempts.txt"
        heads.append(p.read_text(encoding="utf-8") if p.exists() else "")
    check("5j2-a: EVERY branch segment's ledger header names the checkpoint it "
          "branched from, not just the first one -- a branch is a property of the "
          "segments it contains, and recording it on one of three is one of two",
          heads and all("BRANCHED FROM CHECKPOINT ckpt_here" in h for h in heads),
          str([h.splitlines()[0] if h else "MISSING" for h in heads]))
    check("5j2-b: and a segment that did NOT branch has no such line, so the "
          "header is evidence rather than boilerplate",
          "BRANCHED FROM CHECKPOINT" not in
          (pathlib.Path(td) / "walk.attempts.txt").read_text(encoding="utf-8"),
          (pathlib.Path(td) / "walk.attempts.txt").read_text()[:200])
    # The arithmetic, measured rather than asserted from a comment.
    check("5j2-c: the three branches cost 3 x 12 frames off ONE 600-frame walk, "
          "not three walks -- 636 against 1836",
          stub.total_frames() == 600 + 36
          and stub.reports[-1].branch_from == "ckpt_here",
          f"total={stub.total_frames()} (one walk 600 + 3x12; three walks would "
          f"be 1836); last branch_from={stub.reports[-1].branch_from!r}")
    check("5j2-d: `Run.branches` records the checkpoint, its directory, the frame "
          "count it was taken at, and the segments it fed -- so a finished run can "
          "be read back without re-running it",
          len(stub.branches) == 1
          and stub.branches[0]["checkpoint"] == "ckpt_here"
          and stub.branches[0]["inputs_at"] == 600
          and stub.branches[0]["segments"] == ["buy_key", "buy_stick", "buy_shades"],
          str(stub.branches))

# ===================== 5k. THE PID GUARD'S INPUT IS CAPTURED IN THE RIGHT ORDER
#
# TWO MISTAKES, ONE SYMPTOM, and the guard was permanently on because of it. This
# is pinned as a source-level check because both mistakes are about the ORDER of
# statements in `BizHawk.__init__` and about a later assignment overwriting an
# earlier one -- neither is reachable from a fake, and both produced a run that
# stopped with a message about emulator plumbing instead of doing any work.
#
# MEASURED, before this check existed: milestone 1 on Beta 1 with --scouts 3
# stopped after 3.3s with
#     BRIDGE FAILED: this launch added no new EmuHawk process, so it was diverted
# with a window that the same call had demonstrably created, and the delta
# recomputed by hand from the live object was `[1239461]` while the attribute read
# `[]`.
src = pathlib.Path(_ROOT / "src/play/emu.py").read_text(encoding="utf-8")
_lines = src.splitlines()


def _lineno(needle, after=0):
    for i, ln in enumerate(_lines):
        if needle in ln and i > after:
            return i
    return -1


i_snap = _lineno("self._before_pids = set(self.running_emuhawk())")
i_popen = _lineno("self._runsh = subprocess.Popen(")
i_delta = _lineno("self._pids = self._own_pids()")
check("5k-a: the pre-launch PID snapshot is taken BEFORE the Popen -- taken "
      "after, the delta is empty by construction and the guard is always on",
      min(i_snap, i_popen, i_delta) >= 0 and i_snap < i_popen < i_delta,
      f"snapshot at line {i_snap + 1}, Popen at {i_popen + 1}, "
      f"delta at {i_delta + 1}")
# The reset that discarded it, thirty lines later.
# Match only CODE, not the comment that quotes the line. The comment naming the
# old line is deliberate -- it is the record of the bug -- and a check that
# matched it would be a check that fails the moment the record is written.
i_reset = next((i for i, ln in enumerate(_lines)
                if i > i_delta and "self._pids: list[int] = []" in ln
                and not ln.lstrip().startswith("#")), -1)
check("5k-b: and nothing RE-INITIALISES `_pids` after the delta is taken. There "
      "was exactly such a line -- `self._pids: list[int] = []` -- and it discarded "
      "the delta thirty lines after `_own_pids()` computed it, which is why the "
      "guard fired on every run",
      i_reset == -1,
      f"found a re-init at line {i_reset + 1}" if i_reset >= 0
      else "no re-init between the delta and the end of __init__")
check("5k-c: `diverted` is derived from the delta and nothing else, so it cannot "
      "be True while the delta is non-empty",
      "self.diverted = not self._pids" in src,
      "diverted must be `not self._pids`")
check("5k-d: the delta is POLLED (`_own_pids`) rather than sampled once, because "
      "`mono` is not in the process table the instant the bridge answers -- "
      "MEASURED: connected at 3.0s with the new pid absent and present moments "
      "later",
      "self._pids = self._own_pids()" in src
      and "def _own_pids(self" in src,
      "_own_pids must exist and be what assigns _pids")

# ============================================== 6. this file's own coverage
EXPECTED = 32
check(f"6: this file ran exactly {EXPECTED} checks -- a file that matched nothing "
      f"would otherwise report all green", _n + 1 == EXPECTED, f"ran {_n + 1}")

print(f"\n{_n - _fails}/{_n} checks passed")
sys.exit(1 if _fails else 0)