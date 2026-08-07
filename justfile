set windows-shell := ["powershell.exe", "-NoProfile", "-Command"]

# CUDA compiler + target arch. `cuda_nvcc` is the Linux source build of llama-cpp-python;
# `cuda_arch` also drives the native Rust `native-cuda` recipe (as CMAKE_CUDA_ARCHITECTURES).
# Override on the command line, e.g.:  just cuda_arch=120 native-cuda ffmpeg ...
# cuda_arch="native" builds for the GPU present at build time (needs CMake ≥3.24); pin a number
# (e.g. 120 for Blackwell / RTX 50xx) to cross-build without the GPU visible.
cuda_nvcc := "/usr/local/cuda/bin/nvcc"
cuda_arch := "native"

# Default recipe (list all recipes)
_default:
    @just --list

# Resolve a bash that can run installers/*.sh. Unix has exactly one answer; Windows has two, and
# only one of them works: WSL's `bash` (System32\bash.exe) usually shadows Git for Windows on PATH,
# reports `uname -s` = Linux, and therefore sends package.sh looking for `target/release/knaif`
# instead of `knaif.exe`. It also strips backslashes from Windows-style path arguments. The scripts
# need an MSYS bash. Nothing is hardcoded: prefer a non-WSL bash already on PATH, else take the one
# Git for Windows ships beside `git` itself, wherever that happens to be installed.
[windows]
[private]
_bash:
    @$c = Get-Command bash -All -EA SilentlyContinue | Where-Object { $_.Source -notmatch '\\(System32|WindowsApps)\\' } | Select-Object -First 1 -ExpandProperty Source; if (-not $c) { $g = (Get-Command git -EA SilentlyContinue).Source; if ($g) { $c = Join-Path (Split-Path (Split-Path $g)) 'bin\bash.exe' } }; if (-not $c -or -not (Test-Path $c)) { throw 'No MSYS bash found. installers/*.sh need Git for Windows (winget install Git.Git); WSL bash cannot package a Windows build.' }; Write-Output $c

[unix]
[private]
_bash:
    @command -v bash

# Initialize the project virtual environment and install dependencies
init:
    uv venv
    just install

# Install all dependencies including dev
install:
    uv pip install -e "python/core[dev,notebook]"

# Install dependencies for the reference skill bundles (documents PDF/Office stack, OCR).
# These live in the repo-root [dependency-groups], not in the published wheel's extras —
# skills are not packaged, so their deps are a repo concern. OCR also needs the
# `tesseract` binary on PATH (see skills/documents/SPEC.md).
install-skills:
    uv pip install --group documents --group documents-ocr

# Linux needs a C/C++ compiler + CMake; Windows pulls a prebuilt PyPI wheel;
# macOS builds with Metal GPU support enabled automatically.
# Install the CPU llama.cpp inference backend (cross-platform; Metal on macOS)
install-llama:
    uv pip install -e "python/core[llama]"

# Windows: install a prebuilt CUDA wheel from abetlen's index. --index-url +
# --no-deps pins the CUDA wheel so the resolver can't silently fall back to the
# identically-versioned CPU wheel on PyPI ("CUDA installed but not used").
# Because --no-deps also skips llama-cpp-python's own runtime deps, they are
# installed explicitly on the following line.
# Install the CUDA (NVIDIA GPU) llama.cpp backend, then verify offload
[windows]
install-cuda:
    uv pip install -e "python/core[dev,notebook]"
    uv pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12
    uv pip install --force-reinstall --no-cache-dir --no-deps llama-cpp-python==0.3.23 --index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
    uv pip install diskcache jinja2 "numpy>=1.20.0" "typing-extensions>=4.5.0"
    just gpu-check

# Linux (incl. WSL2): no usable prebuilt CUDA wheels exist, so build from source
# against the local CUDA toolkit. Requires build-essential, CMake, and a CUDA
# toolkit ({{cuda_nvcc}} must exist). On WSL the host Windows driver supplies the
# GPU — install only the toolkit, never a Linux display driver.
# Install the CUDA (NVIDIA GPU) llama.cpp backend, then verify offload
[linux]
install-cuda:
    uv pip install -e "python/core[dev,notebook]"
    CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_COMPILER={{cuda_nvcc}} -DCMAKE_CUDA_ARCHITECTURES={{cuda_arch}}" uv pip install --reinstall-package llama-cpp-python --no-binary llama-cpp-python "python/core[llama]"
    just gpu-check

# macOS: CUDA is NVIDIA-only and unavailable. Apple GPUs use Metal, which
# `just install-llama` enables automatically.
# Install the CUDA (NVIDIA GPU) llama.cpp backend, then verify offload
[macos]
install-cuda:
    @echo "CUDA is NVIDIA-only and unavailable on macOS. Use 'just install-llama' — it builds with Metal GPU support automatically."
    @exit 1

# Verify the installed llama.cpp build can offload to the GPU (exits non-zero if CPU-only)
gpu-check:
    uv run -m knaif._gpu_check

# Install core dependencies only
install-core:
    uv pip install -e "python/core"

# Install dev dependencies
install-dev:
    uv pip install -e "python/core[dev]"

# Install notebook dependencies
install-notebook:
    uv pip install -e "python/core[notebook]"

# --- Git hooks (see .pre-commit-config.yaml and CONTRIBUTING.md) ---

# Install the git hooks (pre-commit, commit-msg, pre-push). Run once after `just init`.
hooks-install:
    uv run pre-commit install --install-hooks

# Run every hook against the whole repo (not just staged files)
hooks:
    uv run pre-commit run --all-files

# Run only the slow pre-push tier (mypy, pytest, clippy) against the whole repo
hooks-push:
    uv run pre-commit run --all-files --hook-stage pre-push

# Bump the pinned hook revisions in .pre-commit-config.yaml
hooks-update:
    uv run pre-commit autoupdate

# Remove the installed git hooks
hooks-uninstall:
    uv run pre-commit uninstall --hook-type pre-commit --hook-type commit-msg --hook-type pre-push

# Format code with black
format:
    uv run black .

# Lint code with ruff
lint:
    uv run ruff check .

# Fix linting issues
lint-fix:
    uv run ruff check --fix .

# Type check with mypy
type-check:
    uv run mypy python/core/knaif/

# Run the Python test suite (knaif + all skills) with coverage
test-py:
    uv run pytest --cov=knaif

# Aggregate test target: Python now, native when the Cargo workspace exists (skips cleanly)
test: test-py test-native

# Run knaif core tests only
test-knaif:
    uv run pytest python/core/tests --cov=knaif --tb=short

# Run tests for a specific skill (e.g.: just test-skill ffmpeg)
test-skill skill:
    uv run pytest skills/{{skill}}/ --tb=short

# Sync the packaged core_tools.yaml copy from the contracts/runtime/ canonical source.
# core_tools.yaml is import-critical, so the wheel ships a copy next to the module; this
# keeps it byte-identical to contracts/runtime/ (the cross-language source of truth). The
# drift guard in tests fails if they diverge — run this to fix it.
[windows]
sync-runtime:
    Copy-Item contracts/runtime/core_tools.yaml python/core/knaif/core_tools.yaml -Force
    Write-Host "Synced core_tools.yaml from contracts/runtime/."
[unix]
sync-runtime:
    cp contracts/runtime/core_tools.yaml python/core/knaif/core_tools.yaml
    @echo "Synced core_tools.yaml from contracts/runtime/."

# Sync the packaged LICENSE/NOTICE copies from the repo-root originals. Apache-2.0 requires
# both to travel with every distributed copy, and setuptools resolves `license-files`
# relative to python/core/ (PEP 639 forbids `..`), so the wheel needs its own copies. The
# drift guard in tests fails if they diverge — run this to fix it.
[windows]
sync-license:
    Copy-Item LICENSE python/core/LICENSE -Force
    Copy-Item NOTICE python/core/NOTICE -Force
    Write-Host "Synced LICENSE and NOTICE into python/core/."
[unix]
sync-license:
    cp LICENSE python/core/LICENSE
    cp NOTICE python/core/NOTICE
    @echo "Synced LICENSE and NOTICE into python/core/."

# Regenerate the Built-In Skills inventory in README.md from each skill.yaml
gen-skills:
    uv run python scripts/gen_skills.py

# Fail if README's skill inventory is out of date (run gen-skills and commit)
gen-skills-check:
    uv run python scripts/gen_skills.py --check

# Regenerate media/knaif.ico from media/logo-square.png (run after changing the logo).
# Deliberately NOT part of `just check`: Pillow is not a dev dependency, and gating every check
# run on an ephemeral download to verify an asset that changes once a year is a bad trade.
# `--check` exists for the day that calculus changes.
gen-icon *args:
    uv run --with pillow python scripts/gen_icon.py {{args}}

# --- Grouped recipes (Python authoring + native release runtimes) ---

# Python lint (grouped naming used by CI); delegates to `lint`
lint-py: lint

# Python type-check (grouped naming used by CI); delegates to `type-check`
type-check-py: type-check

# Full Python check: lint + type + test + generated-docs check
check-py: lint-py type-check-py test-py gen-skills-check

# Full CI check: Python now, native when the Cargo workspace exists (skips cleanly)
check: check-py check-native

# Provision the pinned toolchain via mise (mise.toml); prints guidance if mise is absent
[windows]
bootstrap:
    @if (Get-Command mise -ErrorAction SilentlyContinue) { mise install; Write-Host "Toolchain provisioned via mise." } else { Write-Host "mise not found. Install: https://mise.jdx.dev/getting-started.html  then re-run 'just bootstrap'. Fallback: ensure Python 3.14 + uv 0.11.x are on PATH, then 'just init'." }

[unix]
bootstrap:
    @if command -v mise >/dev/null 2>&1; then mise install && echo "Toolchain provisioned via mise."; else echo "mise not found. Install: https://mise.jdx.dev/getting-started.html  then re-run 'just bootstrap'. Fallback: ensure Python 3.14 + uv 0.11.x are on PATH, then 'just init'."; fi

# Native (Rust) recipes. Requires the Rust toolchain (cargo) on PATH — `just bootstrap`
# provisions it via mise. Run arbitrary cargo commands with `just rs <args>`.
rs *args:
    cargo {{args}}

# Rust fmt + clippy gate (fails on any warning)
check-native:
    cargo fmt --all --check
    cargo clippy --workspace --all-targets -- -D warnings

# Rust workspace tests
test-native:
    cargo test --workspace

# Build the native workspace
build-native:
    cargo build --workspace

# Build + run the native CLI with the MOCK backend (no llama.cpp — fast build, `--model` won't
# work). For dev/plumbing/CI: `just native-mock -- --version`, `just native-mock -- skills list`,
# `just native-mock -- models pull knaif-qwen3-4b-v1`. For real inference use `just native` below.
native-mock *args:
    cd "{{invocation_directory()}}"; cargo run --quiet --manifest-path "{{justfile_directory()}}/Cargo.toml" -p knaif-cli {{args}}

# Cargo features for real-inference runs. Override for a GPU backend, e.g.
# `KNAIF_FEATS=llama,vulkan,pdfium just native ...` (or cuda).
FEATS := env_var_or_default("KNAIF_FEATS", "llama,pdfium")

# Default model for `just native`. A name resolves against the model store; a .gguf path is used
# as-is. Override per-run with `KNAIF_MODEL=... just native ...` or an inline `--model` (last wins).
MODEL := env_var_or_default("KNAIF_MODEL", "knaif-qwen3-4b-v1")

# Run a skill through the native CLI with REAL local inference — the manual-testing twin of
# `just cli`. Defaults to --model {{MODEL}}. Mirrors cli's shape:
# `just native ffmpeg "compress clip.mp4 for email"`. First build is slow (compiles llama.cpp).
# Runs from the invocation directory (not the justfile's) so relative input paths (e.g. from
# `sandbox/fixtures/ffmpeg`) resolve against where you're standing, matching `resolve_skills_root`'s
# own upward search.
native skill *args:
    cd "{{invocation_directory()}}"; cargo run --quiet --manifest-path "{{justfile_directory()}}/Cargo.toml" -p knaif-cli --features "{{FEATS}}" -- run {{skill}} --model "{{MODEL}}" {{args}}

# GPU convenience wrappers — pick a backend WITHOUT the PowerShell `$env:KNAIF_FEATS` dance.
# IMPORTANT: the FIRST run with a given GPU backend COMPILES llama.cpp's GPU kernels. CUDA can take
# ~15-30 min and pegs the CPU (that's compilation, not inference — the reason an early run looked
# "frozen" with the CPU hot); subsequent runs reuse the cached build and start fast. These recipes
# drop `--quiet` so you SEE the `Compiling llama-cpp-sys-2` build progress instead of a silent hang.
# Add `--verbose` after the request to confirm GPU offload at runtime (`dev = CUDA`/`dev = Vulkan`
# in the load trace instead of `dev = CPU`). CUDA needs the NVIDIA toolkit (nvcc); `cuda_arch`
# targets the local GPU by default (Blackwell / RTX 50xx = 120).
[windows]
native-cuda skill *args:
    cd "{{invocation_directory()}}"; $env:CMAKE_CUDA_ARCHITECTURES = "{{cuda_arch}}"; cargo run --manifest-path "{{justfile_directory()}}/Cargo.toml" -p knaif-cli --features "llama,cuda,pdfium" -- run {{skill}} --model "{{MODEL}}" {{args}}

[unix]
native-cuda skill *args:
    cd "{{invocation_directory()}}" && CMAKE_CUDA_ARCHITECTURES="{{cuda_arch}}" cargo run --manifest-path "{{justfile_directory()}}/Cargo.toml" -p knaif-cli --features "llama,cuda,pdfium" -- run {{skill}} --model "{{MODEL}}" {{args}}

# Vulkan is the cross-vendor GPU backend (NVIDIA/AMD/Intel). Needs the Vulkan SDK, and must build
# with the Ninja generator (this recipe forces it): the default Visual Studio/MSBuild generator
# breaks on llama.cpp's `vulkan-shaders-gen` ExternalProject install step ("cannot find the batch
# label specified - VCEnd"). Because the Ninja generator drives cl.exe directly, RUN THE WINDOWS
# VARIANT FROM A "Developer PowerShell for VS 2026" — it puts the VS-bundled Ninja on PATH and sets
# the MSVC env (INCLUDE/LIB) a plain shell lacks. (VS bundles Ninja under
# Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja; `winget install Ninja-build.Ninja` also works
# but you still need the MSVC env.) On Linux/macOS no Developer shell is involved — install the
# Vulkan headers, glslc and ninja from your package manager (Ubuntu: `libvulkan-dev glslc
# glslang-tools spirv-tools ninja-build`; see docs/NATIVE.md D0-prep). Ninja is forced on both
# because llama.cpp's shader-gen step wants it either way. If a prior Vulkan build failed under the
# VS generator, clear its stale config first: `just clean-vulkan-build`. First-build caveat as
# native-cuda (lighter compile).
[windows]
native-vulkan skill *args:
    cd "{{invocation_directory()}}"; $env:CMAKE_GENERATOR = "Ninja"; cargo run --manifest-path "{{justfile_directory()}}/Cargo.toml" -p knaif-cli --features "llama,vulkan,pdfium" -- run {{skill}} --model "{{MODEL}}" {{args}}

[unix]
native-vulkan skill *args:
    cd "{{invocation_directory()}}" && CMAKE_GENERATOR="Ninja" cargo run --manifest-path "{{justfile_directory()}}/Cargo.toml" -p knaif-cli --features "llama,vulkan,pdfium" -- run {{skill}} --model "{{MODEL}}" {{args}}

# Remove only the llama-cpp-sys build artifacts so the next GPU build reconfigures cleanly (e.g. to
# switch a half-configured Vulkan build from the VS generator to Ninja). Rebuilds llama.cpp next run.
clean-vulkan-build:
    cargo clean --manifest-path "{{justfile_directory()}}/Cargo.toml" -p llama-cpp-sys-2

# --- Packaging & release (Phase 9) ---

# Regenerate the third-party Rust license report (installers/licenses/THIRD-PARTY-RUST.txt) that
# ships in every artifact's licenses/ dir. Run after changing dependencies.
# Needs: cargo install cargo-about --locked --features cli   (the `cli` feature is required —
# without it the build succeeds but installs no binary).
licenses:
    cargo about generate about.hbs -o installers/licenses/THIRD-PARTY-RUST.txt

# Regenerate the third-party Python license report (installers/licenses/THIRD-PARTY-PYTHON.txt).
# Covers the runtime dependency closure of the distributed wheel only; exits non-zero if a
# copyleft license appears there. Run after changing python/core/pyproject.toml dependencies.
licenses-python:
    uv run python scripts/gen_python_licenses.py

# Both license reports. Re-run before cutting a release: the reports pin dependency VERSIONS,
# so a stale one misreports what the artifact actually ships.
licenses-all: licenses licenses-python

# Stage + archive a portable, self-contained native artifact into dist/ (zip on Windows,
# tar.gz on Unix). BASE build (no llama/GPU features yet). Usage: just package [--no-build].
[windows]
package *args:
    & (just _bash) installers/package.sh {{args}}

[unix]
package *args:
    bash "{{justfile_directory()}}/installers/package.sh" {{args}}

# Build a FUNCTIONAL release artifact (real llama.cpp inference) and package it into dist/.
# kind = cpu | vulkan | cuda (Windows/Linux) | metal (macOS only). RUN FROM A "Developer PowerShell
# for VS" on Windows (needs MSVC + cmake on PATH; Vulkan also needs Ninja). Sets LIBCLANG_PATH to
# the default LLVM\bin if unset (Windows).
#   just package-native vulkan    # THE WINDOWS/LINUX RELEASE ARTIFACT: exe + core libs + CPU *and*
#                                 # Vulkan backends (Option 3 / C5). Gets the plain name.
#   just package-native cpu       # build kind only (a box with no Vulkan SDK) -> `-cpu` suffix.
#                                 # NOT a release artifact: C5b ships one default artifact per OS.
#   just package-native cuda      # opt-in CUDA payload for ~/.knaif/backends (NOT an app), BOTH OSes
#   just package-native metal     # THE MACOS RELEASE ARTIFACT. Gets the plain name (D2: it is the
#                                 # ONLY functional kind there — `cpu`/`vulkan`/`cuda` are refused).
#
# `dynamic-backends` is REQUIRED for EVERY functional kind, `cuda` included: it is what makes the
# ggml backends loadable, which is what lets CUDA be opt-in (C5/Option 3). Building without it
# produces a static exe that installers/package.sh will not stage core libs for — the resulting
# artifact cannot start, and for `cuda` there is no separable ggml-cuda lib at all, so package.sh's
# payload branch has nothing to stage and stops.
# KEEP THE FEATURE SETS IN SYNC WITH `feats_for_kind` in installers/package.sh (the source of truth).
#
# Windows `cuda` USED to be an exception — the pre-Option-3 static-with-redist app, built without
# dynamic-backends. It is not one any more: package.sh emits the opt-in payload on both OSes, and
# 1.1.0 publishes both payloads. The old shape survives only behind `--legacy-windows-cuda-app`.
#
# A cuda build must also carry CUDAARCHS, and on Windows THIS RECIPE is the only place that can set
# it: package.sh refuses to build on Windows (no MSVC from bash), so its own CUDAARCHS export never
# runs and the caller is the last line of defence. Left unset, ggml's default arch list fires and
# package.sh's verify_cuda_archs rejects the result after the full ~183-TU compile. The list is read
# out of package.sh rather than copied, so there is still one source of truth; an explicit CUDAARCHS
# wins, and KNAIF_CUDA_DEV_ARCHS shortens the build exactly as it does on Linux.
#
# CMAKE_DISABLE_FIND_PACKAGE_OpenSSL is set here for the same "package.sh cannot build on Windows"
# reason as CUDAARCHS: this recipe is the only place it can be set, and llama.cpp's LLAMA_OPENSSL
# defaults ON with an unguarded `find_package(OpenSSL)`, so a box that happens to have OpenSSL
# installed links libssl/libcrypto into the llama-common core library. check_pe_imports.py would
# reject the artifact at packaging time — after the full build. See package.sh's own comment.
[windows]
package-native kind="cpu":
    $feats=@{cpu='llama,dynamic-backends,openmp';vulkan='llama,dynamic-backends,vulkan,openmp';cuda='llama,dynamic-backends,cuda,openmp'}['{{kind}}']; if(-not $feats){throw 'kind must be cpu|vulkan|cuda'}; if(-not $env:LIBCLANG_PATH){$env:LIBCLANG_PATH='C:\Program Files\LLVM\bin'}; $env:CMAKE_DISABLE_FIND_PACKAGE_OpenSSL='ON'; if('{{kind}}' -eq 'vulkan'){$env:CMAKE_GENERATOR='Ninja'}; if('{{kind}}' -eq 'cuda' -and -not $env:CUDAARCHS){$env:CUDAARCHS=if($env:KNAIF_CUDA_DEV_ARCHS){$env:KNAIF_CUDA_DEV_ARCHS}else{(Select-String -Path '{{justfile_directory()}}/installers/package.sh' -Pattern '^CUDA_RELEASE_ARCHS="(.+)"$').Matches[0].Groups[1].Value}; Write-Host "  CUDAARCHS=$env:CUDAARCHS"}; cargo build --release -p knaif-cli --features $feats; if($LASTEXITCODE){exit $LASTEXITCODE}; & (just _bash) installers/package.sh --no-build --kind={{kind}}

[unix]
package-native kind="cpu":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{kind}}" in
      cpu) feats=llama,dynamic-backends,openmp;;
      vulkan) feats=llama,dynamic-backends,vulkan,openmp;;
      cuda) feats=llama,dynamic-backends,cuda,openmp;;
      # Metal needs no cargo feature of its own (D1) — GGML_METAL defaults ON under APPLE — so
      # this is the same feature set as `cpu` minus `openmp`. package.sh itself refuses
      # cpu/vulkan/cuda on Darwin and metal everywhere else (D2); this case list only has to
      # stay in sync on names. `openmp` is deliberately omitted here (D3/B5): llama-cpp-2's own
      # default would otherwise link Homebrew's keg-only libomp.dylib whenever the build
      # environment happens to resolve it — an absolute-path dependency that does not exist on a
      # clean Mac, verified 2026-08-07 and caught by check_macho_deps.py (E1).
      metal) feats=llama,dynamic-backends;;
      *) echo "kind must be cpu|vulkan|cuda|metal" >&2; exit 1;;
    esac
    # Both exports below exist in package.sh's own build step too, and are repeated HERE for one
    # reason: this recipe builds directly via cargo and then calls package.sh with `--no-build`,
    # so that step never runs. They must be set before the FIRST configure — neither is a
    # `rerun-if-env-changed` input in llama-cpp-sys-2's build.rs (verified), so setting them later
    # silently keeps a cached build's old value.
    #
    # NOT metal-only: llama.cpp defaults LLAMA_OPENSSL=ON and its bare `find_package(OpenSSL)` has
    # no platform guard, so any box with OpenSSL dev files installed (routine on Linux CI) links
    # libssl/libcrypto into the llama-common core library we ship. See package.sh's own comment on
    # this line for the full chain and why `LLAMA_OPENSSL=OFF` is not the reachable lever.
    export CMAKE_DISABLE_FIND_PACKAGE_OpenSSL=ON
    # The macOS deployment floor IS macOS-only (D9). Without it the floor silently falls back to
    # rustc's own default for aarch64-apple-darwin (11.0, verified) rather than the chosen 12.0 —
    # a real gap, caught 2026-08-03 by checking LC_BUILD_VERSION on the actual output.
    if [ "{{kind}}" = "metal" ]; then
      export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-12.0}"
      echo "  MACOSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET"
    fi
    CMAKE_GENERATOR="${CMAKE_GENERATOR:-Ninja}" cargo build --release -p knaif-cli --features "$feats"
    bash "{{justfile_directory()}}/installers/package.sh" --no-build --kind={{kind}}

# Build the PUBLISHED Linux artifacts inside the floor-pinned container, so the glibc floor is
# chosen rather than inherited from whichever machine ran the build. Docker is required here and
# NOWHERE else in this justfile. See installers/linux/Dockerfile for what is pinned and why.
#
#   just package-linux                 release: builds HEAD's commit from a clean checkout
#   just package-linux --rev=v1.1.0    release: builds that tag
#   just package-linux --dev           development: mounts the worktree (never publish this)
#   just package-linux --kind=cuda     the opt-in CUDA payload, in its own toolkit image
#
# `--kind=cuda` uses installers/linux/Dockerfile.cuda and its own cache volume. Both are separate
# on purpose: the release image carries no CUDA toolkit (3-5 GB for an artifact it does not
# produce), and one volume per kind is required, not tidy — every kind hard-links its libraries
# into the same target/release/, so a stale SONAME symlink from another kind makes the next build
# panic with AlreadyExists.
#
# For a LOCAL artifact you do not need this: `just package-native vulkan` builds natively and the
# floor is simply your own distro's.
#
# Linux release artifacts (tar.gz + AppImage) at a pinned glibc 2.35 floor. Needs Docker.
[windows]
package-linux *args:
    & (just _bash) installers/linux/build-in-container.sh {{args}}

[unix]
package-linux *args:
    bash "{{justfile_directory()}}/installers/linux/build-in-container.sh" {{args}}

# Compile the Windows Inno Setup installer from the STAGED artifact (stage it first with
# `just package-native vulkan`). Needs Inno Setup 6 (ISCC). kind selects which staged artifact to
# wrap and DEFAULTS TO VULKAN, matching knaif.iss's own `#ifndef Kind` default and the one artifact
# per OS that actually ships — `just installer` after the documented release build has to work.
#
# THE OUTPUT NAME CARRIES NO KIND SUFFIX: every kind compiles to
# dist/knaif-<ver>-windows-x64-setup.exe, which is a PUBLISHED artifact with a row in SHA256SUMS.
# So `just installer cpu` overwrites the release installer and silently invalidates its checksum.
# For anything experimental use `just installer-test`, which has its own output dir for this reason.
[windows]
installer kind="vulkan":
    $iscc=@("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe","${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1; if(-not $iscc){throw 'ISCC.exe not found — install Inno Setup 6 (winget install JRSoftware.InnoSetup)'}; & $iscc /DKind={{kind}} "{{justfile_directory()}}\installers\windows\knaif.iss"

# Compile a THROWAWAY installer for wizard verification — the wizard pages are the one part of the
# installer no test can reach, so they have to be looked at, and looking at them must not touch a
# real install. Three things keep it separate: a scratch AppIdGuid (its own uninstall key and its
# own Add/Remove row, labelled "(TEST BUILD)"), /DTestInstall (its own install directory, so the
# two rows cannot end up sharing one tree), and its own output dir (so a rebuild cannot overwrite
# a published setup.exe and invalidate its SHA256SUMS row). Tearing a test build down by deleting
# the production key is exactly how an install ends up with no Add/Remove row and no upgrade path.
#
# Extra ISPP defines pass straight through, which is how the hidden branches get exercised without
# different hardware:
#
#   just installer-test                            the shipped behaviour
#   just installer-test /DMinNvidiaDriver=9999      driver below the floor -> no GPU task
#
# Stage an artifact first (`just package-native vulkan`), as `just installer` needs.
[windows]
installer-test *args:
    $iscc=@("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe","${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1; if(-not $iscc){throw 'ISCC.exe not found — install Inno Setup 6 (winget install JRSoftware.InnoSetup)'}; $out="{{justfile_directory()}}\dist\test-installer"; New-Item -ItemType Directory -Force $out | Out-Null; & $iscc /DAppIdGuid=00000000-0000-0000-0000-00000000TEST /DTestInstall "/O$out" {{args}} "{{justfile_directory()}}\installers\windows\knaif.iss"; if($LASTEXITCODE){exit $LASTEXITCODE}; Get-ChildItem "$out\*.exe" | ForEach-Object { Write-Host "`ntest installer: $($_.FullName)" }

# Clean up tool caches and build artifacts (__pycache__, pytest/mypy/ruff caches,
# *.egg-info, dist/, build/, and the packaged python/core/build/).
clean: _clean

# Single invocation ending in `exit 0`: `clean` is best-effort — a locked or
# delete-pending artifact (e.g. a staged .so held open by another process) must
# never fail the recipe. `-ErrorAction SilentlyContinue` hides the message but still
# leaves $? = false, which `powershell -Command` would otherwise turn into exit 1.
# Leading `@` suppresses just's echo of this (long) line.
[windows]
_clean:
    @foreach ($p in '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '*.egg-info') { Get-ChildItem -Path . -Directory -Filter $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }; 'dist', 'build', 'python/core/build' | Where-Object { Test-Path $_ } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue; exit 0

[unix]
_clean:
    find . -type d \( -name "__pycache__" -o -name ".pytest_cache" -o -name ".mypy_cache" -o -name ".ruff_cache" -o -name "*.egg-info" \) -prune -exec rm -rf {} + 2>/dev/null || true
    rm -rf dist build python/core/build 2>/dev/null || true

# Run Jupyter notebook (specify notebook path as argument)
notebook notebook_path:
    uv run jupyter lab "{{notebook_path}}"

# Run notebook without opening browser
notebook-headless notebook_path:
    uv run jupyter lab --no-browser "{{notebook_path}}"

# Regenerate fixtures into sandbox/fixtures/<skill>/ (e.g.: just eval-fixtures ffmpeg)
eval-fixtures skill *args:
    uv run python -m knaif.evalsuite fixtures regen --skill {{skill}} {{args}}

# Seed draft baseline commands for unseeded corpus rows (e.g.: just eval-seed ffmpeg)
eval-seed skill *args:
    uv run python -m knaif.evalsuite seed-baselines --skill {{skill}} {{args}}

# Open the baseline authoring notebook
baseline-authoring:
    uv run jupyter lab notebooks/baseline_authoring.ipynb

# Run eval with output_diff verifier — real ffmpeg execution (e.g.: just eval-output-diff ffmpeg --save results/)
eval-output-diff skill *args:
    uv run python -m knaif.evalsuite run --skill {{skill}} --verifier output_diff {{args}}

# Run eval with success verifier — ffprobe + success_criteria grading (e.g.: just eval-success ffmpeg --save results/)
eval-success skill *args:
    uv run python -m knaif.evalsuite run --skill {{skill}} --verifier success {{args}}

# Score an external agent's results directory (e.g.: just eval-score-external ffmpeg results/claude-code/)
eval-score-external skill results_dir *args:
    uv run python -m knaif.evalsuite score-external --skill {{skill}} --results-dir {{results_dir}} {{args}}

# Generate report.md and report.html from a scored results dir (e.g.: just eval-report ffmpeg results/)
eval-report skill results_dir *args:
    uv run python -m knaif.evalsuite report --skill {{skill}} --results-dir {{results_dir}} {{args}}

# Mark an eval row as reviewed/rejected/pending (e.g.: just eval-review review_log.json r001 reviewed)
eval-review log_file row status *args:
    uv run python -m knaif.evalsuite review --log {{log_file}} --row {{row}} --status {{status}} {{args}}

# Real-world head-to-head: local knaif vs a premium agent (claude|copilot|codex), cold by default.
# e.g.: just experiment-agent-vs-knaif claude --limit 3   (needs ffmpeg on PATH + the agent CLI)
experiment-agent-vs-knaif agent="claude" *args="":
    uv run python scripts/agent_vs_knaif/run.py --agent {{agent}} {{args}}

# Measure retrieval quality (recall@k / MRR per script slice), model-independent
retrieval *args:
    uv run python -m knaif.evalsuite retrieval {{args}}

# Gate retrieval recall against the locked baseline (used by CI via the test suite too)
retrieval-check:
    uv run python -m knaif.evalsuite retrieval --check evals/retrieval/2026-07-02_phase1.json

# Run eval suite with cheap (text-only) verifier (e.g.: just eval ffmpeg --verbose)
eval skill *args:
    uv run python -m knaif.evalsuite run --skill {{skill}} --verifier cheap {{args}}

# Run eval suite with honest verifier — executes real ffmpeg (e.g.: just eval-honest ffmpeg)
eval-honest skill *args:
    uv run python -m knaif.evalsuite run --skill {{skill}} --verifier honest {{args}}

# Run all backends from eval_backends.yaml and save results to a named stage dir
# e.g.: just eval-stage ffmpeg stage_a --verbose
# e.g.: just eval-stage ffmpeg stage_b --no-retrieval
eval-stage skill stage *args:
    uv run python -m knaif.evalsuite run --skill {{skill}} --config eval_backends.yaml --verifier cheap --save evals/{{stage}} {{args}}

# Compare all backends from eval_backends.yaml side-by-side in one run
# e.g.: just eval-backends ffmpeg --verbose
eval-backends skill *args:
    uv run python -m knaif.evalsuite compare --skill {{skill}} --config eval_backends.yaml --verifier cheap {{args}}

# Writes the bar to skills/<skill>/data/eval_snapshot.json — do it deliberately, in its own
# commit, and only when adopting a measured improvement. Run artifacts go under evals/ like every
# other run; .gitignore keeps only the durable summaries (score.json, report.md), so commit the
# run and add a row to evals/INDEX.md rather than pruning by hand.
# RE-LOCK a skill's acceptance bar (e.g.: just eval-snapshot ffmpeg)
# The VERIFIER IS A PARAMETER, not a constant: `output_diff` is defined in
# skills/ffmpeg/eval/verifiers.py and is NOT a shared verifier. `documents` owns only
# cheap/honest/success, and score_corpus degrades to outcome-accuracy-only when it cannot find
# the named verifier — so hardcoding output_diff here silently produced a non-executing bar for
# every skill that does not own one, and would have downgraded documents' committed `success`
# snapshot (measured 2026-08-04: same 97.6% outcome, every Knaif column n/a). The CLI now refuses
# that outright, and refuses a non-executing verifier like `cheap`; this default just stops
# steering callers into the refusal.
#   just eval-snapshot ffmpeg    --backends <name>                      # ffmpeg owns output_diff
#   just eval-snapshot documents success --backends <name>              # documents does not
eval-snapshot skill verifier="output_diff" *args:
    uv run python -m knaif.evalsuite run --skill {{skill}} --verifier {{verifier}} --snapshot --save evals/runs/snapshot_{{skill}}_{{verifier}} {{args}}

# Regression check against saved snapshot (e.g.: just eval-regression ffmpeg --current path/to/scoreboard.json)
# WITHOUT --current this compares the snapshot to itself and always passes (C0 in the 2026-08-02
# macOS support plan / docs/TODO.md) — it is a smoke check that the snapshot loads, not a gate.
# *args exists so --current is reachable at all; it was silently dropped before this fix.
eval-regression skill *args:
    uv run python -m knaif.evalsuite regression --skill {{skill}} {{args}}

# Compare two backends side-by-side (e.g.: just eval-compare ffmpeg mock,ollama --verbose)
eval-compare skill backends *args:
    uv run python -m knaif.evalsuite compare --skill {{skill}} --backends {{backends}} --verifier cheap {{args}}

# Run the knaif CLI agent — prompt words go unquoted, flags follow after
# e.g.: just cli ffmpeg convert video.mp4 to mov
# e.g.: just cli ffmpeg compress video.mp4 --model qwen3-4b
# e.g.: just cli ffmpeg compress video.mp4 --model-path ./models/qwen.gguf
# Model names resolve against models.yaml; see README "Runtime models".
cli skill *args:
    cd "{{invocation_directory()}}"; uv run knaif-cli run {{skill}} {{args}}

# GGUF both runtimes load for the parity check — identical bytes. Native's `knaif-qwen3-4b-v1`
# The manifest's `file` and Python's `knaif-qwen3-4b-v1` (models.yaml) both resolve to this file, but
# the harness pins BOTH to the path directly (native --model PATH, python --model-path PATH)
# so weight identity is never in doubt. Override with KNAIF_PARITY_MODEL.
PARITY_MODEL := env_var_or_default("KNAIF_PARITY_MODEL", "models/knaif-qwen3-4b-v1-q4_k_m.gguf")

# Cargo appends `.exe` only on Windows; every other target builds a bare `knaif`.
EXE := if os_family() == "windows" { ".exe" } else { "" }

# Native-vs-Python RUNTIME PARITY over a skill's eval utterances (NOT an eval-suite — no
# baselines, no model comparison; see scripts/parity_check.py). Confirms the ported pipeline
# renders identical ffmpeg commands on both runtimes for the same input. Both greedy-decode
# the SAME GGUF, so a diff means a real port sync gap (or a floating-point argmax tie).
# BUILD NATIVE FIRST with your backend so target/debug/knaif[.exe] exists, then run:
#   just native-vulkan ffmpeg "convert clip.mp4 to mkv"   # one warm-up build (any request)
#   just eval-fixtures ffmpeg                              # fixtures the utterances reference
#   just parity ffmpeg --limit 20
# Two comparison levels (pass-through --mode): `--mode command` (default) diffs the rendered
# ffmpeg argv from `run --dry-run` — tests intent expansion + render, but python skips
# compress/platform/thumbnail/batch/reverse and native previews only chain step 1; `--mode plan`
# diffs the `plan --json` envelope (tool+args) for EVERY intent and full chains (no render),
# treating native's materialized optional-arg defaults as equivalent. Run both for full coverage.
# `--batch` (plan mode only) loads each model ONCE and streams all utterances via `plan --batch`
# — ~2-3x faster on full runs (model loads twice total, not 2·N). Recommended for full sweeps:
#   just parity ffmpeg --mode plan --batch
# Pass-through args: --mode plan  --batch  --tags audio,convert  --skip-chains  --strict  --limit N  --out PATH
parity skill *args:
    uv run python "{{justfile_directory()}}/scripts/parity_check.py" --skill {{skill}} --native-bin "{{justfile_directory()}}/target/debug/knaif{{EXE}}" --model-path "{{justfile_directory()}}/{{PARITY_MODEL}}" --cwd "{{justfile_directory()}}/sandbox/fixtures/{{skill}}" {{args}}

# Build the website and package it for Amplify manual upload
# Usage: just web-build
# Then drag site/knaif-site.zip into the Amplify console.
web-build: _web-build-mkdocs _web-zip

_web-build-mkdocs:
    uv pip install mkdocs-material --quiet
    uv run python -m mkdocs build -f site/mkdocs.yml

[windows]
_web-zip:
    Compress-Archive -Path site/site/* -DestinationPath site/knaif-site.zip -Force
    Write-Host "Ready: site/knaif-site.zip"

[unix]
_web-zip:
    cd site/site && zip -r ../knaif-site.zip . -x "*.DS_Store"
    @echo "Ready: site/knaif-site.zip"

# Freeze dependencies to requirements.txt
freeze: _freeze

[windows]
_freeze:
    uv pip freeze | Out-File -FilePath requirements.txt -Encoding utf8

[unix]
_freeze:
    uv pip freeze > requirements.txt
