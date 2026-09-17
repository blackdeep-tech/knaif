"""Slowdowns below 0.5 must produce real, correctly timed media with audio."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from knaif import CommandAgent


@pytest.mark.parametrize("audio_only,speed", [(False, 0.25), (True, 0.125), (True, 0.4)])
def test_sub_half_speed_executes_with_expected_duration(tmp_path: Path, audio_only, speed):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required for the execution regression")
    source = tmp_path / ("source.wav" if audio_only else "source.mp4")
    command = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    if not audio_only:
        command += [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=96x64:rate=20",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
        ]
    subprocess.run(command + ["-t", "1", str(source)], check=True, capture_output=True)
    agent = CommandAgent.from_skill("skills/ffmpeg", sandbox=tmp_path)
    plan = {"plan": [{"tool": "adjust_speed", "args": {"inputs": [source.name], "speed": speed}}]}
    results = agent.execute_plan(plan, dry_run=False, confirmed=True)
    outputs = [p for p in tmp_path.iterdir() if p.stem.endswith("_speed")]
    assert len(outputs) == 1, results
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(outputs[0]),
            ]
        )
    )
    assert abs(float(probe["format"]["duration"]) - 1 / speed) < 0.3
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert any(s["codec_type"] == "video" for s in probe["streams"]) is not audio_only
