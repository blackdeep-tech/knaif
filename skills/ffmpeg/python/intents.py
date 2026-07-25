"""ffmpeg skill — intents module (see handlers.py / SPEC.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knaif.handler_api import HandlerContext
from knaif.tool import Intent, Step

from . import _deps
from ._engine import (
    _VIDEO_CODEC_ALIASES,
    _VIDEO_ENCODER_MAP,
    _assert_in_sandbox,
    _audio_format_from_output,
    _bitrate_from_quality,
    _coerce_bitrate,
    _coerce_dimension,
    _coerce_inputs,
    _container_from_output,
    _image_format_from_output,
    _normalize_platform,
    _parse_scale,
    _platform_clarify,
    _quality_from_crf,
    _summarise_probe,
)
from ._reporting import _fmt_files, _load_platform_summary, _load_quality_hint

# ─────────────────────────────────────────────────────────────────────────────
# Intent expanders.
# ─────────────────────────────────────────────────────────────────────────────


def _build_preview_block(num_inputs: int) -> list[dict[str, Any]]:
    return [
        {
            "tool": "render_preview_command",
            "args": {"recipes": "$recipes"},
            "output": "$preview_cmd",
        },
        {"tool": "run_preview", "args": {"command": "$preview_cmd"}, "output": "$preview_run"},
        {
            "tool": "verify_preview",
            "args": {"preview_output": "$preview_run"},
            "output": "$preview_meta",
        },
        {
            "tool": "wait_for_confirmation",
            "args": {
                "prompt": f"Apply these settings to all {num_inputs} input(s)?",
                "preview": "$preview_meta",
            },
        },
    ]


def _build_batch_block() -> list[dict[str, Any]]:
    return [
        {"tool": "render_batch_commands", "args": {"recipes": "$recipes"}, "output": "$batch_cmds"},
        {"tool": "run_batch", "args": {"commands": "$batch_cmds"}, "output": "$batch_outputs"},
        {
            "tool": "verify_outputs",
            "args": {"outputs": "$batch_outputs"},
            "output": "$verifications",
        },
        {"tool": "generate_report", "args": {"outputs": "$verifications"}, "output": "$report"},
    ]


class PrepareForPlatformIntent(Intent):
    name = "prepare_for_platform"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["inputs"])
        clarify = _platform_clarify(args["platform"])
        if clarify:
            return clarify
        platform = _normalize_platform(args["platform"])
        quality = args.get("quality", "visually_good")
        preview = bool(args.get("preview", True))

        plan: list[dict[str, Any]] = [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {
                "tool": "load_platform_profile",
                "args": {"platform": platform},
                "output": "$platform_profile",
            },
            {
                "tool": "load_quality_profile",
                "args": {"quality": quality},
                "output": "$quality_profile",
            },
            {
                "tool": "build_recipes",
                "args": {
                    "probes": "$probes",
                    "platform_profile": "$platform_profile",
                    "quality_profile": "$quality_profile",
                    "options": {"mode": "platform", "platform": platform},
                },
                "output": "$recipes",
            },
        ]
        if preview:
            plan += _build_preview_block(len(inputs))
        plan += _build_batch_block()
        return plan

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        platform = args.get("platform", "a platform")
        files = _fmt_files(args.get("inputs"))
        skill_dir: Path | None = kwargs.get("skill_dir")
        if skill_dir:
            spec = _load_platform_summary(platform, Path(skill_dir))
            if spec:
                return f"encode {files} → {spec} (for {platform})"
        return f"prepare {files} for {platform}"


class CompressVideoIntent(Intent):
    name = "compress_video"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["inputs"])
        target = args.get("target")
        if target:
            clarify = _platform_clarify(target)
            if clarify:
                return clarify
            target = _normalize_platform(target)
        target_size_mb = args.get("target_size_mb")
        crf = args.get("crf")
        quality = _quality_from_crf(crf, args.get("quality", "small_file"))
        preview = bool(args.get("preview", False))

        options: dict[str, Any] = {"mode": "compress"}
        if target_size_mb is not None:
            options["target_size_mb"] = target_size_mb
        if args.get("output") is not None:
            options["output_path"] = args["output"]

        plan: list[dict[str, Any]] = [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
        ]
        if target:
            plan.append(
                {
                    "tool": "load_platform_profile",
                    "args": {"platform": target},
                    "output": "$platform_profile",
                }
            )
        plan.append(
            {
                "tool": "load_quality_profile",
                "args": {"quality": quality},
                "output": "$quality_profile",
            }
        )

        build_args: dict[str, Any] = {
            "probes": "$probes",
            "quality_profile": "$quality_profile",
            "options": options,
        }
        if target:
            build_args["platform_profile"] = "$platform_profile"
        plan.append({"tool": "build_recipes", "args": build_args, "output": "$recipes"})

        if preview:
            plan += _build_preview_block(len(inputs))
        plan += _build_batch_block()
        return plan

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        target = args.get("target")
        size = args.get("target_size_mb")
        files = _fmt_files(args.get("inputs"))
        quality = args.get("quality", "small_file")
        skill_dir: Path | None = kwargs.get("skill_dir")

        hint = ""
        if skill_dir:
            skill_path = Path(skill_dir)
            # Platform spec takes precedence when a target platform is set.
            if target:
                spec = _load_platform_summary(target, skill_path)
                if spec:
                    q_hint = _load_quality_hint(quality, skill_path)
                    suffix = f" ({q_hint})" if q_hint else ""
                    return f"compress {files} → {spec}{suffix}"
            else:
                q_hint = _load_quality_hint(quality, skill_path)
                if q_hint:
                    hint = f" ({q_hint})"

        if target:
            return f"compress {files} for {target}{hint}"
        if size is not None:
            return f"compress {files} → ~{size} MB{hint}"
        return f"compress {files}{hint}"


class ConvertVideoIntent(Intent):
    name = "convert_video"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["inputs"])
        output = args.get("output")
        # Explicit container wins; otherwise infer from the output extension (e.g.
        # output='clip.webm' → webm) so the encoded container matches the filename.
        container = args.get("container")
        video_codec = args.get("video_codec")
        # A model sometimes slots a video CODEC token ("av1", "hevc", "vp9") into the
        # container arg ("convert to av1" / "nach AV1 konvertieren" → container='av1').
        # These are codecs, not containers; left as-is they remux (-c copy) into a
        # bogus '.av1' file. Move the codec to video_codec and let the container fall
        # back to the output extension or mp4.
        if (
            isinstance(container, str)
            and video_codec is None
            and container.lower() in _VIDEO_CODEC_ALIASES
        ):
            video_codec = container.lower()
            container = None
        if container is None:
            container = _container_from_output(output) or "mp4"
        audio_codec = args.get("audio_codec")
        crf = args.get("crf")
        quality = _quality_from_crf(crf, args.get("quality"))
        preview = bool(args.get("preview", False))

        # Pure container remux: no codec, no quality requested → use stream copy
        remux = video_codec is None and audio_codec is None and quality is None

        options: dict[str, Any] = {"mode": "convert", "container": container}
        if output is not None:
            options["output_path"] = output
        if remux:
            options["remux"] = True
        # Copy audio stream when no audio change was explicitly requested
        if not remux and audio_codec is None:
            options["copy_audio"] = True
        if audio_codec:
            options["audio_codec"] = audio_codec
        if video_codec:
            options["video_encoder"] = _VIDEO_ENCODER_MAP.get(video_codec, video_codec)

        load_quality = quality is not None and not remux

        plan: list[dict[str, Any]] = [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
        ]
        if load_quality:
            plan.append(
                {
                    "tool": "load_quality_profile",
                    "args": {"quality": quality},
                    "output": "$quality_profile",
                }
            )
        plan.append(
            {
                "tool": "build_recipes",
                "args": {
                    "probes": "$probes",
                    **({"quality_profile": "$quality_profile"} if load_quality else {}),
                    "options": options,
                },
                "output": "$recipes",
            }
        )
        if preview:
            plan += _build_preview_block(len(inputs))
        plan += _build_batch_block()
        return plan

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        container = (args.get("container") or "mp4").upper()
        v_codec = args.get("video_codec")
        codec_str = f" ({v_codec.upper()})" if v_codec else ""
        return f"convert {_fmt_files(args.get('inputs'))} → {container}{codec_str}"


class ResizeVideoIntent(Intent):
    name = "resize_video"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["inputs"])
        width = _coerce_dimension(args.get("width"))
        height = _coerce_dimension(args.get("height"))
        quality = args.get("quality", "visually_good")
        preview = bool(args.get("preview", False))

        fit = args.get("fit")
        aspect = args.get("aspect")
        # Legacy keep_aspect_ratio=False → stretch (explicit distort request).
        if not args.get("keep_aspect_ratio", True):
            fit = fit or "stretch"

        options: dict[str, Any] = {"mode": "resize", "copy_audio": True}
        if width is not None:
            options["width"] = width
        if height is not None:
            options["height"] = height
        if fit is not None:
            options["fit"] = fit
        if aspect is not None:
            options["aspect"] = aspect
        options["keep_aspect_ratio"] = bool(args.get("keep_aspect_ratio", True))
        if args.get("output") is not None:
            options["output_path"] = args["output"]

        plan: list[dict[str, Any]] = [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {
                "tool": "load_quality_profile",
                "args": {"quality": quality},
                "output": "$quality_profile",
            },
            {
                "tool": "build_recipes",
                "args": {
                    "probes": "$probes",
                    "quality_profile": "$quality_profile",
                    "options": options,
                },
                "output": "$recipes",
            },
        ]
        if preview:
            plan += _build_preview_block(len(inputs))
        plan += _build_batch_block()
        return plan

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        files = _fmt_files(args.get("inputs"))
        aspect = args.get("aspect")
        fit = args.get("fit")
        w, h = args.get("width"), args.get("height")
        if aspect:
            return f"reframe {files} → {aspect}"
        if fit == "crop" and w and h:
            return f"crop {files} → {w}x{h}"
        if w and h:
            size = f"{w}x{h}"
        elif h:
            size = f"{h}p"
        elif w:
            size = f"{w}px wide"
        else:
            size = "a new size"
        return f"resize {files} → {size}"


class TrimVideoIntent(Intent):
    name = "trim_video"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        input_path = args["input"]
        inputs = _coerce_inputs(input_path)
        options: dict[str, Any] = {"mode": "trim"}
        if args.get("start") is not None:
            options["start"] = args["start"]
        if args.get("duration") is not None:
            options["duration"] = args["duration"]
        if args.get("end") is not None:
            options["end"] = args["end"]
        if args.get("output") is not None:
            options["output_path"] = args["output"]
        quality = args.get("quality", "visually_good")
        preview = bool(args.get("preview", False))

        plan: list[dict[str, Any]] = [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {
                "tool": "load_quality_profile",
                "args": {"quality": quality},
                "output": "$quality_profile",
            },
            {
                "tool": "build_recipes",
                "args": {
                    "probes": "$probes",
                    "quality_profile": "$quality_profile",
                    "options": options,
                },
                "output": "$recipes",
            },
        ]
        if preview:
            plan += _build_preview_block(1)
        plan += _build_batch_block()
        return plan

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        files = _fmt_files(args.get("input") or args.get("inputs"))
        start = args.get("start")
        end = args.get("end")
        duration = args.get("duration")
        if start is not None and end is not None:
            span = f"from {start} to {end}"
        elif duration is not None and start is not None:
            span = f"{duration}s starting at {start}"
        elif duration is not None:
            span = f"the first {duration}s"
        elif end is not None:
            span = f"the first {end}"
        else:
            span = "a segment"
        return f"trim {span} from {files}"


class ExtractAudioIntent(Intent):
    name = "extract_audio"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["inputs"])
        output = args.get("output")
        # Models express the target audio format under several keys; audio_format wins,
        # then `format`, then a misused `container`, then the output filename's
        # extension ('export as audio.flac' → flac), and finally mp3.
        audio_format = (
            args.get("audio_format")
            or args.get("format")
            or args.get("container")
            or _audio_format_from_output(output)
            or "mp3"
        )
        # An explicit bitrate wins (only if bitrate-shaped — a leaked word like 'lower'
        # is dropped); otherwise honor a bitrate-shaped `quality` ('56kbps' → 56k).
        # A profile-name quality ('high_quality') is accepted and ignored.
        bitrate = _coerce_bitrate(args.get("bitrate")) or _bitrate_from_quality(args.get("quality"))
        options: dict[str, Any] = {"mode": "extract_audio", "audio_format": audio_format}
        if bitrate:
            options["audio_bitrate"] = bitrate
        # Trim-while-extract: honor an explicit time range (e.g. "audio from 3 to 5s").
        if args.get("start") is not None:
            options["start"] = args["start"]
        if args.get("end") is not None:
            options["end"] = args["end"]
        if output is not None:
            options["output_path"] = output

        return [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {
                "tool": "build_recipes",
                "args": {"probes": "$probes", "options": options},
                "output": "$recipes",
            },
            *_build_batch_block(),
        ]

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        fmt = args.get("audio_format", "mp3")
        return f"extract audio from {_fmt_files(args.get('inputs'))} as {fmt.upper()}"


class CreateThumbnailIntent(Intent):
    name = "create_thumbnail"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["input"])
        output = args.get("output")
        image_format = _image_format_from_output(output, args.get("image_format", "jpg"))
        options: dict[str, Any] = {
            "mode": "thumbnail",
            "at_time": args.get("at_time", "00:00:01"),
            "image_format": image_format,
            "scale": args.get("scale"),
        }
        if output is not None:
            options["output_path"] = output
        return [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {
                "tool": "build_recipes",
                "args": {"probes": "$probes", "options": options},
                "output": "$recipes",
            },
            *_build_batch_block(),
        ]

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        files = _fmt_files(args.get("input") or args.get("inputs"))
        at = args.get("at_time", "00:00:01")
        scale = args.get("scale")
        suffix = f" scaled to {scale}" if scale else ""
        return f"create a thumbnail from {files} at {at}{suffix}"


class StripAudioIntent(Intent):
    name = "strip_audio"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["inputs"])
        options: dict[str, Any] = {"mode": "strip_audio"}
        if args.get("output") is not None:
            options["output_path"] = args["output"]
        return [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {
                "tool": "build_recipes",
                "args": {"probes": "$probes", "options": options},
                "output": "$recipes",
            },
            *_build_batch_block(),
        ]

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        return f"strip audio from {_fmt_files(args.get('inputs'))}"


class AdjustSpeedIntent(Intent):
    name = "adjust_speed"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["inputs"])
        speed = float(args["speed"])
        quality = args.get("quality", "visually_good")
        options: dict[str, Any] = {"mode": "adjust_speed", "speed": speed}
        if args.get("output") is not None:
            options["output_path"] = args["output"]
        return [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {
                "tool": "load_quality_profile",
                "args": {"quality": quality},
                "output": "$quality_profile",
            },
            {
                "tool": "build_recipes",
                "args": {
                    "probes": "$probes",
                    "quality_profile": "$quality_profile",
                    "options": options,
                },
                "output": "$recipes",
            },
            *_build_batch_block(),
        ]

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        speed = args.get("speed", 1.0)
        return f"adjust the speed of {_fmt_files(args.get('inputs'))} by {speed}x"


class AdjustVolumeIntent(Intent):
    name = "adjust_volume"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["inputs"])
        quality = args.get("quality", "balanced")
        options: dict[str, Any] = {
            "mode": "adjust_volume",
            "level": args.get("level"),
            "normalize": args.get("normalize", False),
        }
        if args.get("output") is not None:
            options["output_path"] = args["output"]
        return [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {
                "tool": "load_quality_profile",
                "args": {"quality": quality},
                "output": "$quality_profile",
            },
            {
                "tool": "build_recipes",
                "args": {
                    "probes": "$probes",
                    "quality_profile": "$quality_profile",
                    "options": options,
                },
                "output": "$recipes",
            },
            *_build_batch_block(),
        ]

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        files = _fmt_files(args.get("inputs"))
        if args.get("normalize"):
            return f"normalize audio in {files}"
        level = args.get("level", "")
        suffix = f" by {level}" if level else ""
        return f"adjust volume of {files}{suffix}"


class RotateVideoIntent(Intent):
    name = "rotate_video"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["inputs"])
        quality = args.get("quality", "balanced")
        options: dict[str, Any] = {
            "mode": "rotate",
            "angle": args.get("angle"),
            "flip": args.get("flip"),
        }
        if args.get("output") is not None:
            options["output_path"] = args["output"]
        return [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {
                "tool": "load_quality_profile",
                "args": {"quality": quality},
                "output": "$quality_profile",
            },
            {
                "tool": "build_recipes",
                "args": {
                    "probes": "$probes",
                    "quality_profile": "$quality_profile",
                    "options": options,
                },
                "output": "$recipes",
            },
            *_build_batch_block(),
        ]

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        parts: list[str] = []
        if args.get("angle"):
            parts.append(f"{args['angle']}°")
        if args.get("flip"):
            parts.append(f"flip {args['flip']}")
        desc = " and ".join(parts) or "rotate"
        return f"rotate {_fmt_files(args.get('inputs'))} ({desc})"


def _assemble_concat_inputs(args: dict[str, Any]) -> list[str]:
    """Return the ordered input list from either inputs, or base+append args."""
    if "base" in args or "append" in args:
        base = args.get("base")
        append = args.get("append") or []
        base_list = _coerce_inputs(base) if base else []
        append_list = list(append) if isinstance(append, list) else _coerce_inputs(append)
        inputs = base_list + append_list
    else:
        inputs_arg = args["inputs"]
        if isinstance(inputs_arg, dict) and "files" in inputs_arg:
            inputs = inputs_arg["files"]
        elif isinstance(inputs_arg, list):
            inputs = [str(i) for i in inputs_arg]
        else:
            inputs = [str(inputs_arg)]
    return inputs


def _concat_filter_args(
    inputs: list[str],
    dry_run: bool,
    *,
    probes: list[dict[str, Any]] | None = None,
    target_resolution: str | None = None,
    target_fps: str | None = None,
) -> list[str]:
    """Build -filter_complex and -map args for a concat.

    When *probes* are supplied (the inputs path, where inspect_media already ran),
    uses them directly — no re-probe, and dry-run gets real dummy dims.  Falls back
    to runtime self-probing when probes=None (base/append path, where $vars make
    ahead-of-time probing impossible).

    Normalization (scale + fps + aresample) fires when:
    - any input's dimensions or frame rate differ from the target, OR
    - the caller supplied an explicit target_resolution / target_fps (forces the
      filter even when inputs already match, because an explicit instruction must
      produce a visible, deterministic command).

    target_resolution / target_fps accept a scale string ("1080p", "WxH"),
    "first" (default — no forced normalize), or "second" (use second input's probe).
    """
    # ── 1. Resolve per-input info ─────────────────────────────────────────────
    infos: list[dict[str, Any]]
    if probes is not None:
        infos = [
            {
                "has_audio": bool(p.get("has_audio")),
                "width": p.get("width"),
                "height": p.get("height"),
                "fps": p.get("fps"),
                "duration": p.get("duration"),
            }
            for p in probes
        ]
    else:
        infos = []
        for inp in inputs:
            if dry_run:
                infos.append(
                    {
                        "has_audio": True,
                        "width": None,
                        "height": None,
                        "fps": None,
                        "duration": None,
                    }
                )
            else:
                try:
                    probe = _deps.run_ffprobe(Path(inp))
                    summary = _summarise_probe(Path(inp), probe)
                    infos.append(
                        {
                            "has_audio": bool(summary.get("has_audio")),
                            "width": summary.get("width"),
                            "height": summary.get("height"),
                            "fps": summary.get("fps"),
                            "duration": summary.get("duration"),
                        }
                    )
                except Exception:  # noqa: BLE001
                    infos.append(
                        {
                            "has_audio": True,
                            "width": None,
                            "height": None,
                            "fps": None,
                            "duration": None,
                        }
                    )

    # Pad infos to match inputs length if some probes failed or were missing.
    while len(infos) < len(inputs):
        infos.append(
            {"has_audio": True, "width": None, "height": None, "fps": None, "duration": None}
        )

    any_audio = any(info["has_audio"] for info in infos)
    n = len(inputs)

    # ── 2. Resolve target resolution ─────────────────────────────────────────
    forced_res = False
    target_w: int | None = None
    target_h: int | None = None
    if target_resolution and target_resolution != "first":
        forced_res = True
        if target_resolution == "second" and len(infos) >= 2:
            target_w = infos[1].get("width")
            target_h = infos[1].get("height")
        else:
            parsed = _parse_scale(target_resolution)
            if parsed:
                parts = parsed.split(":")
                target_w, target_h = int(parts[0]), int(parts[1])
    if target_w is None:
        target_w = next((info["width"] for info in infos if info.get("width")), None)
        target_h = next((info["height"] for info in infos if info.get("height")), None)

    # ── 3. Resolve target fps ─────────────────────────────────────────────────
    forced_fps = False
    target_fps_val: float | None = None
    if target_fps and target_fps != "first":
        forced_fps = True
        if target_fps == "second" and len(infos) >= 2:
            target_fps_val = infos[1].get("fps")
        else:
            try:
                target_fps_val = float(target_fps)
            except (ValueError, TypeError):
                target_fps_val = None
    if target_fps_val is None:
        target_fps_val = next((info["fps"] for info in infos if info.get("fps")), None)

    # ── 4. Decide whether to normalize ───────────────────────────────────────
    any_res_mismatch = any(
        target_w
        and target_h
        and info.get("width")
        and info.get("height")
        and (info["width"] != target_w or info["height"] != target_h)
        for info in infos
    )
    any_fps_mismatch = any(
        target_fps_val and info.get("fps") and info["fps"] != target_fps_val for info in infos
    )
    normalize = forced_res or forced_fps or any_res_mismatch or any_fps_mismatch

    # ── 5. Build filter_complex ───────────────────────────────────────────────
    filter_parts: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []

    if normalize:
        fps_str: str | None = None
        if target_fps_val is not None:
            fps_int = int(target_fps_val)
            fps_str = str(fps_int) if target_fps_val == fps_int else str(target_fps_val)

        for i, info in enumerate(infos):
            v_filters: list[str] = []
            if target_w and target_h:
                v_filters.append(f"scale={target_w}:{target_h}")
            if fps_str:
                v_filters.append(f"fps={fps_str}")
            v_label = f"[v{i}]"
            if v_filters:
                filter_parts.append(f"[{i}:v]{','.join(v_filters)}{v_label}")
                video_labels.append(v_label)
            else:
                video_labels.append(f"[{i}:v]")

            if any_audio:
                if info["has_audio"]:
                    a_label = f"[a{i}]"
                    filter_parts.append(f"[{i}:a]aresample=44100{a_label}")
                    audio_labels.append(a_label)
                else:
                    a_label = f"[_silence{i}]"
                    dur = info.get("duration")
                    dur_param = f":d={dur}" if dur is not None else ""
                    filter_parts.append(f"anullsrc=r=44100:cl=stereo{dur_param}{a_label}")
                    audio_labels.append(a_label)
    else:
        # Minimal path: no scale/fps filters. anullsrc only for inputs missing audio.
        for i, info in enumerate(infos):
            video_labels.append(f"[{i}:v]")
            if any_audio and not info["has_audio"]:
                a_label = f"[_silence{i}]"
                dur = info.get("duration")
                dur_param = f":d={dur}" if dur is not None else ""
                filter_parts.append(f"anullsrc=r=44100:cl=stereo{dur_param}{a_label}")
                audio_labels.append(a_label)
            else:
                audio_labels.append(f"[{i}:a]" if any_audio else "")

    concat_inputs = "".join(f"{video_labels[i]}{audio_labels[i]}" for i in range(n))
    a_flag = 1 if any_audio else 0
    out_labels = "[outv][outa]" if any_audio else "[outv]"
    concat_part = f"{concat_inputs}concat=n={n}:v=1:a={a_flag}{out_labels}"
    filter_str = ";".join(filter_parts + [concat_part])

    result = ["-filter_complex", filter_str, "-map", "[outv]"]
    if any_audio:
        result += ["-map", "[outa]"]
    return result


class RunConcatStep(Step):
    name = "run_concat"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        """Concatenate multiple video files into one using ffmpeg filter_complex concat."""
        inputs = _assemble_concat_inputs(args)

        output = str(args["output"])
        if not Path(output).is_absolute():
            base = ctx.sandbox if ctx.sandbox is not None else ctx.root
            output = str((base / output).resolve())
        _assert_in_sandbox(Path(output), ctx.sandbox)

        # Unwrap probes from inspect_media result dict or accept a bare list.
        probes_arg = args.get("probes")
        probes: list[dict[str, Any]] | None = None
        if isinstance(probes_arg, dict) and "probes" in probes_arg:
            probes = probes_arg["probes"]
        elif isinstance(probes_arg, list):
            probes = probes_arg

        cmd: list[str] = ["ffmpeg", "-y"]
        for inp in inputs:
            cmd += ["-i", str(inp)]
        cmd += _concat_filter_args(
            inputs,
            ctx.dry_run or ctx.skip_execution,
            probes=probes,
            target_resolution=args.get("target_resolution"),
            target_fps=args.get("target_fps"),
        )
        cmd.append(output)

        if ctx.dry_run or ctx.skip_execution:
            return {
                "mode": "dry_run",
                "count": 1,
                "outputs": [{"input": inputs, "output": output, "command": cmd}],
                "command": cmd,
            }

        result = _deps.run_ffmpeg(cmd)
        return {
            "mode": "execute",
            "count": 1,
            "outputs": [
                {
                    "input": inputs,
                    "output": output,
                    "returncode": result["returncode"],
                    "stderr_tail": result["stderr"][-2000:] if result["stderr"] else "",
                }
            ],
            "command": cmd,
            "returncode": result["returncode"],
            "stderr_tail": result["stderr"][-2000:] if result["stderr"] else "",
        }


class ConcatVideoIntent(Intent):
    name = "concat_video"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        output = args.get("output", "combined.mp4")
        use_semantic = "base" in args or "append" in args

        if not use_semantic and "inputs" not in args:
            raise ValueError("concat_video requires either 'inputs' or 'base'/'append' args.")

        # Carry optional normalization targets through to run_concat.
        target_resolution = args.get("target_resolution")
        target_fps = args.get("target_fps")

        if use_semantic:
            # base/append may be $var references resolved at runtime — pass straight to run_concat.
            # No inspect_media step here; _concat_filter_args self-probes at runtime.
            concat_args: dict[str, Any] = {"output": output}
            if "base" in args:
                concat_args["base"] = args["base"]
            if "append" in args:
                concat_args["append"] = args["append"]
            if target_resolution:
                concat_args["target_resolution"] = target_resolution
            if target_fps:
                concat_args["target_fps"] = target_fps
            return [
                {"tool": "run_concat", "args": concat_args, "output": "$concat_result"},
                {
                    "tool": "verify_outputs",
                    "args": {"outputs": "$concat_result"},
                    "output": "$verifications",
                },
                {
                    "tool": "generate_report",
                    "args": {"outputs": "$verifications"},
                    "output": "$report",
                },
            ]

        inputs = _coerce_inputs(args["inputs"])
        run_concat_args: dict[str, Any] = {
            "inputs": "$files",
            "probes": "$probes",
            "output": output,
        }
        if target_resolution:
            run_concat_args["target_resolution"] = target_resolution
        if target_fps:
            run_concat_args["target_fps"] = target_fps
        return [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {"tool": "run_concat", "args": run_concat_args, "output": "$concat_result"},
            {
                "tool": "verify_outputs",
                "args": {"outputs": "$concat_result"},
                "output": "$verifications",
            },
            {"tool": "generate_report", "args": {"outputs": "$verifications"}, "output": "$report"},
        ]

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        output = args.get("output") or "a single file"
        output_name = Path(str(output)).name or str(output)
        base = f"concatenate {_fmt_files(args.get('inputs'))} into {output_name}"
        hints: list[str] = []
        if args.get("target_resolution"):
            hints.append(f"normalized to {args['target_resolution']} as requested")
        if args.get("target_fps"):
            hints.append(f"{args['target_fps']} fps as requested")
        return f"{base} ({', '.join(hints)})" if hints else base


class ReverseVideoIntent(Intent):
    name = "reverse_video"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = _coerce_inputs(args["inputs"])
        include_audio = bool(args.get("include_audio", True))
        quality = args.get("quality", "visually_good")

        options: dict[str, Any] = {"mode": "reverse", "include_audio": include_audio}
        if args.get("output") is not None:
            options["output_path"] = args["output"]

        return [
            {"tool": "resolve_inputs", "args": {"paths": inputs}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {
                "tool": "load_quality_profile",
                "args": {"quality": quality},
                "output": "$quality_profile",
            },
            {
                "tool": "build_recipes",
                "args": {
                    "probes": "$probes",
                    "quality_profile": "$quality_profile",
                    "options": options,
                },
                "output": "$recipes",
            },
            *_build_preview_block(len(inputs)),
            {
                "tool": "wait_for_confirmation",
                "args": {
                    "prompt": (
                        f"Reversing {len(inputs)} clip(s) re-encodes the full file and buffers "
                        "it entirely in RAM — long clips may exhaust memory. Proceed?"
                    ),
                    "preview": "$preview_meta",
                },
            },
            *_build_batch_block(),
        ]

    def summarize(self, args: dict[str, Any], **kwargs: Any) -> str:
        base = f"reverse {_fmt_files(args.get('inputs'))}"
        if args.get("include_audio") is False:
            return f"{base} (video only)"
        return base
