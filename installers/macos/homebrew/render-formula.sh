#!/usr/bin/env bash
# Render the Homebrew formula for a published macOS .zip (G5, D19).
#
#   installers/macos/homebrew/render-formula.sh dist/knaif-<ver>-macos-arm64.zip [> knaif.rb]
#
# Run it on the FINAL notarized .zip — the file whose SHA256SUMS row is published — and commit the
# output to the tap as Formula/knaif.rb. The version comes from the file name, the checksum from
# its bytes and the model from the manifest; nothing is typed by hand.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"

ZIP="${1:-}"
[ -f "$ZIP" ] || { echo "usage: $0 <knaif-<ver>-macos-arm64.zip>" >&2; exit 2; }

base="$(basename "$ZIP")"
case "$base" in
  knaif-?*-macos-arm64.zip) ;;
  *)
    echo "ERROR: $base is not named knaif-<ver>-macos-arm64.zip" >&2
    exit 2
    ;;
esac
VER="${base#knaif-}"
VER="${VER%-macos-arm64.zip}"

if command -v shasum >/dev/null 2>&1; then
  SHA="$(shasum -a 256 "$ZIP" | cut -d' ' -f1)"
else
  SHA="$(sha256sum "$ZIP" | cut -d' ' -f1)"
fi
MODEL="$(awk '/^recommendations:/{r=1; next} r && /^[^ #]/{r=0} r && $1=="desktop:"{print $2; exit}' \
  "$ROOT/contracts/models/model-manifest.yaml")"
[ -n "$MODEL" ] || { echo "ERROR: no recommendations.desktop in the model manifest" >&2; exit 1; }

sed -e "s|@VERSION@|$VER|g" -e "s|@SHA256@|$SHA|g" -e "s|@MODEL@|$MODEL|g" "$HERE/knaif.rb.in"
