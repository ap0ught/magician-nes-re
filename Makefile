# Build the Magician cartridge from Eurocom's source.
#
#   make            decode the source, assemble it, wrap a .nes, report
#   make rom        just the loadable .nes
#   make check      rebuild and fail if the output moved
#   make verbose    per-file incbin trace
#   make gaps       the committed stock-vs-rebuild comparison and gap map
#   make doctor     is the emulator, the display and the cartridges in place
#   make emu-setup  install this project's OWN BizHawk (~150 MB) and core.so
#   make display-up start Xephyr on :2 -- this project's own nested display
#   make probe      run the rebuilt ROM beside the cartridge in BizHawk
#   make movie      parse the TAS movie and emit the input table (needs MOVIE)
#   make replay     replay MOVIE into $(ROM) in BizHawk and report the sync frame
#   make guard      fail if a cartridge, a movie, or vendor/ is staged
#   make check-py   run the instrument test suite (no emulator, no cartridge)
#   make check-isolation  prove no run touched another project's emulator tree
#   make testsuite  run tools/nestrace.py against koute's nes-testsuite
#   make clean      remove generated output
#
# The cartridge is only ever read, never written, and never committed. See
# LEGAL.md.
#
# `assemble` and `rom` are phony and run unconditionally. They used to be file
# targets whose only prerequisites were the four Python files, so `make` printed
# "Nothing to be done for 'all'" whenever `asm/out/prg.bin` happened to be newer
# than the tools -- a build that can silently not build. The assembler takes ~20 s
# because it searches sixteen slots per module; that is the price of a `make`
# that means something, and `make check` is the cheap way to watch for a change.

PYTHON  ?= python3
ASM     := $(PYTHON) asm/build.py
OUT     := asm/out
ROM     := $(OUT)/magician-rebuilt.nes

SOURCES := tools/pds_extract.py asm/build.py asm/mkrom.py asm/pds6502.py \
           asm/patches.py asm/patches.manifest
PDS     := $(wildcard vendor/Magician-NES/X*.PDS)
TEXTOUT := $(wildcard pds-text/x?.pds)

# The cartridge path, in one place. Never committed, so it cannot be a dependency
# the build silently skips. Derived from the registry in asm/patches.py rather
# than written here, because a literal in the Makefile and a literal in
# asm/build.py are two places to forget: that is how every measurement in this
# project came to be taken against the release instead of Beta 1. `python3
# tools/cartref.py` prints the path and nothing else.
CART ?= $(shell python3 tools/cartref.py)

# A checkout of https://github.com/koute/pinky (the nes-testsuite/ directory
# only). It is third-party and is never committed, so it is not a dependency the
# build can rely on: TS points at wherever the user unpacked it.
TS ?= /tmp/opencode/pinky/nes-testsuite
TS_FRAMES ?= 120

# ------------------------------------------------------------------ the instrument
# The emulator and the display, both of which used to belong to another project.
#
# WHERE THE EMULATOR IS: `tools/bizhawk/bizpath.sh` decides, from `MAGICIAN_BIZHAWK`
# (or the `BIZHAWK` alias), falling back to
#
#     $HOME/code/games/magician-nes-bizhawk/BizHawk-2.11.1-linux-x64
#
# and NEVER onto another directory that happens to exist. `MAGICIAN_BIZHAWK=...`
# on any command line below overrides both. The old default was a hardcoded path
# into a sibling checkout in three files, and a caller who exported the variable
# got a guard that certified their directory and a launch out of the other
# project's -- see journal/14 and src/testing/test_bizpath.py.
#
# WHERE THE DISPLAY IS: `MAGICIAN_DISPLAY`, default :2. NOT :1, which is the
# neighbouring project's, so a search in either can be watched while the other
# runs. `run.sh` exports DISPLAY to the emulator, because a systemd user service
# does not inherit the session's DISPLAY and otherwise ends up at "Could not open
# display (X-Server required)" with nothing to say why.
#
# There is no Xvfb on this machine and no `xorg-server-xvfb` package -- that is
# still true and `pacman -Ss` still offers it. What is also true, and was measured
# on 2026-10-05 before this was written: Xephyr IS installed
# (`xorg-server-xephyr` 21.1.24) and BizHawk runs on `Xephyr :2` with no window
# manager, producing the same work-RAM fingerprint as `:0`. The old claim here --
# "there is no Xvfb on this machine", paired with nestrace.py's "BizHawk is
# GUI-only with no Xvfb" -- was literally true about Xvfb and was read as a
# statement about BizHawk, which it was not.
.PHONY: all extract assemble rom check verbose gaps probe testsuite clean guard \
        movie replay install-hooks check-py \
        doctor emu-setup display-up display-down display-status check-isolation \
        install-units

all: extract assemble rom

# `extract` stays a real file target: a plain decode, cheap and idempotent, so
# timestamps are a correct answer for it. The phony `extract` alias forces it.
extract: pds-text/x0.pds
extract:
	$(PYTHON) tools/pds_extract.py

pds-text/x0.pds: $(SOURCES) $(PDS)
	$(PYTHON) tools/pds_extract.py

# Unconditional. See the note at the top of this file.
assemble: extract
	@mkdir -p $(OUT)
	$(ASM) --cart "$(CART)"

rom: assemble
	@mkdir -p $(OUT)
	$(PYTHON) asm/mkrom.py

# A change-detector, not a completeness gate: the rebuild is a February 1990
# development build and the cartridge is a later one, so it is *expected* not to
# match. What must not happen is the assembler dying and the log reading "the PRG
# moved", which is what the old `-$(ASM) ... || true` did -- a crash left the old
# prg.bin in place, `cmp` saw a difference and named the wrong cause.
check:
	@mkdir -p $(OUT)
	@cp -f $(OUT)/prg.bin $(OUT)/prg.bin.prev 2>/dev/null || true
	@if $(ASM) --cart "$(CART)" > $(OUT)/build.log 2>&1; then \
		echo "assembler: exit 0"; \
	else \
		rc=$$?; \
		echo "FAIL: the assembler exited $$rc -- not a comparison failure."; \
		echo "      tail of $(OUT)/build.log:"; \
		tail -n 20 $(OUT)/build.log | sed 's/^/      /'; \
		exit $$rc; \
	fi
	@if [ -z "$$(ls -A $(OUT)/prg.bin 2>/dev/null)" ]; then \
		echo "FAIL: the assembler exited 0 but wrote no prg.bin"; exit 1; \
	fi
	@if cmp -s $(OUT)/prg.bin $(OUT)/prg.bin.prev; then \
		echo "ok: the rebuilt PRG is unchanged"; \
	else \
		echo "FAIL: the rebuilt PRG moved (see $(OUT)/build.log)"; exit 1; \
	fi

verbose: extract
	$(ASM) --cart "$(CART)" --verbose

# The committed comparison of the rebuild against both cartridges: per-bank and
# per-module match/differ/source-only/cart-only counts, plus the classification of
# every region that disagrees. See tools/gapmap.py.
gaps: assemble
	$(PYTHON) tools/gapmap.py --write --probe --boot

# BizHawk's Mono build needs a GL context; --gdi avoids it. Both windows are moved
# side by side over X11 by tools/.
probe: rom
	@tools/bizhawk_probe.sh "$(CART)" "$(ROM)"

# The tracer is the only NES instrument here, so it is measured against a test
# suite rather than trusted. Exits non-zero if anything FAILs or ERRORs, which
# means it is a gate you can leave switched off until the tracer is good enough.
testsuite:
	@test -d "$(TS)/testcases" || { \
		echo "no nes-testsuite at $(TS)"; \
		echo "  git clone --depth 1 --filter=blob:none --sparse https://github.com/koute/pinky"; \
		echo "  cd pinky && git sparse-checkout set nes-testsuite nes mos6502"; \
		echo "  then: make testsuite TS=/path/to/pinky/nes-testsuite"; \
		exit 2; \
	}
	$(PYTHON) tools/nestrace.py --testsuite "$(TS)" --ts-frames $(TS_FRAMES)

# ---------------------------------------------------------------- the movie
# A verified 44 003-frame FCEUX `.fm2`: FatRatKnight's "NES Magician in 12:12.18",
# submission 2237S. It is third-party and copyrighted, so it is NEVER committed
# and never has a default inside the tree -- MOVIE is a path on this machine, and
# tools/fm2.py's DEFAULT_MOVIE is under /tmp.
#
# The parser refuses any cartridge whose md5(PRG+CHR) is not the one the movie
# was recorded on, which is the release and nothing else. For the rebuild -- the
# whole point of `make replay` -- that refusal is expected and is overridden
# with a recorded reason, which ends up in the generated Lua and therefore in
# every replay log.
#
#   make movie                       parse and verify, against $(CART) (Beta 1:
#                                    expected to be REFUSED -- the movie is
#                                    release-timed)
#   make movie ROM=/path ROM=R       verify against a named dump instead
MOVIE  ?= /tmp/opencode/tas/Magician (U)-FatRatKnight GoodEnd+subtitle.fm2
ROMY   ?= $(ROM)
REPLAY_ANYWAY ?= Task 4: our rebuild against a release-timed movie
MOVEOUT := tools/bizhawk/out

# `ROM=` may carry spaces (every dump filename does), so it is quoted and cannot
# be a two-word split. `REPLAY_ANYWAY` is a reason, not a flag, and is always
# passed -- there is no way to get an unlabelled mismatch past this.
movie:
	@mkdir -p $(MOVEOUT)
	$(PYTHON) tools/fm2.py "$(MOVIE)" --rom "$(ROMY)" --expect-frames 44003 \
	    $(if $(filter-out "$(ROM)",$(ROMY)),--replay-anyway "$(REPLAY_ANYWAY)") \
	    --lua $(MOVEOUT)/movie.lua --bin $(MOVEOUT)/movie.padbin

# Replay MOVIE into $(ROM) and report how many frames it stays in sync.
# FRAMES caps the run; SYNC_FIRST=1 stops at the first RAM divergence instead of
# running to the end, which is what makes the "how far does it get" question
# cheap enough to ask repeatedly.
FRAMES ?= 44003
replay: movie
	tools/bizhawk/replay.sh "$(ROMY)" "$(FRAMES)" $(if $(SYNC_FIRST),sync-first,)

# The instrument test suite. Unit-level: no emulator, no cartridge, no movie, no
# display, so it runs in seconds and can gate every commit. It pins the things
# this project got wrong silently -- a harness that reported three zero vectors,
# a parser that read the movie's buttons in the wrong order, a codec that stopped
# one byte short per run. See src/testing/README.md for what each file pins and
# for the live bugs it found while being written.
check-py:
	$(PYTHON) src/testing/run_all.py

# ------------------------------------------------------- the instrument, on disk
# What is present, what is missing, and how to get it. One exit code per class of
# thing, in dependency order, because "not ok" makes the operator go and look and
# this project has a documented habit of fixing the wrong thing:
#
#   0 ready   2 emulator   3 runtime tools   4 display   5 a cartridge or the rebuild
#
# Deliberately NOT part of check-py. check-py must stay runnable in seconds with no
# emulator, no cartridge, no display and no network, because it gates every commit;
# everything this target reports is a property of the MACHINE.
doctor:
	@tools/bizhawk/doctor.sh

# Install this project's own BizHawk. Downloads the official tarball, verifies its
# sha256 against a pin, and builds Lua/socket/core.so -- which BizHawk does not
# ship and which src/play/bridge.lua's `require("socket.core")` needs. Nothing it
# does touches any other project's directory.
emu-setup:
	@tools/bizhawk/setup.sh

# Xephyr on :2, this project's own nested display, with NO window manager.
# Measured: none is needed. BizHawk maps a real window on a bare Xephyr and the
# bridge answers; the instrument reads the core through Lua and screenshots come
# from client.screenshot(), so window placement cannot affect a number it
# produces. `display-down` kills by pid, after checking that pid's command line,
# and never uses `pkill -f Xephyr` -- another project runs one on :1.
display-up:
	@tools/bizhawk/display.sh up

display-down:
	@tools/bizhawk/display.sh down

display-status:
	@tools/bizhawk/display.sh status

# Symlink the two units in tools/systemd/ into ~/.config/systemd/user. They are
# NOT committed to the user's systemd directory by this repository -- a checkout
# that installed units behind your back would be doing something invasive -- so
# this target is explicit and prints what it did.
install-units:
	@tools/bizhawk/install_units.sh

# ------------------------------------------------------- the isolation proof
# Not a claim: a recursive listing with sizes and mtimes, plus a sha256 of every
# file, of a DIRECTORY THIS PROJECT IS NOT ALLOWED TO TOUCH, taken before and
# after a run. `make check-isolation` names the directory; the point is that the
# answer is a comparison rather than a promise.
#
# The default is empty, because naming another project's emulator directory in this
# Makefile is exactly the thing the previous commits removed. Set it explicitly:
#
#   make check-isolation ISOLATE=/path/to/the/other/BizHawk-... cmd='make probe'
check-isolation:
	@tools/isolation.sh "$(ISOLATE)" $(if $(cmd),-- "$(cmd)",)

guard:
	@tools/guard_staged.sh

# Wire the guard into this checkout's pre-commit. It is a local file, so it is
# not committed -- the guard itself is `tools/guard_staged.sh`.
install-hooks:
	@mkdir -p .git/hooks
	@printf '%s\n' '#!/usr/bin/env bash' \
	    'set -uo pipefail' \
	    '"$$(cd "$$(dirname "$${BASH_SOURCE[0]}")/../.." && pwd)/tools/guard_staged.sh" || {' \
	    '  echo "pre-commit: refusing to commit cartridge/movie/vendor content" >&2; exit 1; }' \
	    > .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "installed .git/hooks/pre-commit -> tools/guard_staged.sh"

clean:
	rm -rf asm/out pds-text
