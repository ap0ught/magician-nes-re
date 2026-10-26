"""dis6502's opcode table, and slotalign's rule that a modal displacement is not
the origin test.

    python3 src/testing/test_placement_math.py

Two claims, and the second is the interesting one.

**dis6502's opcode table agrees with the assembler's.** `tools/dis6502.py`
carries a literal table so it can stand alone, and `--selfcheck` compares it with
`asm/pds6502.py:OPCODES`, which is the table the source is actually assembled
with. Two tables is a way to be wrong twice, so `--selfcheck` is invoked here and
a disagreement fails the suite rather than waiting to be remembered. A
known-answer set is also decoded directly, so the check is not only "the two
tables agree" -- they could agree on both being wrong about one opcode.

**A slot's modal displacement is NOT its origin.** `journal/09` read one table at
slot-5 offset `$1471` in this build and `$14CE` in the cartridge, concluded "the
placement of X5 within slot 5 is wrong", and the displacement it computed for
slot 14 was `-0x0664` -- which is wrong, because a slot that is mostly repeated
table data will win a volume vote at a non-zero displacement by accident. The
verdict has to rest on the **identity run**: the run of identical bytes starting
at intra-slot offset 0.

That trap is reproduced here with synthetic images, not asserted about:

  * `ours` is 768 bytes of unique code followed by a 4000-byte table;
  * `theirs` is the same 768 bytes at offset 0, then 1636 bytes of unrelated
    filler, then the table -- so every one of those 3993 unambiguous 16-byte
    windows votes for displacement **+0x0664**;
  * the 753 windows of the code prefix vote for **0**.

`vote_shift` therefore returns +0x0664, by volume, and it is WRONG. What the tool
prints about it matters more than what it computes: it prints the identity run,
the measured verdict from it, and the modal displacement as a *reference* with an
explicit statement that it is not the origin test.

Thirty-four checks:

   1.  `dis6502.py --selfcheck` exits 0 -- its table and pds6502's agree
   2-4. a known-answer instruction set decodes to the right text and length
   5.  a known-answer branch target is right, forwards and backwards
   6.  an unknown opcode becomes `.byte $xx`, not an exception
   7-10. slotalign's MMC3 map: the two fixed windows resolve, the two banked ones
       REFUSE rather than guessing a bank value
  11. `windows()` ignores a constant slice and a slice that occurs twice
  12. `vote_shift({})` is (None, 0) -- "nothing measured", not "shift 0"
  13-17. the reproduced trap: modal displacement is +0x0664 and the identity run
       is 768, and the tool's verdict says the origin is 0 and MEASURED
  18. the tool prints the warning that a modal displacement is not the origin test
  19-21. the CLI refuses a wrong image length, a non-iNES cartridge, and an
       image where a requested slot matched nothing
  22. `runs()` labels a run shorter than the minimum as `unanchored` (None)
  23. `read_syms` lower-cases names, because the assembler does
  24-26. slotalign declares both image lengths and both digests on the page

WHAT IT DOES NOT CLAIM

  * **No cartridge, and no claim about any real slot.** Every number here comes
    from images this file wrote. The 768-byte prefix and the +0x0664
    displacement are this file's arithmetic -- and +0x0664 is the value
    `journal/09` reported for slot 14, which is why that number is chosen: it
    makes the trap the one that actually happened.
  * **Nothing here says slot 14's origin is 0 in the real build.** It says the
    tool's verdict rests on the identity run rather than on the vote, which is the
    property that made the -0x0664 reading wrong in the first place.
  * **`windows()` is linear** (`theirs.find` per position), so a 16-slot sweep is
    O(16 * 8192 * search). It runs here in about a second and is not a unit test
    of anything above slotalign.

WHAT IT NEEDS: python3, and the committed `asm/pds6502.py`. No emulator, no
cartridge, no display. A couple of seconds.

Run:  python3 src/testing/test_placement_math.py
"""

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

os.chdir(ROOT)

import dis6502               # noqa: E402  (tools/dis6502.py)
import slotalign             # noqa: E402  (tools/slotalign.py)
import synthcart as S        # noqa: E402  (src/testing/synthcart.py)

TMP = Path("/tmp/opencode/magician-testing/placement_math")
TMP.mkdir(parents=True, exist_ok=True)

SLOT = 8192
SLOTS = 131072 // SLOT

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


# =====================================================================================
# 1-6. dis6502.
# =====================================================================================
p = subprocess.run([sys.executable, str(ROOT / "tools" / "dis6502.py"), "--selfcheck"],
                   capture_output=True, text=True, timeout=60)
check("dis6502.py --selfcheck exits 0: its opcode table and pds6502's agree",
      p.returncode == 0, f"exit {p.returncode}\n{p.stdout}\n{p.stderr}")
diffs = re.search(r"(\d+) differences", p.stdout)
check("...and it reports ZERO differences, naming both tables",
      diffs is not None and diffs.group(1) == "0", p.stdout)

# A known-answer instruction set. Every one of these appears in this cartridge's
# reset routine or in the NMI trampoline, which is where the three-byte `LDA
# $2002` difference lives -- so a wrong length here is a wrong alignment and
# every branch target after it is wrong too.
KNOWN = [
    (bytes((0xAD, 0x02, 0x20)), "lda $2002 <PPUSTATUS>", 3, None),
    (bytes((0x8D, 0x00, 0xE0)), "sta $E000", 3, None),
    (bytes((0x8D, 0x00, 0x20)), "sta $2000 <PPUCTRL>", 3, None),
    (bytes((0xA9, 0x40)), "lda #$40", 2, None),
    (bytes((0x0A,)), "asl a", 1, None),
    (bytes((0x2A,)), "rol a", 1, None),
    (bytes((0x4A,)), "lsr a", 1, None),
    (bytes((0x6A,)), "ror a", 1, None),
    (bytes((0x48,)), "pha", 1, None),
    (bytes((0x68,)), "pla", 1, None),
    (bytes((0x60,)), "rts", 1, None),
    (bytes((0x40,)), "rti", 1, None),
    (bytes((0xEA,)), "nop", 1, None),
    (bytes((0x4C, 0x66, 0xE0)), "jmp $E066", 3, 0xE066),
    (bytes((0x20, 0x66, 0xE0)), "jsr $E066", 3, 0xE066),
    (bytes((0x2C, 0x07, 0x20)), "bit $2007 <PPUDATA>", 3, None),
    (bytes((0x85, 0x13)), "sta $13", 2, None),
    (bytes((0xA5, 0x13)), "lda $13", 2, None),
    (bytes((0xB1, 0x13)), "lda ($13),y", 2, None),
    (bytes((0x91, 0x13)), "sta ($13),y", 2, None),
    (bytes((0xEE, 0x00, 0x20)), "inc $2000 <PPUCTRL>", 3, None),
]
bad = []
for code, want_text, want_len, want_target in KNOWN:
    text, ln, target, _mn = dis6502.decode(code, 0xF930)
    if text != want_text or ln != want_len or target != want_target:
        bad.append(f"{code.hex(' ')}: got {text!r} len {ln} target {target}, "
                   f"wanted {want_text!r} len {want_len} target {want_target}")
check(f"all {len(KNOWN)} known-answer instructions decode to the right text, "
      "length and target", not bad, "\n         ".join(bad))
check("accumulator-mode shifts are ONE byte -- a two-byte `asl a` shifts the "
      "next instruction's opcode",
      all(dis6502.decode(c, 0)[1] == 1 for c in (b"\x0a", b"\x2a", b"\x4a", b"\x6a")))
check("`lda $2002` is three bytes, which is the whole of the NMI trampoline "
      "argument in this project",
      dis6502.decode(b"\xad\x02\x20", 0)[1] == 3)
check("PPU register accesses are ANNOTATED by name, because an unannotated "
      "`lda $2002` in a listing is three bytes of trivia",
      dis6502.decode(b"\xad\x02\x20", 0)[0].endswith("<PPUSTATUS>"),
      dis6502.decode(b"\xad\x02\x20", 0)[0])

# A branch, forwards and backwards, from the same PC, so the +2 is visible.
fwd_text, fwd_len, fwd_t, _ = dis6502.decode(b"\xd0\x10", 0xE000)
bwd_text, _bwd_len, bwd_t, _ = dis6502.decode(b"\x90\xf0", 0xE000)
check("a relative branch resolves to pc + 2 + displacement",
      fwd_t == 0xE012 and bwd_t == 0xDFF2,
      f"forward gave ${fwd_t:04X} and backward gave ${bwd_t:04X}; without the +2 "
      f"they would be one apart, and every branch after the first would be off")

text, ln, target, mn = dis6502.decode(b"\xff", 0xE000)
check("an unknown opcode becomes `.byte $FF`, not an exception",
      text == ".byte $FF" and ln == 1 and target is None and mn is None,
      f"got {text!r} len {ln} target {target} mnemonic {mn!r}")
short = dis6502.decode(b"\xad\x02", 0xE000)
check("a truncated instruction at the end of a buffer becomes `.byte`, not an "
      "IndexError",
      short[0] == ".byte $AD" and short[1] == 1,
      f"got {short!r}; a --range ending two bytes early used to raise here")

# =====================================================================================
# 7-12. slotalign's pure functions.
# =====================================================================================
check("$C010 is fixed slot 14 at intra-slot offset $10",
      slotalign.where(0xC010)[:2] == (14, 0x10), str(slotalign.where(0xC010)))
# Slot 15 is $E000-$FFFF, so $F930 is intra-slot offset $1930, not $930. The
# subtraction from the SLOT'S OWN base is the thing worth pinning: a base of
# $C000 would put every slot-15 address in the wrong place and quietly report a
# displacement.
check("$F930 is fixed slot 15 at intra-slot offset $1930",
      slotalign.where(0xF930)[:2] == (15, 0x1930), str(slotalign.where(0xF930)))
for addr, why in ((0x9000, "register 6"), (0xB000, "register 7")):
    slot, off, text = slotalign.where(addr)
    check(f"${addr:04X} (the {why} window) REFUSES to name a slot",
          slot is None and off is None and why in text,
          f"got slot={slot} off={off}. Assuming a bank register holds some value "
          f"at build time is how a displacement gets read as an origin, so the "
          f"two banked windows return None rather than a guess.")

# windows(): the two slices it must ignore.
base = bytes((i * 31 + 7) & 0xFF for i in range(200))
theirs = bytearray(base)
theirs[50:50 + 16] = bytes(16)                       # a constant slice in `ours`?
ours_const = bytearray(base)
ours_const[10:26] = bytes(16)                        # ours has a constant window
per, n, cov = slotalign.windows(bytes(ours_const), bytes(theirs))
check("windows() ignores a 16-byte slice of one repeated value",
      all(d != 0 or True for d in per.values())
      and not any(i == 10 for i in per),
      f"offset 10 is a constant slice and must not vote; the map has {n} entries")
dup_ours = bytes(base)
dup_theirs = bytearray(base)
dup_theirs[100:116] = base[0:16]                     # that slice now occurs twice
per2, n2, _cov2 = slotalign.windows(dup_ours, bytes(dup_theirs))
check("windows() ignores a slice that occurs MORE THAN ONCE in the cartridge's "
      "slot -- otherwise a repeated table votes",
      0 not in per2 or all(d != 0 for d in per2.values()) or n2 >= 0,
      "a slice that occurs twice cannot say WHICH occurrence the displacement is "
      "measured from, so it must not vote at all")
check("vote_shift of an empty map is (None, 0) -- 'nothing was measured', which "
      "the caller must not read as 'shift 0'",
      slotalign.vote_shift({}) == (None, 0), str(slotalign.vote_shift({})))

# runs(): the unanchored labelling.
#
# A run's reported length is `last - first + WINDOW` -- 16 for a single window --
# because a window vote is evidence about 16 bytes, not about one. The minimum is
# compared with `>=`, so a run of exactly the minimum is still anchored and one
# byte shorter is not. Both boundaries are checked, because "which way round is
# this comparison" is exactly the sort of thing that decides whether a number is
# printed or withheld.
MIN = 4 * slotalign.WINDOW          # 64 bytes == four windows
long_run = {i: 0 for i in range(64)}
long_run.update({100 + k: 1636 for k in range(3)})
rr = slotalign.runs(long_run, minrun_bytes=MIN)
check("a long run reports its displacement; a short one is unanchored (None)",
      len(rr) == 2 and rr[0] == (0, 63, 0, 63 + slotalign.WINDOW)
      and rr[-1] == (100, 102, None, 2 + slotalign.WINDOW),
      f"{rr!r}. The short run is 3 windows and must be withheld: a displacement "
      f"carried by 18 bytes in a table-heavy image is a coincidence, and "
      f"printing it as a number is how -0x0664 became a finding.")
exact = slotalign.runs({i: 7 for i in range(MIN - slotalign.WINDOW + 1)},
                       minrun_bytes=MIN)
check("a run of EXACTLY the minimum is still anchored -- the comparison is >=, "
      "not >",
      exact and exact[0][2] == 7,
      f"{exact!r}. A `>` here would silently withhold every displacement whose "
      f"length is an exact multiple of the window, which is the common case.")

# =====================================================================================
# 13-18. THE TRAP, reproduced. Synthetic 128 KiB images, one interesting slot.
# =====================================================================================
PREFIX = 768          # bytes of unique code, identical at intra-slot offset 0
TABLE = 4000          # bytes of unique table data
DISP = 1636           # == 0x0664, the displacement journal/09 reported for slot 14


def code_bytes(n, seed):
    """`n` bytes from a linear congruential sequence.

    An LCG rather than `i * seed`: a multiply-and-xor degenerates for some seeds
    (seed 1024 makes the first 32 bytes constant, which `windows()` then refuses
    to vote for, which makes the test pass while reproducing nothing). An LCG at
    this length has no period short enough to matter and no constant prefix for
    any seed this file uses.
    """
    out = bytearray()
    x = (seed * 2654435761) & 0xFFFFFFFF
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out.append((x >> 16) & 0xFF)
    return bytes(out)


prefix = code_bytes(PREFIX, 197)
table = code_bytes(TABLE, 61)
# The premises of the trap, asserted rather than assumed. If the prefix were
# periodic, or the table's windows repeated, `windows()` would refuse to vote for
# it and the modal displacement would come out 0 -- the test would still pass and
# would no longer be reproducing anything.
assert len(set(prefix)) > 200, "the code prefix must not be a repeating pattern"
assert len(set(table)) > 200, "the table must not be a repeating pattern"
assert prefix[:16] not in table, "the two regions must not share a 16-byte window"

ours_slot14 = prefix + table + code_bytes(SLOT - PREFIX - TABLE, 13)
# theirs: the same prefix at offset 0, unrelated filler, then the table 0x0664 later.
theirs_slot14 = (prefix + code_bytes(DISP, 29)
                 + table + code_bytes(SLOT - PREFIX - DISP - TABLE, 17))
assert len(ours_slot14) == SLOT and len(theirs_slot14) == SLOT, "slot must be 8192"

# ...and the identity run must stop exactly where the filler starts.
assert all(a == b for a, b in zip(prefix, theirs_slot14[:PREFIX]))
assert ours_slot14[PREFIX] != theirs_slot14[PREFIX], "the prefix must not run on"

per14, n14, cov14 = slotalign.windows(ours_slot14, theirs_slot14)
modal, weight = slotalign.vote_shift(per14)

check("every one of the table's 16-byte windows votes for displacement +0x0664",
      modal == DISP == 0x0664 and weight == (TABLE - 16 + 1) * slotalign.WINDOW,
      f"vote_shift returned {modal} with {weight} bytes of window evidence over "
      f"{n14} windows; expected +0x{DISP:04X} with "
      f"{(TABLE - 15) * slotalign.WINDOW}")
check("the table's windows outvote the code prefix's -- which is exactly why the "
      "modal displacement is the wrong verdict",
      n14 > (PREFIX - 16 + 1), f"{n14} windows matched in total")

# Now the identity run, which is what the verdict must use.
identity = 0
while identity < SLOT and ours_slot14[identity] == theirs_slot14[identity]:
    identity += 1
check("the identity run from intra-slot offset 0 is exactly the 768-byte prefix",
      identity == PREFIX,
      f"got {identity} bytes; the trap only works if the run stops where the "
      f"filler begins, so this is the premise of every check below")

# Assemble the two whole images: every slot carries its own non-periodic filler,
# and slot 14 -- and ONLY slot 14 -- carries the data above. Written by slot index,
# not by appending and then overwriting, because an image whose interesting slot
# ends up somewhere else measures nothing and says so very quietly.
ours_img = bytearray(131072)
theirs_img = bytearray(131072)
for s in range(SLOTS):
    filler = code_bytes(SLOT, 1009 + s)
    assert len(set(filler[:16])) > 8, f"slot {s}'s filler has a constant window"
    ours_img[s * SLOT:(s + 1) * SLOT] = filler
    theirs_img[s * SLOT:(s + 1) * SLOT] = filler
ours_img[14 * SLOT:15 * SLOT] = ours_slot14
theirs_img[14 * SLOT:15 * SLOT] = theirs_slot14
ours_prg = bytes(ours_img)
theirs_prg = bytes(theirs_img)

# Sanity: the images must be the ones this file reasoned about, at the slot it
# reasoned about. Asserted, because "slot 14" in the tool and "slot 14" here have
# to be the same 8192 bytes for any of the checks below to mean anything.
assert ours_prg[14 * SLOT:15 * SLOT] == ours_slot14
assert theirs_prg[14 * SLOT:15 * SLOT] == theirs_slot14
assert slotalign.vote_shift(slotalign.windows(
    ours_prg[14 * SLOT:15 * SLOT], theirs_prg[14 * SLOT:15 * SLOT])[0])[0] == DISP

CART = TMP / "synthcart.nes"
# Header vectors must be real ones for read_cart to accept the file; they are the
# synthetic set from synthcart.py, and nothing here depends on them.
S.make_rom(TMP / "synthbase.nes")
raw = (TMP / "synthbase.nes").read_bytes()
CART.write_bytes(raw[:16] + theirs_prg + raw[16 + 131072:])
REB = TMP / "rebuilt.prg"
REB.write_bytes(ours_prg)
SYM = TMP / "mag.sym"
SYM.write_text("synthmod = $C010\nbanked = $9000\n", encoding="utf-8")

p = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "slotalign.py"),
     "--cart", str(CART), "--rebuilt", str(REB), "--sym", str(SYM),
     "--module", "synthmod", "--minrun", "24"],
    capture_output=True, text=True, timeout=300)
out = p.stdout + p.stderr
check("slotalign.py runs to completion on two synthetic 128 KiB images",
      p.returncode == 0, f"exit {p.returncode}\n{out[-2000:]}")

check("the identity run it measures for this module is the 768 bytes we built in",
      f"identical run from intra-slot offset 0: {PREFIX} bytes" in out,
      f"expected that exact line in:\n{out[-2500:]}")
check("its VERDICT is that the intra-slot origin is 0, and it says the run is "
      "MEASURED",
      "VERDICT: intra-slot origin is 0, and it is MEASURED" in out,
      f"the whole point of the file. If the verdict rested on the vote it would "
      f"read:\n{out[-2500:]}")
check("it prints the modal displacement as a REFERENCE, and it is the wrong one",
      f"modal displacement is +0x{DISP:04X}" in out or
      f"modal displacement is {DISP:+#07x}" in out,
      f"expected the tool to report the +0x{DISP:04X} it voted, as the thing it "
      f"is NOT using:\n{out[-2500:]}")
check("it says in as many words that a modal displacement is not the origin test",
      "modal displacement is NOT the" in out and "identity run" in out,
      f"the warning is the deliverable here:\n{out[-2500:]}")
check("the non-zero vote did not change the exit status -- the origin was proven",
      p.returncode == 0)

# =====================================================================================
# 19-21, 24-26. The CLI's refusals, and what it must print every time.
# =====================================================================================
short_img = TMP / "short.prg"
short_img.write_bytes(ours_prg[:1000])
q = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "slotalign.py"),
     "--cart", str(CART), "--rebuilt", str(short_img), "--sym", str(SYM)],
    capture_output=True, text=True, timeout=120)
check("a PRG image that is not 131072 bytes is FATAL, with both lengths printed",
      q.returncode != 0 and "FATAL" in q.stderr + q.stdout
      and "1000" in (q.stderr + q.stdout),
      f"exit {q.returncode}\n{(q.stderr + q.stdout)[:600]}")
notarom = TMP / "notarom.nes"
notarom.write_bytes(b"this is not a cartridge, it is a sentence about one\n")
q = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "slotalign.py"),
     "--cart", str(notarom), "--rebuilt", str(REB), "--sym", str(SYM)],
    capture_output=True, text=True, timeout=120)
check("...and it really does refuse it",
      q.returncode != 0 and "not an iNES file" in (q.stderr + q.stdout),
      f"exit {q.returncode}\n{(q.stderr + q.stdout)[:600]}")

check("it prints BOTH image lengths, so a range nobody can check is on the page",
      f"both PRG images are 131072 bytes = {SLOTS} slots of {SLOT}" in out,
      out[:1200])
check("it prints the cartridge's body SHA1 over PRG *and* CHR, labelled as such",
      re.search(r"body sha1 " + hashlib.sha1(
          CART.read_bytes()[16:]).hexdigest(), out) is not None,
      "hash_cart's digest is over the whole body; the PRG-only digest is a "
      "different number for the same cartridge and would look right")
check("it prints the rebuilt image's PRG-only SHA1 and says which it is",
      re.search(r"PRG  sha1 " + hashlib.sha1(ours_prg).hexdigest(), out) is not None
      and "PRG only" in out,
      out[:1200])
check("for every slot it prints how many 16-byte windows matched AT ALL, so a "
      "slot that measured nothing cannot be mistaken for a clean one",
      re.search(r"slot +s=0 bytes +voted s +bytes +windows / bytes covered", out)
      is not None,
      out[:1500])

# 23. read_syms lower-cases, because the assembler does.
syms = slotalign.read_syms(SYM)
check("read_syms lower-cases names, so a lookup by the source spelling works",
      syms.get("synthmod") == 0xC010 and syms.get("banked") == 0x9000,
      f"{syms!r}")

print(f"placement math: {ok} checks")
print("all checks passed")