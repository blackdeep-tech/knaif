#!/bin/bash
# Remove knaif installed by the macOS .pkg:   sudo /usr/local/knaif/uninstall.sh [--purge]
#
# macOS packages have no uninstall action of their own (F5), so this script ships inside the
# install (D13). It removes:
#   - /usr/local/knaif (the program, its skills and contracts),
#   - the /usr/local/bin/knaif link, only if it points at this install,
#   - the package receipts (pkgutil --forget), so a reinstall is a fresh install.
# With --purge it also removes the invoking user's ~/.knaif (downloaded models and backends).
# Tools installed with Homebrew belong to Homebrew and are left alone: `brew uninstall <name>`.
#
# KNAIF_PKG_VOLUME is for the tests only: it moves every path under a scratch directory and skips
# the root check. A real uninstall leaves it unset.

set -euo pipefail

VOL="${KNAIF_PKG_VOLUME:-}"
ROOT="$VOL/usr/local/knaif"
LINK="$VOL/usr/local/bin/knaif"
PKG_PREFIX="tech.blackdeep.knaif."

purge=0
for arg in "$@"; do
  case "$arg" in
    --purge) purge=1 ;;
    -h | --help)
      sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown option: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

if [ -z "$VOL" ] && [ "$(id -u)" != 0 ]; then
  echo "Run with sudo: sudo $0 $*" >&2
  exit 1
fi

# Stop the model daemon (`knaif daemon start`, `knaif run --daemon`) before its program goes, or it
# keeps running the removed build until its idle timeout. It belongs to the user who ran sudo (else
# the console user): only they hold its token, in their ~/.knaif. Best effort, as in the .pkg.
knaif="$ROOT/bin/knaif"
owner="${SUDO_USER:-}"
case "$owner" in "" | root) owner="$(stat -f%Su /dev/console 2>/dev/null || true)" ;; esac
case "$owner" in
  "" | root | loginwindow | _mbsetupuser) ;;
  *)
    if [ -x "$knaif" ]; then
      sudo -u "$owner" -H "$knaif" daemon stop ||
        echo "could not stop knaif's model daemon for $owner; it exits on its own when idle"
    fi
    ;;
esac

if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "/usr/local/knaif/bin/knaif" ]; then
  rm -f "$LINK"
  echo "removed /usr/local/bin/knaif"
fi

if [ -d "$ROOT" ]; then
  rm -rf "$ROOT"
  echo "removed /usr/local/knaif"
fi

for pkg in $(pkgutil --pkgs 2>/dev/null | grep "^${PKG_PREFIX//./\\.}" || true); do
  pkgutil --forget "$pkg" >/dev/null && echo "forgot receipt $pkg"
done

user="${SUDO_USER:-}"
if [ "$purge" = 1 ]; then
  case "$user" in
    "" | root) echo "--purge: run through sudo from your own account to remove your ~/.knaif" >&2 ;;
    *[!A-Za-z0-9._-]*) echo "--purge: unexpected user name '$user'; ~/.knaif left in place" >&2 ;;
    *)
      home="$VOL$(eval echo "~$user")"
      if [ -d "$home/.knaif" ]; then
        rm -rf "${home:?}/.knaif"
        echo "removed $home/.knaif (models and backends)"
      fi
      ;;
  esac
else
  echo "kept ~/.knaif (downloaded models): remove it with --purge, or by hand"
fi
echo "knaif is uninstalled. Homebrew tools stay installed: brew uninstall <name> to remove one."
