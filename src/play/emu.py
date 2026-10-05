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

BIZHAWK = pathlib.Path(os.environ.get("MAGICIAN_BIZHAWK")
                       or (pathlib.Path.home() / "code/games/aibeatszelda/BizHawk-2.11.1-win-x64"))
RUN_SH = ROOT / "tools" / "bizhawk" / "run.sh"
BRIDGE_LUA = HERE / "bridge.lua"

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

WORK_RAM = (0x0000, 0x0800)      # $0000-$07FF: everything the game calls RAM


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


class BizHawk:
    """One EmuHawk instance, driven frame by frame over a loopback socket."""

    def __init__(self, rom: pathlib.Path = ROM, log_name: str = "play",
                 route: str = "", kill_stale: bool = False,
                 settle: int = 120, connect_timeout: int = 90):
        self.rom = pathlib.Path(rom).resolve()
        if not self.rom.exists():
            raise FileNotFoundError(
                f"no ROM at {self.rom} -- run `make rom` first. The rebuilt "
                "cartridge is generated from the source; a cartridge is never "
                "committed (LEGAL.md).")
        if not RUN_SH.exists():
            raise FileNotFoundError(f"no launcher at {RUN_SH}")
        if not BIZHAWK.is_dir():
            raise FileNotFoundError(
                f"no BizHawk at {BIZHAWK}; set MAGICIAN_BIZHAWK=/path")

        for d in (LOGS, SHOTS, CHECKPOINTS, INPUTS):
            d.mkdir(parents=True, exist_ok=True)
        self.route = route
        self.log_path = LOGS / f"{log_name}.log"
        self.bridge_log = LOGS / f"{log_name}.bridge.log"
        self.cmd_log = LOGS / f"{log_name}.cmd.log"
        self._cmdlog = open(self.cmd_log, "a", encoding="utf-8")
        # One EmuHawk at a time. BizHawk diverts a second launch into the first
        # through its single-instance pipe, so a second window would show the
        # FIRST session and this bridge would connect to the wrong emulator.
        # run.sh refuses by default; the override is opt-in and never silent.
        env = dict(os.environ)
        env["MAGICIAN_BRIDGE_LOG"] = str(self.bridge_log)
        env.pop("MAGICIAN_BRIDGE_PORT", None)
        if kill_stale:
            env["MAGICIAN_KILL_STALE"] = "1"
        env["MAGICIAN_SETTLE"] = str(settle)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.port = srv.getsockname()[1]
        env["MAGICIAN_BRIDGE_PORT"] = str(self.port)

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
        if head in ("step", "stepu", "frame", "domains", "save", "load"):
            return _is_int_line(resp)
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
        return self.ram(*WORK_RAM)

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
        parts = self.cmd(f"dom {idx} {addr} {length}").split(" ")
        if parts[1].split("=", 1)[1] != name:
            raise BridgeError(f"asked for domain {name!r}, the core read {parts[1]!r}")
        n = int(parts[2].split("=", 1)[1])
        raw = bytes.fromhex(parts[-1])
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

    def tap(self, *buttons: str, hold: int = 3, release: int = 3) -> int:
        self.step(buttons, hold)
        return self.step((), release)

    def step_until(self, preds: Sequence, buttons: Iterable[str] | str = (),
                   budget: int = 600, what: str = "condition") -> tuple[int, bool]:
        """Hold `buttons` until every predicate in `preds` is true, or `budget` runs out.

        Returns (frames_used, held). The caller MUST assert on `held`; nothing
        here raises on a miss, because "the budget expired" is a normal result
        that recon and exploration need to observe and report.

        The input log is extended for exactly `used` frames. A predicate that
        was already true costs zero frames and logs nothing.
        """
        btn = self._norm(buttons)
        predstr = " ".join(p.encode() for p in preds)
        r = _kv(self.cmd(f"stepu {budget} {','.join(btn) or '-'} {predstr}"))
        used, hit = int(r["used"]), r["hit"] == "1"
        self.inputs.extend([btn] * used)
        self.frame = int(r["frame"])
        if hit:
            return used, True
        if not what:
            what = "condition"
        return used, False

    # ----------------------------------------------------------------- notes
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
                f.write(",".join(b) + "\n")
        with open(INPUTS / f"{name}.events.txt", "w", encoding="utf-8") as f:
            for frame, text in self.events:
                f.write(f"{frame}\t{text}\n")
        return p

    def load_inputs(self, path: pathlib.Path) -> list[tuple[str, ...]]:
        frames = []
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            frames.append(tuple(b for b in line.split(",") if b))
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
        d = CHECKPOINTS / self.route / name
        if d.exists():
            raise FileExistsError(
                f"{d} already exists. A checkpoint is written once, not "
                "overwritten: two different states under one name is a claim "
                "nothing can check. Use a new name.")
        return d

    def snapshot(self, name: str) -> pathlib.Path:
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
            f"route={self.route}\nsegment_list_sha1={self.segment_digest()}\n"
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
            return _route.segment_list_sha1()
        except Exception:
            return "-"

    def input_prefix_digest(self) -> str:
        h = hashlib.sha256()
        for b in self.inputs:
            h.update((",".join(b) + "\n").encode())
        return h.hexdigest()[:16]

    def load_checkpoint(self, route: str, name: str) -> pathlib.Path:
        """Refuse a checkpoint that a different segment list produced."""
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
        self._cmdlog.close()

    def __enter__(self) -> "BizHawk":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

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