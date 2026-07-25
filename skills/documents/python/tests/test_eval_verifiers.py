from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from skills.documents.eval.verifiers import VERIFIERS


def _output(
    *,
    tool: str = "inspect_document",
    result: dict | None = None,
    outcome: str = "plan",
):
    return SimpleNamespace(
        outcome=outcome,
        plan={"plan": [{"tool": tool, "args": {"input": "sample.pdf"}}]},
        execution_results=[{"tool": tool, "result": result or {}}],
    )


def test_documents_verifiers_registered():
    assert set(VERIFIERS) >= {"cheap", "honest"}


def test_cheap_scores_expected_tool_from_plan(tmp_path: Path):
    result = VERIFIERS["cheap"](
        _output(tool="extract_text"),
        {"expected_tool": "extract_text"},
        tmp_path,
    )

    assert result.score == pytest.approx(1.0)
    assert result.verifier_kind == "plan"
    assert "tool:extract_text" in result.matched


def test_cheap_fails_wrong_tool(tmp_path: Path):
    result = VERIFIERS["cheap"](
        _output(tool="inspect_document"),
        {"expected_tool": "extract_text"},
        tmp_path,
    )

    assert result.score == pytest.approx(0.0)
    assert "tool: expected extract_text, got inspect_document" in result.failed


def test_honest_checks_page_count_and_text(tmp_path: Path):
    result = VERIFIERS["honest"](
        _output(result={"pages": 3, "text": "Alpha page one\nGamma page three"}),
        {"pages": 3, "text_contains": "Gamma page three"},
        tmp_path,
    )

    assert result.score == pytest.approx(1.0)
    assert result.verifier_kind == "output"
    assert "pages=3" in result.matched
    assert "text_contains:Gamma page three" in result.matched


def test_honest_checks_output_file_exists(tmp_path: Path):
    produced = tmp_path / "out.pdf"
    produced.write_bytes(b"%PDF tiny")

    result = VERIFIERS["honest"](
        _output(result={"output": str(produced)}),
        {"output_exists": True},
        tmp_path,
    )

    assert result.score == pytest.approx(1.0)
    assert f"output_exists:{produced}" in result.matched


def test_honest_reports_missing_expected_output_file(tmp_path: Path):
    missing = tmp_path / "missing.pdf"

    result = VERIFIERS["honest"](
        _output(result={"output": str(missing)}),
        {"output_exists": True},
        tmp_path,
    )

    assert result.score == pytest.approx(0.0)
    assert f"output_missing:{missing}" in result.failed


def test_success_grades_pages_from_materialized_artifact(tmp_path: Path):
    """Multi-step chains end on a tool whose dry-run dict has no `pages` field;
    the `success` verifier must read the page count off the real PDF artifact,
    not the dict (which would report None)."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=72, height=72)
    artifact = tmp_path / "combined.pdf"
    with open(artifact, "wb") as handle:
        writer.write(handle)

    from types import SimpleNamespace

    output = SimpleNamespace(
        outcome="plan",
        plan={
            "plan": [{"tool": "merge_pdfs", "args": {}}, {"tool": "add_page_numbers", "args": {}}]
        },
        # Dry-run dict from the final tool carries no `pages` field.
        execution_results=[{"tool": "add_page_numbers", "result": {"output": str(artifact)}}],
        artifact_path=artifact,
        artifact_paths=[artifact],
    )

    result = VERIFIERS["success"](
        output,
        {"expected_tool": "merge_pdfs", "output_exists": True, "pages": 4},
        tmp_path,
    )

    assert result.score == pytest.approx(1.0)
    assert "pages=4" in result.matched
    # And a wrong expectation must fail against the real file, not silently pass.
    wrong = VERIFIERS["success"](
        output,
        {"expected_tool": "merge_pdfs", "output_exists": True, "pages": 7},
        tmp_path,
    )
    assert "pages: expected 7, got 4" in wrong.failed
