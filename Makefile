# Build the Magician cartridge from Eurocom's source.
#
#   make            decode the source, assemble it, wrap a .nes, report
#   make rom        just the loadable .nes
#   make check      rebuild and fail if the output moved
#   make verbose    per-file incbin trace
#   make gaps       the committed stock-vs-rebuild comparison and gap map
#   make probe      run the rebuilt ROM beside the cartridge in BizHawk
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
# the build silently skips.
CART ?= /extdrive/backups/SHARE/roms/nes/Magician (USA).nes

# A checkout of https://github.com/koute/pinky (the nes-testsuite/ directory
# only). It is third-party and is never committed, so it is not a dependency the
# build can rely on: TS points at wherever the user unpacked it.
TS ?= /tmp/opencode/pinky/nes-testsuite
TS_FRAMES ?= 120

.PHONY: all extract assemble rom check verbose gaps probe testsuite clean

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

# BizHawk's Mono build needs a GL context; --gdi avoids it, and there is no Xvfb
# on this machine. Both windows are moved side by side over X11 by tools/.
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

clean:
	rm -rf asm/out pds-text
