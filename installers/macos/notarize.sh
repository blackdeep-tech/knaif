#!/usr/bin/env bash
# Notarize one release file, read its log even on success, and staple a .pkg (F3b, F6).
#
#   installers/macos/notarize.sh <file.zip|file.pkg> <cdhashes.json>
#
# Credentials, one of:
#   KNAIF_NOTARY_PROFILE                         a `xcrun notarytool store-credentials` profile
#                                                (the contributor's first build, by hand)
#   KNAIF_NOTARY_KEY + _KEY_ID + _ISSUER         an App Store Connect API key: the .p8 path, its
#                                                Key ID and Issuer ID (CI)
#
# A submission Apple accepts can still carry warnings that become failures on a later OS, so the
# log is checked, not just the status: Accepted, no issues, and every Mach-O's CDHash in the
# ticket. Only a .pkg can be stapled; a .zip's first quarantined run looks the ticket up online,
# which is why the .pkg is the recommended download (D6). Logs land in <dist>/notary/, where
# <dist> is $KNAIF_DIST_DIR or the checkout's dist/.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

FILE="${1:-}"
CDHASHES="${2:-}"
[ -f "$FILE" ] && [ -f "$CDHASHES" ] || {
  echo "usage: $0 <file.zip|file.pkg> <cdhashes.json>" >&2
  exit 2
}

if [ -n "${KNAIF_NOTARY_PROFILE:-}" ]; then
  creds=(--keychain-profile "$KNAIF_NOTARY_PROFILE")
elif [ -n "${KNAIF_NOTARY_KEY:-}" ]; then
  : "${KNAIF_NOTARY_KEY_ID:?set KNAIF_NOTARY_KEY_ID with KNAIF_NOTARY_KEY}"
  : "${KNAIF_NOTARY_ISSUER:?set KNAIF_NOTARY_ISSUER with KNAIF_NOTARY_KEY}"
  creds=(--key "$KNAIF_NOTARY_KEY" --key-id "$KNAIF_NOTARY_KEY_ID" --issuer "$KNAIF_NOTARY_ISSUER")
else
  echo "ERROR: set KNAIF_NOTARY_PROFILE, or KNAIF_NOTARY_KEY + KNAIF_NOTARY_KEY_ID + KNAIF_NOTARY_ISSUER" >&2
  exit 2
fi

py=""
for cand in python3 python; do
  command -v "$cand" >/dev/null 2>&1 && { py="$cand"; break; }
done
[ -n "$py" ] || { echo "ERROR: python3 is needed to read the notarization log" >&2; exit 1; }

logs="${KNAIF_DIST_DIR:-$ROOT/dist}/notary"
mkdir -p "$logs"
name="$(basename "$FILE")"

echo "Notarizing $name (this waits for Apple, usually a few minutes)..."
xcrun notarytool submit "$FILE" "${creds[@]}" --wait --output-format json > "$logs/$name.submit.json"
id="$("$py" -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$logs/$name.submit.json")"
xcrun notarytool log "$id" "${creds[@]}" "$logs/$name.log.json"
"$py" "$ROOT/scripts/check_macos_signing.py" notary-log "$logs/$name.log.json" --cdhashes "$CDHASHES"

case "$FILE" in
  *.pkg)
    xcrun stapler staple "$FILE"
    xcrun stapler validate "$FILE"
    ;;
esac
echo "  ok  $name notarized (submission $id)"
