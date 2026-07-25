# Training subsystem — an optional, documented fine-tuning path for skill authors

**Status:** Planning · **Created:** 2026-06-29 · **Completed:** —
**Owner:** eval · **Ref:** docs/TRAINING_DATA_GENERATION.md, docs/plans/2026-06-27-fine-tuning.md

> **Kept 2026-07-23** (S7 decision — **unexecuted roadmap**, the explicit keep column). Not
> "cited from source": the keep tier listed it that way, but its only three referrers are
> `.md`. It survives on being live future work, not on citations.
>
> **Re-verified 2026-07-23 — none of the 10 tasks is built**, but the ad-hoc path they were
> meant to productionize now exists, so read the deltas below before starting:
>
> | Task | Status against the tree |
> |---|---|
> | 1 `training.yaml` + `config.py` | **Unbuilt.** No config file; every script takes CLI flags. |
> | 2 `train` extra + wheel exclusion | **Obsolete as written, goal already met.** The restructure put `python/training/` *outside* the package root (`python/core/pyproject.toml`, `include = ["knaif*"]`), so no `exclude` entry is needed — the layout enforces it. Core imports zero training modules (verified). The invariant is now stated in [FINE_TUNING.md §2](../FINE_TUNING.md). |
> | 3 `just train-setup` | **Unbuilt as a recipe**, but its output exists: `python/training/requirements-train.lock` is the proven, frozen version set. |
> | 4 `just train-doctor` | **Unbuilt as a recipe**; `python/training/phase0_smoke.py` is the manual equivalent. |
> | 5–7 `train-data` / `-run` / `-export` | **Unbuilt as recipes**; `build_dataset.py`, `train_lora.py`, `merge_to_hf.py` are the working scripts. |
> | 8 `just train-eval` | **Unbuilt.** The trap it cites moved to [FINE_TUNING.md §4](../FINE_TUNING.md) (the snapshot-gate rule). |
> | 9 `docs/TRAINING.md` | **Superseded in substance.** `docs/FINE_TUNING.md` is the canonical execution contract (§2 environment, §3 the pipeline, §6 promotion). Do **not** create a second doc — fold anything missing into it. The one genuinely unwritten part is the **third-party-author flow**, which is this plan's whole reason for existing. |
> | 10 tests / gitignore / CI | **Unbuilt.** |
>
> **So the remaining value is narrower than the plan reads:** the pipeline works and is
> documented for *the maintainer*. What is missing is the packaging of it for **someone
> else's skill** — a config file instead of flag-strings, `just` recipes instead of a
> remembered command order, and an author-facing path. Scope any revival to that gap;
> re-deriving §3 as `docs/TRAINING.md` would be duplicate work.
>
> Target layout below is pre-restructure (`training/` at the root); the tree is
> `python/training/`, and it is a script directory, not a package (no `__init__.py`).
>
> **Why this exists.** The repo documents the *data* side of fine-tuning
> (`docs/TRAINING_DATA_GENERATION.md`) and a one-off *experiment*
> ([2026-06-27-fine-tuning.md](2026-06-27-fine-tuning.md)), but the *execution* side —
> install, setup, run, export — has no home, no recipes, and no optional-install story.
> `knaif` is a framework where third-party authors bring their own skills and may want to
> fine-tune the shared model. Training must therefore be a **well-organized, documented,
> strictly optional** subsystem — installed and run only by those who train, invisible to
> everyone else.
>
> **Not a prerequisite of the fine-tuning experiment — it is the reverse.** The
> [fine-tuning experiment](2026-06-27-fine-tuning.md) is the spike: it runs Phase 0–8 with
> direct commands and proves the Blackwell/sm_120 + Unsloth stack actually works. **This
> subsystem productionizes that proven path** — each recipe below captures the working
> commands the experiment established, not the other way around. **Build this only after the
> experiment's Phase 0–7 have succeeded manually**, so every recipe wraps a command that has
> been seen to run, not a guess about an unproven stack.

**Goal:** A `training/` subsystem (config + thin CLI, wrapped by `just train-*` recipes)
that takes a skill author from "I have a `train.jsonl`" to "I have a tuned, quantized,
regression-gated GGUF backend" — physically isolated from the core package so non-trainers
never install torch or see any of it. Once it exists, re-running a fine-tune (a new skill,
a refreshed corpus) becomes "fill in `training.yaml` and run the recipes."

---

## Decisions locked

| Decision | Choice | Why |
|---|---|---|
| **Trainer shape** | `training/` package + thin `python -m training` CLI, driven by `training.yaml` | Reproducible, testable, matches how `knaif.evalsuite` is structured; best path for 3rd-party reuse |
| **Isolation** | Non-shipped top-level `training/` tree, excluded from the wheel like `skills*` | Core SDK/CLI users never get torch or training code in the wheel; `knaif/` core never imports it |
| **Environment** | Dedicated `.venv-train` on **Python 3.12**, separate from the 3.14 project venv | torch has no 3.14 wheels; the two venvs coexist — training only reads `train.jsonl` and writes `models/*.gguf`, no shared runtime |
| **Dependency wiring** | `train` extra for non-torch deps; torch installed via the `cu128` index in `train-setup` | torch for Blackwell (sm_120) needs a custom index URL that can't be a normal PyPI extra; mirrors the existing `install-cuda` precedent in the justfile |
| **Config** | Root `training.yaml`, sibling to `models.yaml` / `eval_backends.yaml` | One diffable, reproducible file declaring bases, LoRA hyperparams, the skill union, and output naming |
| **Skill-author contract** | A skill adds **nothing** beyond its `data/train.jsonl` | Training is skill-agnostic and operates on the union; the only thing an author produces is good training rows |

### Inherited, do not re-litigate

- **Training is optional.** The untuned model already clears its targets
  (`docs/plans/2026-06-27-fine-tuning.md`). This subsystem measures/produces the *marginal*
  gain; it is never a prerequisite for shipping a skill or the framework.
- **One shared model, union training.** Fine-tuning is on the union of every active skill's
  `train.jsonl`, never one fine-tune per skill (catastrophic forgetting) — inherited from
  `docs/TRAINING_DATA_GENERATION.md` and [cross-skill-eval-monitoring](2026-06-25-cross-skill-eval-monitoring.md).
- **`train.jsonl` ≠ `eval.jsonl`.** Training input vs benchmark; a routing change must land
  in both or they drift (`docs/TRAINING_DATA_GENERATION.md`).
- **Blackwell needs cu128.** sm_120 kernels first ship in the CUDA 12.8 torch wheels; older
  cu124 builds throw `no kernel image is available` at first launch.

---

## Target layout

```text
training/                         # non-shipped, excluded from the wheel
  __init__.py
  __main__.py                     # `python -m training` thin CLI
  setup.py / doctor.py            # env bootstrap + sm_120 smoke test
  data.py                         # assemble + validate the union train.jsonl
  run.py                          # LoRA fine-tune (Unsloth, bf16)
  export.py                       # merge -> convert_hf_to_gguf -> llama-quantize -> stanzas
  config.py                       # load/validate training.yaml
  output/<run>/                   # adapters + run metadata (gitignored)
  tests/test_training.py          # config parse, data union, dry-run paths (no GPU)
training.yaml                     # root config: bases, hyperparams, skill union, naming
docs/TRAINING.md                  # execution contract (counterpart to data-gen contract)
```

`models/*.gguf` remains the home for produced backends (the eval harness reads GGUFs);
ft builds use the locked names `qwen3-<sz>-ft-{f16,q4}.gguf`.

---

## Tasks

### - [ ] 1. `training.yaml` schema + `training/config.py`

Define and load the one config file. Fields: `bases` (HF repo ids + short labels),
`lora` (r, alpha, dropout, target modules, lr, epochs, batch/accumulation, grad
checkpointing), `skills` (the union list — default `[ffmpeg, documents]`), `output`
(adapter dir, GGUF naming template, quant type `Q4_K_M`). Validate on load with clear
errors. Unit-test parse + defaults (no GPU needed).

### - [ ] 2. Dependency wiring — `train` extra + isolation

- Add a `train` optional-dependencies group to `pyproject.toml`: `unsloth`,
  `unsloth_zoo`, `transformers`, `peft`, `trl`, `accelerate`, `datasets`,
  `huggingface_hub`. (No torch here — it needs the cu128 index.)
- Add `training*` to `[tool.setuptools.packages.find] exclude` so it never enters the wheel.
- Confirm `knaif/` core has no import path into `training/` (lint/grep check).

### - [ ] 3. `just train-setup` — bootstrap the isolated env (platform-aware)

Mirror the `install-cuda` recipe pattern. On Linux/WSL:

```bash
uv venv --python 3.12 .venv-train
# Blackwell (sm_120): torch from the cu128 index — NOT plain PyPI
VIRTUAL_ENV=.venv-train uv pip install torch --index-url https://download.pytorch.org/whl/cu128
VIRTUAL_ENV=.venv-train uv pip install ".[train]"
```

Pin exact versions once Task 4 (doctor) confirms a working combination. Document the
Windows fallback (`transformers+peft+trl`, no Unsloth) in `docs/TRAINING.md` per the
fine-tuning plan's fallback ladder.

### - [ ] 4. `just train-doctor` — the Phase-0 gate (`gpu-check` analogue)

`python -m training doctor`: assert `torch.cuda.is_available()`, print device name +
capability (expect `(12, 0)` on the 5080), then run **one** bf16 LoRA step on
`Qwen/Qwen3-1.7B` (`load_in_4bit=False`) and confirm it lands on GPU. Exit non-zero on
any failure. **This captures the Phase-0 gate of [2026-06-27-fine-tuning.md](2026-06-27-fine-tuning.md)
as a repeatable recipe** — the experiment proves the step runs manually first; this recipe
preserves it. Watch for the sm_120 failure mode: imports succeed, first kernel launch
throws `no kernel image is available`.

### - [ ] 5. `just train-data` — assemble + validate the union

`python -m training data`: read each skill's `data/train.jsonl` for the configured union,
validate every row against that skill's `tools.yaml` (reuse `knaif` validation — invoke
out-of-process or vendor the check, since core lives in the 3.14 venv), report per-skill
counts + balance, and write a single `training/output/<run>/union.jsonl` with a content
hash. Refuse on invalid tools/args. Holds the line that `train.jsonl` must stay valid
per `docs/TRAINING_DATA_GENERATION.md`.

### - [ ] 6. `just train-run` — LoRA fine-tune

`python -m training run`: Unsloth, bf16 base, `load_in_4bit=False`, gradient checkpointing,
hyperparameters from `training.yaml`. Train the union onto each configured base → one
adapter per base. Write run metadata (hyperparams, base-weight sha, dataset hash, package
versions) next to each adapter for reproducibility. Keep the Qwen3 instruct template
consistent with the eval prompt format.

### - [ ] 7. `just train-export` — merge → quantize → stanzas

`python -m training export`: per base, merge the adapter into bf16 HF, then
`convert_hf_to_gguf.py --outtype f16` → `llama-quantize ... Q4_K_M`, emitting
`models/qwen3-<sz>-ft-{f16,q4}.gguf`. Print the ready-to-paste `eval_backends.yaml`
stanzas (same options as the base stanzas). Document the llama.cpp tooling requirement
(separate from the trainer; may need the CUDA toolkit only if built from source).

### - [ ] 8. `just train-eval` — wire the existing regression gate

Thin wrapper over the eval suite that already exists: union sweep at `cheap` + `success`
into one save folder, then `regression --all-skills` for the catastrophic-forgetting gate.
Reuse, do not reimplement — and respect the per-skill snapshot-verifier trap, now a
methodology rule in [FINE_TUNING.md §4](../FINE_TUNING.md) — the snapshot-gate rule (ffmpeg's snapshot is
`cheap`, documents' is `success`; a `success`-only run silently skips ffmpeg).

### - [ ] 9. `docs/TRAINING.md` + Documentation Map

Author the execution contract: System Requirements (Python 3.12, cu128 torch, sm_120,
disk), install/setup, the full `train-setup → doctor → data → run → export → eval` flow,
the **"Fine-tuning your skill"** section for third-party authors (minimal path: author
`train.jsonl` per `docs/TRAINING_DATA_GENERATION.md`, then the recipes), artifact/naming
conventions, and the Windows fallback. Add `docs/TRAINING.md` to the Documentation Map in
`CLAUDE.md` next to `docs/TRAINING_DATA_GENERATION.md`.

### - [ ] 10. Tests + `.gitignore` + CI posture

Unit-test config parse, the data-union assembler, and CLI dry-run paths **without a GPU**
(the GPU steps are gated behind `train-doctor`, not CI). Gitignore `.venv-train/` and
`training/output/`. Do not add training to `just check` (CI has no GPU and no torch).

---

**Done when:** a clean checkout can run `just train-setup && just train-doctor` to clear the
Phase-0 gate; `just train-data → train-run → train-export → train-eval` produces a tuned,
quantized, regression-gated GGUF from the skill union; `docs/TRAINING.md` documents the
whole path including the third-party-author flow; and a non-trainer's `uv pip install .`
pulls in **none** of it.
