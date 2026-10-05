#!/usr/bin/env bash
# Fail if anything that must never be committed is staged.
#
# Why this is a script and not a paragraph in LEGAL.md
# ---------------------------------------------------
# LEGAL.md already says the cartridge is never committed and that third-party
# material is not either. A new tool arrives with a 44 003-frame movie on disk
# and a well-meaning `--lua out.lua` a few centimetres away from a ROM, and the
# cheapest thing at that moment is `git add -A`. So the rule is enforced at the
# one moment it can still be cheap.
#
# What it refuses, and why each one is separate:
#
#   vendor/*              the submodule is read-only, unmodified, pinned at
#                         bf653a407cd97e4dfdca665063f25d8b44da130a. A staged
#                         change there is either a mistake or something much
#                         worse.
#   *.nes and friends     the cartridge. Never in the tree, in any form.
#   *.fm2                 the TAS movie. Third-party and copyrighted, and it is
#                         the most tempting file in this project to commit by
#                         accident, because the harness wants a path to it and a
#                         path written into a committed script is one
#                         `git add tools/` away from staging it. This is the
#                         case that actually needed the guard.
#   pds-text/             generated from the submodule's binary containers.
#   tools/bizhawk/out/    capture output: screenshots of cartridge-derived
#                         imagery, and large reproducible memory dumps.
#   *.sav, *.srm, ...     save files.
#   any file over 4 MiB   not a rule about content, a catch-all: a cart is 256
#                         KiB and a movie 693 KiB, so a legitimate file in this
#                         repo is never near that, and anything that is gets a
#                         human to look at it.
#
# `git diff --cached --name-only` lists every staged path -- additions,
# modifications, deletions and renames alike -- so one call is the whole
# enumeration. Exits 0 when the index is clean, so it can be wired into a hook
# and left there.
#
#   make guard          run it now
#   make install-hooks  wire it into .git/hooks/pre-commit as well

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# `--name-only` alone is enough and deliberately so. Anything more elaborate here
# has a way of *missing* a staged path, which is the one thing a guard must not
# do: the earlier version of this script ran three different git invocations and
# deduplicated their output, which meant a path git happened not to print was
# simply not checked.
STAGED=$(git diff --cached --name-only --diff-filter=ACDMRTUXB)
[ -n "$STAGED" ] || STAGED=$(git diff --cached --name-only)

bad=0
count=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  count=$((count + 1))
  case "$f" in
    vendor/*)
      echo "FAIL: staged under vendor/ -- that tree is read-only and pinned:" "$f"; bad=1 ;;
    *.nes|*.unf|*.unif|*.unl|*.fds|*.nes2|*.nsf|*.nsf2)
      echo "FAIL: staged a cartridge image: $f"; bad=1 ;;
    *.fm2|*.fm2s|*.bk2|*.bkm|*.gmv|*.vbm|*.inp|*.m64|*.gmw|*.vfr|*.lsmv)
      echo "FAIL: staged a movie file (third-party and copyrighted): $f"; bad=1 ;;
    *.zip|*.7z)
      echo "FAIL: staged an archive, which could hold a cartridge or a movie: $f"; bad=1 ;;
    pds-text/*)
      echo "FAIL: staged generated source text: $f"; bad=1 ;;
    tools/bizhawk/out/*|shots/*|out/*)
      echo "FAIL: staged capture or scratch output: $f"; bad=1 ;;
    *.sav|*.srm|*.srm2|*.battery|*.SaveRAM)
      echo "FAIL: staged a save file: $f"; bad=1 ;;
  esac
done <<<"$STAGED"

# Size catch-all. A dump of this title is 256 KiB and the movie is 693 KiB; the
# largest legitimate file in this repository is a text document of a few tens of
# KiB, so 4 MiB is far above anything real and far below anything a base64 blob
# of a cartridge would be.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if [ -f "$f" ]; then
    sz=$(stat -c %s "$f")
    if [ "$sz" -gt $((4 * 1024 * 1024)) ]; then
      echo "FAIL: staged file is $sz bytes, over the 4 MiB limit -- get a human:" "$f"
      bad=1
    fi
  fi
done <<<"$STAGED"

[ "$count" -eq 0 ] || echo "guard_staged: $count file(s) staged, $bad violation(s)"
exit $bad