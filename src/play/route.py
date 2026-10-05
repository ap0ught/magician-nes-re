"""An ordered list of segments. A route is a PLAN; `runner.Run` executes one.

WHAT A SEGMENT IS NOW
---------------------
A segment is `(name, factory, success, tries)`:

  * `factory()` returns a **policy** -- `policy(rng, rec, emu, max_frames)` --
    which presses buttons through `rec` and returns a short note saying what it
    did.
  * `success(image)` is a predicate on ONE work-RAM image, and nothing else.
    It is a function of RAM rather than of the emulator on purpose: everything
    these segments assert on lives in `$0000-$07FF`, and a success test that
    needs a live emulator cannot be tested against a fake one.
  * `tries=0` means "this segment has no choices". The policy still runs once,
    through the same `Recorder`, so its inputs still splice into the master log
    by exactly the same path a searched line does.

`route.py` does NOT talk to the emulator. `runner.py` executes a route.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable

from . import ram

__all__ = ["Segment", "Route", "segment_list_sha1", "active", "install"]


@dataclass(frozen=True)
class Segment:
    """One activity, and what makes it finished.

    `why` is not decoration: a route that says "walk east" without saying what
    for cannot be checked by anybody reading it later, and the segments here are
    the ones whose purpose is a measurement.

    `first_success` is the difference between "get there" and "get there
    quickly". Reaching the playing level has no faster line -- the success test
    IS the goal -- so a search for a shorter one spends frames finding nothing.
    Walking has several routes, so it does not.

    `prepare=True` means `success` is a FACTORY, not a predicate: the runner
    calls `success(start_image)` once, with the RAM MAIN is actually at, and
    uses what comes back. Only a test that depends on where a segment started
    needs this -- "the player moved" does, and "phase is g00" does not -- and it
    is spelled out per segment rather than left to a module-level global that a
    second run would inherit.
    """
    name: str
    factory: Callable[[], Callable]
    # `Callable[..., Any]` rather than `Callable[[bytes], bool]` because with
    # `prepare=True` this field is a FACTORY that returns the predicate, and a
    # type that said "always takes bytes, always returns bool" would be a lie in
    # one of its two documented shapes. The runner decides which it is, from
    # `prepare`, once per segment.
    success: Callable[..., Any]
    tries: int = 8
    max_frames: int = 900
    settle: int = 0
    why: str = ""
    first_success: bool = False
    prepare: bool = False
    # Attempts without improvement before a search that already has a line stops
    # and takes it. 0 means the search default. A segment raises it when the
    # POINT of the search is to survey rather than to minimise -- see `walk`,
    # which is not looking for the shortest way to move, it is finding out which
    # of the four directions this town lets you go.
    accept_after: int = 0

    def describe_success(self) -> str:
        doc = (self.success.__doc__ or "").strip().splitlines()
        return doc[0] if doc else getattr(self.success, "__name__", "?")


@dataclass
class Route:
    name: str
    segments: list[Segment] = field(default_factory=list)

    def add(self, name: str, factory, success, *, tries: int = 8,
            max_frames: int = 900, settle: int = 0, why: str = "",
            first_success: bool = False, prepare: bool = False,
            accept_after: int = 0) -> "Route":
        if any(s.name == name for s in self.segments):
            raise ValueError(f"route {self.name!r} already has a segment called {name!r}")
        self.segments.append(Segment(name, factory, success, tries, max_frames,
                                     settle, why, first_success, prepare,
                                     accept_after))
        return self

    def digest(self) -> str:
        return segment_list_sha1(self.segments)


def segment_list_sha1(segments) -> str:
    """SHA1 of a segment list: names, in order, and nothing else.

    Deliberately the list and nothing else -- not the ROM, not the frame count,
    not the policy's identity. Those change for reasons that are not "the plan
    changed", and a checkpoint invalidated by an unrelated edit is a checkpoint
    nobody keeps.
    """
    h = hashlib.sha256()
    for s in segments:
        h.update((s.name if isinstance(s, Segment) else str(s)).encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


_ACTIVE: dict = {"route": None}


def active() -> Route:
    """The route currently installed, for `BizHawk.segment_digest()`."""
    if _ACTIVE["route"] is None:
        raise AssertionError("no route installed; call install() first")
    return _ACTIVE["route"]


def install(r: Route) -> Route:
    _ACTIVE["route"] = r
    return r


def segment_list_sha1_default() -> str:
    """Digest of the active route, or '-' when there is none."""
    r = _ACTIVE["route"]
    return r.digest() if r is not None else "-"


# ---------------------------------------------------------------- predicates
# Shared success tests, built from the named-field predicate language in ram.py
# so that an address which is not in the symbol table cannot reach a segment.

def holds(*preds: ram.Pred):
    """`success(image)` from a set of RAM predicates."""
    ps = list(preds)

    def ok(image: bytes) -> bool:
        return all(p.holds(image) for p in ps)
    ok.__doc__ = " AND ".join(str(p) for p in ps)
    return ok


def moved(img_before: bytes):
    """The player's map position is different. A wall is a legal non-answer."""
    def ok(image: bytes) -> bool:
        return (ram.plrx(image), ram.plry(image)) != (ram.plrx(img_before),
                                                     ram.plry(img_before))
    ok.__doc__ = (f"the player's map position differs from "
                  f"({ram.plrx(img_before)},{ram.plry(img_before)})")
    return ok