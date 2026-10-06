"""Savestate-based search: the primitive behind a scout.

Ported from `aibeatszelda/src/zelda/search.py`. What was kept, and why each
part is load-bearing rather than stylistic:

  * **MAIN and SCOUT are different machines.** MAIN plays one master input log
    forward and never loads a state except to resume a checkpoint. A SCOUT
    searches each segment from a *copy* of MAIN's current state. A scout that
    ran on the live machine would be measuring a machine the run has already
    left, and the run's own input log would be full of the scout's flailing.

  * **Attempts are input lists.** A winner is replayed exactly from the same
    state and is then spliced into the master log. Nothing else about an attempt
    is kept, so there is nothing to disagree with when the winner is replayed.

  * **`Attempt` is one record per try, including the failures.** A search that
    found nothing is evidence -- it says which approaches were tried and what
    each of them did. A search that only returns its winner has thrown that
    evidence away, and the losing attempts are exactly the part that tells you
    whether the winner was luck.

  * **`OverBudget` derives from `BaseException`, not `Exception`.** Deliberate,
    and it is the one thing in this file that must not be "cleaned up". A policy
    is a `try: ... except Exception: return "gave up"` wrapper in every project
    that grows one, and an `Exception` would be swallowed by it: the attempt
    would come back as an ordinary failure with the emulator left mid-policy and
    `emu.step` still patched. `BaseException` means no `except Exception` can
    catch it, while every `finally` -- which is what puts the emulator back --
    still runs, because `finally` runs for `BaseException` too.

  * **`Recorder` wraps the emulator** so a policy's steps are captured as an
    input list rather than being fired into the void.

WHAT IS DIFFERENT FROM THE ORIGINAL, AND WHY

  * **No threads.** The original runs one thread per scout because it has one
    emulator per scout. This project has one emulator, because BizHawk diverts a
    second launch into the first through its single-instance pipe
    (`emu.BizHawk.__init__` refuses a stale session for exactly that reason).
    `parallel_search` therefore keeps the *general* form -- a list of scouts, an
    attempt index handed out round-robin -- and threads them only when the list
    actually has more than one entry. On this machine that is the inline path,
    and it is the same code either way.

  * **The ranking is frames and nothing else.** The original prices hearts,
    bombs and bomb-drops because Zelda Link can lose them. Nothing in the first
    town can be lost, so a second score would be a number nobody measured. When
    the first town grows a thing worth ranking, that is where it goes, and it
    needs a measurement first.

  * **The success test is a function of the RAM image** (`success(image)`),
    not of the emulator. Everything these segments assert on is in
    `$0000-$07FF`, which makes the whole search layer testable against a fake
    emulator in `src/testing/`, which is what pins `OverBudget` and the attempt
    ledger.
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field

from .emu import ActionFailed, BizHawk, BridgeError
from . import ram

__all__ = [
    "Attempt", "SearchResult", "OverBudget", "Recorder", "random_search",
    "parallel_search", "PATIENCE", "ACCEPT_AFTER", "SEED_BASE",
]

# Attempts without improvement before a search with a winner gives up on beating
# it. A NAME, not a literal, because it is the knob a segment multiplies.
PATIENCE = int(os.environ.get("MAGICIAN_PATIENCE", "6"))
# Attempts without improvement before a search that has ALREADY found a line
# stops and takes it. This is the "take what we have" that keeps a solved
# segment from eating a whole afternoon: it is a cap on the fruitless tail
# rather than a different stop rule, so a segment whose lines keep improving is
# never cut off -- only the polling after the last improvement is.
ACCEPT_AFTER = int(os.environ.get("MAGICIAN_ACCEPT_AFTER", "4"))
# Attempt i uses `random.Random(SEED_BASE + i)`. A caller retrying a segment that
# just failed passes 2000, 3000, ... instead: same policy, same state, same
# budget, a different sample of the plans. That is the difference between a retry
# and a repeat.
SEED_BASE = 1000


class OverBudget(BaseException):
    """This attempt cannot beat the best any more, so stop it.

    `BaseException` on purpose: no policy's `except Exception` may swallow it,
    while every `finally` -- the one that restores `emu.inputs` -- still runs.
    """


@dataclass
class Attempt:
    """One try, winner or not.

    `note` is why it ended, in the policy's own words where the policy supplied
    them. A failed attempt with no note is a segment that died with nothing but
    "no success", which is what this record exists to prevent.
    """
    seed: int
    inputs: list = field(default_factory=list)
    frames: int = 0
    success: bool = False
    note: str = ""
    where: str = ""          # short description of the state it ended in
    # Which scout emulator ran this attempt. 0 means the INLINE path -- no scout
    # emulators, `random_search` on MAIN -- and scouts are numbered from 1, so
    # that `if self.scout` is the test for "this came from a parallel search" and
    # cannot be fooled by scout 0.
    #
    # Numbering from 1 rather than 0 is not cosmetic. The first version printed
    # `s{scout}` only when scout was truthy, so scout 0 -- which existed and ran
    # attempts -- printed nothing and its rows were shaped exactly like inline
    # ones. MEASURED: on the first live 3-scout Beta 1 run every attempt line in
    # every ledger read `s1` and scout 0's attempts were indistinguishable from
    # MAIN's. The ledger then said "1 scout emulator(s) contributed" when two had.
    scout: int = 0

    def line(self, width: int = 160) -> str:
        """One attempt as one line, for a ledger or a console.

        `width` is 160 rather than the 60 it started at because the note is the
        part that says WHY, and 60 characters cut it exactly where the reason
        lives: `p_pulse`'s note ends "...that is the CUT, not a verdict on the
        approach", which is 40 characters past 60. A ledger that truncates the
        explanation is a ledger that says only what happened.

        `scout` is printed whenever it is not the inline path's 0. A parallel
        search hands attempts round-robin, so the scout id is the only thing in the
        ledger that says WHICH MACHINE produced a line -- and without it a ledger
        from an N-scout run is indistinguishable from an inline one. MEASURED: the
        first live 3-scout run on Beta 1 produced ledgers whose header said
        "1 scout emulator(s) contributed attempts: [1]" while every line was
        identical in shape to a single-scout run's, so nothing on the line itself
        said which machine it came from.
        """
        who = f"s{self.scout} " if self.scout else ""
        return (f"seed {self.seed:<5} {who:<4}{self.frames:>6}f  "
                f"{'OK  ' if self.success else 'no  '}  {self.note[:width]}"
                + (f"  [{self.where}]" if self.where else ""))


@dataclass
class SearchResult:
    """Everything a search did, including what it did not manage."""
    label: str
    best: Attempt | None
    attempts: list[Attempt] = field(default_factory=list)
    why: str = "exhausted"          # converged / capped / searched / budget
    seconds: float = 0.0

    @property
    def tries(self) -> int:
        return len(self.attempts)

    @property
    def wins(self) -> int:
        return sum(1 for a in self.attempts if a.success)

    def losers(self) -> list[Attempt]:
        return [a for a in self.attempts if not a.success]

    def report(self) -> str:
        """Every attempt, winners first is NOT what this does: in order, with notes.

        Deliberately not filtered. A search log that shows only the winner cannot
        answer "did anything else nearly work", and "nearly" is where the next
        improvement is.
        """
        out = [f"SEARCH {self.label}: {self.tries} attempts, {self.wins} success"
               f"{'es' if self.wins != 1 else ''}, stopped {self.why}"
               f" in {self.seconds:.0f}s"]
        for a in self.attempts:
            out.append("  " + a.line())
        if self.best is not None:
            out.append(f"  BEST seed {self.best.seed} {self.best.frames} frames "
                       f"-- {self.best.note}")
        else:
            out.append("  BEST none: no attempt reached the success test")
        return "\n".join(out)


class _BudgetedLog(list):
    """The attempt's input list, which refuses to grow past the cap.

    A `list` subclass rather than a check in `Recorder.step`, because the input
    log is appended to by *three* places -- `step`, `step_until`, and `run_inputs`
    -- and a cap enforced at only one of them is a cap a policy can walk around
    by calling an action instead. `BizHawk.step()` extends the log BEFORE it
    sends the command, so raising from here means the emulator has not stepped
    the over-budget frames at all. `step_until()` extends AFTER, because the
    frames are spent inside the core before Python sees them; an attempt that
    trips the cap that way overshoots by the batch it was inside, which is an
    attempt that was already lost.
    """

    def __init__(self, rec: "Recorder"):
        super().__init__()
        self._rec = rec

    def _check(self, n: int) -> None:
        cap = self._rec.cap
        if cap is not None:
            limit = cap()
            if limit is not None and len(self) + n > limit:
                raise OverBudget(
                    f"attempt would reach {len(self) + n} frames, past the "
                    f"{limit}-frame cut for this attempt")

    def extend(self, iterable) -> None:
        self._check(len(list(iterable)))
        super().extend(iterable)

    def append(self, item) -> None:
        self._check(1)
        super().append(item)


class Recorder:
    """Wraps an emulator so a policy's steps are captured as an input list.

    Used as a context manager: `emu.inputs` is swapped for the attempt's own
    list for the duration and put back in `__exit__`. That is what makes an
    *existing action* usable as a policy with no rewriting -- `act()`,
    `step_until()`, `walk.step()` all record themselves -- and it is why MAIN's
    master log survives a search that ran a hundred actions through it.

    `rec.step()` is the same call with the cap enforced by hand, for policies
    that step directly.
    """
    entry_skip = 8 * 1024 * 1024        # 8M entries; the log is never that long

    def __init__(self, emu: BizHawk, cap=None):
        self.emu = emu
        self._master = emu.inputs          # bound by identity now
        self.cap = cap                    # callable -> frame count this attempt has already lost
        self.inputs = _BudgetedLog(self)

    def __enter__(self) -> "Recorder":
        self.emu.inputs = self.inputs
        return self

    def __exit__(self, *exc) -> bool:
        self.emu.inputs = self._master
        return False                      # never swallow, not even OverBudget

    def step(self, buttons=(), frames: int = 1) -> int:
        if isinstance(buttons, str):
            buttons = tuple(b for b in buttons.split(",") if b)
        if self.cap is not None:
            limit = self.cap()
            if limit is not None and len(self.inputs) + frames > limit:
                raise OverBudget()
        return self.emu.step(buttons, frames)

    @property
    def frames(self) -> int:
        return len(self.inputs)

    def remaining(self, default: int) -> int:
        """How many more frames this attempt may usefully spend.

        A policy that draws a parameter it has not measured -- a lead-in before a
        button press, a hold length -- should be able to see the cut before it
        draws. Without this the measured cost on Beta 1 was four attempts per
        transition that did nothing at all: after a 42-frame best, every attempt
        whose lead-in draw was 60 or 120 raised `OverBudget` before it had
        pressed anything, and the ledger said "over budget at 0 frames" four
        times instead of saying which lead-in it had picked. The information was
        free -- the rng had already decided it -- and it was being thrown away
        because the policy had no way to ask.

        `default` is the SEGMENT's own `max_frames`, not a licence. It was
        returned whole, which is a third way this function could be wrong and the
        only one that made a segment's budget decorative: with no best attempt
        yet `self.cap()` is `None`, so the answer was `default` no matter how much
        had already been spent. A policy that asked before each leg -- which is
        every policy in `ladder.py`, and the only reason a walk can abort -- was
        told it had its full 900-frame budget while it was 1412 frames into a
        1412-pixel walk. MEASURED on Beta 1: `shop_door` burned 1453 frames
        against `max_frames=900`, in twelve identical attempts, all of which
        said "gave up" and none of which could have known it was over. The
        subtraction is done against BOTH limits, because either one alone is the
        wrong answer when the other is tighter.
        """
        spent = len(self.inputs)
        room = max(0, default - spent)
        limit = self.cap() if self.cap is not None else None
        if limit is not None:
            room = min(room, max(0, limit - spent))
        return room


def _where(image: bytes) -> str:
    """Short description of a state, for the attempt ledger.

    Small on purpose: an attempt ledger is read in columns, and the fields here
    are the ones that differ between "reached the level" and "wedged three
    phases later".
    """
    v = ram.decode(image)
    return (f"phase={v['phase']}({ram.PHASE_NAMES.get(v['phase'], '?')}) "
            f"curlev=${v['curlev']:02X} mapind={v['mapind']} "
            f"plr=({ram.plrx(image)},{ram.plry(image)}) "
            f"nmiflag={v['nmiflag']} bnksel=${v['bnksel']:02X}")


def value_of(a: Attempt) -> float:
    """Ranking. Shorter is better, and that is the whole of it.

    Deliberately not a tuple with a tiebreaker that means something in a later
    project: a second criterion nobody measured would be a second criterion that
    silently decides the route.
    """
    return -float(a.frames)


def _one_attempt(emu: BizHawk, state: str, factory, success, *, seed: int,
                 max_frames: int, settle: int, cutoff, log, scout: int,
                 rng_setup=None) -> Attempt:
    """Restore the scout's start state, run one policy, record what happened.

    The restore is a real savestate load from the checkpoint the runner wrote, so
    every attempt starts from the *same* bytes. `state=None` means "the emulator
    is already where the segment starts", which is what a resumed run wants.

    The calling convention is the one this was ported from, and it is unchanged
    on purpose: `factory()` takes nothing and returns
    `policy(emu, rec, rng, max_frames)`. The policy is handed the `Recorder`
    rather than closing over one, so a factory can be written once and used by
    every attempt -- which is the whole reason a factory exists.
    """
    if state is not None:
        emu.load_state(state)
    rec = Recorder(emu)
    note = ""
    over = False
    with rec:
        try:
            if settle:
                rec.step((), settle)
            if rng_setup is not None:
                rng_setup(rec)
            rec.cap = cutoff
            policy = factory()
            note = str(policy(emu, rec, random.Random(seed), max_frames) or "")
        except OverBudget:
            over = True
            # Say how far it got: "over budget" alone is the failure this
            # project has been reduced to explaining, and the frame count is the
            # one number that says whether the cut was close or absurd.
            note = note or f"over budget at {len(rec.inputs)} frames"
        except ActionFailed as e:
            # An action's own budget expired. That is a *failed attempt*, not a
            # dead scout: the action raises on purpose, and a search that let it
            # escape would lose the whole segment to one leg that overshot.
            note = f"{type(e).__name__}: {str(e)[:120]}"
        except (BridgeError, OSError, RuntimeError) as e:
            # The bridge died. Recorded as a note so the ledger shows it, then
            # re-raised: an emulator that is not answering cannot be searched,
            # and the runner has to put a fresh one in its place.
            note = f"BRIDGE DIED: {type(e).__name__}: {str(e)[:100]}"
            raise
    image = emu.work_ram()
    ok = False
    # A BOOLEAN, not `note != "over budget"`. That string comparison was in the
    # first version of this file and it was wrong the moment the note grew a
    # frame count in it: an attempt cut off by the budget was scored as a
    # success, because "over budget at 4 frames" is not "over budget". The
    # check below caught it -- an over-budget attempt that had already reached
    # the segment's success test reported OK.
    if not over:
        try:
            ok = bool(success(image))
        except Exception as e:            # a success test that throws is a failure
            note = f"success test raised: {type(e).__name__}: {str(e)[:80]}"
    if not note:
        note = "success test held" if ok else "success test did not hold"
    a = Attempt(seed, list(rec.inputs), len(rec.inputs), ok, note, _where(image), scout)
    log(f"  attempt {a.line()}")
    return a


def random_search(emu: BizHawk, state, factory, success, *, tries: int = 20,
                  max_frames: int = 900, settle: int = 0, log=print,
                  label: str = "", patience: int = PATIENCE,
                  accept_after: int = ACCEPT_AFTER, seed_base: int = SEED_BASE,
                  first_success: bool = False) -> SearchResult:
    """Up to `tries` attempts from one state; the shortest success wins.

    `first_success=True` stops at the first success rather than looking for a
    shorter line. For a segment whose success test IS the goal -- reaching the
    playing level, opening the inventory -- there is nothing above a success to
    find, and a search for a faster route into the same room is frames spent on
    nothing. For walking it is wrong: the segment has several routes and the
    short one is worth finding.
    """
    t0 = time.time()
    best: Attempt | None = None
    since = 0
    attempts: list[Attempt] = []
    why = "budget exhausted"
    log(f"SEARCH{': ' + label if label else ''}: up to {tries} attempts from "
        f"{state or 'the current state'}")

    def cutoff() -> int | None:
        """The frame count past which this attempt has already lost."""
        return best.frames if best is not None else None

    for i in range(tries):
        seed = seed_base + i
        a = _one_attempt(emu, state, factory, success, seed=seed,
                         max_frames=max_frames, settle=settle, cutoff=cutoff,
                         log=log, scout=0)
        attempts.append(a)
        if a.success and (best is None or value_of(a) > value_of(best)):
            best, since = a, 0
            log(f"    new best: {a.frames} frames")
        else:
            since += 1
        if first_success and best is not None:
            why = "first success"
            break
        if best is None and since >= patience * 2:
            why = "no line found"
            break
        if best is not None and since >= accept_after:
            why = "capped"
            break
    res = SearchResult(label or state or "search", best, attempts, why,
                       time.time() - t0)
    log(f"  {res.tries} attempts, {res.wins} success, stopped {why}: "
        + (f"best {best.frames} frames" if best else "NO SUCCESS"))
    return res


def parallel_search(scouts, states, factory, success, *, tries: int = 20,
                    max_frames: int = 900, settle: int = 0, log=print,
                    label: str = "", patience: int = PATIENCE,
                    accept_after: int = ACCEPT_AFTER, seed_base: int = SEED_BASE,
                    first_success: bool = False, threads: bool = True) -> SearchResult:
    """`random_search` across several scout emulators, attempts handed round-robin.

    Same ranking and same early stop. Threads only when there is more than one
    scout: one thread per emulator is the point, and with one emulator a thread
    is overhead around a socket that already releases the GIL. `states` is either
    one state name shared by every scout or one per scout.

    THE CUTOFF IS FROZEN AT DISPATCH, and this is the whole of what makes a
    parallel search evidence rather than a lottery. `cutoff` is the frame count an
    attempt has already lost -- `OverBudget` fires once an attempt would exceed
    it -- and it was a live closure over the shared `best`. Serial, that only
    changes between attempts, which is correct. Threaded, one scout's 6-frame win
    can tighten the cutoff while ANOTHER scout is 200 frames into a policy that
    was dispatched with a 900-frame budget, and that attempt is then cut off at
    220 and recorded as a failure for a reason that has nothing to do with the
    approach it tried. Which scout happened to finish first decided which other
    scouts were penalised.

    MEASURED, and it is not a subtle difference: with three fake scouts over six
    attempts the parallel search returned seed 1003 at 6 frames where the serial
    search over the identical seeds returned seed 1001 at 4 frames. The same
    policy, the same seeds, the same start state, a different answer, decided by
    thread timing. `src/testing/test_play_search.py` check 9e is that measurement
    and it is why the cutoff is now an integer read once, under the lock, when the
    attempt is handed out.

    What is NOT claimed: that a parallel search finds the same winner as a serial
    one. It need not, and pretending otherwise would be a lie about a stop rule.
    The stop rules count attempts without improvement, so a search that keeps an
    attempt serial would have pruned has run more attempts and can legitimately
    report a different one. The properties that ARE claimed, and that the tests
    check, are that two parallel runs of the same search agree exactly, and that
    every attempt from every scout is in the ledger.
    """
    scouts = list(scouts)
    if not scouts:
        raise ValueError("parallel_search needs at least one scout")
    if not isinstance(states, (list, tuple)):
        states = [states] * len(scouts)

    t0 = time.time()
    best: Attempt | None = None
    since = 0
    attempts: list[Attempt] = []
    why = "budget exhausted"
    import threading
    lock = threading.Lock()
    st = {"next": 0, "stop": False}
    log(f"SEARCH{': ' + label if label else ''}: up to {tries} attempts on "
        f"{len(scouts)} scout(s), numbered 1..{len(scouts)} "
        "(0 is the inline path, i.e. MAIN alone)")

    def work(k: int) -> None:
        nonlocal best, since
        while True:
            # Under the lock, and READ ONCE. Both halves matter: the read has to
            # be atomic with the `st["next"]` bump so two scouts cannot be handed
            # the same index, and it has to be a value rather than a closure so
            # this attempt's cutoff cannot move underneath it while it runs.
            with lock:
                if st["stop"] or st["next"] >= tries:
                    return
                i = st["next"]
                st["next"] += 1
                frozen = best.frames if best is not None else None
            # `scout=k+1`, not `k`: 0 is the inline path's marker, so scout 0
            # would be recorded as "no scout" and its ledger rows would be
            # indistinguishable from MAIN's. See `Attempt.scout`.
            a = _one_attempt(scouts[k], states[k], factory, success,
                             seed=seed_base + i, max_frames=max_frames,
                             settle=settle, cutoff=(lambda v=frozen: v),
                             log=log, scout=k + 1)
            with lock:
                attempts.append(a)
                if a.success and (best is None or value_of(a) > value_of(best)):
                    best, since = a, 0
                    log(f"    new best (scout {k + 1}): {a.frames} frames")
                else:
                    since += 1
                if first_success and best is not None:
                    st["stop"] = True
                    why = "first success"
                    return
                if best is None and since >= patience * 2:
                    st["stop"] = True
                    why = "no line found"
                    return
                if best is not None and since >= accept_after:
                    st["stop"] = True
                    why = "capped"
                    return

    if len(scouts) == 1 or not threads:
        work(0)
    else:
        ts = [threading.Thread(target=work, args=(k,), daemon=True)
              for k in range(len(scouts))]
        for th in ts:
            th.start()
        for th in ts:
            th.join()
    attempts.sort(key=lambda a: a.seed)
    res = SearchResult(label or "search", best, attempts, why, time.time() - t0)
    log(f"  {res.tries} attempts, {res.wins} success, stopped {why}: "
        + (f"best {best.frames} frames" if best else "NO SUCCESS"))
    return res