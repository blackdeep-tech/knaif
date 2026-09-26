set windows-shell := ["powershell.exe", "-NoProfile", "-Command"]

# CUDA compiler + target arch for the PYTHON llama-cpp-python source build (`just install-cuda`).
# The native Rust build no longer reads this: CMake takes its arch list from `CUDAARCHS`, which
# scripts/build_native_kind.sh sets from package.sh's CUDA_RELEASE_ARCHS (one source of truth).
# Shorten a native dev build with `KNAIF_CUDA_DEV_ARCHS=120-real` instead.
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

# The deterministic parity layers — L1 (contract conformance) and L2 (deterministic pipeline)
# — on BOTH runtimes (plan docs/plans/2026-09-10-skill-quality-lifecycle.md, G4).
#
# In `just check` by default because they are the layers that cost nothing to run: no model,
# no GPU, no external binaries, seconds. Their whole purpose is to fail a PR that changes one
# runtime's prompt, retrieval, validation or rendering without the other — which they cannot do
# if running them is a thing you have to remember.
#
# The Python halves already run inside `test-py`; this recipe exists so the RUST halves run too
# (`check-native` is fmt + clippy only) and so a failure names the layer rather than arriving as
# an anonymous cargo test. `just test-native` remains the broader workspace run.
check-contracts:
    uv run pytest python/core/tests/test_prompt_parity.py python/core/tests/test_retrieval_parity.py python/core/tests/test_settings_parity.py python/core/tests/test_planner_parity.py python/core/tests/test_clarify_gate_parity.py python/core/tests/test_arg_gate_parity.py python/core/tests/test_native_tool_parity.py python/core/tests/test_example_selection_parity.py python/core/tests/test_generation_settings.py python/core/tests/test_scoring_contract.py python/core/tests/test_outcomes.py python/core/tests/test_chain_linking_parity.py python/core/tests/test_expansion_parity.py python/core/tests/test_documents_expansion_parity.py -q
    cargo test -p knaif-core --test parity
    cargo test -p knaif-core --test chain_linking_parity
    cargo test -p knaif-skill-ffmpeg --test expansion_parity
    cargo test -p knaif-skill-documents --test expansion_parity
    cargo test -p knaif-llm --test generation
    cargo test -p knaif-cli --test executor_semantics
    cargo test -p knaif-cli --test prompt_examples
    cargo test -p knaif-cli --bin knaif prompt_parity
    uv run python "{{justfile_directory()}}/scripts/parity_check.py" --self-test
    uv run python -m knaif.evalsuite gate --record-contracts

# At the tag: keep the acceptance records and the gate's verdict for this release under
# evals/acceptance/releases/<version>/ (written once). e.g.: just release-record 1.2.0
release-record version:
    uv run python -m knaif.evalsuite gate --release-record {{version}}

# G1/G2 — a skill may not claim a native status its evidence does not support. Reads
# contracts/release/native_status.yaml; `supported` needs an L4 acceptance record, `parity`
# needs an L3 run, and either goes stale when the tree moves underneath it. Also asserts the
# platform matrix: a platform may not be `supported` without recorded parity coverage.
check-gate:
    uv run python -m knaif.evalsuite gate

# Full CI check: Python, native, both websites (astro check), and the L1/L2 parity contracts.
# Needs node + pnpm on PATH — `just bootstrap` provisions them from mise.toml. Site recipes
# live at the bottom.
check: check-py check-native check-contracts check-gate site-check

# Provision the pinned toolchain via mise (mise.toml); prints guidance if mise is absent.
#
# The `rustup component add` is not redundant with rust-toolchain.toml. mise provisions Rust
# with rustup's MINIMAL profile, which ignores that file's `components` — so a contributor who
# bootstraps exactly as documented gets rustc + cargo + rust-std, and `just check-native` then
# dies on a missing clippy before it lints anything. CI hit this in two separate jobs. It is
# idempotent and costs seconds when the components are already there.
[windows]
bootstrap:
    @if (Get-Command mise -ErrorAction SilentlyContinue) { mise install; if (Get-Command rustup -ErrorAction SilentlyContinue) { rustup component add rustfmt clippy } ; Write-Host "Toolchain provisioned via mise." } else { Write-Host "mise not found. Install: https://mise.jdx.dev/getting-started.html  then re-run 'just bootstrap'. Fallback: ensure Python 3.10+ and uv 0.11.x are on PATH, then 'just init'." }

[unix]
bootstrap:
    @if command -v mise >/dev/null 2>&1; then mise install && { command -v rustup >/dev/null 2>&1 && rustup component add rustfmt clippy; } ; echo "Toolchain provisioned via mise."; else echo "mise not found. Install: https://mise.jdx.dev/getting-started.html  then re-run 'just bootstrap'. Fallback: ensure Python 3.10+ and uv 0.11.x are on PATH, then 'just init'."; fi

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

#   just build-native-kind cpu | vulkan | cuda | base
#
# Each kind gets the matching `release-<kind>` cargo profile. Without that, every feature set
# overwrites the same target/release/knaif AND the same staged llama/ggml libs — the cause of the
# `hard_link … AlreadyExists` kind-switch panic and of a cpu package once built from the leftover
# vulkan tree (portable-builds C1/C2). Plain `release` is untouched.
#
# The feature set is read from installers/package.sh (`--print-feats`), never copied, so the two
# cannot drift. On Windows the script locates Visual Studio and enters VsDevCmd.bat when cl.exe is
# absent, so this does NOT need a "Developer PowerShell for VS".
#
# FIRST build of a kind compiles everything from scratch: ~1 min for cpu/base, ~15-30 min for cuda
# (183 CUDA translation units). After that, switching between kinds costs nothing. To reclaim the
# space: `rm -rf target/release-<kind>`.
#
# Build ONE native kind into its own directory: target/release-<kind>/
[windows]
build-native-kind kind="cpu":
    & (just _bash) scripts/build_native_kind.sh {{kind}}

[unix]
build-native-kind kind="cpu":
    bash "{{justfile_directory()}}/scripts/build_native_kind.sh" {{kind}}

#   just verify-build-kind cuda
#
# Assert a built kind got its own directory, binary and staged libs
[windows]
verify-build-kind kind="cpu":
    & (just _bash) scripts/verify_build_profile.sh {{kind}}

[unix]
verify-build-kind kind="cpu":
    bash "{{justfile_directory()}}/scripts/verify_build_profile.sh" {{kind}}

# Assert every active skill bundle loads in BOTH runtimes (post-v1-ci C2).
#
# The bundle's YAML is read by two loaders in two languages, and a bundle that parses in
# Python but not in Rust is invisible until someone runs the native binary. Compares what
# each loader REPORTS — discovery, stale filtering, `runtimes:`, external tools — not that
# each exits 0. Needs the debug binary; `just parity` is the heavier, model-pinned check
# that answers a different question.
loader-check: build-native
    uv run python "{{justfile_directory()}}/scripts/check_loader_compat.py"

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
    & (just _bash) scripts/build_native_kind.sh cuda; if($LASTEXITCODE){exit $LASTEXITCODE}; cd "{{invocation_directory()}}"; & "{{justfile_directory()}}/target/release-cuda/knaif.exe" run {{skill}} --model "{{MODEL}}" {{args}}

[unix]
native-cuda skill *args:
    bash "{{justfile_directory()}}/scripts/build_native_kind.sh" cuda && cd "{{invocation_directory()}}" && "{{justfile_directory()}}/target/release-cuda/knaif" run {{skill}} --model "{{MODEL}}" {{args}}

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
    & (just _bash) scripts/build_native_kind.sh vulkan; if($LASTEXITCODE){exit $LASTEXITCODE}; cd "{{invocation_directory()}}"; & "{{justfile_directory()}}/target/release-vulkan/knaif.exe" run {{skill}} --model "{{MODEL}}" {{args}}

[unix]
native-vulkan skill *args:
    bash "{{justfile_directory()}}/scripts/build_native_kind.sh" vulkan && cd "{{invocation_directory()}}" && "{{justfile_directory()}}/target/release-vulkan/knaif" run {{skill}} --model "{{MODEL}}" {{args}}

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
# kind = cpu | vulkan | cuda. Builds via `just build-native-kind`, so it lands in its own
# target/release-<kind>/ and does NOT need a "Developer PowerShell for VS" — the build script
# locates Visual Studio and enters VsDevCmd.bat itself, and sets LIBCLANG_PATH, CMAKE_GENERATOR
# and CUDAARCHS. package.sh is then pointed at that directory with --profile.
#   just package-native vulkan    # THE RELEASE ARTIFACT: exe + core libs + CPU *and* Vulkan backends
#                                 # (Option 3 / C5). Gets the plain name; forces the Ninja generator.
#   just package-native cpu       # build kind only (a box with no Vulkan SDK) -> `-cpu` suffix.
#                                 # NOT a release artifact: C5b ships one default artifact per OS.
#   just package-native cuda      # opt-in CUDA payload for ~/.knaif/backends (NOT an app), BOTH OSes
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
# A cuda build must also carry CUDAARCHS, and on Windows nothing else can set it: package.sh
# refuses to build on Windows (no MSVC from bash), so its own CUDAARCHS export never runs. Left
# unset, ggml's default arch list fires and package.sh's verify_cuda_archs rejects the result after
# the full ~183-TU compile. scripts/build_native_kind.sh is now what sets it, reading the list out
# of package.sh rather than copying it, so there is still one source of truth; an explicit
# CUDAARCHS wins, and KNAIF_CUDA_DEV_ARCHS shortens the build exactly as it does on Linux.
[windows]
package-native kind="cpu":
    if('{{kind}}' -notin @('cpu','vulkan','cuda')){throw 'kind must be cpu|vulkan|cuda'}; & (just _bash) scripts/build_native_kind.sh {{kind}}; if($LASTEXITCODE){exit $LASTEXITCODE}; & (just _bash) installers/package.sh --no-build --kind={{kind}} --profile=release-{{kind}}

[unix]
package-native kind="cpu":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{kind}}" in
      cpu|vulkan|cuda) ;;
      *) echo "kind must be cpu|vulkan|cuda" >&2; exit 1;;
    esac
    bash "{{justfile_directory()}}/scripts/build_native_kind.sh" {{kind}}
    bash "{{justfile_directory()}}/installers/package.sh" --no-build --kind={{kind}} --profile=release-{{kind}}

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

# L4 — grade the SHIPPED native binary on the files it really produces (executes for real).
# Needs a native build with the llama feature and the external binaries the skill uses.
# Regenerate fixtures first: `just eval-fixtures <skill>` — a missing fixture scores a correct
# plan ~0, so an empty sandbox reports a catastrophe that did not happen.
# e.g.: just eval-native ffmpeg --save evals/runs/2026-09-11_l4-native_success
eval-native skill *args:
    uv run python -m knaif.evalsuite native --skill {{skill}} --lane native-cli --verifier success {{args}}

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

# Writes the bar to skills/<skill>/data/eval_snapshot.json — do it deliberately and in its own
# commit. Two legitimate reasons, and say which one applies: (1) adopting a MEASURED IMPROVEMENT
# — prove it with a per-row join at the same verifier and population, not an aggregate; or
# (2) a COVERAGE re-lock, when the stored population or verifier can no longer evaluate the
# current corpus at all, so the gate raises rather than judging. The 2026-09-08 re-lock was one
# of each: ffmpeg an improvement, documents pure coverage. Never re-lock to make a red gate go
# green. Run artifacts go under evals/ like every
# other run; .gitignore keeps only the durable summaries (score.json, report.md), so commit the
# run and add a row to evals/INDEX.md rather than pruning by hand.
# The verifier is an argument, not a constant. It used to be hardcoded `output_diff`, which
# silently disagreed with both committed bars: documents was always `success`, and measuring
# ffmpeg both ways (2026-09-08) showed `success` grades 574 plan rows to output_diff's 527 —
# so per EVAL_FRAMEWORK's "success, or output_diff where coverage is better", success wins.
# Override only with evidence that output_diff covers more of the skill.
# RE-LOCK a skill's acceptance bar (e.g.: just eval-snapshot ffmpeg [output_diff])
eval-snapshot skill verifier="success" *args:
    uv run python -m knaif.evalsuite run --skill {{skill}} --verifier {{verifier}} --snapshot --save evals/runs/snapshot_{{skill}}_{{verifier}} {{args}}

# Regression check against saved snapshot. `current` is a scoreboard JSON from a real run
# (e.g.: just eval-success ffmpeg --save evals/runs/2026-01-01_check --verifier <snapshot's verifier>,
# then: just eval-regression ffmpeg evals/runs/2026-01-01_check/ffmpeg_<backend>_<verifier>.json).
# No `current` used to silently compare the snapshot to itself and always print "OK" — fixed
# per docs/audits/2026-09-07-core-principles-and-rtx5080.md (F6); now `current` is required.
eval-regression skill current:
    uv run python -m knaif.evalsuite regression --skill {{skill}} --current {{current}}

# S2 acceptance: grade a run against the skill's written bar (skills/<skill>/acceptance.yaml).
# Distinct from eval-regression, which only asks "did it drop since last time" — this asks
# "is it good enough", against floors, required capability slices, and safety at 100%.
# Fails closed: a missing --safety result is a rejection, not an omission.
#   just eval-safety ffmpeg evals/runs/2026-01-01_check/safety.json
#   just eval-accept ffmpeg evals/runs/2026-01-01_check/ffmpeg_<backend>_success.json evals/runs/2026-01-01_check/safety.json
eval-accept skill current safety="":
    uv run python -m knaif.evalsuite accept --skill {{skill}} --current {{current}} {{ if safety == "" { "" } else { "--safety " + safety } }}

# Run a skill's safety corpus — every row must reject; no tolerance, no curve.
eval-safety skill save="" *args:
    uv run python -m knaif.evalsuite safety --skill {{skill}} {{ if save == "" { "" } else { "--save " + save } }} {{args}}

# Same corpus, through the SHIPPED BINARY. Required for L4 acceptance: Python's refusals are
# not evidence that the binary refuses — they are different code reaching a refusal by
# different routes. e.g.: just eval-safety-native ffmpeg evals/runs/2026-09-11_l4/safety.json
eval-safety-native skill save="" *args:
    uv run python -m knaif.evalsuite safety --skill {{skill}} --lane native-cli {{ if save == "" { "" } else { "--save " + save } }} {{args}}

# L4 ACCEPTANCE — the only check that can buy `supported`. Grades a native lane run against
# BOTH the skill's written S2 bar and the frozen Python baseline:
#     native >= max(S2 floor, accepted python score - 0.02)
# on outcome_accuracy and avg_knaif_score, at complete coverage, every required slice holding,
# safety at 100% from the binary. Records its verdict into evals/acceptance/<skill>.json either
# way — a FAILING L4 record is evidence too, and a different state from never having measured.
# The three steps, in order:
#   just eval-fixtures ffmpeg
#   just eval-native ffmpeg --save evals/runs/2026-09-11_l4-ffmpeg_success
#   just eval-safety-native ffmpeg evals/runs/2026-09-11_l4-ffmpeg_success/safety.json
#   just eval-accept-native ffmpeg evals/runs/2026-09-11_l4-ffmpeg_success/ffmpeg_native-cli_success.json evals/runs/2026-09-11_l4-ffmpeg_success/safety.json
eval-accept-native skill current safety="":
    uv run python -m knaif.evalsuite accept-native --skill {{skill}} --current {{current}} {{ if safety == "" { "" } else { "--safety " + safety } }}

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
# compress/platform/thumbnail/batch/reverse and native REFUSES multi-step chains outright
# (it executes one intent per invocation — audit F5 — so a chain row's native outcome is a
# `reject`, which compares as a mismatch rather than a rendered command); `--mode plan`
# diffs the `plan --json` envelope (tool+args) for EVERY intent and full chains (no render),
# treating native's materialized optional-arg defaults as equivalent. Run both for full coverage.
# `--batch` (plan mode only) loads each model ONCE and streams all utterances via `plan --batch`
# — ~2-3x faster on full runs (model loads twice total, not 2·N). Recommended for full sweeps:
#   just parity ffmpeg --mode plan --batch
# Pass-through args: --mode plan  --batch  --tags audio,convert  --skip-chains  --strict  --limit N  --out PATH
parity skill *args:
    uv run python "{{justfile_directory()}}/scripts/parity_check.py" --skill {{skill}} --native-bin "{{justfile_directory()}}/target/debug/knaif{{EXE}}" --model-path "{{justfile_directory()}}/{{PARITY_MODEL}}" --cwd "{{justfile_directory()}}/sandbox/fixtures/{{skill}}" {{args}}

# ---------------------------------------------------------------------------
# Websites — knaif.org (site/org) and knaif.dev (site/dev).
# Plan: docs/plans/2026-08-04-website-split.md
#
# pnpm workspaces, NOT npm. `just bootstrap` provisions node + pnpm from mise.toml.
# `site-check` runs as part of `just check`; `site-build` + `site-links` do not — they
# need a full production build of both sites, which belongs in the release/deploy gate.
# ---------------------------------------------------------------------------

# Install site dependencies (respects the committed lockfile, like Amplify does)
site-install:
    pnpm --dir site install --frozen-lockfile

# Update site dependencies within declared ranges; pass --latest to include major upgrades
site-update *args:
    pnpm --dir site --recursive update {{args}}

# Update pnpm and the site pin; migrate Corepack shims to standalone pnpm if needed
site-pnpm-update version="latest":
    uv run python "{{justfile_directory()}}/scripts/site_pnpm_update.py" "{{version}}"

# Dev server for one site. Usage: just site-dev org   |   just site-dev dev
site-dev app:
    pnpm --dir site --filter knaif-{{app}} dev

# Production build of both sites, exactly as Amplify builds them
site-build:
    pnpm --dir site install --frozen-lockfile
    pnpm --dir site --filter knaif-org build
    pnpm --dir site --filter knaif-dev build

# Type/content check for both sites (astro check) — the site half of `just check`.
# Installs first: `astro check` on a missing node_modules reports a package resolution
# error, which reads as a broken site rather than as an unprovisioned checkout.
site-check:
    pnpm --dir site install --frozen-lockfile
    pnpm --dir site --filter knaif-org check
    pnpm --dir site --filter knaif-dev check

# Internal link + anchor check over the BUILT sites. Needs `just site-build` first —
# Astro checks neither, so a typo'd href or a moved heading anchor is otherwise invisible.
site-links:
    uv run python "{{justfile_directory()}}/scripts/check_site_links.py"

# Contrast + keyboard-navigation pass over the BUILT sites, in a real browser. Needs
# `just site-build` first, and a one-time `just site-a11y-install` for Chromium.
#
# Out of `just check` on purpose, and for a different reason than site-build: this one
# drives a ~150 MB browser that a contributor has no other need for. It belongs to the
# same deploy gate as site-links. Pass --site / --route to narrow it while iterating.
site-a11y *args:
    uv run --group site-a11y python "{{justfile_directory()}}/scripts/check_site_a11y.py" {{args}}

# One-time provisioning for `just site-a11y`. Downloads Chromium into playwright's cache,
# outside the repo.
site-a11y-install:
    uv run --group site-a11y playwright install chromium

# Regenerate the committed catalog data both sites read (drift-guarded by a test)
site-data:
    uv run python "{{justfile_directory()}}/scripts/site_data.py"

# Run AFTER publishing a release (RELEASE.md §5). URLs are never derived from
# Cargo.toml — the version bump lands before the assets exist, so a derived URL
# would advertise a download that 404s.
#
# Refresh site/data/release.json from the latest PUBLISHED GitHub release
release-data:
    uv run python "{{justfile_directory()}}/scripts/release_data.py"

# Freeze dependencies to requirements.txt
freeze: _freeze

[windows]
_freeze:
    uv pip freeze | Out-File -FilePath requirements.txt -Encoding utf8

[unix]
_freeze:
    uv pip freeze > requirements.txt
