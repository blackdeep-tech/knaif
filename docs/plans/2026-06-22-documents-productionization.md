# Documents Skill — Productionization (implemented → production-ready)

**Status:** Done · **Created:** 2026-06-22 · **Completed:** 2026-06-27
**Owner:** documents · **Ref:** [documents-skill](2026-06-21-documents-skill.md)

> **Status note:** The documents skill is functionally implemented across all build
> phases (31/31 checkboxes, green gate, OCR + eval scaffolding landed) and has been
> taken through the full maturation path below: hands-on testing, corpus growth,
> baseline locking, prompt iteration, step improvements, and coverage gaps. The one
> remaining open box (per-page OCR for mixed text+scanned PDFs) is a **conditional,
> intentionally-parked** item — not outstanding work. Phase F (fine-tuning) was
> deferred to a separate cross-skill plan.
>
> **Kept 2026-07-22** (S7 decision) — the record of how a *second* skill was matured, and
> the only place the ffmpeg-shaped assumptions it broke were written down. The Phase-B
> runner fix is verified live in
> [`runner.py`](../../python/core/knaif/evalsuite/runner.py) (plan JSON as the artifact
> fallback when a skill defines `run_artifact`).
>
> **Three findings extracted, because a third skill author would otherwise rediscover them:**
> - **Plan-shaped vs command-shaped skills** — no rendered command means
>   `_extract_artifact` returns `None` and `output_exists` fails *silently* on every
>   destructive row. Rule: implement `Skill.run_artifact` and grade with `success`, not
>   `honest`. → [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md#verifier-modes).
> - **Corpus authoring forks by skill shape** — criteria-graded skills leave
>   `baseline: null`, use the verifier as the oracle, and need *less* human review;
>   `eval-seed` / `baseline-authoring` are command-shaped **by design**. →
>   [CORPUS_AUTHORING_STEPS.md](../CORPUS_AUTHORING_STEPS.md).
> - **Prefer the narrowest layer** — Phase C fixed every routing failure in
>   `tools.yaml`/handlers without touching the shared system prompt, because a prompt edit
>   moves the whole skill. → [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md).
>
> The Phase-C **negative result** stays here as the worked example: disambiguating
> lock/unlock by description cost qwen 1.00 → 0.987 — *below* the 0.02 gate, caught only by
> the per-row diff, and reverted.
>
> Paths repointed (`eval_results/` → `evals/`) and three bare memory-key references
> replaced with the reasoning they stood for.

**Goal:** Take the documents skill from "it runs" to "production-ready" — the
maturation path (hands-on testing, corpus growth, baseline locking, prompt/step
iteration, coverage gaps), mirroring how **ffmpeg** (the reference skill) got there.
Calibration snapshot at the start of this plan:

| Corpus | ffmpeg (mature) | documents (now) |
|---|---|---|
| `data/eval.jsonl` | 297 rows | 20 |
| `data/train.jsonl` | 33 | 5 |
| `data/safety_test.jsonl` | 9 | 2 |

ffmpeg's `evals/` history (~12 dated runs: `pre-geometry → post-geometry →
prompt-opt → chain-outputs-rebuilt`, on qwen3-4b **and** gemma3-4b) is the shape
of the loop below.

## Divergence from ffmpeg's path (decide in Phase A)

ffmpeg's baseline-authoring notebook is built around one gold **`baseline.command`**
string per row (run the command, diff the output). The documents corpus instead
uses **`success_criteria`** (`format`/`pages`/`has_text_layer`/substring) graded
deterministically by the `honest` verifier, and rows carry `baseline: null`. So
documents needs *less* per-row human baseline authoring — the honest verifier is
the oracle. `just eval-seed` / `just baseline-authoring` as written are
ffmpeg-shaped.

- [x] **Decision (2026-06-24):** keep documents purely `success_criteria`-graded;
  `baseline_authoring.ipynb` (ffmpeg baseline-command shaped) does **not** apply.
  Human validation is via the new read-only `notebooks/documents_corpus_review.ipynb`
  (routes + really-executes each row, grades `success_criteria` with `honest()`,
  green/red table, disk-cached). `evals/baselines/` reserved for locked
  whole-suite snapshots only.

## Human-in-the-loop checkpoints (not bypassable)

Per the corpus-authoring rules in [CORPUS_AUTHORING_STEPS.md](../CORPUS_AUTHORING_STEPS.md):
- Corpus batches are **owner-reviewed** before merge (I draft, you review, I apply
  agreed fixes immediately without re-asking).
- `validated_by: "human"` is set **only** by a person in the notebook; the agent
  leaves authored baselines `null`.
- **Prompt changes** are owner-gated (they move the whole skill).

---

## Phase T — Hands-on testing first (CLI + notebook)

Exercise the skill manually before investing in corpus scale — catch obvious
routing/UX/prompt breakage cheaply.

### T1 — CLI smoke pass (`knaif-cli`)

Entry point: `knaif-cli = knaif.app:main` (no `__main__`; use `uv run knaif-cli`).

```bash
# mock backend — pure plan routing, no model, no side effects
uv run knaif-cli run documents "inspect report.pdf" -b mock -d -v

# real local model, dry-run preview (default --show-plan on)
uv run knaif-cli run documents "pull pages 1-3 out of report.pdf" -b auto -m qwen3-4b -d

# execute for real against a sandbox fixture (requires confirm unless -y)
uv run knaif-cli run documents "stamp DRAFT across every page of report.pdf" -m qwen3-4b
```

- [x] Generate the fixture set once: `just eval-fixtures documents` (txt/md/PDF/scanned-PDF/docx/pptx/xlsx/png into `sandbox/fixtures/`).
- [x] One representative utterance per tool (all 14 public tools + `compress_pdf` and `ocr_document` Intents) routed through a real local model. CLI entry validated (`uv run knaif-cli run documents "inspect report.pdf" -b mock -d` → `inspect_document`). Bulk routing run via a single-model-load harness across qwen3-4b, qwen3-1.7b (the CLI reloads the 2.5 GB GGUF per call, so 19 cold starts is wasteful — load once instead).
- [x] Verified: correct tool chosen + args populated on qwen3-4b for all 17 tools; deterministic `reject` gate fires at 0 ms (pre-model).
- [x] Logged failure modes (below) — feeds Phase A corpus rows + Phase C prompt fixes. **Aggregate before fixing** — qwen and gemma have opposite failure modes, so fixing one row at a time trades one model's gain for the other's loss.

#### T1 results (2026-06-22)

| Model | Routing | Notes |
|---|---|---|
| **qwen3-4b** (recommended) | **18/19 (95%)** | all 17 doc tools correct (~300 ms/infer, GPU offload); only vague-utterance miss |
| qwen3-1.7b | 16/19 (84%) | confirms 4B is the right floor |

**Findings + fixes (all resolved 2026-06-22; re-verified 19/19 on both qwen3-4b and gemma3-4b):**
1. **Vague-utterance under-clarification (was both models).** "do something with a file" →
   `inspect_document {input: "file"}` instead of `clarify`. **Fixed:** added a clarify rule
   + example to `prompt.yaml` ("if no clear action/target, emit `clarify`; do not guess a
   tool or invent a filename"). Re-run: both models now route this to `clarify`. (Phase A
   will still add vague/no-target corpus rows to lock it in.)
2. **Small-model tool hallucination (was qwen3-1.7b only).** Invented a non-existent
   `stamp` tool. **Resolved two ways:** `validate_plan` already rejects unknown tools
   ([planner.py](../../python/core/knaif/planner.py)), and qwen3-1.7b is now excluded from
   `models.yaml` (see #4). 4B/gemma never hallucinated.
3. **CLI UX — dry-run approval prompt.** `--dry-run` showed `Proceed? [Y/n]` even though
   no files change. **Fixed:** `app.py` now skips the approval gate when `dry_run` (dry-run
   makes no changes, so the gate was pointless and blocked non-interactive use).
4. **Registry divergence + model set.** `gemma3-4b` was only in `eval_backends.yaml`, not
   `models.yaml`. **Fixed:** `models.yaml` now mirrors the eval suite's active set
   (**qwen3-4b + gemma3-4b**); qwen3-1.7b, phi4-mini, and mistral-ollama removed. Both
   models load via the CLI and the qwen-vs-gemma comparison is now reachable for Phase C.

### T2 — Interactive notebook tester

- [x] Create `notebooks/documents_skill_tester.ipynb` mirroring
  `notebooks/ffmpeg_skill_tester.ipynb` (model-config cell + interactive tester cell +
  debug-trace cell + prompt-examples table). Reuses `model_selector.ModelSelector` and
  `debug_trace.show_debug` unchanged; a new `helpers/documents_tester_widget.py`
  (`DocumentsNLTester`) renders documents' **structured** results (paths, page records,
  matches) instead of ffmpeg command strings — the ffmpeg widget is command-line-shaped
  and not reusable as-is. `MODEL_CONFIGS` = qwen3-4b + gemma3-4b (matches `models.yaml`).
- [x] Engine verified headlessly against a real qwen3-4b load (route extract/split/clarify,
  full `_debug_state` populated, real extract execution returns text); notebook verified
  end-to-end via `nbconvert --execute` (caught + fixed a `ModelSelector.widget` property
  bug). Interactive multi-utterance exploration via the notebook is now an owner activity.

**Verify (Phase T):** ✅ every tool reachable by a natural utterance on the recommended
model (T1: 19/19 on qwen3-4b and gemma3-4b after the clarify fix); no crashes; the
notebook runs clean under nbconvert. Output: the T1 findings list (above) feeds Phases A & C.

---

## Phase A — Corpus to scale (prerequisite for measurement)

- [x] Grow `data/eval.jsonl` 20 → 86 rows, balanced across all 15 tools + both
  Intents, with `clarify` / `reject` / multi-step. Authored in owner-reviewed
  batches 1–6 (inspect/text, page-ops, assembly, security/convert, heavy intents,
  clarify/reject/multi-step).
- [x] Expand `data/safety_test.jsonl` to 9 rows; adversarial destructive phrasings,
  backed by a hardened `unsafe_phrases` list (0 false positives, verified).
- [x] Each new row carries `expected_outcome`, `expected_tool`, `tags`, and
  `success_criteria`; `baseline: null`.
- [x] JSONL validity covered by `test_eval_data.py` (shape + fixture allowlist).

**Gate:** ✅ PASS (2026-06-24) — every tool ≥5 rows; clarify(7)/reject(3)/multi-step(2)
represented; safety_test 9. Full review on qwen3-4b: 77/78 of the gradable plan rows
(only `041` "move page 3 to front" red — relative-reorder arg comprehension, a Phase D
clarify-gate item).

> **Phase B blocker found:** the eval suite's `honest`/`success` path cannot grade
> documents `output_exists` — `run_corpus` executes dry-run and `_extract_artifact`
> is ffmpeg-command-shaped, so no real artifact is materialized for documents. The
> review notebook works around it (real execution). **Phase B needs a runner fix**:
> set `output.artifact = json.dumps(plan)` for plan-based `run_artifact` skills, then
> register/run a `success` verifier. Until then, an `honest` baseline understates
> destructive rows by ~0.5 each.

## Phase B — Lock the first honest baseline

- [x] **Blocker resolved (2026-06-24):** the eval runner couldn't materialize a
  documents artifact (ffmpeg-shaped `_extract_artifact` → `artifact=None`), so
  `honest` graded a dry-run dict and `output_exists` always failed for destructive
  tools. Fixed: runner falls back to `artifact = json.dumps(plan)` for plan-based
  `run_artifact` skills; `documents.run_artifact` copies plan-referenced inputs into
  the working dir (literal filenames + multi-input merges resolve); added a
  documents `success` verifier that grades the real file. Use **`success`** (not
  `honest`) for documents — honest can't grade `output_exists`.
- [x] Ran `success` across **both** backends, saved + promoted:
  ```bash
  uv run -m knaif.evalsuite run --skill documents --config eval_backends.yaml \
    --backends qwen3-4b,gemma3-4b --verifier success \
    --save evals/runs/2026-06-24_baseline_success
  ```
- [x] `INDEX.md` row added; promoted to
  `baselines/2026-06-24_documents-baseline_success` (local; `evals/**` is gitignored except an allowlist).

**Gate:** ✅ PASS — success run completed both backends; baseline locked + indexed.
qwen3-4b outcome **0.977** / knaif **1.00** / tool 0.977; gemma3-4b **0.907** / **0.981** / 0.930.

## Phase C — Routing / arg iteration (2026-06-24)

Driven by the read-only review notebook (real-execution grading), not `prompt.yaml`
— the failures were per-tool routing/args, fixed at the `tools.yaml`/handler layer
without touching the shared system prompt. Each change re-checked against the
committed snapshot (`evalsuite regression`).

- [x] **qwen3-4b — fixed (review-driven):** enum aliases + case coercion
  (markdown→md, bottom→bottom-center), `reverse`/`all`/`last`/`first`/`N-` page
  grammar, keyword enrichment for retrieval misses (reorder/merge/watermark/
  convert/inspect), extract-page→split_pdf + split↔remove disambiguation, reorder
  padded-order salvage. Net **59→86 / 86** gradable rows; **knaif 1.00**.
- [x] **gemma3-4b ① — fixed (coercible, deterministic):** arg-key alias
  `split_pdf {pages→ranges}` + int→str coercion. **outcome 0.907→0.953**, qwen
  untouched (regression check clean). Skill-agnostic mechanism in `normalize_plan`.
- [x] **gemma3-4b ② — NOT fixable here (→ Phase F):** the residual gemma reds are
  **selection / mis-comprehension**, not retrieval. `061` (lock→unlock) can't be
  description-disambiguated without regressing qwen `065` (decrypt→protect) and
  gemma `086` (open-protected→protect) — the filename "sample-**protected**" and
  "decrypt"/"open with password" all cross-pull. Attempted, caught a sub-threshold
  qwen knaif drop (1.00→0.987) via the per-row diff, **reverted**. `014`
  (number→clarify/ocr) is non-deterministic terseness mis-comprehension. These need
  **fine-tune (Phase F)** — the two backends fail differently, and description edits that help one pull the other the wrong way.

**Gate:** ✅ qwen 86/86 gradable (knaif 1.00); no qwen regression vs snapshot;
changes were `tools.yaml`/handler only (system prompt untouched).

## Phase D — Step / handler improvements

- [x] **Arg coercion done** (folded into Phase C): enum aliases, page grammar
  (`all`/`last`/`first`/`N-`/`reverse`), arg-key aliases, int→str, reorder salvage.
- [x] **Clarify-gate for missing required / grounded args (done 2026-06-24).**
  `required_args_clarify()` runs before `validate_plan` on the NL path: a plan
  omitting a required arg (or a tool with `any_of_args` and none present) now emits
  `clarify` instead of erroring. Fires only on absent args → zero qwen impact (qwen
  always supplies them); converts gemma's omit-required errors into clarifies.
  Fixed gemma `015`/`016` (no password → clarify). `010` enriched to the happy path
  (bare "rotate" now clarifies in production, covered by a unit test).
  **gemma outcome 0.953→0.988** (= qwen); qwen unchanged, snapshot guard still valid.
- [x] watermark/page-number positioning tuning; convert/OCR edge handling — **no
  further tuning needed**. The Phase C positioning aliases (`bottom`→`bottom-center`,
  etc.) were sufficient: qwen grades 1.0 on watermark (5/5), page_numbers (1/1),
  convert (5/5) and ocr (5/5) in the locked snapshot. No edge gap surfaced.
- [x] Re-baseline after each substantive change (new dated run + INDEX row) —
  `runs/2026-06-24_phase-e-rebaseline_success` (qwen 0.988 / knaif 1.00 / tool 0.977,
  identical to `eval_snapshot.json`; `regression --skill documents` exits 0).
  INDEX row added. Phase E being test-only, no model-facing change occurred.

## Phase E — Coverage gaps (from the build-plan audit) — DONE (2026-06-24)

Coverage added in `skills/documents/python/tests/test_documents_coverage.py`. Live
tests skip cleanly when the binary is absent, so the suite and the committed eval
snapshot stay portable on a binary-less CI box.

- [x] Real `soffice` (Office→PDF) and `gs` (lossy compress) runs on a machine that
  has them. `test_convert_office_to_pdf_live` (docx/pptx/xlsx) and
  `test_run_compress_ghostscript_live` exercise the real subprocesses; both pass
  with LibreOffice + Ghostscript installed and skip otherwise. Added the
  ship-path degradation guards too: `test_convert_office_to_pdf_without_libreoffice_raises`
  (clear install message) and `test_run_compress_balanced_without_ghostscript_is_lossless`
  (gs is AGPL-banned from bundling, so `balanced` ships the lossless fallback).
- [x] Messy real-world PDFs: `test_extract_text_handles_multicolumn_pdf` (two-column
  layout), `test_inspect_and_split_large_pdf` (50-page count + tail-range split),
  `test_inspect_encrypted_pdf_reports_gracefully` (encrypted → `encrypted=True`,
  `pages=0`, no crash), and `test_find_in_encrypted_pdf_reports_clear_error`
  (searching protected PDFs reports an unlock/password message instead of a
  raw pypdf traceback). Covers build-plan Risk #1.
- [x] **`unlock_pdf → find_in_document` chain forward-threading (fixed 2026-06-27).**
  A CLI run (`unlock sample-protected.pdf with pass secret and check if it contains
  beta`) routed correctly but the model pointed step 2 at the ORIGINAL locked file,
  so `find_in_document` failed on the still-encrypted original. This is a *chaining*
  gap, not a documents bug: the core auto-link (`_link_chain_intermediates`) only
  handled the *invented-intermediate* mode (ffmpeg), not the *reused-source* mode.
  Note `test_find_in_encrypted_pdf_reports_clear_error` (above) framed the encrypted
  error as graceful degradation and never caught that in a chain the search should
  run on the unlocked output. Fixed deterministically in core
  (`_forward_thread_reused_sources`, agent.py): when an output-capable producer
  consumes a single source and a later step reuses it, the reuse is rewritten onto
  the producer's output (assigning a `-chained` intermediate when none is declared).
  Skill-agnostic — every skill benefits. Tests: `test_chain_intermediate_linking.py`
  (forward-thread units) + `test_unlock_then_find_chain_threads_to_unlocked_output`
  (documents end-to-end).
- [ ] Whole-document OCR limitation (mixed text+scanned PDFs): revisit per-page
  detection only if it proves a real use case. No such use case has surfaced — left
  open intentionally.

## Phase F — Fine-tuning dataset — DEFERRED to a separate cross-skill plan

Per owner decision (2026-06-24): fine-tuning is **not** part of this plan. A
separate plan will cover fine-tuning for **all** skills together (see
`docs/TRAINING_DATA_GENERATION.md`). The gemma-only signals below are recorded
here as the documents-skill input to that future plan.

- **Known gemma-only fine-tune signals (2026-06-24)** — not deterministically fixable
  without regressing qwen: `061` lock→unlock & `065`/`086` protect↔unlock cross-pull
  (selection); `014` "number sample.pdf"→clarify/ocr (non-deterministic terseness);
  `028` "how many pages"→ invents `count_pages` (hallucinated tool); `041` reorder
  arg quality. qwen handles all of these — they're gemma comprehension/selection.

## Recommended order

T (test) → A (corpus) → B (lock baseline) → C/D (iterate) → E (coverage) →
F (deferred to the cross-skill fine-tuning plan). Phase T is cheap and
front-loaded because it surfaces the issues that make Phases A & C efficient;
nothing after B is measurable until the corpus is at scale and a baseline is
locked. Phases A–E are complete; F is out of scope for this plan.
