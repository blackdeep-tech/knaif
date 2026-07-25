#!/usr/bin/env bash
# Assemble a knaif AppImage from a staged full artifact (installers/package.sh output).
#
# The AppDir mirrors the tarball's exe-relative layout so resource resolution is unchanged:
#   AppDir/usr/bin/knaif        the CLI + its core llama/ggml libs + loadable ggml-* backends
#   AppDir/usr/skills/...       runtime skill data (resolve_skills_root finds it at exe_dir/../skills)
#   AppDir/usr/contracts/...       core_tools + model manifest
# The opt-in CUDA payload is NOT bundled — it loads from ~/.knaif/backends outside the read-only mount.
#
# Usage: installers/linux/build-appimage.sh <staged-dir | tarball>
#   e.g. installers/linux/build-appimage.sh dist/staging/knaif-1.0.0-linux-x64
# Feed it the DEFAULT artifact (the `vulkan` kind = CPU+Vulkan, which carries the plain name).
# Env:  APPIMAGETOOL=/path/to/appimagetool (else `appimagetool` on PATH). ARCH override optional.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

[ $# -eq 1 ] || { echo "usage: build-appimage.sh <staged-dir|tarball>" >&2; exit 1; }
SRC="$1"

APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"
command -v "$APPIMAGETOOL" >/dev/null 2>&1 || [ -x "$APPIMAGETOOL" ] || {
  echo "ERROR: appimagetool not found (set APPIMAGETOOL=/path/to/appimagetool-x86_64.AppImage)." >&2
  exit 1
}

VER="$(grep -A3 '\[workspace.package\]' Cargo.toml | grep -m1 '^version' | sed -E 's/.*"([^"]+)".*/\1/')"
ARCH="${ARCH:-x86_64}"

# Unpack a tarball if given one; a staged dir is used in place.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
case "$SRC" in
  *.tar.gz|*.tgz) tar xzf "$SRC" -C "$WORK"; STAGED="$(find "$WORK" -maxdepth 1 -type d -name 'knaif-*' | head -1)" ;;
  *) [ -d "$SRC" ] || { echo "ERROR: not a dir or tarball: $SRC" >&2; exit 1; }; STAGED="$SRC" ;;
esac
[ -f "$STAGED/bin/knaif" ] || { echo "ERROR: $STAGED has no bin/knaif — not a full artifact." >&2; exit 1; }

APPDIR="$WORK/knaif.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"

# Mirror the artifact under usr/: exe + libs in usr/bin, skills/contracts beside it (one level up).
cp -a "$STAGED/bin/." "$APPDIR/usr/bin/"
for d in skills contracts licenses; do
  [ -d "$STAGED/$d" ] && cp -a "$STAGED/$d" "$APPDIR/usr/"
done
[ -f "$STAGED/LICENSE" ]    && cp "$STAGED/LICENSE" "$APPDIR/usr/"
[ -f "$STAGED/README.txt" ] && cp "$STAGED/README.txt" "$APPDIR/usr/"

# AppRun: resolve our own mount dir and exec the CLI. Libs load via the exe's $ORIGIN RPATH.
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/knaif" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# Desktop entry + icon (appimagetool requires both). CLI app → Terminal=true, no menu category noise.
cat > "$APPDIR/knaif.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=knaif
Comment=Natural-language action-plan CLI
Exec=knaif
Icon=knaif
Categories=Utility;
Terminal=true
EOF
# Minimal valid 1x1 PNG icon (appimagetool only needs the file to exist and match Icon=).
base64 -d > "$APPDIR/knaif.png" <<'EOF'
iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMEAYET4dfLAAAAAElFTkSuQmCC
EOF

mkdir -p dist
OUT="dist/knaif-$VER-linux-$ARCH.AppImage"
rm -f "$OUT"
# --appimage-extract-and-run avoids needing FUSE for appimagetool itself; ARCH must be set for it.
ARCH="$ARCH" "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "$OUT" 2>&1 | tail -3 || \
  ARCH="$ARCH" "$APPIMAGETOOL" "$APPDIR" "$OUT"

[ -f "$OUT" ] || { echo "ERROR: appimagetool did not produce $OUT" >&2; exit 1; }
chmod +x "$OUT"
sha256sum "$OUT" | tee "$OUT.sha256"
echo "Created $OUT ($(du -h "$OUT" | cut -f1))"
