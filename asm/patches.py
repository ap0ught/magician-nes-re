#!/usr/bin/env python3
"""Apply the declarative patch manifest to the rebuilt PRG image.

Why this exists
---------------
The released source is a February 1990 development build and the cartridge went
out in February 1991. Some of what the cartridge contains is therefore *not in
the source at all*, and the only honest way to close those gaps is to take the
bytes from the cartridge and say so.

The constraint that makes this delicate is in `LEGAL.md` and `README.md`: **the
cartridge is never committed.** So "mark the borrowed code in the repo" cannot
mean "put the bytes in the repo". It means this:

* `asm/patches.manifest` -- a committed, line-oriented, human-readable file that
  names every filled range: where it goes, how long it is, which dump it came
  from, that dump's digest, and *why* (a classification code plus free text).
* This module -- a build-time applier that reads the manifest, resolves the named
  dump, **verifies its digest**, and copies the bytes in.

There is no hex blob, no `.incbin` of the cartridge, no base64 and no hand-typed
machine code anywhere in the repository. The manifest is a claim about the
cartridge; this module is the only thing that turns the claim into bytes, and it
refuses to do so unless the cartridge on disk is the one the manifest names.

The digest check is not decoration
----------------------------------
Two dumps of this title sit on this machine with **byte-identical iNES headers**
-- same mapper, same 128 KiB PRG, same 128 KiB CHR, same mirroring -- and
different bodies. Nothing in a header can tell them apart, and a manifest entry
that said only "the cartridge" could be applied to either without complaint. So
every region names both a dump *name* and that dump's body SHA1, both are
checked, and **one mismatch anywhere fails the whole application**: a half-patched
image is worse than an unpatched one, because the accounting would then describe
bytes that came from two different places.

Byte accounting
---------------
The build has to be able to say, on every run, how many matching bytes came from
the source and how many from here. Otherwise a rising match percentage is
indistinguishable from a rising number of hidden bugs. `apply()` returns a
`Report` with, per region, the byte count and how many of those bytes the source
had *already* got right -- so a region that merely confirms the source is
distinguishable on the log from one that hides a real difference.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "asm" / "out"

# Where the dumps live. The cartridge is never committed, so this is a path on
# the user's machine and nothing else; `--cart-dir` overrides it.
CART_DIR = pathlib.Path("/extdrive/backups/SHARE/roms/nes")

# The known dumps, by the name a manifest region uses. Both are 262160 bytes with
# the same 16-byte header; only the body differs, and the body SHA1 is the only
# thing that tells them apart. `sha1` is over the body -- header stripped -- which
# is what the digests in README.md and PROVENANCE.md section 1 record.
CARTS: dict[str, dict[str, object]] = {
    "release": {
        "file": "Magician (USA).nes",
        "sha1": "bd806d7f7c318b8012433250ca10aa8387a962bb",
    },
    "beta": {
        "file": "Magician (USA) (Beta).nes",
        "sha1": "2a0a444dae8b5b02f4e5f1b789e16356f3ab08f0",
    },
}

# The classification every region must carry. These are the four cases from
# `README.md`'s "A gap is not one thing", and the point of recording one is that
# the four need opposite treatment:
#
#   a  placement bug      the source is right and the module is in the wrong
#                         8 KiB slot.  FIX THE PLACEMENT. Copying the cartridge
#                         over it destroys the only evidence of the bug.
#   b  absent from source the released source is older/smaller than the cartridge.
#                         This is the only class that may be filled from a cart.
#   c  source bug         the source is present but the assembler mis-assembles
#                         it.  FIX THE ASSEMBLER.
#   d  data / CHR         artwork packing order.  A separate problem from code.
CLASSES = {
    "a": "placement bug -- fix the slot, do NOT copy bytes",
    "b": "genuinely absent from the released source -- may be filled",
    "c": "assembler bug -- fix the assembler, do NOT copy bytes",
    "d": "data/CHR packing -- separate problem",
}

MANIFEST = pathlib.Path(__file__).resolve().parent / "patches.manifest"

# The only class `apply` will fill. Named here rather than written as the literal
# "b" at the two places that need it, so that adding a class to CLASSES cannot
# quietly make it fillable.
FILLABLE = "b"


class PatchError(Exception):
    """Anything that makes the manifest untrustworthy. Never downgraded."""


@dataclass
class Region:
    name: str
    klass: str = ""
    cart: str = ""
    sha1: str = ""
    offset: int = 0
    length: int = 0
    cpu: int | None = None
    reason: list[str] = field(default_factory=list)

    @property
    def end(self) -> int:
        return self.offset + self.length

    def describe(self) -> str:
        where = f"file ${self.offset:05X}-${self.end - 1:05X}"
        if self.cpu is not None:
            where += f" (cpu ${self.cpu:04X}-${self.cpu + self.length - 1:04X})"
        return f"{self.name}: class {self.klass}, {self.length} bytes, {where}, from {self.cart}"


@dataclass
class RegionResult:
    region: Region
    already_correct: int = 0
    changed: int = 0
    matched_before: int = 0
    matched_after: int = 0


@dataclass
class Report:
    applied: list[RegionResult] = field(default_factory=list)
    cart_dir: pathlib.Path = CART_DIR

    @property
    def regions(self) -> list[Region]:
        return [r.region for r in self.applied]

    @property
    def offsets(self) -> set[int]:
        out: set[int] = set()
        for r in self.applied:
            out.update(range(r.region.offset, r.region.end))
        return out

    @property
    def total_bytes(self) -> int:
        return sum(r.region.length for r in self.applied)

    @property
    def matched_after(self) -> int:
        return sum(r.matched_after for r in self.applied)

    @property
    def matched_before(self) -> int:
        return sum(r.matched_before for r in self.applied)


# --------------------------------------------------------------------- parsing

def _int(tok: str, key: str, name: str) -> int:
    """A manifest number. Hexadecimal by default, `0x`/`0o`/`0b` honoured.

    Bare hex is the default because every number in this manifest is a file
    offset, a length or a CPU address, and the rest of the repository writes them
    that way (`$1FC40`, `file 0x1FC40`). `int(tok, 0)` rejects a bare `1FC40`,
    which is a confusing way to fail on a manifest that looks like hex
    everywhere else. Decimal is available as `0d1234`.
    """
    text = tok.strip()
    try:
        if text[:2].lower() == "0d":
            return int(text[2:], 10)
        if text[:2].lower() in ("0x", "0o", "0b"):
            return int(text, 0)
        if text and all(c in "0123456789abcdefABCDEF" for c in text):
            return int(text, 16)
        return int(text, 10)
    except ValueError as exc:
        raise PatchError(f"region {name}: {key} {tok!r} is not a number") from exc


def parse(text: str) -> list[Region]:
    regions: list[Region] = []
    cur: Region | None = None
    seen_version = False

    def close() -> None:
        nonlocal cur
        if cur is None:
            return
        missing = [k for k, v in (("class", cur.klass), ("cart", cur.cart),
                                  ("sha1", cur.sha1)) if not v]
        if missing:
            raise PatchError(f"region {cur.name}: missing required field(s) "
                             f"{', '.join(missing)}")
        if cur.klass not in CLASSES:
            raise PatchError(f"region {cur.name}: class {cur.klass!r} is not one of "
                             f"{', '.join(sorted(CLASSES))}")
        if cur.klass != FILLABLE:
            # The class rule, enforced. `CLASSES` says only b may be filled, and
            # GAPMAP.md repeats it as a fact about this file, but nothing here
            # checked: a class-a region parsed cleanly and `apply` filled it, so
            # the one control the manifest has is a comment.
            #
            # Rejecting it at parse time rather than at apply time is deliberate.
            # A class-a region is a *placement* bug, and the fill would be wrong
            # even if the address were right; it must never reach an image.
            raise PatchError(
                f"region {cur.name}: class {cur.klass!r} is "
                f"{CLASSES[cur.klass]} -- only class {FILLABLE!r} may be filled "
                f"from a cartridge. Fix the slot or the assembler; do not carry "
                f"the bytes.")
        if cur.cart not in CARTS:
            raise PatchError(f"region {cur.name}: cart {cur.cart!r} is not a known "
                             f"dump ({', '.join(sorted(CARTS))})")
        if cur.length <= 0:
            raise PatchError(f"region {cur.name}: length must be positive")
        if not cur.reason:
            raise PatchError(f"region {cur.name}: a reason is required -- an "
                             f"unexplained fill is indistinguishable from a mistake")
        want = str(CARTS[cur.cart]["sha1"]).lower()
        if cur.sha1.lower() != want:
            raise PatchError(
                f"region {cur.name}: declares sha1 {cur.sha1} for dump "
                f"{cur.cart!r}, whose recorded body sha1 is {want}. The manifest "
                f"and the dump registry disagree, so nothing can be trusted.")
        regions.append(cur)
        cur = None

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        key = key.lower()
        value = value.strip()
        if key == "version":
            if value != "1":
                raise PatchError(f"line {lineno}: manifest version {value!r} is not "
                                 f"understood (this applier reads version 1)")
            seen_version = True
        elif key == "region":
            close()
            if not value:
                raise PatchError(f"line {lineno}: `region` needs a name")
            cur = Region(name=value)
        elif key == "end":
            close()
        elif cur is None:
            raise PatchError(f"line {lineno}: {key!r} outside any `region` block")
        elif key == "class":
            cur.klass = value.lower()
        elif key == "cart":
            cur.cart = value.lower()
        elif key == "sha1":
            cur.sha1 = value.lower()
        elif key == "prg":
            parts = value.split()
            if len(parts) != 2:
                raise PatchError(f"region {cur.name}: `prg` wants an offset and a "
                                 f"length, got {value!r}")
            cur.offset = _int(parts[0], "prg offset", cur.name)
            cur.length = _int(parts[1], "prg length", cur.name)
        elif key == "cpu":
            cur.cpu = _int(value, "cpu", cur.name)
        elif key == "reason":
            cur.reason.append(value)
        else:
            raise PatchError(f"line {lineno}: unknown manifest key {key!r}")
    close()
    if not seen_version:
        raise PatchError("no `version` line: refusing to read a manifest whose "
                         "format this applier cannot check")
    for a, b in zip(regions, regions[1:]):
        if a.offset < b.end and b.offset < a.end:
            raise PatchError(f"regions {a.name} and {b.name} overlap "
                             f"(${a.offset:05X}-${a.end - 1:05X} and "
                             f"${b.offset:05X}-${b.end - 1:05X}); the accounting "
                             f"would be ambiguous")
    return regions


def load(path: pathlib.Path = MANIFEST) -> list[Region]:
    return parse(path.read_text())


# ------------------------------------------------------------------- resolving

def raw_body(path: pathlib.Path) -> bytes:
    """Everything after the 16-byte iNES header: PRG *and* CHR.

    This is what the digests recorded in README.md and PROVENANCE.md section 1
    are taken over -- `bd806d7f...` is the SHA1 of the 262 144 body bytes, not of
    the 131 072 PRG bytes. Hashing the PRG alone gives `b5bd2044...` for the same
    file, which is a different number for the same cartridge and would make every
    region look like a digest mismatch.
    """
    data = path.read_bytes()
    if data[:4] != b"NES\x1a":
        raise PatchError(f"{path} has no iNES header")
    prg_len, chr_len = data[4] * 16384, data[5] * 8192
    if len(data) - 16 < prg_len + chr_len:
        raise PatchError(f"{path} is short: header declares {prg_len + chr_len} "
                         f"body bytes, file has {len(data) - 16}")
    return data[16:16 + prg_len + chr_len]


def body(path: pathlib.Path) -> bytes:
    """The 128 KiB PRG image -- the part a manifest region can address."""
    return raw_body(path)[:path.read_bytes()[4] * 16384]


def digest_of(path: pathlib.Path) -> str:
    return hashlib.sha1(raw_body(path)).hexdigest()


def cart_path(name: str, cart_dir: pathlib.Path = CART_DIR) -> pathlib.Path:
    return cart_dir / str(CARTS[name]["file"])


def verify(regions: list[Region], cart_dir: pathlib.Path = CART_DIR
           ) -> dict[str, pathlib.Path]:
    """Check every named dump's digest. All of them, before any of them is used.

    Verifying lazily would let a manifest apply its first two regions from the
    release and its third from the Beta, which is precisely the failure the
    README warns about and which no header can catch.
    """
    resolved: dict[str, pathlib.Path] = {}
    problems: list[str] = []
    for name in sorted({r.cart for r in regions}):
        path = cart_path(name, cart_dir)
        if not path.is_file():
            problems.append(f"{name}: {path} is not there")
            continue
        want = str(CARTS[name]["sha1"]).lower()
        got = digest_of(path)
        if got != want:
            problems.append(f"{name}: {path} body sha1 is {got}, expected {want}")
            continue
        resolved[name] = path
    if problems:
        raise PatchError("refusing to apply the manifest; no region was filled:\n  "
                         + "\n  ".join(problems))
    return resolved


# ------------------------------------------------------------------- applying

def apply(image: bytearray, cart: bytes, regions: list[Region],
          cart_dir: pathlib.Path = CART_DIR) -> Report:
    """Fill `regions` of `image` from `cart`, which must be the named dump.

    Idempotent: applying twice produces the same image, which is what lets both
    `build.py` (for the byte accounting) and `mkrom.py` (for the ROM) call it.
    """
    if len(image) != len(cart):
        raise PatchError(f"image is {len(image)} bytes, the cartridge PRG is "
                         f"{len(cart)}")
    resolved = verify(regions, cart_dir)
    report = Report(cart_dir=cart_dir)
    for region in regions:
        # Belt and braces: `parse` already refuses a non-b class, but `apply`
        # is the thing that writes, so it checks the class it is about to act on
        # rather than trusting its caller to have gone through `parse`.
        if region.klass != FILLABLE:
            raise PatchError(
                f"region {region.name}: refusing to fill class {region.klass!r} "
                f"({CLASSES.get(region.klass, 'unknown class')}); only class "
                f"{FILLABLE!r} may be filled from a cartridge")
        if region.end > len(image):
            raise PatchError(f"region {region.name}: ${region.offset:05X}+"
                             f"{region.length} runs past the end of the "
                             f"{len(image)}-byte PRG")
        source = cart[region.offset:region.end]
        before = bytes(image[region.offset:region.end])
        already = sum(1 for a, b in zip(before, source) if a == b)
        matched_before = sum(1 for a, b in zip(before, cart[region.offset:region.end])
                             if a == b)
        image[region.offset:region.end] = source
        matched_after = sum(1 for a, b in zip(source, source) if a == b)
        report.applied.append(RegionResult(
            region=region,
            already_correct=already,
            changed=region.length - already,
            matched_before=matched_before,
            matched_after=matched_after,
        ))
    return report


def summarise(report: Report) -> str:
    if not report.applied:
        return "patch manifest: no regions"
    lines = [f"patch manifest: {len(report.applied)} region(s), "
             f"{report.total_bytes} bytes filled from the cartridge"]
    for r in report.applied:
        lines.append(f"  {r.region.describe()}")
        lines.append(f"      the source already had {r.already_correct} of these "
                     f"{r.region.length} bytes right; "
                     f"{r.changed} came from the cartridge")
        lines.append(f"      matched bytes before {r.matched_before} -> "
                     f"after {r.matched_after} "
                     f"(+{r.matched_after - r.matched_before})")
        for line in r.region.reason:
            lines.append(f"      why: {line}")
    return "\n".join(lines)


# ---------------------------------------------------------------------- driver

def patch_file(prg_path: pathlib.Path, manifest: pathlib.Path = MANIFEST,
               cart_dir: pathlib.Path = CART_DIR) -> Report:
    image = bytearray(prg_path.read_bytes())
    regions = load(manifest)
    prgs = {name: body(p) for name, p in verify(regions, cart_dir).items()}
    # Every region must name the same dump unless it says otherwise; a manifest
    # that mixes them is legitimate (the format allows it) but the report has to
    # make it obvious, so the per-region cart name is printed above.
    return apply(image, prgs[regions[0].cart] if len({r.cart for r in regions}) == 1
                 else _mixed(prgs, regions), regions, cart_dir)


def _mixed(prgs: dict[str, bytes], regions: list[Region]) -> bytes:
    """Build one image to measure against when regions name different dumps."""
    out = bytearray(max(len(p) for p in prgs.values()))
    for region in regions:
        out[region.offset:region.end] = prgs[region.cart][region.offset:region.end]
    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=pathlib.Path, default=MANIFEST)
    ap.add_argument("--cart-dir", type=pathlib.Path, default=CART_DIR)
    ap.add_argument("--list", action="store_true",
                    help="parse and print the manifest; touch no cartridge")
    args = ap.parse_args()

    regions = load(args.manifest)
    if args.list:
        print(f"{len(regions)} region(s) in {args.manifest}")
        for r in regions:
            print(f"  {r.describe()}")
            for line in r.reason:
                print(f"      why: {line}")
        return 0

    prg = OUT / "prg.bin"
    if not prg.is_file():
        raise SystemExit(f"{prg} does not exist; run `make assemble` first")
    image = bytearray(prg.read_bytes())
    prgs = {name: body(p) for name, p in verify(regions, args.cart_dir).items()}
    report = apply(image, _mixed(prgs, regions) if len(prgs) > 1
                   else prgs[regions[0].cart], regions, args.cart_dir)
    print(summarise(report))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PatchError as exc:
        print(f"patches: {exc}", file=sys.stderr)
        raise SystemExit(2)
