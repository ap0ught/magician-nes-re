#!/usr/bin/env python3
"""Pre-flight for an FCEUX .fm2 TAS: prove the ROM is the one the movie was
recorded on, then turn the movie into the plain files drive.lua needs.

WHY THIS IS A SEPARATE PROGRAM AND NOT PART OF THE RUN
----------------------------------------------------
An .fm2 records INPUT, not state. All 44003 frame lines of FatRatKnight's
Magician run carry an 8-character button field and empty state columns, so there
is no per-frame checksum in the file and no emulator can tell you from the movie
alone whether the game behaved. What the file *does* pin is the cartridge: its
`romChecksum` is base64(MD5(PRG+CHR)), computed over the headerless cartridge
image, not over the file on disk.

That makes the identity check the one assertion in the whole pipeline that can
be made before emulating anything, and the one that must never be optional. Five
Magician dumps sit in this user's collection, all mapper 4, all 128 KiB PRG, four
of them the same release-under-a-different-name -- they are trivially confused
with each other, and a run against the wrong one produces a confident, plausible,
entirely meaningless number.

    python3 tools/tas/identity.py --movie M --rom R --out DIR
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Fields watched during the run, resolved through src/play/ram.py's symbol table
# so no address is written down in two places. See that module's docstring for why
# this project treats a hardcoded RAM address as a defect.
WATCH = ["phase", "newphase", "subphase", "curlev", "mapind", "hilev",
         "manacur", "food", "water", "wealth", "mclock"]


class Fail(SystemExit):
    def __init__(self, msg: str, code: int = 2):
        print(f"identity: FAIL -- {msg}", file=sys.stderr)
        super().__init__(code)


def parse_ines(data: bytes) -> dict:
    if data[:4] != b"NES\x1a":
        return {"error": "not an iNES image (bad magic)"}
    prg_banks, chr_banks = data[4], data[5]
    f6, f7 = data[6], data[7]
    return {
        "prg_bytes": prg_banks * 16384,
        "chr_bytes": chr_banks * 8192,
        "chr_is_ram": chr_banks == 0,
        "mapper": ((f7 & 0xF0) | (f6 >> 4)),
        "battery": bool(f6 & 0x02),
        "trainer": bool(f6 & 0x04),
        "four_screen": bool(f6 & 0x08),
        "prg": data[16:16 + prg_banks * 16384],
    }


def parse_movie(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="latin-1")
    head: dict[str, str] = {}
    subs: list[tuple[int, str]] = []
    fields: list[str] = []
    for line in text.splitlines():
        if line.startswith("|"):
            parts = line.split("|")
            fields.append(parts[2] if len(parts) > 2 else "")
            continue
        if line.startswith("subtitle "):
            m = re.match(r"subtitle\s+(\d+)\s+(.*)$", line)
            if m:
                subs.append((int(m.group(1)), m.group(2)))
            continue
        if " " in line:
            k, v = line.split(" ", 1)
            if k not in head:
                head[k] = v
    return {"header": head, "subtitles": subs, "fields": fields}


def load_watch() -> list[tuple[str, int]]:
    sys.path.insert(0, str(ROOT / "src" / "play"))
    try:
        import ram  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise Fail(f"cannot import src/play/ram.py ({e}). The watched RAM cells "
                   f"must come from the symbol table, not from a literal address "
                   f"written into a Lua file.") from e
    out = []
    for name in WATCH:
        try:
            out.append((name, ram.sym(name)))
        except KeyError as e:
            raise Fail(f"{e}") from e
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--movie", required=True, type=pathlib.Path)
    ap.add_argument("--rom", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path,
                    help="directory for inputs.txt, watch.txt, shots list, subtitles.srt")
    ap.add_argument("--shots", type=int, default=8,
                    help="how many subtitle anchors to screenshot (default 8)")
    args = ap.parse_args()

    for p, what in ((args.movie, "movie"), (args.rom, "ROM")):
        if not p.is_file():
            raise Fail(f"no {what} at {p}")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "shots").mkdir(exist_ok=True)

    movie = parse_movie(args.movie)
    head = movie["header"]
    fields = movie["fields"]
    if not fields:
        raise Fail(f"{args.movie} contains no frame lines; is it really an .fm2?")

    rom = args.rom.read_bytes()
    ines = parse_ines(rom)
    if "error" in ines:
        raise Fail(f"{args.rom}: {ines['error']}")

    print(f"movie            {args.movie.name}")
    print(f"  fm2 version     {head.get('version')}")
    print(f"  emuVersion      {head.get('emuVersion')}  "
          f"(FCEUX 2.1.0 -- the emulator this movie was recorded in)")
    print(f"  romFilename     {head.get('romFilename')}")
    print(f"  rerecordCount   {head.get('rerecordCount')}")
    print(f"  frames          {len(fields)}")
    print(f"  subtitles       {len(movie['subtitles'])}")
    if head.get("comment"):
        print(f"  comment         {head['comment']}")

    # ---- the identity gate -------------------------------------------------
    cksum = head.get("romChecksum", "")
    if not cksum.startswith("base64:"):
        raise Fail(f"romChecksum {cksum!r} is not the base64(MD5(PRG+CHR)) form this "
                   f"tool understands; refusing to guess at a checksum convention")
    want = base64.b64decode(cksum.split(":", 1)[1])
    got = hashlib.md5(ines["prg"] + rom[16 + ines["prg_bytes"]:
                                         16 + ines["prg_bytes"] + ines["chr_bytes"]]).digest()
    print(f"\nROM              {args.rom.name}")
    print(f"  iNES            mapper {ines['mapper']}, PRG {ines['prg_bytes'] // 1024} KiB, "
          f"CHR {'RAM (header says 0)' if ines['chr_is_ram'] else str(ines['chr_bytes'] // 1024) + ' KiB'}"
          f", battery={'yes' if ines['battery'] else 'no'}"
          f"{', trainer' if ines['trainer'] else ''}")
    print(f"  md5(PRG+CHR)    {got.hex()}")
    print(f"  movie expects   {want.hex()}")

    if got != want:
        raise Fail(f"the movie was recorded on a different cartridge.\n"
                   f"  {args.rom.name} is md5(PRG+CHR) {got.hex()}\n"
                   f"  the movie's romChecksum is      {want.hex()}\n"
                   f"  Every measurement below would be of the wrong game.")

    if ines["battery"]:
        sav = args.rom.with_suffix(args.rom.suffix + ".sav")
        note = "present, and WILL be loaded by FCEUX" if sav.exists() else "absent"
        print(f"  save RAM        battery-backed; {args.rom.name}.sav is {note}")

    # ---- outputs the driver needs ------------------------------------------
    (args.out / "inputs.txt").write_text("\n".join(fields) + "\n")

    watch = load_watch()
    (args.out / "watch.txt").write_text(
        "".join(f"{n}\t0x{a:04X}\n" for n, a in watch))
    print(f"\nwatched cells    {', '.join(f'{n}=${a:04X}' for n, a in watch)}")

    # Screenshot frames: a spread of the movie's own subtitle anchors, plus the
    # tail. The subtitles are the author's commentary, so a shot at an anchor is
    # evidence about what the game was supposed to be doing at that frame.
    subs = movie["subtitles"]
    shots: list[int] = []
    if subs:
        step = max(1, len(subs) // max(1, args.shots))
        shots += [subs[i][0] for i in range(0, len(subs), step)]
    tail = max(0, len(fields) - 240)
    shots += [tail + 120, tail + 180, len(fields) - 2, len(fields) - 1]
    seen: set[int] = set()
    shots = sorted(s for s in shots if 1 <= s <= len(fields) and not (s in seen or seen.add(s)))
    (args.out / "shots.txt").write_text(",".join(str(s) for s in shots) + "\n")

    # The author's commentary as a subtitle track: it turns a pile of PNGs into
    # something readable, and it is the movie's own claim about what it is doing.
    def srt_time(fr: int) -> str:
        ms = int(fr * 1000 / 60.0988)
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    srt = []
    for i, (fr, txt) in enumerate(subs):
        end = subs[i + 1][0] - 1 if i + 1 < len(subs) else len(fields) - 1
        srt.append(f"{i + 1}\n{srt_time(fr)} --> {srt_time(max(end, fr))}\n{txt}\n")
    (args.out / "subtitles.srt").write_text("\n".join(srt))

    (args.out / "identity.txt").write_text(
        f"movie\t{args.movie}\nrom\t{args.rom}\n"
        f"frames\t{len(fields)}\nmd5_prg_chr\t{got.hex()}\n"
        f"mapper\t{ines['mapper']}\nbattery\t{ines['battery']}\n")
    print(f"shot frames      {shots}")
    print("identity: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())