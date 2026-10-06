# tools/bizhawk/bizpath.sh -- WHERE THIS PROJECT'S EMULATOR AND DISPLAY ARE.
#
# Sourced, never executed:
#
#     . "$ROOT/tools/bizhawk/bizpath.sh" || exit 1
#
# On success it exports the resolved answers and returns 0. On failure it
# explains itself on stderr and returns 1, and it NEVER substitutes a different
# answer for the one that was asked for.
#
# WHY THIS FILE EXISTS
# --------------------
# Two names for one thing, in two languages, with a fallback in each:
#
#     tools/bizhawk/run.sh:57   BIZ="${BIZHAWK:-$HOME/code/games/aibeatszelda/...}"
#     src/play/emu.py:65        MAGICIAN_BIZHAWK or (home / "code/games/aibeatszelda/...")
#
# `emu.py` builds the child environment from `dict(os.environ)` and adds only
# `MAGICIAN_*` keys, so it never handed `BIZHAWK` to `run.sh`. MEASURED on this
# machine on 2026-10-05, with nothing running:
#
#     exported             emu.py's guard checked   run.sh launched
#     -------------------  -----------------------  -------------------------
#     BIZHAWK=/new         the OTHER project's dir  /new
#     MAGICIAN_BIZHAWK=/new   /new                   the OTHER project's dir
#
# Both directions disagree, and the second is the one that costs you: the guard
# certifies a directory and the emulator runs out of a different one, so the run
# reports success while measuring the other project's copy. That is this
# repository's standing failure mode -- a harness reporting success while
# measuring nothing -- and the bill came due when a service pointed at the other
# project's emulator directory and a `pkill` killed a 136,526-frame replay that
# was not even its own.
#
# So, three rules, and each exists because of the case above:
#
#   1. ONE canonical name: `MAGICIAN_BIZHAWK`. `BIZHAWK` is accepted as an alias
#      because it is what every existing invocation and every shell test already
#      sets -- removing it would break the override this change is required to
#      keep working. If both are set and they DISAGREE, that is a hard error
#      naming both, because picking one silently is the whole bug.
#   2. NO FALLBACK. `${VAR:-<something that exists>}` cannot say "no"; it can
#      only say "not unless something is there". A missing emulator is a
#      non-zero exit with the fix in the message.
#   3. THE DEFAULT IS A LOCATION THIS PROJECT OWNS, not a path that happens to
#      exist:
#
#          $HOME/code/games/magician-nes-bizhawk/BizHawk-2.11.1-linux-x64
#
#      Provisioned by `make emu-setup` (tools/bizhawk/setup.sh), which downloads
#      the official tarball and builds the one thing BizHawk does not ship.
#
# THE DIRECTORY IS NAMED `-linux-x64`, NOT `-win-x64`
# ----------------------------------------------------
# The other project's copy is called `BizHawk-2.11.1-win-x64` because ITS
# launcher derives the name from the checkout. Ours is not named that way because
# nothing here derives anything: the path is a variable. Naming it for what it is
# means the two can never be mistaken for one file, and `BizHawk-2.11.1-win-x64`
# appearing in a log line reads immediately as "that was not us".
#
# WHAT IS VALIDATED, AND WHY ONLY THAT
# -------------------------------------
# What `run.sh` will actually touch, measured on this machine 2026-10-05 by
# diffing a pristine `BizHawk-2.11.1-linux-x64` tarball against a used install:
# of 449 files, **443 are byte-identical** and the only differences are paths
# BizHawk creates at runtime --
#
#     config.ini                          BizHawk's own settings, written on the
#                                         first launch, ~103 KB, and it carries
#                                         the RecentROM/Lua-console history
#     config.ini.runsh.bak                run.sh's own copy, made before launch
#     EmuHawkMono_laststdout.txt          written by EmuHawkMono.sh itself, into
#     EmuHawkMono_laststderr.txt          whatever directory it was launched from
#     NES/SaveRAM/                        battery saves, written by BizHawk
#     NES/State/                          savestates, written by BizHawk
#
# So the install is worth having as a real 150 MB copy rather than symlinks into
# somebody else's tree: 145 MB of it never changes, and a symlink would satisfy
# a path grep while remaining exactly the dependency this file removes. It is
# also why the directory must be WRITABLE -- `EmuHawkMono.sh` writes two files
# into `$PWD` on every launch, so a read-only install cannot run at all.
#
# `Lua/socket/core.so` is NOT checked here. `run.sh` also drives scripts that
# never open a socket; only `src/play/bridge.lua` needs LuaSocket, and it is
# checked where it is needed (`emu.py`) and reported by `make doctor`.

# shellcheck shell=bash
# ------------------------------------------------------------------ constants
# Version and directory name, in one place, because `tools/bizhawk/setup.sh`
# installs exactly this and a literal in two files is two places to forget.
: "${MAGICIAN_BIZHAWK_VERSION:=2.11.1}"
: "${MAGICIAN_BIZHAWK_DIRNAME:=BizHawk-${MAGICIAN_BIZHAWK_VERSION}-linux-x64}"
# The default install root. Overridable wholesale with MAGICIAN_BIZHAWK_HOME.
: "${MAGICIAN_BIZHAWK_HOME:=${HOME:-}/code/games/magician-nes-bizhawk}"
if [ -z "${HOME:-}" ] && [ -z "${MAGICIAN_BIZHAWK:-}" ] && [ -z "${BIZHAWK:-}" ]; then
  _magician_biz_say "bizpath.sh: FAIL -- HOME is not set and no emulator directory was"
  _magician_biz_say "            named, so the default location cannot be built. A systemd"
  _magician_biz_say "            service is the usual cause: it does not inherit the session's"
  _magician_biz_say "            environment. Either give it a HOME= or, better, name the"
  _magician_biz_say "            directory outright:"
  _magician_biz_say "                MAGICIAN_BIZHAWK=/path/to/BizHawk-2.11.1-linux-x64"
  return 1
fi
: "${MAGICIAN_BIZHAWK_DEFAULT:=$MAGICIAN_BIZHAWK_HOME/$MAGICIAN_BIZHAWK_DIRNAME}"

# ------------------------------------------------------------------- messages
_magician_biz_say() { printf '%s\n' "$*" >&2; }

_magician_biz_missing() {
  # $1 = the directory that was asked for and is not usable.
  local d="$1"
  _magician_biz_say "run.sh/bizpath: no usable BizHawk at $d"
  _magician_biz_say ""
  _magician_biz_say "  This project runs its OWN emulator. It does not share one with"
  _magician_biz_say "  another checkout: two projects sharing one BizHawk directory have"
  _magician_biz_say "  no lock on its config.ini or its NES/SaveRAM, and a service from"
  _magician_biz_say "  one can kill a long replay belonging to the other."
  _magician_biz_say ""
  _magician_biz_say "  To install one:"
  _magician_biz_say "      make emu-setup"
  _magician_biz_say "  which puts $MAGICIAN_BIZHAWK_DIRNAME (about 150 MB, from the"
  _magician_biz_say "  official BizHawk tarball) in"
  _magician_biz_say "      $MAGICIAN_BIZHAWK_HOME"
  _magician_biz_say "  and builds Lua/socket/core.so, which BizHawk does not ship and"
  _magician_biz_say "  which src/play/bridge.lua needs."
  _magician_biz_say ""
  _magician_biz_say "  Or point at an install you already have:"
  _magician_biz_say "      MAGICIAN_BIZHAWK=/path/to/BizHawk-2.11.1-linux-x64"
  _magician_biz_say ""
  _magician_biz_say "  What was asked for here, in precedence order:"
  _magician_biz_say "      MAGICIAN_BIZHAWK=${MAGICIAN_BIZHAWK:-<unset>}"
  _magician_biz_say "      BIZHAWK=${BIZHAWK:-<unset>}   (accepted alias)"
  _magician_biz_say "      default  = $MAGICIAN_BIZHAWK_DEFAULT"
}

# ------------------------------------------------------------------ resolution
_magician_resolve() {
  local canon="${MAGICIAN_BIZHAWK:-}"
  local alias="${BIZHAWK:-}"
  local asked source

  # 1. ONE NAME, ONE ANSWER. Two names set to different directories is not a
  #    preference to be resolved; it is a contradiction, and picking one of them
  #    is precisely how a guard ends up certifying a path nothing runs from.
  if [ -n "$canon" ] && [ -n "$alias" ] && [ "$canon" != "$alias" ]; then
    _magician_biz_say "bizpath.sh: FAIL -- the emulator directory is set twice, differently."
    _magician_biz_say ""
    _magician_biz_say "      MAGICIAN_BIZHAWK = $canon"
    _magician_biz_say "      BIZHAWK           = $alias"
    _magician_biz_say ""
    _magician_biz_say "  These are one setting with two names, not two settings. They"
    _magician_biz_say "  disagree, so it is not knowable from here which one the run would"
    _magician_biz_say "  use, and picking one would be a guess in the one place this"
    _magician_biz_say "  project cannot afford a guess. Unset one of them."
    return 1
  fi

  if [ -n "$canon" ]; then
    asked="$canon"; source="MAGICIAN_BIZHAWK"
  elif [ -n "$alias" ]; then
    asked="$alias"; source="BIZHAWK (alias for MAGICIAN_BIZHAWK)"
  else
    asked="$MAGICIAN_BIZHAWK_DEFAULT"; source="the default location"
  fi

  # 2. ABSOLUTE, or `cd "$BIZ"` sends the launch somewhere else. `EmuHawkMono.sh`
  #    does `cd $(dirname $(realpath $0))` and run.sh's own `--config`, SRAM and
  #    `LD_LIBRARY_PATH` paths are all relative to $BIZ, so a relative BIZ is a
  #    different directory by the time anything reads it. This is the same trap
  #    as a relative ROM path, which made BizHawk fall back to NullHawk while the
  #    run reported success.
  case "$asked" in
    /*) ;;
    *)  asked="$PWD/$asked" ;;
  esac

  # 3. IT HAS TO BE A BIZHAWK. `[ -d ]` was the whole check, and an empty
  #    directory passes it: the launch then dies on a missing file with a
  #    Mono stack trace, or -- worse, in some configurations -- runs.
  if [ ! -d "$asked" ]; then
    _magician_biz_missing "$asked"
    return 1
  fi
  # `readlink -f` now, so a symlinked install resolves to one canonical string:
  # the log line, the guard's path and the directory actually used are then the
  # same text, which is the whole point of resolving in one place.
  asked="$(readlink -f "$asked")"
  MAGICIAN_BIZHAWK_DIR="$asked"

  if [ ! -f "$asked/EmuHawkMono.sh" ]; then
    _magician_biz_say "bizpath.sh: FAIL -- $asked has no EmuHawkMono.sh, so it is not a"
    _magician_biz_say "            BizHawk install. A directory that exists is not the same"
    _magician_biz_say "            thing as an emulator; this refuses rather than launching a"
    _magician_biz_say "            path and reporting what it found there. `make emu-setup`"
    _magician_biz_say "            installs one, or set MAGICIAN_BIZHAWK=/path/to/a/real/one."
    return 1
  fi
  if [ ! -x "$asked/EmuHawkMono.sh" ]; then
    _magician_biz_say "bizpath.sh: FAIL -- $asked/EmuHawkMono.sh is not executable."
    _magician_biz_say "            MEASURED on this machine, and it fails as a permission error"
    _magician_biz_say "            rather than as a missing emulator. chmod +x it, or reinstall"
    _magician_biz_say "            with \`make emu-setup\`."
    return 1
  fi
  if [ ! -d "$asked/dll" ]; then
    _magician_biz_say "bizpath.sh: FAIL -- $asked has no dll/ directory."
    _magician_biz_say "            LD_LIBRARY_PATH is set to \$BIZ/dll:\$BIZ, so without it"
    _magician_biz_say "            \`mono EmuHawk.exe\` cannot resolve BizHawk's own assemblies"
    _magician_biz_say "            and the session dies before the Lua bridge is ever loaded."
    return 1
  fi
  # The launcher writes EmuHawkMono_laststdout.txt and _laststderr.txt into its
  # own directory on every single launch (two `tee` processes, see the script),
  # and BizHawk writes config.ini and NES/State/ beside them. A read-only
  # install therefore cannot run at all -- so it is refused here rather than
  # three seconds later inside a Mono stack trace.
  if [ ! -w "$asked" ]; then
    _magician_biz_say "bizpath.sh: FAIL -- $asked is not writable."
    _magician_biz_say "            EmuHawkMono.sh writes EmuHawkMono_last*.txt into its own"
    _magician_biz_say "            directory on every launch and BizHawk writes config.ini and"
    _magician_biz_say "            NES/State/ beside them, so a read-only install cannot run."
    return 1
  fi

  # 4. ALL THREE NAMES, ONE VALUE. `BIZHAWK` is exported as well so that anything
  #    else reading the historical name gets the SAME directory rather than
  #    setting its own. This is the join that was missing: `emu.py` used to
  #    inherit whatever `BIZHAWK` happened to be and never say so.
  MAGICIAN_BIZHAWK="$asked"
  BIZHAWK="$asked"
  MAGICIAN_BIZHAWK_SOURCE="$source"
  export MAGICIAN_BIZHAWK BIZHAWK \
         MAGICIAN_BIZHAWK_DIR MAGICIAN_BIZHAWK_HOME MAGICIAN_BIZHAWK_DEFAULT \
         MAGICIAN_BIZHAWK_DIRNAME MAGICIAN_BIZHAWK_VERSION

  # ------------------------------------------------------------------ display
  # :2, because the neighbouring project uses :1 and the point of a separate
  # display is that a search in either can be watched while the other runs.
  #
  # A DISPLAY already in the environment is respected, and the source is named,
  # because a human at the desktop set that on purpose and silently overriding
  # it is this project's own bug class. The case that bites is the other one:
  # a systemd user service does NOT inherit the session's DISPLAY, so it gets
  # :2 -- which is the correct answer here, and is why the default exists rather
  # than why it is avoided. `tools/systemd/*.service` set it explicitly anyway,
  # so the dependency on a default is not load-bearing.
  if [ -n "${MAGICIAN_DISPLAY:-}" ]; then
    MAGICIAN_DISPLAY_SOURCE="MAGICIAN_DISPLAY"
  elif [ -n "${DISPLAY:-}" ]; then
    MAGICIAN_DISPLAY="$DISPLAY"
    MAGICIAN_DISPLAY_SOURCE="inherited DISPLAY"
  else
    MAGICIAN_DISPLAY="${MAGICIAN_DISPLAY_DEFAULT:-:2}"
    MAGICIAN_DISPLAY_SOURCE="the default"
  fi
  DISPLAY="$MAGICIAN_DISPLAY"
  export MAGICIAN_DISPLAY DISPLAY
  return 0
}

_magician_resolve || return 1
