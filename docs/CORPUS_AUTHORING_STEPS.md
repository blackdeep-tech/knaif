# Corpus Baseline Authoring — Manual Steps

> **This document describes the command-shaped path (ffmpeg).** If your skill is
> plan-shaped, most of it does not apply — see *Which path does my skill take?* below
> before running any of it.

## Which path does my skill take?

Corpus authoring forks on whether your skill renders a **command string**. Both paths are
supported; picking the wrong one wastes a lot of human review time.

| | **Command-shaped** (ffmpeg) | **Criteria-graded** (documents) |
|---|---|---|
| Row carries | a gold `baseline.command` | `baseline: null` |
| The oracle is | a human-validated command string, diffed against the model's | the `success_criteria` dict, graded deterministically by the verifier |
| Seeding | `just eval-seed <skill>` drafts commands via the model | **N/A** — there is no command to draft |
| Human validation | `just baseline-authoring` — approve each command | a read-only review notebook that *really executes* each row and shows a green/red table |
| Human effort | one review per plan row | **less** — criteria are authored once with the row; the verifier is the oracle |

The rest of this document is the **command-shaped** path. For a criteria-graded skill:

- Skip Steps 3–5 entirely (`eval-seed` / `baseline-authoring` are command-shaped by
  design, not a gap to be fixed).
- Author `success_criteria` with the row, leave `baseline: null`, and validate by running
  the skill's review notebook — `skills/documents/notebooks/documents_corpus_review.ipynb`
  is the reference implementation.
- Grade with the `success` verifier and implement `Skill.run_artifact`; see
  *Command-shaped vs plan-shaped skills* in
  [EVAL_FRAMEWORK.md](EVAL_FRAMEWORK.md#verifier-modes).
- `validated_by: "human"` is still set **only** by a person, never by an agent.

Steps 1–2 and everything from the eval run onward apply to both.

## Manual steps after pushing the branch

### Step 1 — Push the branch

```bash
git push -u origin feature/extend-corpus
```

---

### Step 2 — Generate fixtures (idempotent, ~10 seconds)

```bash
just eval-fixtures ffmpeg
```

Produces `sandbox/fixtures/ffmpeg/` with the 7 synthetic lavfi files. Safe to re-run; it skips cached fixtures.

---

### Step 3 — Seed draft baselines for unseeded plan rows

```bash
just eval-seed ffmpeg
```

This runs each unseeded utterance through your configured local model, extracts the ffmpeg command string, and writes it back to `eval.jsonl` with `validated_by: null`. Rows that already have `validated_by: "human"` are never touched.

**Requires** a backend in `eval_backends.yaml`. If you want to use a specific model:

```bash
just eval-seed ffmpeg --backends qwen3-4b
```

When done, all plan rows (those not marked `clarify`/`reject` and not already `validated_by: "human"`) will have a draft `baseline.command`. The command is idempotent — re-running skips rows that are already seeded or human-validated.

---

### Step 4 — Author (validate) the baselines

```bash
just baseline-authoring
```

Opens `notebooks/baseline_authoring.ipynb` in Jupyter Lab. For each unseeded row the notebook shows:

1. The utterance.
2. The draft `baseline.command`.
3. The result of running that command against the fixture.

You type:
- **`a`** → accept (writes `validated_by: "human"`)
- **`e`** → edit, then paste a corrected command
- **`s`** → skip (row stays `validated_by: null`, comes back next time)

The notebook saves after every accepted row. Close and reopen safely at any point.

**Do this in batches** — you don't need to author all rows in one sitting. Run `just eval-seed ffmpeg` and `just baseline-authoring` as many times as you like; already-validated rows are skipped.

---

### Step 5 — Commit the authored baselines

After a batch of authoring:

```bash
git add skills/ffmpeg/data/eval.jsonl
git commit -m "corpus(ffmpeg): human-validated baselines for batch X"
```

---

### Step 6 — Run a quick plan-correctness check (no ffmpeg needed)

```bash
just eval ffmpeg --verbose
```

Runs the `cheap` verifier against all rows with your default local model. Shows outcome accuracy (did the model pick the right tool?) and latency. No ffmpeg execution required.

---

### Step 7 — Run the full quality eval (requires ffmpeg on PATH)

```bash
just eval-success ffmpeg --backends qwen3-4b --config eval_backends.yaml --fixture-dir sandbox/fixtures/ffmpeg/ --save evals/local/
```

Executes each command against fixtures, grades with the `success` verifier against `success_criteria`.

---

### Step 8 — Build and review the HTML report

```bash
just eval-report ffmpeg evals/
# Open it: xdg-open (Linux) / open (macOS) / start (Windows)
xdg-open evals/report.html
```

Review rows in the browser. Worst rows (errors, low scores) appear first. Click **✓** / **✗** to mark each. Download `review_log.json` when done.

---

### Step 9 — Lock the regression snapshot (once happy with the results)

```bash
just eval-snapshot ffmpeg --backends qwen3-4b
```

This locks the acceptance bar with the `output_diff` verifier (not `cheap` routing) and
saves the run under `evals/runs/`. Re-lock deliberately, in its own commit, and only when
adopting a measured improvement.

---

**The only mandatory step before any real eval is Steps 2–4** (fixtures + seed + authoring). Steps 6–9 are the eval workflow you'll run repeatedly. Step 5 is just committing your authoring work.

Steps 6–9 are the corpus-authoring view of the general **eval ladder** (fast routing checks
while developing → executing verifier → locked snapshot → native parity). The ladder, and
why `cheap` is never an acceptance bar, are in
[EVAL_FRAMEWORK.md](EVAL_FRAMEWORK.md#the-eval-ladder--fast-while-developing-executing-before-done).

---

### Next phase — what follows corpus authoring

Once the corpus has validated baselines, the work moves from authoring to measurement:

1. **Cheap run first** — a routing-only sanity pass on the grown corpus. Treat it as a
   *new* baseline, not a comparison point: a run is only comparable to one sharing the
   same verifier and corpus revision (see
   [EVAL_FRAMEWORK.md](EVAL_FRAMEWORK.md#when-two-runs-are-comparable)). Archive
   superseded runs under `evals/_archive/` with a row in `evals/INDEX.md` — do not
   delete them.
2. **Honest run** — `--verifier success`: real execution graded by ffprobe against each
   row's `success_criteria`. This is the number to quote.
3. **Report and triage** — build the HTML report and work the failures worst-first.
   [EVAL_VERIFICATION_SOP.md](EVAL_VERIFICATION_SOP.md) owns this loop end to end,
   including how to read a failure and which layer owns the fix.
4. **Lock the snapshot** — re-lock the acceptance bar only when adopting a measured
   improvement, in its own commit.

Two follow-on tracks have their own plans: a premium-agent comparison arm
([big-llm-comparison](plans/2026-06-27-big-llm-comparison.md), contract in
[BIG_LLM_HANDOFF.md](BIG_LLM_HANDOFF.md)) and the LoRA work
([fine-tuning](plans/2026-06-27-fine-tuning.md)).

The fine-tuning dataset (`data/train.jsonl`) belongs to that second track. See
[TRAINING_DATA_GENERATION.md](TRAINING_DATA_GENERATION.md) for the generation
contract, and do not generate it until routing is healthy — training on a corpus whose
routing still fails bakes in the failures.
