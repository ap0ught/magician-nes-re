#!/usr/bin/env bash
# Install this project's own BizHawk.  `make emu-setup`
#
#   tools/bizhawk/setup.sh [--force] [--dest DIR]
#
# WHY THIS EXISTS
# ---------------
# Until 2026-10-05 this project ran BizHawk out of ANOTHER project's checkout,
# because three files carried a hardcoded default path into it. Two projects
# sharing one BizHawk directory share its `config.ini` and its `NES/SaveRAM/`, with
# no lock on either, and the bill came due when a service in this project launched
# from that directory and a `pkill` killed a 136,526-frame replay belonging to the
# other one. `tools/bizhawk/bizpath.sh` is the resolver; this is the thing it
# resolves to.
#
# There is nothing clever here and that is deliberate. The obvious alternative --
# symlink the big read-only parts (dll/, EmuHawk.exe, Lua/, the NES core) into the
# other project's install and keep only config.ini and NES/SaveRAM/ local -- was
# measured and rejected:
#
#   diff -rq <pristine BizHawk-2.11.1-linux-x64> <a used install>
#   -> 443 of 449 files byte-identical; the only differences are six paths
#      BizHawk creates at runtime.
#
# So 145 MB of the 150 MB never changes, and a symlink would satisfy a path grep
# while remaining exactly the dependency this removes -- and `EmuHawkMono.sh`
# writes `EmuHawkMono_laststdout.txt` and `_laststderr.txt` into whatever
# directory it is launched from, so the "genuinely mutable" part is not only
# config.ini. A real copy costs disk we have and removes the coupling entirely.
#
# TWO THINGS THE TARBALL DOES NOT PROVIDE
# ----------------------------------------
# 1. `EmuHawkMono.sh` is shipped but not marked executable.
# 2. **`Lua/socket/core.so`.** BizHawk's tarball carries only the Windows
#    `Lua/socket/core.dll`, and `src/play/bridge.lua:50` is
#    `require("socket.core")`. Without this module the bridge dies on its first
#    line and the harness reports a CONNECT TIMEOUT, which reads like a networking
#    problem rather than a missing 100 KB file. It has to be built against the Lua
#    5.4 that NLua embeds.
#
# The build omits `-llua54` DELIBERATELY. NLua embeds Lua 5.4 and exports the
# `lua_*` symbols, so the module's undefined references bind to the host at
# `dlopen` time; linking liblua54 would hand it a second, private copy of the
# runtime. It looks like a missing flag at a glance, so it is commented at the
# point it would be "fixed".
#
# Both downloads are pinned by SHA-256, measured on 2026-10-05. A pinned digest
# is the difference between "we installed BizHawk 2.11.1" and "we installed
# whatever the URL served today", and the second is what a run gets measured
# against.
#
# WHAT IT NEEDS: curl, tar, gcc, mono, and the lua5.4 headers. It does not install
# packages -- no `sudo` in a build script -- and says which ones are missing
# instead.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Constants only. Sourcing the resolver here would be refused, because the whole
# point of this script is to run before there is anything to resolve.
MAGICIAN_BIZHAWK_CONSTANTS_ONLY=1
. "$ROOT/tools/bizhawk/bizpath.sh"

# Pinned on 2026-10-05 from the URLs below. Changing a version means changing
# these, which is why they are here rather than in a lockfile nobody updates.
BIZHAWK_URL="https://github.com/TASVideos/BizHawk/releases/download/${MAGICIAN_BIZHAWK_VERSION}/BizHawk-${MAGICIAN_BIZHAWK_VERSION}-linux-x64.tar.gz"
BIZHAWK_SHA256="38c9c12287e337a0a6923fd527767c853457d61d71e7d8ad1a772d64ce8bc93f"
LUASOCKET_URL="https://github.com/lunarmodules/luasocket/archive/refs/tags/v3.1.0.tar.gz"
LUASOCKET_SHA256="bf033aeb9e62bcaa8d007df68c119c966418e8c9ef7e4f2d7e96bddeca9cca6e"

FORCE=0
DEST="${MAGICIAN_BIZHAWK_DIR}"
while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1 ;;
    --dest)  DEST="${2:?--dest needs a directory}"; shift ;;
    -h|--help) sed -n '2,45p' "$0"; exit 0 ;;
    *) echo "setup.sh: unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '==> %s\n' "$*"; }
die() { printf 'setup.sh: FAIL -- %s\n' "$*" >&2; exit "${1:-1}"; }

# ------------------------------------------------------------------ 0. tools
missing=""
for t in curl tar gcc mono; do
  command -v "$t" >/dev/null || missing="$missing $t"
done
[ -f /usr/include/lua5.4/lua.h ] || missing="$missing lua5.4-headers"
if [ -n "$missing" ]; then
  echo "setup.sh: FAIL -- missing:$missing" >&2
  echo "  On Arch/CachyOS:" >&2
  echo "      sudo pacman -S --needed curl tar gcc mono lua54" >&2
  echo "  This script does not install packages itself; it has no business" >&2
  echo "  reaching for sudo from inside a build step." >&2
  exit 4
fi
say "tools present: curl tar gcc mono, lua5.4 headers"

# --------------------------------------------------------------- 1. BizHawk
mkdir -p "$(dirname "$DEST")"
if [ -x "$DEST/EmuHawkMono.sh" ] && [ "$FORCE" = "0" ]; then
  say "BizHawk already installed at $DEST (use --force to reinstall)"
else
  if [ -e "$DEST" ] && [ "$FORCE" = "0" ]; then
    # Refuse rather than merge into a directory we did not create. Merging would
    # leave whatever the previous occupant wrote -- and "the previous occupant"
    # is exactly the ambiguity this whole change is about.
    die 5 "$DEST exists but has no EmuHawkMono.sh. Refusing to merge into a
    directory this script did not create: it may be somebody else's, and the
    reason it exists is that we do not know whose things we are in.
    Move it aside yourself, or pass --dest somewhere else."
  fi
  say "downloading BizHawk $MAGICIAN_BIZHAWK_VERSION (linux-x64, ~93 MB)"
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/magician-emu-setup.XXXXXX")"
  trap 'rm -rf "$tmp"' EXIT
  curl -fL --retry 3 -o "$tmp/bh.tar.gz" "$BIZHAWK_URL" \
    || die 6 "could not download $BIZHAWK_URL"
  got="$(sha256sum "$tmp/bh.tar.gz" | cut -d' ' -f1)"
  if [ "$got" != "$BIZHAWK_SHA256" ]; then
    die 7 "BizHawk tarball sha256 mismatch.
  expected $BIZHAWK_SHA256
  got      $got
  The pin in tools/bizhawk/setup.sh is what makes 'BizHawk 2.11.1' mean a specific
  set of bytes. Do not update it to match a download without finding out why it
  changed."
  fi
  say "sha256 matches the pin"
  say "extracting"
  tar xzf "$tmp/bh.tar.gz" -C "$tmp"
  src="$tmp/BizHawk-${MAGICIAN_BIZHAWK_VERSION}-linux-x64"
  [ -d "$src" ] || die 8 "the tarball did not contain BizHawk-${MAGICIAN_BIZHAWK_VERSION}-linux-x64"
  rm -rf "$DEST"
  mv "$src" "$DEST"
  say "installed to $DEST"
fi

chmod +x "$DEST/EmuHawkMono.sh" 2>/dev/null || true
[ -x "$DEST/EmuHawkMono.sh" ] || die 9 "$DEST/EmuHawkMono.sh is not executable and chmod failed"

# ---------------------------------------------------------- 2. LuaSocket .so
SO="$DEST/Lua/socket/core.so"
if [ -f "$SO" ] && [ "$FORCE" = "0" ]; then
  say "LuaSocket core.so already built"
else
  say "building LuaSocket 3.1.0 core.so for NLua's embedded Lua 5.4"
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/magician-emu-setup.XXXXXX")"
  trap 'rm -rf "$tmp"' EXIT
  curl -fL --retry 3 -o "$tmp/ls.tar.gz" "$LUASOCKET_URL" \
    || die 10 "could not download $LUASOCKET_URL"
  got="$(sha256sum "$tmp/ls.tar.gz" | cut -d' ' -f1)"
  if [ "$got" != "$LUASOCKET_SHA256" ]; then
    die 11 "luasocket tarball sha256 mismatch.
  expected $LUASOCKET_SHA256
  got      $got"
  fi
  tar xzf "$tmp/ls.tar.gz" -C "$tmp"
  mkdir -p "$(dirname "$SO")"
  (
    cd "$tmp/luasocket-3.1.0/src"
    # NO -llua54, on purpose. See the header: NLua embeds Lua 5.4 and exports the
    # lua_* symbols, so they bind to the host at dlopen. Linking liblua54 gives the
    # module its own private copy of the runtime and it stops working.
    gcc -O2 -fPIC -shared -std=gnu99 \
      -DLUASOCKET_INET \
      -DLUASOCKET_API='__attribute__((visibility("default"))) extern' \
      -I/usr/include/lua5.4 \
      -o "$SO" \
      luasocket.c auxiliar.c buffer.c compat.c except.c inet.c io.c mime.c \
      options.c select.c timeout.c tcp.c udp.c usocket.c unix.c unixstream.c \
      unixdgram.c
  ) || die 12 "the LuaSocket build failed; the compiler output is above"
  [ -s "$SO" ] || die 13 "$SO is 0 bytes -- a zero-byte build is not a build"
  say "built $SO ($(stat -c %s "$SO") bytes)"
fi

# ------------------------------------------------------------------ 3. report
cat <<EOF

BizHawk is installed at:
    $DEST

Next:
    make doctor          report the whole setup, including the display
    make display-up      start Xephyr on :2 (this project's own)
    make probe           run the rebuilt ROM beside the cartridge
    python3 src/play/smoke.py    the bridge, end to end

This install is private to this checkout. Nothing in this project reads or writes
inside any other project's directory, and 'make check-isolation' is how that is
demonstrated rather than asserted.
EOF
