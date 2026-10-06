#!/usr/bin/env bash
# Link the two units in tools/systemd/ into ~/.config/systemd/user.  `make install-units`
#
# WHY A TARGET AND NOT SOMETHING THAT JUST HAPPENS
# ------------------------------------------------
# A checkout that wrote units into your systemd directory behind your back would be
# doing something invasive, and a `make` that silently installs a service which
# then launches an emulator is worse than the coupling this whole change removes.
# So: an explicit target, links rather than copies (so the unit in your systemd
# directory cannot drift from the one in this repository), and a message saying
# what it did.
#
# WHY THE UNITS ARE NOT COMMITTED ANYWHERE ELSE
# ---------------------------------------------
# `~/.config/systemd/user/` is outside this repository and untracked, exactly as
# `~/.config/i3/config` is for the neighbouring project. A fresh clone therefore
# gets neither the unit nor the i3 config, and has to be given them again. That is
# stated in README.md rather than worked around, because working around it would
# mean committing a file that belongs to the user's system.
#
# EXIT CODES
#   0 linked     2 no ~/.config/systemd/user   3 systemd not available to this user

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/tools/systemd"
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

[ -d "$SRC" ] || { echo "install_units.sh: no units at $SRC" >&2; exit 2; }
mkdir -p "$DEST"

units=()
for u in "$SRC"/*.service; do
  units+=("$(basename "$u")")
done
[ "${#units[@]}" -gt 0 ] || { echo "install_units.sh: no .service files in $SRC" >&2; exit 2; }

echo "install_units: linking ${#units[@]} unit(s) into $DEST"
for name in "${units[@]}"; do
  # Replace rather than fail if one is already there: re-running after a git pull
  # has to work, and it is the point of a symlink.
  rm -f "$DEST/$name"
  ln -s "$SRC/$name" "$DEST/$name"
  echo "  $DEST/$name -> $SRC/$name"
done

if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user daemon-reload
  echo "install_units: daemon-reload done."
  cat <<EOF

Enable them with:
    systemctl --user enable --now magician-nes-display.service

Then run a milestone under systemd:
    systemctl --user start  magician-nes-run@m1_first_town
    journalctl --user -u magician-nes-run@m1_first_town -f

The run unit sets DISPLAY, MAGICIAN_DISPLAY and MAGICIAN_BIZHAWK explicitly. That
is not tidiness: a user service inherits neither your session's DISPLAY nor this
project's emulator setting, and both omissions fail in ways that do not name their
cause -- "Could not open display (X-Server required)", or a run that reports
success while measuring another project's directory.
EOF
else
  cat <<EOF

systemctl --user is not reachable from this shell, so the units were linked but not
registered. Log out and back in, or run:
    systemctl --user daemon-reload
EOF
fi
