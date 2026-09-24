"""A default output name that already exists is never overwritten.

Found 2026-09-24 by the first native L4 run of documents: `convert sample.txt to markdown` wrote
`sample.md`, which already existed, and `protect_pdf` wrote `sample-protected.pdf` over an existing
file. Both runtimes did it; the Python eval never noticed because it grades the path a tool reports.
For a user, "convert notes.txt to markdown" destroyed an existing notes.md they never mentioned.

The rule, the same as ffmpeg's: a name the plan did not choose moves to the next free one
(`<name>-1<ext>`, `-2`, ...); an explicit `output` is a request and is honoured. Mirrored in
skills/documents/native/src/run.rs (`derive_output`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent

DOCUMENTS_SKILL_DIR = Path(__file__).parents[2]


def _run(tool: str, args: dict, sandbox: Path, *, dry_run: bool = False) -> dict:
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=sandbox, root=sandbox)
    results = agent.execute_plan(
        {"plan": [{"tool": tool, "args": args}]}, dry_run=dry_run, confirmed=True
    )
    return results[0]["result"]


def test_a_default_output_that_exists_moves_to_the_next_free_name(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("Invoice Alpha\n", encoding="utf-8")
    existing = tmp_path / "notes.md"
    existing.write_text("the user's own notes\n", encoding="utf-8")

    result = _run("convert_document", {"input": "notes.txt", "to_format": "md"}, tmp_path)

    assert Path(result["output"]).name == "notes-1.md"
    assert existing.read_text(encoding="utf-8") == "the user's own notes\n"
    assert "Invoice Alpha" in (tmp_path / "notes-1.md").read_text(encoding="utf-8")


def test_the_next_free_name_skips_every_taken_one(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    for name in ("notes.md", "notes-1.md", "notes-2.md"):
        (tmp_path / name).write_text("taken\n", encoding="utf-8")

    result = _run("convert_document", {"input": "notes.txt", "to_format": "md"}, tmp_path)

    assert Path(result["output"]).name == "notes-3.md"


def test_a_free_default_name_is_used_as_is(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    result = _run("convert_document", {"input": "notes.txt", "to_format": "md"}, tmp_path)
    assert Path(result["output"]).name == "notes.md"


def test_an_explicit_output_is_honoured_even_when_it_exists(tmp_path: Path) -> None:
    """Naming the destination is the request; renaming it would second-guess it."""
    (tmp_path / "notes.txt").write_text("fresh\n", encoding="utf-8")
    (tmp_path / "out.md").write_text("old\n", encoding="utf-8")

    result = _run(
        "convert_document", {"input": "notes.txt", "to_format": "md", "output": "out.md"}, tmp_path
    )

    assert Path(result["output"]).name == "out.md"
    assert "fresh" in (tmp_path / "out.md").read_text(encoding="utf-8")


def test_a_dry_run_reports_the_name_a_real_run_would_write(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("taken\n", encoding="utf-8")
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    results = agent.execute_plan(
        {"plan": [{"tool": "convert_document", "args": {"input": "notes.txt", "to_format": "md"}}]},
        dry_run=True,
    )
    assert "notes-1.md" in str(results)


def test_a_suffixed_default_moves_too(tmp_path: Path) -> None:
    pytest.importorskip("pikepdf")
    import pikepdf

    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    pdf.save(tmp_path / "doc.pdf")
    (tmp_path / "doc-protected.pdf").write_bytes(b"someone else's file")

    result = _run("protect_pdf", {"input": "doc.pdf", "password": "secret"}, tmp_path)

    assert Path(result["output"]).name == "doc-protected-1.pdf"
    assert (tmp_path / "doc-protected.pdf").read_bytes() == b"someone else's file"
