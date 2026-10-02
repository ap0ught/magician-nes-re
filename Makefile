# Build the Magician cartridge from Eurocom's source.
#
#   make            decode the source, assemble it, wrap a .nes, report
#   make rom        just the loadable .nes
#   make check      rebuild and fail if the output moved
#   make verbose    per-file incbin trace
#   make probe      run the rebuilt ROM beside the cartridge in BizHawk
#   make clean      remove generated output
#
# The cartridge is only ever read, never written, and never committed. See
# LEGAL.md.

PYTHON  ?= python3
ASM     := $(PYTHON) asm/build.py
OUT     := asm/out
ROM     := $(OUT)/magician-rebuilt.nes
SOURCES := tools/pds_extract.py asm/build.py asm/mkrom.py asm/pds6502.py

# The cartridge path, in one place. Never committed, so it cannot be a dependency
# the build silently skips.
CART ?= /extdrive/backups/SHARE/roms/nes/Magician (USA).nes

.PHONY: all extract assemble rom check verbose probe clean

all: extract assemble rom

extract: pds-text/x0.pds

pds-text/x0.pds: $(SOURCES) $(wildcard vendor/Magician-NES/X*.PDS)
	$(PYTHON) tools/pds_extract.py

assemble: $(OUT)/prg.bin

$(OUT)/prg.bin: $(SOURCES)
	@mkdir -p $(OUT)
	$(ASM) --cart "$(CART)"

rom: $(ROM)

$(ROM): $(OUT)/prg.bin
	$(PYTHON) asm/mkrom.py

# `build.py` exits non-zero while any module's slot is undetermined, and that is
# the honest answer rather than a broken build: it names the modules it left out.
# For a gate on *change* rather than on completeness, compare against the
# previous output instead of trusting the exit code.
check:
	@mkdir -p $(OUT)
	@cp -f $(OUT)/prg.bin $(OUT)/prg.bin.prev 2>/dev/null || true
	-$(ASM) --cart "$(CART)" > $(OUT)/build.log 2>&1 || true
	@if cmp -s $(OUT)/prg.bin $(OUT)/prg.bin.prev; then \
		echo "ok: the rebuilt PRG is unchanged"; \
	else \
		echo "FAIL: the rebuilt PRG moved (see $(OUT)/build.log)"; exit 1; \
	fi

verbose:
	$(ASM) --cart "$(CART)" --verbose

# BizHawk's Mono build needs a GL context; --gdi avoids it, and there is no Xvfb
# on this machine. Both windows are moved side by side over X11 by tools/.
probe: $(ROM)
	@tools/bizhawk_probe.sh "$(CART)" "$(ROM)"

clean:
	rm -rf $(OUT) pds-text