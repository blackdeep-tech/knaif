# T6a control, third take — the run whose value was not its numbers

**2026-09-13** · verifier `success` · policy v2 · backend `qwen3-4b-sft-v3-flat-q4`
· **superseded on arrival: the product changed again after it, see below**

Taken after two fixes from the [v2 audit](../2026-09-13_t6a-control-v2_success/report.md):
`adjust_speed` no longer renders a video recipe for an audio-only input (ffmpeg_226), and
`concat_video`'s tool description now names the `target_resolution` values `first`/`second`
that it has always accepted (ffmpeg_244).

## It went down, not up

| | v2 | v3 |
|---|---|---|
| ffmpeg `outcome_accuracy` | 0.90247 | **0.90012** (−0.00235, 2 utterances) |
| ffmpeg `avg_knaif_score` | 0.98189 | 0.98193 |
| documents | 0.96341 | 0.96341 (unchanged again) |

**10 rows flipped: 4 fixes, 6 regressions.** Decoding is greedy — `temperature: 0.0` in
`contracts/runtime/generation.yaml` — so this is **not sampling noise**. Adding 105 characters
to one tool description changed the model's output on ten unrelated utterances. That is the
number to remember about prompt edits on a 4B: they are not local.

**Both targeted fixes worked, and are confirmed on real artifacts:**

- `ffmpeg_226` **0.5 → 1.0** — the mp3 stayed an mp3. Unit tests pin the rendered command;
  only an executing run can show the artifact carries the right codec.
- `ffmpeg_244#0` **error → plan**, emitting `target_resolution: "second"` — precisely the
  value the description now teaches, chosen for precisely the right utterance.

## What it actually bought: a third defect, deeper than the first two

The description also nudged three utterances from the `inputs` form of `concat_video` into the
`base`/`append` form — and **all three died**:

```
Nothing was written into output file, because at least one of its streams
received no packets.   (exit -22)
```

`ffmpeg_244#1` carries no `target_resolution` at all, so this is not the new hint misfiring.
The two forms of the same operation rendered differently:

```
inputs:      -i <sandbox>/clip.mov -i <sandbox>/clip.mp4
             -filter_complex [0:v]scale=1280:720,fps=25[v0];[0:a]aresample=44100[a0];…

base/append: -i clip.mp4 -i clip.mov
             -filter_complex [0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]
```

Two faults, one cause. The `inputs` form is expanded with `resolve_inputs` + `inspect_media`;
`base`/`append` can be expanded with neither, because they may be `$var` references that only
exist at runtime, so `RunConcatStep` was left to self-probe. It never resolved the filenames
first — so the probe looked for `clip.mp4` relative to the process cwd, found nothing, and
emitted **no normalization**. `concat` demands identical width, height and sample rate, so any
two clips that differ produce an empty file and −22. `output` in the same handler was already
being resolved against the sandbox; the inputs simply were not.

Fixed, plus a second half: the self-probe was skipped entirely under `dry_run`, so the
*preview* of a base/append concat showed a different filter graph from the one execution would
build. ffprobe reads and does not write, and `ctx.dry_run` is about side effects — it now
probes whenever the file is really there, and falls back to dummy info only when it is not.
**The plan a user approves has to be the plan that runs.**

⚠️ **The native runtime already had this right.** `expand_concat` in `run.rs` resolves and
boundary-checks every input before probing, citing audit item R1: *"the raw list must not reach
the rendered command"*. The port was more correct than its reference, and the correction was
never carried back. Worth knowing that parity work can run in that direction.

## Consequence for sequencing

This run's instrument no longer exists — the concat fix landed after it. **Do not compare T6b
against these numbers.** The pattern across v1 → v2 → v3 is that each audit finds another
defect, and each fix invalidates the control, so the next control arm should be taken **once,
immediately before T6b**, and not before. Any product change after that point re-opens it.
