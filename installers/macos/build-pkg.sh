#!/usr/bin/env bash
# Build the macOS .pkg from a staged artifact tree (F5, D13 in the 2026-08-02 macOS support plan).
#
#   installers/macos/build-pkg.sh [<stage-dir>] [--sign "Developer ID Installer: <name> (<TEAM>)"]
#                                                [--out FILE]
#
# <stage-dir> defaults to dist/staging/knaif-<ver>-macos-arm64, what `just package-native metal`
# stages. For a release its Mach-Os must already be signed (sign.sh) — packaging after signing is
# F2's order — and --sign names the Developer ID *Installer* identity. Without --sign the package
# is unsigned: fine for inspecting (`pkgutil --expand`, E6), never for publishing.
#
# One component package per choice on the options page: core, one per skill, the PATH link, one per
# supporting tool, the model download. Payload packages install under /usr/local/knaif; the rest
# carry only a postinstall script. The version is package.sh's (Cargo.toml's), never a fourth
# declaration (D7); the model is the manifest's recommendation, never typed here.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PREFIX="tech.blackdeep.knaif"
INSTALL_LOCATION="/usr/local/knaif"

# Supporting tools: choice id | display name | Homebrew name | cask (1/0) | skill.
# MIRRORS each skill.yaml's `dependencies.external_tools[].macos` and Distribution.xml.in's
# tool_* choices; python/core/tests/test_installer_pkg.py fails when they drift.
TOOLS="ffmpeg|FFmpeg|ffmpeg|0|ffmpeg
ghostscript|Ghostscript|ghostscript|0|documents
libreoffice|LibreOffice|libreoffice|1|documents
tesseract|Tesseract OCR|tesseract|0|documents"
SKILLS="ffmpeg documents"

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }

STAGE=""
SIGN=""
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --sign) SIGN="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) STAGE="$1"; shift ;;
  esac
done
VER="$(grep -A3 '\[workspace.package\]' "$ROOT/Cargo.toml" | grep -m1 '^version' | sed -E 's/.*"([^"]+)".*/\1/')"
STAGE="${STAGE:-$ROOT/dist/staging/knaif-$VER-macos-arm64}"
[ -d "$STAGE" ] || { echo "ERROR: no staged tree at $STAGE — run: just package-native metal" >&2; exit 2; }
STAGE="$(cd "$STAGE" && pwd)"
for f in bin/knaif contracts LICENSE NOTICE README.txt licenses; do
  [ -e "$STAGE/$f" ] || { echo "ERROR: $STAGE has no $f — not a staged artifact" >&2; exit 1; }
done
MODEL="$(awk '/^recommendations:/{r=1; next} r && /^[^ #]/{r=0} r && $1=="desktop:"{print $2; exit}' \
  "$ROOT/contracts/models/model-manifest.yaml")"
# The model package has no payload (its postinstall downloads the GGUF), so Installer would size it
# "Zero KB" and leave it out of "Space Required". Its real size comes from the same manifest entry.
MODEL_BYTES="$(awk -v m="  $MODEL:" 'index($0, m) == 1 { b = 1; next } b && /^  [^ ]/ { b = 0 }
  b && $1 == "size_bytes:" { print $2; exit }' "$ROOT/contracts/models/model-manifest.yaml")"
MIN_OS="${MACOSX_DEPLOYMENT_TARGET:-12.0}"
[ -n "$VER" ] && [ -n "$MODEL" ] && [ -n "$MODEL_BYTES" ] ||
  { echo "ERROR: could not read the version, the model or its size" >&2; exit 1; }
MODEL_KBYTES=$(((MODEL_BYTES + 1023) / 1024))
OUT="${OUT:-$ROOT/dist/knaif-$VER-macos-arm64.pkg}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PKGS="$WORK/pkgs"
mkdir -p "$PKGS"

# A scripts folder for pkgbuild: common.sh plus $2 installed under the name Installer runs ($3).
scripts_dir() {
  local dir="$WORK/scripts-$1"
  mkdir -p "$dir"
  cp "$HERE/pkg/scripts/common.sh" "$dir/common.sh"
  cp "$HERE/pkg/scripts/$2" "$dir/$3"
  chmod 755 "$dir/$3"
  echo "$dir"
}

# Payload modes: owner-writable, world-readable, executables stay executable. pkgbuild's
# `--ownership recommended` makes the payload root:wheel.
normalize_modes() { chmod -R u+rwX,go+rX,go-w "$1"; }

payload_pkg() { # <name> <payload root> [scripts dir]
  local args=(--root "$2" --install-location "$INSTALL_LOCATION" --identifier "$PREFIX.${1//-/.}"
    --version "$VER" --ownership recommended)
  [ -n "${3:-}" ] && args+=(--scripts "$3")
  normalize_modes "$2"
  pkgbuild "${args[@]}" "$PKGS/$1.pkg"
}

script_pkg() { # <name> <scripts dir> [PackageInfo template]
  local args=(--nopayload --scripts "$2" --identifier "$PREFIX.${1//-/.}" --version "$VER")
  [ -n "${3:-}" ] && args+=(--info "$3")
  pkgbuild "${args[@]}" "$PKGS/$1.pkg"
}

# core: everything but the skills, plus the uninstaller.
core="$WORK/root-core"
mkdir -p "$core"
cp -Rp "$STAGE/bin" "$STAGE/contracts" "$STAGE/licenses" "$core/"
cp -p "$STAGE/LICENSE" "$STAGE/NOTICE" "$STAGE/README.txt" "$core/"
cp "$HERE/pkg/uninstall.sh" "$core/uninstall.sh"
chmod 755 "$core/uninstall.sh"
payload_pkg core "$core" "$(scripts_dir core core-preinstall.sh preinstall)"

for skill in $SKILLS; do
  [ -d "$STAGE/skills/$skill" ] || { echo "ERROR: $STAGE has no skills/$skill" >&2; exit 1; }
  root="$WORK/root-skill-$skill"
  mkdir -p "$root/skills"
  cp -Rp "$STAGE/skills/$skill" "$root/skills/"
  payload_pkg "skill-$skill" "$root"
done

script_pkg path "$(scripts_dir path path-postinstall.sh postinstall)"

# The size a script-only choice shows. productbuild sizes each choice from its component's
# PackageInfo and overwrites any size the Distribution states, so it goes into the component:
# pkgbuild keeps a templated <payload installKBytes> on a --nopayload package. Prints the template.
size_info() { # <name> <kbytes>
  printf '<pkg-info><payload installKBytes="%s" numberOfFiles="0"/></pkg-info>\n' "$2" \
    > "$WORK/$1-info.xml"
  echo "$WORK/$1-info.xml"
}

# ESTIMATES of what Homebrew downloads for each tool, dependencies included, so the options page
# does not show "Zero KB". Measured once with Homebrew on Apple Silicon (2026-10-09): ffmpeg 51 MB,
# ghostscript 128 MB, tesseract 33 MB, the LibreOffice app 800 MB, rounded up. The real figure
# depends on the Homebrew version and on what is already installed; the options page says so.
tool_kbytes() {
  case "$1" in
    ffmpeg) echo $((60 * 1024)) ;;
    ghostscript) echo $((130 * 1024)) ;;
    libreoffice) echo $((800 * 1024)) ;;
    tesseract) echo $((40 * 1024)) ;;
    *) echo 0 ;;
  esac
}

while IFS='|' read -r id display brew cask skill; do
  dir="$(scripts_dir "tool-$id" tool-postinstall.sh postinstall)"
  {
    printf 'TOOL_NAME=%q\n' "$display"
    printf 'BREW_NAME=%q\n' "$brew"
    printf 'BREW_CASK=%q\n' "$cask"
    printf 'SKILL=%q\n' "$skill"
  } > "$dir/tool.env"
  script_pkg "tool-$id" "$dir" "$(size_info "tool-$id" "$(tool_kbytes "$id")")"
done <<< "$TOOLS"

dir="$(scripts_dir model model-postinstall.sh postinstall)"
printf 'MODEL=%q\n' "$MODEL" > "$dir/model.env"
script_pkg model "$dir" "$(size_info model "$MODEL_KBYTES")"

# Installed last (its line ends the Distribution's choices-outline): opens the one Terminal window
# that runs what the model and tool scripts queued.
script_pkg finish "$(scripts_dir finish finish-postinstall.sh postinstall)"

# The product: Distribution + resources, Installer-signed when an identity is given.
res="$WORK/resources"
mkdir -p "$res"
cp "$STAGE/LICENSE" "$res/LICENSE.txt"
fill() { sed -e "s|@VERSION@|$VER|g" -e "s|@MODEL@|$MODEL|g" -e "s|@MIN_OS@|$MIN_OS|g" "$1"; }
fill "$HERE/pkg/conclusion.html.in" > "$res/conclusion.html"
fill "$HERE/pkg/Distribution.xml.in" > "$WORK/Distribution.xml"

sign_args=()
[ -n "$SIGN" ] && sign_args=(--sign "$SIGN" --timestamp)
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
productbuild --distribution "$WORK/Distribution.xml" --package-path "$PKGS" --resources "$res" \
  ${sign_args[@]+"${sign_args[@]}"} "$OUT"

echo "Created $OUT (knaif $VER, model $MODEL, macOS >= $MIN_OS${SIGN:+, signed})"
