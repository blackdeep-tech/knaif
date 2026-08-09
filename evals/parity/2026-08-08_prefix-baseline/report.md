# Native pre-fix baseline — ffmpeg, 2026-08-08

P2b of [docs/plans/2026-08-08-native-python-planning-parity.md](../../../docs/plans/2026-08-08-native-python-planning-parity.md).
The "before" snapshot of native planning, captured **prior to any Workstream Q change**, so the
eval-parity lane (S2) has a real gap to measure across rather than a single green number.

## What was run

| | |
|---|---|
| binary | `dist/staging/knaif-1.1.0-windows-x64/bin/knaif.exe` (v1.1.0, ships `ggml-vulkan.dll`) |
| model | `models/knaif-qwen3-4b-v1-q4_k_m.gguf` (the promoted `knaif-qwen3-4b-v1`) |
| corpus | `skills/ffmpeg/data/eval.jsonl` — 314 rows, **847 utterances** (every utterance, not just the first) |
| command | `knaif plan --skill ffmpeg --batch utterances.txt --model <gguf>` |
| hardware | RTX 3070 Laptop (Ampere), Vulkan |

**No build was required, and that is a checked claim rather than a convenience.** The v1.1.0 tag
and this branch's pre-P0 state are planner-identical: `git diff v1.1.0..HEAD~3` shows **zero `.rs`
changes anywhere under `native/crates/`**, the only `apps/cli/src/main.rs` delta is two `--help`
strings on `RunArgs`, and `skills/ffmpeg/tools.yaml` / `prompt.yaml` are untouched. The staged
release therefore builds the same prompt and runs the same planner as the working tree did before
P0. It also avoided a llama.cpp rebuild that could not have happened here anyway — `cmake` is not
on `PATH` (it lives inside the VS 18 install) and `ninja` is absent.

## Results

Scored on **routing only**. `plan --batch` emits validated plans, not rendered commands, so the
shared `cheap` / `success` verifiers cannot consume this output — that gap is exactly S1b, and it
is confirmed here in practice rather than by reading.

| metric | value |
|---|---|
| outcome accuracy | **0.908** (769/847) |
| expected tool present in plan | **0.861** (729/847) |
| error envelopes | 5 |
| step counts | 1 step ×726, 2 ×85, 3 ×31, 0 ×5 |

### Chain rows — the headline

| | |
|---|---|
| chain-tagged utterances | 41 |
| produced ≥2 steps | **39 (95.1%)** |
| produced full expected length | **38** |

**The plan's founding symptom does not reproduce.** It was opened on the observation that native
"would not produce a multi-step plan at all". On this corpus the native runtime chains correctly
almost everywhere, including all four repetitions of each `chain3` row:

```
ffmpeg_hard_001 → convert_video, resize_video, strip_audio
ffmpeg_hard_002 → trim_video,    resize_video, compress_video
ffmpeg_hard_003 → rotate_video,  resize_video, compress_video
ffmpeg_hard_006 → adjust_speed,  resize_video, compress_video
```

The three non-full chains are **not** refusals to chain: two are validation errors (below) and one
`chain3` row produced a correct 2-step plan instead of 3.

### The 5 error envelopes are one bug, not five

Every one is the model emitting an argument the tool does not declare, rejected by validation:

| row | rejected arg |
|---|---|
| `ffmpeg_123`, `ffmpeg_hard_017` | `adjust_speed.include_audio` |
| `ffmpeg_134` | `adjust_volume.target_sample_rate` |
| `ffmpeg_174` | `convert_video.bitrate` |
| `ffmpeg_hard_014` | `convert_video.audio_format` |

Two of them (`hard_014`, `hard_017`) are the *only* reason those chain rows show 0 steps. This is
a prompt/schema-coverage issue, unrelated to retrieval or example selection, and worth its own
item — the utterances ("with no sound", "500 kbps Bitrate") are reasonable requests the schema has
no argument for.

## Comparison to Python — same corpus, same GGUF, same scorer

Added 2026-08-09 once `llama_cpp` was reachable (the venv already had the cu124 wheel; only the
`os.add_dll_directory` wiring in `orchestrator.py` makes it loadable, so a bare `import llama_cpp`
fails and is *not* evidence of a broken install). Python ran the identical 847 utterances against
the identical GGUF **pinned by path**, emitting the same envelope shape, so one scorer grades both.

| metric | native | python | delta |
|---|---|---|---|
| outcome accuracy | **0.908** | 0.903 | **+0.005** |
| expected tool present | **0.861** | 0.857 | **+0.004** |
| invalid plans | **5** | 13 | −8 |
| chain full length | 38/41 | 39/41 | −1 |
| **identical tool sequence** | **783/847 — 92.4%** | | |

**Native is at parity, marginally ahead, and inside noise** (0.5 pp ≈ 4 utterances). It carries a
system prompt 1.77× longer with 13 tools and 28 examples against Python's 5 and 5, and pays
nothing measurable for it.

### The first scoring of this was wrong, in the plan's own characteristic way

The initial pass showed native *behind* — 0.908 vs 0.916, 5 errors against 0. That comparison was
invalid: **`agent.infer()` does not surface validation failures.** `agent.py:950` deliberately
falls through on a validation error and returns the invalid plan (*"so execute_plan surfaces the
error"*), while native's `plan --batch` validates inline and emits `{"plan":[],"error":…}`. The two
lanes were failing at different stages, so Python's invalid plans were scored as successes.

Validating both with `validate_plan` flipped the sign and showed Python producing **13** invalid
plans to native's 5, overlapping on only 2.

**That is an unrecorded runtime divergence and a user-visible one:** native reports an invalid plan
at plan time; Python returns one that fails later during execution. Neither `parity_check.py` nor
the contracts drafted in Workstream R would catch it — it is about *when* an error surfaces, not
what the plan contains.

## Files

All committed. `evals/**` previously ignored the envelopes as per-run scratch; a narrow negation
(`!evals/parity/**/*.jsonl`, `*.txt`) now keeps cross-runtime baselines, because a summary cannot
be re-graded — S2 must re-score these same envelopes with a verifier that does not exist yet, and
the pre-fix side stops being reproducible the moment the runtime changes.

| file | what |
|---|---|
| `utterances.txt` | the 847 utterances, in order, LF |
| `index.jsonl` | envelope line → corpus `id` + `utterance_idx` |
| `native_plans.jsonl` | native envelopes (v1.1.0, Vulkan) |
| `python_plans.jsonl` | Python envelopes (`CommandAgent.infer` + `retrieve_tools`, CUDA) |
| `score.json` | both lanes scored identically, plus per-row parity |
