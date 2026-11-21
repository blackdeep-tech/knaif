"""`batch_output:`: a tool that writes a file only when core repeats it over many files.

(owner, 2026-10-20): "compress all the PDFs and extract their text" leaves a .txt
per PDF, while a single-file `extract_text` still prints. The tool declares the extension in its
own tools.yaml; it then accepts `output`, which the prompt never lists (an optional arg would
have changed 128 of the 212 documents prompts), and the lift names each file's output
(contracts/parity/batch_chain_cases.json, `batch_outputs`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from knaif import create_agent
from knaif.planner import validate_plan
from knaif.registry import ToolDef, load_registry


@pytest.fixture
def make_registry(tmp_path: Path):
    def _make(batch_output: str | None) -> dict[str, ToolDef]:
        cfg = {
            "description": "Read a document's text.",
            "required_args": ["input"],
            "optional_args": ["pages"],
            "effects": ["fs.read"],
        }
        if batch_output:
            cfg["batch_output"] = batch_output
        path = tmp_path / "tools.yaml"
        path.write_text(yaml.safe_dump({"read_text": cfg}), encoding="utf-8")
        return load_registry(path)

    return _make


def test_the_declaration_is_read(make_registry) -> None:
    assert make_registry("txt")["read_text"].batch_output == "txt"
    assert make_registry(None)["read_text"].batch_output is None


def test_a_batch_output_tool_accepts_an_output(make_registry) -> None:
    plan = {"plan": [{"tool": "read_text", "args": {"input": "a.pdf", "output": "a.txt"}}]}
    validate_plan(plan, make_registry("txt"), Path("."))


def test_other_tools_still_refuse_one(make_registry) -> None:
    plan = {"plan": [{"tool": "read_text", "args": {"input": "a.pdf", "output": "a.txt"}}]}
    with pytest.raises(ValueError, match="output"):
        validate_plan(plan, make_registry(None), Path("."))


def test_extract_text_declares_output_since_it_always_saves() -> None:
    """(owner, 2026-10-23): extract_text saves one file's text too, and the model
    names that file once it knows; `output` is declared, so the prompt lists it, while a batch
    still gets its per-file names from core (`batch_output`)."""
    agent = create_agent("documents")
    assert agent.registry["extract_text"].batch_output == "txt"
    system, _ = agent.build_prompt("extract the text from sample.pdf")
    line = system.split("  - extract_text:", 1)[1].splitlines()[1]
    assert "output (optional" in line, line


def _pdf(path: Path, text: str) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()


def test_text_over_many_pdfs_is_saved_next_to_each(tmp_path: Path) -> None:
    pytest.importorskip("reportlab")
    _pdf(tmp_path / "a.pdf", "Alpha text")
    _pdf(tmp_path / "b.pdf", "Beta text")
    agent = create_agent("documents", sandbox=tmp_path)
    agent.root = tmp_path
    plan = {"plan": [{"tool": "extract_text", "args": {"input": "*.pdf"}}]}
    agent.execute_plan(
        plan, utterance="extract the text of every pdf here", dry_run=False, confirmed=True
    )
    assert "Alpha text" in (tmp_path / "a.txt").read_text(encoding="utf-8")
    assert "Beta text" in (tmp_path / "b.txt").read_text(encoding="utf-8")


def test_one_pdfs_text_is_saved_too(tmp_path: Path) -> None:
    """Since 2026-10-23 (owner) one file's text is saved like a batch's; the result
    still carries the text (the display previews it)."""
    pytest.importorskip("reportlab")
    _pdf(tmp_path / "a.pdf", "Alpha text")
    agent = create_agent("documents", sandbox=tmp_path)
    agent.root = tmp_path
    plan = {"plan": [{"tool": "extract_text", "args": {"input": "a.pdf"}}]}
    results = agent.execute_plan(
        plan, utterance="extract the text of a.pdf", dry_run=False, confirmed=True
    )
    assert "Alpha text" in (tmp_path / "a.txt").read_text(encoding="utf-8")
    assert "Alpha text" in str(results)


def test_the_preview_counts_the_text_files(tmp_path: Path) -> None:
    pytest.importorskip("reportlab")
    for name in ("a.pdf", "b.pdf"):
        _pdf(tmp_path / name, name)
    shown: list[str] = []
    agent = create_agent("documents", sandbox=tmp_path)
    agent.root = tmp_path
    agent.show_plan = True
    agent.plan_display = shown.append
    plan = {"plan": [{"tool": "extract_text", "args": {"input": "*.pdf"}}]}
    agent.execute_plan(plan, utterance="extract the text of every pdf here", dry_run=True)
    assert shown[-1] in ("leaves a.txt and b.txt", "leaves 2 text files"), shown
