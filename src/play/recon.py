#!/usr/bin/env python3
"""Reconnaissance of the first town. MEASURE, THEN REPORT.

    python3 src/play/recon.py [--rom PATH] [--label NAME]

Default ROM is the rebuild (`asm/out/magician-rebuilt.nes`). Pass
`--rom` to run the same sequence against a registered dump -- which is how a
difference in behaviour gets attributed to the build rather than to the game.

This is deliberately NOT an action module. An action asserts; a recon observes.
The distinction matters: an action that asserted would stop at the first thing
it did not understand, which is exactly the thing worth finding out.

Every step uses only what an earlier step measured. In particular the button
table is settled by pressing one button at a time and reading the bytes, not by
reading a name out of the source -- the source's names and the order `joykey`
writes them in disagree about $0030/$0031, and both readings look plausible.

WHAT IT MEASURES, IN ORDER

  1. WHICH BYTE IS WHICH BUTTON. Press each button alone, hold long enough for
     the game's own two-equal-reads debounce, and read $002E-$003B.
  2. THE TITLE SCREEN. What `phase` and `curlev` are before anything is pressed,
     which is not derivable: `dotitle` runs before the IRQ loop exists.
  3. LEAVING THE TITLE. START should put the game on the map screen at level
     $E2 (x0.pds:602-604). START and SELECT are compared, because `waitbut`
     returns a carry flag that separates them.
  4. LEAVING THE MAP SCREEN. Any button should enter the playing level.
  5. THE TOWN. Map size, player position, every object slot, the nametable, and
     whether the nametable changes as the player walks.
  6. WHAT RESPONDS TO A AND B, and what the menu screens (START) do.

Output: a text log, a JSON findings file and checkpoints under
logs/checkpoints/recon/.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from play import ram  # noqa: E402
from play.emu import BizHawk  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]

ap = argparse.ArgumentParser()
ap.add_argument("--rom", default=str(ROOT / "asm/out/magician-rebuilt.nes"))
ap.add_argument("--label", default="rebuild")
args = ap.parse_args()

ROM = pathlib.Path(args.rom).resolve()
LABEL = args.label
OUT = ROOT / "logs" / "recon" / LABEL
OUT.mkdir(parents=True, exist_ok=True)

LOG = open(OUT / "recon.txt", "w", encoding="utf-8")
FINDINGS: dict = {"label": LABEL, "rom": str(ROM)}
FAILURES: list[str] = []


def say(*parts) -> None:
    s = " ".join(str(p) for p in parts)
    print(s, flush=True)
    LOG.write(s + "\n")
    LOG.flush()


def snap(name: str) -> None:
    try:
        d = emu.snapshot(f"{LABEL}_{name}")
        say(f"    snapshot -> {d.relative_to(ROOT)}")
    except Exception as e:
        FAILURES.append(f"snapshot {name}: {e}")
        say(f"    SNAPSHOT FAILED {name}: {e}")


def state(emu: BizHawk, label: str, full: bool = False) -> dict:
    img = emu.work_ram()
    v = ram.decode(img)
    row = {
        "label": label,
        "frame": emu.frame,
        "phase": v["phase"],
        "phase_name": ram.PHASE_NAMES.get(v["phase"], "?"),
        "curlev": v["curlev"],
        "oldlev": v["oldlev"],
        "mapind": v["mapind"],
        "hilev": v["hilev"],
        "mapx": v["mapx"], "mapy": v["mapy"],
        "plrx": ram.plrx(img), "plry": ram.plry(img),
        "plrstat": v["plrstat"], "plrflg": v["plrflg"], "plrtype": v["plrtype"],
        "wealth": v["wealth"], "manacur": v["manacur"], "manatop": v["manatop"],
        "gametime": v["gametime"],
        "pandflg": v["pandflg"], "ctrl": v["ctrl"],
        "plrspell": v["plrspell"], "cursp": v["cursp"], "topspell": v["topspell"],
        "invhand": v["invhand"], "shopind": v["shopind"],
        "nmiflag": v["nmiflag"], "bnksel": v["bnksel"],
        "slots": [ram.object_slot(img, i) for i in range(4)],
        "invop": v["invop"], "invsp": v["invsp"],
    }
    say(f"  [{label}] f={row['frame']} phase={row['phase']}({row['phase_name']}) "
        f"curlev=${row['curlev']:02X} oldlev=${row['oldlev']:02X} mapind={row['mapind']} "
        f"org=({row['mapx']},{row['mapy']}) plr=({row['plrx']},{row['plry']}) "
        f"stat=${row['plrstat']:02X} flg={row['plrflg']} mana={row['manacur']}/{row['manatop']} "
        f"gold={row['wealth']} time={row['gametime']} panel={row['pandflg']} "
        f"nmiflag={row['nmiflag']} bnksel=${row['bnksel']:02X}")
    live = [s for s in row["slots"] if s["active"]]
    for s in row["slots"]:
        say(f"        slot {row['slots'].index(s)}: obtyp=${s['obtyp']:02X} "
            f"{'LIVE' if s['active'] else 'free'} obmod=${s['obmod']:02X} "
            f"obstat=${s['obstat']:02X} pos=({s['x']},{s['y']}) tile=({s['x']//16},{s['y']//16}) "
            f"obhel=${s['obhel']:02X} obchr=${s['obchr']:02X} obint=${s['obint']:02X}")
    if full:
        say("        " + " ".join(
            f"{n}={v[n]}" for n in ("plrspell", "cursp", "topspell", "invhand",
                                     "shopind", "ctrl", "hilev")))
    return row


def nt(emu: BizHawk) -> bytes:
    return bytes(emu.domain_read("CIRAM (nametables)", 0, 0x400))


def nt_dump(t: bytes, h: int = 30, w: int = 32) -> str:
    """The nametable as raw tile indices. Not a font.

    A reader who can see the screen compares; a reader who cannot still gets an
    exact picture of which cells differ between two places, which is what the
    walk test needs.
    """
    out = []
    for y in range(h):
        out.append(f"    y{y:02d} " + " ".join(f"{t[y * w + x]:02x}" for x in range(w)))
    return "\n".join(out)


def nt_diff(a: bytes, b: bytes) -> list[tuple[int, int, int]]:
    return [(i // 32, i % 32, b[i]) for i in range(min(len(a), len(b))) if a[i] != a[i]]


BUTTONS = ("A", "B", "Select", "Start", "Up", "Down", "Left", "Right")
# $002E-$0035 in the order joykey writes them, then $0036-$003B the edge bytes.
# Labels are NEUTRAL where the source's name is not established, and the
# source's own name where it is. The measurement below is what turned the
# neutral ones into facts, and it is why they are not simply "jt[0]"..: the
# source calls the accumulator `jt` and then has joykey write EIGHT bytes from
# jt[0] to jt[+7], so `jt` is a name for one byte that is also the first of
# eight. Measured: jt[0] at $002E = RIGHT and jt[1] at $002F = LEFT.
JOY_NAMES = ("right_2E", "left_2F", "lr_30", "ud_31", "start_32", "select_33",
             "b_34", "a_35")
DEB_NAMES = ("deb_lr_36", "deb_ud_37", "deb_sta_38", "deb_sel_39",
             "deb_fireb_3A", "deb_firea_3B")


def main() -> int:
    t0 = time.time()
    say(f"recon [{LABEL}] {ROM}")
    say(f"ram map digest {ram.map_digest()} -- {len(ram.all_fields())} fields")
    stale = BizHawk.running_emuhawk()
    say(f"mono pids already running: {stale or 'none'}")
    if stale:
        say("REFUSING a second EmuHawk: BizHawk diverts it into the first through "
            "its single-instance pipe, so this would measure the other session.")
        return 2

    global emu
    with BizHawk(rom=ROM, log_name=f"recon_{LABEL}", route="recon",
                    run=f"recon_{LABEL}") as emu:
        say(f"connected in {emu.connect_seconds:.1f}s, {len(emu.domains)} domains")
        FINDINGS["domains"] = [d.name for d in emu.domains]

        # ------------------------------------------------------------ 1
        say("")
        say("=== 1. WHICH BYTE IS WHICH BUTTON =================================")
        say("One button at a time, held long enough for joykey's own")
        say("two-consecutive-equal-reads debounce to settle, then read")
        say("$002E-$0035 (the decoded button bytes) and $0036-$003B (their edge")
        say("bytes). 1 = held. The idle row is the baseline.")
        emu.step(frames=180)
        joy_a, joy_b = ram.f("pad").addr, ram.f("deb_lr").addr
        base = list(emu.ram(joy_a, 8)) + list(emu.ram(joy_b, 6))
        say(f"  {'idle':<8} " + " ".join(f"{x:02X}" for x in base))
        button_bytes: dict[str, dict[str, int]] = {}
        for b in BUTTONS:
            emu.step(frames=60)
            emu.step((b,), 120)
            seen = {tuple(list(emu.ram(joy_a, 8)) + list(emu.ram(joy_b, 6)))
                    for _ in range(8)}
            emu.step(frames=60)
            if len(seen) != 1:
                FAILURES.append(f"{b}: {len(seen)} distinct joypad rows while held")
            row = sorted(seen)[0] if seen else base
            lit = [JOY_NAMES[i] for i in range(8) if row[i] and not base[i]]
            edge = [DEB_NAMES[i] for i in range(6) if row[8 + i] and not base[8 + i]]
            say(f"  {b:<8} " + " ".join(f"{x:02X}" for x in row)
                + f"   lit={lit} edge={edge}")
            button_bytes[b] = {n: row[i] for i, n in enumerate(JOY_NAMES)}
            button_bytes[b].update({n: row[8 + i] for i, n in enumerate(DEB_NAMES)})
        FINDINGS["joy_baseline"] = {" ".join(JOY_NAMES + DEB_NAMES): base}
        FINDINGS["joy_by_button"] = button_bytes

        # The edge bytes are $FF only on the frame the value CHANGES, so a hold
        # never shows them. Sample every frame across a transition to catch one.
        say("  edge bytes across a one-frame transition (they are $FF only on the")
        say("  frame the value changes, so holding the button shows nothing):")
        edges: dict[str, list[str]] = {}
        for b in BUTTONS:
            emu.step(frames=45)
            hits = []
            for k in range(10):
                emu.step((b,), 1)
                r = list(emu.ram(joy_b, 6))
                if any(r):
                    hits.append([DEB_NAMES[i] for i in range(6) if r[i]])
            emu.step(frames=60)
            edges[b] = hits
            say(f"    {b:<8} {hits if hits else 'NO EDGE SEEN'}")
        FINDINGS["joy_edges"] = edges

        # ------------------------------------------------- 2. the title
        say("")
        say("=== 2. THE TITLE SCREEN ==========================================")
        say("`phase` cannot detect this: dotitle runs from `start` at x0.pds:601,")
        say("BEFORE the IRQ loop is installed, so phase is whatever reset left.")
        emu.reset()
        title = []
        for w in (60, 60, 60, 60):
            emu.step(frames=w)
            title.append(state(emu, f"title idle +{w}"))
        FINDINGS["title"] = title
        snap("title")
        say(f"  NAMETABLE at the title screen ({len(nt(emu))} bytes):")
        say(nt_dump(nt(emu)))
        title_nt = nt(emu)

        # -------------------------------------------- 3. leaving it
        say("")
        say("=== 3. LEAVING THE TITLE ========================================")
        say("waitbut (x0.pds:745-752) returns C=0 for START and C=1 for SELECT,")
        say("and `start` (x0.pds:602-604) does lda #$0a / ldx #$e2 / jsr newlev")
        say("on the C=0 path. So START should reach phase $0a at level $E2 and")
        say("SELECT should reach the password screen. Both are measured.")
        emu.reset()
        emu.step(frames=180)
        for b in ("Start", "Select"):
            emu.reset()
            emu.step(frames=180)
            emu.tap(b, hold=6, release=10)
            trail = []
            for w in (10, 20, 40, 60, 60, 90, 90):
                emu.step(frames=w)
                trail.append(state(emu, f"{b} +{w}"))
            FINDINGS[f"title_press_{b}"] = trail
            snap(f"after_{b}")

        # ------------------------------------------------- 4. into it
        say("")
        say("=== 4. LEAVING THE MAP SCREEN ====================================")
        emu.reset()
        emu.step(frames=180)
        emu.tap("Start", hold=6, release=10)
        used, held = emu.step_until([ram.pred("phase", "eq", ram.PHASE_MAP)],
                                    budget=400, what="the map screen")
        say(f"  on the map screen after {used} frames (held={held})")
        say(f"  ** {'REACHED' if held else 'DID NOT REACH'} the map screen **")
        if not held:
            FAILURES.append("the map screen was never reached from the title")
        state(emu, "map screen")
        snap("map_screen")
        map_nt = nt(emu)
        say(f"  NAMETABLE on the map screen:")
        say(nt_dump(map_nt))
        FINDINGS["map_screen_nametable_diff_from_title"] = nt_diff(title_nt, map_nt)

        say("")
        say("  pressing A to leave the map screen:")
        emu.tap("A", hold=6, release=10)
        trail = []
        for w in (10, 20, 40, 60, 60, 90, 90, 120):
            emu.step(frames=w)
            trail.append(state(emu, f"after A +{w}"))
        FINDINGS["after_map_press"] = trail
        phases = {t_["phase"] for t_ in trail}
        reached_level = ram.PHASE_MAIN in phases
        say(f"  phases seen after leaving the map: {sorted(phases)}")
        say(f"  ** {'REACHED the playing level (g00)' if reached_level else 'DID NOT reach g00'} **")
        if not reached_level:
            FAILURES.append(
                f"the playing level was never reached; phase stayed at "
                f"{sorted(phases)} and nmiflag="
                f"{trail[-1]['nmiflag']} (1 = the main loop never finished a pass)")
        snap("after_map_press")

        # --------------------------------------------------- 5. the town
        say("")
        say("=== 5. THE TOWN =================================================")
        town_nt = nt(emu)
        town = state(emu, "town", full=True)
        FINDINGS["town"] = town
        FINDINGS["town_nametable_diff_from_map"] = nt_diff(map_nt, town_nt)
        say(f"  nametable differs from the map screen in "
            f"{len(FINDINGS['town_nametable_diff_from_map'])} cells")
        say(nt_dump(town_nt))
        say("  live object slots:")
        for i, s in enumerate(town["slots"]):
            if s["active"]:
                say(f"    slot {i}: obtyp=${s['obtyp']:02X} pos=({s['x']},{s['y']}) "
                    f"tile=({s['x']//16},{s['y']//16}) obchr=${s['obchr']:02X} "
                    f"obint=${s['obint']:02X} obhel=${s['obhel']:02X}")
        say("  ATTRIBUTE table for the visible window "
            "(CIRAM $23C0-$23FF), raw:")
        attr = bytes(emu.domain_read("CIRAM (nametables)", 0x3C0, 0x40))
        say("    " + " ".join(f"{b:02x}" for b in attr))

        # ------------------------------------------------------ 6. walking
        say("")
        say("=== 6. WALKING ===================================================")
        say("Each leg is only a result if the player's map position changed; the")
        say("object slots are re-read after every leg because an NPC that appears")
        say("when you walk is a finding, not a nuisance.")
        walk = []
        for direction in ("Down", "Right", "Right", "Up", "Left", "Down", "Down"):
            before = (town["plrx"], town["plry"])
            emu.step((direction,), 70)
            img = emu.work_ram()
            v = ram.decode(img)
            after = (ram.plrx(img), ram.plry(img))
            moved = before != after
            slots = [ram.object_slot(img, i) for i in range(4)]
            row = {"dir": direction, "from": list(before), "to": list(after),
                   "moved": moved, "plrstat": v["plrstat"],
                   "obtyp": [s["obtyp"] for s in slots],
                   "obpos": [s["x"] for s in slots],
                   "obyp": [s["y"] for s in slots],
                   "plrflg": v["plrflg"]}
            say(f"  {direction:<6} ({before[0]},{before[1]}) -> ({after[0]},{after[1]}) "
                f"{'moved' if moved else 'DID NOT MOVE'}  "
                f"stat=${row['plrstat']:02X} flg={row['plrflg']} "
                f"obtyp={row['obtyp']} obyp={row['obyp']}")
            walk.append(row)
            if not moved:
                FAILURES.append(f"holding {direction} for 70 frames did not move "
                                "the player at all")
            town.update({"plrx": after[0], "plry": after[1]})
        FINDINGS["walk"] = walk
        walked_nt = nt(emu)
        say(f"  nametable changed by walking: "
            f"{len(nt_diff(town_nt, walked_nt))} cells")
        FINDINGS["nametable_changed_by_walk"] = len(nt_diff(town_nt, walked_nt))
        snap("after_walk")

        # -------------------------------------------- 7. buttons in play
        say("")
        say("=== 7. WHAT A AND B AND START DO IN THE TOWN =====================")
        probes = []
        for b in ("A", "B", "Start", "Select"):
            emu.tap(b, hold=4, release=20)
            emu.step(frames=90)
            probes.append(state(emu, f"after {b}", full=True))
        FINDINGS["button_probes"] = probes
        snap("after_buttons")

    FINDINGS["seconds"] = round(time.time() - t0, 1)
    FINDINGS["failures"] = FAILURES
    (OUT / "findings.json").write_text(json.dumps(FINDINGS, indent=2), encoding="utf-8")
    say("")
    say(f"recon [{LABEL}] finished in {FINDINGS['seconds']}s")
    if FAILURES:
        say(f"{len(FAILURES)} THING(S) THAT DID NOT HOLD:")
        for f in FAILURES:
            say(f"  - {f}")
    else:
        say("every step held")
    say(f"findings: {OUT/'findings.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())