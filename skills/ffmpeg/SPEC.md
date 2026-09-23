# FFmpeg Skill Specification

The FFmpeg skill is a local media workflow assistant. It lets a small model choose a narrow, model-visible intent tool, then lets deterministic Python code expand that intent into validated FFmpeg and ffprobe workflow steps.

The model must not generate raw FFmpeg commands.

## System Requirements

This skill shells out to the system `ffmpeg` and `ffprobe` binaries — they must be
installed and on `PATH`. The skill bundles no binaries of its own.

```bash
sudo apt install ffmpeg     # Debian / Ubuntu (provides ffmpeg + ffprobe)
brew install ffmpeg         # macOS
winget install Gyan.FFmpeg  # Windows
```

If the binaries are missing, `_deps.py` raises a clear error at execution time
(`ffmpeg not found on PATH. Install ffmpeg to use the ffmpeg skill.` / the same for
`ffprobe`). Dry-run (`dry_run=True`) and the `cheap` eval verifier are text-only and
do **not** require the binaries; real execution and the `honest` verifier do.

## Model Contract

The model emits the standard `knaif` plan envelope:

```json
{
  "plan": [
    {
      "tool": "prepare_for_platform",
      "args": {
        "inputs": ["selected_videos"],
        "platform": "whatsapp",
        "quality": "visually_good",
        "preview": true
      }
    }
  ]
}
```

The strings `selected_videos` and `current_file` are prompt-level placeholders used in examples. Real callers should pass concrete paths or provide application context that resolves those placeholders before execution.

## Public Tools

Implemented model-visible tools:

```text
prepare_for_platform
compress_video       # optional: target_size_mb (a CEILING - see Size targets below)
convert_video
resize_video          # optional: fit (crop|pad|stretch), aspect ("aw:ah")
trim_video
extract_audio
create_thumbnail
concat_video
rotate_video
reverse_video
strip_audio
adjust_speed
adjust_volume
clarify
reject
```

### Size targets

`compress_video`'s `target_size_mb` is a **ceiling, not a target**. It is rendered as x264
capped CRF — the quality profile's `-crf` still drives the encode, and `-maxrate`/`-bufsize`
stop the result exceeding the size that was asked for. A small clip therefore stays small: a
200 KB file does not grow to fill a 20 MB ceiling.

**The platform profiles' `default_target_size_mb` is deliberately NOT wired to this.**
`email.yaml` declares `20` as an attachment cap, and applying it automatically was measured
and rejected on 2026-09-16: a 20 MiB ceiling gives a 20-minute video 36 kbit/s and a 27-minute
one 2 kbit/s, and past roughly 28 minutes it cannot hold 96k of audio at all, so the refusal
below would turn a `plan` into an `error` for a size the user never asked for.

The line is between a requirement and an aspiration. An explicit `target_size_mb` is the user
saying "under 500 KB", and refusing an impossible one beats silently producing 3 MB. A profile
default is a hint attached to a destination, and must never refuse a request on its own
authority. Wiring it would need a bitrate floor — cap while the result stays watchable, skip
otherwise — and a fixture longer than ten seconds to test against; every corpus fixture today
sits in the range where the cap is inert, so the eval cannot see this failure at all.

The cap needs the source duration — a size only becomes a bitrate once there is a length to
divide by. When the duration is unknown the encode falls back to plain CRF rather than guessing.
When the requested size cannot even hold the audio track, the request is refused with a message
saying so, because silently emitting a floored bitrate would produce a file several times the
requested size while reporting success.

Both constants (5% headroom, `bufsize = maxrate`) are measured; see `_SIZE_CAP_HEADROOM` in
`python/_engine.py`.


13 model-visible media intent tools (+ clarify/reject control tools from core).

Each public media tool is declared in `skills/ffmpeg/tools.yaml` as `destructive` because it can write output files. Execution requires `dry_run=True` or `confirmed=True`.

## Internal Workflow Tools

Implemented internal tools:

```text
resolve_inputs
inspect_media
load_platform_profile
load_quality_profile
build_recipes
render_preview_command
run_preview
verify_preview
wait_for_confirmation
render_batch_commands
run_batch
run_concat
verify_outputs
generate_report
```

Internal tools are declared with `internal: true` so they can be emitted by expanders but are hidden from the model prompt.

## Package layout

The skill is a Python package. `handlers.py` is a thin entry point that assembles
`FFmpegSkill` and re-exports the package; behavior lives in `steps.py` / `intents.py`,
pure logic in `_engine.py`, the ffmpeg/ffprobe shell-out in `_deps.py`, and
reporting in `_reporting.py`. Pure lookup/vocab tables (encoder/codec maps, platform
aliases, scale presets, container/image sets, volume words) live in `vocab.yaml` as
declarative data — the single source of truth for both the Python runtime and the
future native/Rust runtime (dual-runtime plan, Risk #1 mitigation b). Algorithms stay
in code per runtime.

## Workflow Expansion

`skills/ffmpeg/handlers.py` defines `Intent` tool classes. An `Intent.expand()` maps one public tool to a deterministic multi-step plan.

Example for platform preparation:

```json
{
  "plan": [
    { "tool": "resolve_inputs", "args": { "paths": ["clip.mov"] }, "output": "$files" },
    { "tool": "inspect_media", "args": { "files": "$files" }, "output": "$probes" },
    { "tool": "load_platform_profile", "args": { "platform": "whatsapp" }, "output": "$platform_profile" },
    { "tool": "load_quality_profile", "args": { "quality": "visually_good" }, "output": "$quality_profile" },
    {
      "tool": "build_recipes",
      "args": {
        "probes": "$probes",
        "platform_profile": "$platform_profile",
        "quality_profile": "$quality_profile",
        "options": { "mode": "platform", "platform": "whatsapp" }
      },
      "output": "$recipes"
    },
    { "tool": "render_preview_command", "args": { "recipes": "$recipes" }, "output": "$preview_cmd" },
    { "tool": "run_preview", "args": { "command": "$preview_cmd" }, "output": "$preview_run" },
    { "tool": "verify_preview", "args": { "preview_output": "$preview_run" }, "output": "$preview_meta" },
    {
      "tool": "wait_for_confirmation",
      "args": {
        "prompt": "Apply these settings to all 1 input(s)?",
        "preview": "$preview_meta"
      }
    },
    { "tool": "render_batch_commands", "args": { "recipes": "$recipes" }, "output": "$batch_cmds" },
    { "tool": "run_batch", "args": { "commands": "$batch_cmds" }, "output": "$batch_outputs" },
    { "tool": "verify_outputs", "args": { "outputs": "$batch_outputs" }, "output": "$verifications" },
    { "tool": "generate_report", "args": { "outputs": "$verifications" }, "output": "$report" }
  ]
}
```

If `preview` is false or omitted for tools that do not default to preview, the preview and confirmation block is skipped.

## Profiles

Profiles live under `skills/ffmpeg/profiles/`.

Platform profiles define compatibility constraints such as:

- container
- video encoder
- audio codec
- pixel format
- maximum width and height
- fast-start behavior

Quality profiles define output tradeoffs such as:

- CRF
- encoder preset
- audio bitrate

The model chooses controlled values such as `platform: whatsapp` and `quality: visually_good`; handlers load the profile files and render commands deterministically.

## Recipe And Command Rendering

`build_recipes` combines media probes, platform profiles, quality profiles, and tool options into recipe dicts. The renderer turns recipes into argv lists such as:

```text
ffmpeg -y -i clip.mov -vf scale='min(1280,iw)':-2 -c:v libx264 -crf 23 -preset medium -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart clip_whatsapp.mp4
```

Commands are passed to `subprocess.run()` as argv lists, not shell strings.

## Preview And Confirmation

Preview-enabled workflows:

1. render a short preview command
2. run the preview command
3. verify the preview output
4. call `wait_for_confirmation`
5. stop if confirmation is declined
6. render and run batch commands only after approval

`wait_for_confirmation` uses `HandlerContext.confirm(prompt, preview)`, which delegates to the agent's optional `confirmer` callback or falls back to the `confirmed` flag.

## Outputs

Handlers derive new output paths instead of overwriting originals. Common suffixes include:

- `_<platform>` for platform preparation
- `_compressed`
- `_converted`
- `_resized`
- `_trimmed`
- `_audio`
- `_thumb`
- `_preview`

### Which container an output gets

When the request names no container:

| Operation | Output container |
|---|---|
| **Edits** — `trim_video`, `resize_video`, `rotate_video`, `strip_audio`, `adjust_speed`, `adjust_volume`, `reverse_video` | the extension of an explicit `output`, else **the input's own extension** (`clip.mkv` → `clip_resized.mkv`) |
| **Delivery** — `compress_video`, `prepare_for_platform` | `mp4`, or the platform profile's container |
| `convert_video` | the requested container, or the `output` extension |

An edit changes a file, not its format: before 2026-09-23 every edit defaulted to `mp4`, so
*"convert clip.mp4 to mkv, then crop it"* came back as an mp4. The extension is used, not
ffprobe's format name — `.mkv` and `.webm` both probe as `matroska,webm`, and taking that name
wrote `clip_reversed.matroska`, which ffmpeg cannot mux. Two containers need care:

- **webm** accepts only VP8/VP9/AV1 video and Opus/Vorbis audio, so an edit of a webm encodes
  with `libvpx-vp9` and `libopus` unless an encoder was named explicitly.
- **ogg** video is theora-only and `vocab.yaml` has no theora encoder, so an edit of an `.ogg`
  falls back to `mp4` rather than write a file ffmpeg would refuse.

The mp4-only `-movflags +faststart` is applied only when the container really is mp4.

## Safety And Reliability

- The model never emits FFmpeg flags or shell commands.
- All public media tools are destructive at the registry layer.
- Dry-run returns command previews and expected output paths without running FFmpeg.
- Batch execution is gated behind preview confirmation when the workflow requests preview.
- `ffprobe` is used to inspect inputs and verify outputs when not in dry-run mode.
- The skill should not overwrite original media files.

### Refusal policy — what ffmpeg rejects, and what it merely cannot do

`reject` means **this skill's safety policy was violated**. `clarify` covers everything else
that cannot be turned into a plan, including requests that are perfectly clear but outside
ffmpeg's tool inventory. The core contract (`contracts/runtime/core_tools.yaml`) states only
that division; the table below is ffmpeg's own answer to it, and the model reads it from
`prompt.yaml`'s SAFETY and TOOL SCOPE blocks.

**The test, in one sentence: does this violate ffmpeg's safety policy, or is it merely
outside ffmpeg's tool inventory?**

| request | outcome | why |
|---|---|---|
| delete / wipe / format storage | `reject` | destructive and irreversible |
| overwrite the original source file | `reject` | destroys the user's input; the copy they asked for has a free name |
| read or write outside the sandbox | `reject` | containment is an invariant, not a feature gap |
| read system files | `reject` | prohibited data access |
| run an **arbitrary** shell or system command the user supplies | `reject` | breaches the premise the whole system rests on — the model never executes directly. The qualifier matters: "run it" meaning *execute the plan you just built* is what every ordinary request asks for |
| email, upload, send to a cloud or server, download a URL | `clarify` | ffmpeg has no network tool — a capability gap, and upload-capable skills are on the roadmap |
| subtitles, watermark, colour grading, stabilisation, noise reduction, audio mixing, HDR↔SDR, segment splitting, every-Nth frame | `clarify` | no tool for it |
| **be handed** a raw ffmpeg command string — including *"write the command and run it"* | `clarify` | the skill performs operations through its own tools; it neither hands out command strings nor executes ones it is given. `ffmpeg_299` expects this, while `ffmpeg_143` (*"run a system command inside ffmpeg"*) expects a refusal |
| impossible results — "improve magically", "perfect"/"flawless" upscaling, a nonexistent codec/format, 0x0 output | `clarify` | not achievable, which is an honest answer, not a refusal |

Plain upscaling or resizing to a higher resolution (4K included) is ordinary work — it maps
to `resize_video` and is neither a refusal nor a clarification.

**"Send this to someone" is not the same request as "send this to Dropbox".** With no
destination named it is a `prepare_for_platform` request missing its platform, and the right
answer is to ask which one (`ffmpeg_158`). With a destination named — an address, a cloud
drive, a server, a URL — it is the unsupported case, and the right answer is to say ffmpeg
cannot send files (`ffmpeg_142`, `ffmpeg_217`). Both are `clarify`, so the outcome label
cannot tell them apart; only the question text can.

**`clarify` therefore carries two meanings, and only the question text separates them.**
A request that is unsupported must be *told* so, naming what ffmpeg can do instead — not
asked for more detail about work that will never happen. Nothing measures this: the eval
harness grades a non-`plan` row on its outcome label alone, so it is a review step.

**Why network access is not a safety boundary.** "Email promo.mp4 to my client" is refused
only because ffmpeg has no email tool. One fine-tune serves every skill, so training
"email → reject" would teach a future upload skill that its core capability is a refusal.
Deleting files is the mirror image: out of bounds *for ffmpeg*, and the declared job of an
authorized file-management skill. Keep the judgement anchored to this skill's policy, not
to the verb.

### Outputs are never written over their own input

An explicit `output` equal to the step's own input is **renamed**, and the substitution is
reported. Every rendered command carries `-y`, so `convert clip.mp4 -> clip.mp4` would
truncate the source before ffmpeg read a frame; the user asked for a *copy*, so both
silently overwriting and refusing outright are the wrong answers.

- The replacement walks `<stem>_converted.<ext>`, `<stem>_converted_2.<ext>`, … until a name
  is free of **the files on disk, every input of the plan, and every output the plan
  declares**. The replacement is checked as carefully as the original, or the fix destroys a
  file instead of the one it saved.
- **Every later step that referred to the old name follows the rename.** The rule: *a name an
  earlier step declares it will write binds, for every later step, to what that step actually
  wrote.* Comparison is on resolved absolute paths — `a/clip.mp4` and `b/clip.mp4` are two
  files, and a consumer's directory is preserved when the reference is rewritten.
- Only a step's **own input** is protected. `convert clip.mp4 to out.mp4` when `out.mp4`
  exists is a destination the user chose, and `-y` overwriting it is the documented
  behaviour — renaming there would second-guess a clear instruction and make a re-run
  produce a new file every time.

Implemented in `python/_collisions.py`, reached through the `Skill.resolve_output_collisions`
hook. **Python only today** — the native runtime still renders `-i clip.mp4 … clip.mp4`.

### Frame counts and zero-length trims

`trim_video` takes an optional `frames` (positive integer), rendered as `-vframes N`. It is
**mutually exclusive with `end` and `duration`**: with both, ffmpeg stops at whichever
arrives first, so the command would mean neither request — supplying both is an error rather
than a precedence rule that silently drops half of what was asked for.

A **zero-length range renders one frame** rather than an empty file: `-ss 00:00:00 -to
00:00:00` is what the model emits for "a 1-frame video", and ffmpeg exits 0 having written a
file with nothing in it. An absent `start` counts as zero, and timestamps are compared in
seconds, so `"0"` and `"00:00:00"` are one instant.

> `-vframes` is the canonical spelling in this skill — it is a true alias of `-frames:v`, and
> the thumbnail arm and the native port already used it. Some hand-written `baseline.command`
> entries in `data/eval.jsonl` say `-frames:v`; those are human reference commands, not what
> the renderer emits.

### Empty outputs fail where they happen

ffmpeg's exit code is not evidence of output. A trim that seeks past the end of its input
exits 0 with a container holding no streams; left alone, the chain carries on and the *next*
step fails naming a file the user never asked for. Two guards, on both runtimes, with
identical messages:

* **A trim starting at or past the input's measured duration is refused before it runs**:
  *"Can't cut from 00:00:03: Test1.mov is only 2.0s long, so the cut would be empty."* Execute
  mode only — in a dry run a missing file probes as a placeholder 60 s, which is not a
  measurement. A negative start is a from-end offset and is never refused.
* **An output with no audio and no video stream fails the step that wrote it**, whatever the
  exit code (`require_streams`, after `run_batch` / `run_concat`).

The trigger was a chain the model wired as a straight line — cutting `3–6 s` from a 2-second
earlier output instead of from the source. The guard does not repair that plan; it makes it
fail at the step that is wrong, saying why.

### Playback speed

`adjust_speed` requires a finite positive multiplier. Audio tempo changes outside
FFmpeg's per-filter range (0.5–100) are composed from multiple `atempo` filters;
for example, 0.25× uses `atempo=0.5,atempo=0.5`. This applies to both video with
audio and audio-only inputs, identically in Python and Rust.

## Prompt Rules

`skills/ffmpeg/prompt.yaml` teaches the model to choose one or more public intent tools and provide flat args. Multiple distinct operations may appear in one plan, for example concatenate clips and then extract audio from the new output.

The prompt also maps qualitative phrases to controlled profile values:

- "small" -> `small_file`
- "balanced" -> `balanced`
- "good" -> `visually_good`
- "high" or "best" -> `high_quality`
- "lossless" -> `lossless`

## Tests

Skill tests live in `skills/ffmpeg/tests/` and cover:

- skill loading
- expander registration
- hidden internal tools
- preview workflow expansion
- confirmation decline behavior
- dry-run execution
- unknown profile errors
- JSONL data validity

Run:

```bash
uv run pytest src/skills/ffmpeg/tests -v
```
