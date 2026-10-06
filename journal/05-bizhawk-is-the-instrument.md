# 05 - BizHawk *is* the instrument, and the rebuild has been drawing the whole time

Dated 2026-10-03. Branch `fix/boot-from-source`.

## The claim that had to be killed first

Three sessions ended with the sentence "BizHawk cannot be driven headless here (no
Xvfb, no xdotool)" and fell back on `tools/nestrace.py`, a CPU/PPU emulator written
for this project. Every downstream statement -- *the rebuild's frame is flat
colour, max luminance 0.0, 0 non-zero pixels, `PPUMASK=$FE`, all 32 palette
entries `$0F`* -- was measured with that second emulator, not with an emulator
whose correctness anyone had established.

The conclusion was drawn too broadly. Measured on this machine before writing any
code:

    DISPLAY=:0                       X.Org 24.1.13, Wayland session via XWayland
    XAUTHORITY=/run/user/1000/xauth_LRGbuu
    glxinfo -B                       direct rendering: Yes
    /usr/bin/import                  present (ImageMagick)
    xdotool wmctrl xwd scrot Xvfb    absent
    sudo -n true                     exit 0

A display exists, GL works, and the project README already recorded that BizHawk
2.11.1 under Mono loads the rebuilt ROM and reports mapper 4. The only genuine gap
was *input injection*, and that only matters for reaching the GAME RESTORE SCREEN.
The question that actually needed answering -- does it draw a picture, and what is
in the framebuffer -- never needed a button press at all.

## The flag nobody had found

`EmuHawkMono.sh --help` prints a full `System.CommandLine` option list. The one
that matters:

    --lua <lua>   path; Lua script or Console session to load; implies --luaconsole

That is the whole gap. Everything else follows from it:

* `emu.frameadvance()` is exact. Ten calls, then `emu.framecount()` returns 10.
  "The same moment in both images" is reproducible without wall clock.
* `client.screenshot(path)` writes a PNG of the core's own video buffer. No window
  scrape, no occlusion, no window placement.
* `client.exit()` shuts the session down, which matters because a leftover process
  holds the single-instance pipe and diverts the *next* launch into the dead one --
  which is very likely what produced the "no window" symptom past sessions saw.

Two measurement traps found on the way, both of which produce confident nonsense:

1. **The ROM path must be absolute.** `EmuHawkMono.sh` does
   `cd "$(dirname "$(realpath "$0")")"`, so a relative ROM path resolves against
   BizHawk's own directory, the ROM never opens, and the session hangs with
   nothing in the log past `Using SDL2 for host input` and no NES client window.
   This looks exactly like "BizHawk will not start".
2. **`memory.read_u8(addr, domain)` with a wrong domain name is not an error.**
   It silently falls back to the default domain. Reading `$2000`-`$2007` that way
   returned `$F2` for all eight registers in both images, and a `palette` dump of
   33 identical `$F2` bytes, which reads like a plausible broken-pallet result. The
   names are case-sensitive; `memory.getmemorydomainlist()` is the authority.

The first attempt at this harness therefore produced a *correct screenshot* and a
*fabricated register dump* in the same run. Both were kept, and the fabrication is
recorded here because a future session reading only the pretty numbers would
otherwise repeat it.

## Task 1: the control -- the real cartridge

`tools/bizhawk/run.sh` + `tools/bizhawk/frame.lua`, cartridge at
`/extdrive/backups/SHARE/roms/nes/Magician (USA).nes`, `NES/SaveRAM/` cleared
first (header byte 6 is `$42`, bit 1 set, so the cartridge has battery-backed PRG
RAM and BizHawk otherwise resumes a stale save). Target frame 60, unattended.

BizHawk 2.11.1, quickerNES, `Booted with Mapper #4 "mmc3"`,
`BootGod entry found: Magician`.

`client.screenshot()` output, 256x224 -- BizHawk crops to
`nes.gettopscanline()`..`nes.getbottomscanline()`, which is why the picture is 224
rows and not 240:

| | |
|---|---|
| size | 256 x 224 |
| distinct colours | 10 |
| max luminance | 216 |
| mean luminance | 51.91 |
| non-zero pixels | 20 807 of 57 344 |
| dominant colour | `(0,0,0)` 36 537 px |

On screen: the title screen. `EUROCOM ENTERTAINMENT SOFTWARE PRESENTS-` across the
top, the `MAGICIAN` logo in outline, the skeleton in green and the man in orange
over the brick background, `LICENSED BY Nintendo` and `(C) COPYRIGHT TAXAN USA
CORP. 1990` at the bottom.

## Task 2: the rebuild, same session recipe

Identical core, identical `--gdi` path, identical Lua, identical frame 60,
`NES/SaveRAM/` cleared:

| | cartridge | rebuild |
|---|---|---|
| size | 256 x 224 | 256 x 224 |
| distinct colours | 10 | 10 |
| max luminance | 216 | 207 |
| mean luminance | 51.91 | 54.07 |
| non-zero pixels | 20 807 | 21 580 |
| dominant colour | `(0,0,0)` 36 537 px | `(0,0,0)` 35 764 px |

**The rebuild shows a picture.** Not a black screen, not one flat colour. The
top ~40% is right: the brick background, the whole `MAGICIAN` logo, the blue
highlight blob and the top of the skeleton are all correct. The lower ~60% is
scrambled -- tiles that look like they were fetched with the right pattern but the
wrong attributes, or the wrong tile indices entirely.

## What this retires, in the order it was wrong

* **"The rebuild's frame is flat colour, max luminance 0.0, 0 non-zero pixels."**
  An artifact of `tools/nestrace.py`'s own PPU. Real numbers from quickerNES are
  max luminance 207 and 21 580 non-zero pixels. Every claim in this repository
  sourced to a `nestrace.py` framebuffer -- including `PPUMASK=$FE`, "all 32
  palette entries `$0F`", and "659/2048 nametable bytes non-zero" -- is withdrawn
  as evidence about the ROM. `nestrace.py` remains a legitimate instrument for
  *where execution goes*; its framebuffer is not evidence about whether a ROM
  works.
* **"BizHawk cannot be driven here."** It can, headfully on the live display, with
  frame-exact Lua control. No Xvfb was installed and the session was not modified.
* **"Reaching the restore screen needs a button press, so the only measurable
  question is whether it reaches the title screen unattended."** Still true, and
  now measurable *properly*. `tools/bizhawk/apiprobe.lua` lists `joypad.*` on the
  Lua surface, so input injection is likely the next thing to close; it is not
  needed for the picture.

## What is still open, and the new instrument for it

The corruption is in the lower 60% of the screen, which is a statement about VRAM
rather than about a destroyed return address. `tools/bizhawk/dump.lua` exists to
settle it: it writes VRAM, CHR-RAM, OAM, work RAM and the palette to disk from
both images at the same frame, so the divergence is a byte range rather than an
eyeball impression.

The `$9D6C` `rts` from `journal/03` is *not* thereby disproved -- it may still be
a real defect -- but it is now testable against a core whose CPU is known-good
instead of against `nestrace.py`'s. BizHawk's Lua exposes `emu.registerafter` /
`emu.registerbefore`, and `client.cpu` resolves inside those callbacks (it is nil
at script top level, which is what an earlier session hit and misread as "Lua
cannot read the CPU"). That is a core-native instruction trace, and it is the
right instrument for the question the earlier sessions were answering badly.

## Reproduction

    make rom
    python3 tools/bizhawk/capture.py /tmp/opencode/biz 60

Writes `cart.png`, `rebuild.png`, both `.report` files and `summary.json`. The
PNGs are not committed: they contain cartridge-derived imagery.
