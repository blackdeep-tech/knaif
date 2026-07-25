# ffmpeg Retrieval-Miss Triage

**Date:** 2026-07-02
**Plan:** [../plans/2026-07-02-qwen3-finetuning-pass3.md](../plans/2026-07-02-qwen3-finetuning-pass3.md) (Task 3)
**Method:** replay the real inference retrieval (`python/core/knaif/agent.py:1236` →
`retrieve_tools(utterance, registry)`, defaults `top_k=5`, `min_score=0`) over every
`skills/ffmpeg/data/eval.jsonl` utterance whose `expected_tool` is a real skill tool, and
flag rows where the expected tool is **not** in the retrieved top-5. Core-control tools
(`clarify`/`reject`/`done`/`wait_for_confirmation`) are excluded — they are merged
automatically and always available, so an `expected_tool` of `clarify` is a routing/policy
decision, not a retrieval failure.

## Headline

Of **613** utterances with a single expected skill tool, **114** never retrieve that tool:

| category | misses | disposition |
|---|---:|---|
| non-CJK keyword gap | 70 | **cheaply fixable** with keyword additions |
| CJK (whitespace tokenization) | 44 | **blocked** on segmentation work at the time; fixed 2026-07-02 by CJK n-gram tokenization ([retrieval-overhaul](../plans/2026-07-02-retrieval-overhaul.md), Task 2) |

This corroborates the pass-2 audit's ~129 retrieval-miss estimate (that count was per-model
over failed rows; this is the upstream corpus-wide capacity metric). **These 114 rows are not
model-capacity failures** — the correct tool is never shown to the model — and must be
separated from the hard/full metrics before attributing residual error to the model.

## Non-CJK misses by expected tool (the fixable set)

| expected_tool | misses | current keywords lack | representative utterance |
|---|---:|---|---|
| `adjust_volume` | 18 | `loud`, `loudnorm`, `db`, `gain`, DE `lauter`/`leiser` | "make clip.mp4 twice as loud"; "loudnorm clip.mp4"; "clip.mp4 um 6dB lauter machen" |
| `compress_video` | 14 | `tiny`, `reduce`, `size`, `bitrate`, `kbps`, `kilobits`, FR `réduire` | "make clip.mp4 tiny for messaging"; "encode clip2.mp4 at 500 kilobits per second" |
| `adjust_speed` | 11 | `fast`, `faster`, `slow`, `slower` | "make clip.mp4 play twice as fast"; "play clip.mp4 at 0.5x" |
| `convert_video` | 8 | `lossless`, DE `verlustfrei` (rest are chain rows) | "create a lossless copy of clip.mp4"; chain hard_001/007 |
| `create_thumbnail` | 6 | DE `einzelbild`/`bild` | "Einzelbild bei 5 Sekunden aus clip.mp4 extrahieren" |
| `trim_video` | 6 | (mostly audio-in-range chain rows) | "extract just the audio from 3 to 5 seconds of clip.mp4 as mp3" |
| `concat_video` | 4 | DE `verbinden`, RU/BG (non-CJK multilingual) | "verbinde clip.mov und clip.mp4" |
| `extract_audio` | 2 | `flac`, `opus` | "export audio.mp3 as flac" |
| `resize_video` | 1 | (chain row) | hard_005 |

Note `adjust_speed` already carries `speed` but retrieval tokenizes on exact words, so
"twice as **fast**" / "play at 0.5x" score 0 against it. Same pattern for `adjust_volume`
(`louder` present, `loud`/`loudnorm` absent).

## CJK misses by expected tool (blocked)

`convert_video` 11 · `extract_audio` 8 · `compress_video` 7 · `prepare_for_platform` 6 ·
`create_thumbnail` 4 · `adjust_volume` 4 · `adjust_speed` 3 · `concat_video` 1. These are the
whitespace-tokenizer failures on Chinese/Japanese/Korean; keyword additions do not help
because the query never splits into matchable tokens. Excluded from data/model decisions
until segmentation lands.

## Recommended keyword additions (English + Latin-script multilingual)

Apply to `skills/ffmpeg/tools.yaml`, then re-run this triage and confirm (a) the non-CJK
miss count drops and (b) no **new** mis-routing appears on previously-correct rows:

- `adjust_speed`: `fast`, `faster`, `slow`, `slower`
- `adjust_volume`: `loud`, `loudnorm`, `gain`, `db`, `lauter`, `leiser`, `normalisieren`
- `compress_video`: `tiny`, `reduce`, `size`, `bitrate`, `kbps`, `kilobits`, `réduire`
- `convert_video`: `lossless`, `verlustfrei`
- `extract_audio`: `flac`, `opus`, `wav`
- `create_thumbnail`: `einzelbild`, `bild`
- `concat_video`: `verbinden`, `zusammenfügen`

Several `convert_video`/`trim_video`/`resize_video` misses are **multi-step chain rows** where
the expected tool is only the first of several verbs competing for the top-5; those are chain
composition rows, not pure keyword gaps, and should be left to the training data rather than
keyword-forced.

## Caution

Retrieval is shared by production and every eval. Applying these keywords shifts the routing
baseline, so it must be its own committed change with a clean re-eval — do **not** interleave
it with the model-confirmation runs (Task 2), or the before/after model comparison is
confounded by a retrieval change.

## Applied (2026-07-02)

Added the recommended keywords to `skills/ffmpeg/tools.yaml` (dropped `reduce` from
`compress_video` — the registry enforces keyword uniqueness and `adjust_volume` already owns
it; `size`/`tiny`/`shrink` cover the "reduce the size" utterances). Result:

- **non-CJK misses 70 → 46** (−24); total 114 → 90; CJK unchanged (44, needs segmentation).
- After-set ⊂ before-set → **no new mis-routing**.
- Re-eval of the promoted `qwen3-4b-v3` on ffmpeg: full **0.898 → 0.903** (+0.48pt), hard/chain3
  flat — recovers most of the fine-tune's full-corpus cost. `458` ffmpeg+planner tests pass.

The remaining 46 non-CJK misses are multilingual (Cyrillic/German/Spanish) keyword gaps,
multi-step chain rows (expected tool is only the first verb — left to training, not
keyword-forced), and genuine routing ambiguities (`encode … quality` splits between
convert/compress). Lower priority.

## Reproduction

The original scratchpad script was not retained. Its maintained replacement is
`python/core/knaif/evalsuite/retrieval.py`, exposed through
`uv run -m knaif.evalsuite retrieval --skill ffmpeg`; it replays `retrieve_tools` and
reports misses by script category. Deterministic; no GPU.
