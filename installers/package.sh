#!/usr/bin/env bash
# Stage + archive a portable, self-contained knaif native artifact into dist/.
#
# Layout produced (top-level dir = knaif-<version>-<os>-<arch>[-<kind>]/):
#   bin/knaif[.exe]                     the native CLI
#   bin/*.so / *.dll                    (functional kinds) core llama/ggml libs + loadable
#                                       ggml-* backends, staged beside the exe (see below)
#   skills/<name>/                      RUNTIME DATA ONLY (skill.yaml, tools.yaml, prompt.yaml,
#                                       vocab.yaml, profiles/) — never python/, eval/, notebooks/, data/
#   contracts/runtime/core_tools.yaml      core control-tool registry
#   contracts/models/model-manifest.yaml   model catalog (first-run `models pull` reads it)
#   LICENSE, README.txt
#
# The binary resolves these relative to itself (see resolve_skills_root / resolve_repo_file), so the
# unpacked folder runs from anywhere. Archive = .zip on Windows, .tar.gz on Unix.
#
# --- Loadable backends (Option 3 / C5) ---
# Functional kinds build llama.cpp with `dynamic-backends` (GGML_BACKEND_DL=ON + GGML_CPU_ALL_VARIANTS):
# each backend (cpu variants / vulkan / cuda) is a separate `ggml-*` shared lib the exe `dlopen`s at
# runtime, and llama/ggml themselves are dynamically linked. The exe is therefore NOT self-contained —
# its core libs (llama, ggml, ggml-base, llama-common) must ship beside it. On Linux this needs an
# `$ORIGIN` RPATH (added here with patchelf) so a relocated binary finds them. The default download
# ships CPU (+ Vulkan) backends beside the exe; the CUDA backend ships as a separate opt-in payload.
#
# Usage: installers/package.sh [--no-build] [--kind=base|cpu|vulkan|cuda]
#   base   (default) no inference backend — layout/plumbing only. Builds with no extra toolchain.
#   cpu    llama.cpp CPU inference (loadable ggml-cpu-* backends, runtime CPU dispatch).
#   vulkan llama.cpp + Vulkan GPU (cross-vendor) + CPU. THE DEFAULT FUNCTIONAL ARTIFACT.
#   cuda   llama.cpp + CUDA GPU as an OPT-IN PAYLOAD: only ggml-cuda plus NVIDIA's redistributable
#          cudart/cublas/cublasLt libs (users never install the CUDA SDK; they supply the driver).
#          The payload is dropped into ~/.knaif/backends on an existing install — not a standalone app.
#
# On Linux the functional kinds are built by this script directly (gcc + cmake + ninja). On Windows
# they must be COMPILED FIRST in a "Developer PowerShell for VS" (MSVC + cmake; Vulkan also needs
# Ninja), then packaged with --no-build, e.g.:
#   (Dev Shell) cargo build --release -p knaif-cli --features llama,dynamic-backends,cuda
#   installers/package.sh --no-build --kind=cuda
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NO_BUILD=0
KIND=base
for a in "$@"; do
  case "$a" in
    --no-build)       NO_BUILD=1 ;;
    --kind=*)         KIND="${a#--kind=}" ;;
    base|cpu|vulkan|cuda) KIND="$a" ;;
    *) echo "usage: package.sh [--no-build] [--kind=base|cpu|vulkan|cuda]" >&2; exit 1 ;;
  esac
done

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) OS=windows; EXE=knaif.exe; LIB=dll; ARCHIVE=zip ;;
  Linux)  OS=linux;  EXE=knaif; LIB=so; ARCHIVE=tgz ;;
  Darwin) OS=macos;  EXE=knaif; LIB=dylib; ARCHIVE=tgz ;;
  *) echo "unsupported OS: $(uname -s)" >&2; exit 1 ;;
esac
ARCH="$(uname -m)"
[ "$ARCH" = "x86_64" ] && ARCH=x64
[ "$ARCH" = "aarch64" ] && ARCH=arm64

VER="$(grep -A3 '\[workspace.package\]' Cargo.toml | grep -m1 '^version' | sed -E 's/.*"([^"]+)".*/\1/')"

# Cargo features for a functional kind. Functional kinds use `dynamic-backends` so the produced
# backends are loadable libs (Option 3), not static-linked — that is what lets CUDA be opt-in.
feats_for_kind() {
  case "$1" in
    cpu)    echo "llama,dynamic-backends" ;;
    vulkan) echo "llama,dynamic-backends,vulkan" ;;
    cuda)   echo "llama,dynamic-backends,cuda" ;;
  esac
}

if [ "$NO_BUILD" -eq 0 ]; then
  if [ "$KIND" = base ]; then
    echo "Building release binary (base build — no llama/GPU features)…"
    cargo build --release -p knaif-cli
  elif [ "$OS" = linux ]; then
    feats="$(feats_for_kind "$KIND")"
    echo "Building '$KIND' release binary (--features $feats)…"
    # CMAKE_GENERATOR=Ninja is required for the Vulkan shader-gen step; harmless for cpu/cuda.
    CMAKE_GENERATOR="${CMAKE_GENERATOR:-Ninja}" cargo build --release -p knaif-cli --features "$feats"
  else
    echo "ERROR: a '$KIND' build needs the MSVC/C++ toolchain — compile it in a VS Developer shell:" >&2
    echo "  cargo build --release -p knaif-cli --features $(feats_for_kind "$KIND")" >&2
    echo "then re-run:  installers/package.sh --no-build --kind=$KIND" >&2
    exit 1
  fi
fi
BIN="target/release/$EXE"
[ -f "$BIN" ] || { echo "ERROR: $BIN not found — build first." >&2; exit 1; }

# Guard the one thing $BIN cannot tell us. Cargo overwrites target/release/knaif on every build, so
# $BIN is simply "whatever was linked LAST" — it carries no record of which --kind it belongs to.
# Package a functional kind right after a `base` build and you would stage a base exe (no llama at
# all) beside real backends. E1/smoke.sh CANNOT catch that: --version, skills list, skills deps and a
# mock `plan` all pass without llama, so the artifact only fails at a real `run`, in a user's hands.
#
# A functional exe is dynamically linked against llama (dynamic-backends implies dynamic-link), so
# "does it import llama?" separates a functional build from a base one. This does NOT distinguish
# cpu from vulkan — under Option 3 the exe is backend-agnostic and dlopens whatever is staged beside
# it, so that mix is harmless. Still: package each kind immediately after its own build.
if [ "$KIND" != base ]; then
  # Linux: patchelf is already a hard dep here. Windows: grep straight at the binary — Git Bash ships
  # no binutils (no strings/objdump/nm), and the PE import directory stores the DLL name as plain
  # ASCII, so the literal is present iff the exe imports it.
  case "$OS" in
    linux) exe_imports_llama() { patchelf --print-needed "$1" 2>/dev/null | grep -q '^libllama\.so'; } ;;
    *)     exe_imports_llama() { grep -qa 'llama\.dll' "$1"; } ;;
  esac
  exe_imports_llama "$BIN" || {
    echo "ERROR: $BIN does not link llama — it is a 'base' build, but --kind=$KIND needs a" >&2
    echo "       functional one. Cargo overwrote it with the last build; rebuild for this kind:" >&2
    echo "         cargo build --release -p knaif-cli --features $(feats_for_kind "$KIND")" >&2
    exit 1
  }
fi

# Locate the llama-cpp-sys-2 $OUT_DIR that belongs to $KIND (core libs in lib/ or bin/, loadable
# ggml-* backends in backends/). Newest-by-mtime is NOT good enough:
# one dir exists per feature-set hash and they all coexist, cargo does NOT re-run a cached build
# script (so an out dir's mtime does not track the last `cargo build` for that feature set), and the
# dirs are indistinguishable by name. Newest-wins silently staged ggml-vulkan into a CPU artifact.
# Identify a build by the backends it actually emitted — the one property that separates the kinds.
out_dir() {
  local kind="${1:-$KIND}" d be
  for d in $(ls -dt target/release/build/llama-cpp-sys-2-*/out 2>/dev/null); do
    be="$d/backends"
    [ -d "$be" ] || continue
    case "$kind" in
      cpu)
        ls "$be"/*ggml-vulkan.* >/dev/null 2>&1 && continue
        ls "$be"/*ggml-cuda.*   >/dev/null 2>&1 && continue
        echo "$d"; return 0 ;;
      vulkan) ls "$be"/*ggml-vulkan.* >/dev/null 2>&1 && { echo "$d"; return 0; } ;;
      cuda)   ls "$be"/*ggml-cuda.*   >/dev/null 2>&1 && { echo "$d"; return 0; } ;;
    esac
  done
  return 1
}

# Set an $ORIGIN RPATH on an ELF file so it finds sibling libs wherever the artifact is unpacked.
# No-op on non-Linux (Windows searches the module dir; macOS uses @loader_path, handled elsewhere).
set_origin_rpath() {
  [ "$OS" = linux ] || return 0
  command -v patchelf >/dev/null 2>&1 || { echo "ERROR: patchelf not found (needed for \$ORIGIN RPATH on Linux)." >&2; exit 1; }
  patchelf --set-rpath '$ORIGIN' "$1"
}

# ---------------------------------------------------------------------------------------------------
# CUDA opt-in payload (Linux): only ggml-cuda + NVIDIA redist, for ~/.knaif/backends on an existing
# install. Not a standalone app, so it gets its own layout and skips the skills/contracts/smoke steps.
# ---------------------------------------------------------------------------------------------------
if [ "$KIND" = cuda ] && [ "$OS" = linux ]; then
  OUT="$(out_dir)"
  [ -n "$OUT" ] && [ -f "$OUT/backends/libggml-cuda.$LIB" ] || {
    echo "ERROR: libggml-cuda.$LIB not found under target/release/build/.../out/backends —" >&2
    echo "       build with --features $(feats_for_kind cuda) first." >&2
    exit 1
  }
  NAME="knaif-$VER-$OS-$ARCH-cuda-backend"
  STAGE="dist/staging/$NAME"
  rm -rf "$STAGE"
  mkdir -p "$STAGE/licenses"

  cp "$OUT/backends/libggml-cuda.$LIB" "$STAGE/"
  set_origin_rpath "$STAGE/libggml-cuda.$LIB"

  # NVIDIA redistributable runtime libs, resolved from beside libggml-cuda via the $ORIGIN RPATH.
  # -L dereferences the SONAME symlink so the payload carries the real versioned file named .so.13.
  : "${CUDA_PATH:?CUDA_PATH not set (need it to locate the redist .so.13 libs)}"
  cudalib="$CUDA_PATH/targets/${ARCH/x64/x86_64}-linux/lib"
  [ -d "$cudalib" ] || cudalib="$CUDA_PATH/lib64"
  copied=0
  for soname in libcudart.so.13 libcublas.so.13 libcublasLt.so.13; do
    if [ -e "$cudalib/$soname" ]; then
      cp -L "$cudalib/$soname" "$STAGE/$soname"; copied=$((copied + 1))
    fi
  done
  [ "$copied" -ge 3 ] || { echo "ERROR: expected 3 CUDA redist libs in $cudalib, found $copied" >&2; exit 1; }
  echo "  bundled $copied CUDA redist .so.13 lib(s) from $cudalib"

  cp installers/licenses/llama.cpp-LICENSE.txt "$STAGE/licenses/"
  # Hard failure, not a warning: redistributing NVIDIA's cudart/cublas/cublasLt is EULA-permitted only
  # WITH the licence text. A warning scrolls past in a build log and ships anyway.
  [ -f installers/licenses/NVIDIA-CUDA-EULA.txt ] || {
    echo "ERROR: installers/licenses/NVIDIA-CUDA-EULA.txt is missing — REQUIRED to redistribute the" >&2
    echo "       bundled CUDA libs. Copy it from the toolkit: \$CUDA_PATH/EULA.txt" >&2
    exit 1
  }
  cp installers/licenses/NVIDIA-CUDA-EULA.txt "$STAGE/licenses/"

  cat > "$STAGE/README.txt" <<EOF
knaif $VER — CUDA backend opt-in payload ($OS-$ARCH)

This is NOT a standalone app. It contains the loadable CUDA backend (libggml-cuda.so) and NVIDIA's
redistributable CUDA runtime libraries. Drop these files into ~/.knaif/backends (or wherever
\$KNAIF_BACKENDS_DIR points) on an existing knaif install; the next run auto-detects and uses the
GPU. Requires an NVIDIA driver (R580+ for CUDA 13). Remove the files to fall back to CPU/Vulkan.

The libggml-cuda in this payload is ABI-coupled to the knaif $VER binary — use the payload built for
your exact knaif version.
EOF

  mkdir -p dist
  OUT_ART="dist/$NAME.tar.gz"
  tar czf "$OUT_ART" -C dist/staging "$NAME"
  echo "Created $OUT_ART ($(du -h "$OUT_ART" | cut -f1)) [kind=cuda payload]"
  # Deliberately no LICENSE/NOTICE here, unlike the full artifacts below: this payload ships no
  # knaif Apache-2.0 code — only llama.cpp's CUDA backend and NVIDIA's redistributables, whose
  # notices are staged into licenses/ above. Apache-2.0 §4(d) attaches to redistributing the Work,
  # which this is not. Revisit if the payload ever carries knaif-authored binaries.
  exit 0
fi

# ---------------------------------------------------------------------------------------------------
# Full artifact (base / cpu / vulkan, and Windows cuda): a runnable, self-contained knaif tree.
# ---------------------------------------------------------------------------------------------------
# The DEFAULT RELEASE ARTIFACT gets the plain name; every other kind carries a suffix.
#
# That default is `vulkan`, which under Option 3 means CPU **and** Vulkan: the Vulkan backend is one
# extra loadable lib beside the same CPU backends, and it simply loses device selection on a box with
# no usable GPU. So it is a strict superset of `cpu` and runs everywhere `cpu` does.
#
# `cpu` is therefore a BUILD kind (for a box with no Vulkan SDK), NOT a release artifact — C5b ships
# "one default artifact per OS ... never N per-backend artifacts". It keeps a `-cpu` suffix so a local
# build cannot silently overwrite the real one. Before Option 3 the backends were statically linked,
# so cpu/vulkan were genuinely different binaries and both had to ship; that is no longer true.
if [ "$KIND" = vulkan ]; then SUFFIX=""; else SUFFIX="-$KIND"; fi
NAME="knaif-$VER-$OS-$ARCH$SUFFIX"
STAGE="dist/staging/$NAME"
rm -rf "$STAGE"
mkdir -p "$STAGE/bin" "$STAGE/contracts/runtime" "$STAGE/contracts/models"

cp "$BIN" "$STAGE/bin/"

# Functional kinds ship dynamically: stage the core llama/ggml libs + the loadable ggml-* backends
# beside the exe. Without this the exe does not start at all: `dynamic-backends` implies
# `dynamic-link`, so llama/ggml are hard imports (Windows: STATUS_DLL_NOT_FOUND, 0xC0000135).
#
# The two OSes disagree on where cmake leaves the core RUNTIME libs and how they are named:
#   Linux   $OUT/lib/libggml.so.13 (+ SONAME symlinks) — $OUT/lib IS the runtime location
#   Windows $OUT/bin/ggml.dll (no `lib` prefix, unversioned) — $OUT/lib holds .lib IMPORT libs,
#           which are link-time only and must never ship
# so the core-lib step branches while the backend step only differs by the prefix.
#
# Windows `cuda` is deliberately NOT handled here: it keeps the historical static-with-redist shape
# (see the block below), so it has no core libs or loadable backends to stage. Linux `cuda` never
# reaches this point — it is an opt-in payload and exits above. Aligning Windows cuda onto Option 3
# is post-v1 (C6), and v1 publishes no CUDA artifact at all.
if { [ "$OS" = linux ] && [ "$KIND" != base ]; } ||
   { [ "$OS" = windows ] && { [ "$KIND" = cpu ] || [ "$KIND" = vulkan ]; }; }; then
  OUT="$(out_dir)"
  if [ "$OS" = linux ]; then CORE_DIR="$OUT/lib"; PFX=lib; else CORE_DIR="$OUT/bin"; PFX=; fi
  [ -n "$OUT" ] && [ -d "$CORE_DIR" ] && [ -d "$OUT/backends" ] || {
    echo "ERROR: llama-cpp-sys build output ($CORE_DIR + backends/) not found — build first." >&2
    exit 1
  }
  if [ "$OS" = linux ]; then
    # Core libs: copy the SONAME symlink + its real versioned target (cp -a preserves the symlink).
    for stem in libggml-base libggml libllama libllama-common; do
      for f in "$OUT/lib/$stem.$LIB".*; do
        [ -e "$f" ] && cp -a "$f" "$STAGE/bin/"
      done
    done
  else
    # Core libs: plain unversioned DLLs, no symlinks to preserve.
    for stem in ggml-base ggml llama llama-common; do
      f="$CORE_DIR/$stem.$LIB"
      [ -e "$f" ] && cp "$f" "$STAGE/bin/"
    done
  fi
  # Loadable backends: everything ggml-* EXCEPT ggml-cuda (that is the opt-in payload, never in the
  # default artifact) and the ggml-base core lib (already staged above).
  for f in "$OUT/backends/${PFX}ggml-"*."$LIB"; do
    [ -e "$f" ] || continue
    base_name="$(basename "$f")"
    case "${base_name#"$PFX"}" in
      ggml-cuda.*|ggml-base.*) continue ;;
    esac
    cp "$f" "$STAGE/bin/"
  done
  # $ORIGIN RPATH on the exe and every real (non-symlink) lib so siblings resolve after relocation.
  # No-op on Windows, which always searches the loading module's own directory first.
  set_origin_rpath "$STAGE/bin/$EXE"
  for f in "$STAGE/bin/"*."$LIB" "$STAGE/bin/"*."$LIB".*; do
    [ -f "$f" ] && [ ! -L "$f" ] && set_origin_rpath "$f"
  done
  n_be="$(find "$STAGE/bin" -name "${PFX}ggml-*.$LIB" ! -name "${PFX}ggml-base.*" | wc -l)"
  rpath_note=""
  [ "$OS" = linux ] && rpath_note=" (\$ORIGIN RPATH)"
  echo "  staged core libs + $n_be loadable backend(s) beside the exe$rpath_note"

  # The GNU OpenMP runtime is NOT part of a base Linux system — it ships with GCC's runtime
  # libraries (libgomp1), which a minimal install need not have. libggml-base and every
  # ggml-cpu-* variant link it, so without it the artifact fails to load its CPU backends on a
  # machine that merely lacks a compiler toolchain.
  #
  # This is the exact counterpart of VCOMP140.dll on Windows: same library, same set of binaries,
  # missed for the same reason — it resolves on the build box because the build box has GCC.
  # Found by scripts/check_elf_deps.py, not by running the artifact, because running it here
  # could never fail.
  #
  # LICENCE: libgomp is GPLv3 **with the GCC Runtime Library Exception**, which exists precisely
  # to allow shipping it alongside independent programs. No copyleft obligation propagates to
  # knaif, and no additional notice file is required.
  if [ "$OS" = linux ]; then
    gomp="$(gcc -print-file-name=libgomp.so.1 2>/dev/null || true)"
    [ -n "$gomp" ] && [ -e "$gomp" ] || {
      echo "ERROR: libgomp.so.1 not found via gcc -print-file-name." >&2
      echo "       Every ggml-cpu-* backend links it; the artifact would fail to load them on" >&2
      echo "       any machine without GCC's runtime libraries installed." >&2
      exit 1
    }
    # -L dereferences the symlink so the artifact carries the real file under its SONAME.
    cp -L "$gomp" "$STAGE/bin/libgomp.so.1"
    set_origin_rpath "$STAGE/bin/libgomp.so.1"
    echo "  staged libgomp.so.1 (OpenMP runtime, not guaranteed present on a base system)"
  fi

  # The Visual C++ runtime is NOT part of Windows and must ship beside the exe like every other
  # lib above. Without it the artifact dies at process start with STATUS_DLL_NOT_FOUND
  # (0xC0000135) printing nothing — confirmed in Windows Sandbox on a clean 11 24H2 image, where
  # VCRUNTIME140/MSVCP140/VCOMP140 are all absent. (`ucrtbase.dll` IS present: the Universal CRT
  # has been an OS component since Windows 10, which is why the `api-ms-win-crt-*` imports need
  # nothing staged.)
  #
  # Nothing catches this by running the artifact HERE — the build box has Visual Studio, so the
  # dependency always resolves. `scripts/check_pe_imports.py` reads the import table instead and
  # is machine-independent. It runs at the end of this block as a REQUIRED packaging step, so
  # every Windows artifact is verified at the moment it is assembled.
  #
  # VCOMP140 is llama.cpp's OpenMP runtime and lives in a DIFFERENT redist folder from the CRT.
  # Omitting it does not merely break startup — it breaks every ggml-cpu-* variant, i.e. inference.
  if [ "$OS" = windows ]; then
    # PREFER THE ACTIVE DEVELOPER ENVIRONMENT. Windows functional kinds are built in a "Developer
    # PowerShell for VS", which exports VCToolsRedistDir pointing at the redist tree for the very
    # toolset that compiled the binaries. That is Microsoft's documented way to locate these files,
    # and it is the only method that guarantees the runtime MATCHES the compiler rather than merely
    # being present somewhere on the disk.
    #
    # Scanning the filesystem is the fallback, and it is a poor one: a box with several Visual
    # Studios installed offers several answers, and shell globs sort LEXICALLY, not by version — so
    # "2022" sorts after "18", and a hypothetical "14.9" sorts after "14.51". Picking the wrong tree
    # can stage a runtime OLDER than the compiler, which is exactly the unsupported direction.
    vcredist_dir=""
    if [ -n "${VCToolsRedistDir:-}" ]; then
      # MSVC exports a Windows path; convert if cygpath is available (Git Bash ships it).
      if command -v cygpath >/dev/null 2>&1; then
        vcredist_dir="$(cygpath -u "$VCToolsRedistDir" 2>/dev/null || echo "$VCToolsRedistDir")"
      else
        vcredist_dir="$VCToolsRedistDir"
      fi
      vcredist_dir="${vcredist_dir%/}/$ARCH"
      [ -d "$vcredist_dir" ] || vcredist_dir=""
    fi
    if [ -z "$vcredist_dir" ]; then
      echo "  NOTE: VCToolsRedistDir unset or unusable — falling back to a filesystem scan."
      echo "        Run packaging from a Developer PowerShell so the runtime matches the compiler."
      # Sort version dirs numerically (-V) so 14.51 beats 14.9, newest last.
      for root in "/c/Program Files/Microsoft Visual Studio"/*/*/VC/Redist/MSVC \
                  "/c/Program Files (x86)/Microsoft Visual Studio"/*/*/VC/Redist/MSVC; do
        [ -d "$root" ] || continue
        for v in $(ls -1 "$root" 2>/dev/null | sort -V); do
          [ -d "$root/$v/$ARCH" ] && vcredist_dir="$root/$v/$ARCH"
        done
      done
    fi
    [ -n "$vcredist_dir" ] && [ -d "$vcredist_dir" ] || {
      echo "ERROR: no VC++ redistributable directory found for arch '$ARCH'." >&2
      echo "       Set VCToolsRedistDir (a VS Developer shell does this) or install the" >&2
      echo "       'C++ ... Redistributable MSMs' component. The artifact cannot be made" >&2
      echo "       self-contained without it." >&2
      exit 1
    }
    n_crt=0
    for dll in vcruntime140.dll vcruntime140_1.dll msvcp140.dll vcomp140.dll; do
      # CRT and OpenMP live in sibling Microsoft.VC*.{CRT,OpenMP} dirs under the same arch dir.
      src=""
      for cand in "$vcredist_dir"/Microsoft.VC*/"$dll"; do
        [ -e "$cand" ] && src="$cand"
      done
      [ -n "$src" ] || {
        echo "ERROR: $dll not found under $vcredist_dir — cannot stage the VC++ runtime." >&2
        exit 1
      }
      # The single licence boundary. Microsoft's Distributable List grants everything under
      # VC\redist EXCEPT debug_nonredist/ and onecore/debug_nonredist/, which hold the DEBUG
      # CRT/OpenMP and are explicitly non-redistributable. Today's glob cannot reach them
      # (debug_nonredist is a sibling of x64/, not a child), so this asserts a property rather
      # than fixing a bug — but shipping a debug CRT is a licence violation, not a bug, and it
      # would look identical in the artifact. Cheap insurance against a future loosened glob.
      case "$src" in
        *debug_nonredist*)
          echo "ERROR: refusing to stage $dll from a debug_nonredist path:" >&2
          echo "       $src" >&2
          echo "       Microsoft's Distributable List excludes it. This is a licence" >&2
          echo "       boundary, not a build preference." >&2
          exit 1
          ;;
      esac
      cp "$src" "$STAGE/bin/"
      n_crt=$((n_crt + 1))
    done
    echo "  staged $n_crt VC++ runtime DLL(s) from $(basename "$(dirname "$(dirname "$src")")")"

    # Verify the tree is actually self-contained, HERE, at the moment it is assembled. This must
    # not be deferred to smoke.sh: smoke.sh is run per-artifact and by hand, and an artifact that
    # was never smoke-tested would ship with no check at all. Packaging is the only step that
    # every Windows artifact passes through by construction.
    #
    # Hard-fail when python is missing rather than skipping. A guard that silently opts out on
    # the machine that lacks the tooling is worth nothing — this exact defect shipped in every
    # 1.0.x artifact because nothing on the build box could observe it.
    py=""
    for cand in python python3; do
      command -v "$cand" >/dev/null 2>&1 && { py="$cand"; break; }
    done
    [ -n "$py" ] || {
      echo "ERROR: python not found — cannot verify the artifact is self-contained." >&2
      echo "       scripts/check_pe_imports.py is a required packaging step, not optional." >&2
      exit 1
    }
    "$py" "$ROOT/scripts/check_pe_imports.py" "$STAGE/bin" || {
      echo "ERROR: staged tree has undeclared runtime dependencies (see above)." >&2
      exit 1
    }
  fi
fi

# CUDA on Windows keeps the historical static-with-redist shape (Windows functional builds are
# produced manually in a VS Dev shell and packaged with --no-build).
if [ "$KIND" = cuda ] && [ "$OS" != linux ]; then
  [ -n "${CUDA_PATH:-}" ] || { echo "ERROR: CUDA_PATH not set (need it to locate the redist DLLs)." >&2; exit 1; }
  cudabin="$(cygpath -u "$CUDA_PATH" 2>/dev/null || echo "$CUDA_PATH")/bin/x64"
  copied=0
  for pat in 'cudart64_*.dll' 'cublas64_*.dll' 'cublasLt64_*.dll'; do
    for dll in "$cudabin"/$pat; do
      [ -f "$dll" ] && { cp "$dll" "$STAGE/bin/"; copied=$((copied + 1)); }
    done
  done
  [ "$copied" -gt 0 ] || { echo "ERROR: no CUDA redist DLLs found in $cudabin" >&2; exit 1; }
  echo "  bundled $copied CUDA redist DLL(s) from $cudabin"
fi

# Skills: runtime data only. Native v1 ships ffmpeg + documents (io is stale, excluded).
for skill in ffmpeg documents; do
  dst="$STAGE/skills/$skill"
  mkdir -p "$dst"
  for f in skill.yaml tools.yaml prompt.yaml vocab.yaml; do
    [ -f "skills/$skill/$f" ] && cp "skills/$skill/$f" "$dst/"
  done
  [ -d "skills/$skill/profiles" ] && cp -r "skills/$skill/profiles" "$dst/"
done

cp contracts/runtime/core_tools.yaml "$STAGE/contracts/runtime/"
cp contracts/models/model-manifest.yaml "$STAGE/contracts/models/"
cp LICENSE "$STAGE/"
# NOTICE ships beside LICENSE, not instead of it. Apache-2.0 §4(d) requires the NOTICE file to
# travel with every redistribution, and this is the file carrying the Qwen3 derivation attribution
# for the models knaif downloads. It was missing from every artifact on every OS until 2026-07-26
# (F11); installers/smoke.sh now asserts both files are present so it cannot silently stop again.
cp NOTICE "$STAGE/"

# Third-party license notices. Rust deps always; llama.cpp for any inference build; NVIDIA redist
# for CUDA; PDFium for pdfium builds. (installers/licenses/ is the committed source; regen the Rust
# report with `just licenses`.)
mkdir -p "$STAGE/licenses"
cp installers/licenses/THIRD-PARTY-RUST.txt "$STAGE/licenses/"
if [ "$KIND" != base ]; then
  cp installers/licenses/llama.cpp-LICENSE.txt "$STAGE/licenses/"
fi
if [ "$KIND" = cuda ] && [ "$OS" != linux ]; then
  # Hard failure, not a warning: we are redistributing NVIDIA's cudart/cublas/cublasLt, which their
  # EULA permits only WITH the licence text. A warning scrolls past in a build log and ships anyway;
  # a licence violation is worse than a failed build.
  [ -f installers/licenses/NVIDIA-CUDA-EULA.txt ] || {
    echo "ERROR: installers/licenses/NVIDIA-CUDA-EULA.txt is missing — it is REQUIRED to redistribute" >&2
    echo "       the bundled CUDA DLLs. Copy it from the toolkit: \$CUDA_PATH/EULA.txt" >&2
    exit 1
  }
  cp installers/licenses/NVIDIA-CUDA-EULA.txt "$STAGE/licenses/"
fi

case "$KIND" in
  base)   INFER="NOTE: base build — no inference backend. 'run' cannot do real inference (dev/plumbing artifact)." ;;
  cpu)    INFER="Inference: CPU (llama.cpp, loadable ggml-cpu backends with runtime dispatch)." ;;
  vulkan) INFER="Inference: Vulkan GPU (cross-vendor) with CPU fallback. Needs a Vulkan-capable GPU driver. Add the CUDA opt-in payload to ~/.knaif/backends for NVIDIA CUDA offload." ;;
  cuda)   INFER="Inference: NVIDIA CUDA GPU with CPU fallback. CUDA runtime DLLs are bundled; needs an NVIDIA driver." ;;
esac

cat > "$STAGE/README.txt" <<EOF
knaif $VER — native CLI ($OS-$ARCH${SUFFIX})

knaif turns a natural-language request into a validated, executable action plan: the
model only proposes the plan, and deterministic code validates, confirms and runs it.

Maintained by Blackdeep Technologies Ltd. — https://blackdeep.tech
License:  Apache-2.0 (see LICENSE; NOTICE carries required attribution).
          licenses/ holds third-party notices for bundled components.
Issues:   https://github.com/blackdeep-tech/knaif/issues

Quick start:
  bin/$EXE skills list                        list available skills
  bin/$EXE skills deps                         check required external tools (ffmpeg, ...)
  bin/$EXE models pull knaif-qwen3-4b-v1       download the recommended model (~2.5 GB)
  bin/$EXE run ffmpeg "compress clip.mp4 for email" --model knaif-qwen3-4b-v1

Models live in ~/.knaif/models. External tools (ffmpeg, LibreOffice, Ghostscript,
Tesseract) install separately — run 'skills deps' to see what each skill needs.

$INFER
EOF

mkdir -p dist
OUT="dist/$NAME"
if [ "$ARCHIVE" = "zip" ]; then
  rm -f "$OUT.zip"
  # Windows' bundled bsdtar — NOT the GNU tar on the Git-Bash PATH, which cannot write zip at all,
  # and NOT PowerShell 5.1's Compress-Archive, which writes BACKSLASH path separators in violation
  # of APPNOTE 4.4.17.1. Explorer and 7-Zip tolerate those; unzip and Python's zipfile do not, and
  # cannot reconstruct the tree. bsdtar ships with Windows 10 1803+ / 11, so this adds no dependency.
  wintar="$(cygpath -u "${SYSTEMROOT:-C:\\Windows}" 2>/dev/null || echo /c/Windows)/System32/tar.exe"
  [ -x "$wintar" ] || {
    echo "ERROR: $wintar not found — Windows bsdtar is required to write a spec-compliant zip." >&2
    exit 1
  }
  "$wintar" -a -c -f "$OUT.zip" -C dist/staging "$NAME"
  ART="$OUT.zip"
else
  tar czf "$OUT.tar.gz" -C dist/staging "$NAME"
  ART="$OUT.tar.gz"
fi

# Self-containment smoke: run from OUTSIDE the checkout so resource resolution must be exe-relative
# (an empty KNAIF_SKILLS_ROOT is treated as unset; a temp cwd has no `skills/` above it).
TMP="$(mktemp -d)"
(
  cd "$TMP"
  KNAIF_SKILLS_ROOT= "$ROOT/$STAGE/bin/$EXE" --version >/dev/null
  KNAIF_SKILLS_ROOT= "$ROOT/$STAGE/bin/$EXE" skills list >/dev/null
)
rm -rf "$TMP"
echo "  ✓ self-contained (skills list works from outside the checkout)"

echo "Created $ART ($(du -h "$ART" | cut -f1)) [kind=$KIND]"
