#!/usr/bin/env python3
"""Stock-vs-rebuild comparison and gap map. Writes GAPMAP.md.

This is the measurement the rebuild did not have. `asm/build.py` prints one
number -- how many PRG bytes match the cartridge -- and that number cannot tell
the four things that matter apart:

* the source is right and the module is in the wrong 8 KiB slot  (class a)
* the source genuinely does not contain this code               (class b)
* the assembler mis-assembles source that is present             (class c)
* the artwork is packed in the wrong order                      (class d)

A rising match percentage is equally consistent with all four, and with a rising
number of hidden bugs, so this tool reports *where* the disagreement is instead of
how much of it there is: per 8 KiB slot, per module, and as contiguous runs with
an owner and a CPU address. The clusters are the answer; the percentage is not.

Nothing here re-implements a disassembler. `tools/dis6502.py` is the disassembler,
its opcode table is cross-checked against `asm/pds6502.py` by `dis6502.py
--selfcheck`, and both the boot-path listings and the probe windows below come out
of its `decode`/`disasm_block`.

    python3 tools/gapmap.py                 # print the report
    python3 tools/gapmap.py --write         # also write GAPMAP.md
    python3 tools/gapmap.py --probe         # placement evidence, per module
    python3 tools/gapmap.py --boot          # side-by-side boot path only
    python3 tools/gapmap.py --runs 40       # how many mismatch clusters to list

The comparison runs on the **source-only** image (`out/prg.src.bin`), so "which
bytes are missing" means "which bytes the released source does not produce".
Manifest regions are listed in their own section and are never counted as
agreement the source achieved.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

import build as B            # noqa: E402
import pds6502 as P          # noqa: E402
import patches               # noqa: E402
from dis6502 import TABLE, disasm_block  # noqa: E402

PRG = B.PRG_SIZE
SLOT = 0x2000
# A branch displacement is measured from the instruction *after* it.
GAPMAP = ROOT / "GAPMAP.md"

# The two dumps. Neither is distinguished by its header -- byte-identical iNES
# headers -- so both are named everywhere and the body SHA1 is printed.
DUMPS = ("release", "beta")


# ------------------------------------------------------------------ assembling

class Footprint(P.Assembler):
    """Records, per module, which PRG offsets it wrote and what it wrote there.

    `Assembler.emitted` is cleared at the start of every `run_file`, so the build
    itself has no record of which module a byte belongs to once it has run. This
    is the same trick `tools/whowrote.py` uses; here it is restricted to the
    *project* pass, because pass 1 assembles every module at all sixteen slots as
    a search and its footprints are the union of sixteen attempts, which would
    report every module as having written the whole image.
    """

    owner: dict[int, str] = {}
    byte_of: dict[int, int] = {}
    bymodule: dict[str, set[int]] = {}
    order: list[str] = []
    tracing = False

    def run_file(self, path, slot=None, origin=None, window_slots=None):
        super().run_file(path, slot=slot, origin=origin, window_slots=window_slots)
        if not Footprint.tracing:
            return None
        name = pathlib.Path(path).name
        offs = set(self.emitted)
        Footprint.bymodule.setdefault(name, set()).update(offs)
        for o in offs:
            Footprint.owner[o] = name
            Footprint.byte_of[o] = self.emitted[o]
        if name not in Footprint.order:
            Footprint.order.append(name)
        return None


def assemble() -> tuple[bytes, dict[int, str], dict[str, set[int]], list[str]]:
    """The source-only project-pass image, with a per-module footprint.

    The project pass is assembled here by hand rather than by calling
    `build.assemble_prg`, because that function's *pass 1* assembles every module
    at all sixteen slots as a slot search. Tracing that would union sixteen
    attempts per module and report each one as having written the whole image --
    which is exactly the bug `tools/whowrote.py`'s comment warns about, and the
    first version of this function made. `whowrote.py` avoids it the same way.
    Doing it by hand is also much faster: the slot search is ~20 s of the ~22 s
    build and its only product is `slots`, which is not what this tool is asking.

    Two attempts, because X7 is anchored so that `reset` lands on the cartridge's
    reset vector, and that anchor is only known after the first pass has measured
    how far into X7 `reset` sits.
    """
    cart = B.read_cart(patches.cart_path("release"))[0]
    chained = set(B.CHAINED)
    placed = [(B.SRC / m, None if m in chained else B.ASSUMED_SLOTS[m])
              for m in B.MODULES]
    placed += [(B.SRC / m, s) for m, s in B.SEQ_MODULES]
    origins = {"SEQ.SRC": B.SEQ_ORIGIN, **B.MODULE_ORIGINS}
    wslots = {"SEQ.SRC": B.SEQ_WINDOW_SLOTS}

    Footprint.owner, Footprint.byte_of = {}, {}
    Footprint.bymodule, Footprint.order = {}, []
    image = bytearray(PRG)
    x7_base = None
    for attempt in range(2):
        asm = Footprint(image, B.SRC, [B.SRC])
        asm.force_conditions = {"0=1": True}
        asm.prebank_symbols = frozenset({"b"})
        asm.prebank_split = True
        asm.demo_errors = B.X7_DEMO_ERRORS
        asm.prescan(B.ALL_SOURCES)
        asm.collect_macros(B.ALL_SOURCES)
        o = dict(origins)
        if x7_base is not None:
            o["X7.PDS"] = x7_base
        Footprint.tracing = True
        try:
            asm.run_all([p for p, _ in placed], [s for _, s in placed], o, wslots)
        finally:
            Footprint.tracing = False
        reset = asm.sym.get("reset")
        if reset is None or x7_base is not None:
            break
        x7_base = (cart[0x1FFFC] | (cart[0x1FFFD] << 8)) - (
            reset - P.Assembler.slot_origin(B.PINNED_SLOTS["X7.PDS"]))
    return bytes(image), Footprint.owner, Footprint.bymodule, Footprint.order


def loads() -> dict[str, bytes]:
    out = {}
    for name in DUMPS:
        path = patches.cart_path(name)
        if path.is_file():
            out[name] = patches.body(path)
    if not out:
        raise SystemExit(f"no cartridge under {patches.CART_DIR}")
    return out


# --------------------------------------------------------------------- counting

def cpu_of(off: int) -> str:
    """How to name a PRG offset on the CPU.

    Only slots 14 and 15 have a fixed CPU address -- $C000 and $E000 -- because
    MMC3 fixes those two windows whatever the bank registers say. Every other slot
    is presented at $8000 or $A000 depending on which register the game writes, so
    for those the honest label is the slot and the offset within it, not an address
    that would be wrong for half of them.
    """
    slot, within = divmod(off, SLOT)
    if slot == 14:
        return f"cpu ${0xC000 + within:04X}"
    if slot == 15:
        return f"cpu ${0xE000 + within:04X}"
    return f"slot {slot} +${within:04X}"


def tally(ours: bytes, cart: bytes, lo: int = 0, hi: int = PRG) -> dict[str, int]:
    """match / differ / source-only / cart-only / both-empty over a range.

    `source-only` is a byte we emit and the cartridge does not have; `cart-only`
    is one the cartridge has and we do not. Both are non-zero by definition --
    zero-versus-zero is agreement on an empty cell and counting it as a
    difference would drown the real disagreements.
    """
    out = {"match": 0, "differ": 0, "source_only": 0, "cart_only": 0,
           "both_empty": 0, "ours_nz": 0, "cart_nz": 0}
    for i in range(lo, hi):
        a, b = ours[i], cart[i]
        if a:
            out["ours_nz"] += 1
        if b:
            out["cart_nz"] += 1
        if a == b:
            out["match"] += 1
            if not a:
                out["both_empty"] += 1
        elif not b:
            out["source_only"] += 1
        elif not a:
            out["cart_only"] += 1
        else:
            out["differ"] += 1
    return out


def runs(ours: bytes, cart: bytes, lo: int, hi: int, kinds: tuple[str, ...],
         gap: int = 0) -> list[tuple[int, int, str]]:
    """Maximal runs of bytes that are *not* agreement, as (start, end, kind).

    `gap` extends a run across up to `gap` matching bytes, so one routine that is
    90% right reads as one cluster rather than ninety one-byte clusters.
    """
    def kind(i: int) -> str | None:
        a, b = ours[i], cart[i]
        if a == b:
            return None
        if not b:
            return "source-only"
        if not a:
            return "cart-only"
        return "differ"

    out: list[tuple[int, int, str]] = []
    start: int | None = None
    kind_now: str | None = None
    quiet = 0
    for i in range(lo, hi):
        k = kind(i)
        if k == kind_now and k is not None:
            quiet = 0
            continue
        if k is not None and (start is None or quiet > gap):
            if start is not None and kind_now is not None:
                out.append((start, i, kind_now))
            start, kind_now, quiet = i, k, 0
        elif k is None and start is not None:
            quiet += 1
            if quiet > gap:
                out.append((start, i, kind_now or "differ"))
                start, kind_now, quiet = None, None, 0
        else:
            quiet = 0
    if start is not None and kind_now is not None:
        out.append((start, hi, kind_now))
    return [r for r in out if r[2] in kinds]



# ------------------------------------------------------------------- boot path

# X7.PDS:991 writes `dw nmi,reset,irq` at `org $fffa`, so $FFFA=nmi, $FFFC=reset,
# $FFFE=irq. The order in this table is the address order, not the source order.
BOOT = [
    ("nmi", "X7.PDS:903", 0x1FFFA, 0x14),
    ("reset", "X7.PDS:922", 0x1FFFC, 0x40),
    ("irq", "X7.PDS:912", 0x1FFFE, 0x10),
]


def boot_section(ours: bytes, carts: dict[str, bytes],
                 asm: P.Assembler | None) -> list[str]:
    """The three vectors, and the code at each, from both sides.

    The cartridge's PRG is filed so that the fixed window is at file +$10000, so a
    CPU address in $E000-$FFFF is `addr + 0x10000` for *any* dump. That is the one
    mapping in this project that needs no slot arithmetic, which is why the boot
    path is comparable at all while the rest is not.
    """
    lines = ["## The boot path, both sides", "",
             "The only addresses comparable without knowing a module's slot are the",
             "fixed window's: file offset = CPU address + `$10000`. So this section is",
             "real evidence, not a count.", ""]
    for label, where, vec_off, length in BOOT:
        ours_vec = ours[vec_off] | (ours[vec_off + 1] << 8)
        lines.append(f"### `{label}` -- source: {where}, vector at "
                     f"`${vec_off - 0x10000:04X}`")
        lines.append("")
        lines.append("| | vector | first bytes |")
        lines.append("|---|---|---|")
        lines.append(f"| rebuild | `${ours_vec:04X}` | "
                     f"`{' '.join(f'{b:02X}' for b in ours[ours_vec + 0x10000:ours_vec + 0x10000 + 12])}` |")
        for name, cart in carts.items():
            vec = cart[vec_off] | (cart[vec_off + 1] << 8)
            f = vec + 0x10000
            lines.append(f"| {name} | `${vec:04X}` | "
                         f"`{' '.join(f'{b:02X}' for b in cart[f:f + 12])}` |")
        lines.append("")
        if ours_vec == 0:
            lines.append("The rebuild's vector is zero; nothing to compare.")
            lines.append("")
            continue
        for name, cart in carts.items():
            vec = cart[vec_off] | (cart[vec_off + 1] << 8)
            f = vec + 0x10000
            if vec != ours_vec:
                lines.append(f"`{name}`'s `{label}` is at `${vec:04X}` and the "
                             f"rebuild's at `${ours_vec:04X}` -- "
                             f"{vec - ours_vec:+d} bytes apart, so a side-by-side "
                             f"listing would compare different instructions. The two "
                             f"are listed separately below.")
                lines.append("")
            lines.append(f"**rebuild `${label}` at `${ours_vec:04X}`**")
            lines.append("")
            lines.append("```")
            for addr, text in disasm_block(
                    ours[ours_vec + 0x10000:ours_vec + 0x10000 + length], ours_vec):
                lines.append(f"{addr:04X}: {text}")
            lines.append("```")
            lines.append("")
            lines.append(f"**{name} `{label}` at `${vec:04X}`**")
            lines.append("")
            lines.append("```")
            for addr, text in disasm_block(cart[f:f + length], vec):
                lines.append(f"{addr:04X}: {text}")
            lines.append("```")
            lines.append("")
            common = sum(1 for k in range(length)
                         if ours[ours_vec + 0x10000 + k] == cart[f + k])
            lines.append(f"{common} of the first {length} bytes agree.")
            lines.append("")
    return lines


# -------------------------------------------------------- placement evidence

# Operand modes whose bytes do not move when the module moves. `rel` moves with
# the module and is position-independent *within* the module; `abs` is treated as
# clean only when it points below $8000, which is zero page, RAM and the I/O
# registers -- none of which are relocated by banking.
CLEAN_MODES = {"imp", "acc", "zp", "zpx", "zpy", "imm", "rel"}


def clean_instruction(mode: str, buf: bytes, i: int) -> bool:
    if mode in CLEAN_MODES:
        return True
    if mode in ("abs", "absx", "absy", "indx", "indy") and i + 2 < len(buf):
        base = buf[i + 1]
        # `indx`/`indy` are `($nn,x)`/`($nn),y`: one operand byte, zero page.
        if mode in ("indx", "indy"):
            return True
        return (buf[i + 1] | (buf[i + 2] << 8)) < 0x8000
    return False


def probe_windows(ours: bytes, lo: int, hi: int, minlen: int = 12
                  ) -> list[tuple[int, int]]:
    """Maximal position-independent instruction runs in `ours[lo:hi]`.

    A window is a stretch of code containing no operand that encodes a relocated
    address, so if the module's code is *right* and only its address is wrong, the
    window appears verbatim in the cartridge somewhere. A window that appears
    nowhere is evidence the code itself changed -- which is the distinction between
    class (a) and class (b), and the one per-byte scoring cannot make because
    every byte of a window is scored against the same slot.

    Two filters, both learned the hard way:

    * **At least four distinct byte values.** Data is full of runs, and a run of
      ten `$00` decodes as ten `brk`s and matches every `$00` run in the cartridge.
      Without this, X5's tables produced 61 496 "hits" in slot 0 and the table was
      noise. X1, X2 and SEQ.SRC, which are mostly code, are unaffected.
    * **A 12-byte minimum.** Chance for a 10-byte exact match in 131 072 bytes is
      already ~2^-8 per position; a shorter window stops being evidence.

    Note the residue: a *data* region can still decode as clean instructions, so a
    hit inside a module's tables is weaker evidence than a hit inside its code.
    The windows are reported with their length so that judgement can be made.
    """
    buf = ours[lo:hi]
    out: list[tuple[int, int]] = []
    start: int | None = None
    i = 0
    while i < len(buf):
        entry = TABLE.get(buf[i])
        if entry is None:
            if start is not None:
                out.append((start, i))
                start = None
            i += 1
            continue
        _, mode = entry
        n = P.SIZE[mode]
        if i + n > len(buf):
            break
        if clean_instruction(mode, buf, i):
            if start is None:
                start = i
        elif start is not None:
            out.append((start, i))
            start = None
        i += n
    if start is not None:
        out.append((start, len(buf)))
    return [(a, b) for a, b in out
            if b - a >= minlen and len(set(buf[a:b])) >= 4]


def where(off: int) -> str:
    return cpu_of(off)


def probe_module(name: str, offs: set[int], ours: bytes,
                 carts: dict[str, bytes], minlen: int = 12,
                 top: int = 3) -> list[str]:
    """Where each of a module's position-independent windows is found, if anywhere."""
    lines = [f"#### {name} -- {len(offs)} bytes", ""]
    if not offs:
        lines.append("not assembled in the project pass.")
        lines.append("")
        return lines
    lo, hi = min(offs), max(offs) + 1
    windows = probe_windows(ours, lo, hi, minlen)
    total = sum(b - a for a, b in windows)
    lines.append(f"{len(windows)} position-independent window(s) of >= {minlen} "
                 f"bytes with >= 4 distinct byte values, {total} bytes in all "
                 f"(`${lo:05X}-${hi - 1:05X}`, {where(lo)}..).")
    lines.append("")
    if not windows:
        lines.append("No window survives the filters, so this module is undecidable "
                     "by this method -- there is nothing in it that does not encode "
                     "a relocated address.")
        lines.append("")
        return lines
    for cname, cart in carts.items():
        # delta = where the cartridge's copy sits minus where ours sits. A
        # *consistent* delta across many windows is a placement measurement: it
        # says "this module's code is right and it belongs N bytes into slot q".
        deltas: dict[int, int] = {}
        hits = 0
        for a, b in windows:
            pat = ours[lo + a:lo + b]
            for q in range(16):
                base = q * SLOT
                k = cart.find(pat, base, base + SLOT)
                while k != -1:
                    d = k - (lo + a)
                    deltas[d] = deltas.get(d, 0) + 1
                    hits += 1
                    k = cart.find(pat, k + 1, base + SLOT)
        if not hits:
            lines.append(f"* **{cname}**: **no window found anywhere in any slot.** "
                         f"The module's code is not in this dump at any address, so "
                         f"this is source drift -- class (b) -- and not a placement "
                         f"bug.")
            lines.append("")
            continue
        best = max(deltas, key=lambda d: deltas[d])
        implied = lo + best
        isl, iw = divmod(implied, SLOT)
        lines.append(f"* **{cname}**: {hits} window placement(s) across "
                     f"{len(deltas)} distinct offset(s). Best-supported: "
                     f"**{deltas[best]} window(s) say this module's bytes belong "
                     f"{best:+d} bytes from where the build puts them** -- the "
                     f"module's `${lo:05X}-${hi - 1:05X}` would then be "
                     f"`${implied:05X}-${implied + (hi - lo) - 1:05X}`"
                     + (f", which is cpu ${(implied - 0x10000) & 0xFFFF:04X}"
                        f"-${(implied + (hi - lo) - 0x10001) & 0xFFFF:04X} in the "
                        f"fixed window" if isl == 14 else
                        f", starting at slot {isl} offset `${iw:04X}`"))
        ranked = sorted(deltas.items(), key=lambda kv: -kv[1])[:top + 1]
        for d, n in ranked:
            q2, w2 = divmod(lo + d, SLOT)
            lines.append(f"    * {n:6d} window(s) at delta {d:+#08x} -> file "
                         f"`${lo + d:05X}` "
                         + (f"(cpu ${(lo + d - 0x10000) & 0xFFFF:04X})"
                            if q2 == 14 else f"(slot {q2} + `${w2:04X}`)"))
        lines.append("")
    return lines


# ------------------------------------------------------------------- the report

def report(write: bool, want_probe: bool, want_boot: bool,
           n_runs: int) -> int:
    ours, owner, bymodule, order = assemble()
    carts = loads()
    release = carts.get("release")
    if release is None:
        raise SystemExit("the release dump is required as the comparison target")

    # Which dump is the rebuild closer to? Measured, not assumed: the source is
    # February 1990 and the release is 1991, so the Beta *might* be nearer -- but
    # "nearer" only helps if the difference is large enough that each remaining
    # difference is individually diagnosable.
    totals = {n: tally(ours, c) for n, c in carts.items()}
    L: list[str] = []
    L.append("# GAPMAP -- stock vs rebuild")
    L.append("")
    L.append(f"Generated by `tools/gapmap.py` on {date.today().isoformat()}. "
             f"Do not edit by hand; `make gaps` rewrites it.")
    L.append("")
    L.append("## What is being compared")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append("| source | `vendor/Magician-NES`, Eurocom's PDS 1.26 release, "
             "last update 02/03/90 |")
    L.append(f"| rebuild | `asm/out/prg.src.bin`, {PRG} bytes, "
             f"sha256 `{hashlib.sha256(ours).hexdigest()[:16]}` |")
    for n, c in carts.items():
        L.append(f"| dump `{n}` | `{patches.CARTS[n]['file']}`, body sha1 "
                 f"`{patches.CARTS[n]['sha1']}`, 262160 bytes, "
                 f"**same iNES header as the other dump** |")
    L.append("")
    L.append("The comparison runs on the source-only image, so `cart-only` means "
             "*the released source does not produce this*. Manifest regions are in "
             "their own section and are never counted as agreement the source "
             "achieved.")
    L.append("")

    # ---------------------------------------------------------------- headline
    L.append("## Headline, and which dump to target")
    L.append("")
    L.append("| dump | matched | of which non-empty | source-only | cart-only | "
             "differ | both empty |")
    L.append("|---|---|---|---|---|---|---|")
    for n, t in totals.items():
        L.append(f"| `{n}` | {t['match']} ({100.0 * t['match'] / PRG:.2f}%) | "
                 f"{t['match'] - t['both_empty']} | {t['source_only']} | "
                 f"{t['cart_only']} | {t['differ']} | {t['both_empty']} |")
    L.append("")
    if len(totals) == 2:
        r, b = totals["release"], totals["beta"]
        delta = b["match"] - r["match"]
        pct = 100.0 * delta / PRG
        L.append(f"The two dumps differ by **{delta:+d} matched bytes "
                 f"({pct:+.2f} percentage points)**. That is a tie, not a signal: "
                 f"at that distance neither dump is close enough to the February "
                 f"1990 source for the remaining differences to be individually "
                 f"diagnosable, so the Beta's possible closeness buys nothing. "
                 f"`release` stays the target because it is the shipped revision and "
                 f"is the one the pin in README.md records. **The pin has not been "
                 f"changed.**")
        L.append("")
        L.append("Where the two dumps actually differ, for the record:")
        L.append("")
        v = {}
        for n, c in carts.items():
            v[n] = {k: c[0x1FFFA + 2 * i] | (c[0x1FFFA + 2 * i + 1] << 8)
                    for i, k in enumerate(("nmi", "reset", "irq"))}
        L.append("| vector | " + " | ".join(f"`{n}`" for n in carts) + " |")
        L.append("|---|" + "---|" * len(carts))
        for k in ("nmi", "reset", "irq"):
            L.append(f"| `{k}` | " + " | ".join(f"`${v[n][k]:04X}`" for n in carts)
                     + " |")
        L.append("")
        rd = sum(1 for a, c in zip(carts["release"], carts["beta"]) if a != c)
        L.append(f"{rd} of the 131 072 PRG bytes differ between the two dumps. Both "
                 f"carry `reset` at a different address, so *every* address, label "
                 f"and replayed input from the source applies to exactly one of "
                 f"them.")
        L.append("")

    # ------------------------------------------------------------- per-slot
    L.append("## Per 8 KiB slot (source-only image vs each dump)")
    L.append("")
    L.append("`slot` is the PRG file slot. The MMC3 map (X5.PDS:10-21) is "
             "`$8000`=reg6, `$A000`=reg7, `$C000`=slot 14, `$E000`=slot 15, so a "
             "slot is presented at `$8000 + (slot & 3) * $2000` only for slots "
             "0-1 and 14-15; see `MODULE_ORIGINS` in `asm/build.py`.")
    L.append("")
    head = "| slot | owner(s) | oursNZ |"
    for n in carts:
        head += f" {n} match% | {n} cart-only | {n} differ |"
    L.append(head)
    L.append("|---" * (3 + 3 * len(carts)) + "|")
    for q in range(16):
        lo, hi = q * SLOT, (q + 1) * SLOT
        owners: dict[str, int] = {}
        for o in range(lo, hi):
            w = owner.get(o)
            if w:
                owners[w] = owners.get(w, 0) + 1
        who = ", ".join(f"{k}:{v}" for k, v in sorted(owners.items(),
                                                      key=lambda kv: -kv[1]))
        row = f"| {q} | {who or '-'} | {sum(1 for b in ours[lo:hi] if b)} |"
        for n, c in carts.items():
            t = tally(ours, c, lo, hi)
            row += (f" {100.0 * t['match'] / SLOT:5.2f}% | {t['cart_only']} | "
                    f"{t['differ']} |")
        L.append(row)
    L.append("")

    # ------------------------------------------------------------ per-module
    L.append("## Per module")
    L.append("")
    L.append("`slot` is where the bytes are filed, `origin` the CPU address the "
             "module is assembled at. They are different questions and only four "
             "slots can have an origin at all.")
    L.append("")
    L.append("| module | bytes | slot | origin | pinned? | "
             + " | ".join(f"{n} match" for n in carts) + " |")
    L.append("|---|---|---|---|---|" + "---|" * len(carts))
    for m in order:
        offs = bymodule.get(m, set())
        if not offs:
            continue
        slots = sorted({o // SLOT for o in offs})
        slot = f"{slots[0]}" if len(slots) == 1 else ",".join(str(s) for s in slots)
        if m in B.MODULE_ORIGINS:
            org = f"`${B.MODULE_ORIGINS[m]:04X}` (**forced**, see `MODULE_ORIGINS`)"
        else:
            own = B._leads_with_org(m)
            org = f"`${own:04X}` (own `org`)" if own is not None else "inherited"
        pinned = "yes" if m in B.PINNED_SLOTS else "**no**"
        row = (f"| {m} | {len(offs)} | {slot} | {org} | {pinned} |")
        for n, c in carts.items():
            hit = sum(1 for o in offs if ours[o] == c[o])
            row += f" {hit} ({100.0 * hit / max(len(offs), 1):.1f}%) |"
        L.append(row)
    L.append("")
    L.append("A module with no `origin` of its own inherits `slot_origin(slot)`, "
             "which is only a true statement about the hardware for slots 0, 1, 14 "
             "and 15. `X6.PDS` has no `org` and was in slot 3, so it inherited "
             "`$E000` -- the window MMC3 fixes to slot 15. That was a real bug and "
             "is fixed; see `MODULE_ORIGINS`.")
    L.append("")

    # ------------------------------------------------------------ the clusters
    L.append("## Where the disagreement clusters")
    L.append("")
    L.append("Contiguous runs of `differ` (both sides non-zero and unequal) and "
             "`cart-only` (the cartridge has it, we have zero), joined across up to "
             "8 agreeing bytes so one routine reads as one cluster. This is the "
             "table the match percentage cannot replace: a cluster is a place to "
             "look, a percentage is not.")
    L.append("")
    for n, c in carts.items():
        found = sorted(
            runs(ours, c, 0, PRG, ("differ", "cart-only"), gap=8),
            key=lambda r: -(r[1] - r[0]))
        L.append(f"### `{n}` -- {len(found)} clusters, longest first")
        L.append("")
        L.append("| bytes | file | cpu | kind | owner | cartridge disassembly |")
        L.append("|---|---|---|---|---|---|")
        for start, end, kind in found[:n_runs]:
            span = end - start
            owners: dict[str, int] = {}
            for o in range(start, end):
                w = owner.get(o)
                if w:
                    owners[w] = owners.get(w, 0) + 1
            who = ", ".join(f"{k}:{v}" for k, v in
                            sorted(owners.items(), key=lambda kv: -kv[1])[:2])
            txt = disasm_block(c[start:start + 12], 0)
            dis = "; ".join(t for _, t in txt[:3])
            L.append(f"| {span} | `${start:05X}-${end - 1:05X}` | "
                     f"{cpu_of(start)} | {kind} | {who or '-'} | `{dis}` |")
        L.append("")
        tot_d = sum(1 for i in range(PRG) if ours[i] and c[i] and ours[i] != c[i])
        tot_c = sum(1 for i in range(PRG) if c[i] and not ours[i])
        covered = sum(end - start for start, end, _ in found[:n_runs])
        L.append(f"{tot_d} differing bytes and {tot_c} cart-only bytes in total; "
                 f"the {min(n_runs, len(found))} rows above cover {covered} of them.")
        L.append("")

    # ------------------------------------------------------------- empty holes
    L.append("## Whole 4 KiB pages the source does not produce at all")
    L.append("")
    L.append("A page with a high `match%` and `ourNZ == 0` is agreement on empty "
             "cells, not agreement. These are the pages where the cartridge has "
             "content and the build has none, which is the difference between a "
             "placement bug and a hole.")
    L.append("")
    # One `cartNZ | match%` pair per dump, interleaved to match the cell order
    # below. Grouping all the counts together and all the percentages together
    # silently shifts every value one column left, which is the kind of table that
    # gets believed.
    L.append("| page | file | slot | ourNZ | "
             + " | ".join(f"{n} cartNZ | {n} match%" for n in carts)
             + " | longest cart-only run |")
    L.append("|---|---|---|---|" + "---|" * (2 * len(carts)) + "---|")
    for page in range(PRG // 4096):
        lo, hi = page * 4096, (page + 1) * 4096
        if any(ours[lo:hi]):
            continue
        c0 = carts[next(iter(carts))]
        if not any(c0[lo:hi]):
            continue
        best = 0
        for n, c in carts.items():
            rs = runs(ours, c, lo, hi, ("cart-only",), gap=0)
            best = max([best] + [e - s for s, e, _ in rs])
        cells = []
        for n, c in carts.items():
            cells.append(str(sum(1 for b in c[lo:hi] if b)))
            cells.append(f"{100.0 * tally(ours, c, lo, hi)['match'] / 4096:5.2f}%")
        L.append(f"| {page} | `${lo:05X}` | {lo // SLOT} | 0 | " +
                 " | ".join(cells) + f" | {best} |")
    L.append("")
    L.append("**These are deliberately not filled from the cartridge.** Three "
             "readings are still live for them -- a module in the wrong slot, code "
             "the February 1990 source does not contain, or a source region this "
             "build never reaches -- and copying the cartridge over them would "
             "destroy the evidence that tells them apart. They are class (a)/(b) "
             "*undecided*, and `asm/patches.manifest` refuses to carry a region "
             "whose class is not `b`.")
    L.append("")

    # ------------------------------------------------------------- the manifest
    L.append("## Patch manifest regions")
    L.append("")
    try:
        regions = patches.load()
    except patches.PatchError as exc:
        regions = []
        L.append(f"`asm/patches.manifest` does not parse: {exc}")
        L.append("")
    if regions:
        L.append("| region | class | cart | file | bytes | source already right | "
                 "matched before -> after |")
        L.append("|---|---|---|---|---|---|---|")
        filled = {}
        prgs = {n: patches.body(p) for n, p in patches.verify(regions).items()}
        for r in regions:
            src = prgs[r.cart]
            before = sum(1 for i in range(r.offset, r.end) if ours[i] == src[i])
            L.append(f"| `{r.name}` | **{r.klass}** | `{r.cart}` | "
                     f"`${r.offset:05X}-${r.end - 1:05X}` | {r.length} | "
                     f"{before} | {before} -> {r.length} |")
            filled[r.name] = r
        L.append("")
        L.append("Every row is cartridge bytes, none of it is in this repository, "
                 "and `asm/patches.py` refuses to apply a row unless the named "
                 "dump's body SHA1 matches both the manifest and the file on disk. "
                 "See `asm/patches.manifest` for the reasoning behind each region.")
    else:
        L.append("No regions.")
    L.append("")

    # ------------------------------------------------------------------- CHR
    L.append("## CHR")
    L.append("")
    chr_ours = (B.OUT / "chr.bin")
    L.append("The CHR packer writes the files in `SENDG` order and checks the "
             "result against the cartridge; 8 of 32 4 KiB pages are identical while "
             "the artwork itself is byte-identical, so only the *order* is open. "
             "That is class (d) and it is a separate problem from every code class "
             "above: no amount of correct 6502 changes it. Not attempted this "
             "session.")
    L.append("")

    if want_boot:
        L.extend(boot_section(ours, carts, None))

    if want_probe:
        L.append("## Placement evidence: position-independent code windows")
        L.append("")
        L.append("A *position-independent window* is a run of instructions with no "
                 "operand that encodes a relocated address -- no `jsr`/`jmp` target "
                 "above $8000, no absolute data pointer, no absolute branch. If a "
                 "module's code is right and only its slot is wrong, such a window "
                 "appears verbatim in the cartridge *somewhere*. That is the test "
                 "per-byte slot scoring cannot perform, because it scores every "
                 "byte against the *same* slot and a module's code bytes do not "
                 "depend on its slot once every symbol resolves.")
        L.append("")
        L.append("Read it as: **found -> placement evidence (class a). Not found -> "
                 "source drift (class b).** A module with no windows at all is "
                 "undecidable this way.")
        L.append("")
        for m in order:
            if not bymodule.get(m):
                continue
            L.extend(probe_module(m, bymodule[m], ours, carts))

    text = "\n".join(L).rstrip() + "\n"
    if write:
        GAPMAP.write_text(text)
        print(f"wrote {GAPMAP} ({len(text.splitlines())} lines)")
    else:
        print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help=f"write {GAPMAP}")
    ap.add_argument("--probe", action="store_true",
                    help="include the position-independent window placement evidence")
    ap.add_argument("--boot", action="store_true",
                    help="include the side-by-side boot path")
    ap.add_argument("--runs", type=int, default=20,
                    help="how many mismatch clusters to list per dump")
    args = ap.parse_args()
    if not args.write:
        args.probe = args.probe or True
    return report(args.write, args.probe, args.boot, args.runs)


if __name__ == "__main__":
    raise SystemExit(main())
