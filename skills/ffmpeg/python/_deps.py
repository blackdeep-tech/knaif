"""External-tool boundary for the ffmpeg skill.

Every ffmpeg/ffprobe invocation is routed through this module so tests can
monkeypatch ``run_ffprobe`` / ``run_ffmpeg`` by patching the module object
(``handlers._deps.run_ffprobe``) and CI can run without the binaries installed.
Keeping the shell-out in one place is also the obvious spot to look when
debugging "what did we actually exec?".
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class FFmpegNotAvailable(RuntimeError):
    """Raised when the ffmpeg/ffprobe binary cannot be invoked."""


def run_ffprobe(file_path: Path) -> dict[str, Any]:
    """Run ffprobe and return parsed JSON metadata."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise FFmpegNotAvailable(
            "ffprobe not found on PATH. Install ffmpeg to use the ffmpeg skill."
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {file_path}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def run_ffmpeg(command: list[str]) -> dict[str, Any]:
    """Run an ffmpeg command (already rendered as a list of args) and return a status dict."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise FFmpegNotAvailable(
            "ffmpeg not found on PATH. Install ffmpeg to use the ffmpeg skill."
        ) from exc
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
