# Contributing to knaif

Thanks for your interest. This guide covers getting set up, what a PR must pass, and how
the repository is laid out.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). For security
issues, **do not open a public issue** — follow [SECURITY.md](SECURITY.md).

## What we accept

- **Skills are welcome.** A new skill, or a change to one, is the best way to contribute. Every
  skill comes in under [Apache-2.0](LICENSE); a skill under any other licence is not accepted.
- **The core is maintained by Blackdeep Technologies Ltd.** The core is everything outside
  `skills/`: the runtimes, contracts, toolkits, installers and tooling. For anything there, open
  an issue first. Small fixes may be accepted as pull requests; larger changes are made by the
  maintainers.

Every commit in a pull request is signed off; see [Licence](#licence).

## CI

Every pull request runs CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) on Linux:
the Python suite on 3.10 and 3.14, the native checks, the git hooks, and a lint of the PR title
and every commit. A macOS job builds and smoke-tests the native binary when a change touches
the native code, installers, contracts or a skill's `skill.yaml`. Windows is not in CI.

**Still run `just check` locally first** — CI is a backstop, not the place to find out. Say in
your PR which platform you ran on: knaif ships on Windows, Linux and macOS, and some failures
are platform-specific.

## Setup

You need [uv](https://docs.astral.sh/uv/) and [just](https://just.systems). For the native
runtime you also need a Rust toolchain.

```bash
just bootstrap        # provisions the pinned toolchain via mise, if you have it
just init             # uv venv + all dependencies, including dev
just hooks-install    # optional git hooks — see "Git conventions" below
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

## Git conventions

`main` is always releasable, and nothing lands on it except by pull request. Besides
short-lived work branches there are **integration branches** — a release (`release/X.Y.Z`)
or a big feature that several branches, and sometimes several people, build on. Work reaches
an integration branch by PR, and the integration branch reaches `main` by one PR.

### Branches

Work on a branch named `type/short-slug` — lowercase, kebab-case, and short enough to read
in a PR list:

```text
fix/cli-sdk-py310-args        feat/ffmpeg-batch-intent
docs/eval-ladder              corpus/documents-ocr-rows
refactor/planner-var-binding  release/1.1.0
```

`type` is one of the commit types below, plus `release/` for release prep and `exp/` for
experiments you may never merge (fine-tune runs, backend spikes). Branch names are a
convention, not a gate — nothing rejects a differently named branch.

### Commits

Commit subjects are Conventional-Commits-shaped:

```text
type(scope): subject
```

| Group | Types | Use for |
|---|---|---|
| Code | `feat` `fix` `perf` `refactor` | behaviour a user could notice, or a rewrite that changes none |
| Support | `docs` `test` `build` `ci` `chore` `deps` `revert` | everything around the code |
| Domain | `eval` `corpus` `snapshot` `model` | knaif's own lifecycle — see below |

The **scope** is optional and unvalidated, but prefer an established one: `core`, `cli`,
`sdk`, `native`, `contracts`, `training`, `installers`, `site`, or a skill or toolkit name
(`ffmpeg`, `documents`, `fs`).

Rules the hook enforces:

- subject line **≤ 72 characters**, including the `type(scope): ` prefix
- no trailing period
- a blank line between the subject and the body

Rules it cannot enforce, but reviewers will:

- **imperative mood** — "add a batch intent", not "added" or "adds"
- the body explains **why**, not what; the diff already says what
- breaking changes get a `!` (`refactor(native)!: …`) and a `BREAKING CHANGE:` footer

The domain types exist because this repo's lifecycle has steps that are neither features
nor chores, and they get their own release-note treatment:

| Type | Means |
|---|---|
| `eval` | eval harness, verifiers, or a saved run + its `evals/INDEX.md` row |
| `corpus` | rows added or re-annotated in a skill's `data/*.jsonl` |
| `snapshot` | **re-locking an acceptance bar** (`data/eval_snapshot.json`) |
| `model` | promoting a model — `models.yaml`, the manifest, a skill's `recommended_model:` |

`snapshot` is deliberately its own type: re-locking moves the bar a regression gate is
measured against, so it belongs in **its own commit** whose message says which measured
improvement justified it. A snapshot buried in a feature commit is how a quality
regression gets silently ratified.

Examples:

```text
fix(sdk): validate Arg schemas on Python 3.10
feat(ffmpeg): add a batch-convert intent
snapshot(documents): re-lock at 0.91 success after the retrieval fix
deps: add tomli backport for the 3.10 floor
refactor(native)!: rename HandlerContext::sandbox to root
```

Git's own messages are exempt — merges, `git revert`'s default subject, and
`fixup!`/`squash!` commits headed for `--autosquash` all pass unchecked.

### Pull requests

Two merge methods, chosen by what the branch is:

| Branch | Merged with | Why |
|---|---|---|
| short `fix/…`, `docs/…`, one-change `feat/…` | **squash** | one commit on the target; the branch's own commits are thrown away |
| integration branches (`release/*`, big features others build on) | **merge commit** | others have merged these commits into their own branches; a squash makes new commits git cannot match, so their later merge conflicts and duplicates history. It also keeps every commit an eval report cites reachable from `main` |

Either way the **PR title becomes the subject on the target** (the squash commit, or the
merge commit, whose body is the PR description), so it must follow the commit convention.
With a squash your branch's own commits can be as messy as you like. **With a merge commit
they land on `main` unchanged**, so every one must follow the convention too. CI's
`pr-title` job lints the title **and every non-merge commit of the PR**
(`python scripts/check_commit_msg.py --range BASE..HEAD` runs the same check locally), but
install the hooks (below) so you hear about it at commit time. A few commits shared before the
check existed pass by their full ID (`EXEMPT_COMMITS` in the script); adding one needs the owner. Never rebase a branch someone
else has merged; bring `main`'s changes in by merging. `git log --first-parent main` shows one entry per merged PR.

GitHub appends ` (#123)` to the squashed subject, so a title at the full 72 characters
lands as 78 on `main`. Keep PR titles to **about 65 characters** to leave room for the
number — the `commit-msg` hook checks your local commits, not the squashed result, so
nothing will warn you about this.

Keep a PR to one reviewable change. If you find yourself writing "and also" in the
description, it is probably two PRs — and a `snapshot` re-lock is *always* its own commit,
which for a squash-merged branch means its own PR.

Fill in [the template](.github/PULL_REQUEST_TEMPLATE.md), and say which platform you ran
`just check` on — CI runs on Linux (and macOS only for native changes), so for Windows that
statement is the only evidence a reviewer has.

### Tags and releases

Releases are [SemVer](https://semver.org), tagged `vMAJOR.MINOR.PATCH` (`v1.0.1`) to match
the `v*.*.*` trigger the release workflow uses. Every release gets a
[Keep a Changelog](https://keepachangelog.com) entry in [CHANGELOG.md](CHANGELOG.md)
written from the subjects that landed on `main` (`git log --first-parent`). Cutting one is a documented procedure —
[`docs/RELEASE.md`](docs/RELEASE.md).

### Git hooks

Optional, and **never a substitute for `just check`** — they catch the cheap mechanical
mistakes in seconds instead of at the end of a full test run.

```bash
just hooks-install    # once, after `just init`
just hooks            # run every hook over the whole repo
just hooks-push       # just the slow tier
```

Three tiers, split by what they cost:

| Stage | Runs | Cost |
|---|---|---|
| `pre-commit` | whitespace/EOF, YAML+TOML+JSON syntax, large-file guard, `nbstripout`, `ruff --fix`, `black`, `cargo fmt`, generated-copy drift, no local paths | ~3 s |
| `prepare-commit-msg` | adds your DCO `Signed-off-by` line (see [Licence](#licence)) | instant |
| `commit-msg` | the commit convention above | instant |
| `pre-push` | `mypy`, `pytest`, `cargo clippy` | minutes |

The drift hooks are the ones worth having: they catch a `core_tools.yaml`, `LICENSE`, or
README-inventory copy that got edited on the wrong side *before* you commit it, rather than
as a confusing test failure later. Formatters run through `uv run`, so a hook uses the same
version as `just check` — a hook and the gate disagreeing is worse than either alone.

`git commit --no-verify` skips them when a hook is simply wrong. If you reach for it often,
the hook is the bug — open an issue.

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
just package-linux           # Linux: build the artifacts in the release container
just release-macos           # macOS: sign, notarize and staple the .zip and .pkg
just licenses-all            # regenerate both third-party licence reports
```

`just package-native` must run from a "Developer PowerShell for VS" on Windows. A bare
`cargo build` is fine for development, never for a release: the scripts strip the builder's
paths from the binaries. Full procedure — per OS and kind, CUDA arch range, checksums,
publishing — is in
[`docs/RELEASE.md`](docs/RELEASE.md).

## Repository layout

Five pillars plus support directories:

```text
python/core/knaif/   the knaif Python package (import path is knaif.*)
python/training/     LoRA/DPO dataset builders and training scripts
native/crates/       reusable Rust engine crates (knaif-core, -models, -llm, -skill-api)
apps/cli/            the native knaif CLI binary
skills/<name>/       self-contained skill bundles (YAML + python/ + native/ + data/ + eval/)
toolkits/<name>/     shared tools skills build on (tools.yaml + python/), e.g. toolkits/fs
contracts/           language-neutral contracts shared by both runtimes
installers/          Windows, Linux and macOS packaging
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
tool is a `Handler` (it executes) or an `Intent` (it expands into more plan steps), linked
1:1 to its `tools.yaml` entry by `name`. Every tool also declares its `effects:` (`fs.read`,
`fs.write`, `net`, `exec:<program>` or `[]`); a handler runs programs only through
`ctx.exec` and writes files only through the `fs` toolkit. Shared tools come from
`toolkits/`, listed in the skill's `skill.yaml`, never copied into the skill.

Handlers are only step one. A skill is "done" when it is also evaluated, represented in
the training mix, and — if it ships natively — ported. Corpora and the acceptance bar live
in the bundle, not centrally:

```bash
just eval-fixtures <skill>    # regenerate fixtures (idempotent) — do this first
just eval <skill>             # cheap verifier — fast routing gate, no external binaries
just eval-output-diff <skill> # executes the real tool, diffs against baseline commands
just eval-success <skill>     # honest metric — real execution + success_criteria grading
just eval-regression <skill> <current>  # gate a saved run's scoreboard against the committed snapshot
```

Verifiers run cheapest-first: `cheap` → `output_diff` → `success`. Use `cheap` while
iterating; the executing verifiers need the skill's underlying tool (e.g. `ffmpeg`) on
PATH, and the fixtures the corpus references.

**Quote `success`, not `cheap`, when reporting quality.** `cheap` only checks routing.

A skill is accepted against the written bar in its `acceptance.yaml` (aggregate floors,
required slices, safety at 100%), not against a feeling:

```bash
just eval-safety <skill> <save.json>                      # utterances that must be rejected
just eval-accept <skill> <scoreboard.json> <safety.json>  # the verdict against acceptance.yaml
```

The full ladder is *The eval ladder* in [`docs/EVAL_FRAMEWORK.md`](docs/EVAL_FRAMEWORK.md).

By default the eval suite uses mock inference. For a real model, add
`--config eval_backends.yaml --backends <name>` — or use the multi-backend recipes:

```bash
just eval-backends <skill>              # every backend in eval_backends.yaml, side-by-side
just eval-compare <skill> mock,ollama   # a named subset
just retrieval                          # retrieval quality (recall@k / MRR), model-independent
just retrieval-check                    # gate retrieval against the locked baseline
```

`just eval-lock <skill> <scoreboard.json>` **re-locks the baseline** at
`skills/<skill>/data/eval_snapshot.json` from a saved, accepted run — do that deliberately,
in its own commit, and only when adopting a measured improvement.

`evals/` is the single home for run history. Save with
`--save evals/runs/<YYYY-MM-DD>_<label>_<verifier>/` and add a row to
[`evals/INDEX.md`](evals/INDEX.md); never write to a root-level `results/` or `runs/`.
`.gitignore` keeps the durable summaries (`score.json`, `report.md`, `review_log.json`)
and drops generated media, so commit the run rather than pruning it by hand.

Schema, verifier semantics, and scoring: [`docs/EVAL_FRAMEWORK.md`](docs/EVAL_FRAMEWORK.md).
Verifying results by hand: [`docs/EVAL_VERIFICATION_SOP.md`](docs/EVAL_VERIFICATION_SOP.md).

## Conventions

- **Branches, commits, PR titles, and tags:** see [Git conventions](#git-conventions)
  above. `just hooks-install` enforces the commit format locally.
- **Don't** import a specific skill from core, hard-code skill-specific safety rules in
  core, or resolve variable references during `validate_plan` (resolution is runtime
  behaviour).
- **Notebooks:** clear outputs before committing — they carry absolute paths and execution
  metadata otherwise. The `nbstripout` hook does this for you if you installed the hooks.
  Skill-specific notebooks live with their skill under
  `skills/<name>/notebooks/`; top-level `notebooks/` is reserved for cross-skill
  experiments and authoring tools. Open one with `just notebook <path>`.
- **No local paths in committed files.** The repo is public: write paths relative to the
  repo, or `<repo>/...` / `~/...`, never your home directory or a real username. The
  `no-local-paths` hook and a test in `just check` enforce it.
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

Contributions are licensed under [Apache-2.0](LICENSE), matching the project; you keep your
copyright.

Every commit in a pull request is signed off under the
[Developer Certificate of Origin](DCO) (DCO 1.1): a statement that you wrote the change, or
otherwise have the right to submit it under the project's licence. With the git hooks installed
(`just hooks-install`) the line is added for you from your git config; without them, sign off
with `git commit -s`. Either way the commit message gets a line:

```text
Signed-off-by: Alice Example <alice@example.com>
```

The e-mail must match the commit's author; a GitHub noreply address is fine. A DCO check on the
pull request reports any commit without it. To fix one, amend it (`git commit --amend -s`), or
sign off several with `git rebase --signoff origin/main`, then force-push your branch.

Maintainers: when you squash-merge an outside pull request, keep its `Signed-off-by` lines in the
squash commit message, so the sign-off survives on `main`.

If you add a dependency, run `just licenses-all` and commit the regenerated reports — both trees
are **permissive-only**, and the Python generator fails on copyleft.
