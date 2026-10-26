"""One file into many: which pieces a split, a frame grab or several thumbnails will write.

Owner, 2026-10-20. One planner for both sides of the promise: the skill hook
core asks before anything runs (`FFmpegSkill.planned_outputs`) and `build_recipes` when the step
runs read the same pieces, so the preview names what is written. Each piece is an ordinary
recipe: a trim (re-encoded, so it starts and ends exactly where asked) or a thumbnail.

Names: the set is a sequence pattern (`clip_%03d.mp4`), the files its numbers from 1. A pattern
the model wrote is kept; a plain output name gets the number before its extension
(`part.mp4` -> `part_001.mp4`); without one, next to the input: `clip_001.mp4` for pieces,
`clip_frame_001.jpg` for frames, `clip_thumb_001.jpg` for thumbnails at several times.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any

from ._engine import _format_seconds, _image_format_from_output, _timestamp_seconds

#: A remainder shorter than this is not a piece of its own (`every: 10` over 20.04 s is two).
SLIVER_S = 0.1


@dataclass(frozen=True)
class Pieces:
    """The set's name and, per piece, its output and its recipe options."""

    name: str
    items: list[tuple[str, dict[str, Any]]]


def _source(args: dict[str, Any]) -> str:
    value = args.get("input") or args.get("inputs")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").replace("\\", "/")


def _times(value: Any) -> list[str]:
    """Several times, as a list or one comma-separated string; a single time is one."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return []


def _seconds(value: Any) -> float | None:
    if value is None or value == "":
        return None
    seconds = _timestamp_seconds(str(value).strip())
    return seconds if seconds is not None and seconds > 0 else None


def _set_name(output: Any, source: str, suffix: str, ext: str) -> str:
    """The sequence pattern of the set (module docstring), relative to where paths resolve.

    A relative output is relative to the input's folder, as for every ffmpeg tool, so the
    input's folder leads it: core compares these names with the plan's.
    """
    folder, base = posixpath.split(source)
    if isinstance(output, str) and output:
        if not (output.startswith(("/", "\\")) or ":" in output[:3]):
            output = posixpath.join(folder, output.replace("\\", "/"))
        if "%" in output:
            return output
        stem, out_ext = posixpath.splitext(output)
        return f"{stem}_%03d{out_ext or '.' + ext}"
    stem = posixpath.splitext(base)[0]
    return posixpath.join(folder, f"{stem}{suffix}_%03d.{ext}")


def plan_pieces(
    tool: str, args: dict[str, Any], duration: float, fps: float | None
) -> Pieces | None:
    """The pieces *tool* will write from one input of *duration* seconds, else None.

    None for a tool that writes one file, including ``create_thumbnail`` at one time.
    """
    source = _source(args)
    output = args.get("output")
    if tool == "split_video":
        every, parts = _seconds(args.get("every")), args.get("parts")
        spans: list[tuple[float, float]] = []
        if every is not None:
            start = 0.0
            while duration - start >= SLIVER_S:
                spans.append((start, min(every, duration - start)))
                start += every
        elif parts:
            n = int(parts)
            length = duration / n
            spans = [(i * length, length if i < n - 1 else duration - i * length) for i in range(n)]
        else:
            raise ValueError(
                "To split a video, say how long each piece is (every) or how many pieces (parts)."
            )
        ext = posixpath.splitext(source)[1].lstrip(".") or "mp4"
        name = _set_name(output, source, "", ext)
        return Pieces(
            name,
            [
                (
                    name % (i + 1),
                    {
                        "mode": "trim",
                        "start": _format_seconds(start),
                        "duration": _format_seconds(length),
                        "output_path": name % (i + 1),
                    },
                )
                for i, (start, length) in enumerate(spans)
            ],
        )
    if tool == "extract_frames":
        every, count, nth = _seconds(args.get("every")), args.get("count"), args.get("every_nth")
        if every is None and nth and fps:
            every = int(nth) / float(fps)
        if every is not None:
            times, k = [], 0
            while k * every < duration - 1e-9:
                times.append(k * every)
                k += 1
        elif count:
            c = int(count)
            times = [duration * (i + 0.5) / c for i in range(c)]
        else:
            raise ValueError(
                "To take frames, say how often (every), every which frame (every_nth) or how "
                "many frames (count)."
            )
        return _stills(args, source, output, "_frame", [_format_seconds(t) for t in times])
    if tool == "create_thumbnail":
        times = _times(args.get("at_time"))
        if len(times) < 2:
            return None
        return _stills(args, source, output, "_thumb", times)
    return None


def _stills(
    args: dict[str, Any], source: str, output: Any, suffix: str, times: list[str]
) -> Pieces:
    image_format = _image_format_from_output(output, args.get("image_format") or "jpg")
    name = _set_name(output, source, suffix, image_format)
    return Pieces(
        name,
        [
            (
                name % (i + 1),
                {
                    "mode": "thumbnail",
                    "at_time": at,
                    "image_format": image_format,
                    "scale": args.get("scale"),
                    "output_path": name % (i + 1),
                },
            )
            for i, at in enumerate(times)
        ],
    )
