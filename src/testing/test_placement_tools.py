"""The placement tools: their overrides must keep up with the assembler, and
they must not report a measurement they did not take.

    python3 src/testing/test_placement_tools.py

Four tools -- `tools/modrange.py`, `tools/gapmap.py`, `tools/whowrote.py` and
`tools/slotscore.py` -- each subclass `pds6502.Assembler` and override
`run_file` so they can see which module wrote which byte. All four of them broke
at once, in the same way, and all four went quiet:

  * `Assembler.run_file` grew a parameter (`addr_ceiling`). Every override that
    did not name it raised `TypeError` on *every call*, so the tool measured
    nothing.
  * `tools/modrange.py` caught that `TypeError` with a bare `except:` and printed
    a table anyway: **a 0% match over an all-zero image**, which reads as a fact
    about a cartridge and is actually a statement about a broken call.
  * `tools/gapmap.py`, `whowrote.py` and `slotscore.py` had no handler at all, so
    they died -- which was better, and still cost a session.

The signature check below is derived from `asm/pds6502.py`'s own source rather
than written down, because a list of parameter names in a test is a list that
goes stale the next time the signature changes -- which is the bug.

The second half is the other half of the same disease: a tool that reports a
measurement it did not take. `whowrote.Traced` and `gapmap.Footprint` both have a
`tracing` flag, because the build assembles every module at all sixteen slots as
a *search* before the project pass. Recording during that pass unions sixteen
attempts and reports a module as having written the whole image.

Thirty-five checks:

   1-5.  each override accepts every parameter the base `run_file` declares, and
         every keyword `run_all` actually passes it -- both read out of
         `asm/pds6502.py`
   6.    a positive control: a deliberately wrong override IS reported as wrong,
         so checks 1-5 are known to be capable of failing
   7-8.  each override genuinely assembles a synthetic module and records its
         bytes, at the address range it was told to use
   9-12. with its tracing flag OFF, each of them records nothing -- a tool that
         reports a module it did not assemble is worse than one that crashes
  13-16. none of the four swallows an exception: no bare `except:` and no
         `except Exception` anywhere in them
  17.    `all_slots()` covers every module and prefers PINNED over ASSUMED, so
         indexing it can never be the KeyError it was
  18.    the four tools are the four this file names -- a rename is a loud failure

WHAT IT DOES NOT CLAIM

  * **No cartridge and no full build.** Each override is driven over one
    synthetic three-instruction module written to /tmp, assembled at one slot.
    Running `modrange.py` or `slotscore.py` for real takes about twenty seconds
    because `asm/build.py` searches sixteen slots per module, which is a build
    and not a unit test.
  * **Nothing here says a slot is correctly assigned.** That is
    `test_placement_math.py` (dis6502/align6502/slotalign) and the build log.
  * **A signature check is not a behaviour check**, which is why check 7 actually
    calls the override and check 9 actually checks that nothing was recorded.

WHAT IT NEEDS: python3, and the committed `vendor/` submodule for the assembler's
source to read (not opened). Under a second.

Run:  python3 src/testing/test_placement_tools.py
"""

import ast
import inspect
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "asm"))
sys.path.insert(0, str(ROOT / "tools"))

os.chdir(ROOT)

import pds6502                # noqa: E402  (asm/pds6502.py)
import build as B             # noqa: E402  (asm/build.py)

TMP = Path("/tmp/opencode/magician-testing/placement")
TMP.mkdir(parents=True, exist_ok=True)

# (module name, the subclass's name in it). Derived from what each file defines;
# check 18 asserts this list still matches the tree.
TOOLS = [("modrange", "Traced"), ("gapmap", "Footprint"),
         ("whowrote", "Traced"), ("slotscore", "Footprint")]

ok = 0


def check(name, cond, detail=""):
    global ok
    if not cond:
        raise AssertionError(f"{name}\n         {detail}")
    ok += 1
    print(f"  ok {name}")


def load(modname):
    """Import a tools/ module. Import failure is a failure, not a skip."""
    import importlib
    return importlib.import_module(modname)


# =====================================================================================
# The two parameter lists, both read out of asm/pds6502.py rather than written down.
# =====================================================================================
base_params = [n for n in inspect.signature(pds6502.Assembler.run_file).parameters
               if n != "self"]

# Every keyword `run_all` passes to `self.run_file(...)`, found by parsing the
# source. This is the list that grew by one and broke four tools, so it is
# derived from the file that grew rather than copied into this test.
pds_src = (ROOT / "asm" / "pds6502.py").read_text(encoding="utf-8")
tree = ast.parse(pds_src)
call_kwargs: set[str] = set()
for node in ast.walk(tree):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run_file"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"):
        for kw in node.keywords:
            if kw.arg:
                call_kwargs.add(kw.arg)

check("asm/pds6502.py's run_file declares a parameter list this test can read",
      base_params == ["path", "slot", "origin", "window_slots", "addr_ceiling"],
      f"run_file now takes {base_params}; this test does not need editing to "
      f"notice, because it reads them")
check("run_all passes run_file more than just a path, and the keywords are "
      "the ones the overrides name",
      {"slot", "origin", "window_slots", "addr_ceiling"} <= call_kwargs,
      f"run_all's call sites pass {sorted(call_kwargs)}")


def missing_params(override) -> set[str]:
    """Parameters of the base `run_file` that this override does not accept."""
    params = inspect.signature(override).parameters.values()
    if any(p.kind is p.VAR_KEYWORD for p in params):
        return set()
    own = set(inspect.signature(override).parameters)
    return set(base_params) - own


# 1-5. Each override.
overrides = {}
for modname, clsname in TOOLS:
    mod = load(modname)
    cls = getattr(mod, clsname, None)
    if cls is None:
        raise AssertionError(f"tools/{modname}.py defines no class named {clsname}")
    overrides[modname] = cls.run_file
    missing = missing_params(cls.run_file)
    check(f"{modname}.{clsname}.run_file accepts every parameter the base declares",
          not missing,
          f"it is missing {sorted(missing)}. `run_all` calls run_file with "
          f"{sorted(call_kwargs)}, so every one of those raises TypeError on "
          f"EVERY module and the tool measures nothing -- which is how a 0% "
          f"match over an all-zero image got printed.")

MOD = TMP / "SYNTHMOD.PDS"
MOD.write_text("synthmod\n  lda #$01\n  sta $10\n  rts\n", encoding="utf-8")
SLOT = 14

# 6. The positive control. Without this, checks 1-5 are four assertions that have
#    never been observed to fail and might not be capable of it.
class DeliberatelyWrong(pds6502.Assembler):
    """An override missing `addr_ceiling` -- the exact drift that happened."""

    def run_file(self, path, slot=None, origin=None, window_slots=None):
        return super().run_file(path, slot=slot, origin=origin,
                                window_slots=window_slots)


check("the check above CAN fail: an override missing addr_ceiling is reported",
      missing_params(DeliberatelyWrong.run_file) == {"addr_ceiling"},
      f"it reported {sorted(missing_params(DeliberatelyWrong.run_file))!r}, so "
      f"checks 1-5 would not notice a real drift")
try:
    DeliberatelyWrong(bytearray(131072), TMP, []).run_file(
        MOD, slot=SLOT, origin=None, window_slots=None, addr_ceiling=None)
except TypeError as e:
    ok += 1
    print(f"  ok ...and calling it the way run_all does raises TypeError, as it "
          f"did\n         {e}")
else:
    raise AssertionError(
        "an override missing addr_ceiling accepted a call carrying addr_ceiling. "
        "Either python has changed or the check above is not measuring what it "
        "claims, and in either case checks 1-5 cannot be trusted.")

# =====================================================================================
# 7-12. Behaviour, over the synthetic module.
#
# Three instructions, no `org`, no `include`, no symbols -- so the module
# assembles without needing any of the source tree, a cartridge, or a dialect
# question this file would then have to answer. `slot=14` puts it in $E000, which
# is where modrange's Traced records its address range from.
# =====================================================================================
for modname, clsname in TOOLS:
    mod = load(modname)
    cls = getattr(mod, clsname)
    a = cls(bytearray(131072), TMP, [])
    if hasattr(a, "tracing"):
        a.tracing = True
    if hasattr(a, "trace"):
        a.trace = True
    a.run_file(MOD, slot=SLOT, origin=None, window_slots=None, addr_ceiling=None)
    check(f"{modname}.{clsname} assembles a synthetic module and records its bytes",
          len(a.emitted) == 5,
          f"`lda #$01` + `sta $10` + `rts` is 2+2+1 = 5 bytes; got "
          f"{len(a.emitted)}")
    if hasattr(a, "ranges"):
        lo, hi = a.ranges["SYNTHMOD.PDS"]
        check(f"{modname}.{clsname} records the ADDRESS RANGE it assembled at, "
              f"not the file offset",
              (lo, hi) == (0xC000, 0xC004),
              f"got ${lo:04X}-${hi:04X} for slot {SLOT} at $E000; the range has to "
              f"be the address, because that is what decides whether two modules "
              f"overlap")

    # 9-12. Tracing OFF must record nothing. The build assembles every module at
    # all sixteen slots as a search; a tool that records then unions sixteen
    # attempts and reports a module as having written the whole image.
    #
    # A FRESH SUBCLASS, with every container attribute replaced by an empty one,
    # deliberately. `owner`, `bymodule` and `order` are class attributes on these
    # classes, so they are shared by every instance and inherited unchanged by any
    # subclass -- which is a genuine footgun, and here it would mean the "tracing
    # off" probe read the "tracing on" probe's results and passed or failed for
    # the wrong reason. (It is the same hazard `run_all.py` gives every test file
    # its own process to avoid.)
    fresh = {k: (type(v)() if isinstance(v, (dict, list, set)) else v)
             for k, v in vars(cls).items()}
    Off = type(clsname + "Off", (cls,), fresh)
    b = Off(bytearray(131072), TMP, [])
    if hasattr(b, "tracing"):
        b.tracing = False
    if hasattr(b, "trace"):
        b.trace = False
    b.run_file(MOD, slot=SLOT, origin=None, window_slots=None, addr_ceiling=None)
    if modname == "modrange":
        # modrange.Traced has no flag at all: it records unconditionally, which is
        # correct for it, because it only ever runs the project pass -- there is
        # no search pass to confuse it with.
        check("modrange.Traced has no tracing flag, so it cannot half-record",
              not hasattr(b, "tracing"))
        continue
    store = getattr(b, "bymodule", None)
    if store is None:
        store = getattr(b, "foot", None)
    check(f"{modname}.{clsname} records NOTHING while its tracing flag is off",
          not store,
          f"it recorded {list(store)!r} during the search pass. A module that "
          f"looks like it wrote the whole image is a measurement of the search, "
          f"not of the build.")
    order = getattr(b, "order", None)
    if order is not None:
        check(f"{modname}.{clsname}'s module list stays empty while its tracing "
              f"flag is off", order == [], f"order {order!r}")

# =====================================================================================
# 13-16. No swallowed exceptions. This is the bug that turned a TypeError into a
#        printed table, and it is invisible in the output either way.
# =====================================================================================
BARE = re.compile(r"^\s*except\s*:", re.M)
BROAD = re.compile(r"^\s*except\s+(Exception|BaseException)\b", re.M)
for modname, _cls in TOOLS:
    src = (ROOT / "tools" / f"{modname}.py").read_text(encoding="utf-8")
    bare, broad = BARE.search(src), BROAD.search(src)
    check(f"{modname}.py has no bare `except:`",
          bare is None,
          f"line {bare.start() if bare else '-'}: a bare "
          f"except turns any failure into 'the tool printed a table', which is "
          f"how a TypeError became a 0% match over an all-zero image")
    check(f"{modname}.py has no `except Exception` either",
          broad is None,
          f"line {broad.start() if broad else '-'}: an "
          f"instrument that measures may fail; it may not decide what a failure "
          f"means")

# =====================================================================================
# 17. all_slots(). The three modules that moved from ASSUMED to PINNED made
#     ASSUMED_SLOTS[m] a KeyError in four tools at once.
# =====================================================================================
slots = B.all_slots()
missing = [m for m in B.MODULES if m not in slots]
check("all_slots() names a slot for every module build.py lists",
      not missing,
      f"missing {missing!r}. Indexing ASSUMED_SLOTS for one of those is the "
      f"KeyError that took out four tools when X4/X6/X7 were pinned.")
check("every all_slots() value is a real 8 KiB slot, 0-15",
      all(isinstance(v, int) and 0 <= v <= 15 for v in slots.values()),
      f"{slots!r}")
check("where a module is in both PINNED and ASSUMED, all_slots() reports the "
      "pinned one -- a measurement outranks a guess",
      all(slots[m] == B.PINNED_SLOTS[m] for m in B.PINNED_SLOTS),
      f"PINNED {B.PINNED_SLOTS!r} vs all_slots "
      f"{ {m: slots[m] for m in B.PINNED_SLOTS} !r}")

# 18. The list at the top of this file still matches the tree.
for modname, clsname in TOOLS:
    src = (ROOT / "tools" / f"{modname}.py").read_text(encoding="utf-8")
    check(f"tools/{modname}.py still defines {clsname}",
          re.search(rf"^class {clsname}\b", src, re.M) is not None,
          f"a rename would leave this file testing nothing for {modname}")

print(f"placement tools: {ok} checks")
print("all checks passed")