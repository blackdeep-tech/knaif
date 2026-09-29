"""FFmpeg skill — entry point and ``FFmpegSkill`` assembly.

The model emits only an intent (e.g. ``prepare_for_platform``); the intents
translate it into a deterministic multi-step workflow (Workflow Language v1),
and each step has its own handler. This module is the thin entry point: it wires
the tool classes into ``FFmpegSkill`` and re-exports the package's public names.

Package layout (see SPEC.md):

- ``_deps``     — ffmpeg/ffprobe shell-out (the test patch + debug seam)
- ``_engine``   — pure recipe build, command rendering, probe/profile/geometry
- ``steps``     — ``Step`` workflow handlers
- ``intents``   — ``Intent`` expanders (+ the concat subsystem)
- ``_reporting``— summarizers, preflights, result formatting, artifact runner
"""

from __future__ import annotations

from typing import Any

from knaif.skill_base import Skill
from knaif.steps import ResolveInputs

from . import _deps
from ._deps import FFmpegNotAvailable, run_ffmpeg, run_ffprobe
from ._engine import (
    _ASPECT_RE,
    _AUDIO_EXTS,
    _BITRATE_RE,
    _CRF_RE,
    _DASH_TRANSLATION,
    _IMAGE_EXTENSIONS,
    _OUTPUT_SUFFIX_BY_MODE,
    _PACKAGE_PROFILES,
    _PLATFORM_ALIASES,
    _SCALE_PRESETS,
    _VIDEO_CODEC_ALIASES,
    _VIDEO_CONTAINERS,
    _VIDEO_ENCODER_MAP,
    _VOLUME_LOUDER,
    _VOLUME_NUMERIC_RE,
    _VOLUME_QUIETER,
    _assert_in_sandbox,
    _audio_encoder_for,
    _audio_format_from_output,
    _bitrate_from_quality,
    _build_flags,
    _build_one_recipe,
    _codec_from_encoder,
    _coerce_bitrate,
    _coerce_dimension,
    _coerce_inputs,
    _coerce_volume_level,
    _container_from_output,
    _crf_to_profile_name,
    _derive_output_path,
    _dummy_probe,
    _geometry_vf,
    _image_format_from_output,
    _load_yaml,
    _normalize_platform,
    _parse_fps,
    _parse_scale,
    _platform_clarify,
    _preview_output_for,
    _profiles_root,
    _quality_from_crf,
    _render_command,
    _summarise_probe,
    _valid_platforms,
    _WxH_RE,
)
from ._reporting import (
    _fmt_files,
    _format_results,
    _load_platform_summary,
    _load_quality_hint,
    _preflight_inputs,
    _preflight_trim_frames,
)
from .intents import (
    AdjustSpeedIntent,
    AdjustVolumeIntent,
    CompressVideoIntent,
    ConcatVideoIntent,
    ConvertVideoIntent,
    CreateThumbnailIntent,
    ExtractAudioIntent,
    PrepareForPlatformIntent,
    ResizeVideoIntent,
    ReverseVideoIntent,
    RotateVideoIntent,
    RunConcatStep,
    StripAudioIntent,
    TrimVideoIntent,
    _assemble_concat_inputs,
    _build_batch_block,
    _build_preview_block,
    _concat_filter_args,
)
from .steps import (
    BuildRecipesStep,
    GenerateReportStep,
    InspectMediaStep,
    LoadPlatformProfileStep,
    LoadQualityProfileStep,
    RenderBatchCommandsStep,
    RenderPreviewCommandStep,
    RunBatchStep,
    RunPreviewStep,
    VerifyOutputsStep,
    VerifyPreviewStep,
)

# ─────────────────────────────────────────────────────────────────────────────
# Skill
# ─────────────────────────────────────────────────────────────────────────────


class FFmpegSkill(Skill):
    tools = [
        ResolveInputs,
        InspectMediaStep,
        LoadPlatformProfileStep,
        LoadQualityProfileStep,
        BuildRecipesStep,
        RenderPreviewCommandStep,
        RunPreviewStep,
        VerifyPreviewStep,
        RenderBatchCommandsStep,
        RunBatchStep,
        RunConcatStep,
        VerifyOutputsStep,
        GenerateReportStep,
        PrepareForPlatformIntent,
        CompressVideoIntent,
        ConvertVideoIntent,
        ResizeVideoIntent,
        TrimVideoIntent,
        ExtractAudioIntent,
        CreateThumbnailIntent,
        StripAudioIntent,
        AdjustSpeedIntent,
        AdjustVolumeIntent,
        RotateVideoIntent,
        ConcatVideoIntent,
        ReverseVideoIntent,
    ]

    def preflight(self, tool: str, args: dict[str, Any], **kw: Any) -> list[str]:
        return _preflight_trim_frames(args) + _preflight_inputs(args, **kw)

    def format_results(
        self, results: list[dict[str, Any]], *, dry_run: bool
    ) -> list[dict[str, str]]:
        items = _format_results(results, dry_run=dry_run)
        # A rename the user is not told about is half an answer: they asked to convert
        # clip.mp4 and a differently-named file appeared. Report it first, before the
        # outputs, so the name in the next line is the one they were just told about.
        #
        # Scoped to the tools actually in *these* results: the formatter runs once per
        # intent, so an unscoped note prints again on every later intent, the second time
        # attached to one that renamed nothing.
        tools_here = {step.get("tool") for step in results}
        notes = [
            {
                "kind": "note",
                "message": (
                    f"{sub['requested']} would have been overwritten by the step that reads "
                    f"it, so the result was written to {sub['used']} instead."
                ),
            }
            for sub in self.output_substitutions
            if sub["step"] in tools_here
        ]
        return notes + items

    def resolve_output_collisions(
        self, plan: list[dict[str, Any]], *, sandbox: Any = None
    ) -> list[dict[str, Any]]:
        """Rename an output that would truncate its own input, and rebind its consumers.

        Every rendered command carries ``-y``, so this is the difference between converting
        a file and destroying it. The substitutions are kept on the skill so
        ``format_results`` can tell the user which name was actually written — the user
        asked for a copy, and silently writing it somewhere else is only half an answer.
        """
        from ._collisions import rebind_colliding_outputs

        plan, subs = rebind_colliding_outputs(plan, sandbox)
        # Rebound every call, never accumulated: `execute_plan` has clarify returns *before*
        # this hook, so a plan that never reached it must not inherit the last one's notes.
        # (One skill instance serves one agent, so concurrent `execute_plan` calls on the
        # same agent would still interleave here — as they would through every other piece
        # of per-plan state in the pipeline.)
        self.output_substitutions = subs
        return plan


__all__ = [
    "_deps",
    "AdjustSpeedIntent",
    "AdjustVolumeIntent",
    "BuildRecipesStep",
    "CompressVideoIntent",
    "ConcatVideoIntent",
    "ConvertVideoIntent",
    "CreateThumbnailIntent",
    "ExtractAudioIntent",
    "GenerateReportStep",
    "InspectMediaStep",
    "LoadPlatformProfileStep",
    "LoadQualityProfileStep",
    "PrepareForPlatformIntent",
    "RenderBatchCommandsStep",
    "RenderPreviewCommandStep",
    "ResizeVideoIntent",
    "ReverseVideoIntent",
    "RotateVideoIntent",
    "RunBatchStep",
    "RunConcatStep",
    "RunPreviewStep",
    "StripAudioIntent",
    "TrimVideoIntent",
    "VerifyOutputsStep",
    "VerifyPreviewStep",
    "_ASPECT_RE",
    "_AUDIO_EXTS",
    "_BITRATE_RE",
    "_CRF_RE",
    "_DASH_TRANSLATION",
    "_IMAGE_EXTENSIONS",
    "_OUTPUT_SUFFIX_BY_MODE",
    "_PACKAGE_PROFILES",
    "_PLATFORM_ALIASES",
    "_SCALE_PRESETS",
    "_VIDEO_CODEC_ALIASES",
    "_VIDEO_CONTAINERS",
    "_VIDEO_ENCODER_MAP",
    "_VOLUME_LOUDER",
    "_VOLUME_NUMERIC_RE",
    "_VOLUME_QUIETER",
    "_WxH_RE",
    "_assemble_concat_inputs",
    "_assert_in_sandbox",
    "_audio_encoder_for",
    "_audio_format_from_output",
    "_bitrate_from_quality",
    "_build_batch_block",
    "_build_flags",
    "_build_one_recipe",
    "_build_preview_block",
    "_codec_from_encoder",
    "_coerce_bitrate",
    "_coerce_dimension",
    "_coerce_inputs",
    "_coerce_volume_level",
    "_concat_filter_args",
    "_container_from_output",
    "_crf_to_profile_name",
    "_derive_output_path",
    "_dummy_probe",
    "_fmt_files",
    "_format_results",
    "_geometry_vf",
    "_image_format_from_output",
    "_load_platform_summary",
    "_load_quality_hint",
    "_load_yaml",
    "_normalize_platform",
    "_parse_fps",
    "_parse_scale",
    "_platform_clarify",
    "_preflight_inputs",
    "_preview_output_for",
    "_profiles_root",
    "_quality_from_crf",
    "_render_command",
    "_summarise_probe",
    "_valid_platforms",
    "FFmpegSkill",
    "FFmpegNotAvailable",
    "run_ffmpeg",
    "run_ffprobe",
]
