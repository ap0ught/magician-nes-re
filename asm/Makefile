# Assemble the cartridge from Eurocom's source.
#
#   make            build, and report how much of it matches the cartridge
#   make rom        wrap the result in an iNES header
#   make check      the same, but fail if the output moved
#   make verbose    per-file incbin trace
#   make extract    decode the PDS containers into pds-text/ first
#
# Nothing here is committed: out/ and pds-text/ are generated (see .gitignore),
# and the cartridge is only ever read.

PYTHON ?= python3
ROOT   := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

# Our own sources are build inputs. They were not listed here, which meant
# `src/magician/STARTLEV.PDS` could be edited, committed, and built into a
# release note while `make` reported "Nothing to be done" -- and the whole point
# of the split is that a change there IS a change to the ROM. A build system
# that ignores the directory you are told to put your changes in is the quietest
# possible way to lose them.
OUR_SRC := $(wildcard $(ROOT)/src/magician/*.PDS $(ROOT)/src/magician/*.SRC)

.PHONY: all rom check verbose extract clean

all: $(ROOT)/asm/out/prg.bin

$(ROOT)/asm/out/prg.bin: $(ROOT)/tools/pds_extract.py $(ROOT)/asm/build.py $(ROOT)/asm/pds6502.py $(OUR_SRC)
	$(PYTHON) $(ROOT)/asm/build.py

rom: $(ROOT)/asm/out/magician-rebuilt.nes

$(ROOT)/asm/out/magician-rebuilt.nes: $(ROOT)/asm/out/prg.bin $(ROOT)/asm/mkrom.py
	$(PYTHON) $(ROOT)/asm/mkrom.py

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