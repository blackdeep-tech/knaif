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
package *args:
    bash "{{justfile_directory()}}/installers/package.sh" {{args}}

# Build a FUNCTIONAL release artifact (real llama.cpp inference) and package it into dist/.
# kind = cpu | vulkan | cuda. RUN FROM A "Developer PowerShell for VS" (needs MSVC + cmake on
# PATH; Vulkan also needs Ninja). Sets LIBCLANG_PATH to the default LLVM\bin if unset.
#   just package-native vulkan    # THE RELEASE ARTIFACT: exe + core libs + CPU *and* Vulkan backends
#                                 # (Option 3 / C5). Gets the plain name; forces the Ninja generator.
#   just package-native cpu       # build kind only (a box with no Vulkan SDK) -> `-cpu` suffix.
#                                 # NOT a release artifact: C5b ships one default artifact per OS.
#   just package-native cuda      # Linux: opt-in CUDA payload for ~/.knaif/backends (NOT an app)
#
# `dynamic-backends` is REQUIRED for cpu/vulkan: it is what makes the ggml backends loadable, which
# is what lets CUDA be opt-in (C5/Option 3). Building without it produces a static exe that
# installers/package.sh will not stage core libs for — the resulting artifact cannot start.
# KEEP THE FEATURE SETS IN SYNC WITH `feats_for_kind` in installers/package.sh (the source of truth).
#
# Windows `cuda` is the ONE exception: it keeps the historical static-with-redist shape that
# package.sh's Windows cuda branch expects, so it is built WITHOUT dynamic-backends. Aligning it
# onto Option 3 is post-v1 (C6); v1 publishes no CUDA artifact on either OS.
[windows]
package-native kind="cpu":
    $feats=@{cpu='llama,dynamic-backends';vulkan='llama,dynamic-backends,vulkan';cuda='llama,cuda'}['{{kind}}']; if(-not $feats){throw 'kind must be cpu|vulkan|cuda'}; if(-not $env:LIBCLANG_PATH){$env:LIBCLANG_PATH='C:\Program Files\LLVM\bin'}; if('{{kind}}' -eq 'vulkan'){$env:CMAKE_GENERATOR='Ninja'}; cargo build --release -p knaif-cli --features $feats; if($LASTEXITCODE){exit $LASTEXITCODE}; bash '{{justfile_directory()}}/installers/package.sh' --no-build --kind={{kind}}

[unix]
package-native kind="cpu":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{kind}}" in
      cpu) feats=llama,dynamic-backends;;
      vulkan) feats=llama,dynamic-backends,vulkan;;
      cuda) feats=llama,dynamic-backends,cuda;;
      *) echo "kind must be cpu|vulkan|cuda" >&2; exit 1;;
    esac
    CMAKE_GENERATOR="${CMAKE_GENERATOR:-Ninja}" cargo build --release -p knaif-cli --features "$feats"
    bash "{{justfile_directory()}}/installers/package.sh" --no-build --kind={{kind}}

# Compile the Windows Inno Setup installer from the STAGED artifact (stage it first, e.g.
# `just package-native cpu`). Produces dist/knaif-<ver>-windows-x64[-<kind>]-setup.exe. Needs
# Inno Setup 6 (ISCC). kind selects which staged artifact to wrap (cpu|vulkan|cuda).
[windows]
installer kind="cpu":
    $iscc=@("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe","${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1; if(-not $iscc){throw 'ISCC.exe not found — install Inno Setup 6 (winget install JRSoftware.InnoSetup)'}; & $iscc /DKind={{kind}} "{{justfile_directory()}}\installers\windows\knaif.iss"

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
eval-snapshot skill *args:
    uv run python -m knaif.evalsuite run --skill {{skill}} --verifier output_diff --snapshot --save evals/runs/snapshot_{{skill}}_output_diff {{args}}

# Regression check against saved snapshot (e.g.: just eval-regression ffmpeg)
eval-regression skill:
    uv run python -m knaif.evalsuite regression --skill {{skill}}

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
