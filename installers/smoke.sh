#!/usr/bin/env bash
# Release smoke test for a packaged knaif artifact (plan E1).
#
# Unpacks the artifact into a temp dir OUTSIDE the checkout and exercises the handful of things
# that must work on a user's machine but cannot be proven by `cargo test`:
#   1. the binary runs at all, and reports the version the release claims
#   2. exe-relative resource resolution finds skills/ + contracts/ from an unrelated cwd
#   3. the skills the artifact ships are actually discoverable (ffmpeg + documents)
#   4. dependency detection ("doctor") runs — reporting a MISSING tool is a pass; this checks the
#      probe works, not that the machine happens to have ffmpeg
#   5. one offline mock plan produces a JSON envelope on stdout
#
# Deliberately never downloads: `--json` already forbids it (see select_model), and
# KNAIF_LLM_BACKEND=mock opts out of default-model auto-select, so a CI box that happens to have a
# GGUF in ~/.knaif/models still tests the mock path rather than loading 2.5 GB.
#
# Usage: installers/smoke.sh <artifact.zip | .tar.gz | .AppImage | staged-dir>
#   e.g. installers/smoke.sh dist/knaif-1.0.0-windows-x64.zip
#        installers/smoke.sh dist/staging/knaif-1.0.0-linux-x64
#
# Exit 0 = pass. Any failure exits non-zero with the failing check named.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ $# -eq 1 ] || { echo "usage: installers/smoke.sh <artifact.zip|.tar.gz|.AppImage|staged-dir>" >&2; exit 1; }
ARTIFACT="$1"
[ -e "$ARTIFACT" ] || { echo "ERROR: no such artifact: $ARTIFACT" >&2; exit 1; }
ARTIFACT="$(cd "$(dirname "$ARTIFACT")" && pwd)/$(basename "$ARTIFACT")"

# The version the release claims, from the same source package.sh names artifacts after
# (A4 keeps this in step with the wheel + installer).
VER="$(grep -A3 '\[workspace.package\]' "$ROOT/Cargo.toml" | grep -m1 '^version' | sed -E 's/.*"([^"]+)".*/\1/')"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

case "$ARTIFACT" in
  *.zip)
    if command -v unzip >/dev/null 2>&1; then
      unzip -q "$ARTIFACT" -d "$WORK"
    else
      powershell.exe -NoProfile -Command \
        "Expand-Archive -Path '$(cygpath -w "$ARTIFACT" 2>/dev/null || echo "$ARTIFACT")' -DestinationPath '$(cygpath -w "$WORK" 2>/dev/null || echo "$WORK")' -Force"
    fi
    ;;
  *.tar.gz|*.tgz) tar xzf "$ARTIFACT" -C "$WORK" ;;
  *.AppImage)
    # `--appimage-extract` is handled by the AppImage runtime itself and needs NO FUSE, so this
    # works inside a container and on a box with no libfuse — which is exactly where a release
    # artifact gets verified. It extracts to ./squashfs-root relative to the CWD, hence the
    # subshell cd. The layout mirrors the tarball one level down (usr/bin/knaif, usr/LICENSE),
    # and the BIN search plus the ART=dirname(BIN)/.. below both handle that unchanged.
    #
    # Until this existed the AppImage was the ONE artifact no check could open, and it is
    # precisely the one that shipped without NOTICE for weeks after package.sh was fixed.
    chmod +x "$ARTIFACT" 2>/dev/null || true
    ( cd "$WORK" && "$ARTIFACT" --appimage-extract >/dev/null ) || {
      echo "ERROR: --appimage-extract failed on $ARTIFACT" >&2
      exit 1
    }
    ;;
  *)
    [ -d "$ARTIFACT" ] || { echo "ERROR: not an archive or directory: $ARTIFACT" >&2; exit 1; }
    cp -r "$ARTIFACT" "$WORK/"
    ;;
esac

# Locate bin/knaif[.exe] wherever the archive put its top-level dir.
BIN="$(find "$WORK" -type f \( -name knaif -o -name knaif.exe \) -path '*/bin/*' | head -1)"
[ -n "$BIN" ] || { echo "FAIL: no bin/knaif[.exe] inside $ARTIFACT" >&2; exit 1; }
chmod +x "$BIN" 2>/dev/null || true

# Run from a temp cwd with no skills/ above it, and an empty (= unset) KNAIF_SKILLS_ROOT, so
# resource resolution MUST be exe-relative — the property that makes the artifact relocatable.
CWD="$(mktemp -d)"
trap 'rm -rf "$WORK" "$CWD"' EXIT
run() { (cd "$CWD" && KNAIF_SKILLS_ROOT= "$BIN" "$@"); }

fail() { echo "FAIL: $1" >&2; exit 1; }

echo "smoke: $(basename "$ARTIFACT")"

# 1. runs, and reports the version this release claims
out="$(run --version 2>&1)" || fail "--version exited non-zero: $out"
case "$out" in
  *"$VER"*) echo "  ok  --version -> $out" ;;
  *) fail "--version reported '$out', expected version $VER" ;;
esac

# 2 + 3. exe-relative resolution + the shipped skills are discoverable
out="$(run skills list 2>&1)" || fail "skills list exited non-zero: $out"
for skill in ffmpeg documents; do
  case "$out" in
    *"$skill"*) ;;
    *) fail "skills list is missing '$skill' (exe-relative skills/ resolution broken?): $out" ;;
  esac
done
echo "  ok  skills list -> ffmpeg + documents found from an unrelated cwd"

# 4. dependency detection runs. A missing tool is a legitimate result on a clean box — this asserts
# the probe works and names the skills, not that the machine has ffmpeg installed.
out="$(run skills deps 2>&1)" || fail "skills deps exited non-zero: $out"
case "$out" in
  *ffmpeg*) echo "  ok  skills deps -> dependency detection runs" ;;
  *) fail "skills deps never mentioned ffmpeg: $out" ;;
esac

# 5. one offline mock plan -> a JSON envelope on stdout. Mock is forced so this never loads a real
# model or downloads one, even on a box with a GGUF already installed.
out="$( (cd "$CWD" && KNAIF_SKILLS_ROOT= KNAIF_LLM_BACKEND=mock "$BIN" plan --skill ffmpeg --json "compress clip.mp4" 2>/dev/null) )" \
  || fail "plan --json exited non-zero"
case "$out" in
  '{'*'"plan"'*) echo "  ok  plan --json -> JSON envelope on stdout" ;;
  *) fail "plan --json did not emit a JSON plan envelope, got: $out" ;;
esac

# 6. legal attribution actually ships. Apache-2.0 §4(a) requires LICENSE and §4(d) requires NOTICE
# to travel with every redistribution; NOTICE also carries the Qwen3 derivation attribution for the
# models knaif downloads. NOTICE was absent from every artifact on every OS until 2026-07-26 (F11)
# precisely because nothing executed it — a file that must ship but that no test reads is the kind
# that stops shipping unnoticed. This is that test.
# NB: $ROOT is the repo, not the artifact — checking there would pass unconditionally and assert
# nothing. The artifact root is the parent of the bin/ dir $BIN was found in.
ART="$(cd "$(dirname "$BIN")/.." && pwd)"
for f in LICENSE NOTICE; do
  [ -f "$ART/$f" ] || fail "$f is missing from the artifact (Apache-2.0 requires it to ship)"
done
echo "  ok  LICENSE + NOTICE present"

# 7. no undeclared runtime dependency (Windows). Everything above RUNS the exe, so on the build box
# it proves only that the build box can run it — and the build box has Visual Studio. That blind
# spot is how every 1.0.x Windows binary came to import VCRUNTIME140/MSVCP140/VCOMP140 without any
# of them being staged: on a clean Windows 11 24H2 image all three are absent and the process dies
# at startup with STATUS_DLL_NOT_FOUND (0xC0000135), printing nothing at all. Verified in Windows
# Sandbox, 2026-07-27.
#
# Reading the import table is machine-independent, so unlike every check above it fails HERE, on
# the machine that introduced the problem, instead of in a user's hands.
if [ "$(uname -s)" != Linux ] && [ -f "$ART/bin/knaif.exe" ]; then
  if command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then
    py="$(command -v python || command -v python3)"
    "$py" "$ROOT/scripts/check_pe_imports.py" "$ART/bin" \
      || fail "artifact has undeclared runtime dependencies (see above)"
  else
    echo "  --  skipped import check (no python on PATH)"
  fi
fi

echo "PASS: $(basename "$ARTIFACT") (v$VER)"
