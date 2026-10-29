# "Convert to mp4 and make it smaller", rows first — 2026-10-29

Manual test of the rebuilt native binary (owner, 2026-10-29): `k ffmpeg "convert clip_tiny.mov to
mp4 and make it smaller"` answered "I can't convert video that way — that isn't supported". The
4B's first reply was `compress_video {inputs: [clip_tiny.mov], container: mp4}`. compress_video had
no `container` arg, so the plan failed validation. The retry put an invented `target_size_mb: 15`
on `convert_video`, which the unsupported-args gate turned into that question. This is
`ffmpeg_size_003` #0, the 4B's recorded miss in `2026-10-29_relock-compress-result_success`.

The fix (owner's choices, 2026-10-29):

- `satisfied_args` (tools.yaml, both runtimes): compress_video accepts `container: mp4` and nothing
  else, and not beside a `target` (archive writes mkv). The prompt does not list it, so no reply
  changes. Any other container is still "isn't supported".
- A compress that names its format and is not smaller than a source of another format delivers the
  conversion: the stream copy convert_video would write, under the compress's output name, reported
  as kept. If that copy fails, the encode is delivered.

Both shipped models, Python runtime, CUDA, `success` verifier, on `corpus.jsonl`: every row whose
expected tools or recorded plans (either model) use compress_video, plus `ffmpeg_045`. That makes
56 rows and 163 utterances. `run_all.sh` reproduces it. The tree was uncommitted on `0c12c5c`
(`DIRTY_FILES.run`).

## Result

Against `2026-10-29_relock-compress-result_success` (`comparison/`, `a` = this run):

| Model | Same grade | Same plan | Same command | Changed |
|---|---|---|---|---|
| 4B | 162/163 | 163/163 | 163/163 | `ffmpeg_size_003` #0: clarify → plan, knaif 1.0 (`clip_tiny_compressed.mp4` delivered, not larger than the source) |
| 1.7B | 163/163 | 163/163 | 163/163 | none |

Before the run, the locked boards predicted this: across both models' 2,132 recorded ffmpeg replies
and both safety boards, that utterance is the only compress_video step with a `container`.

## Superseded

`superseded_drop_rule/`: the first attempt dropped `container: mp4`. The row became a plan but wrote
nothing, because the starved clip cannot shrink and a compress that is not smaller leaves things as
they were. It scored knaif 0.75 with no mp4. See `WHY.txt` there.
