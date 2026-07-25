# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] — 2026-07-25

Bug-fix release. All fixes are in the Python runtime; the native runtime is unchanged and
its version tracks the workspace. The `knaif.cli` SDK did not validate arguments on Python
3.10, the oldest interpreter `requires-python` advertises — found by a clean-room run on the
version floor rather than the development environment (3.13).

### Fixed

- **CLI SDK arguments are now schema-validated on Python 3.10.** `Arg` and `Opt` were
  unhashable dataclasses, so `get_type_hints(include_extras=True)` raised on 3.10 (which
  hashes `Annotated` metadata; 3.11+ does not). `build_registry` caught the error and fell
  back to no schemas, so every argument silently skipped validation. `Arg`/`Opt` are hashable
  again, and the fallback now warns instead of degrading in silence.
- **Optional argument types resolve regardless of wrapper order.** Python 3.10 re-wraps a
  `None`-defaulted parameter as `Optional[Annotated[…]]`, putting the union outside
  `Annotated` — the reverse of 3.11+. Annotation unwrapping now peels both layers in any
  order, so `X | None` and `Optional[X]` derive the same schema on every supported version.
- **Model-load and Ollama failures no longer print to stdout.** They go through the warnings
  machinery, so a failed load can't contaminate the output of a CLI that embeds knaif. The
  unreachable-Ollama message also no longer carries the raw urllib3 exception chain.
- **The test suite collects on Python 3.10.** A test imported `tomllib` unconditionally
  (stdlib only from 3.11); it now falls back to the `tomli` backport, added to the `dev` extra.

## [1.0.0] — 2026-07-20

First public release. knaif turns a natural-language request into a **validated JSON
action plan** and executes it through skill packages. The model only ever proposes a
plan; every step of validation, safety classification, expansion, confirmation, and
execution is deterministic code.

### Runtimes

- **Python library and SDK** (`knaif`) — the authoring, evaluation, and training runtime.
  `CommandAgent` drives the full pipeline: prompt construction → inference → plan parse →
  validation → intent expansion → optimization → variable resolution → execution.
- **Native CLI** (`knaif`, Rust) — the shipped runtime, with `skills`, `models`, `plan`,
  and `run` commands. It is a **port, not a rewrite**: both runtimes read the same
  language-neutral YAML contracts and the same skill bundles, so the same utterance
  renders the same command on either side. Cross-runtime parity is enforced by
  `just parity <skill>` against shared contract fixtures.

### Skills

Skills are self-contained bundles under `skills/<name>/` — declarative YAML at the top,
per-language implementations beneath.

| Skill | Runtimes | Status |
|---|---|---|
| `ffmpeg` | Python + native | supported |
| `documents` | Python + native | supported |
| `io` | Python only | stale — under rebuild |

### Developer SDK

`knaif.cli` adds a natural-language front end to an existing CLI. Declare commands with a
click-like decorator, or wrap an existing `click.Group` with `nk.from_click(cli)`.

### Models

Two fine-tuned models, downloaded on first run rather than bundled:

| Model | Quant | Base |
|---|---|---|
| `knaif-qwen3-4b-v1` | Q4_K_M | `Qwen/Qwen3-4B` |
| `knaif-qwen3-1.7b-v1` | Q6_K | `Qwen/Qwen3-1.7B` |

Both are derivative works of Qwen3 (Apache-2.0) — see [`NOTICE`](NOTICE) and
[`docs/PROVENANCE.md`](docs/PROVENANCE.md). Downloads are pinned to a commit SHA and
verified against a recorded SHA-256. Mock inference and an Ollama backend need no
download at all.

### Safety model

- The model emits only `{ "plan": [...] }` — unknown tools and unsupported arguments are rejected.
- Sandbox-sensitive paths are validated before execution **and again after variable resolution**.
- `dry_run=True` previews a plan without side effects.
- Steps marked `safety_category: destructive` require explicit confirmation.
- Preview gates can route through a caller-supplied `confirmer` callback.

### Evaluation

Per-skill corpora and a committed acceptance bar live inside each bundle. Verifiers run
cheapest-first — `cheap` (routing only) → `output_diff` → `success` (real execution graded
against each row's criteria). `success` is the honest metric.

### Platforms

| Platform | Artifacts |
|---|---|
| Windows x64 | portable `.zip`, per-user installer `.exe` |
| Linux x64 | portable `.tar.gz`, `.AppImage` |

Each default artifact carries **CPU and Vulkan** backends.

### Known limitations

- **macOS is not supported at v1.** The Rust core is cross-platform and the inference
  backend trait keeps macOS reachable; only packaging is deferred to a fast-follow.
- **No CUDA artifact.** CUDA is a manual, opt-in build. The default artifact's Vulkan
  backend covers GPU acceleration on NVIDIA hardware.
- **Windows builds are unsigned**, so SmartScreen will warn on first run.
- **The `io` skill is stale** and under rebuild; `ffmpeg` and `documents` are the
  production skills.
- External tools are **not bundled** — FFmpeg in particular must be installed separately
  and remains under its own license.

[1.0.0]: https://github.com/blackdeep-tech/knaif/releases/tag/v1.0.0
