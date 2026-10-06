#!/usr/bin/env bash
# Sign, package and notarize the macOS release from a staged tree, in F2's order, so it cannot be
# got wrong by hand (F2 in the 2026-08-02 macOS support plan).
#
#   just package-native metal          # first: build + stage + check_macho_deps + the .zip
#   installers/macos/release.sh [--stage DIR]
#
#   stage → sign every Mach-O → verify every Mach-O
#     ├─ .zip (rebuilt from the signed tree) → notarize → read the log      (cannot be stapled)
#     └─ .pkg (Installer-signed)             → notarize → read the log → staple → validate
#   → verify the way Gatekeeper does (F7)
#
# Environment: sign.sh's (KNAIF_SIGN_IDENTITY, KNAIF_TEAM_ID, optional KNAIF_SIGN_KEYCHAIN),
# notarize.sh's (KNAIF_NOTARY_PROFILE, or the API-key trio), and
#   KNAIF_INSTALLER_IDENTITY   "Developer ID Installer: <name> (<TEAM>)"                 required
#   KNAIF_DIST_DIR             where dist/ is (default: the checkout's)                 optional
#
# NOT here, on purpose: the clean-room runs (E3, E4, E6) on these final files, and SHA256SUMS —
# generated once, by hand, over the complete release set right before publishing (RELEASE.md).
# Stapling rewrites the .pkg, so any checksum taken before this script ends is already wrong.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"
DIST="${KNAIF_DIST_DIR:-$ROOT/dist}"
mkdir -p "$DIST" && DIST="$(cd "$DIST" && pwd)" # absolute: the zip step runs from elsewhere

VER="$(grep -A3 '\[workspace.package\]' Cargo.toml | grep -m1 '^version' | sed -E 's/.*"([^"]+)".*/\1/')"
NAME="knaif-$VER-macos-arm64"
STAGE="$DIST/staging/$NAME"
while [ $# -gt 0 ]; do
  case "$1" in
    --stage) STAGE="$2"; shift 2 ;;
    -h | --help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -d "$STAGE/bin" ] || { echo "ERROR: no staged tree at $STAGE — run: just package-native metal" >&2; exit 1; }
NAME="$(basename "$STAGE")"
: "${KNAIF_INSTALLER_IDENTITY:?set KNAIF_INSTALLER_IDENTITY to the Developer ID Installer identity}"

# The same guard package.sh runs, again here: package.sh stops only after staging, so a refused
# tree can still be sitting in dist/staging. Nothing that names the builder is signed or sent to
# Apple (AGENTS.md, Public Output Hygiene; 2026-10-06, a build under ~ was).
echo "== 0/5 the staged tree carries no builder home directory"
PY=""
for cand in python python3; do
  command -v "$cand" >/dev/null 2>&1 && { PY="$cand"; break; }
done
[ -n "$PY" ] || { echo "ERROR: python not found — cannot check $STAGE for local paths." >&2; exit 1; }
"$PY" "$ROOT/scripts/check_no_local_paths.py" "$STAGE" || {
  echo "ERROR: $STAGE carries the builder's home directory; not signing it." >&2
  exit 1
}

echo "== 1/5 sign and verify every Mach-O"
bash "$HERE/sign.sh" "$STAGE"
CDHASHES="$DIST/notary/$NAME.cdhashes.json"

echo "== 2/5 rebuild the .zip from the signed tree"
# The same command package.sh uses (see there for why `zip -y`, not `ditto`): package.sh's .zip
# holds the UNSIGNED binaries and must never be the one published.
ZIP="$DIST/$NAME.zip"
rm -f "$ZIP"
( cd "$(dirname "$STAGE")" && zip -qry "$ZIP" "$NAME" )

echo "== 3/5 notarize the .zip"
bash "$HERE/notarize.sh" "$ZIP" "$CDHASHES"

echo "== 4/5 build, sign and notarize the .pkg"
PKG="$DIST/$NAME.pkg"
bash "$HERE/build-pkg.sh" "$STAGE" --sign "$KNAIF_INSTALLER_IDENTITY" --out "$PKG"
bash "$HERE/notarize.sh" "$PKG" "$CDHASHES"

echo "== 5/5 verify the way Gatekeeper does (F7)"
# A bare CLI binary is checked with codesign's notarization requirement; spctl's exec check is the
# app-bundle one. The decisive test is still the quarantined launch in the clean room (E4).
codesign -R="notarized" --check-notarization -vv "$STAGE/bin/knaif"
spctl -a -vvv -t install "$PKG"

cat <<EOF

Done: $ZIP and $PKG are signed and notarized; the .pkg is stapled.
Next, by hand (RELEASE.md):
  1. clean room on these exact files — E3 (macOS 12 VM), E4 (quarantined launch), E6 (.pkg)
  2. SHA256SUMS over the complete release set, after everything else, right before publishing
  3. the Homebrew formula: installers/macos/homebrew/render-formula.sh $ZIP
EOF
