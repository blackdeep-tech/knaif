# `contracts/runtime/` — language-neutral runtime contracts

No-code runtime contracts shared by every runtime (Python now, Rust later). This is the
**canonical source of truth** for these files — apps read them here without reaching into
the Python package.

- **`core_tools.yaml`** — metadata for the core control tools (clarify / reject / done /
  wait_for_confirmation), merged into every skill's registry by name.
- **`steps.yaml`** — metadata for the shared step library (`resolve_inputs`), and the
  reserved home for the future capability stdlib. Reference-only: not loaded by the Python
  runtime today (skills carry the `resolve_inputs` entry in their own `tools.yaml`).
- **`generation.yaml`** — the generation settings that decide what the model emits
  (`max_tokens`, `n_ctx`, `temperature`, `json_mode`, `thinking_enabled`) for the promoted
  models. Enforced by comparison, not by copying — see below.

## Packaging note

`core_tools.yaml` is **import-critical** for the Python framework (loaded at
`knaif.core_tools` import), so a byte-identical copy ships **inside the `knaif` wheel**
next to the module. The loader (`knaif.core_tools._resolve_runtime_yaml`) resolves the
packaged copy first, else walks up to this directory in a checkout.

Edit the canonical file here, then run **`just sync-runtime`** to refresh the packaged
copy. A drift-guard test (`test_runtime_data.py`) fails if the two diverge, so CI catches
an unsynced edit. `steps.yaml` is not loaded by Python and is **not** shipped in the wheel.

## Two kinds of guard

`core_tools.yaml` is **synced**: its consumer is a loader, so a copy is safe and
`just sync-runtime` writes it.

`generation.yaml` is **compared**. Its consumers are `models.yaml`, `eval_backends.yaml`
and two Rust constants — hand-annotated files and code, where a generator would flatten
commentary worth more than the duplication costs. So nothing is copied from it; two guards
read it and assert:

- `python/core/tests/test_generation_settings.py` — the promoted stanzas in `models.yaml`
  and `eval_backends.yaml`, plus the Python orchestrator's own defaults.
- `native/crates/knaif-llm/tests/generation.rs` — `knaif_llm::MAX_TOKENS` / `N_CTX`.

Change a value here deliberately and both guards fail, naming the files to update.

See [`docs/plans/2026-06-17-monorepo-dual-runtime.md`](../../docs/plans/2026-06-17-monorepo-dual-runtime.md) (Phase 3, *Skill Distribution Model*).
