"""BizHawk session for The Magician: frame stepping, RAM decode, snapshots.

This is the ONLY module in src/play/ that talks to the emulator. Actions and
routes receive a `BizHawk` and never see a socket, a subprocess or a Lua path.

WHY A BRIDGE, AND WHY THIS SHAPE
--------------------------------
EmuHawk takes exactly one `--lua` script and offers no input-injection API from
outside. Input comes from `joypad.set()`, which only exists inside a Lua script.
So the architecture is: Python listens on a loopback socket, launches
tools/bizhawk/run.sh (which proves the core and the cartridge before handing
over), and the Lua script in src/play/bridge.lua connects back and executes one
command per line. This is aibeatszelda's design and it is the design that works
here; `make probe`'s comment that input injection is "genuinely missing" was
true only for tools that drive the window from outside.

Two things are done differently from aibeatszelda, both because this project's
failure mode is a plausible wrong number rather than a crash:

  * **Predicates are evaluated inside the core.** `step_until` sends the whole
    predicate set with the frame budget and gets back "held after N frames, or
    never". Evaluating predicates in Python would cost a socket round trip and
    a full 2 KiB RAM read per frame -- tens of thousands of them per walk -- and
    would move the thing being measured (the RAM) across a socket in the middle
    of a predicate. The contract is unchanged: the assertion is still on a value
    the core read out of its own memory, and `step_until` still raises when the
    budget expires.

  * **Every reply is checked for being an answer to the question asked.** A
    reply that arrives out of step is the failure that costs a whole run and
    reports nothing; `_answers()` rejects a reply that cannot possibly be right
    for the command sent, and the error names the last few (command, reply)
    pairs so the slip can be located.

WHAT IS ASSERTED HERE RATHER THAN ASSUMED
-----------------------------------------
  * the core is the one asked for and the cartridge is the exact PRG window on
    disk (tools/bizhawk/verify_preamble.lua, run before bridge.lua at all)
  * `domains` reports nine, and every one the snapshot contract names is present
  * every domain read is confirmed selected with getcurrentmemorydomain(), because
    an unknown name does not fail -- it leaves the previous selection in place
  * every RAM read is all-or-nothing: a short buffer is an error, never a short
    hex string that decodes to something plausible
  * a snapshot's files exist at the exact size the core said the domain was
  * the cartridge's battery bit is derived per ROM; nothing is deleted from
    NES/SaveRAM/ and nothing in it is written by this module
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import socket
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Sequence

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# ------------------------------------------------------------------ WHERE THINGS ARE
# One name, one answer, and the answer is a directory THIS PROJECT owns.
#
# The measured shape of the bug this replaces (2026-10-05, journal/14):
#
#     exported              this guard checked         run.sh launched
#     --------------------  -------------------------  --------------------------
#     BIZHAWK=/new          ANOTHER PROJECT's dir       /new
#     MAGICIAN_BIZHAWK=/new     /new                   ANOTHER PROJECT's dir
#
# `run.sh` read `$BIZHAWK`, this module read `$MAGICIAN_BIZHAWK`, both had their
# own hardcoded default into the other project's install, and `__init__` built the
# child environment from `dict(os.environ)` while mentioning only `MAGICIAN_*`
# keys -- so the guard and the launch could not agree even in principle. Setting
# `MAGICIAN_BIZHAWK` got you a guard that certified your directory and a run out
# of somebody else's, both reporting success. The bill came due when a service
# pointed at that install and a `pkill` killed a 136,526-frame replay that was
# not its own.
#
# So `MAGICIAN_BIZHAWK` is canonical and `BIZHAWK` is an accepted alias -- the
# alias stays because every existing invocation and every shell test sets it, and
# because "keep the env-var override working" is part of the requirement -- and the
# two DISAGREEING is an error rather than a preference to be resolved quietly.
#
# `tools/bizhawk/bizpath.sh` is the same rule in bash, and the two are compared
# against each other in `src/testing/test_bizpath.py` section F rather than
# assumed to agree. Two copies of a rule in two languages drift silently; a
# comment claiming they match is worth nothing.
#
# RESOLUTION DOES NOT TOUCH THE FILESYSTEM. Five files in `src/testing/` import
# this module and the suite must stay emulator-free, so "is there a BizHawk here"
# is a question for `__init__`, which is where it already was, and the import
# cannot fail on a machine with no emulator installed.
BIZHAWK_VERSION = "2.11.1"
# Named `-linux-x64`, not `-win-x64`: the other project's copy carries the
# Windows name because ITS launcher derives the directory name from its checkout.
# Nothing here derives anything, so the name can be the truth -- which also means
# `BizHawk-2.11.1-win-x64` appearing in one of our log lines reads immediately as
# "that was not us".
BIZHAWK_DIRNAME = f"BizHawk-{BIZHAWK_VERSION}-linux-x64"
# The `HOME`-relative part, kept as a constant rather than a whole path because
# the whole path is `$HOME`-dependent: the first version of `resolve_bizhawk`
# built it at IMPORT time from the real `$HOME`, so a caller (or a test) whose
# environment named a different home got the real machine's default back and the
# Python and bash halves disagreed. `src/testing/test_bizpath.py` section F is
# what found it.
BIZHAWK_HOME_SUFFIX = ("code", "games", "magician-nes-bizhawk")
BIZHAWK_HOME_DEFAULT = pathlib.Path.home().joinpath(*BIZHAWK_HOME_SUFFIX)


class BizHawkConfigError(RuntimeError):
    """The emulator directory is configured twice, differently.

    Its own exception rather than a `FileNotFoundError` because it is not a
    missing file: nothing is missing, and a caller that catches
    `FileNotFoundError` to report "install it with `make emu-setup`" would report
    the wrong fix for the wrong problem.
    """


def resolve_bizhawk(env: dict | None = None) -> tuple[pathlib.Path, str]:
    """`(directory, where that answer came from)` from an environment mapping.

    Deliberately takes the mapping rather than reading `os.environ` inside, so a
    test can ask what a given environment resolves to without mutating this
    process's own -- the difference between checking the rule and checking one
    particular invocation of it.

    Raises `BizHawkConfigError` if both names are set to different directories.
    Returns a path that may not exist; existence is `BizHawk.__init__`'s question,
    because this module is imported by a test suite that has no emulator.
    """
    e = os.environ if env is None else env
    canon = e.get("MAGICIAN_BIZHAWK") or ""
    alias = e.get("BIZHAWK") or ""
    if canon and alias and os.path.abspath(canon) != os.path.abspath(alias):
        raise BizHawkConfigError(
            f"the emulator directory is set twice, differently:\n"
            f"    MAGICIAN_BIZHAWK = {canon}\n"
            f"    BIZHAWK           = {alias}\n"
            f"These are one setting with two names, not two settings. They "
            f"disagree, so it is not knowable from here which one the run would "
            f"use, and picking one would be a guess in the one place this project "
            f"cannot afford a guess. Unset one of them.")
    if canon:
        return pathlib.Path(os.path.abspath(canon)), "MAGICIAN_BIZHAWK"
    if alias:
        return pathlib.Path(os.path.abspath(alias)), "BIZHAWK (alias)"
    home = pathlib.Path(os.path.abspath(
        e.get("MAGICIAN_BIZHAWK_HOME") or e.get("HOME")
        or BIZHAWK_HOME_DEFAULT))
    return home.joinpath(*BIZHAWK_HOME_SUFFIX) / BIZHAWK_DIRNAME, \
        "the default location"


BIZHAWK, BIZHAWK_SOURCE = resolve_bizhawk()

RUN_SH = ROOT / "tools" / "bizhawk" / "run.sh"
BRIDGE_LUA = HERE / "bridge.lua"
# `bridge.lua` opens with `require("socket.core")`. BizHawk ships only the Windows
# `Lua/socket/core.dll`, so on Linux the module has to be BUILT for NLua's embedded
# Lua 5.4 and installed beside it -- `make emu-setup` does that, and it is the one
# thing about this project's emulator that no tarball provides. Checked HERE
# rather than in `bizpath.sh` because `run.sh` also drives scripts that never open
# a socket (title.lua, replay.lua, frame.lua); only this module needs it.
SOCKET_SO = "Lua/socket/core.so"

LOGS = ROOT / "logs"
SHOTS = ROOT / "shots"
CHECKPOINTS = LOGS / "checkpoints"
INPUTS = LOGS / "inputs"

ROM = pathlib.Path(os.environ.get("MAGICIAN_ROM") or (ROOT / "asm/out/magician-rebuilt.nes"))

BUTTONS = ("Up", "Down", "Left", "Right", "Select", "Start", "B", "A")
ALL_BUTTONS = BUTTONS + ("Power", "Eject", "Stretch", "L", "R")

_HEX = set("0123456789abcdefABCDEF")

# The nine memory domains this build of quickerNES exposes. Named here because
# the snapshot contract is written in terms of them; the bridge refuses to start
# if any is missing, so a core that lost one fails loudly instead of producing a
# snapshot that is quietly smaller.
REQUIRED_DOMAINS = (
    "CHR", "CHR VROM", "CIRAM (nametables)", "CPU registers", "OAM",
    "PALRAM", "PRG ROM", "System Bus", "WRAM",
)

# The RAM window lives in ram.py -- see the note there.


class BridgeError(RuntimeError):
    """The bridge answered something other than an answer to the question asked."""


class ActionFailed(AssertionError):
    """An action's predicate did not become true inside its frame budget.

    An AssertionError subclass so a test can assert on it with `pytest.raises`,
    and so the failure reads as a failed assertion rather than a crash. Actions
    raise this instead of warning: a budget that expires means the plan and the
    build disagree, and continuing would put every later measurement in doubt.
    """


def _kv(line: str) -> dict[str, str]:
    out = {}
    for tok in line.split():
        k, _, v = tok.partition("=")
        out[k] = v
    return out


def _is_int_line(resp: str) -> bool:
    """`ok k=v k=v ...` where every value is a non-negative integer.

    Stricter than "starts with ok": the reply that must be caught is one that
    came from the wrong command entirely, and it starts with `ok` too.
    """
    toks = resp.split()
    if not toks or toks[0] != "ok":
        return False
    for t in toks[1:]:
        k, sep, v = t.partition("=")
        if not sep or not k or not v.isdigit():
            return False
    return True


def _hex_ok(resp: str) -> bool:
    """`ok frame=<n> bytes=<n> <hex>` -- and only that.

    The hex payload is the LAST space-separated field, so the reply cannot be
    split on " " and take token 3: `resp.split(" ", 2)[2]` is
    `"bytes=2048 ffff..."`, which contains letters that are not hex digits, and
    a checker that rejects a perfectly good reply fails as mysteriously as one
    that accepts a bad one.
    """
    parts = resp.split(" ")
    if len(parts) < 3 or parts[0] != "ok":
        return False
    for t in parts[1:-1]:
        k, sep, v = t.partition("=")
        if not sep or not v.isdigit():
            return False
    payload = parts[-1]
    n = int(parts[-2].split("=", 1)[1])
    return len(payload) == n * 2 and not (set(payload) - _HEX)


@dataclass
class Domain:
    index: int
    name: str
    size: int


@dataclass
class Result:
    """What an action did, for the run log and for tests.

    `used` is frames spent; `before`/`after` are full work-RAM images so the log
    can show what actually changed rather than what was expected to change.
    `held` is whether the action's predicate became true inside its budget --
    an action that ran its budget out raises, so a Result with held=False only
    ever appears for a recon probe, never for an action.
    """
    name: str
    used: int
    before: bytes
    after: bytes
    held: bool
    note: str = ""

    def diff(self, fields) -> list[str]:
        """`name old -> new` for every named field whose value changed.

        Used by the run log and by tests. Reading the values out of the two RAM
        images rather than from a running emulator is what makes this checkable
        after the fact.
        """
        out = []
        for f in fields:
            try:
                a = f.get(self.before)
                b = f.get(self.after)
            except Exception:
                continue
            if a != b:
                out.append(f"{f.name} {a} -> {b}")
        return out


def child_env(base: dict | None = None) -> dict:
    """The environment `run.sh` is launched with, with the emulator pinned in it.

    THE FUNCTION THIS PROJECT WAS MISSING, and the smallest expression of the
    measured bug. `__init__` used to do `env = dict(os.environ)` and add only
    `MAGICIAN_*` keys, so `run.sh` inherited whatever `BIZHAWK` happened to hold
    and this module's own `MAGICIAN_BIZHAWK` never reached it. The guard in
    `__init__` and the launcher below it were then reading two different variables
    with two different defaults, and both halves could be right.

    Both names are set, and to the SAME value, deliberately:

      * setting `MAGICIAN_BIZHAWK` is what `bizpath.sh` reads first, so this is
        the name that decides;
      * setting `BIZHAWK` too OVERWRITES rather than coexists. If only one were
        set and the caller's environment carried the other, `bizpath.sh` would
        see two different directories and refuse -- which is the correct outcome,
        but it would be a refusal at launch time for something decided at import
        time. Overwriting makes the disagreement unrepresentable instead of
        merely detectable.

    `base` exists so a test can ask what the environment would be under a given
    mapping instead of mutating this process's own. Checked by
    `src/testing/test_bizpath.py` section F.
    """
    env = dict(os.environ if base is None else base)
    env["MAGICIAN_BIZHAWK"] = str(BIZHAWK)
    env["BIZHAWK"] = str(BIZHAWK)
    env["MAGICIAN_BIZHAWK_SOURCE"] = BIZHAWK_SOURCE
    return env


class BizHawk:
    """One EmuHawk instance, driven frame by frame over a loopback socket."""

    def __init__(self, rom: pathlib.Path = ROM, log_name: str = "play",
                 route: str = "", run: str = "", kill_stale: bool = False,
                 settle: int = 120, connect_timeout: int = 90,
                 concurrent: bool = False):
        self.rom = pathlib.Path(rom).resolve()
        if not self.rom.exists():
            raise FileNotFoundError(
                f"no ROM at {self.rom} -- run `make rom` first. The rebuilt "
                "cartridge is generated from the source; a cartridge is never "
                "committed (LEGAL.md).")
        if not RUN_SH.exists():
            raise FileNotFoundError(f"no launcher at {RUN_SH}")
        # The emulator, checked HERE rather than only inside run.sh, and the two
        # are held together by `child_env()` below handing run.sh this same path.
        # A missing install is a refusal that names the fix, never a fallback onto
        # some other directory that happens to exist: this is the bug class
        # `journal/14` is about, and `${VAR:-<the sibling>}` cannot express "no".
        if not BIZHAWK.is_dir():
            raise FileNotFoundError(
                f"no BizHawk at {BIZHAWK} (from {BIZHAWK_SOURCE}).\n"
                f"  This project runs its OWN emulator install; it does not share "
                f"one with another checkout.\n"
                f"  make emu-setup            installs "
                f"{BIZHAWK_DIRNAME} (~150 MB) into\n"
                f"                            {BIZHAWK_HOME_DEFAULT} and builds "
                f"{SOCKET_SO},\n"
                f"                            which BizHawk does not ship.\n"
                f"  MAGICIAN_BIZHAWK=/path    use an install you already have.")
        if not (BIZHAWK / SOCKET_SO).is_file():
            # Measured, not assumed: `bridge.lua:50` is `require("socket.core")`
            # and BizHawk's tarball carries only the Windows `core.dll`. Without
            # this module the bridge dies on its first line and the run then
            # reports a *connect timeout*, which reads like a networking problem
            # rather than a missing 100 KB file.
            raise FileNotFoundError(
                f"no {SOCKET_SO} in {BIZHAWK}, so src/play/bridge.lua cannot "
                f"`require('socket.core')`.\n"
                f"  BizHawk ships only the Windows core.dll; the Lua 5.4 module "
                f"has to be built.\n"
                f"  make emu-setup            downloads, extracts and builds it.")
        if not (BIZHAWK / "EmuHawkMono.sh").is_file():
            raise FileNotFoundError(
                f"no EmuHawkMono.sh in {BIZHAWK} -- that directory exists but is "
                f"not a BizHawk install.\n"
                f"  A path that happens to exist is not the same thing as an "
                f"emulator.\n"
                f"  make emu-setup            installs one.")

        for d in (LOGS, SHOTS, CHECKPOINTS, INPUTS):
            d.mkdir(parents=True, exist_ok=True)
        self.route = route
        # `run` namespaces the checkpoints on disk. It is NOT part of a
        # checkpoint's identity -- `segments.txt` records the route's segment
        # digest, and that is what the guard checks -- it only stops two runs of
        # the SAME route from colliding. Without it, the second run of a milestone
        # could not run at all, because every checkpoint name would already
        # exist, and a milestone that can only be run once is a milestone whose
        # second run has to be deleted by hand.
        self.run = run or log_name
        self.log_path = LOGS / f"{log_name}.log"
        self.bridge_log = LOGS / f"{log_name}.bridge.log"
        self.cmd_log = LOGS / f"{log_name}.cmd.log"
        self._cmdlog = open(self.cmd_log, "a", encoding="utf-8")
        # run.sh refuses to launch beside another EmuHawk, because a launch that
        # IS diverted into a running session would report the other session's
        # memory while looking entirely healthy. `concurrent=True` is the opt-in
        # for launching a second window on purpose, and it is never silent: run.sh
        # prints what it is doing and `Run.start` then checks that this launch
        # really did add a process.
        #
        # The refusal was once stated as a property of BIZHAWK ("diverts a
        # single-instance launch into the first") and that was wrong on this
        # machine: BizHawk 2.11.1's config.ini carries SingleInstanceMode=false
        # and three concurrent sessions were measured, each with its own PID and
        # its own RAM. run.sh's own guard is what refused. Both are recorded in
        # runner.N_EMULATORS; this comment is only here so the flag's meaning is
        # readable at the place it is used.
        env = child_env()
        env["MAGICIAN_BRIDGE_LOG"] = str(self.bridge_log)
        env.pop("MAGICIAN_BRIDGE_PORT", None)
        if concurrent:
            env["MAGICIAN_ALLOW_CONCURRENT"] = "1"
        if kill_stale:
            env["MAGICIAN_KILL_STALE"] = "1"
        env["MAGICIAN_SETTLE"] = str(settle)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.port = srv.getsockname()[1]
        env["MAGICIAN_BRIDGE_PORT"] = str(self.port)

        # THE PRE-LAUNCH SNAPSHOT, in the one place it can be correct: immediately
        # before Popen, and nowhere else.
        #
        # It was taken AFTER the launch, several lines further down, and the delta
        # was therefore always empty -- so `diverted` was always True and
        # `Run.start`'s "this launch added no new EmuHawk process" guard fired on
        # every run, including one that had demonstrably just created its window.
        # MEASURED: milestone 1 on Beta 1 with --scouts 3 stopped at
        # `BRIDGE FAILED: ... added no new EmuHawk process` after 3.3 seconds.
        # A guard whose input is captured in the wrong order does not degrade
        # gracefully; it is simply always on, and reads as an emulator problem.
        self._before_pids = set(self.running_emuhawk())

        cmd = [str(RUN_SH), str(BRIDGE_LUA), str(self.rom), str(self.log_path)]
        self._runsh = subprocess.Popen(
            cmd, cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        t0 = time.time()
        srv.settimeout(connect_timeout)
        try:
            self.conn, _ = srv.accept()
        except socket.timeout:
            self._runsh.kill()
            out = self._runsh.stdout.read() if self._runsh.stdout else ""
            raise BridgeError(
                f"BizHawk never connected to the bridge in {connect_timeout}s "
                f"(port {self.port}). run.sh said:\n{out}\n"
                f"emulator log: {self.log_path}")
        finally:
            srv.close()
        self.connect_seconds = time.time() - t0
        self.conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.conn.settimeout(300)
        # And now the delta. Taken after the bridge has answered -- the window is
        # demonstrably up, because it is answering -- but NOT necessarily after
        # `mono` appears in the process table. `EmuHawkMono.sh` execs a wrapper
        # which execs mono, and there is a measurable gap: MEASURED, the bridge
        # connected at 2.8s with the new pid absent, and it was present moments
        # later. So the delta is POLLED rather than sampled once.
        #
        # Sampling once is what a guard should not do here. It reported "diverted"
        # for a session that was demonstrably its own, and the run stopped with a
        # message about emulator plumbing instead of doing any work.
        self._pids = self._own_pids()
        self.diverted = not self._pids
        # Deliberately NOT makefile()/readline(). Two reasons, one of which cost
        # a run: the accepted socket inherits its timeout behaviour from a
        # listening socket that HAD one, and a buffered reader over it does not
        # surface a partial line the way a plain recv loop does. A recv loop also
        # keeps the "read until a line that starts `ok `/`err `" rule in one
        # place, which is the only reason multi-line replies can be trusted at
        # all.
        self._buf = b""
        self._recent: deque[tuple[str, str]] = deque(maxlen=10)
        self.inputs: list[tuple[str, ...]] = []
        self.events: list[tuple[int, str]] = []
        self.frame = 0
        self._closed = False

        # WHICH EMUHAWK IS OURS. `_pids` was already computed above, immediately
        # after the bridge answered. It is NOT re-initialised here, and that line
        # used to be here:
        #
        #     self._pids: list[int] = []
        #
        # which silently discarded the delta thirty lines after it was taken. So
        # `_pids` was ALWAYS empty, `diverted` was always True, and
        # `Run.start`'s "this launch added no new EmuHawk process" guard fired on
        # every run -- including the one that had just created the window it was
        # complaining about. MEASURED: the delta recomputed by hand from the same
        # object was `[1239461]` while the attribute read `[]`.
        #
        # Two separate mistakes with one symptom, which is why the guard needed
        # checking rather than believing: the snapshot was taken after the launch,
        # and then the result was overwritten. Either alone would have been enough
        # to make the guard permanently on.
        assert self.cmd("ping") == "pong", "the bridge answered ping with something else"
        self.domains = self._read_domains()
        missing = [d for d in REQUIRED_DOMAINS if d not in {x.name for x in self.domains}]
        if missing:
            self.close()
            raise BridgeError(f"the core is missing required memory domains: {missing}")
        self.fast()

    # ------------------------------------------------------------- low level
    def _readline(self) -> bytes:
        """One line from the bridge, or b"" at end of stream.

        A recv loop rather than makefile().readline(): a buffered reader over a
        socket that inherited a timeout from the listening socket does not
        reliably surface a partial line, and this protocol has partial lines
        (a 4 KiB RAM hex payload is one) and multi-line replies.
        """
        while b"\n" not in self._buf:
            chunk = self.conn.recv(65536)
            if not chunk:
                tail, self._buf = self._buf, b""
                return tail
            self._buf += chunk
        i = self._buf.index(b"\n")
        line, self._buf = self._buf[:i], self._buf[i + 1:]
        return line

    def cmd(self, line: str) -> str:
        """Send one command; return the whole reply.

        A reply is a run of body lines terminated by exactly one line starting
        `ok`, `err` or `pong`. Reading only the FIRST line is a bug this project
        has already paid for elsewhere: a truncated read leaves the rest of the
        reply in the socket, where it is read as the answer to the next command,
        and the symptom is a `dom 1 8192 WRAM` where an `ok` was expected.

        The terminator set includes `pong` because that is what `ping` answers.
        Leaving it out is not a subtlety: the loop then blocks in recv() forever
        on a reply that already arrived, which looks exactly like a bridge that
        never responded.
        """
        if self._closed:
            raise BridgeError("the bridge is closed")
        self._cmdlog.write(line + "\n")
        self._cmdlog.flush()
        self.conn.sendall((line + "\n").encode())
        body: list[str] = []
        resp = None
        for _ in range(256):
            raw = self._readline()
            if not raw:
                raise BridgeError(
                    f"the bridge connection died while sending {line!r}; "
                    f"see {self.bridge_log} and {self.log_path}")
            s = raw.decode("utf-8", "replace").rstrip("\n")
            if s.startswith(("ok", "err", "pong")):
                resp = s
                break
            body.append(s)
        if resp is None:
            raise BridgeError(
                f"the bridge sent {len(body)} body lines and no verdict for "
                f"{line!r}; last body line {body[-1][:80]!r}")
        self._recent.append((line, resp))
        if resp.startswith("err "):
            raise BridgeError(f"the bridge refused {line!r}: {resp[4:]}")
        if not self._answers(line, resp):
            before = " | ".join(f"{c!r}->{r[:32]!r}"
                                for c, r in list(self._recent)[:-1])
            raise BridgeError(
                f"the bridge's answer to {line!r} is not an answer to it: {resp[:120]!r}"
                + (f"  (before: {before})" if before else ""))
        return "\n".join(body + [resp])

    @staticmethod
    def _answers(cmd: str, resp: str) -> bool:
        """Does this reply answer this command? Coarse, and only in the strict direction.

        A check that is wrong in the strict direction costs a whole emulator, so
        only shapes that are fixed are rejected. The two that matter here are the
        hex replies (`ram`, `dom`) and the `ok k=v` replies (`step`, `stepu`,
        `frame`, `snapshot`).
        """
        head = cmd.split(" ", 1)[0]
        if head == "dom":
            # `ok dom=<NAME> bytes=<n> <hex>` and two of the nine real names
            # contain spaces, so the name cannot be token-matched.
            m = re.match(r"^ok dom=(.+) bytes=(\d+) ([0-9a-f]+)$", resp)
            return bool(m) and len(m.group(3)) == int(m.group(2)) * 2
        if head in ("ram",):
            return _hex_ok(resp)
        if head in ("step", "stepu", "frame", "domains", "reset"):
            return _is_int_line(resp)
        if head == "load":
            # `ok frame=<n>`, and the field is REQUIRED. `_is_int_line`
            # accepts a bare "ok", which for `load` is a bridge that did not
            # say where it put the machine -- and `load_state` would then
            # KeyError on it. The integer group above has the same looseness
            # for `step`, where a bare "ok" is harmless because the frame
            # comes back from `frame` afterwards; here it is the whole answer.
            return bool(re.match(r"^ok frame=\d+$", resp))
        if head == "save":
            # `ok state=<n> <path>` -- the path is echoed back and is not a
            # number, so _is_int_line rejects a perfectly good reply. This is
            # the third time this file has been bitten by the same shape (after
            # `snapshot` and `screenshot`), and the third time the reply was
            # RIGHT and the checker was wrong. `save` was listed with the
            # integer replies from the day the bridge grew it and nothing called
            # it for weeks, so nothing noticed.
            return bool(re.match(r"^ok state=\d+ \S+$", resp))
        if head == "snapshot":
            # `ok files=<n> frame=<n> manifest=<path>` -- the path is not a
            # number, so _is_int_line rejects a perfectly good reply.
            return bool(re.match(
                r"^ok files=\d+ frame=\d+ manifest=\S+$", resp))
        if head == "screenshot":
            return bool(re.match(r"^ok png=\d+ \S+$", resp))
        return resp == "ok" or resp == "pong"

    def _read_domains(self) -> list[Domain]:
        out: list[Domain] = []
        for line in self.cmd("domains").split("\n"):
            # `dom <i> <size> <name>` -- and two of the nine real names contain
            # spaces AND parentheses ("CIRAM (nametables)"), so this cannot be
            # parsed by splitting on whitespace. Getting that wrong yields four
            # domains instead of nine and a self-inconsistent count.
            m = re.match(r"^dom (\d+) (\d+) (.+)$", line)
            if m:
                out.append(Domain(int(m.group(1)), m.group(3), int(m.group(2))))
                continue
            t = line.split()
            if t and t[0] == "ok" and t[1].startswith("domains="):
                if len(out) != int(t[1].split("=")[1]):
                    raise BridgeError(
                        f"the bridge reported {t[1]} but {len(out)} domain lines "
                        "arrived; at least one name did not parse")
        if not out:
            raise BridgeError("the core reported no memory domains")
        return out

    # --------------------------------------------------------------- control
    def fast(self) -> None:
        self.cmd("fast")

    def normal(self) -> None:
        self.cmd("normal")

    def reset(self) -> None:
        """Power-cycle the core. The input log is only valid again after this."""
        self.cmd("reset")
        self.inputs.clear()
        self.input_log_valid = True
        self.frame = self.read_frame()

    input_log_valid = True

    def read_frame(self) -> int:
        return int(_kv(self.cmd("frame"))["frame"])

    # ------------------------------------------------------------------- RAM
    def ram(self, addr: int, length: int) -> bytes:
        if addr < 0 or length < 1 or addr + length > 0x10000:
            raise ValueError(f"ram({addr:#x}, {length}) is outside the CPU bus")
        parts = self.cmd(f"ram {addr} {length}").split(" ")
        # `parts.index("bytes=")` cannot work: the reply says `bytes=2048`, not
        # `bytes=`, so the index lookup raises rather than finding the field.
        n = int(dict(p.split("=", 1) for p in parts[1:-1])["bytes"])
        if n != length:
            raise BridgeError(f"asked for {length} bytes at ${addr:04X}, got {n}")
        raw = bytes.fromhex(parts[-1])
        if len(raw) != length:
            raise BridgeError(f"hex payload for ${addr:04X} is {len(raw)} bytes, not {length}")
        return raw

    def byte(self, addr: int) -> int:
        return self.ram(addr, 1)[0]

    def work_ram(self) -> bytes:
        from play import ram
        return self.ram(*ram.WORK_RAM)

    def fingerprint(self) -> str:
        """SHA1 over $0000-$07FF. Two runs of the same inputs must agree.

        This is the whole proof mechanism: a milestone is only reported complete
        if a FRESH emulator, replayed from power-on with the recorded inputs,
        produces this same digest.
        """
        return hashlib.sha1(self.work_ram()).hexdigest()

    def domain_read(self, name: str, addr: int, length: int) -> bytes:
        idx = next((d.index for d in self.domains if d.name == name), None)
        if idx is None:
            raise KeyError(f"no memory domain named {name!r}; the core has "
                           + ", ".join(d.name for d in self.domains))
        # NOT split(" "): two of the nine real domain names contain spaces, so
        # `parts[1]` is "dom=CIRAM" and the comparison below fails on a correct
        # reply. The same greedy-then-exact pattern `_answers` uses.
        m = re.match(rf"^ok dom={re.escape(name)} bytes=(\d+) ([0-9a-f]+)$",
                     self.cmd(f"dom {idx} {addr} {length}"))
        if not m:
            raise BridgeError(
                f"asked for domain {name!r}, but the reply did not name it: "
                f"{self._recent[-1][1][:120]!r}")
        n = int(m.group(1))
        raw = bytes.fromhex(m.group(2))
        if n != length or len(raw) != length:
            raise BridgeError(f"domain {name} returned {n}/{len(raw)} bytes, wanted {length}")
        return raw

    # ----------------------------------------------------------------- input
    @staticmethod
    def _norm(buttons: Iterable[str] | str) -> tuple[str, ...]:
        if isinstance(buttons, str):
            buttons = [b for b in buttons.split(",") if b]
        out = []
        for b in buttons:
            if b not in ALL_BUTTONS:
                raise ValueError(f"unknown button {b!r}; the bridge knows {ALL_BUTTONS}")
            out.append(b)
        return tuple(out)

    def step(self, buttons: Iterable[str] | str = (), frames: int = 1) -> int:
        btn = self._norm(buttons)
        self.inputs.extend([btn] * frames)
        r = _kv(self.cmd(f"step {frames} {','.join(btn) or '-'}"))
        self.frame = int(r["frame"])
        return self.frame

    def step_until(self, preds: Sequence, buttons: Iterable[str] | str = (),
                   budget: int = 600, what: str = "condition",
                   pulse: int = 0) -> tuple[int, bool]:
        """Hold -- or PULSE -- `buttons` until every predicate in `preds` is true.

        `pulse > 0` alternates `pulse` frames pressed with `pulse` frames
        released, and this is not a refinement: the game's input is EDGE
        triggered. `waitbut` (x0.pds:745-752) tests `lda dsel / bne` /
        `lda dsta / beq waitbut`, and those are the debounced edge bytes, which
        are $FF only on the single frame the value CHANGES (DISP.SRC:325-333).
        A button held down for four hundred frames produces exactly ONE edge, at
        the first frame -- so if the game is not yet listening, that edge is gone
        and nothing else happens for the rest of the run. That is exactly how the
        first attempt at `new_game` failed: the title was asserted ready three
        frames after power-on, before the fade-in delay had expired, and the
        press it made landed on a screen that was not listening yet.

        Returns (frames_used, held). The caller MUST assert on `held`.
        """
        if not pulse:
            btn = self._norm(buttons)
            predstr = " ".join(p.encode() for p in preds)
            r = _kv(self.cmd(f"stepu {budget} {','.join(btn) or '-'} {predstr}"))
            used, hit = int(r["used"]), r["hit"] == "1"
            self.inputs.extend([btn] * used)
            self.frame = int(r["frame"])
            return used, hit
        return self._pulse_until(preds, buttons, budget, what, pulse)

    def _pulse_until(self, preds, buttons, budget: int, what: str, pulse: int):
        # `pulse < 1` would mean `min(pulse, budget - used) == 0`, the loop would
        # never advance `used`, and this would spin forever. The real
        # step_until() never gets here with pulse == 0 -- that goes to the bridge
        # -- but src/testing/test_play_actions.py's FakeEmu routes everything
        # through this function so that the pulsing logic under test IS this
        # logic, and it found the hang.
        pulse = max(1, pulse)
        img = self.work_ram()
        # Check BEFORE the first step, exactly as the bridge does. The pulsing
        # path used to step first and check afterwards, so a predicate that was
        # already true cost one frame and pressed one button -- and the two paths
        # disagreed about the one property a caller would assume they share.
        if all(p.holds(img) for p in preds):
            return 0, True
        used = 0
        pressing = True
        while used < budget:
            n = min(pulse, budget - used)
            self.step(buttons if pressing else (), n)
            used += n
            img = self.work_ram()
            if all(p.holds(img) for p in preds):
                return used, True
            pressing = not pressing
        return used, False

    def tap(self, *buttons: str, hold: int = 3, release: int = 3) -> int:
        self.step(buttons, hold)
        return self.step((), release)

    # ----------------------------------------------------------------- notes
    def screenshot(self, name: str) -> pathlib.Path:
        """The core's own video buffer, as a PNG.

        `client.screenshot` returns nothing and writes lazily, so a zero-byte
        file is a screenshot that was never taken. The bridge re-stats the file
        and refuses to answer `ok` for anything under 9 bytes.
        """
        SHOTS.mkdir(parents=True, exist_ok=True)
        p = SHOTS / f"{name}.png"
        r = self.cmd(f"screenshot {p}")
        n = int(_kv(r)["png"])
        if not p.exists() or p.stat().st_size != n:
            raise BridgeError(
                f"screenshot {name}: the bridge says {n} bytes, the file says "
                f"{p.stat().st_size if p.exists() else 'missing'}")
        return p

    def note(self, text: str) -> None:
        self.events.append((len(self.inputs) + 1, text))

    def save_inputs(self, name: str) -> pathlib.Path:
        INPUTS.mkdir(parents=True, exist_ok=True)
        p = INPUTS / f"{name}.inputs.txt"
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# frames={len(self.inputs)} "
                    f"valid_from_poweron={self.input_log_valid} "
                    f"rom={self.rom.name}\n")
            for b in self.inputs:
                # "-" for "nothing pressed", NOT an empty line.
                #
                # An empty line is what `",".join(())` produces, and the loader
                # skips blank lines -- so a run that spent 244 of its 636 frames
                # with nothing pressed recorded 392 frames and replayed 392. The
                # proof then compared the replay's fingerprint against a run it
                # had not been given the inputs for, and reported MISMATCH. The
                # replay had 392 frames because the log was short, and nothing
                # said so: `len(emu.inputs)` said 636 and the file said 637
                # lines, which look like the same fact.
                f.write((",".join(b) or "-") + "\n")
        with open(INPUTS / f"{name}.events.txt", "w", encoding="utf-8") as f:
            for frame, text in self.events:
                f.write(f"{frame}\t{text}\n")
        return p

    @staticmethod
    def load_inputs(path: pathlib.Path) -> list[tuple[str, ...]]:
        """Read a recorded input log. Asserts the frame count in its header.

        A frame that pressed nothing is written `-` and reads back as an empty
        tuple, so the frame count is preserved exactly -- which it has to be,
        because a replay that is 244 frames short is not a replay.
        """
        frames = []
        declared = None
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                m = re.search(r"frames=(\d+)", line)
                if m:
                    declared = int(m.group(1))
                continue
            if not line.strip():
                raise BridgeError(
                    f"{path.name} has a BLANK line. A blank line used to mean "
                    "'nothing pressed' and was skipped, which silently shortened "
                    "every replay; '-' is the encoding now and a blank line is a "
                    "corrupt log")
            if line.strip() == "-":
                frames.append(())
                continue
            frames.append(tuple(b for b in line.split(",") if b))
        if declared is not None and declared != len(frames):
            raise BridgeError(
                f"{path.name} says frames={declared} in its header but holds "
                f"{len(frames)} frames. A log that does not match its own header "
                "is a log that will replay into a different game.")
        return frames

    def run_inputs(self, frames: Sequence[tuple[str, ...]]) -> None:
        """Feed a recorded log, batching runs of identical input.

        Batching is not an optimisation that changes behaviour: `step n` holds
        the same buttons for all n frames and logs n identical entries, exactly
        as n single-frame steps would.
        """
        i = 0
        while i < len(frames):
            j = i
            while j < len(frames) and frames[j] == frames[i]:
                j += 1
            self.step(frames[i], j - i)
            i = j

    # ------------------------------------------------------------- snapshots
    def snapshot_dir(self, name: str) -> pathlib.Path:
        if not self.route:
            raise ValueError(
                "snapshot() needs a route name: checkpoints record which segment "
                "list produced them, and a checkpoint with no route is exactly "
                "the thing that guard exists to refuse")
        d = CHECKPOINTS / self.route / self.run / name
        if d.exists():
            raise FileExistsError(
                f"{d} already exists. A checkpoint is written once, not "
                "overwritten: two different states under one name is a claim "
                "nothing can check. Use a new name.")
        return d

    def snapshot(self, name: str) -> pathlib.Path:
        """See snapshot_dir; `name` must also survive the wire."""
        if not name or any(c.isspace() for c in name):
            raise ValueError(
                f"snapshot name {name!r} contains whitespace. The bridge's "
                "`snapshot` command takes a single whitespace-free token, so a "
                "name built from a ROM filename ('Magician (USA)') is refused "
                "here rather than becoming a command the bridge rejects with a "
                "'snapshot needs <dir>' that says nothing about the cause.")
        """All nine domains, the framebuffer, the registers and the frame count.

        Also records `segments.txt`: the SHA1 of this route's segment list. A
        checkpoint produced by one route therefore cannot be loaded by another,
        which is what stops a run from quietly continuing through a state it
        never earned.
        """
        d = self.snapshot_dir(name)
        d.mkdir(parents=True)
        r = self.cmd(f"snapshot {d}")
        written = int(_kv(r)["files"])
        # The bridge already verified every file against the size the core
        # reported for its domain. Verify it again here, from this side, because
        # the point of the exercise is not to trust one report.
        #
        # One domain is legitimately short and the shortfall must be EVIDENCE,
        # not a tolerance: this core's "CPU registers" domain is 12 bytes of
        # which only the first 8 are byte-addressable, and the other four are
        # status bits that exist only under their names. A domain is accepted
        # short only if the manifest says which offsets are missing AND regs.txt
        # carries a value for every named register. An unaccounted short file is
        # a failure, because "the size was a bit off" is how a snapshot quietly
        # stops being a snapshot.
        manifest = (d / "manifest.txt").read_text(encoding="utf-8")
        shorts: dict[str, str] = {}
        for line in manifest.splitlines():
            m = re.match(r"^domain \d+ (\S+.*?)\s+(\d+) of (\d+) -- offsets (.*)$", line)
            if m:
                shorts[m.group(1).strip()] = m.group(4)
        regs = (d / "regs.txt").read_text(encoding="utf-8")
        for rname in ("PC", "A", "X", "Y", "SP", "P", "C", "Z", "I"):
            if not re.search(rf"^{rname}\s+[0-9A-F]{{2}}$", regs, re.M):
                raise BridgeError(
                    f"snapshot {name}: regs.txt has no hex value for {rname}; the "
                    "CPU register domain is short by exactly the offsets whose "
                    "values only exist under their names")
        for dom in self.domains:
            f = d / f"{dom.index:02d}_{_safe(dom.name)}.bin"
            if not f.exists():
                raise BridgeError(f"snapshot {name}: {dom.name} produced no file")
            got = f.stat().st_size
            if got == dom.size:
                continue
            if dom.name not in shorts:
                raise BridgeError(
                    f"snapshot {name}: {f.name} is {got} bytes, the core says "
                    f"{dom.name} is {dom.size}, and the manifest does not record "
                    "why it is short")
            (d / "SHORT.txt").write_text(
                f"{dom.name}: {got} of {dom.size} bytes; offsets "
                f"{shorts[dom.name]} are not byte-addressable in this API. "
                f"Their values are in regs.txt.\n", encoding="utf-8")
        digests = {}
        for f in sorted(d.glob("*")):
            if f.is_file():
                digests[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        (d / "sha256.txt").write_text(
            "".join(f"{v}  {k}\n" for k, v in sorted(digests.items())), encoding="utf-8")
        (d / "segments.txt").write_text(
            f"route={self.route}\nrun={self.run}\n"
            f"segment_list_sha1={self.segment_digest()}\n"
            f"frame={self.frame}\ninputs_prefix_sha1={self.input_prefix_digest()}\n",
            encoding="utf-8")
        self.note(f"snapshot {name} at frame {self.frame}")
        return d

    def segment_digest(self) -> str:
        """SHA1 of this route's segment list, or '-' when there is no route module.

        Imported lazily and by absolute name: entry-point scripts run with
        `src/play` on sys.path, not `src`, so a relative import would only work
        when they happen to be run as `-m`.
        """
        try:
            import play.route as _route
            return _route.active().digest()
        except Exception:
            return "-"

    def input_prefix_digest(self) -> str:
        h = hashlib.sha256()
        for b in self.inputs:
            h.update((",".join(b) + "\n").encode())
        return h.hexdigest()[:16]

    def load_checkpoint(self, route: str, name: str) -> pathlib.Path:
        """Refuse a checkpoint that a different segment list produced."""
        if self.run:
            d = CHECKPOINTS / route / self.run / name
        else:
            d = CHECKPOINTS / route / name
        meta = d / "segments.txt"
        if not meta.exists():
            raise FileNotFoundError(f"{d} has no segments.txt; it is not a checkpoint")
        got = dict(
            line.split("=", 1) for line in meta.read_text().splitlines() if "=" in line)
        want = self.segment_digest()
        if got.get("segment_list_sha1") != want:
            raise BridgeError(
                f"checkpoint {route}/{name} was produced by segment list "
                f"{got.get('segment_list_sha1')} and this run's is {want}. "
                "Loading it would put state into a run that never reached it.")
        return d

    # ------------------------------------------------------- savestates
    # A scout needs to start every attempt from the SAME bytes, and BizHawk
    # allows exactly one emulator per session, so "another copy of MAIN" is a
    # savestate on disk rather than a second window. These two are the only
    # places a state is moved, and they deliberately do NOT touch `self.inputs`:
    # loading a state is not rewinding the log, it is a separate question, and
    # conflating them is how a scout's frames end up in MAIN's log.
    def state_path(self, name: str) -> pathlib.Path:
        return CHECKPOINTS / f"_states" / f"{self.route or 'noroute'}" / \
            f"{self.run or 'run'}" / f"{name}.state"

    def save_state(self, name: str) -> pathlib.Path:
        """Write a savestate. Refuses to overwrite an existing name."""
        if not name or any(c.isspace() for c in name):
            raise ValueError(f"state name {name!r} contains whitespace; the "
                             "bridge's `save` command takes one token")
        p = self.state_path(name)
        if p.exists():
            raise FileExistsError(
                f"{p} already exists. A savestate is written once: two different "
                "states under one name is a claim nothing can check.")
        p.parent.mkdir(parents=True, exist_ok=True)
        r = _kv(self.cmd(f"save {p}"))
        n = int(r["state"])
        if n <= 0 or not p.exists() or p.stat().st_size != n:
            raise BridgeError(
                f"save_state({name}): the bridge says {n} bytes, the file says "
                f"{p.stat().st_size if p.exists() else 'missing'}")
        self.note(f"savestate {name} at frame {self.frame}")
        return p

    def load_state(self, name: str) -> int:
        """Load a savestate written by `save_state`. Returns the frame count.

        The input log is left exactly as it is. `input_log_valid` is untouched
        too: a state produced by a prefix of the log is still consistent with
        that prefix, and the replay-from-power-on proof in `Run.finish()` is what
        establishes that the log as a whole reproduces the run.
        """
        p = self.state_path(name)
        if not p.exists():
            raise FileNotFoundError(
                f"no savestate {p}. A scout that cannot find its start state is "
                "not a scout that is searching the wrong thing -- it is a scout "
                "that is searching nothing.")
        self.frame = int(_kv(self.cmd(f"load {p}"))["frame"])
        return self.frame

    # -------------------------------------------------------------- shutdown
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.cmd("quit")
        except Exception:
            pass
        for closer in (self.conn.close,):
            try:
                closer()
            except Exception:
                pass
        try:
            self._runsh.wait(timeout=20)
        except Exception:
            self._runsh.kill()
        # And then wait for the emulator ITSELF, by PID. run.sh backgrounds
        # EmuHawk with setsid and returns long before the window closes, so
        # without this the next BizHawk() is diverted into this one and measures
        # a session that is shutting down.
        deadline = time.time() + 45
        while time.time() < deadline:
            if not [p for p in self._pids if p in self.running_emuhawk()]:
                self._pids = []
                break
            time.sleep(0.5)
        else:
            still = [p for p in self._pids if p in self.running_emuhawk()]
            if still:
                raise BridgeError(
                    f"EmuHawk {still} is still running {45}s after `quit`. The "
                    "next BizHawk() would be diverted into it by the "
                    "single-instance pipe and would measure the old session. "
                    "Kill those PIDs by hand -- with the PID, never with "
                    "`pkill -f EmuHawk`, which matches its own command line.")
        self._cmdlog.close()

    def __enter__(self) -> "BizHawk":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _own_pids(self, timeout: float = 15.0) -> list[int]:
        """EmuHawk PIDs that appeared since before this launch, polled for a while.

        Polling because `mono` is not in the process table the instant the bridge
        answers -- `EmuHawkMono.sh` execs a wrapper which execs mono, and the gap
        is seconds, not milliseconds (MEASURED: 2.8s to connect, the new pid
        absent at that instant and present shortly after).

        The wait is bounded and it is the right way round: a genuinely diverted
        launch costs this timeout, and it costs it BEFORE any measurement rather
        than after, so the failure is a refusal to start rather than a number
        somebody acts on.
        """
        deadline = time.time() + timeout
        while True:
            new = sorted(set(self.running_emuhawk()) - self._before_pids)
            if new or time.time() >= deadline:
                return new
            time.sleep(0.25)

    @staticmethod
    def running_emuhawk() -> list[int]:
        """PIDs of running EmuHawk processes, by comm name.

        Never `pkill -f EmuHawk`: that pattern matches the pkill process's own
        command line, so it kills the shell running it. This reads `comm`
        instead, which is the executable name and cannot match the matcher.
        """
        out = subprocess.run(["ps", "-eo", "pid,comm"], capture_output=True,
                             text=True, check=True).stdout
        pids = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) == 2 and parts[1] == "mono":
                pids.append(int(parts[0]))
        return pids


def _safe(name: str) -> str:
    """Filename-safe form of a domain name -- MUST match bridge.lua's `safe()`.

    Runs of non-alphanumerics collapse to ONE underscore, because that is what
    Lua's `gsub("[^A-Za-z0-9]+", "_")` does. A per-character replacement looks
    equivalent and is not: "CIRAM (nametables)" becomes `CIRAM__nametables_` here
    and `CIRAM__nametables_` there only by accident of this particular name, and
    the first name where it is not, the snapshot lookup silently finds no file.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", name)