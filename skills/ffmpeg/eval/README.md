# FFmpeg Skill — Eval Guide

End-to-end runbook for the ffmpeg eval suite. Every command is implemented and
tested. See the design rationale at
[`docs/plans/2026-05-24-eval-suite.md`](../../../docs/plans/2026-05-24-eval-suite.md).

## What you get at the end

A score table comparing four arms against the human baseline:

| Arm | What runs | Score type |
|---|---|---|
| **Baseline** | Human-validated ffmpeg command | Reference (1.00 by definition) |
| **knaif + local models** | One row per model in `eval_backends.yaml` | Property-diff score 0.0–1.0 |
| **Big-LLM agents** (Claude Code, Codex, …) | External agent runs prompts end-to-end | Property-diff score 0.0–1.0 |
| **Plan-shape** | Did the model pick the right tool/args? | 0.0–1.0, reported separately |

All numbers are deterministic ffprobe property comparisons. Visual/audio
quality is a manual review step, supported by a priority-sorted HTML report
(see Phase E).

**Result files live under `evals/` at the project root** — one
subdirectory per arm (`local/`, `claude-code/`, `gpt-4o/`, …). Lightweight
artifacts (`score.json`, `review_log.json`, `report.md`) are committed.
Generated media (`out.*`, `.baselines/`) and the HTML report are gitignored.

## Prerequisites

```bash
just init        # create venv + install all deps

# ffmpeg + ffprobe on PATH
ffmpeg -version
ffprobe -version
```

Optional: install local model backends declared in `eval_backends.yaml`
(llama.cpp, Ollama). Skip if you're only running the baseline + big-LLM arms.

## Verifier modes

| Mode | `--verifier` flag | What it checks | Needs fixture? |
|---|---|---|---|
| **cheap** | `cheap` | Parses the rendered ffmpeg command — codec tokens, flags, filter substrings present in `success_criteria`. No execution. Fast; use for iteration and CI. | No |
| **honest** | `honest` | Executes the command against the fixture, probes the output with ffprobe, diffs against the stored `baseline` profile. | Yes |
| **success** | `success` | Executes the command, then grades the output file against the row's `success_criteria` dict (container, codec, resolution, duration, …). More precise than `honest`; preferred for release grading. | Yes |

All three modes compute the same **outcome accuracy** (`actual_outcome == expected_outcome`).
The verifier score (0.0–1.0) is an additional quality grade that only applies to `plan` rows.

## Phase A — Generate fixtures + seed baselines

Fixtures are synthesized locally via ffmpeg `lavfi`. Nothing is committed.

```bash
# Build the fixture set (testsrc/sine/etc. — a few seconds total).
# Outputs land under sandbox/fixtures/ffmpeg/.
just eval-fixtures ffmpeg

# Seed draft baseline commands from an LLM for any unvalidated rows.
just eval-seed ffmpeg
```

What `eval-seed` does:

- Reads each row's `utterances[0]`.
- Asks the configured LLM for a draft `baseline.command`.
- Writes the draft to the jsonl with `validated_by: null`.
- Skips rows that already have a validated baseline.

The draft is a starting point. Phase B is where it becomes ground truth.

## Phase B — Author baselines (Jupyter)

```bash
just baseline-authoring
```

For each row needing validation, the notebook displays:

1. The utterance(s).
2. The draft `baseline.command`.
3. The result of running that command against the fixture.

You decide:

- **`a`** — accept. Notebook writes `validated_by` back to the jsonl.
- **`e`** — edit. Paste a corrected command; notebook re-prompts.
- **`s`** — skip. Row stays unvalidated; loop continues.

The notebook saves the jsonl after each approval. Safe to close mid-flow.

## Phase C — Evaluate local models

For quick iteration (no fixtures needed):

```bash
just eval ffmpeg --save evals/local/ --verbose
```

For release-quality grading against `success_criteria` (requires fixtures from Phase A):

```bash
just eval-success ffmpeg --save evals/local/ --verbose
```

What it does:

- For each backend × each row × each utterance:
  - Calls knaif's pipeline → infers a plan, resolves stems, runs the NL clarify gate.
  - For `cheap`: renders the ffmpeg command and grades it against `success_criteria` tokens.
  - For `success`: executes the command against the fixture under
    `sandbox/<backend>/<row_id>__<utt_idx>/`, runs ffprobe on the output, grades
    against `success_criteria` fields.
  - Records outcome accuracy + verifier score 0.0–1.0.
- Writes one JSON per backend to `evals/local/`.

Flags:

```bash
# Single backend
just eval ffmpeg --backends qwen3-4b --save evals/local/

# Keep output files for manual inspection (success/honest only)
just eval-success ffmpeg --keep-artifacts --save evals/local/
```

## Phase D — Evaluate big LLMs

Hand the agent three things:

1. **The contract** — [`docs/BIG_LLM_HANDOFF.md`](../../../docs/BIG_LLM_HANDOFF.md).
2. **The corpus** — `src/skills/ffmpeg/data/eval.jsonl`.
3. **The fixtures** — `sandbox/fixtures/ffmpeg/` (run `just eval-fixtures ffmpeg` first).

Example prompt:

```
You are given:
  - An instruction contract: docs/BIG_LLM_HANDOFF.md  (read this first)
  - A task corpus:           src/skills/ffmpeg/data/eval.jsonl
  - Input video fixtures:    sandbox/fixtures/ffmpeg/

Follow the contract exactly. Write your results to evals/<your-agent-name>/.

For each corpus row where expected_outcome == "plan":
  1. Read utterances[0].
  2. Produce and run an ffmpeg command using sandbox/fixtures/ffmpeg/<fixture>.<ext> as input.
  3. Write evals/<your-agent-name>/<row_id>__0/cmd.txt  (the exact command)
  4. Write evals/<your-agent-name>/<row_id>__0/out.<ext> (the output file)

For rows where expected_outcome == "clarify":
  Write only cmd.txt containing: clarify: <your reason>
```

Then score:

```bash
just eval-score-external ffmpeg evals/claude-code/
```

## Phase E — Triage report

```bash
just eval-report ffmpeg evals/
```

Writes:

- **`evals/report.md`** — committable summary.
- **`evals/report.html`** — local triage UI.

```bash
# Open it: xdg-open (Linux) / open (macOS) / start (Windows)
xdg-open evals/report.html
# Or serve locally if file:// media links don't work:
uv run python -m http.server 8000
```

Mark rows as reviewed:

```bash
just eval-review evals/review_log.json r001 reviewed
just eval-review evals/review_log.json r002 rejected --notes "wrong codec"
```

## Phase F — Snapshot and regression

```bash
just eval-snapshot ffmpeg

# Later, after changes:
just eval-regression ffmpeg
```

`eval-regression` exits non-zero if any arm's average score drops by more than
the threshold (default 0.02).

## Phase G — Extending the corpus

### Quick add (one row)

1. Add an entry to `eval.jsonl` with `utterances`, `fixture`, `expected_outcome`,
   `expected_tool`. Leave `baseline` empty.
2. If the row needs a new fixture, add it to `eval/fixtures.py` and re-run
   `just eval-fixtures ffmpeg`.
3. Seed and validate: `just eval-seed ffmpeg`, then Phase B.
4. Re-run Phase C/D/E/F.

To add rephrasings to an existing row: append to the `utterances` array.
No baseline regeneration needed — rephrasings share the existing baseline.

### Bulk generation (agent playbook)

Load this context before generating:

```
src/skills/ffmpeg/tools.yaml          # supported tools and args
src/skills/ffmpeg/data/train.jsonl    # existing seed examples (style reference)
src/skills/ffmpeg/data/eval.jsonl     # current corpus (avoid duplicates)
src/skills/ffmpeg/eval/README.md      # success_criteria schema (below)
docs/EVAL_FRAMEWORK.md                # corpus envelope schema
```

Generate rows covering all supported tools, all platforms, all quality levels,
ambiguous requests (`expected_outcome: "clarify"`), bad/unsafe requests
(`expected_outcome: "reject"`), complex multi-step requests, and edge cases
(boundary values, typos, informal phrasing). Target ~300 rows total.

Aim for 3–5 `utterances` per row in different languages (EN, DE, ES, BG, ZH;
FR and RU where natural). All utterances on one row share the same fixture,
baseline, and `success_criteria`.

**Authoritative row template** (all fields that `CorpusRow` loads):

```jsonl
{
  "id": "ffmpeg_NNN",
  "utterances": ["<primary request>", "<rephrasing>", "<German>", "<Spanish>"],
  "expected_outcome": "plan",
  "fixture": "clip_1080p_h264_aac",
  "baseline": {"command": "ffmpeg -y -i current_file ... out.mp4", "validated_by": null},
  "expected_tool": "convert_video",
  "tolerances": {},
  "success_criteria": {"container": "mp4", "video_codec": "h264", "audio_codec": "aac"},
  "tags": ["convert"]
}
```

For `clarify`/`reject` rows, set `success_criteria: {}` and `expected_tool: null`.
For complex multi-step rows, set `expected_tool` to the primary intent and grade
on the final output only.

Leave `baseline.validated_by: null` for seeded drafts; the authoring notebook
sets it to `"human"` after review.

Validate after generation:

```bash
uv run pytest tests/test_evalsuite_corpus.py -v
```

## `success_criteria` Schema

Each corpus row's `success_criteria` is a JSON object used by the verifiers to
grade output. All fields are optional.

| Field | Type | Description |
|---|---|---|
| `container` | `str` | Expected output container (`"mp4"`, `"mkv"`, `"webm"`). |
| `video_codec` | `str` | Expected video codec (`"h264"`, `"hevc"`, `"vp9"`, `"av1"`, `"copy"`). Aliases resolved. |
| `audio_codec` | `str` | Expected audio codec (`"aac"`, `"mp3"`, `"opus"`, `"flac"`, `"copy"`). Use `"none"` when stripped. |
| `no_audio` | `bool` | `true` if audio should be stripped. |
| `encoder` | `str` | Exact encoder library name (`"libx264"`, `"libvpx-vp9"`). Raw token match. |
| `max_width` | `int` | Maximum output width in pixels. |
| `max_height` | `int` | Maximum output height in pixels. |
| `filters` | `list[str]` | Substrings expected in filter arguments (e.g. `["scale", "vf"]`). |
| `flags` | `list[str]` | Substrings expected as ffmpeg flags (e.g. `["-movflags", "-ss"]`). |

Verifier confidence levels:

- **`verifier_kind: "command"`** — text-only checks against the command string. Fast, lower confidence.
- **`verifier_kind: "output"`** — ffprobe on the actual output file. Higher confidence.

Examples:

```json
// Convert to MP4 with H.264 + AAC
{ "container": "mp4", "video_codec": "h264", "audio_codec": "aac" }

// Extract audio only (MP3)
{ "no_audio": false, "audio_codec": "mp3", "container": "mp3" }

// Scale down to 720p
{ "max_height": 720, "filters": ["scale"] }

// WhatsApp platform encode
{ "container": "mp4", "video_codec": "h264", "audio_codec": "aac",
  "max_width": 1280, "flags": ["-movflags"] }
```

For `expected_outcome: "clarify"` rows, leave `success_criteria` as `{}`.

To add a new criterion: add the field to `SUCCESS_CRITERIA_FIELDS` in
`verifiers.py`, implement the check, and update the table above.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ffmpeg not found on PATH` | Install ffmpeg; restart shell. |
| Baseline regeneration takes minutes | Cache miss. Check `sandbox/baselines/.cache` exists; verify fixture SHA hasn't changed. |
| Property diff fails on `bit_rate` only | Noisy field. Set `tolerances.bitrate_pct: 30` in the row, or drop it. |
| Notebook kernel dies mid-validation | Reopen — jsonl is consistent, approved rows stay approved. |
| Big-LLM agent wrote outputs but `score-external` skips them | Check directory layout matches `BIG_LLM_HANDOFF.md`. |
| `output_diff` reports failure but file looks fine | Property diff isn't perceptual. Compare `ffprobe baseline.mp4` vs `ffprobe model.mp4`. |
