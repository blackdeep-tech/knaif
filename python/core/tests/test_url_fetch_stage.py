"""The fetch stage: URL inputs are downloaded into the run scratch before anything else runs.

Runs the real documents skill against a local server (``KNAIF_NET_TEST_HOST`` sends the reserved
``files.knaif.test`` there). The rules:
- nothing is requested without approval: ``allow_download`` (the CLI's ``--allow-download``), or
  a confirmer that says yes for that URL, asked once per URL;
- a dry run requests nothing, and its plan already names the scratch path and user-side outputs;
- only the result reaches the user's folder, never the download, and the scratch is removed;
- a refused download stops the run, naming the reason, with nothing written;
- a skill that declares no ``url_inputs:`` says it works on local files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif import create_agent
from knaif.evalsuite.url_server import UrlServer
from knaif.scratch import scratch_root

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "sandbox" / "fixtures" / "documents"
H = "https://files.knaif.test/documents"


@pytest.fixture
def server(monkeypatch):
    if not (FIX / "sample.txt").is_file():
        pytest.skip("documents fixtures not generated — run `just eval-fixtures documents`")
    files = {"/documents/notes.txt": FIX / "sample.txt", "/documents/page": FIX / "sample.txt"}
    with UrlServer(files) as s:
        monkeypatch.setenv("KNAIF_NET_TEST_HOST", s.test_host_env())
        yield s


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    sb = tmp_path / "sb"
    sb.mkdir()
    return sb


def _convert(url: str) -> dict:
    return {"plan": [{"tool": "convert_document", "args": {"input": url, "to_format": "md"}}]}


def _run(agent, url: str, *, dry_run: bool = False) -> list[dict]:
    return agent.execute_plan(
        _convert(url), utterance=f"convert {url} to markdown", dry_run=dry_run, confirmed=True
    )


def _runs_left() -> set[str]:
    root = scratch_root()
    return {p.name for p in root.glob("run-*")} if root.exists() else set()


def test_an_approved_url_becomes_one_result_in_the_users_folder(server, sandbox):
    agent = create_agent("documents", sandbox=str(sandbox))
    agent.allow_download = True
    before = _runs_left()
    results = _run(agent, f"{H}/notes.txt")
    assert sorted(p.name for p in sandbox.iterdir()) == ["notes.md"], results
    assert "Invoice Alpha" in (sandbox / "notes.md").read_text(encoding="utf-8")
    assert [r["path"] for r in server.requests] == ["/documents/notes.txt"]
    assert _runs_left() <= before  # the scratch, download included, is gone
    assert agent.last_downloads[0]["url"] == f"{H}/notes.txt"


def test_without_approval_nothing_is_requested(server, sandbox):
    agent = create_agent("documents", sandbox=str(sandbox))
    results = _run(agent, f"{H}/notes.txt")
    assert server.requests == []
    assert list(sandbox.iterdir()) == []
    assert "--allow-download" in str(results[-1]["result"])


def test_a_declined_url_is_not_requested(server, sandbox):
    asked: list[tuple[str, int]] = []
    agent = create_agent("documents", sandbox=str(sandbox))
    agent.download_confirmer = lambda url, cap: asked.append((url, cap)) or False
    results = _run(agent, f"{H}/notes.txt")
    assert asked == [(f"{H}/notes.txt", agent.url_inputs.max_bytes)]
    assert server.requests == []
    assert results[-1]["result"]["status"] == "declined"
    assert list(sandbox.iterdir()) == []


def test_a_confirmed_url_is_asked_once_and_downloaded(server, sandbox):
    asked: list[str] = []
    agent = create_agent("documents", sandbox=str(sandbox))
    agent.download_confirmer = lambda url, cap: asked.append(url) or True
    _run(agent, f"{H}/notes.txt")
    assert asked == [f"{H}/notes.txt"]
    assert (sandbox / "notes.md").is_file()


def test_a_dry_run_requests_nothing_and_names_user_side_outputs(server, sandbox):
    agent = create_agent("documents", sandbox=str(sandbox))
    results = _run(agent, f"{H}/notes.txt", dry_run=True)
    assert server.requests == []
    assert list(sandbox.iterdir()) == []
    assert agent.last_downloads == [
        {"url": f"{H}/notes.txt", "path": agent.last_downloads[0]["path"], "dry_run": True}
    ]
    assert Path(agent.last_downloads[0]["path"]).name == "notes.txt"
    text = str(results)
    assert "https:" not in text.replace(H, "")  # no URL left as a path anywhere
    assert str(sandbox / "notes.md") in text or "notes.md" in text


def test_a_refused_download_stops_the_run_by_name(server, sandbox):
    agent = create_agent("documents", sandbox=str(sandbox))
    agent.allow_download = True
    with pytest.raises(ValueError, match="web page"):
        _run(agent, f"{H}/page?type=text/html")
    assert list(sandbox.iterdir()) == []


def test_a_skill_without_url_inputs_works_on_local_files(server, sandbox):
    agent = create_agent("documents", sandbox=str(sandbox))
    agent.allow_download = True
    agent.url_inputs = None
    results = _run(agent, f"{H}/notes.txt")
    assert results[-1]["tool"] == "clarify"
    assert "local files" in results[-1]["result"]["question"]
    assert server.requests == []


def test_an_executing_eval_fetches_during_its_preview_and_cleans_up(tmp_path, monkeypatch):
    """The executing eval plans with a dry run that stands in for a real one, so its preview
    reads real bytes (`fetch_in_preview`, set only by the eval); the scratch it fetched into is
    removed afterwards, and the planned download is still reported for the replay."""
    pdf = FIX / "sample.pdf"
    if not pdf.is_file():
        pytest.skip("documents fixtures not generated")
    with UrlServer({"/documents/report.pdf": pdf}) as s:
        monkeypatch.setenv("KNAIF_NET_TEST_HOST", s.test_host_env())
        sandbox = tmp_path / "sb"
        sandbox.mkdir()
        agent = create_agent("documents", sandbox=str(sandbox))
        agent.allow_download = True
        agent.fetch_in_preview = True
        url = f"{H}/report.pdf"
        before = _runs_left()
        results = agent.execute_plan(
            {"plan": [{"tool": "extract_text", "args": {"input": url}}]},
            utterance=f"extract the text from {url}",
            dry_run=True,
            confirmed=True,
        )
        assert len(s.requests) == 1
        assert "Gamma page three" in str(results)
        assert agent.last_downloads[0]["dry_run"] is True
        assert _runs_left() <= before
        assert list(sandbox.iterdir()) == []
