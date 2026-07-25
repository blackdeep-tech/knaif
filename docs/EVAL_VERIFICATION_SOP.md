# Eval Verification SOP

Standard operating procedure for manually verifying ffmpeg eval results —
both local-model runs and premium-agent runs.

## Prerequisites

- `ffmpeg` and `ffprobe` on PATH.
- Fixtures generated: `just eval-fixtures ffmpeg` (idempotent, cached).

---

## A. Local model verification

### 1. Run the eval

```bash
# Full run, success verifier (grades against success_criteria spec)
uv run -m knaif.evalsuite run \
    --skill ffmpeg \
    --verifier success \
    --backends qwen3-4b \
    --config eval_backends.yaml \
    --fixture-dir sandbox/fixtures/ffmpeg/ \
    --save evals/local/

# Quick plan check (no ffmpeg execution, fast)
uv run -m knaif.evalsuite run \
    --skill ffmpeg \
    --verifier cheap \
    --backends qwen3-4b \
    --config eval_backends.yaml \
    --save evals/local/
```

Output files land under `sandbox/<backend>/<row_id>__<idx>/out.<ext>`.
The scoreboard JSON is saved to `evals/local/ffmpeg_<backend>_success.json`.

**Do not pipe the run through `tail`** — it buffers until the process exits, so a
long run looks hung. Run it plain, or `tee` to a logfile; watch progress by listing
`sandbox/<backend>/<row_id>/` as rows land. Always `--save` to a fresh directory
rather than overwriting a previous run — tag-level comparison against the earlier
scoreboard is the whole point.

### 2. Build the report

```bash
uv run -m knaif.evalsuite report \
    --skill ffmpeg \
    --results-dir evals/
```

Opens `evals/report.md` (committable summary) and
`evals/report.html` (interactive triage UI).

### 3. Verify in the browser

```bash
# Open the report directly or serve locally if file:// media links don't load:
uv run -m http.server 8000
# Then open http://localhost:8000/evals/report.html
```

**What you see per row** (worst rows shown first — errors, then low scores):

| Column | Meaning |
|---|---|
| Row | `<row_id>__<utterance_idx>` |
| Utterance | The natural-language request |
| Tags | Skill category, language code, etc. |
| `<model>` score | 0.0–1.0 from the `success` or `cheap` verifier |
| Review | ✓ (pass) / ✗ (fail) / ↩ (reset) buttons |

**Click the row ID** (or the `▶ details` summary) to expand the detail card:

- **Reference player** — the baseline command's output (what "correct" looks like).
- **Model output player** — what the model actually produced. Play both to compare.
- **ffprobe diff table** — matched properties (green) vs failed ones (red).
- **Review CLI command** — copy-paste for `evalsuite review`.

### 4. Mark rows pass/fail

**Option A — in the browser** (fastest):
- Click **✓** to mark a row reviewed (passes).
- Click **✗** to mark a row rejected (fails).
- Click the **Download review_log.json** button when done to persist.

**Option B — CLI**:
```bash
uv run -m knaif.evalsuite review \
    --log evals/review_log.json \
    --row ffmpeg_001 \
    --utterance-idx 0 \
    --status reviewed \
    --notes "wrong codec"
```

### 5. Commit lightweight artifacts

Commit (do NOT commit generated media):
- `evals/<arm>/ffmpeg_<backend>_success.json` — scores
- `evals/review_log.json` — human verdicts
- `evals/report.md` — human-readable summary

These are gitignored automatically (add to `.gitignore` if needed):
- `evals/**/*.mp4`, `evals/**/*.mkv`, etc. — generated media
- `evals/.baselines/` — baseline reference files

---

## B. Premium agent verification (Claude Code, GPT-4o, …)

### 1. Hand off the task

Give the agent three things:

1. **Contract:** `docs/BIG_LLM_HANDOFF.md` (read first).
2. **Corpus:** `skills/ffmpeg/data/eval.jsonl`.
3. **Fixtures:** `sandbox/fixtures/ffmpeg/` (run `just eval-fixtures ffmpeg` first).

The agent must write, for each corpus row where `expected_outcome == "plan"`:
```
evals/<agent-name>/<row_id>__<utterance_idx>/
    cmd.txt      # the exact ffmpeg command run (one line, absolute paths)
    out.<ext>    # the output file
    meta.json    # {"elapsed_ms": 4200}
```
For `clarify`/`reject` rows: write only `cmd.txt` with `clarify: <reason>`.

### 2. Score the agent's results

```bash
uv run -m knaif.evalsuite score-external \
    --skill ffmpeg \
    --results-dir evals/<agent-name>/ \
    --fixture-dir sandbox/fixtures/ffmpeg/
```

This runs the `success` verifier against each `out.<ext>` file and writes
`evals/<agent-name>/score.json` in the **same schema** as a local
`run --save` scoreboard — including `latency_ms` from `meta.json`.

### 3. Compare all arms in one report

```bash
uv run -m knaif.evalsuite report \
    --skill ffmpeg \
    --results-dir evals/
```

The premium agent arm appears as another column in `report.html` beside
the local models. You see:
- **Quality** — same `success` verifier score side by side.
- **Speed** — mean/p50/p95 latency for each arm in the summary header.
- **Media players** — premium output and local output side by side for each row.
- **Same ✓/✗ buttons** — mark premium rows reviewed exactly as local rows.

---

## Triage order

The report sorts rows so the human starts at the most suspicious:

1. **Execution errors / parse failures** — model produced no usable output.
2. **Wrong outcome** — model `clarify`'d when it should have planned, or vice versa.
3. **Low score (< 0.5)** — command ran but output doesn't match the spec.
4. **Mid score (0.5–0.94)** — partial match; worth inspecting.
5. **High score (≥ 0.95)** — likely correct; spot-check only.

### Reading a failure

The `expected_outcome → actual_outcome` pair names the failure mode before you open
the row:

| Transition | What it means |
|---|---|
| `plan → error` / `parse_error` | knaif bug — schema, validation, or the model emitting unparseable JSON. **Not** a routing failure; do not chase it in the prompt. |
| `plan → clarify` | Model didn't understand the request, or a deterministic gate downgraded a correct plan (check the clarify text's source before blaming the model). |
| `plan → reject` | Model refused something in scope. |
| `clarify → plan` | Model acted on an ambiguous request — the outcome that actually matters for safety review. |
| `reject → clarify` | Model hedged instead of refusing. See "Refusal routing is a metric, not a guardrail" in [REQUIREMENTS.md](REQUIREMENTS.md) before treating this as a defect. |

### Classifying the fix

For each failing row, decide which layer owns it — the layer, not the symptom,
determines the fix:

- **Harness bug** (the *test rig* failed, not the product) → the eval wiring. Check this
  first, because it is invisible in the scoreboard: the row shows a model failure while
  the model was right. In one round, **9 of 41 failures** were the eval agent's sandbox
  pointing at `sandbox/` while the fixtures lived in `sandbox/fixtures/`, so extension-less
  corpus names raised `StemNotFoundError` and forced a clarify over a correct plan. The
  tell is that the raw pre-gate plan is correct — capture it and compare.
- **Routing bug** (wrong tool selected) → `tools.yaml` keywords and descriptions **first**,
  `prompt.yaml` only if that cannot express it. These are not interchangeable: a
  `tools.yaml` edit changes one tool's retrievability, while a `prompt.yaml` edit moves
  **every row in the skill** and needs a full re-measure across all active backends. A
  whole round of documents fixes — enum aliases, page grammar, arg-key aliases, keyword
  enrichment — landed at the `tools.yaml`/handler layer with the shared system prompt
  untouched. Prefer the narrowest layer that can hold the fix.
- **Expander/handler bug** (right tool, wrong command built) → the skill's
  `intents.py` / `handlers.py`.
- **Corpus gap** (the row tests something knaif genuinely can't do) → re-label it
  `expected_outcome: clarify` and file the feature; a corpus row is not a bug report.
- **Premium arm fails it too** → the task may be inherently ambiguous. Verify the
  `success_criteria` are actually achievable before changing any code.

Prefer deterministic fixes over prompt tuning when both could work: they are testable,
they hold across models, and repeatedly in this project a small structural fix has
outperformed every prompt edit tried against the same failures.

**Classify the whole failure set before editing anything.** The single most productive
round in this project began by root-causing all 41 failures into three buckets — harness
artifact (9), deterministic-guard artifact (11), genuine model error (21) — and found that
roughly **half the gap was not prompt-addressable at all**: about 6.7 of the 13.8 points.
Fixing the harness and the guard lifted outcome accuracy from 0.862 toward 0.93 *without a
single prompt edit*. Had the round started with prompt tweaks, those points would have been
attributed to the prompt and the real bugs would still be there.

### A fix that only satisfies the cheaper verifier is not a fix

`cheap` grades routing; `success` executes. When a change makes the cheap number move,
ask what `success` would say about the same rows.

A worked case: chains where the model omitted step-1's `output` were being downgraded to
`clarify` by the hallucinated-filename guard. Relaxing the guard was proposed and
**rejected** — it would have passed cheap's routing check while the chain remained
structurally unexecutable (step 2 had nothing to consume), so `success` would still fail
it. The accepted fix was deterministic: link the undeclared intermediate to the preceding
producer's `output`, which fixes routing *and* execution.

The general form: if a candidate fix would improve the metric without improving the
artifact, it is measuring-instrument work, not product work. Reach for `success` to
adjudicate.

### Establish the noise floor before trusting any delta

Run the **unchanged** configuration twice and diff per row. Until you know the noise band,
no variant's improvement is interpretable.

With greedy sampling (`temperature 0.0`) and `json_mode` off, two full control runs here
were **bit-identical** — same headline metrics and **0 per-row flips** — which is what
made single-row deltas trustworthy signal rather than sampling jitter. Do not assume this;
GPU float nondeterminism can flip tokens even at temperature 0, and the answer differs by
backend. Measure it, write the number down, and set the adoption threshold from it.

### Churn in one layer masks signal in another

If some rows flip nondeterministically under *any* change, they add noise to every arm you
measure and can swallow a real gain.

In the prompt round, chain-fragility churned ±5–7 rows regardless of what the prompt said,
so a prompt arm worth about +10 rows netted only +4 to +6 — and two variants of it looked
different from each other when the difference was entirely churn. The resolution was to
land the deterministic chain-linking fix **first**, then re-measure the prompt arm against
a stable baseline. When two arms interact like this, sequence them; do not try to read
both from one run.

### Prompt edits must clear every active backend

A prompt change that helps one model can regress another, so re-score **all** active
backends before keeping one — and state the acceptance bar *before* running, or the
result argues itself into acceptance afterwards. **Prefer no change over a regressing
change:** the corpus is large, any edit moves rows in both directions, and a net-zero
edit that shifts which rows fail is a loss, not a wash.

Deterministic fixes are exempt from the both-backends dance in one specific sense — an
error→plan coercion cannot change which outcome a model *chooses*, so a moved outcome
row after such a fix is re-sampling noise, not an effect. Prompt edits have no such
guarantee; every moved row is potentially real.

Chasing a single stubborn row with prompt edits is usually a mistake. A rule tightened
until one row flips is a rule loose enough to catch neighbours — the honest disposition
for a known small-model limit is to leave it failing and let the training mix address
it.

**A flat headline number is not a failed round.** As the deterministic surface gets
fixed, the remaining headroom shrinks: an early pass may move the aggregate several
points, a later one a point or two, and a round whose real work was removing *false*
failures can land flat by design. Judge a round by whether the rows it touched are now
scored for the right reason, not by the delta — and say which it was when reporting.

---

## Snapshot and regression gate

After a confirmed-good run, lock the snapshot with the `output_diff` acceptance-bar
verifier (this is what `just eval-snapshot ffmpeg` runs):

```bash
uv run -m knaif.evalsuite run --skill ffmpeg --verifier output_diff --snapshot
```

CI regression check (exits 1 if any metric drops > 0.02):

```bash
uv run -m knaif.evalsuite regression --skill ffmpeg
```

### The two ways an aggregate gate lies to you

A gate that always passes is worse than no gate: it looks healthy in CI and nobody checks
it again. Both failure modes below were found while building the cross-skill gate, and
both generalize to any gate that loops over several targets.

**1. The self-compare false green.** `regression` defaults `current = baseline` when
`--current` is omitted. So the obvious all-skills implementation — loop over skills,
call the per-skill gate — compares each snapshot **against itself** and passes every time,
for every skill, forever. This is why `regression --all-skills` *requires* `--current-run`
pointing at a sweep folder. When you write a gate, assert that it can actually fail:
inject a regression into one target and confirm a red, and confirm a clean run is not a
no-op self-compare.

**2. "Not measured" read as "passed".** Snapshots are heterogeneous — ffmpeg's is
`output_diff`, documents' is `success` — so a sweep will not always produce a scoreboard matching every
snapshot's verifier. Two superficially identical situations need opposite handling:

| Situation | Verdict |
|---|---|
| The snapshot's verifier was **never run** in this sweep | **Skip, with a printed reason.** Not a failure — nothing was measured. |
| That verifier **was** run for other skills, but this skill's file is missing | **Hard fail — coverage gap.** The skill should have been measured and silently wasn't. |

Collapse them one way and the gate floods with false reds; collapse them the other and a
skill can drop out of the sweep entirely while the gate stays green. A skill with no
snapshot at all is a third case: report "no baseline", never a silent pass.

### The gate is only valid when the corpus row set is unchanged

`eval-regression` compares **aggregate** metrics against the snapshot. If the corpus
gained or lost rows since the snapshot was locked, that comparison silently conflates
two different things — "behavior changed" and "the denominator changed" — and the
direction of the error depends on whether the new rows are easier or harder than the
old mean. Adding twenty easy rows can mask a real regression; adding twenty hard ones
invents one.

So when a change touches the corpus or the tool set, do not read the aggregate. **Join
the two runs per row on shared ids** and ask which previously-passing rows now fail:

```python
b = {r["id"]: r for r in json.load(open(BASELINE))["rows"]}
c = {r["id"]: r for r in json.load(open(CURRENT))["rows"]}
shared    = b.keys() & c.keys()
regressed = [i for i in shared if b[i]["outcome_correct"] and not c[i]["outcome_correct"]]
improved  = [i for i in shared if not b[i]["outcome_correct"] and c[i]["outcome_correct"]]
```

New rows appear in `c.keys() - b.keys()` and are graded by their own
`success_criteria`, not by this diff.

### A non-empty `REGRESSED` list is not automatically a regression

Read every entry before believing it. In the geometry/thumbnail round, twelve rows
flagged across two backends and **none** was a real regression: each was either a single
flaky multilingual utterance failing to emit a plan while the row's other utterances
passed, or a row already failing in the baseline on knaif score. None touched the code
that had changed. The check to apply is whether the flagged row exercises the changed
path — a regression list is a list of *candidates*.

### Snapshot the pre-change state first

Re-locking the snapshot overwrites the only aggregate record of the previous state. If
no baseline exists for the current code, save one **before** making the change:

```bash
uv run -m knaif.evalsuite run --skill ffmpeg --verifier success \
  --backends qwen3-4b,gemma3-4b --save evals/baselines/<date>_<label>_success
```

then log it in `evals/INDEX.md`. This is a lesson from losing the pre-geometry snapshot
to a re-lock; the saved run folders are the durable per-row record, the snapshot is not.

### Which comparison tool to reach for

| Tool | Compares | Granularity | Good for |
|---|---|---|---|
| per-row id join (above) | two saved run JSONs | per-row, shared ids | "did existing behavior change" when the corpus grew or shrank |
| `eval-regression` | a run vs `eval_snapshot.json` | aggregate, 0.02 threshold | quick gate — **only** when the row set is unchanged |
| `eval-compare` | backends within **one** run | aggregate, side-by-side | model A vs model B at the same code state |
| `eval-report` | one run | per-row, human-readable | worst-row triage |

> **Run artifacts are mostly untracked.** `evals/**` is gitignored except an allowlist —
> `INDEX.md`, `score.json`, `report.md`, `review_log.json`, and `evals/retrieval/*.json`.
> Everything else (per-backend run JSONs, generated media, `report.html`) exists only on the
> machine that produced it, so re-running a tool that writes into a run directory overwrites
> the previous output with no git copy to recover or diff against. `evals/INDEX.md` is the
> durable record of what each run was.
>
> **If you add a gate, check that what it reads is tracked.** The retrieval baselines are on
> the allowlist because `just check` runs a test that opens
> `evals/retrieval/2026-07-02_phase1.json` by path — while it was ignored, that test raised
> `FileNotFoundError` on any fresh clone. A gate whose baseline lives under `evals/**` is
> broken-by-default for everyone but the machine that created it, and the failure looks like a
> broken test rather than a missing file. Verify with `git check-ignore -v <path>`, never by
> seeing the file in your own working tree.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Videos don't play in `file://` | Serve with `uv run -m http.server 8000` instead |
| `score-external` skips rows | Check directory layout matches `BIG_LLM_HANDOFF.md` |
| Baseline player blank | Run `just eval-fixtures ffmpeg` to regenerate fixtures |
| `ffprobe_failed` in failed list | ffprobe not on PATH, or output file is corrupt |
| Score differs from visual quality | `success` is property-based, not perceptual — inspect the ffprobe diff table |
