#!/usr/bin/env bash
# Sign every Mach-O in a staged tree, inside-out, and verify each one (F3 in the 2026-08-02 macOS
# support plan).
#
#   installers/macos/sign.sh <stage-dir>
#
# Environment:
#   KNAIF_SIGN_IDENTITY      "Developer ID Application: <name> (<TEAM>)"                 required
#   KNAIF_TEAM_ID            the 10-character Team ID every signature must carry          required
#   KNAIF_SIGN_KEYCHAIN      keychain holding the identity (CI's temporary one)           optional
#   KNAIF_SIGN_ENTITLEMENTS  entitlements plist — F4 starts with NONE; add one only for a
#                            reproduced failure, and record it in the plan               optional
#   KNAIF_DIST_DIR           where dist/ is (default: the checkout's)                     optional
#
# The file list is `check_macho_deps.py --list`, the portability audit's own enumeration, so the
# two cannot disagree about what is in the artifact. No `codesign --deep`: Apple discourages it
# for signing, and it reads poorly over a flat bin/ of loose Mach-Os. Writes each binary's CDHash
# to <dist>/notary/<stage name>.cdhashes.json for notarize.sh to find in the notarization log.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DIST="${KNAIF_DIST_DIR:-$ROOT/dist}"

STAGE="${1:-}"
[ -n "$STAGE" ] && [ -d "$STAGE/bin" ] || {
  echo "usage: $0 <stage-dir>   (a staged tree with a bin/ folder)" >&2
  exit 2
}
: "${KNAIF_SIGN_IDENTITY:?set KNAIF_SIGN_IDENTITY to the Developer ID Application identity}"
: "${KNAIF_TEAM_ID:?set KNAIF_TEAM_ID to the 10-character Team ID}"

py=""
for cand in python3 python; do
  command -v "$cand" >/dev/null 2>&1 && { py="$cand"; break; }
done
[ -n "$py" ] || { echo "ERROR: python3 is needed to list the Mach-O files" >&2; exit 1; }

args=(--force --options runtime --timestamp --sign "$KNAIF_SIGN_IDENTITY")
[ -n "${KNAIF_SIGN_KEYCHAIN:-}" ] && args+=(--keychain "$KNAIF_SIGN_KEYCHAIN")
[ -n "${KNAIF_SIGN_ENTITLEMENTS:-}" ] && args+=(--entitlements "$KNAIF_SIGN_ENTITLEMENTS")

files="$("$py" "$ROOT/scripts/check_macho_deps.py" "$STAGE/bin" --list)"
count=0
while IFS= read -r file; do
  codesign "${args[@]}" "$file"
  count=$((count + 1))
done <<< "$files"
echo "  signed $count Mach-O file(s), libraries first, executable last"

mkdir -p "$DIST/notary"
cdhashes="$DIST/notary/$(basename "$STAGE").cdhashes.json"
"$py" "$ROOT/scripts/check_macos_signing.py" codesign "$STAGE/bin" --team "$KNAIF_TEAM_ID" \
  --cdhashes-out "$cdhashes"
echo "  CDHashes: $cdhashes"
