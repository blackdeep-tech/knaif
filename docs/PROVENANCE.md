# Provenance

Where every non-code asset in this repository came from, and under what terms it
is redistributed. Code dependencies are covered separately by the generated
license reports (see [Code dependencies](#code-dependencies) below); this file
exists because a dependency report does **not** cover models, data, or media.

Legal attribution notices live in [`NOTICE`](../NOTICE). This file is the
engineering record behind them.

---

## Models

knaif does **not** bundle model weights. The runtime downloads them on first run
from the URLs pinned in [`contracts/models/model-manifest.yaml`](../contracts/models/model-manifest.yaml),
each pinned to a commit SHA and verified against a recorded SHA-256.

| Released model | Base model | Base license | Fine-tune license |
|---|---|---|---|
| `knaif-qwen3-4b-v1` | [`Qwen/Qwen3-4B`](https://huggingface.co/Qwen/Qwen3-4B) | Apache-2.0 | Apache-2.0 |
| `knaif-qwen3-1.7b-v1` | [`Qwen/Qwen3-1.7B`](https://huggingface.co/Qwen/Qwen3-1.7B) | Apache-2.0 | Apache-2.0 |

Both are **derivative works** of the Qwen3 family by Alibaba Cloud, used under
Apache-2.0. Apache-2.0 permits redistribution of derivatives under the same
license, which is what knaif does. The manifest records this machine-readably via
each entry's `base_model` and `base_model_license` fields, so the provenance
travels inside the artifact rather than living only in documentation.

Fine-tuning method, hyperparameters, and measured outcomes are documented in
[`FINE_TUNING.md`](FINE_TUNING.md); the internal fine-tune cycle that produced
each public release is recorded in the manifest's `training_run` field.
[`MODELS.md`](MODELS.md) is the engineering overview of the same artifacts —
hosting, selection rationale, and the publishing procedure.

## Training and preference data

| Corpus | Location | Origin |
|---|---|---|
| Training | `skills/<name>/data/train.jsonl` | Authored for this project |
| Evaluation | `skills/<name>/data/eval.jsonl` | Authored for this project |
| Safety | `skills/<name>/data/safety_test.jsonl` | Authored for this project |
| Acceptance bar | `skills/<name>/data/eval_snapshot.json` | Generated from this project's eval runs |

All corpora were written for knaif — none is derived from an external dataset —
and are covered by the project license. Rows are natural-language utterances
paired with expected plans; they reference **synthetic filenames only**
(`clip.mp4`, `broll.mov`, `report.pdf`, …) and contain no personal, customer, or
third-party content. The authoring workflow is documented in
[`CORPUS_AUTHORING_STEPS.md`](CORPUS_AUTHORING_STEPS.md) and
[`TRAINING_DATA_GENERATION.md`](TRAINING_DATA_GENERATION.md).

## Media and document fixtures

**No third-party media is committed to this repository.** Every fixture is
generated procedurally at eval time into `sandbox/fixtures/<skill>/`, which is
untracked:

- **Video and audio** (`skills/ffmpeg/eval/fixtures.py`) — synthesized by FFmpeg's
  built-in `lavfi` sources (`testsrc`, `testsrc2`, `sine`). No recorded footage
  and no sampled audio is involved, so nothing carries a third-party claim.
- **Documents and images** (`skills/documents/eval/fixtures.py`) — generated with
  `reportlab` and `pikepdf` (PDF, including the encrypted and scanned variants),
  `python-docx` / `openpyxl` / `python-pptx` (Office), and Pillow (raster).
  Content is synthetic placeholder text. These generators are **dev-time only** —
  they are not runtime dependencies of the distributed wheel, which is why they
  do not appear in `THIRD-PARTY-PYTHON.txt`.

Regenerate with `just eval-fixtures <skill>`.

## Site and documentation assets

| Asset | Locations | Notes |
|---|---|---|
| `logo.png` | `media/`, `site/docs/assets/` | Identical file, committed twice |
| `knaif-logo-rect.svg` | `media/`, `site/docs/assets/` | Identical file, committed twice; used by the README header |
| `execution-pipeline.svg` | `site/docs/assets/` | Diagram of the pipeline in `ARCHITECTURE.md` |

**No fonts are bundled** — no `.woff`/`.woff2`/`.ttf`/`.otf` file is tracked, and
no SVG embeds an `@font-face` or references a `font-family`. The SVGs carry no
editor metadata (no `dc:creator`, no Inkscape/Illustrator blocks).

> **Owner confirmation required before publication:** these marks are assumed to
> be original work commissioned or created for the project. If any was derived
> from a stock or licensed source, record that here and add the required
> attribution to `NOTICE`. This is the one provenance claim in this file that was
> not verified mechanically.

The duplicated logo files are a known redundancy (`media/` for GitHub rendering,
`site/docs/assets/` for the MkDocs build, which can only reference files under its
own `docs_dir`). De-duplicating them is a cleanup task, not a provenance issue.

## Code dependencies

Generated reports, regenerated with `just licenses-all`:

| Report | Covers | Generator |
|---|---|---|
| [`THIRD-PARTY-RUST.txt`](../installers/licenses/THIRD-PARTY-RUST.txt) | Rust crate tree | `cargo-about`, config in [`about.toml`](../about.toml) |
| [`THIRD-PARTY-PYTHON.txt`](../installers/licenses/THIRD-PARTY-PYTHON.txt) | Runtime closure of the distributed wheel | [`scripts/gen_python_licenses.py`](../scripts/gen_python_licenses.py) |

Both trees are **permissive-only** — no GPL, AGPL, LGPL, or SSPL. `about.toml`
enforces this for Rust via its `accepted` allowlist; the Python generator exits
non-zero if a copyleft license appears in the runtime closure.

Development and test tooling (pytest, black, ruff, mypy) is excluded from the
Python report because it is never distributed. The optional `llama` extra is
likewise excluded; llama.cpp's own MIT license ships as
[`llama.cpp-LICENSE.txt`](../installers/licenses/llama.cpp-LICENSE.txt).

**These reports pin dependency versions, so they go stale.** Regenerate and
commit them as part of cutting a release — a report naming the previous version
misdescribes what the artifact actually ships.

## External binaries (not bundled)

Skills that shell out to external tools do not ship them. FFmpeg in particular is
**not** bundled or linked; the user installs it themselves and it remains under
its own license (LGPL or GPL depending on their build). Per-skill requirements
are listed in each `skills/<name>/SPEC.md`.
