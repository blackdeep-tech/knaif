"""concat_video over a pattern or a folder: "merge all the clips in this folder into one".

Owner, 2026-10-19: the clips join in natural order (`clip1`, `clip2`,
`clip10`), the order a batch runs them in, and a second run never reads the first run's result
back (`resolve_inputs` with `exclude`, contracts/parity/batch_cases.json).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from knaif import CommandAgent

FFMPEG_SKILL_DIR = Path("skills/ffmpeg")
CLIP = Path("sandbox/fixtures/ffmpeg/clip.mp4")

pytestmark = pytest.mark.skipif(
    not CLIP.exists(), reason="needs the generated ffmpeg fixtures (just eval-fixtures ffmpeg)"
)


def _joined(folder: Path, inputs: list[str]) -> tuple[list[str], str]:
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=folder, root=folder)
    plan = {"plan": [{"tool": "concat_video", "args": {"inputs": inputs, "output": "all.mp4"}}]}
    results = agent.execute_plan(
        json.loads(json.dumps(plan)), utterance="merge all the clips here", dry_run=True
    )
    for entry in results:
        if entry["tool"] == "run_concat":
            out = entry["result"]["outputs"][0]
            return [Path(p).name for p in out["input"]], Path(out["output"]).name
    raise AssertionError(f"no run_concat step in {results!r}")


def _clips(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy(CLIP, folder / name)


def test_a_glob_joins_in_natural_order(tmp_path: Path) -> None:
    _clips(tmp_path, "clip10.mp4", "clip2.mp4", "clip1.mp4")
    assert _joined(tmp_path, ["*.mp4"]) == (["clip1.mp4", "clip2.mp4", "clip10.mp4"], "all.mp4")


def test_a_folder_joins_in_natural_order(tmp_path: Path) -> None:
    _clips(tmp_path / "parts", "p10.mp4", "p9.mp4")
    assert _joined(tmp_path, ["parts"])[0] == ["p9.mp4", "p10.mp4"]


def test_a_second_run_does_not_join_its_own_result(tmp_path: Path) -> None:
    _clips(tmp_path, "clip1.mp4", "clip2.mp4", "all.mp4")
    assert _joined(tmp_path, ["*.mp4"])[0] == ["clip1.mp4", "clip2.mp4"]
