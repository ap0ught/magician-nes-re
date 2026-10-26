#!/usr/bin/env python3
"""Where does every SEQ.SRC table actually sit in the cartridge?

    python3 tools/seqtab.py [--cart PATH] [--slot 5] [--rebuilt PATH]

`journal/09` reported that the 32-byte ascending tile counter is at slot 5
offset `$1471` in the rebuild and `$14CE` in the cartridge, and concluded from
that one table that "the placement of X5 within slot 5 is wrong". Two things
were not checked: whether the table belongs to X5 at all, and whether the other
several hundred tables in the same module agree about the shift. One table gives
a displacement; a whole module's worth of tables gives an origin, and can also
show the displacement is not constant -- which would mean a length error inside
the module rather than a wrong `org`.

**The module is `SEQ.SRC`, not `X5`.** `asm/build.py` places X5 in slot 14
(`$C000`, its own `org`), X3 in slot 4, SEQ.SRC in slot 5, X7 in slot 15, and
gives slots 6..13 to x7's own `b = $N` data groups. The ascending table is
`SEQ.SRC:607`, label `D43`. So the question is SEQ.SRC's origin, not X5's.

## Coverage, and why it is asserted

A per-label table that silently searched the wrong range would print a clean
column of "not found" for every row and look like a result. So this tool:

  * refuses to run unless the cartridge PRG is exactly 131072 bytes and the
    rebuilt PRG is exactly 131072 bytes, and prints both lengths;
  * searches the **whole** 131072-byte PRG for every table, never a slot, and
    prints how many candidate hits each table had, so "one hit" and "forty
    coincidental hits" are distinguishable;
  * reports tables with 0 hits, tables with >1 hit, and tables it could not
    measure at all (a label with no symbol in `mag.sym`, or a zero/one-byte
    table that cannot be located meaningfully), and counts them;
  * exits non-zero if any label in `SEQ.SRC` went unmeasured.

A byte that is not in the cartridge is reported as such. It is not rounded to
"nearest hit" and it is not filled in from the rebuild.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cartref import DEFAULT_CART  # noqa: E402
DEFAULT_CART = DEFAULT_CART
SEQ = ROOT / "vendor" / "Magician-NES" / "SEQ.SRC"
SYM = ROOT / "asm" / "out" / "mag.sym"
PRG_LEN = 131072


def read_cart_prg(path: Path) -> tuple[bytes, str, bytes]:
    raw = path.read_bytes()
    if raw[:4] != b"NES\x1a":
        raise SystemExit(f"{path}: not an iNES file (magic {raw[:4]!r})")
    prg_units, chr_units = raw[4], raw[5]
    prg_len = prg_units * 16384
    body = raw[16:]
    if len(body) < prg_len + chr_units * 8192:
        raise SystemExit(f"{path}: body {len(body)} shorter than the header claims")
    return body[:prg_len], hashlib.sha1(body).hexdigest(), raw[:16]


def read_syms(path: Path) -> dict[str, int]:
    syms: dict[str, int] = {}
    for line in path.read_text().splitlines():
        if "=" not in line:
            continue
        name, _, val = line.partition("=")
        val = val.strip()
        if val.startswith("$"):
            try:
                syms[name.strip()] = int(val[1:], 16)
            except ValueError:
                pass
    return syms


def seq_labels() -> list[str]:
    """Labels SEQ.SRC defines, in source order.

    A label is the first whitespace-separated token of a line that is not a
    comment (`;;`), a blank, or a directive continuation (`HEX`/`DL`/`DH` on
    their own line).
    """
    out: list[str] = []
    for raw in SEQ.read_text(errors="replace").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith(";;"):
            continue
        tok = line.split()[0]
        if tok.upper() in ("HEX", "DL", "DH"):
            continue
        out.append(tok)
    return out


def find_all(hay: bytes, needle: bytes) -> list[int]:
    hits, i = [], hay.find(needle)
    while i >= 0:
        hits.append(i)
        i = hay.find(needle, i + 1)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cart", type=Path, default=DEFAULT_CART)
    ap.add_argument("--rebuilt", type=Path, default=ROOT / "asm" / "out" / "prg.bin")
    ap.add_argument("--sym", type=Path, default=SYM)
    ap.add_argument("--only-differing", action="store_true",
                    help="print only rows whose delta is not the modal delta")
    args = ap.parse_args()

    cart, body_sha1, header = read_cart_prg(args.cart)
    reb = args.rebuilt.read_bytes()
    if len(cart) != PRG_LEN:
        raise SystemExit(f"cartridge PRG is {len(cart)} bytes, expected {PRG_LEN}")
    if len(reb) != PRG_LEN:
        raise SystemExit(f"rebuilt PRG is {len(reb)} bytes, expected {PRG_LEN}")
    syms = read_syms(args.sym)
    labels = seq_labels()

    print(f"cartridge   {args.cart}")
    print(f"  header    {' '.join(f'{b:02x}' for b in header)}")
    print(f"  body sha1 {body_sha1}")
    print(f"  PRG       {len(cart)} bytes searched in full (all 16 slots)")
    print(f"rebuilt     {args.rebuilt}  {len(reb)} bytes")
    print(f"symbols     {args.sym}  {len(syms)} entries")
    print(f"SEQ.SRC     {len(labels)} labels parsed\n")

    # `asm/pds6502.py` lower-cases symbol names, so `ANIMTAB` and `D43` reach
    # `mag.sym` as `animtab` and `d43`. Looking the source spelling up as
    # written would report every one of the 497 labels unmeasurable, which is
    # the failure this tool is built to avoid confusing with a result.
    def addr_of(lab: str) -> int | None:
        return syms.get(lab.lower())

    rows = []
    unmeasured: list[tuple[str, str]] = []
    for i, lab in enumerate(labels):
        addr = addr_of(lab)
        if addr is None:
            unmeasured.append((lab, "no symbol in mag.sym"))
            continue
        # Length is the distance to the next label we can also locate. The
        # labels are in source order, so the next *measurable* label bounds
        # this one; anything unreachable is reported rather than guessed.
        nxt = None
        for j in range(i + 1, len(labels)):
            nxt = addr_of(labels[j])
            if nxt is not None:
                break
        if nxt is None:
            unmeasured.append((lab, "no following label with a symbol"))
            continue
        length = nxt - addr
        if length <= 0:
            unmeasured.append((lab, f"non-positive length {length}"))
            continue
        if addr < 0 or addr + length > PRG_LEN:
            unmeasured.append((lab, f"outside the PRG: ${addr:04X}+{length}"))
            continue
        blob = reb[addr:addr + length]
        # A table whose bytes are one repeated value cannot be located: every
        # 8 KiB slot boundary matches it. Say so rather than print a hit.
        if len(set(blob)) < 2:
            unmeasured.append((lab, f"{length} bytes, all {blob[0]:02X} -- unlocatable"))
            continue
        hits = find_all(cart, blob)
        rows.append((lab, addr, length, len(hits), hits, blob))

    # ---- the table
    print("label   rebuild addr  len   hits  cartridge hits (file offset / slot+off)"
          "   delta")
    for lab, addr, length, nhits, hits, blob in rows:
        if nhits == 1:
            h = hits[0]
            slot, so = h >> 13, h & 0x1FFF
            reb_slot, reb_so = addr >> 13, addr & 0x1FFF
            d = so - reb_so
            where = f"${h:07X} slot {slot} +${so:04X}"
            delta = f"{d:+#06x}" if slot == reb_slot else f"slot {slot} vs {reb_slot}"
        else:
            where = ", ".join(f"${h:07X}" for h in hits[:6]) + (" ..." if nhits > 6 else "")
            delta = "-"
        print(f"{lab:<6} ${addr:04X}     {length:5d}  {nhits:5d}  {where:<44} {delta}")

    # ---- the delta distribution: the point of the whole exercise
    deltas: dict[int, list[str]] = {}
    for lab, addr, length, nhits, hits, blob in rows:
        if nhits != 1:
            continue
        h = hits[0]
        if (h >> 13) != (addr >> 13):
            continue
        deltas.setdefault((h & 0x1FFF) - (addr & 0x1FFF), []).append(lab)
    print("\ndelta histogram (cartridge slot offset minus rebuild slot offset),"
          " tables with exactly one hit in the same slot:")
    for d in sorted(deltas, key=lambda k: -len(deltas[k])):
        print(f"  {d:+#07x}  {len(deltas[d]):4d} table(s)"
              + ("   <-- MODAL" if len(deltas[d]) == max(len(v) for v in deltas.values())
                 else ""))

    zero = [r for r in rows if r[3] == 0]
    multi = [r for r in rows if r[3] > 1]
    print(f"\ncoverage: {len(rows)} of {len(labels)} labels measured"
          f"  ({100.0*len(rows)/max(1,len(labels)):.1f}%)")
    print(f"  exactly one hit : {sum(1 for r in rows if r[3] == 1)}")
    print(f"  no hit at all   : {len(zero)}  {[r[0] for r in zero][:20]}")
    print(f"  several hits    : {len(multi)}  {[r[0] for r in multi][:20]}")
    print(f"  not measurable  : {len(unmeasured)}  {unmeasured[:20]}")
    if unmeasured:
        print("  FAIL: some labels were not measured, so this table is not complete.")
    return 1 if unmeasured else 0


if __name__ == "__main__":
    raise SystemExit(main())