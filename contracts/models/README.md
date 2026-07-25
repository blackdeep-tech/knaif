# `contracts/models/` — language-neutral model contract

Home of the **no-code, cross-language model manifest** (`model-manifest.yaml`): model
names, download URLs, SHA-256 checksums, sizes, license/source notes, per-skill
compatibility, and surface recommendations (desktop/native CLI `knaif-qwen3-4b-v1`; mobile
`knaif-qwen3-1.7b-v1`). Read by the shared `knaif-models` `ModelStore` and by any UI's
model-management screen. Manifest keys are **public release** versions (v1, v2, …); the
originating fine-tune cycle is recorded per entry in `training_run`/`source`.

`contracts/` holds **no-code, language-neutral contracts only** — skill bundles live at root
`skills/`, not here.

See [`docs/MODELS.md`](../../docs/MODELS.md).
