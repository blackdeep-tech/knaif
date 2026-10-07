#!/usr/bin/env bash
# Authenticode-sign every unsigned PE (.exe/.dll) directly inside a staged directory, then re-read
# every signature. Windows only; called by installers/package.sh on each tree it stages.
#
# Usage: installers/sign_stage.sh <dir>
#
# The signer is whatever $KNAIF_SIGN_CMD names — this script knows no provider. It is run once,
# with every file to sign appended as a NATIVE Windows path, and may contain quoted words
# (it is evaluated by bash). The maintainer's command is in docs/RELEASE.md:
#   powershell -NoProfile -ExecutionPolicy Bypass -File "<repo>\scripts\sign_windows.ps1"
#
# Unset, the tree stays unsigned and this says so: a local build must keep working without an
# Azure login. Set, the result is all-or-nothing — any binary still lacking a valid embedded
# signature afterwards fails the build, because a release that silently skipped signing looks
# exactly like one that did not.
#
# "Unsigned" means "no valid EMBEDDED (Authenticode) signature". Files that already carry one —
# Microsoft's VC++ runtime, NVIDIA's CUDA redistributables — are left alone: re-signing them would
# replace their vendor's signature with ours. Catalog-signed status does not count, because a
# catalog stays on the machine it was installed on and does not travel with the file.
set -euo pipefail

dir="${1:?usage: sign_stage.sh <dir>}"
[ -d "$dir" ] || { echo "ERROR: $dir is not a directory." >&2; exit 1; }
todo=() left=()  # filled by read_unsigned, by name

# Native paths of every .exe/.dll in $1 without a valid embedded signature, one per line. Returns
# non-zero when the check itself did not run to completion.
#
# FAIL CLOSED. An empty answer means "nothing unsigned" only if the check demonstrably finished, so
# it must exit 0 AND print its STATUS-OK marker. The first live run of this script hit the open
# failure: Get-AuthenticodeSignature's module would not load, the error went to stderr, no paths
# came back, and the tree was reported as already signed.
#
# PSModulePath is cleared for the child because it is inherited down `pwsh -> just -> Windows
# PowerShell -> bash`, and a module path assembled by another PowerShell edition is what broke that
# run. Unset, Windows PowerShell builds its own default.
unsigned_pes() {
  local win out
  win="$(cygpath -w "$1")"
  out="$(env -u PSModulePath powershell.exe -NoProfile -NonInteractive -Command "
    \$ErrorActionPreference = 'Stop'
    Import-Module Microsoft.PowerShell.Security
    Get-ChildItem -LiteralPath '$win' -File |
      Where-Object { \$_.Extension -in '.exe', '.dll' } |
      Get-AuthenticodeSignature |
      Where-Object { \$_.Status -ne 'Valid' -or \$_.SignatureType -ne 'Authenticode' } |
      ForEach-Object { 'UNSIGNED ' + \$_.Path }
    'STATUS-OK'" | tr -d '\r')" || return 1
  printf '%s\n' "$out" | grep -qx 'STATUS-OK' || return 1
  printf '%s\n' "$out" | sed -n 's/^UNSIGNED //p'
}

# unsigned_pes into the array named $1, or abort the build.
read_unsigned() {
  local list
  list="$(unsigned_pes "$dir")" || {
    echo "ERROR: could not read the Authenticode status of the binaries in $dir." >&2
    echo "       Refusing to guess — an unreadable status is not a signed one." >&2
    exit 1
  }
  mapfile -t "$1" < <(printf '%s' "$list" | sed '/^$/d')
}

read_unsigned todo

if [ -z "${KNAIF_SIGN_CMD:-}" ]; then
  echo "  NOTE: KNAIF_SIGN_CMD unset — ${#todo[@]} binary(ies) in $(basename "$dir")/ left UNSIGNED"
  echo "        (fine for a local build, never for a release; see docs/RELEASE.md)."
  exit 0
fi

if [ "${#todo[@]}" -eq 0 ]; then
  echo "  signing: every binary in $(basename "$dir")/ already carries a valid signature"
  exit 0
fi

echo "  signing ${#todo[@]} binary(ies) in $(basename "$dir")/ via \$KNAIF_SIGN_CMD…"
bash -c "$KNAIF_SIGN_CMD \"\$@\"" knaif-sign "${todo[@]}" || {
  echo "ERROR: the signing command failed (KNAIF_SIGN_CMD=$KNAIF_SIGN_CMD)." >&2
  echo "       If it reports 401/403, run 'az login' and check the signer role (docs/RELEASE.md)." >&2
  exit 1
}

# Trust the signatures, not the signer's exit code.
read_unsigned left
if [ "${#left[@]}" -gt 0 ]; then
  echo "ERROR: signing reported success, but these still lack a valid signature:" >&2
  printf '         %s\n' "${left[@]}" >&2
  exit 1
fi
echo "  ✓ signed and verified ${#todo[@]} binary(ies) in $(basename "$dir")/"
