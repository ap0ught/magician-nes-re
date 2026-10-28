# Issue #2 — Beta 1 vs retail ROM asset measurements

Measured from user-provided cartridge images, **not** from a live gameplay CHR-RAM capture. No ROM bytes are committed.

Both iNES files contain 128 KiB PRG and 128 KiB CHR (32 × 4 KiB CHR banks). Their PRG and CHR payloads are **not interchangeable**:

- PRG payload: **94,956 differing bytes** of 131,072.
- CHR payload: **29,369 differing bytes** of 131,072.
- Identical 4 KiB CHR bank indices (zero-based in the file): **1, 2, 3, 4, 5, 6, 11, 13, 24** (9/32).
- All other 4 KiB banks differ at least once.

Measurement procedure:
```python
from pathlib import Path
beta = Path("Magician (USA) (Beta 1) (1990-03-02).nes").read_bytes()
retail = Path("Magician (USA).nes").read_bytes()
assert beta[:4] == retail[:4] == b"NES\\x1a"
assert len(beta) == len(retail) == 262160
prg = slice(16, 16 + 131072)
chr_ = slice(16 + 131072, 16 + 262144)
print(sum(x != y for x, y in zip(beta[prg], retail[prg])))
print(sum(x != y for x, y in zip(beta[chr_], retail[chr_])))
```

For the actual filigree tile hunt, decode the **Beta 1** CHR payload as planar NES 2bpp 8×8 tiles and inspect visually. The raw file-bank position is **not necessarily** the live tile index or palette during gameplay: `docs/capture-analysis.md` documents dynamically loaded CHR RAM and one unmatched captured bank. Follow a candidate tile through the original CHR loader and verify in a gameplay capture before rendering it.

Next implementation gate: locate the specific menu filigree tile indices, their live CHR-RAM residency, and a stable nametable/VBlank hook. Only then install an in-ROM PDS6502 drawing routine.
