#!/usr/bin/env python3
"""Audit ROM-native HUD placement prerequisites without modifying the cartridge.

Run: python3 tools/hud_audit.py
This intentionally reports *unknown* graphics/hooks instead of guessing them.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "asm" / "build.py"
RAM = ROOT / "src" / "play" / "ram.py"

def manifest_entries(source: str) -> list[tuple[str, int | None, int | None]]:
    """Read literal placement keys from OUR_MODULES without importing the build."""
    tree = ast.parse(source)
    node = next(
        (n.value for n in tree.body
         if isinstance(n, (ast.Assign, ast.AnnAssign))
         and (n.target.id == "OUR_MODULES" if isinstance(n, ast.AnnAssign)
              and isinstance(n.target, ast.Name) else
              any(isinstance(t, ast.Name) and t.id == "OUR_MODULES"
                  for t in n.targets) if isinstance(n, ast.Assign) else False)),
        None,
    )
    if not isinstance(node, ast.Dict):
        raise ValueError("OUR_MODULES dictionary not found")
    result = []
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(value, ast.Dict):
            raise ValueError("Unexpected manifest entry")
        attrs = {}
        for k, v in zip(value.keys, value.values):
            if isinstance(k, ast.Constant) and k.value in ("slot", "origin"):
                if isinstance(v, ast.Constant) and isinstance(v.value, int):
                    attrs[k.value] = v.value
        result.append((key.value, attrs.get("slot"), attrs.get("origin")))
    return result

def main() -> None:
    if not BUILD.is_file() or not RAM.is_file():
        raise SystemExit("Run inside magician-nes-re checkout")
    entries = manifest_entries(BUILD.read_text())
    print("ROM-native HUD preflight — manifest placement inventory")
    for name, slot, origin in entries:
        if slot is not None or origin is not None:
            print(f"  {name:20} slot={str(slot):>4} origin="
                  f"{('$%04X' % origin) if origin is not None else 'symbol-defined'}")
    print(f"Total registered modules: {len(entries)}")
    print("Known shared draw primitive: DRAW.PDS / drawfill (verify bank/call convention)")
    print("Unverified: filigree CHR tile IDs, gameplay CHR residency, HUD render hook,")
    print("            VBlank budget, fixed strip scrolling, player health semantics.")
    print("No ROM bytes changed; this is a discovery aid, not a HUD implementation.")

if __name__ == "__main__":
    main()
