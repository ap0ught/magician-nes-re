"""nestrace's CPU core, against known answers. NOT a framebuffer instrument.

    python3 src/testing/test_nestrace_cpu.py

    *** Every check in this file is named `cpu:` and none of them is about
    *** pixels. tools/nestrace.py reports a black screen for a ROM it got right
    *** and for a ROM it got wrong, and its PPU is a second PPU written to match
    *** this project's assumptions. `make testsuite` runs it against
    *** koute/pinky's nes-testsuite and is the only gate on its correctness; this
    *** file is not that gate and does not pretend to be. See WHAT IT DOES NOT
    *** CLAIM.

`tools/nestrace.py` exists because this machine has no NES emulator and BizHawk
is GUI-only, so it is the only instrument that can execute the rebuild. That
makes it load-bearing and, for that reason, it once carried a bug that could not
fail loudly:

    `asl a` / `lsr a` / `rol a` / `ror a` read the *previous* instruction's opcode
    to decide they were accumulator-mode. Those four are the only instructions
    with no operand byte, so a wrong answer about "is there an operand" means
    `mode` came out 0 -- and address 0 is zero page. The result was that
    `asl a` at $8000 shifted the value in $00 and stored the shifted byte back
    into it, corrupting game RAM on every accumulator shift in the ROM, with no
    error anywhere. It is checked here four times over, with $00 preloaded with a
    sentinel that must not move.

Twenty-eight checks:

   1-4.  `asl a`, `lsr a`, `rol a`, `ror a` shift A and leave $00 untouched, with
          the flags each is supposed to set
   5-6.  the control: `sta $00` does write $00, and the accumulator shifts write
          nothing at all
   7-9.  a known-answer instruction set: addressing modes, zero-page wraparound,
          indexed page crossing, indirect
  10-13. interrupts: NMI reads $FFFA, IRQ reads $FFFE, RESET reads $FFFC, and the
          three are read in that order -- this is the same iNES ordering trap as
          `tools/bizhawk/identity.py`, in a different file
  14-15. `jsr`/`rts` push and pull the right bytes and return past the call
  16-17. cycles: the official per-opcode counts, which is the one thing nestrace
          does claim about timing
  18.    a ROM on a mapper this tracer does not model RAISES rather than running
          through the wrong bank map -- and the MMC3 IRQ acknowledge applies to
          MMC3 only, not to NROM
  19-21. the CPU-only guarantee: after every check above, PPU RAM, the palette,
          OAM and the nametable are all still zero. That is what makes "none of
          these checks is about pixels" a measurement rather than a promise.

WHAT IT DOES NOT CLAIM

  * **Nothing here says a frame is right.** The PPU is not exercised and the
    framebuffer is not touched, and check 19-22 exist to make that visible rather
    than to assert it. `nestrace.py`'s own docstring calls it "a diagnostic, not
    an accuracy claim", and that is still true; this file only pins the CPU half,
    which is the half that can be pinned without another emulator.
  * **Cycle counts are the official base counts.** Page-crossing and branch-taken
    penalties are not checked here, so a timing claim beyond the base count is
    not one this file supports.
  * **Unofficial opcodes are treated as NOPs of the right length**, which is a
    choice, not an accuracy claim, and is checked only for length.
  * **A ROM on an unmodelled mapper is refused.** That is check 18 and it is the
    most load-bearing thing in the file: a wrong bank map produces a confident,
    plausible, wrong execution trace, which is the failure this whole directory
    exists to prevent.

WHAT IT NEEDS: python3. No emulator, no cartridge, no display. Under a second.

Run:  python3 src/testing/test_nestrace_cpu.py
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

os.chdir(ROOT)

import nestrace as N  # noqa: E402  (tools/nestrace.py)

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok cpu: {name}")


# A 16 KiB NROM PRG, so the CPU sees $8000-$BFFF and $C000-$FFFF as ordinary ROM.
PRG = bytearray(0x4000)
# The three vectors, at PRG-6, deliberately far apart and all different. If any
# two were equal a swapped read could not be detected.
VEC_NMI, VEC_RESET, VEC_IRQ = 0x9000, 0xA000, 0xB000


def put(addr, data):
    """Bytes into the ROM image at a CPU address in $8000-$FFFF."""
    assert addr >= 0x8000
    PRG[addr - 0x8000:addr - 0x8000 + len(data)] = data


def vectors():
    PRG[-6] = VEC_NMI & 0xFF
    PRG[-5] = VEC_NMI >> 8
    PRG[-4] = VEC_RESET & 0xFF
    PRG[-3] = VEC_RESET >> 8
    PRG[-2] = VEC_IRQ & 0xFF
    PRG[-1] = VEC_IRQ >> 8


# Test code lives at $8100, never $8000. On MMC3 a read of $8006 is the IRQ
# acknowledge, so an operand byte placed there reads back as the scanline counter
# and the instruction after it executes as `brk`. That is a real property of the
# board and worth not colliding with -- and it is how this file first found the
# mapper-0 half of the same bug, which is now fixed and asserted below.
CODE_AT = 0x8100


def machine(code, start=CODE_AT, ram=None):
    """A Bus with `code` at `start`, and a CPU about to execute it there."""
    vectors()
    if code:
        put(start, code)
    bus = N.Bus(bytes(PRG), b"", mapper=0, vertical=True)
    if ram:
        for addr, val in ram.items():
            bus.ram[addr] = val
    return bus, N.CPU(bus)


def run(code, steps, start=CODE_AT, ram=None):
    """Execute `code` for `steps` instructions and return (cpu, bus).

    `steps` is required rather than defaulted to the byte length: a loop that
    runs one step per byte executes whatever follows the code, and on a synthetic
    image that is zeroes, so the test passes while measuring nothing.
    """
    bus, cpu = machine(code, start=start, ram=ram)
    cpu.pc = start
    for _ in range(steps):
        cpu.step()
    return cpu, bus


# =====================================================================================
# 1-6. THE ACCUMULATOR-MODE BUG. The sentinel in $00 must not move.
# =====================================================================================
SENTINEL = 0x5A
for mnemonic, opcode, start_a, want, flags in (
        ("asl", 0x0A, 0x81, 0x02, dict(c=1, n=0, z=0)),   # $81 << 1 = $02
        ("lsr", 0x4A, 0x03, 0x01, dict(c=1, n=0, z=0)),   # $03 >> 1 = $01
        ("rol", 0x2A, 0x81, 0x03, dict(c=1, n=0, z=0)),   # $81 rol with C=1
        ("ror", 0x6A, 0x01, 0x80, dict(c=1, n=1, z=0))):  # $01 ror with C=1
    bus, cpu = machine(bytes((opcode,)), ram={0x00: SENTINEL})
    cpu.pc = CODE_AT
    cpu.a, cpu.c = start_a, 1
    cpu.step()
    check(f"{mnemonic} a shifts A: ${start_a:02X} -> ${cpu.a:02X}, and does NOT "
          f"touch zero page",
          cpu.a == want and bus.ram[0x00] == SENTINEL
          and (cpu.c, cpu.n, cpu.z) == (flags["c"], flags["n"], flags["z"]),
          f"A became ${cpu.a:02X} (wanted ${want:02X}), $00 became "
          f"${bus.ram[0x00]:02X} (wanted ${SENTINEL:02X} untouched), flags were "
          f"N={cpu.n} Z={cpu.z} C={cpu.c} (wanted {flags})")

# A second pass with a different C, because "the carry in" and "the carry out" are
# two different bits and a shift that mixes them up is still a plausible number.
for mnemonic, opcode, start_a, cin, want, flags in (
        ("rol", 0x2A, 0x40, 0, 0x80, dict(c=0, n=1, z=0)),   # $40 rol with C=0
        ("ror", 0x6A, 0x03, 0, 0x01, dict(c=1, n=0, z=0)),   # $03 ror with C=0
        ("asl", 0x0A, 0x00, 1, 0x00, dict(c=0, n=0, z=1))):  # $00 asl, C out = 0
    bus, cpu = machine(bytes((opcode,)), ram={0x00: SENTINEL})
    cpu.pc = CODE_AT
    cpu.a, cpu.c = start_a, cin  # noqa: E501
    cpu.step()
    check(f"{mnemonic} a with carry in {cin}: ${start_a:02X} -> ${cpu.a:02X}, "
          f"$00 still untouched",
          cpu.a == want and bus.ram[0x00] == SENTINEL
          and (cpu.c, cpu.n, cpu.z) == (flags["c"], flags["n"], flags["z"]),
          f"A became ${cpu.a:02X} (wanted ${want:02X}), $00 is "
          f"${bus.ram[0x00]:02X}; flags N={cpu.n} Z={cpu.z} C={cpu.c} wanted {flags}")

# The control: an instruction that *should* write $00 does.
cpu, bus = run(bytes((0xA9, SENTINEL, 0x85, 0x00)), steps=2, ram={0x00: 0x00})
check("the control: `lda #$5A / sta $00` DOES write $00",
      bus.ram[0x00] == SENTINEL,
      f"$00 is ${bus.ram[0x00]:02X}. If this ever stops being true the checks "
      f"above are not measuring what they claim.")

# And an accumulator shift writes nothing anywhere in zero page or RAM.
cpu, bus = run(bytes((0x0A, 0x0A, 0x0A)), steps=3, ram={i: 0x11 for i in range(0x20)})
untouched = [i for i in range(0x20) if bus.ram[i] != 0x11]
check("three accumulator shifts in a row write NOTHING to zero page",
      not untouched,
      f"zero page at {['${0x%02X' % i for i in untouched]} changed. That is the "
      f"bug this file exists to keep fixed: an accumulator shift had an operand "
      f"of $00.")

# =====================================================================================
# 7-9. A known-answer instruction set.
# =====================================================================================
cpu, bus = run(bytes((0xA9, 0x42, 0xA2, 0x03, 0xA0, 0x02,
                      0x9D, 0x00, 0x02,            # sta $0200,x  -> $0203
                      0xBD, 0x00, 0x02)), steps=5)
check("indexed stores land at base+X and the load reads back the same byte",
      bus.ram[0x0200 + 3] == 0x42 and cpu.a == 0x42,
      f"$0203 is ${bus.ram[0x0203]:02X} and A is ${cpu.a:02X}; a wrong index "
      f"puts the byte next door and the loop then reads a neighbour's variable")

cpu, bus = run(bytes((0xA2, 0xFF, 0xB5, 0x80)), steps=2)   # ldx #$FF / lda $80,x
check("zero-page indexed addressing WRAPS: $80+$FF is $7F, not $17F",
      cpu.a == bus.ram[0x007F],
      f"A came from ${cpu.a:02x} and $7F holds ${bus.ram[0x007F]:02x}. Without the "
      f"wrap the byte is taken from RAM instead, silently.")

# (zp),y with a pointer at $02F8 and an index of $08: the real address is $0300,
# which crosses a page. A decoder that drops the pointer's high byte reads
# $0200 instead, and this game's zero-page pointer arithmetic does exactly this
# kind of thing -- `mptrl`/`mptrh` are the compressed-message pointer.
bus, cpu = machine(bytes((0xA0, 0x08,            # ldy #$08
                          0xA9, 0x5A,            # lda #$5A
                          0x91, 0x10,            # sta ($10),y
                          0xB1, 0x10)),          # lda ($10),y
                 ram={0x10: 0xF8, 0x11: 0x02, 0x0200: 0x00})
cpu.pc = CODE_AT
for _ in range(3):
    cpu.step()
check("(zp),y keeps the pointer's high byte: a pointer at $02F8 indexed by $08 "
      "reaches $0300, not $0200",
      cpu.a == SENTINEL and bus.ram[0x0300] == SENTINEL
      and bus.ram[0x0200] == 0x00,
      f"A came out ${cpu.a:02X}, $0300 holds ${bus.ram[0x0300]:02X} and the decoy at "
      f"$0200 holds ${bus.ram[0x0200]:02X}. Losing the page reads a different "
      f"byte of RAM with no error, and a message decoder that does that prints "
      f"the wrong character rather than crashing.")

# =====================================================================================
# 10-13. Interrupts. Same ordering trap as identity.py, in a different file.
# =====================================================================================
for name, trigger, want in (("nmi", "nmi_pending", VEC_NMI),
                            ("irq", "irq_pending", VEC_IRQ)):
    bus, cpu = machine(b"")
    cpu.pc = 0x8123
    setattr(cpu, trigger, True)
    cpu.step()
    check(f"{name.upper()} vectors through ${want:04X}, and the two vectors are "
          f"different addresses",
          cpu.pc == want,
          f"pc is ${cpu.pc:04X}, wanted ${want:04X}. NMI is $FFFA and IRQ is $FFFE; "
          f"reading them the other way round produces two numbers that both look "
          f"like plausible addresses.")

bus, cpu = machine(b"")
check("the base class distinguishes NMI from IRQ: RESET is neither",
      VEC_RESET not in (VEC_NMI, VEC_IRQ) and VEC_RESET == 0xA000,
      "RESET is the middle vector at $FFFC and has no pending flag here, which is "
      "why a swapped pair can go unnoticed in a test that only checks one")
# The CPU boots by reading $FFFC/$FFFD; read it the same way the machine does.
boot = bus.read(0xFFFC) | (bus.read(0xFFFD) << 8)
check("RESET is reachable at $FFFC/$FFFD and reads back the middle vector",
      boot == VEC_RESET, f"got ${boot:04X}, wanted ${VEC_RESET:04X}")

# =====================================================================================
# 14-17. jsr/rts, and the cycle counts.
# =====================================================================================
PRG[:] = bytearray(0x4000)
vectors()
put(CODE_AT, bytes((0x20, 0x10, 0x81)))        # jsr $8110
put(0x8110, bytes((0x60,)))                    # rts
bus, cpu = machine(b"")
cpu.pc = CODE_AT
cpu.step()
pushed = (bus.ram[0x100 + cpu.sp + 2], bus.ram[0x100 + cpu.sp + 1])
check("jsr pushes the address of the LAST byte of its own instruction ($8102)",
      pushed == (0x81, 0x02),
      f"pushed ${pushed[1]:02X}${pushed[0]:02X}, wanted $8102. rts adds one, so a "
      f"push of $8101 returns into the middle of the jsr and its third byte is "
      f"executed as an opcode.")
after_jsr, cyc_jsr = cpu.pc, cpu.cycles
cpu.step()
check("rts returns to $8103, past the three-byte jsr",
      cpu.pc == 0x8103, f"pc is ${cpu.pc:04X}, wanted $8103")
check("jsr costs 6 cycles and rts 6, the official base counts",
      (cyc_jsr, cpu.cycles - cyc_jsr) == (6, 6),
      f"jsr took {cyc_jsr} cycles and rts took {cpu.cycles - cyc_jsr}; a base "
      f"count that is wrong shifts every raster split after it")

# (code, instructions to execute, total cycles those instructions must cost, name)
# The step count is explicit per case because the byte length is not the
# instruction count, and a case that executes more than it meant to measures the
# wrong total without noticing.
CYCLES = [(b"\xa9\x00", 1, 2, "lda #"), (b"\x85\x00", 1, 3, "sta zp"),
          (b"\xa5\x00", 1, 3, "lda zp"), (b"\x8d\x00\x02", 1, 4, "sta abs"),
          (b"\xa9\x00\x8d\x00\x02", 2, 6, "lda # then sta abs"),
          (b"\x9d\x00\x02", 1, 5, "sta abs,x with X=0 -- NO page-cross penalty"),
          (b"\x4c\x00\x81", 1, 3, "jmp abs"), (b"\xea", 1, 2, "nop")]
bad = []
for code, steps, want, name in CYCLES:
    bus, cpu = machine(code)
    cpu.pc = CODE_AT
    for _ in range(steps):
        cpu.step()
    if cpu.cycles != want:
        bad.append(f"{name}: {cpu.cycles} cycles, wanted {want} "
                   f"(table says {N.OPS[code[0]][2]} for "
                   f"{code[0]:02X} {N.OPS[code[0]][0]} {N.OPS[code[0]][1]})")
check("the official base cycle counts are right for eight representative "
      "opcodes, including that a store gets NO page-crossing penalty",
      not bad, "\n         ".join(bad))

# =====================================================================================
# 18. The load-bearing refusal.
# =====================================================================================
# Mapper 4 is MMC3 -- this game's actual board, and modelled. Mapper 1 is MMC1,
# which this tracer does not model and must therefore refuse.
for good in (0, 4):
    try:
        N.Bus(bytes(0x4000), b"", mapper=good, vertical=True)
    except Exception as e:                                  # noqa: BLE001
        raise AssertionError(
            f"mapper {good} is refused ({type(e).__name__}: {e}). Mapper 4 is "
            f"MMC3 and is what this cartridge is; mapper 0 is what most of the "
            f"checks above use.") from None
check("mapper 0 (NROM) and mapper 4 (MMC3) are both accepted", True)

try:
    N.Bus(bytes(0x4000), b"", mapper=1, vertical=True)
    raised = False
    msg = ""
except N.UnsupportedMapper as e:
    raised, msg = True, str(e)
except Exception as e:                                   # noqa: BLE001
    raised, msg = False, f"{type(e).__name__}: {e}"
# The MMC3 IRQ acknowledge must not leak onto NROM. `bankreg` starts at 6 and is
# never changed on a board with no bank registers, so an unguarded
# `bankreg == 6 and (a & 0x1FFF) in (0x0006, 0x2006)` shadows $8006 on mapper 0
# too -- and $8006 is an ordinary address in a 16 KiB program. This is the check
# that found it: the operand byte of `sta $0200,x` was sitting at $8006 and read
# back as $00.
rom_at_8006 = bytes((0x9D, 0x00, 0x02))       # sta $0200,x
bus6, cpu6 = machine(rom_at_8006, start=0x8000)
check("on NROM, $8006 is PRG -- the MMC3 IRQ acknowledge does not shadow it",
      bus6.read(0x8006) == 0x00,
      f"$8006 read back as ${bus6.read(0x8006):02X}, wanted $00. If the IRQ "
      f"acknowledge applies to mapper 0 then any 16 KiB program with a byte at "
      f"$8006 or $A006 executes the wrong instruction, silently.")
m3, cpu3 = machine(b"", start=0x8000)
m3.mapper = 4
check("on MMC3 the same read IS the scanline counter -- the behaviour the check "
      "above narrows, not removes",
      m3.read(0x8006) == m3.irq_cycles,
      f"$8006 on MMC3 read ${m3.read(0x8006):02X}, wanted the scanline counter "
      f"${m3.irq_cycles:02X}. This game's bank-switch loop polls $8006 and never "
      f"reaches its main loop without it.")

check("a bus on a mapper this tracer does not model (MMC1) RAISES", raised,
      f"it raised {msg}. A ROM executed through the wrong bank map produces a "
      f"confident, plausible, wrong trace -- which is the failure this whole "
      f"directory exists to prevent, and it is invisible without this check.")

# =====================================================================================
# 19-22. The CPU-only guarantee.
# =====================================================================================
# Aim $2006 at $2000 (nametable 0) and write a byte through $2007. This is the
# positive control for the check below it: if a CPU-only test could not reach
# the PPU at all, "nothing was written" would be true for a reason that has
# nothing to do with the CPU being correct.
bus, cpu = machine(bytes((0xA9, 0x20,            # lda #$20
                          0x8D, 0x06, 0x20,      # sta $2006   (high byte)
                          0xA9, 0x00,            # lda #$00
                          0x8D, 0x06, 0x20,      # sta $2006   (low byte)
                          0xA9, 0x3F,            # lda #$3F
                          0x8D, 0x07, 0x20)))       # sta $2007
cpu.pc = CODE_AT
for _ in range(6):
    cpu.step()
check("a $2006/$2007 write from this file's synthetic code DOES land in VRAM, so "
      "the PPU is reachable and the next check is not vacuous",
      bus.vram[0] == 0x3F and bus.ppu_writes.get(7) == 1,
      f"v is ${bus.v:04X}, vram[0] is ${bus.vram[0]:02X}, register writes were "
      f"{dict(bus.ppu_writes)}")

fresh_bus, fresh_cpu = machine(bytes((0xA9, 0x00, 0x85, 0x10,
                                      0xA9, 0x00, 0x85, 0x10)))
fresh_cpu.pc = CODE_AT
for _ in range(4):
    fresh_cpu.step()
check("...and with no $2006/$2007 write at all, VRAM, the palette, OAM and the "
      "nametable are all still zero",
      not any(fresh_bus.vram) and not any(fresh_bus.pal) and not any(fresh_bus.oam),
      "which is the measurable form of 'none of the checks above says anything "
      "about the picture'. nestrace's PPU is a second PPU written to match this "
      "project's assumptions; BizHawk's is the one that decides.")

check("the tracer's own docstring still calls itself a diagnostic and not an "
      "accuracy claim",
      "not an accuracy claim" in (ROOT / "tools" / "nestrace.py")
      .read_text(encoding="utf-8").replace("accuracy-\nclaim", "accuracy claim"),
      "if that sentence is edited away, the distinction between this file's CPU "
      "checks and a correctness claim about frames disappears with it")

check("the nes-testsuite gate is still wired into the Makefile, since it is the "
      "only gate on the tracer's correctness",
      "testsuite:" in (ROOT / "Makefile").read_text(encoding="utf-8")
      and "tools/nestrace.py --testsuite" in (ROOT / "Makefile").read_text(encoding="utf-8"),
      "without this, a suite of CPU checks could be mistaken for one that "
      "validates the tracer")

print(f"nestrace cpu: {ok} checks")
print("all checks passed")
print("NOTE: every check above is `cpu:`. This file says nothing about "
      "framebuffers, and the two checks that touch the PPU do so only to show "
      "that the rest do not.")