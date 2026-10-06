#!/usr/bin/env bash
# Prove, by measurement, that a run did not touch a directory it must not touch.
#   `make check-isolation`
#
#   tools/isolation.sh <directory> -- <command>
#   tools/isolation.sh <directory> --snapshot <outfile>
#   tools/isolation.sh <directory> <before-manifest> <after-manifest>
#   ISOLATE_CMD='<command>' tools/isolation.sh <directory>
#
#   make check-isolation ISOLATE=/path/to/other/BizHawk-... cmd='make probe'
#
# TWO WAYS TO PASS THE COMMAND, AND WHY BOTH
# -------------------------------------------
# `-- <command>` is the documented form and the one the test suite drives.
# `ISOLATE_CMD` in the environment exists because `make check-isolation cmd='...'`
# has to survive make's own expansion before the command ever reaches a shell --
# make will happily eat `$(python3 tools/cartref.py)` as a make variable, and the
# cartridge path it produces contains spaces, because every dump filename on this
# machine does. `src/testing/test_shell_tools.py` drives the `--` form; the
# Makefile uses the environment form; both funnel into one code path and one set
# of exit codes.
#
# WHY THE SNAPSHOTS ARE TAKEN HERE AND NOT BY THE CALLER
# ------------------------------------------------------
# Because the first draft of the test suite for this file took its before-snapshot,
# edited the tree, and only then invoked the tool -- so both snapshots were taken
# after the edit and every detection check passed against a tool that had detected
# nothing. Six checks, green, meaningless. The mutation therefore has to happen
# INSIDE the command, which is why this script brackets it.
#
# WHY THIS IS A TOOL AND NOT A SENTENCE IN THE README
# ---------------------------------------------------
# The claim "this project does not read or write inside another project's emulator
# directory" is worth nothing as prose, and it is exactly the kind of claim this
# project has been wrong about: for months it ran BizHawk out of a sibling
# checkout, printed healthy numbers, and the coupling only became visible when a
# `pkill` killed a 136,526-frame replay belonging to that sibling. There was a
# `config.ini.runsh.bak` sitting in it as physical evidence.
#
# SO: a recursive listing with sizes AND mtimes, plus a sha256 of every file,
# before and after. All three halves are needed:
#   * a content change is caught by the sha256 half;
#   * a RENAME -- identical bytes under a new name -- is caught only by the listing;
#   * an mtime-only touch -- or a file written and written back with identical
#     content -- is caught only by the mtime. That last one is the case a
#     checksum-only manifest calls "unchanged", and it is the reason this is not
#     just a checksum of the tree.
#
# WHY A SYMLINK IS NOT ENOUGH, and this does not catch it either
# -----------------------------------------------------------
# A symlink from this project's install into another project's would pass every
# check here, because what is compared is the content of the OTHER directory and a
# symlink changes nothing there. A naive `grep` for the sibling's name in our tree
# would also pass. That is why the install is a real copy rather than a symlink --
# see tools/bizhawk/setup.sh -- and why `src/testing/nodep.py` exists: it looks at
# code, not at filenames, and the copy is what makes the question "is there any
# dependency at all" answerable rather than "is there any dependency we can see".
# Stated here so the next person does not mistake this tool for a complete answer.
#
# EXIT CODES
#   0  unchanged
#   2  the directory does not exist, or no command was given (nothing to compare;
#      say so, do not pass -- a check that cannot fail is worse than no check)
#   3  CHANGED -- something wrote, created, deleted, renamed or modified it
#   4  the command itself failed (the isolation may still hold; both are reported)

set -uo pipefail

snap() {
  # $1 = directory. Emits ONE manifest: a sorted listing with type, size and mtime
  # in nanoseconds, then a sha256 per file. Both halves are needed and neither is
  # sufficient: a rename shows up in the listing and not in the checksums, and a
  # modification shows up in both.
  local d="$1"
  (
    cd "$d" 2>/dev/null || exit 2
    find . -printf '%y %10s %T@ %p\n' | LC_ALL=C sort -k4
    find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum
  )
}

usage() {
  sed -n '2,60p' "$0" >&2
  exit 2
}

[ $# -ge 1 ] || usage

DIR="$1"; shift

case "${1:-}" in
--snapshot)
  OUT="${2:?--snapshot needs an output file}"
  [ -d "$DIR" ] || { echo "isolation.sh: no directory at $DIR" >&2; exit 2; }
  snap "$DIR" > "$OUT"
  echo "isolation.sh: wrote $OUT ($(wc -l < "$OUT") lines)"
  exit 0
  ;;
esac

BEFORE=""
AFTER=""
CMD=""
if [ "${1:-}" = "--" ]; then
  CMD="${2:?-- needs a command}"
  shift 2
elif [ -n "${ISOLATE_CMD:-}" ]; then
  CMD="$ISOLATE_CMD"
fi

if [ -n "$CMD" ]; then
  BEFORE="$(mktemp "${TMPDIR:-/tmp}/magician-iso-before.XXXXXX")"
  AFTER="$(mktemp "${TMPDIR:-/tmp}/magician-iso-after.XXXXXX")"
  trap 'rm -f "$BEFORE" "$AFTER"' EXIT
elif [ $# -ge 2 ] && [ -f "$1" ] && [ -f "$2" ]; then
  BEFORE="$1"; AFTER="$2"
else
  echo "isolation.sh: nothing to do." >&2
  echo "  Give a command:   tools/isolation.sh <dir> -- 'make probe'" >&2
  echo "  or two manifests: tools/isolation.sh <dir> <before> <after>" >&2
  echo "  or one manifest:  tools/isolation.sh <dir> --snapshot <outfile>" >&2
  echo "  Exiting 2 without comparing, rather than reporting OK: a check that" >&2
  echo "  silently has nothing to measure is the failure this project keeps" >&2
  echo "  running into, and it is worse than a check that is absent." >&2
  exit 2
fi

if [ -n "$CMD" ]; then
  if [ ! -d "$DIR" ]; then
    echo "isolation.sh: FAIL -- no directory at $DIR" >&2
    echo "  Nothing to compare. Passing because there is nothing there would be" >&2
    echo "  a check that cannot fail, which is worse than no check." >&2
    exit 2
  fi
  snap "$DIR" > "$BEFORE"
  echo "isolation.sh: baseline -- $(wc -l < "$BEFORE") entries under $DIR"
  echo "isolation.sh: manifest digest $(sha256sum "$BEFORE" | cut -d' ' -f1)"
  echo "isolation.sh: ---- running: $CMD"
  # `bash -c`, not `eval`: one layer of quoting, and the caller's shell already
  # had its say about which arguments are quoted.
  ( cd "$(dirname "$0")/.." && bash -c "$CMD" )
  CMDRC=$?
  echo "isolation.sh: ---- command exited $CMDRC"
  snap "$DIR" > "$AFTER"
else
  [ -f "$BEFORE" ] && [ -f "$AFTER" ] || usage
fi

echo "isolation.sh: after    -- $(wc -l < "$AFTER") entries under $DIR"
echo "isolation.sh: manifest digest $(sha256sum "$AFTER" | cut -d' ' -f1)"

RC=0
if diff -u "$BEFORE" "$AFTER" > /tmp/magician-iso-diff.$$ 2>&1; then
  echo "isolation.sh: OK -- $DIR is byte-for-byte and mtime-for-mtime unchanged."
  echo "isolation.sh:       every file's sha256 is identical and so is every"
  echo "isolation.sh:       size and mtime. Nothing was created, written or deleted."
else
  echo "isolation.sh: FAIL (3) -- $DIR CHANGED:" >&2
  head -60 /tmp/magician-iso-diff.$$ | sed 's/^/    /' >&2
  echo >&2
  echo "isolation.sh: Something in that tree was read-modify-written by a run that" >&2
  echo "isolation.sh: was supposed to be touching nothing there. Find out what:" >&2
  echo "isolation.sh:     grep -rn '$(basename "$DIR")' --include='*.sh' --include='*.py' ." >&2
  rm -f /tmp/magician-iso-diff.$$
  RC=3
fi
rm -f /tmp/magician-iso-diff.$$ 2>/dev/null

if [ -n "$CMD" ] && [ "$CMDRC" != "0" ]; then
  echo "isolation.sh: note -- the command itself exited $CMDRC." >&2
  echo "isolation.sh:        That is a separate failure from the isolation result," >&2
  echo "isolation.sh:        and both are reported rather than one standing in for" >&2
  echo "isolation.sh:        the other. Collapsing them is how a passing harness" >&2
  echo "isolation.sh:        reports success while measuring nothing." >&2
  [ "$RC" = "0" ] && RC=4
fi
exit "$RC"
