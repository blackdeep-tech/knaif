# Knaif Eval Framework

The eval suite (`python/core/knaif/evalsuite/`) evaluates any `CommandAgent` backend
against a versioned JSONL corpus. It compares knaif's deterministic output
against a pre-recorded freeform-LLM baseline, without making live cloud calls
at eval time.

> **This is the batch view.** For the per-request "one user, one request — what does each
> option cost and deliver?" view, including live premium-agent arms and their measured
> tokens, see [the 2026-07-02 agent comparison](experiments/2026-07-02-agent-vs-knaif-realworld.md)
> and its harness in `scripts/agent_vs_knaif/`. The two are complementary: the corpus tells
> you aggregate quality, the experiment tells you what a single request costs in seconds and
> dollars.

## Corpus envelope

Each row in an `eval.jsonl` file follows this schema:

```jsonl
{
  "id": "ffmpeg_042",
  "utterances": ["take clip1.mov and clip2.mov, combine into one mp4 but reversed"],
  "expected_outcome": "plan" | "clarify" | "reject",
  "expected_tool": "concat_video",
  "fixture": "clip.mp4",
  "baseline": {"command": "ffmpeg -i clip1.mov ...", "validated_by": "human"},
  "tolerances": {"duration_s": 1.0},
  "tags": ["concat", "multilingual", "es"]
}
```

### Field reference

| Field | Required | Description |
|---|---|---|
| `id` | yes | Unique identifier, e.g. `ffmpeg_042` |
| `utterances` | yes | List of natural-language phrasings. Each is run as `<id>__<idx>` (e.g. `ffmpeg_042__0`). |
| `expected_outcome` | yes | `"plan"`, `"clarify"`, or `"reject"` |
| `expected_tool` | for `plan` | The intent tool the planner should pick |
| `fixture` | no | Fixture filename, extension included (e.g. `clip.mp4`); resolved under `sandbox/fixtures/<skill>/`, needed for honest-mode runs |
| `baseline` | no | `{command, validated_by}` — pre-recorded freeform-LLM command for comparison |
| `tolerances` | no | Verifier-specific tolerances (e.g. `duration_s` for trim) |
| `success_criteria` | no | Absolute quality spec for the `success` verifier (see below). Leave `{}` for `clarify`/`reject` rows. |
| `tags` | no | Categorization tags for per-tag breakdowns. Conventional tags: `multilingual`, `en`/`de`/`es`/`bg`/`zh`/`fr`/`ru`, `indirect`, `trap`, `crf`, `quality`, `complex` |
| `outputs` | no | Multi-output rows only — ordered list, one entry per deliverable (see below) |
| `expected_tools` | no | Expected public-tool sequence, e.g. `["trim_video", "extract_audio"]`. `expected_tools[0]` must equal `expected_tool`. |
| `grade` | no | `"full"` (default) or `"routing"` — see below. Any other value fails corpus load. |

For unsupported features (feature gaps), set `expected_outcome: "clarify"` —
knaif is graded on whether it correctly asks for clarification. The
`baseline.command` is still recorded to show what the user probably wanted
and feed "should we build this?" decisions.

### Row shapes

| Shape | `expected_outcome` | Description |
|---|---|---|
| **Standard** | `plan` | Single intent tool, straightforward phrasing |
| **Complex** | `plan` | Multi-step ("trim first, then scale result"); graded on final output |
| **Bad** | `reject` | Unsafe, impossible, or data-exfiltration requests |
| **Edge** | `clarify` | Boundary values, typos, informal/ambiguous phrasing |

Each shape is authored in multiple `utterances` on the same row so language variants share
one fixture, one baseline, and one `success_criteria`.

### The utterance-equivalence contract

Bundling utterances on one row asserts that **all of them are true paraphrases of one
intent** — same expected outcome, same criteria. Break that contract and the row
manufactures permanent false negatives: if some utterances name their inputs (correctly
`plan`) while siblings are underspecified (correctly `clarify`), one group is scored
wrong no matter what the model does, and no amount of model improvement can fix it.

Distinguishing a mis-binned row from ordinary model noise takes a **cross-backend**
read, which is why it is worth running two backends:

| Fingerprint | Diagnosis |
|---|---|
| The *same* utterance gets different outcomes on different backends | Model variance. Not a corpus bug. |
| *Different* utterances in one row get different outcomes, each stable across backends | The utterances aren't equivalent — a corpus bug. |

The fix is to **split** the row by outcome, not to reword utterances until they agree.
Rewording a clarify utterance into a named-file one silently converts a clarify test
into a plan test and erases deliberate coverage. Rewording is legitimate in the narrow
case where one utterance simply *says something different* from its siblings — a
"remove the audio" row whose fourth utterance literally reads "extract the audio" is a
translation slip, and correcting the wording restores the row's intent rather than
changing it.

**Before editing any `success_criteria`, read the row's other utterances and the other
backends.** A low score is usually one utterance on one model, while the criteria are
correct for the majority — so tuning the criterion to the failure regresses everything
that was passing. Ask which of these the row actually is:

| The failing utterance… | Then |
|---|---|
| means something different from its siblings | reword or re-bin it; leave the criteria alone |
| is genuinely ambiguous (two defensible readings) | either mark the row `clarify`, or keep the criteria and accept that the other reading scores 0 — but decide, and record which |
| is right, and the criteria are too strict | loosen the criteria, then re-score every backend to confirm nothing regressed |

The failure mode to avoid is over-fitting the corpus to one model's output. Criteria
describe what a correct result looks like, not what the current model happens to emit.

### Corpus composition

Coverage is not the same as row count, and eval wall-clock is a real cost — every row
runs × utterances × backends.

- Keep rows that test **distinct triggers** (unsupported feature, vague operation,
  bad parameter). Each one asks a different question.
- Prune **redundant restatements** of a behavior the operation doesn't affect. Thirteen
  rows proving "a descriptive reference is not a filename" across thirteen operations
  test one behavior thirteen times.
- Once a behavior becomes **deterministic**, a few unit tests on that code cover it
  better than corpus volume ever did. Keep a token sample in the corpus and move the
  real coverage into tests.
- Trimming is an **eval** decision only. `train.jsonl` may still want the examples the
  eval no longer needs — teaching a behavior and measuring it have different appetites.

### Descriptor rows and the injection assumption

A row whose utterance names no file — `"resize the 4K video to 1080p"`, `"the mov"`,
`"compress it"` — but whose `expected_outcome` is `plan` is **silently assuming context
injection**: the model can only act legitimately if something told it which files exist.
Nothing does today. Such a row is mislabeled for the current runtime and should either
expect `clarify` or name its file.

This is easy to write by accident, because the row's `fixture` puts exactly the file the
model would have to guess into the sandbox. Two rules keep it honest:

- **The eval harness runs with injection permanently OFF.** An injector that handed the
  fixture listing back to the model would re-validate the guess and re-open the hole the
  clarify gate exists to close.
- **Resolution is name-based, never property-based.** `"the 4K video"` resolves only by
  matching the token against known *filenames* (`clip_4k.mp4` contains `4k`). Nothing
  probes files with ffprobe at plan time to decide which one is "the 4K one".

#### Sizing it — the descriptor analyzer

`knaif.evalsuite.descriptor_analysis` is a read-only tool that finds these rows. It reads
a run's per-arm `*_success.json` scoreboards plus the corpus, classifies each emitted
plan input (`exact` / `stem` / `glob` / `chain` / `descriptor`), and resolves the
descriptors against an available-files set. It decides nothing and mutates nothing —
it writes `descriptor_analysis.{md,json}` into the run directory.

```bash
uv run -m knaif.evalsuite.descriptor_analysis evals/runs/<run-dir> \
  --corpus skills/ffmpeg/data/eval.jsonl
```

Every cross-tab is computed **twice**, because the right answer differs by world:

| World | Available files | Meaning |
|---|---|---|
| **OFF** | filenames written in the utterance itself | today's runtime and today's harness |
| **ON** | that set ∪ the row's `fixture` | the future world where a listing is injected |

The `fixture` is the honest proxy for the listing an injection step would produce, which
is what lets the effect of injection be measured **before the feature exists**. Descriptor
rows then bin by (expected, actual, resolution):

| Bin | Meaning |
|---|---|
| **PRIZE** | expected `clarify`, model planned on an unresolvable descriptor — the gate correctly catches it |
| **RISK** | expected `plan`, currently correct, but the descriptor doesn't resolve — the gate would wrongly clarify it |
| **SAFE** | expected `plan`, correct, descriptor resolves uniquely |
| **POLICY-CONFLICT** | expected `clarify`, yet the descriptor *does* resolve uniquely — corpus and rule disagree |

Read the two worlds together: a row that is **RISK under OFF but SAFE under ON** is
exactly the mislabeled-for-today case above. On the reference run that class was 6 rows on
qwen3-4b and 4 on gemma3-4b, against 34 and 3 POLICY-CONFLICT rows respectively.

Stem-format tokens (`clip_4k`, no extension) are tracked on a separate axis
(`STEM_SAFE`/`STEM_RISK`/`STEM_CONFLICT`/`STEM_OK`) because the deterministic stem
resolver handles them before the clarify gate ever sees them — don't read them as
descriptor findings.

Design rationale: [descriptor-mixed-intent-analyzer](plans/2026-06-09-descriptor-mixed-intent-analyzer.md).

### Routing-only rows (`grade: "routing"`)

Batch/glob rows — `inputs: ["*.mp4"]`, "convert every file in this folder" — route
correctly but cannot materialize a single artifact for the `success` verifier to grade:
the runner gates execution on one `row.fixture`, and the agent's sandbox is `./sandbox`,
not `sandbox/fixtures/`. Graded normally they score 0 with `artifact_file_missing` and
drag their whole tag down while nothing is actually broken.

Set `grade: "routing"` on such a row and `success()` scores it on outcome only, skipping
artifact grading. This is a **scoring** decision, not a lowered bar: the batch capability
is verified where the logic lives, with unit tests on glob expansion in
`ResolveInputs`/`cmd_resolve_inputs`. Reach for it only when the row is structurally
ungradeable — never to silence a row that genuinely produces a bad artifact.

### Language tag convention

Target languages: `en`, `de`, `es`, `bg`, `zh` (primary); `fr`, `ru` (maintained).
Multi-language rows carry `["multilingual", "<lang-code>"]` in `tags`.

### `success_criteria` schema

The `success` verifier grades the produced output file against these fields (all optional):

| Field | Type | Description |
|---|---|---|
| `container` | `str` | Expected output container (`"mp4"`, `"mkv"`, `"webm"`). Compared as a **token set**: both sides split on `,` and pass on any intersection, so ffprobe's multi-valued `format_name` (`"matroska,webm"`) matches either `"webm"` or the full string. Codec checks resolve aliases the same way — never assume an exact string compare. |
| `video_codec` | `str` | Expected video codec (`"h264"`, `"hevc"`, `"vp9"`, `"av1"`). Aliases resolved. |
| `audio_codec` | `str` | Expected audio codec (`"aac"`, `"mp3"`, `"opus"`, `"flac"`). Use `"none"` when stripped. |
| `no_audio` | `bool` | `true` if audio must be absent |
| `encoder` | `str` | Exact encoder library name (`"libx264"`, `"libvpx-vp9"`) |
| `max_width` | `int` | Maximum output width in pixels |
| `max_height` | `int` | Maximum output height in pixels |
| `filters` | `list[str]` | Substrings expected in filter arguments (e.g. `["scale", "vf"]`) |
| `flags` | `list[str]` | Substrings expected as ffmpeg CLI flags (e.g. `["-movflags", "-ss"]`) |

Leave `success_criteria` as `{}` for `clarify`/`reject` rows.

### Multi-output rows

Some utterances ask for **two deliverables** from one request — "trim clip.mp4 to 3–5
seconds, save it, then extract the audio as mp3" yields a trimmed video *and* an mp3.
These are only expressible as a chain (`trim_video → extract_audio`), so one
`command` / `success_criteria` pair cannot grade them.

A row is multi-output iff it sets `outputs`; such rows must be `expected_outcome:
"plan"`. Each entry describes one deliverable:

| Key | Type | Description |
|---|---|---|
| `command` | `str` | Reference command. Entries run **in sequence in one working dir**, so a later `-i <name>` may reference an earlier entry's output filename. |
| `criteria` | `dict` | Per-output `success_criteria` (same keys as above) |
| `tolerances` | `dict` | Optional per-output tolerances |

Grading: under an executing verifier (`success`, `output_diff`) the runner captures one
artifact per intent batch into `AgentOutput.artifact_paths` and routes the row to the
`grade_outputs` verifier, which applies the normal `success` logic per deliverable.
Labels are prefixed `outN:`; a missing path scores 0 for that output; the row score is
the **mean** of per-output scores, so every deliverable weighs equally. Under `cheap`
(no execution) an `outputs` row falls back to the plan-level verifier.

When authoring the reference commands, name intermediate files explicitly and reuse the
name verbatim in the next command — do not use `$var` chaining. See the caveat in
`docs/VARIABLE_BINDING.md`.

#### An `outputs` row with empty `criteria` measures nothing

`grade_outputs` scores each deliverable against `outputs[i].criteria`. Leave that dict
empty and the row still scores — it checks only that *N files were produced*, not that any
of them is correct. The score goes **up**, because "a file exists" is far easier than the
single-artifact grading it replaced.

This happened: auto-seeding `outputs` onto chain rows without criteria moved avg knaif
0.966 → 0.974, and the gain was hollow. Rebuilt with real per-output criteria, the honest
number was **0.971** — lower than the hollow one, and lower than it looked, but now
actually measuring chain correctness. It immediately exposed real weaknesses the inflated
metric had hidden (final file not resized; mkv requested, mp4 produced; mp3 requested, aac
produced).

Two rules follow. Every `outputs` entry needs criteria that could **fail** — derive them
from the row's human-validated `success_criteria`, and if a deliverable has none worth
writing, drop that entry rather than shipping an empty one. And the entry count must match
the chain the model actually produces: seeding two outputs where one command does both
operations grades a file that was never meant to exist.

A metric that rises while its criteria get weaker is the thing to watch for — check what a
score change is measuring before believing it.

## Verifier contract

Each skill exposes verifiers in `skills/<name>/eval/verifiers.py`:

```python
from knaif.evalsuite.runner import AgentOutput
from knaif.evalsuite.scoring import VerifyResult
from pathlib import Path
from typing import Any

def my_verifier(
    output: AgentOutput,
    criteria: dict[str, Any],
    sandbox_dir: Path,
) -> VerifyResult:
    ...

VERIFIERS: dict[str, Verifier] = {
    "cheap": my_verifier,
    "honest": another_verifier,   # optional — executes the command, probes the output
    "success": success_verifier,  # optional — grades output against success_criteria
}

SUCCESS_CRITERIA_FIELDS: dict[str, str] = {
    "field_name": "description of what this field checks",
}
```

`VerifyResult` fields:
- `score: float` — 0.0–1.0 (fraction of criteria matched)
- `matched: list[str]` — criteria that passed
- `failed: list[str]` — criteria that failed
- `verifier_kind: str` — `"command"`, `"output"`, or `"plan"`

### Verifier modes

| Mode | `--verifier` flag | What it checks | Executes? | Needs fixture? | Row must carry |
|---|---|---|---|---|---|
| **cheap** | `cheap` | Parses the rendered command **string** — codec tokens, flags, filter substrings — plus the outcome. | No | No | — |
| **honest** | `honest` | Runs the command against a fixture, then probes the output. Skill-defined. | Yes | Yes | — |
| **output_diff** | `output_diff` | Runs the command and compares the result against the row's human-validated `baseline.command` output. | Yes | Yes | `baseline` |
| **success** | `success` | Runs the command, then grades the output file against the row's absolute `success_criteria`. Most precise. | Yes | Yes | `success_criteria` |
| **grade_outputs** | *(auto)* | Multi-output chains — applies `success` logic per deliverable. Routed to automatically for `outputs` rows under an executing verifier. | Yes | Yes | `outputs[].criteria` |

Skill authors may omit `honest` / `output_diff` / `success` — only `cheap` is required to exist.
But see the rule below: `cheap` alone is not enough to call a skill finished.

### The eval ladder — fast while developing, executing before done

Verifiers are not alternatives to choose between; they are **phases**. Live in 1–2 while
building a skill, cross 3–5 once to finish it, then re-run 3–5 on meaningful change.

| Phase | Command | Needs | Speed | Answers |
|---|---|---|---|---|
| **1. Authoring** | `uv run pytest skills/<name>/python/tests/`<br>`just native-mock -- skills list` | nothing (mock backend) | seconds | Does it load, validate, dry-run? |
| **2. Routing** | `just eval <skill> --limit 20`, then full `just eval <skill>` | model | minutes | Does the model pick the right tool? |
| **3. Honest** | `just eval-fixtures <skill>` **first**, then `just eval-success <skill>` | model + external binaries | slow | Is the produced artifact actually right? |
| **4. Lock** | `just eval-snapshot <skill>` | model + binaries | slow, rare | Commit the acceptance bar (own commit) |
| **5. Parity** | `just parity <skill>` | model + native build | slow | Does the native runtime render what Python renders? |

> **Phase 3 prerequisite — generate fixtures first, always.** An executing verifier with
> missing fixtures does not error; it *silently scores near-zero on correct plans*. A
> documents baseline once landed at outcome ≈0.55 with knaif score **1.000** — 58 of 129
> rows errored purely because `sandbox/fixtures/documents/` was absent. If an executing run
> looks catastrophically bad while routing looks fine, check fixtures before anything else.

**The rule:**

> **`cheap` is an iteration instrument, never an acceptance bar.** A skill's committed
> snapshot is always an *executing* verifier — `success` where rows carry
> `success_criteria`, `output_diff` where they carry `baseline` commands. A skill is not
> "done" until its snapshot is locked with one of those.

Why `cheap` cannot hold the bar: it never runs the command, so it inherits the
*validation stops at dispatch* gap ([ARCHITECTURE.md](ARCHITECTURE.md#known-gap-validation-stops-at-dispatch))
— a command string can look perfect and still produce the wrong file. Worse, it reports
**false regressions**: when 11 ffmpeg chain rows gained validated `outputs`, their
`verifier_kind` flipped `plan → output` and scored 0.0 under no-execution, dropping the
cheap aggregate 0.973 → 0.928 with no behavior change whatsoever. A bar that moves when
the corpus is annotated is not a bar.

Pick between the two executing verifiers by **coverage of that skill's corpus**: prefer
`success` (more precise), fall back to `output_diff` when more rows carry a `baseline`
than `success_criteria`. Changing a skill's snapshot verifier is a deliberate re-lock, in
its own commit — never automatic.

Absent CI, nothing forces a fast gate, so there is no infrastructural reason to lock a
`cheap` snapshot. The only remaining excuse is a skill whose external binaries genuinely
cannot run on the author's machine — a temporary concession that means running with **no
artifact-level regression gate at all**, not a supported configuration. Say so in the
skill's SPEC if you do it.

#### Command-shaped vs plan-shaped skills

The table above describes ffmpeg, which renders **one shell command string** per intent —
so the runner can capture that string as the artifact, execute it, and probe the result.

Not every skill works that way. A **plan-shaped** skill (documents is the reference case)
executes through library calls, so there is no command to capture: `_extract_artifact`
returns `None`, and any check that needs a produced file — `output_exists` above all —
fails for every destructive row. The failure is **silent**: the run completes, the rows
score, and destructive rows are simply understated. Nothing in the scoreboard says why.

Two rules follow for a plan-shaped skill:

- **Implement `Skill.run_artifact`.** When it is present, the runner falls back to
  `artifact = json.dumps(plan)`, and `run_artifact` replays that plan — copying
  plan-referenced inputs into a working directory and materializing the real output file
  to grade. Command-shaped skills are unaffected: their artifact is non-`None` already.
- **Grade with `success`, not `honest`.** `honest` is defined against a probe of a
  command's output; `success` grades the produced file against the row's
  `success_criteria`, which is what a plan-shaped skill can actually satisfy.

If a new skill's destructive rows all score suspiciously low on `output_exists`, this is
the first thing to check.

## Scoring model

For each corpus row the engine produces:

- **Outcome bucket** — one of `plan`, `clarify`, `reject`, `parse_error`,
  `error`. `parse_error` is set when the model's response cannot be parsed as
  a valid plan JSON; the runner exposes this distinct from `clarify` so the
  small-model JSON-emission failure mode does not hide inside the clarify
  bucket. The agent's `last_parse_error` attribute carries the underlying
  `ValueError` message for diagnostics.
- **Outcome accuracy** — `actual_outcome == expected_outcome`. The
  primary correctness metric. The `cheap` / `honest` verifier `score` only
  runs on rows with `outcome == "plan"`, so 100% pass rate in the report
  does *not* imply 100% outcome accuracy — always read the
  `outcome_accuracy` field from the per-backend JSON.
- **Intent score** — from `evaluator.compute_metrics`: tool accuracy, arg
  accuracy, clarify/reject PRF.
- **Verifier score** — 0.0–1.0 against `success_criteria` (only on `plan`
  rows).
- **Baseline outcome score** — same verifier run on `baseline_freeform_command`.
- **Time-to-artifact** — wall-clock ms from utterance to ready command
  string, aggregated as `mean_ms` / `p50_ms` / `p95_ms` / `max_ms` /
  `total_s`. The first row of each run is marked `is_warmup` and excluded
  so the model-load + cold-KV-cache cost does not skew the mean. Plan rows
  only — clarify/reject/error rows have different cost profiles.

Aggregated scoreboard answers: "knaif scored X, freeform baseline scored Y."
Per-tag breakdowns surface which feature classes drag the average.

### When two runs are comparable

Only compare runs that share **both** the same verifier and the same corpus
revision. Different verifiers measure different things (`cheap` checks routing;
`success` checks the artifact), and a grown corpus changes the mix — accuracy can fall
purely because the added rows are harder, with nothing regressed. A run that differs in
either dimension is a fresh baseline, not a data point in a trend; label it accordingly
in [`evals/INDEX.md`](../evals/INDEX.md) and archive rather than delete the superseded
one. Per-tag and per-row comparison stays valid across corpus growth where the rows
themselves are unchanged, and is usually what you actually want.

## Retrieval and the prompt the model actually sees

`run_corpus` applies `retrieve_tools()` per utterance and passes the result as
`registry_override` to `agent.infer()`, so the eval suite measures the same
prompt the agent uses in production (typically 5 retrieved tools instead of the
full registry). When `Skill.prompt_examples` is populated, examples are
also filtered to those whose plan steps overlap the retrieved tool set.

The retriever normalizes the query before scoring — lowercases and strips
combining diacritics (`comprimír` → `comprimir`) — and supports keyword
aliases in non-English languages. ffmpeg ships ES/DE/FR/RU aliases on every
intent tool.

Pass `--no-retrieval` to disable both per-utterance retrieval and example
filtering. This measures the full unfiltered prompt and is intended for
diagnostic A/B comparison only.

## CLI usage

```bash
# Cheap-mode run (mock LLM, no backend required)
uv run -m knaif.evalsuite run --skill ffmpeg --verifier cheap

# Limit to first 20 rows for a quick smoke test
uv run -m knaif.evalsuite run --skill ffmpeg --verifier cheap --limit 20

# Documents skill smoke test using the suite's mock backend fallback
uv run -m knaif.evalsuite fixtures regen --skill documents --force
uv run -m knaif.evalsuite run --skill documents --backends mock --verifier cheap --limit 1
uv run -m knaif.evalsuite run --skill documents --backends mock --verifier honest --limit 1

# Run with a specific backend from eval_backends.yaml
uv run -m knaif.evalsuite run --skill ffmpeg --config eval_backends.yaml --backends qwen3-4b --verifier cheap

# Honest mode (requires ffmpeg on PATH)
uv run -m knaif.evalsuite run --skill ffmpeg --config eval_backends.yaml --backends qwen3-4b --verifier honest --workers 4

# Compare two backends side-by-side
uv run -m knaif.evalsuite compare --skill ffmpeg --config eval_backends.yaml --backends qwen3-4b,phi4-mini --verifier cheap

# Diagnostic: measure the unfiltered prompt instead of the retrieved one
uv run -m knaif.evalsuite run --skill ffmpeg --config eval_backends.yaml --backends qwen3-4b --verifier cheap --no-retrieval

# Save snapshot for regression detection (output_diff is ffmpeg's acceptance-bar verifier)
uv run -m knaif.evalsuite run --skill ffmpeg --verifier output_diff --snapshot

# Regression check (exits 1 if any metric dropped > threshold)
uv run -m knaif.evalsuite regression --skill ffmpeg --threshold 0.02

# Show baseline command for a row
uv run -m knaif.evalsuite show-baseline --skill ffmpeg --id ffmpeg_042
```

The `just eval-stage` and `just eval-backends` recipes in `justfile` wrap the
common multi-backend invocations.

### Cross-skill: one model build against every skill

One shared model serves every skill, so the unit that matters when the *model* changes is
the whole matrix, not one skill. Three commands cover it.

**Sweep every active skill into one run folder.** The sweep owns the folder, so `--save` is
required; per-skill options (`--corpus`, `--fixture-dir`, `--snapshot`) are rejected because
each skill uses its own corpus and fixtures. Skills without `data/eval.jsonl`, and skills
marked `status: stale` in `skill.yaml`, are skipped and reported rather than failing the run.

```bash
uv run -m knaif.evalsuite run --all-skills --verifier cheap \
  --config eval_backends.yaml --backends qwen3-4b \
  --save evals/runs/<YYYY-MM-DD>_<label>_cheap
```

It writes the usual `{skill}_{backend}_{verifier}.json` per skill plus `matrix.{json,md}` —
skills × backends × verifier. `matrix.json` embeds a `meta` block (`label`, `date`,
`git_sha`, `git_branch`, `backends`) and each cell's corpus size, so a skill's history is
reconstructable from the run folders alone.

**Gate the sweep against every skill's committed snapshot.** `--current-run` is required —
see the false-green note below. A skill may override the global `--threshold` with a
`regression_threshold` field in its `eval_snapshot.json`. Exits non-zero if any skill
regresses.

```bash
uv run -m knaif.evalsuite regression --all-skills \
  --current-run evals/runs/<YYYY-MM-DD>_<label>_cheap
```

**Track one skill across model builds.**

```bash
uv run -m knaif.evalsuite trend --skill documents --last 5
```

`trend` reads the embedded `meta` from `matrix.json` files, not `evals/INDEX.md`.

The multi-skill fine-tuning loop that uses these is in
[TRAINING_DATA_GENERATION.md](TRAINING_DATA_GENERATION.md); wiring a *new* skill into the
sweep is a checklist in [TOOL_SCHEMA.md](TOOL_SCHEMA.md).

> **What this gate is for.** It catches **shared-model catastrophic forgetting** —
> fine-tuning on skill X's utterances, or adding a new skill to the training union,
> silently degrading skill Y. It does **not** measure cross-skill *retrieval interference*,
> because cross-skill retrieval does not exist: a caller names the skill up front and
> `retrieve_tools` ranks only that skill's registry. If a combined multi-skill router is
> ever built, measuring routing interference is a separate effort layered on top.

## Backend configuration

Backends are defined in `eval_backends.yaml` at the repo root. Two backend
types are supported:

```yaml
backends:
  # llama.cpp via GGUF file (resolved relative to the repo root)
  qwen3-4b:
    backend: llama_cpp
    options:
      path: models/Qwen3-4B-Q4_K_M.gguf
      description: Qwen3 4B Q4_K_M
      n_ctx: 4096
      n_gpu_layers: 99       # 99 = offload all layers to GPU
      n_threads: 8
      max_tokens: 4096
      json_mode: false       # disable for thinking-template models
      thinking_enabled: false # appends /no_think to system prompt (Qwen3)

  # Ollama (requires `ollama serve` running locally)
  gemma3-4b-ollama:
    backend: ollama
    model: gemma3:4b
    options:
      temperature: 0.0
      max_tokens: 4096
      json_mode: true
```

`json_mode: true` constrains the output to a valid JSON object. **Disable it for any
reasoning model** (Qwen3, DeepSeek-R1): the constraint demands JSON from the first token
while the template still wants to emit a reasoning preamble, so the two deadlock.

**The fix differs by backend — they are not interchangeable:**

| Backend | Reasoning model settings | Mechanism |
|---|---|---|
| `llama_cpp` | `json_mode: false`, `thinking_enabled: false` | appends `/no_think` to the system prompt |
| `ollama` | `json_mode: false`, **`thinking_enabled: true`**, `max_tokens: 2048+` | Ollama moves reasoning to `message.thinking`, leaving `content` clean |

On Ollama, `thinking_enabled: false` is actively harmful: it sends `think: false`, which
does not stop the model reasoning — it only stops Ollama *separating* the reasoning, so it
lands in `content` and destroys the JSON. Ollama also charges reasoning against
`max_tokens`, so the 256 default is spent before the answer starts. Full measurements:
[INFERENCE.md → Reasoning models on Ollama](INFERENCE.md#reasoning-models-on-ollama--leave-thinking-on).

The `gemma3-4b-ollama` entry above keeps `json_mode: true` because Gemma 3 is not a
reasoning model — the setting is safe there.

Use `--backends <name1>,<name2>` to select a subset. Omit to run all backends.

## File layout

```
python/core/knaif/evalsuite/      Framework modules (corpus, runner, scoring, report, snapshot, cli)
skills/<name>/eval/   Per-skill verifiers, fixtures, and playbooks
skills/<name>/data/   eval.jsonl corpus + eval_snapshot.json (acceptance bar)
```

Fixtures are generated into `sandbox/fixtures/<skill>/` (gitignored) on the first
honest-mode run or via `just eval-fixtures <skill>`; the generator skips cached files.

## Adding a new skill

1. Create `skills/<new_skill>/eval/README.md` using the ffmpeg one as a template.
   It covers: fixtures, baseline authoring, local eval, big-LLM eval, reporting, and
   the `success_criteria` schema for the new skill's domain.
2. Implement `VERIFIERS` in `verifiers.py` (at minimum `cheap`).
3. Seed `eval.jsonl` with 5–10 rows and validate them (see Phase A–B in the README).
4. Grow the corpus to ~300 rows using the bulk generation playbook in the README.
5. Commit `eval.jsonl` and the snapshot.
