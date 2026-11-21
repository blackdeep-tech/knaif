"""A known-folder row, run by the eval the way `success` and exact-files run it, for real.

The plan writes into Downloads and asks for an alert. The scored run must put the file in the
row's redirected Downloads (``<row>.known/Downloads``), never in the fixtures or the user's real
folder; exact-files must deliver it there too and find the alert in the row's sink.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from knaif.agent import CommandAgent
from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.exact_files import replay_row
from knaif.evalsuite.provisioning import row_known_dir
from knaif.evalsuite.runner import run_corpus
from knaif.known_folders import ROOT_ENV

FFMPEG_SKILL_DIR = Path(__file__).parents[2]
FIXTURES = FFMPEG_SKILL_DIR.parents[1] / "sandbox" / "fixtures" / "ffmpeg"

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH"),
    pytest.mark.skipif(not (FIXTURES / "clip.mp4").exists(), reason="needs generated fixtures"),
]

UTTERANCE = "convert clip.mp4 to mkv, save it in Downloads and beep when done"
PLAN = {
    "plan": [
        {
            "tool": "convert_video",
            "args": {"inputs": ["clip.mp4"], "container": "mkv", "output": "Downloads/clip.mkv"},
        },
        {"tool": "alert_when_done", "args": {}},
    ]
}


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    src = tmp_path / "fixtures"
    src.mkdir()
    shutil.copy2(FIXTURES / "clip.mp4", src / "clip.mp4")
    return src


@pytest.fixture(autouse=True)
def _no_real_folders(tmp_path: Path, monkeypatch) -> None:
    # Should anything escape the eval's own redirection, it lands here and the test sees it.
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "escaped"))
    monkeypatch.setenv("KNAIF_OS_SINK", str(tmp_path / "escaped.jsonl"))


def test_the_scored_run_delivers_into_the_rows_downloads(tmp_path: Path, fixture_dir: Path):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=fixture_dir)
    row = CorpusRow(
        id="ffmpeg_folder_t",
        utterances=[UTTERANCE],
        expected_outcome="plan",
        fixture="clip.mp4",
        expected_tool="convert_video",
        tags=["known_folder"],
        expected_alert=True,
        expected_folder="downloads",
    )
    lane = tmp_path / "lane"
    with patch.object(agent, "infer", return_value=PLAN):
        [out] = run_corpus(
            agent, [row], execute=True, sandbox=lane, fixture_dir=fixture_dir, apply_retrieval=False
        )
    assert out.outcome == "plan", out.error
    assert out.alert is True
    assert (
        out.artifact_path == row_known_dir(lane / "ffmpeg_folder_t__0") / "Downloads" / "clip.mkv"
    )
    assert out.artifact_path.is_file()
    assert sorted(p.name for p in fixture_dir.iterdir()) == ["clip.mp4"]
    assert not (tmp_path / "escaped").exists()
    assert os.environ[ROOT_ENV] == str(tmp_path / "escaped")


def test_exact_files_delivers_into_downloads_and_records_the_alert(
    tmp_path: Path, fixture_dir: Path
):
    # The recorded plan has its alert split off, as the scoreboard stores it.
    recorded = {"plan": [PLAN["plan"][0]]}
    report = replay_row(
        lambda folder: CommandAgent.from_skill(FFMPEG_SKILL_DIR, root=folder),
        recorded,
        UTTERANCE,
        fixture_dir,
        tmp_path / "row",
        alert=True,
    )
    assert report["error"] is None
    assert report["delivered"] == ["~/Downloads/clip.mkv"]
    assert report["leftovers"] == []
    assert [a["outcome"] for a in report["alerts"]] == ["done"]
    assert not (tmp_path / "escaped").exists()
    assert not (tmp_path / "escaped.jsonl").exists()


def test_exact_files_with_a_relative_row_folder_still_delivers_beside_it(
    tmp_path: Path, fixture_dir: Path, monkeypatch
):
    """`evalsuite exact-files` hands `replay_row` a relative row folder (`sandbox/...`)."""
    monkeypatch.chdir(tmp_path)
    report = replay_row(
        lambda folder: CommandAgent.from_skill(FFMPEG_SKILL_DIR, root=folder),
        {"plan": [PLAN["plan"][0]]},
        UTTERANCE,
        fixture_dir,
        Path("rows") / "r__0",
        alert=True,
    )
    assert report["delivered"] == ["~/Downloads/clip.mkv"]
    assert (tmp_path / "rows" / "r__0.known" / "Downloads" / "clip.mkv").is_file()
