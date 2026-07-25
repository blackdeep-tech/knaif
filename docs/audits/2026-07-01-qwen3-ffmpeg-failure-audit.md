# Qwen3 ffmpeg Failure Audit

**Date:** 2026-07-01
**Plan:** [../plans/2026-07-01-qwen3-ffmpeg-max-results.md](../plans/2026-07-01-qwen3-ffmpeg-max-results.md)
**Verifier:** `success`
**Corpus:** current `src/skills/ffmpeg/data/eval.jsonl` (846 utterance-evals, 55 hard)

## Models Audited

| model | run file | outcome | hard outcome |
|---|---|---:|---:|
| `1.7B-ft-Q6-union` | `2026-06-30_1.7b-quant-sweep_success/ffmpeg_qwen3-1.7b-ft-q6_success.json` | 0.881 | 0.873 |
| `1.7B-ffmpegv1-Q6` | `2026-07-01_ffmpeg-only-v1_success/ffmpeg_qwen3-1.7b-ffmpegv1-q6_success.json` | 0.868 | 0.873 |
| `4B-base-Q4` | `2026-06-29_finetune-pre_success/ffmpeg_qwen3-4b-base-q4_success.json` | 0.905 | 0.909 |
| `4B-ft-Q4` | `2026-06-30_finetune-post_success/ffmpeg_qwen3-4b-ft-q4_success.json` | 0.895 | 0.945 |

## Bucket Summary

Counts are utterance-level failures. Buckets are heuristic, based on expected outcome,
expected tools from the corpus, actual plan, validation error, and tags.

| bucket | 1.7B union | 1.7B ffmpeg-only | 4B base | 4B ft |
|---|---:|---:|---:|---:|
| over-clarify | 40 | 47 | 29 | 32 |
| clarify missed | 21 | 24 | 24 | 16 |
| wrong enum/arg | 11 | 11 | 7 | 12 |
| bad chain / invalid composed args | 7 | 9 | 4 | 5 |
| unsafe/impossible not rejected | 8 | 9 | 12 | 9 |
| over-reject | 5 | 5 | 1 | 5 |
| wrong / hallucinated tool | 1 | 0 | 0 | 10 |
| other / manual review | 8 | 7 | 3 | 0 |
| **total failures** | **101** | **112** | **80** | **89** |

## Hard-Slice Failures

The hard-slice failures are the main fine-tuning signal. They are mostly chain composition
failures, not cross-skill contamination.

| bucket | 1.7B union | 1.7B ffmpeg-only | 4B base | 4B ft |
|---|---:|---:|---:|---:|
| bad chain / invalid composed args | 3 | 5 | 2 | 3 |
| clarify missed | 2 | 2 | 3 | 0 |
| wrong enum/arg | 1 | 0 | 0 | 0 |
| other / parse/manual review | 1 | 0 | 0 | 0 |
| **hard failures** | **7** | **7** | **5** | **3** |

Hard examples to target with neighboring training rows:

| id | failing models | issue | example |
|---|---|---|---|
| `ffmpeg_hard_001` | 1.7B union, 1.7B ffmpeg-only, 4B base | 3-step convert-resize-strip compressed into invalid args such as `resize` / `target_size` on `convert_video` | "convert clip.mov to mp4, scale it down to 480p, and remove the audio track" |
| `ffmpeg_hard_005` | all except 4B base | 3-step resize-compress-strip collapsed into unsupported resize args or missing strip step | "resize clip_4k.mp4 to 1080p, shrink the file and drop the audio" |
| `ffmpeg_hard_007` | 4B base, 4B ft | convert-resize-compress collapsed into invalid `convert_video` args | "convert clip.mov to mp4, scale it to 360p, then compress it" |
| `ffmpeg_hard_009` | 1.7B union | audio extract routed through `strip_audio` with `audio_format` | "rip just the audio from clip.mp4 as mp3" |
| `ffmpeg_hard_011` | 1.7B union, 1.7B ffmpeg-only, 4B base | cue-less "first bit/start/part" should clarify, but models emit `trim_video` | "extract the first bit of clip.mp4" |
| `ffmpeg_hard_014` | 4B ft | convert-strip collapsed into unsupported `audio_track` arg on `convert_video` | "convert clip.mov to mp4 and remove its audio" |

## Common Full-Corpus Failures

These rows fail across several models and should be split into training targets vs corpus or
retrieval cleanup before generating data.

| id | total failures across audited models | interpretation |
|---|---:|---|
| `ffmpeg_252` | 16 | Corpus/clarify-policy issue: several utterances say "three clips" without naming files, but expected outcome is a concrete concat plan. Do not train on these until the expected behavior is decided. |
| `ffmpeg_272` | 13 | Clarify boundary: "adjust/change audio levels" should ask for amount/normalization target. Good contrastive data target. |
| `ffmpeg_203` | 13 | Clarify boundary: "rotate/flip" without direction should clarify. Good contrastive data target. |
| `ffmpeg_114`, `ffmpeg_113` | 9 / 7 | Audio-only conversion should route through `extract_audio`, not hallucinated `convert_audio` or document-like conversion. Good targeted data. |
| `ffmpeg_244` | 8 | Concat with normalization/resolution preservation is hard; may need either better training or a corpus capability check. |
| `ffmpeg_236`, `ffmpeg_090` | 8 / 6 | Thumbnail/poster extraction and size/scale phrasing. Mixed retrieval and routing signal. |
| `ffmpeg_216` | 8 | Impossible media request: thumbnail from audio should reject. Good reject contrastive target. |
| `ffmpeg_174` | 8 | Bitrate phrasing for compression; some retrieval misses. Add keywords or avoid as pure fine-tune target until retrieval is healthy. |

## Retrieval Check

A separate retrieval pass found 129 utterances where at least one expected tool is not
retrieved. Many are CJK rows already known to be blocked by whitespace tokenization, but
there are also keyword gaps such as bitrate/compress, volume, and some multilingual
thumbnail/compress terms.

Failed rows with retrieval misses:

| model | failed retrieval-miss utterances | hard subset |
|---|---:|---:|
| 1.7B union | 18 | 2 |
| 1.7B ffmpeg-only | 24 | 2 |
| 4B base | 14 | 0 |
| 4B ft | 18 | 1 |

Implication: do not treat retrieval-miss rows as model-capacity failures. Either fix
retrieval/keywords first or exclude those rows from data-generation decisions. The hard
slice is still mostly trainable because its failures are dominated by invalid chain
composition.

## Data Guidance

For ffmpeg-v3, prioritize:

1. 3-step chain rows that force separate valid tools with explicit intermediate outputs.
2. Contrastive clarify rows for vague rotate/flip, vague volume adjustment, and cue-less
   "first bit/start/part" extraction.
3. Audio conversion rows that route audio-only and video-audio phrasing through
   `extract_audio`, never a hallucinated `convert_audio`.
4. Reject rows for impossible media transformations, especially media-type mismatches like
   thumbnails from audio.
5. Small keyword/retrieval fixes before training on bitrate, CJK, and some multilingual
   thumbnail/compress rows.

Avoid using `ffmpeg_252` as a fine-tuning target until the corpus policy is fixed: unnamed
"three clips" should probably clarify rather than invent filenames.
