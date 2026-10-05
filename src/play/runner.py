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

ONE EMULATOR, NOT TWO
---------------------
`aibeatszelda` gives every scout its own EmuHawk. This project cannot: BizHawk
diverts a second launch into the first through its single-instance pipe, which
is why `emu.BizHawk.__init__` refuses to start when a session is already
running. So "a copy of MAIN's state" here is a *savestate on disk*:

    MAIN  --save_state-->  seg.state
    scout: restore seg.state, run an attempt, restore, run the next
    MAIN:  restore seg.state, play the winner's inputs, append to the log

MAIN is restored rather than rewound: the frame counter, the PPU, the banks --
everything in the savestate -- goes back to the bytes that produced the log
prefix, and the prefix itself is never touched. `search.Recorder` is what keeps
the scout's own frames out of the master log while a search runs.

Because there is exactly one emulator, `Run.open()` refuses `scouts > 1` rather
than pretending. `search.parallel_search` still takes a list of scouts, because
that is the general form and it is the form the runner will use on a machine
that can hold more than one window.

RESUMING DOES NOT LOAD A SAVESTATE
----------------------------------
To resume, the recorded input prefix is replayed from power-on. That is slower
than a savestate load and it is the point: the one operation a resumable run
must not have is "put the machine somewhere it has not been and carry on".
"""
from __future__ import annotations

import hashlib
import json
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


@dataclass
class SegmentReport:
    """What one segment cost, and what the scout learned while paying it."""
    name: str
    frames: int
    tries: int
    wins: int
    why: str
    result: search.SearchResult


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
        self.result: dict = {}            # filled in by finish()
        self.t0 = time.time()
        self.outdir = SEARCH_LOGS / self.label
        self.outdir.mkdir(parents=True, exist_ok=True)

    # -- lifecycle -------------------------------------------------------
    def start(self, route: Route | None = None) -> None:
        stale = BizHawk.running_emuhawk()
        if stale:
            raise BridgeError(
                f"mono pids {stale} are already running. BizHawk allows one "
                "instance and would divert this launch into the other, so the "
                "run would measure a session it did not start.")
        if self.scouts_wanted != 1:
            raise ValueError(
                f"{self.scouts_wanted} scouts were asked for and this project "
                "cannot have two: BizHawk's single-instance pipe diverts a "
                "second launch into the first. `search.parallel_search` takes a "
                "list of scouts and will use it on a machine that can hold more "
                "than one window; here there is one machine, and a scout is a "
                "savestate on it.")
        # The route is installed BEFORE the emulator exists, because every
        # snapshot records the route's segment digest and `snapshot()` is called
        # before the first segment. Installed afterwards, every checkpoint in the
        # run reads `segment_list_sha1=-`, the guard that refuses a foreign
        # checkpoint is inert for the whole run, and nothing says so.
        if route is not None:
            install(route)
        self.emu = BizHawk(rom=self.rom, log_name=f"{self.name}_{self.label}",
                           route=self.name, run=self._free_run_tag())
        self.emu.fast()
        self.log(f"run {self.name} [{self.label}] on {self.rom}")
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
    def segment(self, seg: Segment, *, tries: int | None = None) -> SegmentReport:
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
            return self._segment(seg, tries)
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

    def _segment(self, seg: Segment, tries: int | None) -> SegmentReport:
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
        self.log(f"  checkpoint -> {snap}")
        self.log(f"  scout state -> {state} ({state.stat().st_size} bytes)")
        if seg.why:
            self.log(f"  {seg.why}")
        self.log(f"  success test: {test.__doc__ or seg.describe_success()}")
        if tries:
            res = search.random_search(
                emu, st, seg.factory, test, tries=tries,
                max_frames=seg.max_frames, settle=seg.settle, log=self.log,
                label=seg.name, first_success=seg.first_success,
                accept_after=seg.accept_after or search.ACCEPT_AFTER)
            self._ledger(seg, res)
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
            self._ledger(seg, res)
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
                            res.why, res)
        self.reports.append(rep)
        self.log(f"[{seg.name}] {res.best.frames} frames "
                 f"(of {len(emu.inputs)} total), {res.wins}/{res.tries} attempts "
                 f"succeeded, stopped {res.why}")
        self.log(f"  now at {search._where(after)}")
        emu.snapshot(f"{seg.name}_post")
        self._write_ledger(seg, res)
        return rep

    # -- the route -------------------------------------------------------
    def run(self, route: Route) -> "Run":
        install(route)
        if self.emu is None:
            self.start(route)
        for i, seg in enumerate(route.segments):
            self.log("")
            self.log(f"[{i + 1}/{len(route.segments)}] {seg.name}")
            self.segment(seg)
        return self

    # -- ledgers ---------------------------------------------------------
    def _ledger(self, seg: Segment, res: search.SearchResult) -> None:
        """One line per attempt, written as it happens.

        Not written at the end of the segment: a search that hangs, or a run that
        is killed, must still leave behind what it tried. The losing attempts
        are the ones that matter here -- a route that only records its winner
        cannot be told apart from a route that got lucky.
        """
        p = self.outdir / f"{seg.name}.attempts.txt"
        head = (f"# {seg.name} on {self.label} -- {self.rom.name}\n"
                f"# {seg.why}\n"
                f"# success test: {seg.describe_success()}\n"
                f"# every attempt, winners and losers:\n")
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
        self.log("closing the emulator so a battery save cannot leak into the replay...")
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
               "rom": str(self.rom), "label": self.label}
        self.result = out
        if fp2 != fp:
            self.log("  MISMATCH -- the recorded inputs do not reproduce the run, "
                     "so this run is NOT reproducible and nothing about it is claimed.")
        else:
            self.log("  MATCH -- the recorded inputs reproduce the run from power-on "
                     "in a fresh emulator.")
        return out

    # -- misc ------------------------------------------------------------
    def _emu(self) -> BizHawk:
        if self.emu is None:
            raise RuntimeError("the run is not started (or has been closed)")
        return self.emu

    def summary(self) -> str:
        out = [f"{self.name} on {self.label}: {len(self.done)} segments, "
               f"{self.reports[-1].frames if self.reports else 0} frames in the "
               f"last one"]
        for r in self.reports:
            out.append(f"  {r.name:<16} {r.frames:>6}f  {r.wins}/{r.tries} attempts  "
                       f"stopped {r.why}")
        return "\n".join(out)


def segment_list_sha1(segments) -> str:
    """Names in order, and nothing else. See `route.segment_list_sha1`."""
    return hashlib.sha256(
        "|".join(s.name if isinstance(s, Segment) else str(s)
                 for s in segments).encode()).hexdigest()[:16]