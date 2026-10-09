#!/bin/bash
# postinstall of tech.blackdeep.knaif.path — the "put knaif on my PATH" option (D13).
#
# A symlink in /usr/local/bin, which is on every macOS user's default PATH (/etc/paths). Never
# replaces a file or link that is not ours: something else put it there.

# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

target="/usr/local/knaif/bin/knaif"
link="$VOL/usr/local/bin/knaif"

mkdir -p "$VOL/usr/local/bin"
if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then
  log "PATH link already in place: /usr/local/bin/knaif"
elif [ -e "$link" ] || [ -L "$link" ]; then
  log "not replacing /usr/local/bin/knaif: it exists and is not this install's link." \
    "Run knaif as $target, or remove that file and reinstall."
else
  ln -s "$target" "$link" && log "linked /usr/local/bin/knaif -> $target"
fi
exit 0
