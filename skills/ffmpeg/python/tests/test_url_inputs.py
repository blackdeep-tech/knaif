"""ffmpeg with a URL input: downloaded into the run scratch, the result in the user's folder.

Preflight checks inputs against the sandbox; the download is in the run scratch, which is the
run's own. The output is named on the user's side (`user_side_path`), and the download itself
never reaches the user's folder.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from knaif import create_agent
from knaif.evalsuite.url_server import UrlServer

REPO = Path(__file__).resolve().parents[4]
CLIP = REPO / "sandbox" / "fixtures" / "ffmpeg" / "clip.mov"
URL = "https://files.knaif.test/ffmpeg/holiday.mov"


@pytest.fixture
def served(monkeypatch):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    if not CLIP.is_file():
        pytest.skip("fixture not generated — run `just eval-fixtures ffmpeg`")
    with UrlServer({"/ffmpeg/holiday.mov": CLIP}) as s:
        monkeypatch.setenv("KNAIF_NET_TEST_HOST", s.test_host_env())
        yield s


def _convert(sandbox: Path, *, dry_run: bool):
    agent = create_agent("ffmpeg", sandbox=str(sandbox))
    agent.allow_download = True
    plan = {"plan": [{"tool": "convert_video", "args": {"inputs": [URL], "container": "mp4"}}]}
    results = agent.execute_plan(
        plan, utterance=f"convert {URL} to mp4", dry_run=dry_run, confirmed=True
    )
    return agent, results


def test_a_url_is_converted_into_the_users_folder(served, tmp_path):
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    agent, results = _convert(sandbox, dry_run=False)
    assert sorted(p.name for p in sandbox.iterdir()) == ["holiday_converted.mp4"], results[-1]
    assert len(served.requests) == 1
    assert agent.last_downloads[0]["content_type"] == "video/quicktime"


def test_a_dry_run_renders_the_command_without_a_request(served, tmp_path):
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    _agent, results = _convert(sandbox, dry_run=True)
    assert served.requests == []
    assert list(sandbox.iterdir()) == []
    commands = [c for r in results for c in (r.get("result") or {}).get("commands", [])]
    argv = commands[0]["command"]
    assert Path(argv[argv.index("-i") + 1]).name == "holiday.mov"
    assert Path(argv[-1]) == sandbox / "holiday_converted.mp4"
