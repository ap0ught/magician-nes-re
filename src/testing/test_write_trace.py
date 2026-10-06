#!/usr/bin/env python3
"""Tests for the RAM write trace, the input-log replay, and the address mapping.

    python3 src/testing/test_write_trace.py

WHY THESE EXIST
---------------
"Who writes $004D" (journal 13, item 1; journal 14, item 1) is answered by an
instrument, and this project has twelve instruments that reported plausible
wrong answers without error. So the instrument gets tests that check the things
that make a wrong answer look right:

  * a linear sweep that finds nothing must say it looked, not just print nothing;
  * a CPU address below the fixed window must be REFUSED, because extending
    `cpu + $10000` there reads a different 8 KiB slot and labels it with this
    address -- and that is exactly how journal 13 read $C300 one slot low,
    found $7A filler in both ROMs, and refuted the correct hypothesis;
  * the recorded input log's header must be checked against its body, because a
    replay of the wrong length still looks like a run;
  * an unknown button name must be refused rather than mapped to a bit.

The emulator-level tests build a tiny 6502 program and run it through the real
`Bus`/`CPU`, so they test the model rather than a restatement of it.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "asm"))

CHECKS = 0
FAILURES: list[str] = []


def check(cond: bool, what: str) -> None:
    """`  ok ...` on success, because `run_all.py` counts checks by that prefix.

    The first version of this file raised nothing and printed no `  ok ` lines,
    so `make check-py` reported `PASS test_write_trace.py 0.32s exit=0 0 checks`
    and read as a passing file that had asserted nothing. That is the failure
    mode this project has hit most often, so the runner's counter is the thing
    being satisfied here, not a formality: a test that runs and checks nothing
    is indistinguishable, in that output, from a test that was never written.
    """
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(what)
        print(f"  FAIL  {what}")
    else:
        print(f"  ok trace: {what}")


def section(name: str) -> None:
    print(f"\n{name}")


# --------------------------------------------------------------------- imports
section("import the modules under test")
import dis6502  # noqa: E402
import findstore  # noqa: E402
import nestrace  # noqa: E402

check(hasattr(nestrace, "load_input_log"), "nestrace has load_input_log")
check(isinstance(nestrace.Bus(b"", b"", 0, True).ram_writes, dict),
      "a Bus instance records ram_writes")


# ------------------------------------------------------------- the input log
section("load_input_log: format, and the two ways a log can be wrong")

with tempfile.TemporaryDirectory() as td:
    td = pathlib.Path(td)

    good = td / "good.txt"
    good.write_text("# frames=4 valid_from_poweron=True rom=x.nes\n"
                    "-\nA\nA,B\n-\n")
    masks, head = nestrace.load_input_log(good)
    check(masks == [0, 1, 3, 0], f"masks read in hardware bit order: {masks}")
    check(masks[1] == 1 << nestrace.HARDWARE_BITS["A"], "A is bit 0")
    check(masks[2] == (1 << nestrace.HARDWARE_BITS["A"]) | (1 << nestrace.HARDWARE_BITS["B"]),
          "A,B is both bits")
    check(head.get("frames") == "4", "header frames parsed")
    check(head.get("valid_from_poweron") == "True", "header valid_from_poweron parsed")

    # The bit order is the hardware's, not src/play's enumeration order. This is
    # a real distinction: src/play lists (Up,Down,Left,Right,Select,Start,B,A)
    # and BizHawk's button names are not a bit layout, so a replay that assumed
    # they were would press Select when the log said Down.
    for name, bit in nestrace.HARDWARE_BITS.items():
        check(bit == len([1 for b in nestrace.HARDWARE_BITS.values() if b < bit]),
              f"{name} bit {bit} is distinct and ordered")
    check([k for k, _ in sorted(nestrace.HARDWARE_BITS.items(),
                                key=lambda kv: kv[1])]
          == ["A", "B", "Select", "Start", "Up", "Down", "Left", "Right"],
          "hardware bit order is A,B,Select,Start,Up,Down,Left,Right")

    # Header says N frames, body holds M. Replaying the body is the wrong length
    # and still produces output, so this must be a hard error.
    short = td / "short.txt"
    short.write_text("# frames=99 valid_from_poweron=True\n-\n-\n")
    try:
        nestrace.load_input_log(short)
        check(False, "header/body frame mismatch must raise")
    except SystemExit as e:
        check("frames=99" in str(e) and "2" in str(e),
              f"mismatch names both counts: {e}")

    # A blank line is what the writer used to emit for an idle frame, and the
    # reader used to skip -- so it silently replayed fewer frames than it
    # claimed. It must be refused rather than counted as an idle frame.
    blank = td / "blank.txt"
    blank.write_text("# frames=3 valid_from_poweron=True\n-\n\n-\n")
    try:
        nestrace.load_input_log(blank)
        check(False, "blank line must raise")
    except SystemExit as e:
        check("blank line" in str(e), f"blank line is named: {e}")

    unknown = td / "unknown.txt"
    unknown.write_text("# frames=1 valid_from_poweron=True\nC\n")
    try:
        nestrace.load_input_log(unknown)
        check(False, "unknown button must raise")
    except SystemExit as e:
        check("C" in str(e) and "Right" in str(e),
              f"unknown button lists what is known: {e}")

    # No header at all is allowed -- the loader does not invent one -- but the
    # body is still every frame.
    nohdr = td / "nohdr.txt"
    nohdr.write_text("-\nA\n")
    masks2, head2 = nestrace.load_input_log(nohdr)
    check(masks2 == [0, 1], f"headerless log reads its body: {masks2}")
    check(head2 == {}, "headerless log has no header fields")


# -------------------------------------------------------------- resolve()
section("resolve: the spelling the symbol table itself uses")

syms = {"mapind": 0x4D, "reinit|!a": 0x100, "reinit": 0x100}
check(nestrace.resolve("mapind", syms) == 0x4D, "a symbol resolves")
check(nestrace.resolve("$F5D4", syms) == 0xF5D4,
      "`$F5D4` resolves -- int(s, 0) rejects it, and mag.sym is written that way")
check(nestrace.resolve("0xF5D4", syms) == 0xF5D4, "`0xF5D4` resolves")
check(nestrace.resolve("62932", syms) == 62932, "a decimal address resolves")
check(nestrace.resolve("reinit|!a", syms) == 0x100, "a local label resolves")


# ------------------------------------------------- controller, through the CPU
section("controller: the eight-bit serial read, run on the real CPU")


def machine(code_at, code, setup=None):
    """A minimal NROM-256 machine: `code` at $8000, `code_at` as the reset PC.

    NROM-256 is a 32 KiB PRG presented whole at $8000-$FFFF, so `cpu & 0x7FFF`
    is the file offset and the reset vector lives at 0x7FFC. A 16 KiB image
    would have to be indexed $8000-relative, and writing the vector at 0xFFFC
    of a 0x8000-byte buffer is an IndexError -- which is the kind of thing that
    makes a test look like it is testing the emulator.
    """
    prg = bytearray(0x8000)
    prg[0:len(code)] = code
    prg[0xFFFC - 0x8000] = code_at & 0xFF
    prg[0xFFFD - 0x8000] = code_at >> 8
    if setup:
        setup(prg)
    bus = nestrace.Bus(bytes(prg), b"", 0, True)
    cpu = nestrace.CPU(bus)
    bus.cpu = cpu
    cpu.pc = bus.read(0xFFFC) | (bus.read(0xFFFD) << 8)
    return bus, cpu


# Read all eight bits the way `jk0` does: strobe high, strobe low, then eight
# reads of $4016, collecting `and #$03 / cmp #$01 / rol jt`.
# The loop the cartridge actually runs, assembled by hand:
#   lda #$01 / sta jt / sta $4016 / lda #$00 / sta $4016
#   ldy #8
# !a lda $4016 / and #$03 / cmp #$01 / rol jt / dey / bne !a
#   brk
READ8 = bytes([
    0xA9, 0x01,        # 00 lda #$01
    0x85, 0x20,        # 02 sta $20      (jt)
    0x8D, 0x16, 0x40,  # 04 sta $4016
    0xA9, 0x00,        # 07 lda #$00
    0x8D, 0x16, 0x40,  # 09 sta $4016
    0xA0, 0x08,        # 0C ldy #8
    0xA9, 0x00,        # 0E lda #$00    <- accumulator, holds the pad byte
    0xAD, 0x16, 0x40,  # 10 lda $4016
    0x29, 0x03,        # 13 and #$03
    0xC9, 0x01,        # 15 cmp #$01
    0x26, 0x20,        # 17 rol $20
    0x88,              # 19 dey
    0xD0, 0xF3,        # 1A bne (patched below)
    0x00,              # 1C brk
])
# Fix the branch target. `bne` at $1A with operand k jumps to $1C + k, and the
# `lda $4016` to repeat is at $10, so k = $10 - $1C = -$0C = $F4. Writing $F3
# lands on $0F -- the operand byte of the `lda #$00` at $0E -- which re-decodes
# as `brk`, and the first run of this file produced eight identical wrong
# answers for exactly that reason. The instruction addresses in the comment
# above are the check that catches it.
READ8 = bytearray(READ8)
READ8[0x1B] = 0xF4


def read_pad(bus, cpu, frames=1):
    for _ in range(400):
        try:
            cpu.step()
        except nestrace.Halt:
            break
    return bus.ram[0x20]


# `rol` shifts LEFT, so the carry captured by the FIRST read ends up in bit 7
# after eight rotations and the last read's in bit 0. That reversal is not a
# detail of the test -- it is why `joykey` follows `jk0` with an `asl a` loop
# over x = 7..0 (DISP.SRC:295-305), which undoes it so that `lr[0]` is the A
# button. Writing the expectation the other way round was the first run of this
# file producing eight wrong answers that all looked like a plausible bit
# reversal in the tracer.
ROLLED = lambda bit: 1 << (7 - bit)  # noqa: E731

for name, bit in nestrace.HARDWARE_BITS.items():
    bus, cpu = machine(0x8000, bytes(READ8))
    bus.buttons = 1 << bit
    got = read_pad(bus, cpu)
    check(got == ROLLED(bit),
          f"{name} alone is read as bit {bit} and lands at bit {7 - bit} after "
          f"eight `rol`s (got ${got:02X})")

bus, cpu = machine(0x8000, bytes(READ8))
bus.buttons = 0
check(read_pad(bus, cpu) == 0, "nothing pressed arrives as $00")

bus, cpu = machine(0x8000, bytes(READ8))
bus.buttons = 0xFF
check(read_pad(bus, cpu) == 0xFF, "all eight pressed arrive as $FF -- `rol` is a "
                                  "rotation, so all-ones survives it")

# The eight reads and then 1s: `jk0` terminates its loop on `bcc`, which needs a
# value >= 1 after eight reads. A pad that returns 0 forever hangs the game, so
# this is not a nicety.
bus, cpu = machine(0x8000, bytes([0xEA] * 64))
bus.buttons = 0
bus.write(0x4016, 1)
bus.write(0x4016, 0)
seq = [bus.read(0x4016) for _ in range(10)]
check(seq[:8] == [0] * 8, f"eight idle reads are all 0: {seq[:8]}")
check(seq[8:] == [1, 1], f"reads past the eighth are 1, which ends jk0: {seq[8:]}")


# ------------------------------------------------------ RAM write recording
section("RAM write trace: who wrote it, and which instruction")

# `sta $10` / `sta $11` / `inc $10` / `rts`, entered at $8000 with the caller
# pushing a return address of $8100 so `cur_pc` is meaningful.
TRACE = bytes([
    0xA9, 0xAA,        # 00 lda #$AA
    0x85, 0x10,        # 02 sta $10
    0xA9, 0xBB,        # 04 lda #$BB
    0x85, 0x11,        # 06 sta $11
    0xE6, 0x10,        # 08 inc $10
    0x60,              # 0A rts
])
bus, cpu = machine(0x8000, TRACE)
bus.ram_writes[0x10] = []
bus.ram_writes[0x11] = []
for _ in range(40):
    try:
        cpu.step()
    except nestrace.Halt:
        break
rows10 = bus.ram_writes[0x10]
rows11 = bus.ram_writes[0x11]
check(len(rows10) == 2, f"sta $10 and inc $10 are both recorded ({len(rows10)})")
check([r[3] for r in rows10] == [0xAA, 0xAB],
      f"the values are the ones written, inc included: {[hex(r[3]) for r in rows10]}")
check(rows10[0][2] == 0x8002, f"the store is attributed to its own PC (${rows10[0][2]:04X})")
check(rows10[1][2] == 0x8008, f"the read-modify-write is attributed to its PC (${rows10[1][2]:04X})")
check(len(rows11) == 1 and rows11[0][2] == 0x8006, "sta $11 attributed to $8006")
check(all(r[0] == 0 for r in rows10), "the frame is recorded")

# A cell nobody watches must record nothing, and the report must say so rather
# than printing nothing at all -- "no writers" and "did not look" are different.
bus2, cpu2 = machine(0x8000, TRACE)
bus2.ram_writes = {}
for _ in range(40):
    try:
        cpu2.step()
    except nestrace.Halt:
        break
check(bus2.ram_writes == {}, "an unwatched cell records nothing")


# ------------------------------------------------------------- slot mapping
section("slot mapping: the fixed window is $C000+, and below it is refused")

check(dis6502.file_of_cpu(0xC000) == 0x1C000,
      "CPU $C000 is PRG $1C000 (8 KiB slot 14)")
check(dis6502.file_of_cpu(0xC300) == 0x1C300,
      "CPU $C300 is PRG $1C300 -- the byte journal 13 read at $10300")
check(dis6502.file_of_cpu(0xFFFF) == 0x1FFFF, "CPU $FFFF is PRG $1FFFF")
check(dis6502.file_of_cpu(0x8550) is None,
      "CPU $8550 has no file offset: it is a switchable window")
check(dis6502.file_of_cpu(0xBFFF) is None, "CPU $BFFF has no file offset")
check(dis6502.slot_cpu_base(14) == 0xC000, "slot 14 is at $C000")
check(dis6502.slot_cpu_base(15) == 0xE000, "slot 15 is at $E000")
check(dis6502.slot_cpu_base(0) == 0x8000, "slot 0 is at $8000")
check(dis6502.slot_cpu_base(1) == 0xA000, "slot 1 is at $A000")
check(dis6502.slot_cpu_base(16) is None, "slot 16 does not exist")
check(dis6502.slot_of_file(0x1C300) == 14, "PRG $1C300 is slot 14")
check(dis6502.slot_of_file(0x10300) == 8,
      f"PRG $10300 is slot {dis6502.slot_of_file(0x10300)}, not slot 14 -- which is "
      f"the address journal 13 read $C300 at")

# The tool must REFUSE a CPU address below the fixed window, because extending
# `cpu + $10000` there reads a different slot and prints it with this address.
prg = ROOT / "asm" / "out" / "prg.bin"
if prg.exists():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "dis6502.py"),
                        "--prg", str(prg), "--from", "0x8550", "--length", "8"],
                       capture_output=True, text=True)
    check(r.returncode != 0, "dis6502 --from below $C000 fails")
    check("not in the fixed window" in (r.stdout + r.stderr),
          f"dis6502 says why: {(r.stdout + r.stderr).splitlines()[0][:70]}")
    # And the address it must accept.
    r2 = subprocess.run([sys.executable, str(ROOT / "tools" / "dis6502.py"),
                         "--prg", str(prg), "--from", "0xC300", "--length", "8"],
                        capture_output=True, text=True)
    check(r2.returncode == 0 and r2.stdout.startswith("C300:"),
          f"dis6502 --from 0xC300 disassembles: {r2.stdout.splitlines()[:1]}")
    # A range that mixes the fixed window with a switchable one is not a range.
    r3 = subprocess.run([sys.executable, str(ROOT / "tools" / "dis6502.py"),
                         "--prg", str(prg), "--range", "0x8550:0xC300"],
                        capture_output=True, text=True)
    check(r3.returncode != 0 and "mixes" in (r3.stdout + r3.stderr),
          "a range mixing windows is refused")
else:
    print("  (skipped: asm/out/prg.bin not built)")


# ---------------------------------------------------------- the six NOPs
section("the six one-byte NOPs: decoded by both tables, executed by the tracer")

for op in (0x1A, 0x3A, 0x5A, 0x7A, 0xDA, 0xFA):
    check(op in nestrace.UNOFFICIAL_LEN and nestrace.UNOFFICIAL_LEN[op] == 1,
          f"${op:02X} is a one-byte NOP to the tracer")
    check(op in dis6502.UNDOC and dis6502.UNDOC[op][0] == "nop",
          f"${op:02X} disassembles as nop")
    check(op not in dis6502.LITERAL,
          f"${op:02X} is not an official opcode (it would be a table conflict)")

bus, cpu = machine(0x8000, bytes([0x1A, 0x1A, 0x60, 0xEA, 0xEA, 0xEA]))
pc0 = cpu.pc
try:
    cpu.step()
    cpu.step()
except nestrace.Halt as h:
    check(False, f"two $1A must execute, not halt: {h}")
check(cpu.pc == pc0 + 2, f"two NOPs advance PC by 2 (got ${cpu.pc:04X})")


# ---------------------------------------------------------------- findstore
section("findstore: every encoding that can reach the cell, not just sta")

table = {**dis6502.LITERAL, **dis6502.UNDOC}
# A synthetic image with one writer of each form at $0000, so the sweep is tested
# without needing a ROM. `E6 4D` must be found: `inc mapind` is the increment of
# `mapind`, and a search that only knows stores finds nothing and reads as "the
# routine does not exist" -- which is how journal 13 recorded `sta mapind`
# assembling as `8D 4D 00` when it assembles as `85 4D`.
img = bytearray(0x100)
img[0x00:0x02] = bytes([0x85, 0x4D])   # sta $4D
img[0x10:0x12] = bytes([0x86, 0x4D])   # stx $4D
img[0x20:0x22] = bytes([0x84, 0x4D])   # sty $4D
img[0x30:0x32] = bytes([0xE6, 0x4D])   # inc $4D
img[0x40:0x42] = bytes([0xC6, 0x4D])   # dec $4D
img[0x50:0x53] = bytes([0x8D, 0x4D, 0x00])   # sta $004D
hits, scanned, _u = findstore.scan(bytes(img), 0x4D, table, False)
forms = sorted((mn, mode) for _o, _l, mn, mode in hits)
check(len(hits) == 6, f"all six writers of $4D found ({len(hits)})")
check(("inc", "zp") in forms, f"inc $4D is a writer: {forms}")
check(("dec", "zp") in forms, f"dec $4D is a writer: {forms}")
check(("sta", "abs") in forms, f"sta $004D is a writer: {forms}")

# An absolute form must not be matched by a cell of the same low byte at another
# address: `8D 4D 01` writes $014D, not $004D.
img2 = bytearray(0x100)
img2[0x00:0x03] = bytes([0x8D, 0x4D, 0x01])
hits2, _s, _u = findstore.scan(bytes(img2), 0x4D, table, False)
check(hits2 == [], f"sta $014D is not a writer of $004D ({hits2})")

# A read is not a write: `A5 4D` is `lda $4D` and must not be reported.
img3 = bytearray(0x100)
img3[0x00:0x02] = bytes([0xA5, 0x4D])
hits3, _s, _u = findstore.scan(bytes(img3), 0x4D, table, False)
check(hits3 == [], f"lda $4D is not a writer ({hits3})")

# Indexed forms are reported but flagged, because they only hit while the index
# is zero -- a claim that needs stating rather than hiding.
img4 = bytearray(0x100)
img4[0x00:0x02] = bytes([0x95, 0x4D])
hits4, _s, _u = findstore.scan(bytes(img4), 0x4D, table, False)
check(len(hits4) == 1 and hits4[0][3] == "zpx",
      f"sta $4D,x is found and its mode recorded: {hits4}")


# ------------------------------------------------------------------ summary
print(f"\nwrite trace: {CHECKS} checks")
if FAILURES:
    print(f"{len(FAILURES)} FAILED")
    for f in FAILURES:
        print(f"  - {f}")
    raise SystemExit(1)
print("all checks passed")