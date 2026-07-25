"""FFmpeg output_diff verifier (v2).

Compares two media files by diffing their ffprobe property profiles.
Replaces the command-string-based cheap/honest verifiers from v1.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from knaif.evalsuite.protocols import VerifyResult

# Default per-field tolerances. Per-row tolerances in the corpus override these.
_DEFAULTS: dict[str, Any] = {
    "duration_s": 0.5,
    "size_pct": 20,
    "bitrate_pct": 20,
}

# Accepted codec name aliases (ffprobe output varies across versions).
_CODEC_ALIASES: dict[str, list[str]] = {
    "h264": ["h264", "avc", "avc1"],
    "hevc": ["hevc", "h265"],
    "vp9": ["vp9"],
    "av1": ["av1", "libaom-av1"],
    "aac": ["aac", "aac_lc"],
    "mp3": ["mp3", "libmp3lame"],
    "opus": ["opus", "libopus"],
    "flac": ["flac"],
    "pcm": ["pcm_s16le", "pcm"],
}


def _ffprobe(path: Path) -> dict[str, Any] | None:
    """Run ffprobe on *path* and return parsed JSON, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        return None


def _codec_matches(actual: str, expected: str) -> bool:
    actual_l = actual.lower()
    aliases = _CODEC_ALIASES.get(expected.lower(), [expected.lower()])
    return any(a == actual_l for a in aliases)


def _pct_diff(a: float, b: float) -> float:
    if a == 0:
        return 0.0 if b == 0 else 100.0
    return abs(a - b) / a * 100


def output_diff(
    artifact_path: Path,
    reference_path: Path,
    tolerances: dict[str, Any],
    sandbox: Path,
) -> VerifyResult:
    """
    Compare artifact against reference via ffprobe property diff.

    Returns VerifyResult with score 0.0–1.0 and per-field matched/failed lists.
    A passing score means no deterministic check failed — not perceptual equivalence.
    """
    tol = {**_DEFAULTS, **tolerances}

    # File existence and non-empty size
    if not reference_path.exists() or reference_path.stat().st_size == 0:
        return VerifyResult(
            score=0.0, failed=["reference_file_missing_or_empty"], verifier_kind="output"
        )
    if not artifact_path.exists() or artifact_path.stat().st_size == 0:
        return VerifyResult(
            score=0.0, failed=["artifact_file_missing_or_empty"], verifier_kind="output"
        )

    # File extension / container type match (quick pre-ffprobe check)
    matched: list[str] = []
    failed: list[str] = []
    ref_ext = reference_path.suffix.lower().lstrip(".")
    art_ext = artifact_path.suffix.lower().lstrip(".")
    if ref_ext and art_ext:
        if ref_ext == art_ext:
            matched.append(f"extension={ref_ext}")
        else:
            failed.append(f"extension: expected .{ref_ext}, got .{art_ext}")

    ref_probe = _ffprobe(reference_path)
    art_probe = _ffprobe(artifact_path)

    if ref_probe is None or art_probe is None:
        reason = "ffprobe_failed_reference" if ref_probe is None else "ffprobe_failed_artifact"
        total = len(matched) + len(failed) + 1
        score = len(matched) / total
        return VerifyResult(
            score=score, matched=matched, failed=failed + [reason], verifier_kind="output"
        )

    ref_streams = ref_probe.get("streams") or []
    art_streams = art_probe.get("streams") or []
    ref_fmt = ref_probe.get("format") or {}
    art_fmt = art_probe.get("format") or {}

    ref_video = next((s for s in ref_streams if s.get("codec_type") == "video"), None)
    art_video = next((s for s in art_streams if s.get("codec_type") == "video"), None)
    ref_audio = next((s for s in ref_streams if s.get("codec_type") == "audio"), None)
    art_audio = next((s for s in art_streams if s.get("codec_type") == "audio"), None)

    # Container
    ref_containers = [n.strip() for n in ref_fmt.get("format_name", "").lower().split(",")]
    art_containers = [n.strip() for n in art_fmt.get("format_name", "").lower().split(",")]
    if set(ref_containers) & set(art_containers):
        matched.append(f"container={ref_containers[0]}")
    else:
        failed.append(f"container: expected {ref_containers}, got {art_containers}")

    # Video stream presence
    if (ref_video is None) != (art_video is None):
        failed.append(
            f"video_stream: expected {'present' if ref_video else 'absent'}, "
            f"got {'present' if art_video else 'absent'}"
        )
    elif ref_video and art_video:
        # Video codec
        ref_vc = ref_video.get("codec_name", "")
        art_vc = art_video.get("codec_name", "")
        if _codec_matches(art_vc, ref_vc) or _codec_matches(ref_vc, art_vc) or ref_vc == art_vc:
            matched.append(f"video_codec={ref_vc}")
        else:
            failed.append(f"video_codec: expected {ref_vc!r}, got {art_vc!r}")

        # Resolution
        if ref_video.get("width") == art_video.get("width") and ref_video.get(
            "height"
        ) == art_video.get("height"):
            matched.append(f"resolution={ref_video.get('width')}x{ref_video.get('height')}")
        else:
            failed.append(
                f"resolution: expected {ref_video.get('width')}x{ref_video.get('height')}, "
                f"got {art_video.get('width')}x{art_video.get('height')}"
            )

        # Pixel format
        ref_pix = ref_video.get("pix_fmt", "")
        art_pix = art_video.get("pix_fmt", "")
        if ref_pix and art_pix:
            if ref_pix == art_pix:
                matched.append(f"pix_fmt={ref_pix}")
            else:
                failed.append(f"pix_fmt: expected {ref_pix!r}, got {art_pix!r}")

    # Audio stream presence
    if (ref_audio is None) != (art_audio is None):
        failed.append(
            f"audio_stream: expected {'present' if ref_audio else 'absent'}, "
            f"got {'present' if art_audio else 'absent'}"
        )
    elif ref_audio and art_audio:
        # Audio codec
        ref_ac = ref_audio.get("codec_name", "")
        art_ac = art_audio.get("codec_name", "")
        if _codec_matches(art_ac, ref_ac) or _codec_matches(ref_ac, art_ac) or ref_ac == art_ac:
            matched.append(f"audio_codec={ref_ac}")
        else:
            failed.append(f"audio_codec: expected {ref_ac!r}, got {art_ac!r}")

        # Sample rate
        if ref_audio.get("sample_rate") == art_audio.get("sample_rate"):
            matched.append(f"sample_rate={ref_audio.get('sample_rate')}")
        else:
            failed.append(
                f"sample_rate: expected {ref_audio.get('sample_rate')}, "
                f"got {art_audio.get('sample_rate')}"
            )

        # Channels
        if ref_audio.get("channels") == art_audio.get("channels"):
            matched.append(f"channels={ref_audio.get('channels')}")
        else:
            failed.append(
                f"channels: expected {ref_audio.get('channels')}, got {art_audio.get('channels')}"
            )

    # Duration
    try:
        ref_dur = float(ref_fmt.get("duration", 0))
        art_dur = float(art_fmt.get("duration", 0))
        if abs(ref_dur - art_dur) <= tol["duration_s"]:
            matched.append(f"duration≈{ref_dur:.1f}s")
        else:
            failed.append(
                f"duration: expected {ref_dur:.2f}s ±{tol['duration_s']}s, got {art_dur:.2f}s"
            )
    except (TypeError, ValueError):
        pass

    # File size
    try:
        ref_size = int(ref_fmt.get("size", 0))
        art_size = int(art_fmt.get("size", 0))
        if ref_size > 0 and _pct_diff(ref_size, art_size) <= tol["size_pct"]:
            matched.append(f"size≈{ref_size}")
        elif ref_size > 0:
            failed.append(
                f"size: expected {ref_size} ±{tol['size_pct']}%, got {art_size} "
                f"({_pct_diff(ref_size, art_size):.0f}% diff)"
            )
    except (TypeError, ValueError):
        pass

    # Bit rate (skip if missing — known noisy field)
    try:
        ref_br = int(ref_fmt.get("bit_rate", 0))
        art_br = int(art_fmt.get("bit_rate", 0))
        if ref_br > 0 and art_br > 0:
            if _pct_diff(ref_br, art_br) <= tol["bitrate_pct"]:
                matched.append(f"bitrate≈{ref_br}")
            else:
                failed.append(
                    f"bitrate: expected {ref_br} ±{tol['bitrate_pct']}%, got {art_br} "
                    f"({_pct_diff(ref_br, art_br):.0f}% diff)"
                )
    except (TypeError, ValueError):
        pass

    total = len(matched) + len(failed)
    score = len(matched) / total if total > 0 else 1.0
    return VerifyResult(score=score, matched=matched, failed=failed, verifier_kind="output")


def cheap(output: Any, criteria: dict[str, Any], sandbox: Path) -> VerifyResult:
    """Text-only verifier: checks the command string.

    Without success_criteria: score 1.0 if the artifact starts with 'ffmpeg'.
    With success_criteria.flags/filters: also checks those substrings in the command.
    No binary execution required — useful for fast CI and plan-iteration.
    """
    artifact = getattr(output, "artifact", None)
    if not artifact:
        return VerifyResult(score=0.0, failed=["no_artifact"], verifier_kind="plan")
    if not artifact.strip().startswith("ffmpeg"):
        return VerifyResult(score=0.5, failed=["artifact_not_ffmpeg_command"], verifier_kind="plan")

    matched: list[str] = ["ffmpeg_command_produced"]
    failed: list[str] = []

    for flag in criteria.get("flags") or []:
        if flag in artifact:
            matched.append(f"flag:{flag}")
        else:
            failed.append(f"flag:{flag} not in command")

    for filt in criteria.get("filters") or []:
        if filt in artifact:
            matched.append(f"filter:{filt}")
        else:
            failed.append(f"filter:{filt} not in command")

    total = len(matched) + len(failed)
    score = len(matched) / total if total > 0 else 1.0
    return VerifyResult(score=score, matched=matched, failed=failed, verifier_kind="plan")


def honest(
    output: Any,
    criteria: dict[str, Any],
    sandbox: Path,
    *,
    keep_artifacts: bool = False,
) -> VerifyResult:
    """Honest verifier: score 1.0 if the artifact path exists (was actually produced).

    Relies on the runner having set output.artifact_path when execute=True.
    Falls back to 0.0 if no artifact was produced.
    """
    artifact = getattr(output, "artifact", None)
    if not artifact:
        return VerifyResult(score=0.0, failed=["no_artifact"], verifier_kind="output")

    artifact_path: Path | None = getattr(output, "artifact_path", None)
    if artifact_path is not None and artifact_path.exists():
        if not keep_artifacts:
            try:
                artifact_path.unlink(missing_ok=True)
            except OSError:
                pass
        return VerifyResult(score=1.0, matched=["output_file_produced"], verifier_kind="output")

    return VerifyResult(score=0.0, failed=["output_file_not_produced"], verifier_kind="output")


SUCCESS_CRITERIA_FIELDS: dict[str, str] = {
    "container": "Expected output container format (mp4, mkv, webm, …)",
    "video_codec": "Expected video codec, aliases resolved (h264, hevc, vp9, av1)",
    "audio_codec": "Expected audio codec, aliases resolved (aac, mp3, opus, flac). Use 'none' when stripped.",
    "no_audio": "True if audio must be absent from the output",
    "encoder": "Exact encoder library name expected in the command (libx264, libvpx-vp9, …)",
    "max_width": "Maximum output width in pixels (inclusive)",
    "max_height": "Maximum output height in pixels (inclusive)",
    "filters": "List of substrings that must appear in filter arguments",
    "flags": "List of substrings that must appear as ffmpeg CLI flags",
}


def success(output: Any, criteria: dict[str, Any], sandbox: Path) -> VerifyResult:
    """Absolute-quality verifier: grade the produced file against success_criteria.

    Checks command-text criteria (filters, flags, encoder) from the artifact string,
    and media-property criteria (container, codec, resolution, no_audio) via ffprobe
    on the produced output file.  Returns a VerifyResult with per-field matched/failed
    lists so the HTML detail table keeps working.

    When criteria is empty, returns score=1.0 (no requirements to fail).
    """
    # Routing-only rows (glob/batch with fixture None) can't materialize a single
    # artifact in the success eval. Reaching here means the row routed to a plan —
    # the only thing we grade — so skip artifact grading and score full.
    if criteria.get("grade") == "routing":
        return VerifyResult(score=1.0, matched=["routing_only"], verifier_kind="output")

    if not criteria:
        return VerifyResult(score=1.0, matched=["no_criteria"], verifier_kind="output")

    matched: list[str] = []
    failed: list[str] = []
    artifact: str | None = getattr(output, "artifact", None)

    # ── command-text checks ──────────────────────────────────────────────────
    for flag in criteria.get("flags") or []:
        if artifact and flag in artifact:
            matched.append(f"flag:{flag}")
        else:
            failed.append(f"flag:{flag} not in command")

    for filt in criteria.get("filters") or []:
        if artifact and filt in artifact:
            matched.append(f"filter:{filt}")
        else:
            failed.append(f"filter:{filt} not in command")

    for enc in ([criteria["encoder"]] if "encoder" in criteria else []):
        if artifact and enc in artifact:
            matched.append(f"encoder:{enc}")
        else:
            failed.append(f"encoder:{enc} not in command")

    # ── file-property checks ─────────────────────────────────────────────────
    _FILE_FIELDS = {
        "container",
        "video_codec",
        "audio_codec",
        "no_audio",
        "max_width",
        "max_height",
    }
    has_file_criteria = any(k in criteria for k in _FILE_FIELDS)

    if not has_file_criteria:
        total = len(matched) + len(failed)
        score = len(matched) / total if total > 0 else 1.0
        return VerifyResult(score=score, matched=matched, failed=failed, verifier_kind="output")

    artifact_path: Path | None = getattr(output, "artifact_path", None)
    if artifact_path is None or not Path(artifact_path).exists():
        total = len(matched) + len(failed) + 1
        score = len(matched) / total
        return VerifyResult(
            score=score,
            matched=matched,
            failed=failed + ["artifact_file_missing_or_not_produced"],
            verifier_kind="output",
        )

    probe = _ffprobe(Path(artifact_path))
    if probe is None:
        total = len(matched) + len(failed) + 1
        score = len(matched) / total
        return VerifyResult(
            score=score,
            matched=matched,
            failed=failed + ["ffprobe_failed"],
            verifier_kind="output",
        )

    streams = probe.get("streams") or []
    fmt = probe.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if "container" in criteria:
        containers = [n.strip() for n in fmt.get("format_name", "").lower().split(",")]
        exp = [n.strip() for n in criteria["container"].lower().split(",")]
        if set(exp) & set(containers):
            matched.append(f"container={exp[0]}")
        else:
            failed.append(f"container: expected {exp}, got {containers}")

    if "video_codec" in criteria:
        exp_vc = criteria["video_codec"]
        actual_vc = (video or {}).get("codec_name", "")
        if video and (_codec_matches(actual_vc, exp_vc) or _codec_matches(exp_vc, actual_vc)):
            matched.append(f"video_codec={exp_vc}")
        elif not video:
            failed.append(f"video_codec: expected {exp_vc!r}, no video stream")
        else:
            failed.append(f"video_codec: expected {exp_vc!r}, got {actual_vc!r}")

    if "audio_codec" in criteria:
        exp_ac = criteria["audio_codec"]
        actual_ac = (audio or {}).get("codec_name", "")
        if exp_ac in ("none", "null") or criteria.get("no_audio"):
            if audio is None:
                matched.append("audio_codec=none")
            else:
                failed.append(f"audio_codec: expected none, got {actual_ac!r}")
        elif audio and (_codec_matches(actual_ac, exp_ac) or _codec_matches(exp_ac, actual_ac)):
            matched.append(f"audio_codec={exp_ac}")
        elif not audio:
            failed.append(f"audio_codec: expected {exp_ac!r}, no audio stream")
        else:
            failed.append(f"audio_codec: expected {exp_ac!r}, got {actual_ac!r}")

    if "no_audio" in criteria:
        if criteria["no_audio"]:
            if audio is None:
                matched.append("no_audio=true")
            else:
                failed.append(f"no_audio: expected absent, got {(audio or {}).get('codec_name')!r}")

    if "max_width" in criteria and video:
        w = video.get("width") or 0
        exp_w = criteria["max_width"]
        if w <= exp_w:
            matched.append(f"max_width={w}<={exp_w}")
        else:
            failed.append(f"max_width: expected <={exp_w}, got {w}")

    if "max_height" in criteria and video:
        h = video.get("height") or 0
        exp_h = criteria["max_height"]
        if h <= exp_h:
            matched.append(f"max_height={h}<={exp_h}")
        else:
            failed.append(f"max_height: expected <={exp_h}, got {h}")

    total = len(matched) + len(failed)
    score = len(matched) / total if total > 0 else 1.0
    return VerifyResult(score=score, matched=matched, failed=failed, verifier_kind="output")


class _GradeTarget:
    """Lightweight stand-in for the runner's AgentOutput, for per-file grading."""

    def __init__(self, artifact: str | None, artifact_path: Path | None) -> None:
        self.artifact = artifact
        self.artifact_path = artifact_path


def grade_outputs(
    produced_paths: list[Path | None],
    outputs_spec: list[dict[str, Any]],
    sandbox: Path,
) -> VerifyResult:
    """Grade an ordered list of produced files against a multi-output spec.

    ``outputs_spec`` is a corpus row's ``outputs`` list — one entry per expected
    deliverable, each a dict with a ``command`` (used for command-text criteria)
    and ``criteria`` (file-property + command-text success_criteria). Each entry
    is graded with :func:`success` against the matching ``produced_paths`` item;
    a missing path (``None``) scores 0 for that output. Per-output matched/failed
    labels are prefixed ``outN:`` and the aggregate score is the mean of the
    per-output scores so every deliverable is weighted equally.
    """
    if not outputs_spec:
        return VerifyResult(score=1.0, matched=["no_outputs"], verifier_kind="output")

    matched: list[str] = []
    failed: list[str] = []
    scores: list[float] = []

    for i, spec in enumerate(outputs_spec):
        criteria = spec.get("criteria") or {}
        path = produced_paths[i] if i < len(produced_paths) else None
        if path is None:
            scores.append(0.0)
            failed.append(f"out{i}:output_not_produced")
            continue
        target = _GradeTarget(artifact=spec.get("command"), artifact_path=path)
        res = success(target, criteria, sandbox)
        scores.append(res.score)
        matched.extend(f"out{i}:{m}" for m in res.matched)
        failed.extend(f"out{i}:{f}" for f in res.failed)

    score = sum(scores) / len(scores) if scores else 1.0
    return VerifyResult(score=score, matched=matched, failed=failed, verifier_kind="output")


VERIFIERS = {
    "cheap": cheap,
    "honest": honest,
    "output_diff": output_diff,
    "success": success,
    "grade_outputs": grade_outputs,
}

VERIFIER_PREFLIGHT: dict[str, Any] = {}
