"""ffmpeg exports no artifact runner, and that is the point.

`Skill.run_artifact` is the extension point for a skill whose artifact is **not** a command
line — `documents` hands over a JSON plan payload. Every ffmpeg artifact *is* a command line,
so it goes through `knaif.evalsuite.chain.run_command_chain`: one per-row directory,
provisioned by copy, with every path token re-rooted into it and the command run as rendered.

ffmpeg used to supply `_run_artifact`, which rewrote `-i` to the fixture path and the output
into a separate directory — so the `output == input` collision `ffmpeg_175` is about was
removed before ffmpeg saw it, and a non-zero exit came back as the same `None` as a missing
binary. Retired 2026-09-12; see T5b in docs/plans/2026-09-11-reject-clarify-taxonomy.md.
"""

from __future__ import annotations

from pathlib import Path

from knaif.skill import Skill

SKILL_DIR = Path(__file__).parents[2]


def test_ffmpeg_exports_no_artifact_runner():
    """A second implementation of the execution rule is what let the lanes drift apart."""
    skill = Skill.load(SKILL_DIR)
    assert skill.artifact_runner is None
