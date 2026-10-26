"""A document's text, one layout on both runtimes (owner, 2026-10-24).

Native's extracted text started each page with a line break and put pages a blank line further
apart, so every match span was one higher than Python's; Python wrote Windows line ends on
Windows. The owner chose Python's layout with plain `\\n` everywhere: each page's text without
the line breaks at its edges, pages one blank line apart, one `\\n` at the end, for the saved
`.txt`, `convert_document` to text and `run --stdout` alike. Native's side is
`skills/documents/native/src/text.rs` and the `cli_output_cases.json` PDF cases.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from knaif.agent import CommandAgent

SKILL = Path(__file__).parents[2]
REPO = Path(__file__).parents[4]
FIXTURE = REPO / "sandbox" / "fixtures" / "documents" / "sample.pdf"
TEXT = "Alpha page one\n\nBeta page two\n\nGamma page three\n"

pytest.importorskip("pypdf")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    if not FIXTURE.exists():
        pytest.skip("documents fixtures not generated")
    shutil.copy(FIXTURE, tmp_path / "sample.pdf")
    return tmp_path


def _run(folder: Path, tool: str, **args: object) -> dict:
    agent = CommandAgent.from_skill(SKILL, sandbox=folder, root=folder)
    plan = {"plan": [{"tool": tool, "args": {"input": "sample.pdf", **args}}]}
    utterance = f"{tool} sample.pdf"
    results = agent.execute_plan(plan, utterance=utterance, dry_run=False, confirmed=True)
    return results[-1]["result"]


def test_the_saved_text_has_one_layout_and_plain_line_ends(folder: Path) -> None:
    result = _run(folder, "extract_text")
    assert (folder / "sample.txt").read_bytes() == TEXT.encode("utf-8")
    assert result["text"] == TEXT


def test_a_page_has_no_line_breaks_at_its_edges(folder: Path) -> None:
    pages = _run(folder, "extract_text")["pages"]
    assert [p["text"] for p in pages] == ["Alpha page one", "Beta page two", "Gamma page three"]


def test_a_match_span_counts_from_the_page_start(folder: Path) -> None:
    matches = _run(folder, "find_in_document", query="Beta")["matches"]
    assert [(m["page"], m["span"]) for m in matches] == [(2, [0, 4])]


def test_converting_to_text_writes_the_same_bytes(folder: Path) -> None:
    _run(folder, "convert_document", to_format="txt")
    assert (folder / "sample.txt").read_bytes() == TEXT.encode("utf-8")


def test_stdout_writes_plain_line_ends(folder: Path) -> None:
    plan = '{"plan":[{"tool":"extract_text","args":{"input":"sample.pdf"}}]}'
    env = {**os.environ, "KNAIF_LLM_BACKEND": "mock", "KNAIF_LLM_MOCK_RESPONSE": plan}
    env.pop("KNAIF_VIEW", None)
    out = subprocess.run(
        [sys.executable, "-c", "from knaif.app import cli; cli()", "run", "documents"]
        + ["--stdout", "extract the text of sample.pdf"],
        cwd=folder,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    assert out.stdout == TEXT.encode("utf-8")
