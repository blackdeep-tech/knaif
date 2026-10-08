#!/bin/bash
# preinstall of tech.blackdeep.knaif.core — installed as `preinstall` by build-pkg.sh.
#
# Clear the previous install's program folders before the new payload lands. A package only ever
# adds or replaces files, so without this an upgrade keeps every file the new version dropped, and
# a skill deselected on upgrade stays installed. The same folders knaif.iss's [InstallDelete]
# clears on Windows; user data lives in ~/.knaif and is never touched.

# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

# Stop the previous install's model daemon (`knaif daemon start`, `knaif run --daemon`) first, as
# knaif.iss's StopDaemon does: it outlives every run and would keep serving the old build until its
# idle timeout. It belongs to the console user (its token is in their ~/.knaif), so it is asked as
# them, by the old binary. A release without the command (1.2.x exits 2), a daemon that will not
# stop, or nobody logged in is logged and never fails the install (D13).
knaif="$KNAIF_ROOT/bin/knaif"
user="$(console_user)"
if [ -x "$knaif" ] && [ -n "$user" ]; then
  as_user "$user" "$knaif" daemon stop ||
    log "could not stop knaif's model daemon for $user; it exits on its own when idle"
fi

for dir in bin skills contracts licenses; do
  rm -rf "${KNAIF_ROOT:?}/$dir"
done
exit 0
