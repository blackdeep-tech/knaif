"""Real-ffmpeg reproductions of audit findings F1 and F2, on the actual ffmpeg skill.

Ref: docs/audits/2026-09-07-core-principles-and-rtx5080.md. Structural (registry-only,
no real ffmpeg) coverage of the same fix lives in
``python/core/tests/test_tool_trust_boundary.py``; these two tests execute for real,
so they belong here per conftest.py's `_no_media_binaries` guard in core.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from knaif import create_agent

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def test_ffmpeg_run_preview_rejected_from_raw_plan(tmp_path: Path):
    """F1: a plan naming ffmpeg's internal `run_preview` with an arbitrary argv must
    never reach subprocess.run — model/caller output can only name public tools."""
    marker = tmp_path / "AUDIT_MARKER"
    agent = create_agent("ffmpeg", sandbox=str(tmp_path))
    payload = {
        "plan": [
            {
                "tool": "run_preview",
                "args": {"command": [sys.executable, "-c", f"open(r'{marker}', 'w').close()"]},
            }
        ]
    }
    with pytest.raises(ValueError):
        agent.execute_plan(payload, dry_run=False, confirmed=False)
    assert not marker.exists()


def test_ffmpeg_strip_audio_blocked_without_confirmation(tmp_path: Path):
    """F2: `strip_audio` (destructive) expands into safe `run_preview`/`run_batch`
    leaves; the intent's safety category must survive expansion instead of being
    lost, so this must raise before writing `silent.mp4`."""
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=32x32:rate=10:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-shortest",
            str(clip),
        ],
        capture_output=True,
        check=True,
    )
    agent = create_agent("ffmpeg", sandbox=str(tmp_path))
    payload = {
        "plan": [{"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "silent.mp4"}}]
    }
    with pytest.raises(ValueError, match="confirmed"):
        agent.execute_plan(payload, dry_run=False, confirmed=False)
    assert not (tmp_path / "silent.mp4").exists()
