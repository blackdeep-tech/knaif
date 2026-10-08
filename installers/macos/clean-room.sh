#!/bin/bash
# The macOS clean room, run INSIDE the VM (E3, E4 and E6's install half of the 2026-08-02 macOS
# support plan; D8, D15, D16). Host steps — creating the macOS 12 VM with tart and copying files
# in — are in installers/macos/README.md.
#
#   bash clean-room.sh --zip knaif-<ver>-macos-arm64.zip --fixtures DIR [--model GGUF]
#   bash clean-room.sh --pkg knaif-<ver>-macos-arm64.pkg --fixtures DIR [--model GGUF]
#                      [--upgrade-from OLD.pkg] [--offline]
#
# DIR holds sample.pdf (from `just eval-fixtures documents`). --model is the GGUF to run; without
# it the recommended model is pulled from Hugging Face (so the network must be up). --offline
# asserts the network is really down — the condition that proves a stapled .pkg needs no online
# ticket lookup (D6, E4). Results: one PASS/FAIL/INFO line per check, in ./clean-room-results.txt.
#
# RULES — the room passes iff every PASS/FAIL line is PASS and none is missing:
#   room_floor         the VM runs macOS 12 (the deployment floor, D9/D15)
#   room_no_clt        no Command Line Tools / Xcode (`xcode-select -p` fails)
#   room_no_brew       no Homebrew
#   room_offline       (--offline only) no route to Apple's notary service
#   quarantined        the downloaded file carries com.apple.quarantine, as a browser leaves it
#   zip: quarantine_propagated  every extracted file carries it too (what Finder does, E4)
#   pkg: pkg_gatekeeper spctl accepts the quarantined .pkg for install
#   pkg: pkg_install    `installer` succeeds; pkg_receipt its receipt names this version (D7);
#        pkg_path_link  /usr/local/bin/knaif is the install's link, and every later check runs
#                       knaif THROUGH it (the symlinked-executable case)
#   launch             `knaif --version` runs quarantined: no Gatekeeper block, no dylib failure
#   smoke              installers/smoke.sh passes on the installed tree
#   cpu_run            one real documents request on the CPU — the tree copied with its Metal
#                      backend removed (D2's procedure, D16) — exits 0 and writes a PDF
#   pkg --upgrade-from: upgrade_receipt  the newer receipt wins over an installed older .pkg
#   pkg: uninstall     uninstall.sh removes the install, the link and the receipts
# INFO (recorded, never gating — D16): what Metal does inside the VM.

set -uo pipefail

ZIP="" PKG="" FIXTURES="" MODEL="" OLD_PKG="" OFFLINE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --zip) ZIP="$2"; shift 2 ;;
    --pkg) PKG="$2"; shift 2 ;;
    --fixtures) FIXTURES="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --upgrade-from) OLD_PKG="$2"; shift 2 ;;
    --offline) OFFLINE=1; shift ;;
    -h | --help) sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$ZIP$PKG" ] || { [ -n "$ZIP" ] && [ -n "$PKG" ]; }; then
  echo "give exactly one of --zip / --pkg" >&2
  exit 2
fi
[ -f "$FIXTURES/sample.pdf" ] || { echo "--fixtures DIR must hold sample.pdf" >&2; exit 2; }
# The runs below work in a scratch folder, and knaif reads a --model that is not a file from there as
# a model name: give a file path absolutely.
[ -f "$MODEL" ] && MODEL="$(cd "$(dirname "$MODEL")" && pwd)/$(basename "$MODEL")"
HERE="$(cd "$(dirname "$0")" && pwd)"
SMOKE="$HERE/smoke.sh"
[ -f "$SMOKE" ] || { echo "copy installers/smoke.sh next to this script" >&2; exit 2; }

SCRATCH="${TMPDIR:-/tmp}"
SCRATCH="${SCRATCH%/}"
RESULTS="$PWD/clean-room-results.txt"
: > "$RESULTS"
expected=0
check() { # <name> <0 = pass> [detail]
  expected=$((expected + 1))
  echo "$([ "$2" = 0 ] && echo PASS || echo FAIL) $1${3:+: $3}" | tee -a "$RESULTS"
}
info() { echo "INFO $1${2:+: $2}" | tee -a "$RESULTS"; }

# A browser download's quarantine attribute: flags;timestamp;agent;id.
quarantine() { xattr -w com.apple.quarantine "0083;$(printf %x "$(date +%s)");Safari;$(uuidgen)" "$1"; }

# -- the room itself ----------------------------------------------------------------------------
ver="$(sw_vers -productVersion)"
case "$ver" in 12.*) check room_floor 0 "macOS $ver" ;; *) check room_floor 1 "macOS $ver, not 12" ;; esac
if xcode-select -p >/dev/null 2>&1; then check room_no_clt 1 "$(xcode-select -p)"; else check room_no_clt 0; fi
# Homebrew's own install locations, not PATH: a non-login shell may not have them on PATH. The
# override exists for the tests, which cannot fake an absolute path on a Mac that has Homebrew.
brew_found=""
for d in ${KNAIF_ROOM_BREW_DIRS:-/opt/homebrew/bin /usr/local/bin}; do
  [ -x "$d/brew" ] && brew_found="$d/brew"
done
if [ -n "$brew_found" ]; then
  check room_no_brew 1 "Homebrew is installed ($brew_found)"
else
  check room_no_brew 0
fi
if [ "$OFFLINE" = 1 ]; then
  if curl -s --max-time 5 -o /dev/null https://api.apple-cloudkit.com; then
    check room_offline 1 "the network is up"
  else
    check room_offline 0
  fi
fi

# -- get the artifact the way a user does -------------------------------------------------------
ARTIFACT="${ZIP:-$PKG}"
quarantine "$ARTIFACT"
xattr -p com.apple.quarantine "$ARTIFACT" >/dev/null 2>&1
check quarantined $?

if [ -n "$ZIP" ]; then
  # ditto extracts the way Finder does, carrying the archive's quarantine onto every file.
  dest="$HOME/Applications/knaif-cleanroom"
  rm -rf "$dest" && mkdir -p "$dest"
  ditto -x -k "$ZIP" "$dest"
  TREE="$(find "$dest" -mindepth 1 -maxdepth 1 -type d | head -1)"
  missing=0
  while IFS= read -r f; do
    xattr -p com.apple.quarantine "$f" >/dev/null 2>&1 || missing=$((missing + 1))
  done < <(find "$TREE" -type f)
  check quarantine_propagated "$([ "$missing" = 0 ] && echo 0 || echo 1)" "$missing file(s) without it"
  K="$TREE/bin/knaif"
else
  spctl -a -vvv -t install "$PKG" > spctl.log 2>&1
  check pkg_gatekeeper $? "$(tail -1 spctl.log)"
  # shellcheck disable=SC2024  # the logs belong to the user running the room, not to root
  if [ -n "$OLD_PKG" ]; then
    quarantine "$OLD_PKG"
    sudo installer -pkg "$OLD_PKG" -target / > install-old.log 2>&1 || true
  fi
  # shellcheck disable=SC2024
  sudo installer -pkg "$PKG" -target / > install.log 2>&1
  check pkg_install $? "see install.log"
  want="$(basename "$PKG" | sed -E 's/^knaif-(.*)-macos-arm64\.pkg$/\1/')"
  got="$(pkgutil --pkg-info tech.blackdeep.knaif.core 2>/dev/null | awk '/^version:/{print $2}')"
  check pkg_receipt "$([ "$got" = "$want" ] && echo 0 || echo 1)" "receipt $got, file $want"
  [ -n "$OLD_PKG" ] && check upgrade_receipt "$([ "$got" = "$want" ] && echo 0 || echo 1)" "after $OLD_PKG"
  link_ok=1
  [ "$(readlink /usr/local/bin/knaif)" = /usr/local/knaif/bin/knaif ] && link_ok=0
  check pkg_path_link "$link_ok"
  TREE=/usr/local/knaif
  K=/usr/local/bin/knaif
fi

# -- run it -------------------------------------------------------------------------------------
out="$(cd "$SCRATCH" && "$K" --version 2>&1)"
case "$out" in "knaif "*) check launch 0 "$out" ;; *) check launch 1 "$out" ;; esac

bash "$SMOKE" "$TREE" > smoke.log 2>&1
check smoke $? "see smoke.log"

# Without --model, `run --yes` pulls the recommended model itself, from its pinned URL.
model_arg=()
[ -n "$MODEL" ] && model_arg=(--model "$MODEL")

# D16: the gate is a CPU run. Remove Metal from a copy of the tree (D2's procedure) — never set
# KNAIF_N_GPU_LAYERS=0, which PERFORMANCE.md §4 measured as not CPU-only.
cpu="$SCRATCH/knaif-cpu"
rm -rf "$cpu" && cp -R "$TREE" "$cpu" && rm -f "$cpu"/bin/libggml-metal.*
work="$SCRATCH/knaif-cleanroom-work"
rm -rf "$work" && mkdir -p "$work" && cp "$FIXTURES/sample.pdf" "$work/"
(cd "$work" && "$cpu/bin/knaif" run documents --yes --verbose ${model_arg[@]+"${model_arg[@]}"} \
  "rotate sample.pdf 90 degrees") > cpu_run.log 2>&1
rc=$?
pdf="$(find "$work" -maxdepth 1 -type f ! -name sample.pdf -exec basename {} \; | head -1)"
if [ "$rc" = 0 ] && [ -n "$pdf" ] && [ "$(head -c 4 "$work/$pdf")" = "%PDF" ]; then
  check cpu_run 0 "$pdf"
else
  check cpu_run 1 "exit $rc, output ${pdf:-none}; see cpu_run.log"
fi
grep -qi "library not loaded\|image not found" cpu_run.log && check cpu_run_dylibs 1 "see cpu_run.log"

# INFO only (D16): Metal inside a VM is Apple's paravirtualized GPU, not evidence about a Mac.
rm -rf "$work" && mkdir -p "$work" && cp "$FIXTURES/sample.pdf" "$work/"
(cd "$work" && "$K" run documents --yes --verbose ${model_arg[@]+"${model_arg[@]}"} \
  "rotate sample.pdf 90 degrees") > metal_vm.log 2>&1
info metal_in_vm "exit $?; $(grep -m1 -o 'offloaded [0-9]*/[0-9]* layers' metal_vm.log || echo 'no offload line')"

if [ -n "$PKG" ]; then
  # shellcheck disable=SC2024
  sudo /usr/local/knaif/uninstall.sh > uninstall.log 2>&1
  left=""
  [ -e /usr/local/knaif ] && left="$left install-dir"
  [ -L /usr/local/bin/knaif ] && left="$left link"
  [ "$(pkgutil --pkgs | grep -c '^tech\.blackdeep\.knaif\.')" = 0 ] || left="$left receipts"
  check uninstall "$([ -z "$left" ] && echo 0 || echo 1)" "${left:-clean}"
fi

pass=$(grep -c '^PASS' "$RESULTS")
fail=$(grep -c '^FAIL' "$RESULTS")
echo
if [ "$fail" = 0 ] && [ "$pass" = "$expected" ]; then
  echo "CLEAN ROOM PASS ($pass checks) — $RESULTS"
  exit 0
fi
echo "CLEAN ROOM FAIL ($pass passed, $fail failed, $expected expected) — $RESULTS"
exit 1
