"""An ffmpeg run leaves exactly the requested outputs in the user's folder.

Before the run scratch existed, four things were left behind and never deleted: a chain's
intermediate (`clip_trimmed.mp4` beside `clip_trimmed_resized.mp4`), the preview clip
(`clip_preview.mp4`, written even when the user declined), and the empty or partial file of a
step that failed. These tests execute real plans with real ffmpeg in a fresh folder and check
the folder with the same rule the corpus-wide check uses (`knaif.evalsuite.exact_files`).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.evalsuite.exact_files import replay_row

FFMPEG_SKILL_DIR = Path(__file__).parents[2]
FIXTURES = FFMPEG_SKILL_DIR.parents[1] / "sandbox" / "fixtures" / "ffmpeg"

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH"),
    pytest.mark.skipif(not (FIXTURES / "clip.mp4").exists(), reason="needs generated fixtures"),
]


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    """Only the one fixture these plans read: copying every fixture per test is slow."""
    src = tmp_path / "fixtures"
    src.mkdir()
    shutil.copy2(FIXTURES / "clip.mp4", src / "clip.mp4")
    return src


def _agent(confirmer=None):
    def make(folder: Path) -> CommandAgent:
        return CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=folder, confirmer=confirmer)

    return make


CHAIN = {
    "plan": [
        {
            "tool": "trim_video",
            "args": {"input": "clip.mp4", "end": "00:00:02", "output": "clip_trimmed.mp4"},
        },
        {"tool": "resize_video", "args": {"inputs": ["clip_trimmed.mp4"], "height": 240}},
    ]
}


def test_a_chain_leaves_only_its_final_output(tmp_path: Path, fixture_dir: Path) -> None:
    report = replay_row(
        _agent(),
        CHAIN,
        "trim clip.mp4 to 2 seconds, then resize it to 240p",
        fixture_dir,
        tmp_path / "row",
    )
    assert report["error"] is None
    assert report["leftovers"] == []
    assert len(report["delivered"]) == 1
    assert (tmp_path / "row" / report["delivered"][0]).is_file()


def test_an_intermediate_the_user_named_stays(tmp_path: Path, fixture_dir: Path) -> None:
    plan = {
        "plan": [
            {
                "tool": "trim_video",
                "args": {"input": "clip.mp4", "end": "00:00:02", "output": "short.mp4"},
            },
            {"tool": "resize_video", "args": {"inputs": ["short.mp4"], "height": 240}},
        ]
    }
    report = replay_row(
        _agent(),
        plan,
        "trim clip.mp4 into short.mp4, then resize short.mp4 to 240p",
        fixture_dir,
        tmp_path / "row",
    )
    assert report["error"] is None
    assert report["leftovers"] == []
    assert "short.mp4" in report["delivered"]
    assert (tmp_path / "row" / "short.mp4").is_file()


def test_a_preview_clip_is_shown_from_scratch_then_deleted(
    tmp_path: Path, fixture_dir: Path
) -> None:
    seen: list[Path] = []

    def confirmer(prompt: str, preview: dict | None) -> bool:
        path = Path((preview or {}).get("preview_output") or "")
        # The clip exists while the user is asked, and it is not in their folder.
        assert path.is_file(), f"preview clip missing at confirm time: {path}"
        assert (tmp_path / "row").resolve() not in path.resolve().parents
        seen.append(path)
        return True

    plan = {"plan": [{"tool": "reverse_video", "args": {"inputs": ["clip.mp4"]}}]}
    report = replay_row(_agent(confirmer), plan, "reverse clip.mp4", fixture_dir, tmp_path / "row")
    assert report["error"] is None
    assert report["leftovers"] == []
    assert seen, "the user was never shown the preview"
    assert not seen[0].exists(), "the preview clip outlived the run"


def test_a_declined_preview_leaves_nothing(tmp_path: Path, fixture_dir: Path) -> None:
    seen: list[Path] = []

    def decline(prompt: str, preview: dict | None) -> bool:
        seen.append(Path((preview or {}).get("preview_output") or ""))
        return False

    plan = {"plan": [{"tool": "reverse_video", "args": {"inputs": ["clip.mp4"]}}]}
    report = replay_row(_agent(decline), plan, "reverse clip.mp4", fixture_dir, tmp_path / "row")
    assert report["leftovers"] == []
    assert report["delivered"] == []
    assert seen and not seen[0].exists()


def test_a_failed_step_leaves_no_partial_file(
    tmp_path: Path, fixture_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def make(folder: Path) -> CommandAgent:
        agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=folder)
        steps = sys.modules[type(agent.tool_map["run_batch"]).__module__]

        def half_written(cmd: list[str]) -> dict:
            # ffmpeg died mid-encode: some bytes at the output path, and a non-zero exit.
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"\x00" * 64)
            return {"returncode": 1, "stderr": "Conversion failed!"}

        monkeypatch.setattr(steps._deps, "run_ffmpeg", half_written)
        return agent

    plan = {
        "plan": [{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "container": "mkv"}}]
    }
    report = replay_row(make, plan, "convert clip.mp4 to mkv", fixture_dir, tmp_path / "row")
    assert report["delivered"] == []
    assert report["leftovers"] == []


def test_no_preview_is_rendered_when_nobody_is_asked(tmp_path: Path, fixture_dir: Path) -> None:
    """Owner, 2026-10-10: since 1.3.0 a run acts without asking unless --confirm. A preview only
    makes sense if someone looks at it, so without an interactive confirmer none is rendered and
    the plan goes straight to the batch (as native always did)."""
    rendered: list[list[str]] = []

    def make(folder: Path) -> CommandAgent:
        agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=folder)  # no confirmer
        steps = sys.modules[type(agent.tool_map["run_batch"]).__module__]
        real = steps._deps.run_ffmpeg

        def spy(cmd: list[str], ctx) -> dict:
            rendered.append(cmd)
            return real(cmd, ctx)

        monkeypatch_target.setattr(steps._deps, "run_ffmpeg", spy)
        return agent

    with pytest.MonkeyPatch.context() as monkeypatch_target:
        plan = {"plan": [{"tool": "reverse_video", "args": {"inputs": ["clip.mp4"]}}]}
        report = replay_row(make, plan, "reverse clip.mp4", fixture_dir, tmp_path / "row")
    assert report["error"] is None
    assert report["leftovers"] == []
    assert len(report["delivered"]) == 1
    assert not any("_preview." in c[-1] for c in rendered), rendered
