# Contributing to knaif

Thanks for your interest. This guide covers getting set up, what a PR must pass, and how
the repository is laid out.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). For security
issues, **do not open a public issue** — follow [SECURITY.md](SECURITY.md).

## There is no CI yet

**Run the checks locally.** CI lands after the v1.0.0 release, so until then a green
`just check` on your machine is the only gate. Say in your PR which platform you ran on —
knaif ships on Windows and Linux and some failures are platform-specific.

## Setup

You need [uv](https://docs.astral.sh/uv/) and [just](https://just.systems). For the native
runtime you also need a Rust toolchain.

```bash
just bootstrap        # provisions the pinned toolchain via mise, if you have it
just init             # uv venv + all dependencies, including dev
```

`just bootstrap` is optional — it installs the versions pinned in [mise.toml](mise.toml)
via [mise](https://mise.jdx.dev), and prints guidance if mise is absent. Without it, put
Python 3.14 and uv 0.11.x on PATH yourself. `just init` is `uv venv` followed by
`just install`; run `just install` alone if you already have a venv.

Local inference is optional — mock inference and the Ollama backend need no model
download. For llama.cpp:

```bash
just install-llama    # CPU (Linux needs a C/C++ compiler + CMake; macOS enables Metal)
just install-cuda     # NVIDIA GPU; ends by running `just gpu-check` to prove offload
```

Both recipes are platform-split and the CUDA one differs meaningfully per OS — see
[`docs/INFERENCE.md`](docs/INFERENCE.md) before debugging a build.

## What a PR must pass

```bash
just check            # the full gate: Python + native
```

That is `check-py` (lint, type-check, tests, generated-docs check) plus `check-native`
(`cargo fmt` + `clippy`, **warnings are errors**). Narrower targets while iterating:

| Command | Scope |
|---|---|
| `just test-py` | Python suite (core + all skills), with coverage |
| `just test-skill <name>` | one skill's tests |
| `just test-native` | `cargo test --workspace` |
| `just lint` / `just type-check` | ruff / mypy |
| `just format` | black |
| `just check-native` | `cargo fmt --check` + clippy |

Or directly:

```bash
uv run pytest python/core/tests/ --tb=short
uv run pytest skills/ --tb=short
cargo test --workspace
```

**Both runtimes must stay in step.** If you change planning, validation, or expansion,
run `just parity <skill>` — it pins both runtimes to the same GGUF and diffs the rendered
output. A change that makes the two disagree is a bug regardless of whether tests pass.

```bash
just parity ffmpeg --limit 20            # diff rendered commands (needs fixtures + a built binary)
just parity ffmpeg --mode plan --batch   # diff plan envelopes for every intent, ~2-3x faster
```

Read the recipe's comments in the [justfile](justfile) first — the two modes cover
different ground, and both want a warm native build.

## Running it by hand

```bash
just cli ffmpeg convert video.mp4 to mov    # Python runtime; prompt words unquoted, flags after
just native ffmpeg "convert clip.mp4 to mkv"  # native runtime, real inference
just native-mock -- skills list             # native runtime, mock backend, fast build
just rs <args>                              # arbitrary cargo command in the workspace
```

`just native-vulkan` / `just native-cuda` pick a GPU backend without the `KNAIF_FEATS`
dance. The **first** run with a given GPU backend compiles llama.cpp's kernels — CUDA can
take 15–30 minutes with the CPU pegged. That is compilation, not a hang.

Model names resolve through [`models.yaml`](models.yaml); see
[`docs/INFERENCE.md`](docs/INFERENCE.md).

## Packaging

Release artifacts are built from the workspace, not by hand:

```bash
just package-native vulkan   # the release artifact: exe + core libs + CPU and Vulkan backends
just installer vulkan        # Windows: wrap the staged artifact in an Inno Setup installer
just licenses-all            # regenerate both third-party licence reports
```

`just package-native` must run from a "Developer PowerShell for VS" on Windows. Full
procedure — per OS and kind, CUDA arch range, checksums, publishing — is in
[`docs/RELEASE.md`](docs/RELEASE.md).

## Repository layout

Four pillars plus support directories:

```text
python/core/knaif/   the knaif Python package (import path is knaif.*)
python/training/     LoRA/DPO dataset builders and training scripts
native/crates/       reusable Rust engine crates (knaif-core, -models, -llm, -skill-api)
apps/cli/            the native knaif CLI binary
skills/<name>/       self-contained skill bundles (YAML + python/ + native/ + data/ + eval/)
contracts/           language-neutral contracts shared by both runtimes
evals/               eval run history, baselines, retrieval and parity results
docs/                documentation and durable plans
```

The native runtime is a **port, not a rewrite**. Both runtimes read the same YAML
contracts and the same skill bundles, so the same utterance must produce the same command
on both sides.

### Contracts are generated in one direction

`contracts/runtime/core_tools.yaml` is canonical; a byte-identical copy ships inside the
wheel. Edit the canonical file, then run `just sync-runtime` — a drift-guard test fails
otherwise. Likewise, the README's skill inventory is generated: run `just gen-skills`
after changing a `skill.yaml` (`just gen-skills-check` is part of `just check`).

## Working on a skill

Start with [`docs/TOOL_SCHEMA.md`](docs/TOOL_SCHEMA.md). A skill is a self-contained
bundle at `skills/<name>/` — declarative YAML at the top, implementations beneath. Every
tool is a `Step` or `Intent` class linked to its `tools.yaml` entry by `name`.

Handlers are only step one. A skill is "done" when it is also evaluated, represented in
the training mix, and — if it ships natively — ported. Corpora and the acceptance bar live
in the bundle, not centrally:

```bash
just eval-fixtures <skill>    # regenerate fixtures (idempotent) — do this first
just eval <skill>             # cheap verifier — fast routing gate, no external binaries
just eval-output-diff <skill> # executes the real tool, diffs against baseline commands
just eval-success <skill>     # honest metric — real execution + success_criteria grading
just eval-regression <skill>  # gate against the committed snapshot
```

Verifiers run cheapest-first: `cheap` → `output_diff` → `success`. Use `cheap` while
iterating; the executing verifiers need the skill's underlying tool (e.g. `ffmpeg`) on
PATH, and the fixtures the corpus references.

**Quote `success`, not `cheap`, when reporting quality.** `cheap` only checks routing.

By default the eval suite uses mock inference. For a real model, add
`--config eval_backends.yaml --backends <name>` — or use the multi-backend recipes:

```bash
just eval-backends <skill>              # every backend in eval_backends.yaml, side-by-side
just eval-compare <skill> mock,ollama   # a named subset
just retrieval                          # retrieval quality (recall@k / MRR), model-independent
just retrieval-check                    # gate retrieval against the locked baseline
```

`just eval-snapshot <skill>` **re-locks the acceptance bar** at
`skills/<skill>/data/eval_snapshot.json` — do that deliberately, in its own commit, and
only when adopting a measured improvement.

`evals/` is the single home for run history. Save with
`--save evals/runs/<YYYY-MM-DD>_<label>_<verifier>/` and add a row to
[`evals/INDEX.md`](evals/INDEX.md); never write to a root-level `results/` or `runs/`.
`.gitignore` keeps the durable summaries (`score.json`, `report.md`, `review_log.json`)
and drops generated media, so commit the run rather than pruning it by hand.

Schema, verifier semantics, and scoring: [`docs/EVAL_FRAMEWORK.md`](docs/EVAL_FRAMEWORK.md).
Verifying results by hand: [`docs/EVAL_VERIFICATION_SOP.md`](docs/EVAL_VERIFICATION_SOP.md).

## Conventions

- **Commits** follow `type: subject` (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`,
  `chore:`, plus domain prefixes like `eval:`, `corpus:`, `native:`).
- **Don't** import a specific skill from core, hard-code skill-specific safety rules in
  core, or resolve variable references during `validate_plan` (resolution is runtime
  behaviour).
- **Notebooks:** clear outputs before committing — they carry absolute paths and execution
  metadata otherwise. Skill-specific notebooks live with their skill under
  `skills/<name>/notebooks/`; top-level `notebooks/` is reserved for cross-skill
  experiments and authoring tools. Open one with `just notebook <path>`.
- **Performance claims:** cite [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) rather than
  restating figures — it records which machine produced which number.
- **Plans** go in `docs/plans/YYYY-MM-DD-<topic>.md`; `docs/TODO.md` holds active
  checklists.

## Where to start

| Task | Start here |
|---|---|
| Add or change a skill | `skills/<name>/` + `docs/TOOL_SCHEMA.md` |
| Plan validation | `python/core/knaif/planner.py` |
| Prompt format | `python/core/knaif/prompt.py` |
| Execution flow | `python/core/knaif/agent.py` |
| Port a skill to native | `skills/<name>/native/` + `docs/NATIVE.md` |
| Training data | `skills/<name>/data/train.jsonl` + `docs/TRAINING_DATA_GENERATION.md` |

Architecture overview: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Native runtime:
[`docs/NATIVE.md`](docs/NATIVE.md).

## Licence

Contributions are licensed under [Apache-2.0](LICENSE), matching the project. If you add a
dependency, run `just licenses-all` and commit the regenerated reports — both trees are
**permissive-only**, and the Python generator fails on copyleft.
