"""A file of a batch that fails is reported once (2026-10-24).

The Python CLI previews every real run (`app._existing_outputs`, a dry run) to find the outputs it
would replace. That preview kept the batch's failure hook, so a file whose failure a dry run
already sees (a password-protected PDF, found by the rehearsal before anything runs) was
printed twice: once by the preview, once by the run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from knaif.app import cli

FIXTURES = Path(__file__).parents[4] / "sandbox" / "fixtures" / "documents"

pytest.importorskip("pypdf")


def test_a_protected_pdf_in_a_batch_is_reported_once(tmp_path, monkeypatch) -> None:
    for name in ("sample.pdf", "sample-protected.pdf"):
        if not (FIXTURES / name).exists():
            pytest.skip("documents fixtures not generated")
        shutil.copy(FIXTURES / name, tmp_path / name)
    monkeypatch.chdir(tmp_path)
    plan = {"plan": [{"tool": "extract_text", "args": {"input": "*.pdf"}}]}
    env = {
        "KNAIF_LLM_BACKEND": "mock",
        "KNAIF_LLM_MOCK_RESPONSE": json.dumps(plan),
        "KNAIF_VIEW": None,
    }
    result = CliRunner().invoke(
        cli, ["run", "documents", "extract the text of every pdf here"], env=env
    )
    assert result.stderr.count("✗ sample-protected.pdf") == 1, result.stderr
    assert (tmp_path / "sample.txt").is_file()


def test_a_broken_pdf_prints_no_library_warnings(tmp_path, monkeypatch) -> None:
    """pypdf logs its own warnings ("invalid pdf header: b'not a'", "EOF marker not found"), and
    with no logging set up Python printed them into the tree, twice (owner,
    2026-10-24). Native prints none; the step's error says what went wrong."""
    import os
    import subprocess
    import sys

    (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
    plan = {"plan": [{"tool": "extract_text", "args": {"input": "broken.pdf"}}]}
    env = {**os.environ, "KNAIF_LLM_BACKEND": "mock", "KNAIF_LLM_MOCK_RESPONSE": json.dumps(plan)}
    env.pop("KNAIF_VIEW", None)
    # A real process: pytest's own log handlers would swallow the warnings in-process.
    out = subprocess.run(
        [sys.executable, "-c", "from knaif.app import cli; cli()", "run", "documents"]
        + ["extract the text of broken.pdf"],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert "could not open PDF broken.pdf" in out.stderr, out.stderr
    assert "invalid pdf header" not in out.stdout + out.stderr
    assert "EOF marker" not in out.stdout + out.stderr
