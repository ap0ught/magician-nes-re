"""Where run.sh's identity window comes from, and the four ways it used to be wrong.

    python3 src/testing/test_identity.py

`tools/bizhawk/run.sh` is the gate in front of every measurement in this project.
Before a frame is drawn it must prove three things: the emulator is running the
core it was asked for, on the *file* it was pointed at, read back through the
core's own "System Bus" domain. The second of those needs 48 bytes of the
cartridge chosen out of the file, and choosing them is where this harness went
wrong -- twice, silently, and both times it printed a number.

  * `PRG_NIB=$(head -c 1 "$ROM" | tail -c +5)`. Header byte 4 is the **fifth**
    byte, and `head -c 1` produces a one-byte stream, so `tail -c +5` returns
    nothing. `PRG_LEN` was 0, the vectors were read at file offset 10 -- the
    iNES header's own zero padding -- and run.sh reported **three zero vectors
    without complaint**. It looked exactly like a successful run.
  * The iNES vector order is NMI ($FFFA), **RESET** ($FFFC), IRQ/BRK ($FFFE).
    The bash read them as NMI, IRQ, RESET. Two labels are swapped and all three
    numbers look fine.

Both are now `tools/bizhawk/identity.py`, which raises instead of returning 0.
This file is what keeps that true. It is the highest-priority test in the suite:
every other measurement in `tools/bizhawk/` is downstream of this window, and a
window read at the wrong address is a window of perfectly real cartridge bytes.

Sixteen checks:

   1. PRG and CHR lengths come from header bytes 4 and 5
   2. ...and are 128 KiB each for the size every dump of this title has
   3. the six vector bytes are read in NMI/RESET/IRQ order, and the three are
      different from each other so a swap cannot pass
   4. the raw six bytes are at the last six bytes of PRG, in that order
   5. the window is 48 bytes starting 8 below the lowest vector
   6. ...and the window's bytes equal an *independently* computed read of the
      same CPU addresses
   7. the PRG fill is not periodic, so a window read at the wrong offset cannot
      accidentally equal the right one (this is what makes check 6 mean anything)
   8. a PRG length of 0 RAISES, and does not report three zero vectors  <- the bug
   9. a file too short to hold its own header RAISES, and says so
  10. a header declaring more PRG than the file holds RAISES
  11. CHR = 0 means CHR-RAM and is legal: no error, chr_len 0, file is 16+PRG
  12. a vector outside $C000-$FFFF RAISES
  13. a window that cannot contain all three vectors RAISES rather than shipping
  14. the battery bit is derived per dump: $40 clear, $42 set
  15. `render`/`parse_rendered` round-trip every key, so run.sh's shell read
      cannot silently drop one
  16. the CLI exits 2 and prints nothing on stdout for a bad ROM, and the save
      stem strips only the last extension

WHAT IT DOES NOT CLAIM

  * **No cartridge is read.** Every ROM here is built by `synthcart.py` from
    arithmetic, so the suite needs no dump, no emulator and no display, and no
    dump's mtime can change. The vectors are $C111/$C222/$C333, which no dump of
    this title has; if a test ever printed one of those numbers it would mean only
    that this file's own arithmetic ran.
  * **It does not check the ROM is the one anyone had in mind.** A tampered file
    is self-consistent and passes every check here. `run.sh`'s
    `MAGICIAN_EXPECT_SHA1` is what pins that, and `test_run_sh.py` tests it.
  * **It does not check the window contains anything meaningful** -- only that
    it contains the three vectors. What the bytes *mean* is the cartridge's
    business; a window that missed a vector would still be 48 real bytes.
  * **It does not test run.sh's shell.** The arithmetic is tested here; the guards
    around it are in `test_run_sh.py`, which runs the real script.

WHAT IT NEEDS: python3. Runs in well under a second.

Run:  python3 src/testing/test_identity.py
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools" / "bizhawk"))
sys.path.insert(0, str(HERE))

os.chdir(ROOT)

import identity as I            # noqa: E402  (tools/bizhawk/identity.py)
import synthcart as S           # noqa: E402  (src/testing/synthcart.py)

TMP = Path("/tmp/opencode/magician-testing/identity")
TMP.mkdir(parents=True, exist_ok=True)

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


def raises(name, fn, must_say=()):
    """`fn` must raise IdentityError mentioning every fragment in `must_say`."""
    global ok
    try:
        got = fn()
    except I.IdentityError as exc:
        text = str(exc)
        missing = [m for m in must_say if m not in text]
        if missing:
            raise AssertionError(
                f"{name}: raised, but the message does not say {missing!r}.\n"
                f"         It said: {text}\n"
                f"         The message is the only thing a reader gets, so what it "
                f"fails to mention is a failure they cannot act on.") from None
        ok += 1
        print(f"  ok {name}")
        return
    raise AssertionError(
        f"{name}: returned {got!r} instead of raising.\n"
        f"         This is the shape of every quiet failure in this project's "
        f"history: a plausible value where an error belongs. If a caller cannot "
        f"distinguish this from a good result, the raise is the whole fix.")


# =====================================================================================
# 1-6. The well-formed case, and every number cross-checked against a second path.
# =====================================================================================
rom = S.make_rom(TMP / "plain.nes")
v = I.identity(rom)

check("header byte 4 -> 8 PRG units -> 131072 bytes",
      v["prg_len"] == 131072, f"got {v['prg_len']}")
check("header byte 5 -> 16 CHR units -> 131072 bytes",
      v["chr_len"] == 131072, f"got {v['chr_len']}")
check("header is 16 bytes", v["hdr"] == 16, f"got {v['hdr']}")

# The three vectors are $E0A1/$E0B9/$E0A9 -- all different, by construction, so a
# reader that swaps two labels cannot produce the right answer by luck. These are
# not any dump's vectors: the address is stated in `synthcart.py`'s docstring.
check("nmi = $E0A1 (the first little-endian word at PRG-6)",
      v["vec_nmi"] == 0xE0A1, f"got ${v['vec_nmi']:04X}")
check("reset = $E0B9 -- the MIDDLE word; reading it as irq is the bug",
      v["vec_reset"] == 0xE0B9, f"got ${v['vec_reset']:04X}")
check("irq = $E0A9 -- the third word",
      v["vec_irq"] == 0xE0A9, f"got ${v['vec_irq']:04X}")
check("the three vectors are pairwise different, so no swap can pass",
      len({v["vec_nmi"], v["vec_reset"], v["vec_irq"]}) == 3)

# The raw bytes, straight out of the file, in file order. iNES stores NMI at
# $FFFA, RESET at $FFFC, IRQ/BRK at $FFFE, so this is the ground truth the
# tuple above has to agree with.
raw6 = rom.read_bytes()[16 + 131072 - 6:16 + 131072]
check("the six vector bytes are at the last six bytes of PRG, little-endian, "
      "nmi/reset/irq",
      raw6 == bytes((0xA1, 0xE0, 0xB9, 0xE0, 0xA9, 0xE0)),
      f"got {raw6.hex(' ')}; expected a1 e0 b9 e0 a9 e0")

# The window: 8 below the lowest vector, 48 bytes long. Checked against the
# arithmetic rather than against identity.py's own numbers.
want_at = min(S.VEC_NMI, S.VEC_RESET, S.VEC_IRQ) - I.WINDOW_LEAD
check("window starts 8 bytes below the lowest vector",
      v["win_at"] == want_at == 0xE099, f"got ${v['win_at']:04X}, want ${want_at:04X}")
check("window is 48 bytes", v["win_len"] == 48, f"got {v['win_len']}")
check("window contains all three vectors",
      all(v["win_at"] <= x < v["win_at"] + 48
          for x in (v["vec_nmi"], v["vec_reset"], v["vec_irq"])))

# Check 6. `synthcart.rom_at` recomputes the file offset from the MMC3 bank map
# -- the fixed window is the last 16 KiB of PRG -- and reads the file. It shares
# no code with identity.py's `cpu + $10000 + hdr`, so the two agreeing means the
# two independent derivations meet.
want_hex = S.rom_at(rom, v["win_at"], length=48).hex()
check("the window's 48 bytes equal an independently computed read of the same "
      "CPU addresses", v["win_hex"] == want_hex,
      f"identity got {v['win_hex'][:32]}...\n         rom_at got  {want_hex[:32]}...")

# 7. ...which only means something because the fill cannot be periodic. Assert it
# rather than trust it: a pattern with a short period would make an off-by-16
# window indistinguishable from the right one, which is precisely the failure the
# window check exists to catch.
fill = S.prg_fill(131072)
check("the PRG fill is not periodic at 1, 2, 4, 8, 16 or 32 bytes",
      all(fill[i] != fill[i + p] for p in (1, 2, 4, 8, 16, 32) for i in range(0, 4000)))
check("the PRG fill is not all zeroes", len(set(fill[:4096])) > 16)
# ...and demonstrate the property directly: the window 16 bytes earlier must NOT
# read the same bytes.
check("a window read 16 bytes early reads different bytes",
      S.rom_at(rom, v["win_at"] - 16, length=48).hex() != v["win_hex"])

# =====================================================================================
# 8. THE ZERO-PRG REGRESSION. This is the check that did not exist.
# =====================================================================================
# `NES\x1a` followed by a PRG size of 0 -- which is what run.sh computed when its
# `head -c 1 | tail -c +5` read no byte at all. The old harness printed
# "nmi=$0000 reset=$0000 irq=$0000" and exited 0.
zero = S.make_rom(TMP / "zeroprg.nes", prg_units=0, chr_units=0,
                  header=bytes(S.MAGIC) + bytes((0, 0, 0x40, 0)) + bytes(8))
check("the zero-PRG image really does exist and really is tiny",
      zero.stat().st_size == 16, f"{zero.stat().st_size} bytes")
raises("a PRG length of 0 RAISES rather than reporting three zero vectors",
       lambda: I.identity(zero), must_say=("PRG", "0 is what a failed read"))

# 9. A file that begins with the magic and stops. Header bytes 4 and 5 are simply
# not there; reading them anyway is how a length becomes 0.
stub = TMP / "stub.nes"
stub.write_bytes(S.MAGIC)
raises("a file that cannot hold its own header RAISES",
       lambda: I.identity(stub), must_say=("header", "16"))

# 10. A header that declares 128 KiB of PRG over a 40-byte file. Reading on would
# put the vectors in whatever bytes happened to be there.
short = S.make_rom(TMP / "short.nes", truncate=40)
raises("a header declaring more PRG than the file holds RAISES",
       lambda: I.identity(short), must_say=("bytes", "header declares"))

# =====================================================================================
# 11. CHR = 0 means CHR-RAM, not "no graphics". It is legal.
# =====================================================================================
ram = S.make_rom(TMP / "chrram.nes", chr_units=0)
vr = I.identity(ram)
check("CHR = 0 gives chr_len 0 and does NOT raise",
      vr["chr_len"] == 0, f"got {vr['chr_len']}")
check("a CHR-RAM image is exactly 16 + PRG bytes",
      ram.stat().st_size == 16 + 131072, f"{ram.stat().st_size}")
check("the vectors still read correctly out of a CHR-RAM image",
      (vr["vec_nmi"], vr["vec_reset"], vr["vec_irq"])
      == (0xE0A1, 0xE0B9, 0xE0A9))

# 12. A vector outside the fixed window. On MMC3 with 128 KiB of PRG all three
# live in $C000-$FFFF, so anything else means the arithmetic is wrong.
lo = S.make_rom(TMP / "lowvec.nes", vectors=(0x8000, 0xE0B9, 0xE0A9))
raises("a vector outside $C000-$FFFF RAISES", lambda: I.identity(lo),
       must_say=("$8000", "MMC3"))

# 13. A window too small to hold all three. identity.py would otherwise happily
# read 48 bytes that do not include the reset vector and call it an identity check.
wide = S.make_rom(TMP / "widevec.nes", vectors=(0xE0A1, 0xFFFF, 0xE0A9))
raises("a 48-byte window that cannot contain all three vectors RAISES",
       lambda: I.identity(wide), must_say=("does not", "reset"))

# 14. The battery bit, per dump, from header byte 6 bit 1. Beta 1's is $40 --
# CLEAR -- so nothing in NES/SaveRAM/ can affect a run against it. The release's
# is $42 and set. Deriving it is the only reason `NES/SaveRAM/` is off-limits
# rather than wiped.
nb = S.make_rom(TMP / "nobatt.nes", f6=S.F6_NO_BATTERY)
bt = S.make_rom(TMP / "batt.nes", f6=S.F6_BATTERY)
check("header byte 6 $40 -> battery CLEAR", I.identity(nb)["battery"] == 0)
check("header byte 6 $42 -> battery SET", I.identity(bt)["battery"] == 1)
check("header byte 6 is read, not inferred from the PRG size",
      I.identity(nb)["prg_len"] == I.identity(bt)["prg_len"])

# 15. run.sh reads the CLI's KEY=VALUE lines. If a key were dropped from `render`,
# run.sh would start with an empty variable for it -- and its own `[ -z ]` guard
# exists precisely so that is a loud failure rather than a silent one. This checks
# the render side; `test_run_sh.py` checks the shell side.
text = I.render(v)
back = I.parse_rendered(text)
check("render/parse_rendered round-trips every key identity() produces",
      set(back) == set(v) and all(back[k] == str(v[k]) for k in v),
      f"rendered {sorted(back)}\n         identity has {sorted(v)}")
check("the window hex survives the round trip with its full length",
      len(back["win_hex"]) == 96, f"{len(back['win_hex'])} characters")

# =====================================================================================
# 16. The CLI. Two claims: a bad ROM exits non-zero *and* says so on stderr, and a
#     good ROM says nothing at all under --quiet.
# =====================================================================================
env = dict(os.environ, PYTHONPATH="")
p = subprocess.run([sys.executable, str(ROOT / "tools/bizhawk/identity.py"), str(zero)],
                   capture_output=True, text=True)
check("identity.py exits 2 on a ROM it cannot identify",
      p.returncode == 2, f"exit {p.returncode}")
check("identity.py prints NOTHING to stdout for a bad ROM",
      p.stdout == "", repr(p.stdout[:200]))
check("identity.py explains itself on stderr", "PRG" in p.stderr, repr(p.stderr[:200]))

q = subprocess.run([sys.executable, str(ROOT / "tools/bizhawk/identity.py"),
                    str(rom), "--quiet"], capture_output=True, text=True)
check("--quiet exits 0 and prints nothing", q.returncode == 0 and q.stdout == "",
      f"exit {q.returncode}, stdout {q.stdout[:120]!r}")

# The save stem: BizHawk names its save after the ROM's filename, so this is the
# name a stale NES/SaveRAM entry would have -- and NES/SaveRAM is only ever *read*.
dotted = S.make_rom(TMP / "a.b.nes")
check("the save stem drops only the last extension",
      I.identity(dotted)["save_stem"] == "a.b", I.identity(dotted)["save_stem"])

print(f"identity: {ok} checks")
print("all checks passed")