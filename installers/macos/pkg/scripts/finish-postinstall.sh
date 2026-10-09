#!/bin/bash
# postinstall of tech.blackdeep.knaif.finish — the hidden package Installer installs last.
#
# Runs the steps the model and tool choices queued (common.sh, QUEUE) in ONE Terminal window in the
# console user's session, where `models pull` and `brew` show their progress, as the Windows
# installer's console does. Installer.app shows nothing a package script prints, and pkgbuild gives
# every package script a 600 s timeout; this script only writes the window's script and opens it,
# so setup does not wait. With nobody logged in, or no window to be had, nothing runs and every
# step's command is logged for later; the conclusion page names them too. Never fatal.

# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

if [ ! -s "$QUEUE" ]; then
  rm -f "$QUEUE" "$QUEUE.retry"
  exit 0
fi
steps="$(cat "$QUEUE")"
retries="$(cat "$QUEUE.retry" 2>/dev/null)"
rm -f "$QUEUE" "$QUEUE.retry"

# log <why>, then each step's command, one per line, for whoever reads install.log.
later() {
  log "$1"
  printf '%s\n' "$retries" | while IFS= read -r retry; do
    [ -n "$retry" ] && log "  $retry"
  done
}

user="$(console_user)"
if [ -z "$user" ]; then
  later "nobody is logged in to finish setup for; run these later:"
  exit 0
fi

# The window's script: the queued steps between a header and a summary. Written as the user into a
# folder of their own under /tmp, and removed by itself at the end; the window stays open on the
# summary.
window_script() {
  cat <<'HEADER'
#!/bin/bash
# Written by the knaif installer, which cannot show a download's progress itself.
here="$(cd "$(dirname "$0")" && pwd)"
failed=()
step() { # <what> <retry> <command...>
  local what="$1" retry="$2"
  shift 2
  echo
  echo "== $what"
  "$@" || failed+=("$retry")
}
echo 'Finishing the knaif setup. This window can take a while on a slow connection.'
HEADER
  printf '%s\n' "$steps"
  cat <<'FOOTER'
echo
if [ "${#failed[@]}" -eq 0 ]; then
  echo 'Done. Open a new Terminal window and try: knaif skills list'
else
  echo 'Not everything finished. Run these again any time:'
  printf '  %s\n' "${failed[@]}"
fi
rm -rf "$here"
FOOTER
}

log "opening a Terminal window to finish setup for $user"
dir="$(as_user "$user" mktemp -d /tmp/knaif-setup.XXXXXX)" || dir=""
cmd="$dir/finish-knaif-setup.command"
if [ -n "$dir" ] &&
  window_script | as_user "$user" tee "$cmd" >/dev/null &&
  as_user "$user" chmod +x "$cmd" &&
  launchctl asuser "$(id -u "$user")" sudo -u "$user" -H open -a Terminal "$cmd"; then
  exit 0
fi
[ -n "$dir" ] && rm -rf "$dir"
later "could not open a Terminal window; knaif is installed anyway. Run these later:"
exit 0
