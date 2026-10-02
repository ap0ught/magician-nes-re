# Assemble the cartridge from Eurocom's source.
#
#   make            build, and report how much of it matches the cartridge
#   make check      the same, but fail if the output moved
#   make verbose    per-file incbin trace
#   make extract    decode the PDS containers into pds-text/ first
#
# Nothing here is committed: out/ and pds-text/ are generated (see .gitignore),
# and the cartridge is only ever read.

PYTHON ?= python3
ROOT   := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

.PHONY: all check verbose extract clean

all: $(ROOT)/asm/out/prg.bin

$(ROOT)/asm/out/prg.bin: $(ROOT)/tools/pds_extract.py $(ROOT)/asm/build.py $(ROOT)/asm/pds6502.py
	$(PYTHON) $(ROOT)/asm/build.py

verbose:
	$(PYTHON) $(ROOT)/asm/build.py --verbose

# The rebuild is a development build, not the shipped one, so exact agreement is
# not the target -- see PROVENANCE.md section 3. This fails only when the output
# moves, which is what makes the build usable as a regression check.
check: $(ROOT)/asm/out/prg.bin
	@cp $(ROOT)/asm/out/prg.bin $(ROOT)/asm/out/prg.bin.prev
	@$(PYTHON) $(ROOT)/asm/build.py >/dev/null
	@cmp -s $(ROOT)/asm/out/prg.bin $(ROOT)/asm/out/prg.bin.prev \
		|| { echo "FAIL: the rebuilt PRG moved"; exit 1; }
	@echo "ok: the rebuilt PRG is unchanged"

extract:
	$(PYTHON) $(ROOT)/tools/pds_extract.py

clean:
	rm -rf $(ROOT)/asm/out