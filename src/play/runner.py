"""Run manager: MAIN plays one master input log forward, SCOUT searches each
segment from a copy of MAIN's current state.

Ported from `aibeatszelda/src/zelda/runner.py`. The contract it keeps:

    runner = Run("m1_first_town", rom=..., label="beta1")
    runner.start()                                  # power-on
    for seg in first_town():                        # ordered segments
        runner.segment(seg)
    runner.finish()                                 # save log, replay from power-on

The artifact is always the input log. `finish()` proves it by replaying it from
power-on in a fresh emulator and comparing a SHA1 of `$0000-$07FF`; a
checkpoint cannot launder anything into the run, because a checkpoint is never
played -- only its input prefix is, and only from frame 0.

SEVERAL EMULATORS, ONE MAIN
---------------------------
`aibeatszelda` gives every scout its own EmuHawk. So does this project, and the
earlier claim in this file that it could not was WRONG -- see `N_EMULATORS` below
for the measurement. What a scout is, either way, is a *savestate on disk*:

    MAIN  --save_state-->  seg.state
    scout: restore seg.state, run an attempt, restore, run the next
    MAIN:  restore seg.state, play the winner's inputs, append to the log

MAIN is restored rather than rewound: the frame counter, the PPU, the banks --
everything in the savestate -- goes back to the bytes that produced the log
prefix, and the prefix itself is never touched. `search.Recorder` is what keeps
the scout's own frames out of the master log while a search runs.

A scout emulator is a full second session with its own window, and MAIN is
restorable underneath all of them, so the two never touch the same machine at the
same time. What a scout must NOT do is reach past its own attempt into MAIN's
state, and the way that is prevented is that every attempt begins with a savestate
load of the bytes the runner wrote before the search started.

BRANCHING FROM A CHECKPOINT
---------------------------
`Run.branch()` takes a named checkpoint -- the end state of a segment that was
already *verified* -- and runs further segments from there instead of re-walking
the route to it. The whole point is that three shop purchases cost one walk and
three short branches rather than three walks, and the thing that makes that safe
is that a branch is a development convenience and never a substitute for the
proof: `finish()` still replays the whole input log from power-on in a fresh
emulator, and a run assembled from branches is verified exactly like a run
assembled by walking.

A checkpoint records the SHA1 of the segment list that produced it
(`emu.BizHawk.snapshot` writes `segments.txt`), and `branch()` refuses a
checkpoint whose digest is not this run's. That is the guard `aibeatszelda`'s
`resume()` has: a checkpoint from a different route must not be able to carry
its state into this one. A branch records which checkpoint it came from, in its
own report line, so a run assembled from branches can be read back.

RESUMING DOES NOT LOAD A SAVESTATE
----------------------------------
To resume, the recorded input prefix is replayed from power-on. That is slower
than a savestate load and it is the point: the one operation a resumable run
must not have is "put the machine somewhere it has not been and carry on".
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import time
from dataclasses import dataclass
from typing import Callable

from . import emu as emu_paths
from . import ram, search
from .emu import ROM as DEFAULT_ROM, ActionFailed, BizHawk, BridgeError
from .route import Route, Segment, active, install

SEARCH_LOGS = pathlib.Path(__file__).resolve().parents[2] / "logs" / "search"

# HOW MANY EMULATORS THIS MACHINE ACTUALLY RUNS, and the measurement behind it.
#
# The number is 3 and it is NOT the number anyone would have guessed. Three
# claims were in tension and all three were measured rather than reasoned about:
#
#  1. `runner.py` said ONE, and said so for months, on the authority of
#     `emu.BizHawk.__init__`'s refusal: "BizHawk allows one instance and would
#     divert this launch into the other".
#  2. `tools/bizhawk/run.sh` had a `pgrep` guard that refuses a second launch --
#     so claim 1 looked confirmed every time it was tested, because every test
#     hit run.sh and never reached BizHawk.
#  3. BizHawk 2.11.1's config.ini on this machine has `"SingleInstanceMode":
#     false`.
#
# MEASURED, three concurrent sessions: three distinct PIDs, three windows, three
# bridge ports, and three distinct SHA1s of $0000-$07FF from three different input
# patterns -- which is the check that matters, because two "independent" sessions
# that were secretly one machine would agree. And the reason claim 1 survived so
# long is now obvious from the guard: three threads racing past a `pgrep` all see
# no EmuHawk running and all proceed. The guard was satisfied by the very
# concurrency it claimed to forbid.
#
# So the divergence is resolved: it is not BizHawk refusing, it is run.sh. The
# guard is kept as the default (a stray window from an interrupted run should not
# be adopted silently) with MAGICIAN_ALLOW_CONCURRENT=1 as the opt-in, and
# `Run.start` asserts that the sessions it opened really are distinct processes.
N_EMULATORS = 3

# What N_EMULATORS is used for, and what it is NOT used for.
#
# It is the width of `parallel_search` for a scouting segment, and it is the
# number of scout emulators a branch may open at once. It is NOT a licence to
# run N milestone runs: MAIN is one emulator and MAIN is the thing that has to be
# restored under all of them, and `scout()` below restores from a savestate so
# that no scout and MAIN are ever live on the same machine at the same time.


@dataclass
class SegmentReport:
    """What one segment cost, and what the scout learned while paying it."""
    name: str
    frames: int
    tries: int
    wins: int
    why: str
    result: search.SearchResult
    # Which checkpoint this segment's state came from, when it was run as a
    # branch rather than as the next step of a walk. `None` for a straight-line
    # run. It is on the report so a run assembled from branches can be READ BACK:
    # "which checkpoint did this come from" is a question the log has to answer,
    # because nothing else in the run's output distinguishes a branch from a
    # continuation.
    branch_from: str = ""


class Run:
    def __init__(self, name: str, *, rom=None, label: str = "", log=print,
                 scouts: int = 1, verify: bool = True):
        self.name = name
        self.rom = pathlib.Path(rom).resolve() if rom else DEFAULT_ROM
        self.label = label or self.rom.stem.replace(" ", "_")
        self.log = log
        self.verify = verify
        self.scouts_wanted = scouts
        self.emu: BizHawk | None = None
        self.done: list[str] = []
        self.reports: list[SegmentReport] = []
        # Scout emulators opened for parallel searches, and the branches taken
        # off checkpoints. Both are run-level state rather than per-segment
        # state: a scout outlives the segment that opened it, and a branch is
        # something a reader of the finished run has to be able to see.
        self._scouts_open: list[BizHawk] = []
        self.branches: list[dict] = []
        self.result: dict = {}            # filled in by finish()
        self.t0 = time.time()
        self.outdir = SEARCH_LOGS / self.label
        self.outdir.mkdir(parents=True, exist_ok=True)

    # -- lifecycle -------------------------------------------------------
    def start(self, route: Route | None = None) -> None:
        stale = BizHawk.running_emuhawk()
        if stale and not os.environ.get("MAGICIAN_ALLOW_CONCURRENT"):
            raise BridgeError(
                f"mono pids {stale} are already running and this run was not "
                "started with MAGICIAN_ALLOW_CONCURRENT=1. BizHawk 2.11.1 on "
                "this machine runs with SingleInstanceMode=false and does hold "
                "several windows (N_EMULATORS in runner.py records the "
                "measurement), so the question is not whether a second window is "
                "possible but whether these are MINE. They are not: they predate "
                "this run. Set the variable only when you mean to run beside "
                "them.")
        if self.scouts_wanted < 1:
            raise ValueError(f"scouts={self.scouts_wanted}; at least MAIN is needed")
        if self.scouts_wanted > N_EMULATORS:
            raise ValueError(
                f"{self.scouts_wanted} emulators were asked for. N_EMULATORS is "
                f"{N_EMULATORS}: three concurrent sessions were MEASURED on this "
                "machine (distinct PIDs, distinct $0000-$07FF fingerprints), and "
                "four has not. Raise N_EMULATORS only after measuring it, because "
                "the number is what a parallel search's width rests on.")
        # The route is installed BEFORE the emulator exists, because every
        # snapshot records the route's segment digest and `snapshot()` is called
        # before the first segment. Installed afterwards, every checkpoint in the
        # run reads `segment_list_sha1=-`, the guard that refuses a foreign
        # checkpoint is inert for the whole run, and nothing says so.
        if route is not None:
            install(route)
        before = set(BizHawk.running_emuhawk())
        self.emu = BizHawk(rom=self.rom, log_name=f"{self.name}_{self.label}",
                           route=self.name, run=self._free_run_tag(),
                           concurrent=bool(stale))
        self.emu.fast()
        # PROVE the session is a new process rather than assuming it. `emu._pids`
        # is every EmuHawk alive when this session finished starting, which
        # includes anything that was already running -- so the delta is the only
        # part that is ours. A diverted launch would add nothing to the delta and
        # would then be measuring the OLD session, which is the failure this
        # whole arrangement exists to prevent.
        new = sorted(set(self.emu._pids) - before)
        if not new:
            self.close()
            raise BridgeError(
                "this launch added no new EmuHawk process, so it was diverted "
                "into one that was already running and everything it reports "
                "would be the other session's. Set MAGICIAN_ALLOW_CONCURRENT=1 "
                "if a second window is intended, and check that run.sh's pgrep "
                "guard is not killing the launch instead.")
        self.log(f"run {self.name} [{self.label}] on {self.rom}")
        self.log(f"  MAIN is EmuHawk pids {new} (of {self.emu._pids} alive); "
                 f"stale before this run: {sorted(before) or 'none'}")
        self.log(f"  checkpoint namespace logs/checkpoints/{self.name}/"
                 f"{self.emu.run}")
        self.log(f"  ram map digest {ram.map_digest()} "
                 f"({len(ram.all_fields())} fields)")
        self.log(f"  segment list {self.digest}")
        if self.digest == "-":
            raise ValueError(
                "no route is installed, so every snapshot this run writes would "
                "record `segment_list_sha1=-` and the checkpoint guard would be "
                "inert. Pass the route to Run.start(route) or Run.run(route).")

    def _free_run_tag(self) -> str:
        """A checkpoint namespace this run has not used before.

        `BizHawk.snapshot()` refuses to write a name that exists, on purpose --
        two different states under one name is a claim nothing can check. A
        searched route wants to be run again the moment something changes, and
        deleting yesterday's checkpoints by hand to get there is exactly how
        evidence gets lost. So the namespace gets a number and the refusal keeps
        its meaning: within one namespace, a name is written once.
        """
        base = re.sub(r"[^A-Za-z0-9_.-]", "_", self.label)
        n = 1
        while (emu_paths.CHECKPOINTS / self.name / f"{base}_{n}").exists() or \
                (emu_paths.CHECKPOINTS / "_states" / self.name / f"{base}_{n}").exists():
            n += 1
        if n > 1:
            self.log(f"  checkpoints for [{base}] already exist from an earlier "
                     f"run; this one writes to {base}_{n} rather than overwriting "
                     f"them")
        return f"{base}_{n}"

    def close(self) -> None:
        # Scouts FIRST, and always. A scout is a full EmuHawk window, and the
        # next `BizHawk()` this process makes would be measured against whatever
        # is still up. The scout list is emptied even if a close raises, because
        # a second close on a dead emulator is worse than a lost error message.
        self._close_scouts()
        if self.emu is not None:
            try:
                self.emu.close()
            finally:
                self.emu = None

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def digest(self) -> str:
        return self.emu.segment_digest() if self.emu is not None else "-"

    # -- one segment -----------------------------------------------------
    def segment(self, seg: Segment, *, tries: int | None = None,
                branch_from: str = "") -> SegmentReport:
        """Search this segment from a copy of MAIN's state, then play the winner.

        The order is fixed and each step is load-bearing:

        1. MAIN snapshots itself and writes a savestate. The snapshot is the
           human-readable checkpoint (nine domains, PNG, registers, the route's
           digest); the savestate is what a scout restores. They are different
           jobs and neither substitutes for the other.
        2. Every attempt restores the savestate, so all of them start from the
           same bytes.
        3. The winner's inputs are replayed into MAIN *through the ordinary
           `emu.step`*, which appends them to the master log. MAIN is not told
           what happened; it is played the same inputs a scout found.
        4. The success test is re-checked on MAIN. A segment whose winner does
           not hold on the machine that has to carry it is a driver bug, and
           saying so is more useful than accepting the search's word.
        """
        emu = self._emu()
        try:
            return self._segment(seg, tries, branch_from=branch_from)
        except BaseException:
            # A FAILED checkpoint for ANY exception, not only a failed
            # assertion. A policy that raises something the search does not
            # recognise -- a FileExistsError from a colliding checkpoint name, a
            # KeyError from a policy reading a field it invented, a bridge that
            # went mid-attempt -- used to escape with no checkpoint at all, and a
            # run that stops with nothing written down is a run whose last state
            # has to be guessed at.
            #
            # Best-effort by design: the checkpoint may be the thing that failed.
            try:
                emu.snapshot(f"{seg.name}_FAILED")
            except Exception as se:       # noqa: BLE001
                self.log(f"    (could not write the FAILED checkpoint either: "
                         f"{type(se).__name__}: {se})")
            raise

    def _segment(self, seg: Segment, tries: int | None,
                 *, branch_from: str = "") -> SegmentReport:
        emu = self._emu()
        tries = tries if tries is not None else seg.tries
        st = f"{seg.name}_start"
        before = emu.work_ram()
        # `prepare` segments: the success test is built from where MAIN actually
        # is right now, not from a global a previous run may have left behind.
        test: Callable[[bytes], bool] = (
            seg.success(before) if seg.prepare else seg.success)
        snap = emu.snapshot(f"{seg.name}_pre")
        state = emu.save_state(st)
        self.log("")
        self.log(f"--- {seg.name} ---")
        self.log(f"  from frame {emu.frame}: {search._where(before)}")
        if branch_from:
            # Said here and on the report, and on the ledger header: a run built
            # from branches has to be readable afterwards, and "which checkpoint
            # did this segment start from" is the one thing that distinguishes a
            # branch from the next step of a walk. A log that recorded only the
            # frames would make the two indistinguishable.
            self.log(f"  BRANCHED from checkpoint {branch_from!r} "
                     f"(frame {emu.frame})")
        self.log(f"  checkpoint -> {snap}")
        self.log(f"  scout state -> {state} ({state.stat().st_size} bytes)")
        if seg.why:
            self.log(f"  {seg.why}")
        self.log(f"  success test: {test.__doc__ or seg.describe_success()}")
        scouts = self._scouts(st, test, seg, tries)
        if scouts:
            # The parallel path, and only ever this one: MAIN is restored to
            # `st` before the winner is replayed into it below, so no scout is
            # ever live on MAIN's machine.
            res = search.parallel_search(
                scouts, st, seg.factory, test, tries=tries,
                max_frames=seg.max_frames, settle=seg.settle, log=self.log,
                label=f"{seg.name} on {len(scouts)} scouts",
                first_success=seg.first_success,
                accept_after=seg.accept_after or search.ACCEPT_AFTER)
            self._ledger(seg, res, branch_from)
        elif tries:
            res = search.random_search(
                emu, st, seg.factory, test, tries=tries,
                max_frames=seg.max_frames, settle=seg.settle, log=self.log,
                label=seg.name, first_success=seg.first_success,
                accept_after=seg.accept_after or search.ACCEPT_AFTER)
            self._ledger(seg, res, branch_from)
        else:
            # `tries=0` means "this segment has no choices", and running the
            # policy once with no cut is the honest way to say that: the inputs
            # still come from a Recorder, so they still splice into the log the
            # same way a searched line does.
            res = search.random_search(emu, st, seg.factory, test,
                                       tries=1, max_frames=seg.max_frames,
                                       settle=seg.settle, log=self.log,
                                       label=seg.name, first_success=True,
                                       accept_after=seg.accept_after
                                       or search.ACCEPT_AFTER)
            self._ledger(seg, res, branch_from)
        if res.best is None:
            self._write_ledger(seg, res)
            raise ActionFailed(
                f"segment {seg.name}: no attempt in {res.tries} reached the "
                f"success test. What was tried is in "
                f"{self.outdir / (seg.name + '.attempts.txt')}. "
                f"It last ended at {search._where(emu.work_ram())}")

        emu.load_state(st)
        for b in res.best.inputs:
            emu.step(b, 1)
        after = emu.work_ram()
        if not test(after):
            self._write_ledger(seg, res)
            raise ActionFailed(
                f"segment {seg.name}: the winner's {res.best.frames} inputs were "
                f"replayed into MAIN and the success test did NOT hold there. "
                f"The scout and MAIN disagree about the same state, which is a "
                f"driver bug rather than a build problem. "
                f"MAIN is at {search._where(after)}; the scout's attempt was at "
                f"{res.best.where}. A {seg.name}_FAILED checkpoint holds the "
                f"state MAIN was in.")
        self.done.append(seg.name)
        rep = SegmentReport(seg.name, res.best.frames, res.tries, res.wins,
                            res.why, res, branch_from)
        self.reports.append(rep)
        self.log(f"[{seg.name}] {res.best.frames} frames "
                 f"(of {len(emu.inputs)} total), {res.wins}/{res.tries} attempts "
                 f"succeeded, stopped {res.why}")
        self.log(f"  now at {search._where(after)}")
        emu.snapshot(f"{seg.name}_post")
        self._write_ledger(seg, res)
        return rep

    # -- scout emulators -------------------------------------------------
    def _scouts(self, state: str, test, seg: Segment, tries: int) -> list:
        """`scouts_wanted - 1` extra emulators, or `[]` for the inline path.

        MAIN is not in this list. It is the machine that has to carry the winning
        inputs into the master log, and it is restored from `state` after the
        search; putting MAIN in the scout list would mean searching on the
        machine whose frame counter and input log are being accumulated.

        Each scout is a full second EmuHawk session with its own window, its own
        bridge port and its own copy of the cartridge's RAM, and each restores
        `state` per attempt -- the same savestate MAIN would restore -- so every
        attempt on every scout starts from identical bytes. That identical
        capture is what makes parallelism safe rather than merely fast; it is
        asserted by `self._scout_pids_are_distinct()` before any attempt runs.
        """
        main = self._emu()
        want = max(0, min(self.scouts_wanted, N_EMULATORS) - 1)
        if not want:
            return []
        out = []
        for k in range(want):
            before = set(BizHawk.running_emuhawk())
            e = BizHawk(rom=self.rom, log_name=f"{self.name}_{self.label}_scout{k}",
                        route=self.name, run=f"{main.run}_scout{k}",
                        concurrent=True)
            e.fast()
            new = sorted(set(e._pids) - before)
            if not new:
                e.close()
                raise BridgeError(
                    f"scout {k} of {seg.name}: launching it added no new EmuHawk "
                    "process, so it was diverted into a running session and would "
                    "report that session's memory. A parallel search over "
                    "diverted scouts measures one machine N times and looks like "
                    "N independent results.")
            # The FULL pid set, not `new`. This check was originally written
            # against `new` and could therefore never fire: `new` is a set
            # difference against the pre-launch snapshot, so it is disjoint from
            # everything that was already running -- and MAIN's pids were
            # necessarily already running. A guard that cannot fire is worse than
            # no guard, because it reads as a check.
            #
            # It matters because a scout that shares a process with MAIN is not a
            # slower scout, it is a second name for the same machine: both would
            # restore the same savestate and their attempts would overwrite each
            # other's frames.
            shared = sorted(set(e._pids) & set(main._pids))
            if shared:
                e.close()
                raise BridgeError(
                    f"scout {k} of {seg.name}: it is the same process as MAIN -- "
                    f"pids {shared} appear in both (scout reported {e._pids}, MAIN "
                    f"is {main._pids}). Two 'independent' scouts on one machine is "
                    "worse than one scout, because their attempts would overwrite "
                    "each other's savestates.")
            self.log(f"  scout {k} -> EmuHawk pids {new} (MAIN is "
                     f"{main._pids})")
            out.append(e)
        self._scouts_open.extend(out)
        return out

    def _close_scouts(self) -> None:
        for e in self._scouts_open:
            try:
                e.close()
            except Exception as se:       # noqa: BLE001
                self.log(f"  (scout {getattr(e, 'log_path', '?')} did not close: "
                         f"{type(se).__name__}: {se})")
        self._scouts_open.clear()

    # -- branching -------------------------------------------------------
    def branch(self, checkpoint: str, segments: list, *, tries: int | None = None
               ) -> list[SegmentReport]:
        """Run `segments` from a checkpoint's end state, not from a walk to it.

        The checkpoint must be one THIS RUN wrote, and its recorded segment-list
        digest must be this run's. That guard is the whole reason a branch is
        safe: a checkpoint produced by a different route may hold a state this
        run never reached, and loading it would put that state into a run whose
        input log does not contain the inputs that produced it. `aibeatszelda`'s
        `resume()` has the same check and this is a faithful port of it.

        `segments` is a list of `Segment`, not `Route.segments`: a branch is
        usually a handful of things tried from one place, and requiring a whole
        route would make the common case -- "three purchases from the shop door"
        -- a route in its own right.

        What a branch is NOT: a substitute for the proof. `finish()` still
        replays the entire input log from power-on in a fresh emulator, so a run
        assembled from branches is verified exactly like a run assembled by
        walking, and a branch that cannot be replayed fails the same way.
        """
        emu = self._emu()
        d = emu.load_checkpoint(self.name, checkpoint)
        self.log("")
        self.log(f"=== BRANCH from checkpoint {checkpoint!r} ===")
        self.log(f"  {d}")
        meta = (d / "segments.txt").read_text(encoding="utf-8")
        self.log("  " + meta.replace("\n", "\n  ").rstrip())
        if not emu.inputs:
            raise ActionFailed(
                f"branching to {checkpoint!r} with an empty input log. The "
                "branch's whole value is that it does not re-walk the route, and "
                "it can only be replayed if the log still holds every frame that "
                "came before this point. Load the checkpoint's own input log "
                "first -- this run has none, so it was never here.")
        self.log(f"  the run's input log is intact at {len(emu.inputs)} frames, "
                 f"which is what makes this replayable")
        self.branches.append({"checkpoint": checkpoint, "dir": str(d),
                              "inputs_at": len(emu.inputs),
                              "segments": [s.name for s in segments]})
        out = []
        for i, seg in enumerate(segments):
            self.log("")
            self.log(f"[branch {i + 1}/{len(segments)}] {seg.name}")
            out.append(self.segment(seg, tries=tries, branch_from=checkpoint))
        return out

    # -- the route -------------------------------------------------------
    def run(self, route: Route) -> "Run":
        """Every segment in order, each one walking on from the last."""
        install(route)
        if self.emu is None:
            self.start(route)
        for i, seg in enumerate(route.segments):
            self.log("")
            self.log(f"[{i + 1}/{len(route.segments)}] {seg.name}")
            self.segment(seg)
        return self

    # -- ledgers ---------------------------------------------------------
    def _ledger(self, seg: Segment, res: search.SearchResult,
                 branch_from: str = "") -> None:
        """One line per attempt, written as it happens.

        Not written at the end of the segment: a search that hangs, or a run that
        is killed, must still leave behind what it tried. The losing attempts
        are the ones that matter here -- a route that only records its winner
        cannot be told apart from a route that got lucky.
        """
        p = self.outdir / f"{seg.name}.attempts.txt"
        scouts = sorted({a.scout for a in res.attempts if a.scout})
        head = (f"# {seg.name} on {self.label} -- {self.rom.name}\n"
                f"# {seg.why}\n"
                f"# success test: {seg.describe_success()}\n"
                + (f"# BRANCHED FROM CHECKPOINT {branch_from}\n" if branch_from
                   else "")
                + (f"# {len(scouts)} scout emulator(s) contributed attempts: "
                   f"{scouts}. Every attempt below records which one.\n"
                   if scouts else "")
                + "# every attempt, winners and losers:\n")
        body = "".join("  " + a.line() + "\n" for a in res.attempts)
        tail = (f"# {res.tries} attempts, {res.wins} success, stopped {res.why}"
                + (f"; best {res.best.frames} frames (seed {res.best.seed})"
                   if res.best else "; NO SUCCESS") + "\n")
        p.write_text(head + body + tail, encoding="utf-8")

    def _write_ledger(self, seg: Segment, res: search.SearchResult) -> None:
        """Where the segment ended up, next to its attempts."""
        img = self._emu().work_ram()
        (self.outdir / f"{seg.name}.where.txt").write_text(
            f"{seg.name} on {self.label} ({self.rom.name})\n"
            f"  last state: {search._where(img)}\n"
            f"  attempts:   {res.tries}\n"
            f"  successes:  {res.wins}\n"
            f"  stopped:    {res.why}\n", encoding="utf-8")

    # -- finishing -------------------------------------------------------
    def finish(self, comment: str = "") -> dict:
        """Save the log, then replay it from power-on in a FRESH emulator.

        Nothing is claimed before this. The two outcomes are deliberately
        different words: `verified` means the inputs alone reproduced the RAM,
        and `diverged` means the run is not reproducible and the run's own log
        says so in its own output rather than in a later reader's memory.
        """
        emu = self._emu()
        fp = emu.fingerprint()
        inputs = emu.save_inputs(f"{self.name}_{self.label}")
        state = emu.work_ram()
        v = ram.decode(state)
        self.log("")
        self.log(f"final at frame {emu.frame}: {search._where(state)} "
                 f"mana={v['manacur']}/{v['manatop']} gold={v['wealth']} "
                 f"time={v['gametime']}s")
        self.log(f"input log: {inputs} ({len(emu.inputs)} frames, "
                 f"valid_from_poweron={emu.input_log_valid})")
        self.log(f"fingerprint: {fp}")
        if not self.verify:
            self.result = {"verified": None, "fingerprint": fp, "inputs": inputs,
                           "frames": len(emu.inputs), "note": "replay SKIPPED -- not a proof"}
            return self.result
        # Close the playing emulator first. BizHawk flushes a battery save when it
        # shuts down, and a SaveRAM file appearing while the replay emulator is
        # loading the ROM makes the replay start from a saved game instead of a
        # blank cartridge -- a bogus "the log does not reproduce the run".
        self.log("closing MAIN and every scout so a battery save cannot leak into the "
                 "replay...")
        # `close()` closes the scouts too, and it has to: a scout is a full
        # window, and one left running would be adopted by the replay
        # emulator's launch -- the exact failure the 45-second PID wait in
        # `emu.close()` exists to prevent.
        self.close()
        self.log("replaying the recorded inputs from power-on in a fresh emulator...")
        with BizHawk(rom=self.rom, log_name=f"{self.name}_{self.label}_replay",
                     route="replay", run=f"{self.label}_replay") as emu2:
            frames = emu2.load_inputs(inputs)
            self.log(f"  replaying {len(frames)} frames from power-on")
            emu2.run_inputs(frames)
            fp2 = emu2.fingerprint()
            self.log(f"  replay ended at frame {emu2.frame}, fingerprint {fp2}")
            self.log(f"  replay state: {search._where(emu2.work_ram())}")
            emu2.screenshot(f"{self.name}_{self.label}_replay")
        out = {"verified": fp2 == fp, "fingerprint": fp, "replay_fingerprint": fp2,
               "inputs": inputs, "frames": len(emu.inputs),
               "rom": str(self.rom), "label": self.label,
               "branches": list(self.branches),
               "scouts": max(0, self.scouts_wanted - 1)}
        self.result = out
        if fp2 != fp:
            self.log("  MISMATCH -- the recorded inputs do not reproduce the run, "
                     "so this run is NOT reproducible and nothing about it is claimed.")
        else:
            self.log("  MATCH -- the recorded inputs reproduce the run from power-on "
                     "in a fresh emulator.")
            if self.branches:
                # Worth saying out loud, because it is the thing a reader of a
                # branch-assembled run wants to know and cannot infer: the proof
                # above replays the ENTIRE log from frame 0, branch points
                # included. No savestate and no checkpoint contributed to it.
                self.log(f"  this run was assembled from {len(self.branches)} "
                         f"branch(es) "
                         + ", ".join(f"{b['checkpoint']}@{b['inputs_at']}f"
                                     for b in self.branches)
                         + " -- and the replay above replayed all "
                         f"{len(emu.inputs)} frames from frame 0, so no branch "
                         "contributed state to the proof.")
        return out

    # -- misc ------------------------------------------------------------
    def _emu(self) -> BizHawk:
        if self.emu is None:
            raise RuntimeError("the run is not started (or has been closed)")
        return self.emu

    def total_frames(self) -> int:
        """Frames in the run's input log, or its last count if the run is closed.

        After `finish()` MAIN has been closed and the log lives in a file, so the
        number comes from the result instead. Reported either way rather than 0,
        because a summary saying "0 frames" for a finished run is worse than no
        summary at all.
        """
        e = self.emu
        if e is not None:
            return len(e.inputs)
        n = (self.result or {}).get("frames")
        return int(n) if isinstance(n, int) else 0

    def summary(self) -> str:
        out = [f"{self.name} on {self.label}: {len(self.done)} segments, "
               f"{self.total_frames()} frames total, "
               f"{self.scouts_wanted - 1} scout emulator(s)"]
        for r in self.reports:
            src = f"  <- branch {r.branch_from}" if r.branch_from else ""
            out.append(f"  {r.name:<16} {r.frames:>6}f  {r.wins}/{r.tries} attempts  "
                       f"stopped {r.why}{src}")
        for b in self.branches:
            out.append(f"  BRANCH {b['checkpoint']} at {b['inputs_at']}f -> "
                       + ", ".join(b["segments"]))
        return "\n".join(out)


def _total_frames(run: "Run") -> int:
    """Frames in the run's input log, or its last count if the run is closed.

    After `finish()` MAIN has been closed and the log lives in a file, so the
    number comes from the result instead. Reported either way rather than 0,
    because a summary that says "0 frames" for a finished run is worse than no
    summary.
    """
    e = run.emu
    if e is not None:
        return len(e.inputs)
    n = (run.result or {}).get("frames")
    return int(n) if isinstance(n, int) else 0


def segment_list_sha1(segments) -> str:
    """Names in order, and nothing else. See `route.segment_list_sha1`."""
    return hashlib.sha256(
        "|".join(s.name if isinstance(s, Segment) else str(s)
                 for s in segments).encode()).hexdigest()[:16]