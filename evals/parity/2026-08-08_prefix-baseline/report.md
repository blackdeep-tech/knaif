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

## Comparison to Python — indicative only

The committed `skills/ffmpeg/data/eval_snapshot.json` (Python, **cheap** verifier) records outcome
**0.933** and tool accuracy **0.845**, against this run's 0.908 and 0.861.

**Do not read that as a parity measurement.** Three things differ: the snapshot covers 297 rows
against today's 314/847, the `cheap` verifier grades a rendered command string while this grades a
plan envelope, and "tool accuracy" there is not defined identically to "expected tool present"
here. The numbers are close enough to say native is not dramatically behind, and not comparable
enough to say anything sharper. Producing the sharp version is what Workstream S is for.

A same-corpus Python run is currently blocked: `llama-cpp-python` is not installed in the venv
(`just install-llama`).

## Files

`score.json` is committed. `native_plans.jsonl` (847 envelopes), `index.jsonl` (envelope → corpus
id mapping) and `utterances.txt` are **gitignored** under `evals/**` as per-run scratch — see the
plan's P2b note, because for this run they are arguably the durable artefact: S2 has to re-grade
them with an adapter that does not exist yet, and a summary cannot be re-graded.
