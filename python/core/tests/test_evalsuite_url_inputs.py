"""The eval harness serves URL-input rows from a local server.

A corpus whose utterances name ``https://files.knaif.test/...`` gets a ``UrlServer`` answering
the skill's ``data/eval_url_files.json`` aliases from the fixture folder, and
``KNAIF_NET_TEST_HOST`` pointing the reserved host at it. A corpus without URL rows starts
nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from knaif.evalsuite.url_server import start_for_corpus


def _skill(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "skill" / "data"
    data.mkdir(parents=True)
    (data / "eval_url_files.json").write_text(
        json.dumps({"files": {"/docs/report.pdf": "sample.pdf"}}), encoding="utf-8"
    )
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "sample.pdf").write_bytes(b"%PDF-1.4 test")
    return data, fixtures


def test_url_rows_start_a_server_for_the_test_host(tmp_path, monkeypatch):
    monkeypatch.delenv("KNAIF_NET_TEST_HOST", raising=False)
    data, fixtures = _skill(tmp_path)
    server = start_for_corpus(
        data, fixtures, ["extract the text from https://files.knaif.test/docs/report.pdf"]
    )
    assert server is not None
    try:
        host, _, base = os.environ["KNAIF_NET_TEST_HOST"].partition("=")
        assert host == "files.knaif.test" and base == server.base
        assert requests.get(server.url("docs/report.pdf"), timeout=5).content == b"%PDF-1.4 test"
    finally:
        server.__exit__(None, None, None)


def test_a_corpus_without_url_rows_starts_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("KNAIF_NET_TEST_HOST", raising=False)
    data, fixtures = _skill(tmp_path)
    assert start_for_corpus(data, fixtures, ["convert clip.mp4 to mkv"]) is None
    assert "KNAIF_NET_TEST_HOST" not in os.environ


def test_prepare_runs_after_provisioning_and_its_failure_fails_the_row(tmp_path):
    from knaif.evalsuite.chain import run_command_chain

    _data, fixtures = _skill(tmp_path)
    row = tmp_path / "rows" / "r1__0"
    seen: list[bool] = []
    run_command_chain([], fixtures, row, prepare=lambda d: seen.append((d / "sample.pdf").exists()))
    assert seen == [True]  # the row was provisioned first

    def boom(_d):
        raise ValueError("the link is a web page")

    failed = run_command_chain(["echo never"], fixtures, row, prepare=boom)
    assert failed[0]["command"] == "download" and failed[0]["returncode"] == 1
    assert "web page" in failed[0]["stderr"]


def test_a_rendered_download_is_fetched_into_the_rows_scratch(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from knaif.evalsuite.provisioning import row_scratch_dir
    from knaif.evalsuite.runner import _url_downloads
    from knaif.urls import UrlInputs
    from toolkits.net.python.download import DownloadFile

    monkeypatch.delenv("KNAIF_NET_TEST_HOST", raising=False)  # restored at teardown
    data, fixtures = _skill(tmp_path)
    server = start_for_corpus(data, fixtures, ["https://files.knaif.test/docs/report.pdf"])
    assert server is not None
    try:
        agent = SimpleNamespace(
            last_downloads=[
                {
                    "url": "https://files.knaif.test/docs/report.pdf",
                    "path": str(tmp_path / "knaif" / "run-1-ab" / "report.pdf"),
                    "dry_run": True,
                }
            ],
            tool_map={"download_file": DownloadFile()},
            url_inputs=UrlInputs(("application/pdf",), 1_000_000),
            skill_dir=tmp_path,
        )
        prepare = _url_downloads(agent)
        row = tmp_path / "rows" / "r1__0"
        prepare(row)
        assert (row_scratch_dir(row) / "report.pdf").read_bytes() == b"%PDF-1.4 test"
    finally:
        server.__exit__(None, None, None)


def test_no_planned_download_means_no_hook():
    from types import SimpleNamespace

    from knaif.evalsuite.runner import _url_downloads

    assert _url_downloads(SimpleNamespace(last_downloads=[], tool_map={}, url_inputs=None)) is None


class _ClarifyAgent:
    """Just enough agent for run_corpus: always asks."""

    registry = None
    artifact_runner = None
    allow_download = False
    fetch_in_preview = False

    def infer(self, *_a, **_k):
        return {"plan": [{"tool": "clarify", "args": {"question": "which?"}}]}


def test_an_executing_run_approves_downloads_and_fetches_in_its_preview(tmp_path):
    from knaif.evalsuite.corpus import CorpusRow
    from knaif.evalsuite.runner import run_corpus

    rows = [CorpusRow(id="r1", utterances=["x"], expected_outcome="clarify")]
    plan_only = _ClarifyAgent()
    run_corpus(plan_only, rows)
    assert (plan_only.allow_download, plan_only.fetch_in_preview) == (False, False)
    executing = _ClarifyAgent()
    run_corpus(executing, rows, execute=True, sandbox=tmp_path, fixture_dir=tmp_path)
    assert (executing.allow_download, executing.fetch_in_preview) == (True, True)
