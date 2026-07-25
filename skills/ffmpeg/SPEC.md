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
compress_video
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

## Safety And Reliability

- The model never emits FFmpeg flags or shell commands.
- All public media tools are destructive at the registry layer.
- Dry-run returns command previews and expected output paths without running FFmpeg.
- Batch execution is gated behind preview confirmation when the workflow requests preview.
- `ffprobe` is used to inspect inputs and verify outputs when not in dry-run mode.
- The skill should not overwrite original media files.

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
