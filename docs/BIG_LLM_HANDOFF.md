# BIG_LLM_HANDOFF.md — Eval Suite v2 Handoff Contract

## Purpose

This document describes everything a big LLM (GPT-4, Claude, Gemini, etc.) needs to
produce valid results that `evalsuite score-external` can consume and score.

The scorer grades your outputs with the **same `success` verifier** used for local
models, and emits a **scoreboard with the same schema**, so quality and speed are
directly comparable between local and premium arms in the HTML report.

---

## What the task is

The eval suite tests the ffmpeg skill of `knaif`. Each row in the corpus is a
natural-language video-processing request. The big LLM must:

1. Read the utterance.
2. Produce a single ffmpeg command that fulfils the request.
3. Run the command against the provided fixture file.
4. Write the output file, the command, and timing metadata to the results directory.

---

## Input — corpus schema

The eval corpus is at `skills/ffmpeg/data/eval.jsonl`. Each line is a JSON object:

```json
{
  "id": "ffmpeg_001",
  "utterances": ["convert clip.mp4 to mp4"],
  "expected_outcome": "plan",
  "fixture": "clip.mp4",
  "baseline": {"command": "ffmpeg -y -i clip.mp4 -c copy -movflags +faststart clip_converted.mp4", "validated_by": "human"},
  "expected_tool": "convert_video",
  "tolerances": {},
  "success_criteria": {"container": "mp4", "video_codec": "h264", "audio_codec": "aac"},
  "tags": ["convert"]
}
```

Key fields:
- `id` — unique row identifier.
- `utterances[0]` — the primary natural-language request. Process all utterances
  when you want multi-phrasing coverage (each utterance becomes `<id>__<idx>`).
- `fixture` — the **exact fixture filename, extension included** (e.g. `clip.mp4`).
  It is the file name directly, not a symbolic key: the input path is
  `sandbox/fixtures/<skill>/<fixture>`.
- `expected_outcome` — `"plan"` = produce a command; `"clarify"` = request is
  ambiguous; `"reject"` = request is unsafe/impossible.
- `success_criteria` — the absolute spec the verifier checks your output against.
  Present on a subset of rows; when absent the verifier falls back to `output_diff`
  against the `baseline` command.

---

## Fixture videos

Fixtures live under `sandbox/fixtures/<skill>/` (generate with `just eval-fixtures ffmpeg`).
The corpus `fixture` field is the filename itself — the input path is
`sandbox/fixtures/ffmpeg/<fixture>`. Canonical set (see [`skills/ffmpeg/eval/fixtures.py`](../skills/ffmpeg/eval/fixtures.py)):

| `fixture` | Description |
|---|---|
| `clip.mp4` | 10 s, 1920×1080/30, H.264 + AAC |
| `clip_ctr.mp4` | 10 s, 1920×1080/10, CBR ~1.6 MB — size-constrained tests only |
| `clip_no_audio.mp4` | 10 s, 1280×720/25, H.264, no audio |
| `clip_4k.mp4` | 5 s, 3840×2160/30, H.264 + AAC |
| `clip.mov` | 8 s, 1280×720/25, MOV container |
| `clip2.mp4` | 8 s, 1920×1080/30 — second clip for concat tests |
| `audio.mp3` | 10 s audio only |

---

## Output format — results directory

For each utterance you process, write one subdirectory:

```
results/
  <row_id>__<utterance_idx>/
    cmd.txt      # the exact ffmpeg command you ran (one line, absolute paths)
    out.<ext>    # the output file produced by that command
    meta.json    # timing and optional notes (see below)
```

- `<row_id>` — the `id` field from the corpus row.
- `<utterance_idx>` — 0 for `utterances[0]`, 1 for `utterances[1]`, etc.
- `cmd.txt` — the **exact** ffmpeg command you ran, with absolute paths. One line.
- `out.<ext>` — the output media file. Extension must match the intended container.
- `meta.json` — **required** for speed comparison. See schema below.

### `meta.json` schema

```json
{
  "elapsed_ms": 4200,
  "notes": "optional free-text"
}
```

- `elapsed_ms` — wall-clock milliseconds from receiving the utterance to the output
  file being fully written. Measures the same "time-to-artifact" the local runner
  records via `latency_ms`, enabling apples-to-apples speed comparison.
- `notes` — optional; any relevant context (e.g. model name, temperature used).

## Cost and speed — what is and isn't comparable

Quality compares cleanly (same verifier, same criteria). Cost does not, and the reason is
structural: **the two arms are not token-symmetric by construction.** The local model emits
a JSON *plan* that deterministic code renders into ffmpeg; the premium model emits the raw
ffmpeg line itself. So token counts and dollar costs describe the premium side only —
there is no local number to put beside them. Local llama.cpp is treated as free (local GPU)
and tracked on inference time alone.

If an arm records cost, put it in `meta.json` alongside `elapsed_ms` and label it as an
estimate:

| Field | Meaning |
|---|---|
| `est_input_tokens` / `est_output_tokens` | **Heuristic** — a chars ÷ ≈3.8 approximation. Not `count_tokens` (needs an API key), and deliberately not `tiktoken`, which undercounts Claude. |
| `est_cost_usd` | `input/1e6 × in_rate + output/1e6 × out_rate` at the arm's published rates. |
| `est_llm_latency_ms` | `output_tokens ÷ throughput` — a derived figure, **not a measurement**. |

Keep `elapsed_ms` as the real execution time; do not fold an inference estimate into it.
Treat the resulting cost as order-of-magnitude framing for the quality gap — "does the
local model's gap justify $X per 1000 requests?" — never as a billing figure.

### `clarify` / `reject` rows

For `expected_outcome == "clarify"` or `"reject"`, write **only** `cmd.txt`:

```
cmd.txt → "clarify: <your reason>"
```
or
```
cmd.txt → "reject: <your reason>"
```

No output file or `meta.json` is required for these rows.

### Example for row `ffmpeg_001`

```
results/
  ffmpeg_001__0/
    cmd.txt    → "ffmpeg -y -i /abs/path/clip.mp4 -c:v libx264 -c:a aac out.mp4"
    out.mp4    → the transcoded file
    meta.json  → {"elapsed_ms": 3200}
```

---

## Scoring

After the results directory is populated, run:

```bash
uv run -m knaif.evalsuite score-external \
    --skill ffmpeg \
    --results-dir results/ \
    --fixture-dir sandbox/fixtures/<skill>/
```

This:
- Runs the `success` verifier (same as local `--verifier success`) against each
  `out.<ext>` file, grading it against the row's `success_criteria`.
- Reads `meta.json` to populate `latency_ms` per entry.
- Writes `results/score.json` in **the same scoreboard schema** as a local
  `run --save` scoreboard (including `outcome_accuracy`, `time_to_artifact_ms`,
  per-row scores, `by_tag`).

To render the report and compare with local models:

```bash
uv run -m knaif.evalsuite report \
    --skill ffmpeg \
    --results-dir evals/
```

Your arm appears as a column in `report.html` beside the local models. Quality
(success scores) and speed (latency stats) are shown side by side.

---

## Checklist for the big LLM

- [ ] Run `just eval-fixtures ffmpeg` to generate fixture files.
- [ ] Read `eval.jsonl` and process every row where `expected_outcome == "plan"`.
- [ ] Use the fixture file from `sandbox/fixtures/<skill>/<fixture>` as input (the
      `fixture` field already includes the extension — do not append one).
- [ ] Write output to `results/<row_id>__0/out.<ext>`.
- [ ] Record the exact command in `results/<row_id>__0/cmd.txt`.
- [ ] Record elapsed time in `results/<row_id>__0/meta.json` (`{"elapsed_ms": N}`).
- [ ] For `clarify`/`reject` rows, write only `cmd.txt` with `clarify:` / `reject:`.
- [ ] Run `score-external` and aim for avg score ≥ 0.8.
- [ ] Share `results/score.json` for the combined report comparison.
