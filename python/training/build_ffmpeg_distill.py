"""Generate verifier-filtered ffmpeg distillation rows.

Rows are synthetic but not blindly trusted: each candidate must avoid verbatim
eval/train utterance duplicates, pass the live plan validator, and dry-run
through the FFmpeg skill expansion when it is an executable media plan.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from knaif import CommandAgent  # noqa: E402

OUT = ROOT / "training" / "ffmpeg_distill_v1.jsonl"
FIXTURE_SANDBOX = ROOT / "sandbox" / "fixtures" / "ffmpeg"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower().rstrip(".!?"))


def plan(tool: str, **args: Any) -> dict[str, Any]:
    return {"plan": [{"tool": tool, "args": args}]}


def reject(reason: str) -> dict[str, Any]:
    return plan("reject", reason=reason)


def clarify(question: str) -> dict[str, Any]:
    return plan("clarify", question=question)


def chain(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"plan": list(steps)}


def step(tool: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args}


def existing_utterances() -> set[str]:
    seen: set[str] = set()
    for path in [
        ROOT / "src/skills/ffmpeg/data/train.jsonl",
        ROOT / "src/skills/ffmpeg/data/eval.jsonl",
    ]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            for utterance in rec.get("utterances") or [rec.get("utterance", "")]:
                if utterance:
                    seen.add(norm(utterance))
    return seen


def candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(utterance: str, payload: dict[str, Any], tags: list[str]) -> None:
        rows.append(
            {
                "utterance": utterance,
                "plan": payload,
                "tags": ["distill_v1", *tags],
            }
        )

    # Quality enum corrections: avoid medium/low/high enum drift.
    for utt, src, quality in [
        ("compress clip2.mp4 with balanced visual quality", "clip2.mp4", "balanced"),
        ("make clip_ctr.mp4 a small-file encode", "clip_ctr.mp4", "small_file"),
        ("encode clip.mov so it still looks visually good", "clip.mov", "visually_good"),
        ("re-encode clip.mp4 using the high quality profile", "clip.mp4", "high_quality"),
        ("make a lossless encode of clip2.mp4", "clip2.mp4", "lossless"),
        ("squeeze clip_4k.mp4 for a tiny output file", "clip_4k.mp4", "small_file"),
        ("create a decent looking compressed copy of clip.mp4", "clip.mp4", "visually_good"),
        ("compress clip_no_audio.mp4 with balanced settings", "clip_no_audio.mp4", "balanced"),
    ]:
        add(utt, plan("compress_video", inputs=[src], quality=quality), ["quality", "compress"])

    # Codec/container corrections: video_codec belongs in video_codec, not container/profile.
    for utt, src, codec, container in [
        ("transcode clip.mp4 using hevc video", "clip.mp4", "hevc", "mp4"),
        ("encode clip2.mp4 as h264 in an mp4 container", "clip2.mp4", "h264", "mp4"),
        ("make clip.mov into a vp9 webm file", "clip.mov", "vp9", "webm"),
        ("convert clip.mp4 to av1 webm", "clip.mp4", "av1", "webm"),
        ("turn clip_ctr.mp4 into hevc mkv", "clip_ctr.mp4", "hevc", "mkv"),
        ("batch convert every mov to vp9 webm", "*.mov", "vp9", "webm"),
        ("convert all mp4 clips to hevc", "*.mp4", "hevc", "mp4"),
        ("bulk transcode the mov files to h264 mp4", "*.mov", "h264", "mp4"),
    ]:
        add(
            utt,
            plan("convert_video", inputs=[src], container=container, video_codec=codec),
            ["convert", "codec", "batch" if "*" in src else "distill_single"],
        )

    # Resize/scale and thumbnail scale corrections.
    for utt, src, height in [
        ("downscale clip_4k.mp4 to 480p", "clip_4k.mp4", 480),
        ("resize clip2.mp4 down to 720p", "clip2.mp4", 720),
        ("make clip.mp4 a proportional 1080p video", "clip.mp4", 1080),
        ("scale all mp4 files to 720p", "*.mp4", 720),
        ("reduce clip_ctr.mp4 to 480 pixels tall", "clip_ctr.mp4", 480),
    ]:
        add(
            utt,
            plan("resize_video", inputs=[src], height=height, keep_aspect_ratio=True),
            ["resize", "scale", "batch" if "*" in src else "distill_single"],
        )
    for utt, src, at_time, scale in [
        ("make a 4k thumbnail from clip.mp4 at 5 seconds", "clip.mp4", "00:00:05", "4k"),
        (
            "grab a 3840x2160 still from clip_4k.mp4 at 6 seconds",
            "clip_4k.mp4",
            "00:00:06",
            "3840x2160",
        ),
        (
            "create a 1080p poster frame from clip2.mp4 at 00:00:04",
            "clip2.mp4",
            "00:00:04",
            "1080p",
        ),
        ("export a 720p frame grab from clip.mp4 at 3 seconds", "clip.mp4", "00:00:03", "720p"),
    ]:
        add(
            utt,
            plan("create_thumbnail", input=src, at_time=at_time, scale=scale),
            ["thumbnail", "scale"],
        )

    # Clarify/reject boundaries.
    for utt, question in [
        (
            "export clip.mp4 for a good streaming destination",
            "Which platform should I prepare clip.mp4 for?",
        ),
        (
            "make clip.mp4 good for sending",
            "Which platform or delivery target should I prepare clip.mp4 for?",
        ),
        (
            "encode clip.mp4 with the best settings",
            "Which output format or target should I encode clip.mp4 for?",
        ),
        (
            "adjust the volume of clip.mp4",
            "Should I normalize the audio or change it by a specific dB amount?",
        ),
        ("rotate clip2.mp4", "What rotation angle should I use: 90, 180, or 270 degrees?"),
        ("process the media folder", "What operation should I apply to the media folder?"),
        ("convert a transport stream to mp4", "Which transport stream file should I convert?"),
        ("compress the 4k file for email", "Which 4K file should I compress for email?"),
        (
            "turn the silent clip into mkv and scale to 1080p",
            "Which silent clip should I convert and resize?",
        ),
    ]:
        add(utt, clarify(question), ["clarify", "edge"])

    for utt, reason in [
        (
            "convert clip.mp4 and then upload it to my server",
            "Uploading files to external services is outside the local media-processing scope.",
        ),
        (
            "re-encode clip2.mp4 and overwrite the original",
            "Overwriting the original source file is not allowed.",
        ),
        (
            "convert every video under /etc to mp4",
            "Reading system directories is outside the sandbox and out of scope.",
        ),
        (
            "email clip.mp4 after converting it",
            "Sending or emailing files is outside the local media-processing scope.",
        ),
        ("use imaginarycodec for clip.mp4", "A nonexistent codec is not a valid request."),
        ("make a 0 by 0 output from clip.mp4", "A zero-size output is not a valid result."),
        (
            "make clip.mp4 flawlessly upscale to perfect 8k",
            "Promising flawless or perfect upscaling is not an achievable result.",
        ),
        (
            "download a video from example.com and convert it",
            "Downloading from URLs is outside the local sandbox scope.",
        ),
    ]:
        add(utt, reject(reason), ["reject", "safety"])

    # Chain rows with explicit intermediate filenames.
    add(
        "rotate clip.mp4 right, shrink it to 480p, then make it smaller",
        chain(
            step("rotate_video", inputs=["clip.mp4"], angle=90, output="clip_rotated.mp4"),
            step(
                "resize_video",
                inputs=["clip_rotated.mp4"],
                height=480,
                keep_aspect_ratio=True,
                output="clip_rotated_480p.mp4",
            ),
            step("compress_video", inputs=["clip_rotated_480p.mp4"], quality="small_file"),
        ),
        ["hard_target", "chain3", "rotate", "resize", "compress"],
    )
    add(
        "resize clip_4k.mp4 to 1080p, compress it, and remove the sound",
        chain(
            step(
                "resize_video",
                inputs=["clip_4k.mp4"],
                height=1080,
                keep_aspect_ratio=True,
                output="clip_1080p.mp4",
            ),
            step(
                "compress_video",
                inputs=["clip_1080p.mp4"],
                quality="small_file",
                output="clip_1080p_small.mp4",
            ),
            step("strip_audio", inputs=["clip_1080p_small.mp4"]),
        ),
        ["hard_target", "chain3", "resize", "compress", "strip"],
    )
    add(
        "convert clip.mov to mp4 and remove its audio",
        chain(
            step(
                "convert_video", inputs=["clip.mov"], container="mp4", output="clip_converted.mp4"
            ),
            step("strip_audio", inputs=["clip_converted.mp4"]),
        ),
        ["hard_target", "chain2", "convert", "strip"],
    )
    add(
        "make clip.mp4 twice as fast and silent",
        chain(
            step("adjust_speed", inputs=["clip.mp4"], speed=2.0, output="clip_fast.mp4"),
            step("strip_audio", inputs=["clip_fast.mp4"]),
        ),
        ["hard_target", "chain2", "speed", "strip"],
    )
    add(
        "trim clip_4k.mp4 to two seconds, export webm, then resize to 720p",
        chain(
            step(
                "trim_video",
                input="clip_4k.mp4",
                start="00:00:00",
                duration="00:00:02",
                output="clip_4k_trimmed.mp4",
            ),
            step(
                "convert_video",
                inputs=["clip_4k_trimmed.mp4"],
                container="webm",
                video_codec="vp9",
                output="clip_4k_trimmed.webm",
            ),
            step(
                "resize_video", inputs=["clip_4k_trimmed.webm"], height=720, keep_aspect_ratio=True
            ),
        ),
        ["hard_target", "chain3", "trim", "convert", "resize"],
    )
    return rows


def validate_rows(rows: list[dict[str, Any]], *, strict_execute: bool) -> list[dict[str, Any]]:
    seen = existing_utterances()
    agent = CommandAgent.from_skill("src/skills/ffmpeg", sandbox=str(FIXTURE_SANDBOX))
    accepted: list[dict[str, Any]] = []
    errors: list[str] = []
    for rec in rows:
        utterance = rec["utterance"]
        key = norm(utterance)
        if key in seen:
            errors.append(f"duplicate: {utterance!r}")
            continue
        try:
            agent.validate_plan(rec["plan"])
            first_tool = rec["plan"]["plan"][0]["tool"]
            if strict_execute and first_tool not in {"clarify", "reject"}:
                agent.execute_plan(
                    rec["plan"],
                    dry_run=True,
                    confirmed=False,
                    utterance=utterance,
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{utterance!r}: {exc}")
            continue
        accepted.append(rec)
        seen.add(key)
    if errors:
        print(f"filtered out {len(errors)} row(s):")
        for err in errors[:30]:
            print(f"  - {err}")
    return accepted


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-execute-filter", action="store_true")
    args = ap.parse_args()

    rows = validate_rows(candidates(), strict_execute=not args.no_execute_filter)
    out = Path(args.out)
    out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} accepted distillation rows -> {out}")


if __name__ == "__main__":
    main()
