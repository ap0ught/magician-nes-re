#!/usr/bin/env bash
# Prove, by measurement, that a run did not touch a directory it must not touch.
#   `make check-isolation`
#
#   tools/isolation.sh <directory> [-- <command to run>]
#   tools/isolation.sh <directory> --snapshot <outfile>
#   tools/isolation.sh <before> <after>
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
# before and after. Mtimes are in the snapshot as well as the checksums because a
# file can be written and written back with identical content -- unlikely, but the
# whole point is not to argue about likelihood.
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
#   2  the directory does not exist (nothing to compare; say so, do not pass)
#   3  CHANGED -- something wrote, created, deleted or modified it
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
  sed -n '2,32p' "$0" >&2
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
  BEFORE="$(mktemp "${TMPDIR:-/tmp}/magician-iso-before.XXXXXX")"
  AFTER="$(mktemp "${TMPDIR:-/tmp}/magician-iso-after.XXXXXX")"
  trap 'rm -f "$BEFORE" "$AFTER"' EXIT
elif [ $# -ge 2 ] && [ -f "$1" ]; then
  # the compare-two-manifests form
  BEFORE="$1"; AFTER="$2"
else
  usage
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
  ( cd "$(dirname "$0")/.." && eval "$CMD" )
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
