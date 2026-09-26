#!/usr/bin/env bash
# Build one native kind into its OWN directory: target/release-<kind>/.
#
# kind = base | cpu | vulkan | cuda, the same vocabulary installers/package.sh uses. The feature
# set is READ FROM package.sh (`--print-feats`) rather than copied, so the two cannot drift.
#
# Each kind gets the matching `release-<kind>` cargo profile, which is what stops two kinds
# overwriting one another's binary and staged llama/ggml libs in target/release/. See
# docs/plans/2026-09-21-per-backend-build-profiles.md.
#
#   scripts/build_native_kind.sh cpu
#   scripts/build_native_kind.sh cuda
#
# Windows: needs MSVC (cl.exe, INCLUDE/LIB) and cmake, which a plain shell does not have. Rather
# than requiring a "Developer PowerShell for VS", this locates Visual Studio with vswhere and
# re-runs the build through VsDevCmd.bat when cl.exe is absent. An existing developer shell is
# used as-is.
#
# Honoured if already set: LIBCLANG_PATH, CMAKE_GENERATOR, CUDAARCHS, KNAIF_CUDA_DEV_ARCHS.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

KIND="${1:-cpu}"
case "$KIND" in
  base|cpu|vulkan|cuda) ;;
  *)
    echo "usage: $0 <base|cpu|vulkan|cuda>" >&2
    exit 2
    ;;
esac

PROFILE="release-$KIND"
FEATS="$(bash installers/package.sh --print-feats="$KIND")"

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) OS=windows ;;
  Darwin)                   OS=macos ;;
  *)                        OS=linux ;;
esac

echo "building '$KIND' into target/$PROFILE/"
[ -n "$FEATS" ] && echo "  features: $FEATS" || echo "  features: (none — mock-only build)"

# PDFium beside the exe, as the packaged artifact has it, so a dev build does OCR with the library
# users get and no $KNAIF_PDFIUM_PATH. Staged BEFORE the build because every branch below ends in
# `exec cargo`; it needs nothing from the build. Every functional kind builds with `pdfium`.
if [ "$KIND" != base ]; then
  case "$OS-$(uname -m)" in
    windows-x86_64) PDFIUM_PLATFORM=win-x64 ;;
    linux-x86_64) PDFIUM_PLATFORM=linux-x64 ;;
    macos-arm64) PDFIUM_PLATFORM=mac-arm64 ;;
    *) PDFIUM_PLATFORM="" ;;
  esac
  if [ -n "$PDFIUM_PLATFORM" ]; then
    bash installers/fetch_pdfium.sh "$PDFIUM_PLATFORM" "target/$PROFILE"
  else
    echo "  (no pinned PDFium for $OS-$(uname -m); OCR needs KNAIF_PDFIUM_PATH)"
  fi
fi

# The release CUDA arch list lives in package.sh and is asserted against docs/RELEASE.md by
# test_cuda_arch_list.py. Read it rather than copy it. KNAIF_CUDA_DEV_ARCHS shortens a dev build;
# an explicit CUDAARCHS always wins.
if [ "$KIND" = cuda ] && [ -z "${CUDAARCHS:-}" ]; then
  if [ -n "${KNAIF_CUDA_DEV_ARCHS:-}" ]; then
    CUDAARCHS="$KNAIF_CUDA_DEV_ARCHS"
  else
    CUDAARCHS="$(sed -nE 's/^CUDA_RELEASE_ARCHS="(.+)"$/\1/p' installers/package.sh | head -1)"
  fi
  [ -n "$CUDAARCHS" ] || {
    echo "ERROR: could not read CUDA_RELEASE_ARCHS from installers/package.sh" >&2
    exit 1
  }
  export CUDAARCHS
  echo "  CUDAARCHS=$CUDAARCHS"
fi

# llama.cpp's Vulkan backend must use Ninja: the default Visual Studio generator breaks on its
# `vulkan-shaders-gen` ExternalProject install step.
if [ "$KIND" = vulkan ] && [ -z "${CMAKE_GENERATOR:-}" ]; then
  export CMAKE_GENERATOR=Ninja
  echo "  CMAKE_GENERATOR=Ninja"
fi

# Assemble the cargo invocation once; `base` carries no features, and an empty --features would
# be a different thing from omitting it.
cargo_args=(build --profile "$PROFILE" -p knaif-cli)
[ -n "$FEATS" ] && cargo_args+=(--features "$FEATS")

if [ "$OS" != windows ]; then
  : "${CMAKE_GENERATOR:=Ninja}"
  export CMAKE_GENERATOR
  exec cargo "${cargo_args[@]}"
fi

# ---------------------------------------------------------------- Windows

: "${LIBCLANG_PATH:=C:\\Program Files\\LLVM\\bin}"
export LIBCLANG_PATH

# Only the llama kinds compile C++ and therefore need MSVC and cmake. A `base` build is pure Rust,
# and rustc locates the MSVC linker by itself, so there is nothing to enter for.
if [ -z "$FEATS" ]; then
  exec cargo "${cargo_args[@]}"
fi

if command -v cl.exe >/dev/null 2>&1; then
  echo "  MSVC: already on PATH"
  exec cargo "${cargo_args[@]}"
fi

# No cl.exe. Find Visual Studio and run the build inside its environment instead of failing with
# whatever cmake says when it cannot find a compiler.
vswhere=""
for cand in \
  "/c/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe" \
  "/c/Program Files/Microsoft Visual Studio/Installer/vswhere.exe"; do
  [ -x "$cand" ] && { vswhere="$cand"; break; }
done

[ -n "$vswhere" ] || {
  echo "ERROR: cl.exe is not on PATH and vswhere.exe was not found, so Visual Studio cannot be" >&2
  echo "       located. Install the MSVC build tools, or run this from a" >&2
  echo "       'Developer PowerShell for VS'." >&2
  exit 1
}

vs_path="$("$vswhere" -latest -products '*' -property installationPath | tr -d '\r')"
[ -n "$vs_path" ] || {
  echo "ERROR: vswhere found no Visual Studio installation." >&2
  exit 1
}

vsdevcmd="$vs_path\\Common7\\Tools\\VsDevCmd.bat"
vsdevcmd_unix="$(cygpath -u "$vs_path")/Common7/Tools/VsDevCmd.bat"
[ -f "$vsdevcmd_unix" ] || {
  echo "ERROR: VsDevCmd.bat not found under $vs_path" >&2
  exit 1
}

vs_name="$("$vswhere" -latest -products '*' -property displayName | tr -d '\r')"
[ -n "$vs_name" ] || vs_name="$(basename "$vs_path")"
echo "  MSVC: not on PATH — entering $vs_name via VsDevCmd.bat"

# VsDevCmd only sets the environment for the process it runs in, so the build has to happen inside
# that same cmd invocation. A generated batch file keeps the quoting honest: paths here contain
# spaces, and cargo's arguments must survive both cmd and the shell that wrote them.
tmpdir="$(mktemp -d)" || exit 1
trap 'rm -rf "$tmpdir"' EXIT
bat="$tmpdir/build.bat"

{
  echo "@echo off"
  # VsDevCmd shells out to vswhere by bare name; without the Installer directory on PATH it
  # prints "'vswhere.exe' is not recognized" and carries on. Harmless, but it reads like the
  # build failed, so give it the directory.
  echo "set \"PATH=$(cygpath -w "$(dirname "$vswhere")");%PATH%\""
  echo "call \"$vsdevcmd\" -arch=amd64 -host_arch=amd64 || exit /b 1"
  echo "set \"LIBCLANG_PATH=$LIBCLANG_PATH\""
  [ -n "${CMAKE_GENERATOR:-}" ] && echo "set \"CMAKE_GENERATOR=$CMAKE_GENERATOR\""
  [ -n "${CUDAARCHS:-}" ] && echo "set \"CUDAARCHS=$CUDAARCHS\""
  echo "cd /d \"$(cygpath -w "$ROOT")\""
  printf 'cargo'
  printf ' %s' "${cargo_args[@]}"
  printf '\n'
} > "$bat"

cmd //c "$(cygpath -w "$bat")"
