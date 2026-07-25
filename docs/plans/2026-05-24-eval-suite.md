# Eval Suite — Framework + ffmpeg

**Status:** Done · **Created:** 2026-05-24 · **Completed:** —
**Owner:** eval · **Ref:** —

> **Status note:** Fully implemented — this is the spec the `evalsuite` package and the
> ffmpeg eval plugin were built to; both shipped. (The original "no code yet" framing below
> is the pre-implementation context.) It supersedes and absorbs an earlier same-week draft
> (2026-05-23) that was replaced before implementation; that draft's surviving rationale —
> the execution model and the locked decisions — is carried below.
>
> **Path note (2026-07-23).** The body uses the **pre-monorepo** layout it was written in —
> `src/knaif/evalsuite/` and `src/skills/ffmpeg/eval/`. The code shipped and now lives at
> **`python/core/knaif/evalsuite/`** and **`skills/ffmpeg/eval/`** (via the monorepo move and
> the 2026-07-19 [restructure](2026-07-19-repo-restructure.md)); `eval_results/` is now
> `evals/`. Read the paths below as historical; the import name `knaif.evalsuite` is unchanged.

**Goal:** Replace the command-string-as-baseline eval with an output-based eval backed
by a reusable framework (`knaif.evalsuite`) plus an ffmpeg eval plugin.

## Goal

Replace the current command-string-as-baseline eval with an output-based eval
backed by a reusable framework. Two outcomes:

1. **Framework** (`src/knaif/evalsuite/`) — runs any skill's `CommandAgent`
   over a versioned corpus, compares results across arms (human baseline /
   local models / big LLMs), and produces a priority-sorted triage report so
   humans review only the highest-signal rows. No skill-specific code lives
   here.
2. **ffmpeg plugin** (`src/skills/ffmpeg/eval/`) — the first concrete
   verifier + reviewer + reference loader on top of the framework. Answers
   one question per row: **does the model's ffmpeg command produce output
   equivalent to a human-validated reference?**

Final deliverable is a comparison table:

| Row | Human baseline | knaif (model A) | knaif (model B) | Claude Code | Codex |
|---|---|---|---|---|---|
| ffmpeg_v2_001 | 1.00 | 0.83 | 0.91 | 0.95 | 0.88 |
| ... | | | | | |

## Problems with the first eval design that this fixes

1. `baseline_freeform_command` references fake paths (`input.mkv`) — the honest
   verifier can't run them, so `baseline_score` is always 0.
2. Scoring penalizes valid command variation. Two ffmpeg commands producing
   identical output should score the same; today they don't.
3. One utterance per row. No way to measure phrasing robustness.
4. `success_criteria` is hand-maintained and drifts from reality (we hit a
   `flag`/`flags` typo on row 012).
5. No mechanism to compare against big-LLM agents (Claude Code, Codex) — so
   no answer to "is knaif's ffmpeg skill better than just asking Claude
   directly?"
6. No reuse path for the next skill. Anything skill-specific is tangled into
   the core eval code.

---

# Part 1 — Framework

## Cross-skill evaluation models

Different skills have fundamentally different evaluation models:

| Skill kind | Example | Reference | Comparison | Human role |
|---|---|---|---|---|
| **Deterministic execution** | ffmpeg | Validated output file | Property diff | Spot-check disagreements |
| **Plan-only (no execution)** | trading, infra | Expected step sequence | Step-set / order comparison | Review divergent plans |
| **Side-effecting API** | Slack, Linear | Expected API calls + args | Recorded-call diff (mock) | Review intent vs result |
| **Structured output** | data extraction, code review findings | Reference JSON | Field-level diff | Resolve ambiguous cases |
| **Subjective freeform** | joke writing, copywriting | None (or human-rated exemplar) | No automated score | Rate every output |

The framework is generic; what varies per skill is **the verifier and the
reviewer**.

## Generic vs skill-specific

**Generic (in `src/knaif/evalsuite/`):**

- Corpus loading (utterances, tags, expected_outcome).
- Multi-arm runner (baseline / local models / big LLMs).
- Score aggregation, snapshot, and regression logic.
- Triage report (priority-sorted, persistent review log).
- HTML/Markdown report skeleton with a "row card" slot the skill fills in.
- Big-LLM-handoff CLI subcommand (`score-external --skill <name> --agent <name>`).

**Skill-specific (in `src/skills/<name>/eval/`):**

- **Verifier plugin** — `(artifact, reference, sandbox) → VerifyResult`.
  Returns a score in [0,1] for automatable skills, or
  `score=None, needs_human=True` for subjective ones.
- **Reference type** — what counts as ground truth. May be:
  a file path (ffmpeg), a structured plan (trading), an expected-calls
  list (Slack), a freeform exemplar (jokes), or nothing at all.
- **Reviewer plugin** — `render_row(row, arm_outputs, reference) → HTML`.
  Renders a card the human can act on: video player for ffmpeg, step diff
  for trading, side-by-side text for jokes with a 1–5 rating widget.
- **Verdict parser** — turns human input (y/n, rating, free text) into a
  score that aggregates into the same scoreboard.
- **Skill runbook** (e.g. `src/skills/<name>/EVAL_GUIDE.md`) — concrete
  end-to-end instructions for the skill's eval workflow.
- **Big-LLM handoff contract** (`src/skills/<name>/eval/BIG_LLM_HANDOFF.md`)
  — describes the input/output layout external agents follow for this skill.

## Contract summary

```python
# Every skill's src/skills/<name>/eval/ exposes:
VERIFIERS: dict[str, Verifier]          # named verifiers (e.g. "output_diff", "plan_diff")
REVIEWER: Reviewer | None               # render+parse for human review; None = no UI
REFERENCE_LOADER: Callable | None       # how to load reference for a row; None = no reference
```

`Verifier` and `Reviewer` are Protocols defined in `evalsuite/`. The runner
doesn't know what skill it's running. The report doesn't know what's in
the card. Reviewing a joke and reviewing a video reuse the same scaffold.

## Corpus schema (generic)

The framework defines required generic fields. Each skill is free to add
its own; the framework treats unknown keys as opaque pass-through.

**Generic required:**

| Field | Notes |
|---|---|
| `id` | Unique within the corpus. |
| `utterances` | List ≥1. Each runs as a separate eval pass; all share one reference (if any). |
| `expected_outcome` | `plan` / `clarify` / `reject` |
| `tags` | List of strings; framework reports per-tag breakdowns. |

**Generic optional:**

| Field | Notes |
|---|---|
| `expected_tool` | For plan-shape scoring. |
| `baseline` | Skill-specific object; reference data plus `validated_by`/`validated_at` provenance. May be `null` for skills with no reference. |

Skills add fields under `baseline` (e.g. ffmpeg's `baseline.command` +
`baseline.output`; trading's `baseline.plan`) without framework changes.

## Multi-arm runner

The runner produces one output per `(row, utterance, arm)` triple:

- **Baseline arm** — runs the skill's reference. ffmpeg executes
  `baseline.command`; trading uses the stored plan directly. Skill plugin
  decides.
- **Local model arms** — one per backend in `eval_backends.yaml`. Each
  drives the skill's `CommandAgent`.
- **Big-LLM arms** — external. Framework defines a directory contract
  (`eval_results/<agent>/<row_id>__<utt_idx>/`) and a `score-external`
  CLI that scores any directory matching the contract through the skill's
  verifier.

All arms produce skill-specific artifacts; the verifier reads them and
returns a normalized score.

## Execution model

Local LLM inference is hardware-saturating — one model uses the whole GPU or all
CPU threads. That dictates the concurrency shape:

- **Across backends: serial by default.** Backend A processes the whole corpus,
  then backend B. Running them concurrently only trades one bottleneck for
  contention, and interleaving would pay a 5–30 s GGUF model swap per prompt.
- **Within a backend, two phases.** Inference is serial per row (the model is the
  bottleneck); *verification* — executing the generated command against fixtures
  and probing the output — is codec-bound and independent per row, so it
  parallelizes across `--workers N` processes. This is why the worker flag exists
  on the verify side and nowhere else.
- **`--parallel-backends` is an advanced opt-in** for multi-GPU rigs. Off by
  default, documented with a contention warning.

For Ollama-only comparisons, keep-alive can hold several warm models in VRAM if
they fit, which removes the reload cost between sequential ollama backends.

## Triage at scale

Property diff (or whatever the skill's verifier produces) scores every row
automatically, but at N=1000 rows × ~3 rephrasings × ~4 arms = ~12,000
outputs, manual review of everything is infeasible. The framework
produces two reports that bring the highest-signal rows to the top:

**`eval_results/report.md`** — committable summary:

- Pass rate per arm, per tag.
- Top 50 disagreements between arms (one arm passed, another failed).
- Top 50 close-miss fails (sorted by closeness — most likely tolerance
  issues or genuine edge cases).
- Random sample of 20 passes per arm (sanity check).

Fits in a PR diff. The number people look at first.

**`eval_results/report.html`** — local triage UI:

- Self-contained HTML, no server. Open via `file://` or
  `python -m http.server` from the repo root.
- Sortable/filterable table of every row × arm.
- Per row: the skill's reviewer renders a card (video preview for ffmpeg,
  step diff for trading, rating widget for jokes).
- "Mark reviewed" button writes to `eval_results/review_log.json` (the
  one persistent state file across review sessions).
- Re-generated from `eval_results/*.json` by `evalsuite report`. No
  database, no separate state.

**Priority order** in both reports:

1. Unreviewed disagreements between arms.
2. Unreviewed close-miss fails.
3. Unreviewed clean fails.
4. Random sample of passes.

The point is to never ask the human to look at the same row twice and to
make sure the first 100 rows they see are the most informative.

**Scoring bands.** The three triage sections above are defined by these cutoffs
(they appear as bare literals throughout `report.py`, so this is the one place
that says what they mean):

| Band | Rule | Why |
|---|---|---|
| **Pass** | `score >= 0.95` | Not 1.0 — property diff carries per-row tolerances (duration ±0.5 s, size ±20%), so a correct output rarely scores exactly 1.0. |
| **Disagreement** | `max(arm scores) >= 0.95 and min < 0.5`, ≥2 arms scored | Deliberately a *wide* gap. One arm clearly right and another clearly wrong is the highest-signal row a human can look at; near-ties are noise. |
| **Close miss** | `0.3 <= score < 0.95` | The band where a fail is most likely a tolerance that needs tuning or a genuine edge case, rather than the model doing something unrelated. Sorted by score descending — closest first. The `0.3` floor exists to keep outright wrong-tool rows out of a section meant for tolerance triage. |

Sampled passes use a seeded RNG so the same 20 rows per arm reappear across runs —
re-running the report must not reshuffle what the human already reviewed.

**The HTML report orders by a finer key than the four-item priority list above**
(`_triage_key` in `report.py`), because two failure modes turned out to outrank any
score-based band: a row that *errored or produced no artifact*, and a row whose
**outcome** was wrong (`plan` when it should have clarified). Both are bugs rather
than quality signals, so they sort above even the widest arm disagreement:

```text
0 — execution errors / missing outputs
1 — wrong outcome (outcome_correct = False)
2 — score < 0.5, or no score on a plan row (artifact missing)
3 — score 0.5–0.94
4 — score >= 0.95, and clarify/reject rows that are scoreless by design
```

Ties inside a tier break on the row's minimum score across arms, so the worst
offender in each tier surfaces first.

## LLM-as-judge as a per-skill verifier choice

For skills where deterministic scoring isn't possible (jokes, copywriting,
summarization, code-review findings), the skill *may* register an
LLM-judge verifier alongside the human reviewer:

- **Verifier** = LLM-judge: prompts a model with `(utterance, model output, [optional exemplar])` and asks for a score + rationale. Returns a number; logs the rationale.
- **Reviewer** = human spot-check: the report's review log lets the human override the LLM-judge's verdict on flagged rows.
- **Calibration**: a small "golden set" of human-rated rows establishes the LLM-judge's bias direction (charitable / harsh / inconsistent). Re-run when changing judge models.

This is *not* a framework feature — it's just a verifier implementation a
skill author can write. ffmpeg won't use it (deterministic property diff
is better). Trading probably won't (plan-step equality is deterministic).
Jokes likely will. The choice is per-skill, decided when the skill's eval
plugin is written.

Risks worth flagging when a skill opts in:

- Judge bias drifts with model upgrades — pin the judge model explicitly.
- Self-preference: a Claude-judge tends to rate Claude outputs higher. Use a different family as judge than as evaluated arm where possible.
- Cost scales with `rows × arms` — for 1000 × 4 arms, an API-based judge is real money.

---

# Part 2 — First consumer: ffmpeg

The ffmpeg-specific implementation on top of the framework.

## Corpus schema v2 (ffmpeg)

```jsonl
{
  "id": "ffmpeg_v2_001",
  "utterances": [
    "Convert this MKV to MP4.",
    "Make this an MP4",
    "Save as mp4 please"
  ],
  "fixture": "clip_1080p_h264_aac.mkv",
  "baseline": {
    "command": "ffmpeg -y -i clip_1080p_h264_aac.mkv -c:v libx264 -crf 23 -c:a aac out.mp4",
    "output": "baselines/ffmpeg_v2_001.mp4",
    "validated_by": "maintainer",
    "validated_at": "2026-05-24"
  },
  "expected_outcome": "plan",
  "expected_tool": "convert_video",
  "tolerances": { "duration_s": 0.5, "size_pct": 20, "bitrate_pct": 20 },
  "tags": ["convert", "container"]
}
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Unique, `ffmpeg_v2_NNN` |
| `utterances` | yes | List, ≥1 entry. Each runs as a separate eval pass; all share one baseline. |
| `fixture` | yes for `plan` rows | Fixture name (key in `fixtures.py`). Resolved to `sandbox/fixtures/<name>` at run time. |
| `baseline.command` | yes for `plan` rows | Human-validated ffmpeg invocation. |
| `baseline.output` | yes for `plan` rows | Relative path inside `sandbox/baselines/`. Regenerated locally. |
| `baseline.validated_by` / `validated_at` | yes when baseline present | Provenance. |
| `expected_outcome` | yes | `plan` / `clarify` / `reject` |
| `expected_tool` | optional | For plan-shape scoring. |
| `tolerances` | optional | Per-row overrides of global defaults. |
| `tags` | optional | Same use as today. |

`success_criteria` is gone. The reference profile is derived at eval time by
ffprobe-ing `baseline.output`.

## Fixtures: synthesized, not committed

**No media binaries in git.** Everything ffmpeg-produced (fixtures, baselines,
model outputs) lives under `sandbox/` and is `.gitignore`d. Only text
artifacts (jsonl, json scores, md reports) are committed.

Fixtures are synthesized via ffmpeg `lavfi`:

```python
# src/skills/ffmpeg/eval/fixtures.py
FIXTURES = {
    "clip_1080p_h264_aac": (
        "ffmpeg -y -f lavfi -i testsrc=duration=10:size=1920x1080:rate=30 "
        "-f lavfi -i sine=frequency=1000:duration=10 "
        "-c:v libx264 -c:a aac -shortest clip_1080p_h264_aac.mp4"
    ),
    "clip_no_audio": "...",
    "clip_4k_h264": "...",
    "clip_mov": "...",
    "audio_only_mp3": "...",
}
```

`evalsuite fixtures regen --skill ffmpeg` rebuilds them all under
`sandbox/fixtures/`. Cheap (~seconds), deterministic. Adding a new fixture
is one dict entry, not a binary upload.

**Baselines** are regenerated by running `baseline.command` against the
relevant fixture under `sandbox/baselines/`. Cache key = SHA256 of
`(baseline.command, fixture_name)`; cache hit → skip regeneration.

## Verifier: ffprobe property diff

ffmpeg's verifier plugin implements the framework's `Verifier` contract.
Two outputs are "equivalent" when all of these match (with per-row
tolerances):

| Field | Comparison | Default tolerance |
|---|---|---|
| `format_name` (container) | membership in token list | exact |
| video `codec_name` | equality with codec aliases | exact |
| audio `codec_name` | equality with codec aliases | exact |
| video stream presence | exact | — |
| audio stream presence | exact | — |
| `width` × `height` | equality | exact |
| `pix_fmt` | equality | exact |
| duration | difference | ±0.5 s |
| file size | percent band | ±20% |
| `sample_rate`, `channels` (audio) | equality | exact |
| `bit_rate` | percent band | ±20% (often noisy; skip if missing) |

Anything outside this list (frame content, color accuracy, audio quality,
trim correctness) → manual review with `--keep-artifacts`. A passing score
means "didn't fail a deterministic check," not "did the right thing." The
report header must say so.

## Reviewer: HTML video card

ffmpeg's reviewer plugin implements the framework's `Reviewer` contract.
The per-row HTML card contains:

- The utterance(s).
- Inline `<video>` (or `<audio>`/`<img>`) tag for: fixture, baseline output,
  each arm's output. Sources are `file://` paths under `sandbox/`.
- ffprobe-property diff table highlighting failed fields.
- "Mark reviewed" buttons (writes to framework's review log).

## Baseline authoring: Jupyter notebook

For the human-validation step, a Jupyter notebook is the right tool — the
workflow is fundamentally about *seeing* output, not scripting it.

**Why notebook over CLI:**

- Inline `<video>` and `<audio>` preview of fixture, baseline output, and
  model output. No alt-tabbing to a player.
- Side-by-side display when correcting a placeholder.
- ffprobe results render as pandas DataFrames.
- Iterative: re-run one cell after editing a command; no full restart.
- Re-runnable on demand; not part of CI.

**Notebook location:** `src/skills/ffmpeg/eval/baseline_authoring.ipynb`.

**Notebook flow (one cell per phase):**

1. Load `src/skills/ffmpeg/data/eval_v2.jsonl` into a DataFrame.
2. Filter to rows needing work (`baseline.validated_by` is null or
   `--re-validate` flag set).
3. For each row, in a loop with `display(...)`:
   - Show utterances + fixture preview.
   - Run `baseline.command` against fixture; preview output inline.
   - Show ffprobe properties.
   - Prompt approval: `input("approve [y/n/edit]: ")`.
     - `y` → write `validated_by`/`validated_at` back to jsonl.
     - `n` → mark row as needs-rework, continue.
     - `edit` → open a text input for a corrected command, re-run, re-prompt.
4. Save the jsonl. Print summary (`approved: N`, `pending: M`).

**Placeholder seeding** (so the human has something to start from):

When a skill is scaffolded (or via a one-shot CLI helper:
`evalsuite seed-baselines --skill ffmpeg`), an LLM call generates a draft
`baseline.command` for each row using the utterance and the corpus header.
`validated_by` stays null; the notebook is where these get approved.

## Big-LLM handoff (spec only)

v1 ships a markdown contract describing how an external coding agent (Claude
Code, Codex, etc.) should consume the ffmpeg corpus and produce results. v1
does NOT build the multi-agent runner.

**Contract file:** `src/skills/ffmpeg/eval/BIG_LLM_HANDOFF.md`. Describes:

- Input: path to `eval_v2.jsonl` + `sandbox/fixtures/` (after `fixtures regen`).
- Task per row: for each utterance, produce an ffmpeg command, execute it,
  save the output to a known location.
- Output layout: `eval_results/<agent_name>/<row_id>__<utterance_idx>/cmd.txt`
  and `out.<ext>`.
- Validation: human runs `evalsuite score-external --skill ffmpeg --agent <name>`
  which reads the directory and runs the same property diff.

The point is to make external-agent runs interoperable with our scoring
without writing a per-agent harness now.

---

# Part 3 — Implementation

## What changes in the code

**Framework (`src/knaif/evalsuite/`):**

| Module | Change |
|---|---|
| `corpus.py` | New schema parser. `utterances: []` (list, not scalar). Pass-through of skill-specific `baseline` object. Keep v1 loader as fallback for migration window. |
| `runner.py` | One utterance = one run. Calls skill's verifier with skill's reference. Skill-agnostic. |
| `scoring.py` | Generic scoreboard. Dispatches to skill verifier by name. No ffprobe knowledge. |
| `protocols.py` | New. Defines `Verifier`, `Reviewer`, `VerifyResult`, `ReferenceLoader` Protocols. |
| `report.py` | Emits `report.md` + `report.html`. Calls skill reviewer for row cards. |
| `cli.py` | New subcommands: `seed-baselines`, `score-external`, `report`, `fixtures regen`. Each dispatches by `--skill`. |
| `review_log.py` | New. Persistent state for human verdicts. |

**ffmpeg plugin (`src/skills/ffmpeg/`):**

| Module | Change |
|---|---|
| `eval/verifiers.py` | Implements framework `Verifier` contract — `output_diff` replaces `cheap`/`honest`. Tolerance constants live here. |
| `eval/fixtures.py` | New. lavfi-based fixture definitions. |
| `eval/reviewer.py` | New. HTML card renderer + verdict parser implementing framework `Reviewer` contract. |
| `eval/BIG_LLM_HANDOFF.md` | New. External-agent handoff contract for ffmpeg. |
| `eval/baseline_authoring.ipynb` | New. Jupyter notebook for human validation. |
| `data/eval_v2.jsonl` | New corpus. v1 file kept for one release. |
| `EVAL_GUIDE.md` | Already drafted. End-to-end runbook for ffmpeg. |

**Repo-level:**

| File | Change |
|---|---|
| `.gitignore` | Add `sandbox/` (everything under it). No binaries in git. |

## Migration

- Land the new framework alongside v1 corpus support. The runner accepts
  both schemas during the transition.
- Land `eval_v2.jsonl` alongside `eval_v1.jsonl`. Tests reference whichever
  version they target.
- Port the 15 existing v1 rows to v2 in the notebook (one sitting; the
  baselines are simple).
- Delete v1 loader + cheap/honest verifier protocol + v1 corpus after v2
  lands clean for one cycle.

## What stays the same

- Plan-shape scoring (`compute_metrics`, `_args_match`). Reported alongside
  per-skill verifier scores as a separate column. Tells you "model picked
  wrong tool" vs "tool picked right but produced wrong artifact."
- The `CommandAgent` / `Skill` / `HandlerContext` API. No skill code changes
  outside `eval/` subfolders.
- Per-tag breakdowns and snapshot/regression workflow.
- Multi-backend support via `eval_backends.yaml`.

## Decisions locked

- **The scoreboard snapshot is committed to git**, so regression mode works out of
  the box for any clone and the team shares one acceptance bar. Snapshot diff noise
  on PRs is accepted as the cost.
- **Generated media is not.** Fixtures, baselines, and model outputs are
  regenerated locally on first run and gitignored — see *Fixtures: synthesized, not
  committed*.
- **No live cloud calls at eval time.** External-agent results arrive through the
  directory contract and are scored offline; the framework never holds an API key.

## Out of scope (v1)

- Perceptual comparison (SSIM, VMAF, PESQ). Manual review owns this.
- Multi-agent runner for big LLMs. Spec only.
- Auto-rephrasing (LLM-generated alternate utterances). Hand-written for now.
- Trimming-correctness check (model could output a 90s clip with wrong
  start; property diff won't catch it). Manual review owns this.
- LLM-as-judge verifier for ffmpeg. Property diff is sufficient; LLM-judge
  would add cost without signal.

## Open questions / risks

**Framework-level:**

1. **HTML report file:// links.** Some browsers refuse `file://` links to
   local media for security reasons. If that bites, fall back to
   `python -m http.server` from repo root and use relative HTTP paths.
2. **Review-log conflict resolution.** If two reviewers append to
   `review_log.json` from different machines (e.g. via shared repo), last
   write wins. Acceptable for single-developer use; document if it stops
   being true.
3. **Big-LLM agents don't speak the contract yet.** v1 ships the markdown
   spec, but no agent has been driven through it end-to-end. First real
   use will surface gaps. Acceptable; iterate.
4. **Per-skill verifier interface stability.** Once skills depend on the
   `Verifier` Protocol shape, changing it cascades. Lock the interface
   before the second skill adopts it.

**ffmpeg-specific:**

5. **bit_rate is noisy.** `ffprobe` reports format-level bit_rate that varies
   ±5% rerunning the same command. Recommend: skip unless `target_size_mb`
   is in play, then check size only.
6. **Fixture catalog needs definition.** First pass: 1080p h264+aac, no-audio,
   4K, mov container, mp3-only. All synthesized via lavfi (see
   `fixtures.py`). Add more as new rows need them — one dict entry per
   fixture.
7. **Notebook state loss.** If the kernel dies mid-validation, the jsonl
   should still be in a consistent state. The notebook writes after each
   approval, not at the end.
8. **Codec aliasing inconsistency.** Audio codec `aac` appears as `aac` or
   `aac_lc` in different ffprobe versions; existing alias table needs an
   audit before being used as ground truth.
9. **Tolerance values are guesses.** First pass uses the table above; tune
   from real data after first 10 baselines land.

---

# Part 4 — Task breakdown

## Starting state (verified 2026-05-24)

- `src/knaif/evalsuite/` exists with `cli.py`, `corpus.py`, `runner.py`,
  `scoring.py`, `report.py`, `snapshot.py`. Subcommands: `run`, `compare`,
  `regression`, `show-baseline`. We extend, not bootstrap.
- `eval_backends.yaml` at repo root. `gemma3-4b-ollama` enabled; others
  commented out.
- `src/skills/ffmpeg/eval/` has `fixtures.py` and `verifiers.py` (v1 cheap/
  honest verifier). To be replaced.
- `src/skills/ffmpeg/data/eval_v1.jsonl` — AI-generated, **disposable**.
  Replaced by hand-seeded v2 corpus; not migrated.
- `corpus.CorpusRow.utterance` is singular and contains
  `success_criteria`, `baseline_freeform_command`. To be replaced with the
  v2 schema.
- `runner.run_corpus` runs `execute_plan(dry_run=True)` and extracts the
  command string. For v2 we add a real-execute mode that runs the command
  and returns the **output file path** under
  `sandbox/<backend>/<row_id>__<utt_idx>/`.

## Approach: CLI-first, TDD

Every subcommand gets a test before implementation. The notebook calls the
same CLI subcommands (via `subprocess.run` or by importing the
`cmd_*` functions) — no duplicated logic in notebook code, no separate
notebook smoke test required.

## Tasks (ordered)

Sequencing is dependency-driven: each task is unblocked by the ones above
it and should land as one logical commit.

### Foundations

**T1. `.gitignore`** — add `sandbox/`. One-line change; unblocks every
output-producing task.

**T2. `protocols.py`** — define `Verifier`, `Reviewer`, `VerifyResult`,
`ReferenceLoader` Protocols.
*Verify:* `pytest tests/test_protocols.py` confirms a stub verifier
satisfies `isinstance(stub, Verifier)`.

**T3. v2 corpus schema** — replace `CorpusRow` fields with `utterances:
list[str]`, `fixture`, `baseline: dict | None`, `expected_tool`,
`tolerances`, `tags`. Drop `success_criteria` and
`baseline_freeform_command`. Update `load_corpus`/`save_corpus`. Delete
`eval_v1.jsonl`. Update `show-baseline` and any v1-shaped tests/fixtures.
*Verify:* `pytest tests/test_corpus.py` covers a v2 row round-trip and
schema-error cases.

### ffmpeg plugin

**T4. `fixtures.py` (rewrite)** — lavfi `FIXTURES` dict (at least:
`clip_1080p_h264_aac`, `clip_no_audio`, `clip_4k_h264`, `clip_mov`,
`audio_only_mp3`). Pure data — no execution code lives here.
*Verify:* `pytest` imports the module and checks each entry is a valid
ffmpeg lavfi command (parses, has `-f lavfi`).

**T5. `evalsuite fixtures regen --skill ffmpeg`** — new CLI subcommand
that reads `fixtures.py` and runs each command into
`sandbox/fixtures/<name>`. SHA-based cache skip if fixture exists with the
same source command.
*Verify:* Integration test runs the subcommand with a 1-entry fixtures
dict against a tmp sandbox and asserts the file exists + is non-empty.
Skips if `ffmpeg` not on PATH.

**T6. `output_diff` verifier** — replace `cheap`/`honest` in
`verifiers.py`. Takes `(model_output_path, baseline_output_path,
tolerances)`, runs ffprobe on both, returns `VerifyResult(score, fields,
failed_fields)`. Tolerance constants at module top.
*Verify:* Unit tests with two fixture files (identical → 1.0; one with
mismatched codec → 0.x; both ffprobe results mocked to avoid binary
dependency). One integration test with real ffprobe-able files.

### Runner change (execute + capture)

**T7. Runner real-execute mode** — `runner.run_corpus` gets an
`execute: bool = False` flag. When true, runs `execute_plan(dry_run=False)`
inside a per-row `sandbox/<row_id>__<utt_idx>/`, captures the resulting
output file path, returns it on `AgentOutput.artifact_path`. Add
`AgentOutput.utterance_idx` so multiple utterances per row get distinct
paths.
*Verify:* Test runs a 1-row corpus with a mock handler that writes a known
file and asserts `artifact_path` matches.

**T8. ffmpeg handler honors per-row sandbox** — confirm
`ctx.sandbox` is the per-row dir at execute time; adjust the handler if it
hard-codes paths. May be no-op if `HandlerContext.sandbox` already
threads through.
*Verify:* Existing ffmpeg handler tests pass; new test runs the handler
with a custom sandbox and asserts output lands in it.

### CLI subcommands (TDD each)

**T9. `evalsuite seed-baselines --skill <name>`** — for each row where
`baseline.validated_by` is null, call an LLM (using the same backend
config) with the utterance and write `baseline.command` (validated_by
stays null). Idempotent.
*Verify:* Test stubs the LLM call, runs against a 3-row fixture, asserts
draft commands populated.

**T10. `evalsuite run` extended** — add `--verifier output_diff`,
`--save DIR`, `--keep-artifacts`. Internally calls runner with
`execute=True` for the chosen rows, scores via `output_diff` against
baseline output (which is regenerated on demand from `baseline.command` +
fixture; cache in `sandbox/baselines/.cache/`).
*Verify:* End-to-end test on a 1-row corpus with a stubbed handler; asserts
scoreboard JSON written and contains score for the row.

**T11. `evalsuite score-external --skill <name> --agent <name>
--results-dir DIR`** — reads the directory layout
(`<row_id>__<utt_idx>/{cmd.txt, out.<ext>}`), runs the same verifier
against each, writes `<dir>/score.json`.
*Verify:* Test runs against a fixture results dir with 2 entries.

**T12. `evalsuite report --skill <name> --results-dir DIR`** — emits
`report.md` (committable summary, top 50 disagreements, top 50 close
misses, sampled passes) and `report.html` (sortable table, calls skill's
`Reviewer.render_row` for each card).
*Verify:* Test runs against synthetic scoreboard JSON, asserts both files
emitted with the right sections.

**T13. `review_log.py` + "mark reviewed" wiring** — persistent JSON state.
HTML report writes via a small JS form to a file:// — or, more reliably,
the human runs `evalsuite review --row <id> --status reviewed` from the
command line. Pick one before implementing.
*Verify:* Test for the log read/write round-trip.

### ffmpeg reviewer + content

**T14. ffmpeg `reviewer.py`** — implements `Reviewer.render_row`. Returns
an HTML fragment with utterance, inline `<video>` tags for fixture +
baseline + each arm, ffprobe diff table.
*Verify:* Snapshot test on the rendered HTML for one row.

**T15. Seed `eval_v2.jsonl`** — hand-write 10–15 rows covering the
common ffmpeg use cases (convert, trim, resize, audio-extract,
concat, …). Each row: id, 1 utterance (rephrasings come later), fixture,
empty baseline, expected_outcome, expected_tool, tags. Then run
`seed-baselines` to fill in draft commands.
*Verify:* `evalsuite run --skill ffmpeg --limit 3` completes without
errors against the seeded corpus.

### Notebook + handoff doc (last; both call the CLI)

**T16. `BIG_LLM_HANDOFF.md`** — the contract document.
*Verify:* Read through; no automated test.

**T17. `baseline_authoring.ipynb`** — calls `evalsuite seed-baselines`,
loads the jsonl, loops with display() + input(), saves on each approval.
No new logic — everything heavy lives in CLI functions the notebook
imports.
*Verify:* Manual walkthrough on 2 rows. The user explicitly waived
automated notebook tests.

### Cleanup

**T18. EVAL_GUIDE.md polish** — remove the "not yet implemented" notice;
ensure every command shown matches the actual CLI. Snapshot the working
flow.
*Verify:* Run every PowerShell block in EVAL_GUIDE.md top-to-bottom; all
succeed.

**T19. Delete v1 artifacts** — `eval_v1.jsonl` and any v1 verifier code
not yet removed. Old tests deleted or rewritten against v2.
*Verify:* `pytest` green; `grep -r "cheap\|honest\|eval_v1"` returns
nothing meaningful.

## Notes on TDD discipline

- Write the test before the code for T2, T3, T6, T7, T9–T14. These are
  the deterministic pieces.
- T5, T8, T10, T11, T17, T18 are integration-shaped — write the test as
  an end-to-end CLI invocation in a tmp dir.
- T15, T16 are content tasks; no TDD applies.
- After each task: `just check` (full suite) before moving on.
