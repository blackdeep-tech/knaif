#!/usr/bin/env bash
# Fetch the pinned PDFium build, verify it, and stage it beside a knaif binary.
#
#   installers/fetch_pdfium.sh <platform> <bin-dir> [<licenses-dir>]
#
# <platform> is a pin key (win-x64 | linux-x64 | mac-arm64) or a package.sh "$OS-$ARCH" pair
# (windows-x64, linux-x64, macos-arm64). The library lands in <bin-dir>, where pdfium-render looks
# first after $KNAIF_PDFIUM_PATH; with <licenses-dir>, the packaging LICENSE and the 15 component
# notices land in <licenses-dir>/PDFium/, which is what the artifact must carry to redistribute it.
#
# Pinned by version and sha256 in contracts/release/pdfium.yaml. A downloaded archive that does not
# match the pin is refused, not staged: a wrong PDFium is a wrong OCR engine under a trusted name.
#
# Env: KNAIF_PDFIUM_PIN (pin file), KNAIF_PDFIUM_CACHE (download cache, default target/pdfium),
#      KNAIF_PDFIUM_OFFLINE=1 (never download; the archive must already be in the cache).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PLATFORM="${1:?usage: fetch_pdfium.sh <platform> <bin-dir> [<licenses-dir>]}"
BIN_DIR="${2:?usage: fetch_pdfium.sh <platform> <bin-dir> [<licenses-dir>]}"
LIC_DIR="${3:-}"
PIN="${KNAIF_PDFIUM_PIN:-$ROOT/contracts/release/pdfium.yaml}"
CACHE_ROOT="${KNAIF_PDFIUM_CACHE:-$ROOT/target/pdfium}"

case "$PLATFORM" in
  windows-x64) PLATFORM=win-x64 ;;
  macos-arm64) PLATFORM=mac-arm64 ;;
esac

# `<key>: "<archive> <lib> <sha256>"` — one line, so grep is enough.
field() { grep -E "^$1:" "$PIN" | head -1 | sed -E 's/^[^:]+:[[:space:]]*"?([^"]*)"?[[:space:]]*$/\1/'; }
VERSION="$(field version)"
LINE="$(field "$PLATFORM")"
[ -n "$VERSION" ] && [ -n "$LINE" ] || {
  echo "ERROR: $PIN pins no PDFium for '$PLATFORM'." >&2
  exit 1
}
read -r ARCHIVE LIB SHA <<<"$LINE"

CACHE="$CACHE_ROOT/${VERSION//\//-}"
mkdir -p "$CACHE"
if [ ! -f "$CACHE/$ARCHIVE" ]; then
  if [ "${KNAIF_PDFIUM_OFFLINE:-0}" = 1 ]; then
    echo "ERROR: $CACHE/$ARCHIVE is not cached and KNAIF_PDFIUM_OFFLINE=1." >&2
    exit 1
  fi
  url="https://github.com/bblanchon/pdfium-binaries/releases/download/$VERSION/$ARCHIVE"
  echo "  fetching PDFium $VERSION ($ARCHIVE)"
  curl -fsSL --retry 3 -o "$CACHE/$ARCHIVE.part" "$url"
  mv "$CACHE/$ARCHIVE.part" "$CACHE/$ARCHIVE"
fi

if command -v sha256sum >/dev/null 2>&1; then
  got="$(sha256sum "$CACHE/$ARCHIVE" | cut -d' ' -f1)"
else
  got="$(shasum -a 256 "$CACHE/$ARCHIVE" | cut -d' ' -f1)"
fi
if [ "$got" != "$SHA" ]; then
  echo "ERROR: $ARCHIVE sha256 $got does not match the pin $SHA ($PIN)." >&2
  echo "       Delete $CACHE/$ARCHIVE and retry; if it persists, the upstream asset changed." >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# From inside the cache: GNU tar reads `C:/...` as host:path (a remote archive) on Windows, and
# `--force-local` is GNU-only, so a relative archive name is the portable answer.
(cd "$CACHE" && tar -xzf "$ARCHIVE" -C "$work")
[ -f "$work/$LIB" ] || {
  echo "ERROR: $ARCHIVE has no $LIB." >&2
  exit 1
}
mkdir -p "$BIN_DIR"
cp "$work/$LIB" "$BIN_DIR/"
echo "  staged PDFium $VERSION: $BIN_DIR/$(basename "$LIB")"

if [ -n "$LIC_DIR" ]; then
  mkdir -p "$LIC_DIR/PDFium"
  cp "$work/LICENSE" "$LIC_DIR/PDFium/LICENSE"
  if [ -d "$work/licenses" ]; then
    cp -R "$work/licenses" "$LIC_DIR/PDFium/"
  fi
  echo "  staged PDFium notices: $LIC_DIR/PDFium/"
fi
