#!/bin/bash
# Shared by the .pkg's install scripts — sourced, never run on its own.
#
# macOS Installer runs every preinstall/postinstall as root with $1 = the package path, $2 = the
# install location and $3 = the target volume. Sourced without arguments, this file sees the
# caller's.
#
# NON-FATAL BY CONSTRUCTION (D13). A failed Homebrew or model download is reported to
# /var/log/install.log (script stdout lands there) and never fails the install: every script ends
# in `exit 0` and none uses `set -e`. The same rule as the Windows installer's [Run] entries.

# The target volume without its trailing slash ("" for the boot volume), so every absolute path is
# "$VOL/usr/local/knaif". Correct for another volume, and what lets the tests run these scripts
# against a scratch directory.
VOL="${3%/}"
# shellcheck disable=SC2034  # used by the scripts that source this file
KNAIF_ROOT="$VOL/usr/local/knaif"

log() { echo "knaif: $*"; }

# The user logged in at the console — the one the options page was shown to. Homebrew refuses to
# run as root, and the model belongs in that user's ~/.knaif, so both run as them. Prints nothing
# when nobody is (a headless `installer -pkg` at the login window).
console_user() {
  local user
  user="$(stat -f%Su /dev/console 2>/dev/null)" || return 0
  case "$user" in
    "" | root | loginwindow | _mbsetupuser) return 0 ;;
  esac
  echo "$user"
}

# Run a command as user $1, with their HOME.
as_user() {
  local user="$1"
  shift
  sudo -u "$user" -H "$@"
}

# STEPS FOR THE TERMINAL WINDOW. The model download and the Homebrew installs never run in setup
# itself: Installer.app shows nothing a package script prints, and pkgbuild gives every package
# script a 600 s timeout, so on a slow line they ran for minutes with no sign of life and could be
# killed (2026-10-09). Their scripts queue the step here instead, and the hidden finish package,
# installed last, runs the queue in one Terminal window in the user's session
# (finish-postinstall.sh). Root-owned, under the install folder, so no other user can add a step.
QUEUE="$KNAIF_ROOT/.setup-steps"

# queue_step <what> <retry> <command...>: the window shows <what>, runs the command as the user,
# and names <retry> if it fails. Each step is one `step ...` line, every argument quoted.
queue_step() {
  local what="$1" retry="$2"
  shift 2
  {
    printf 'step'
    printf ' %q' "$what" "$retry" "$@"
    printf '\n'
  } >> "$QUEUE"
  printf '%s\n' "$retry" >> "$QUEUE.retry"
}

# Homebrew's own location: /opt/homebrew on Apple Silicon, /usr/local for a Rosetta install.
find_brew() {
  local brew
  for brew in "$VOL/opt/homebrew/bin/brew" "$VOL/usr/local/bin/brew"; do
    if [ -x "$brew" ]; then
      echo "$brew"
      return 0
    fi
  done
  return 1
}
