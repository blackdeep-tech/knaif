"""documents on the run scratch: only deliverables reach the user's folder.

A chain's intermediate (an `output` a later step reads, with a name the user did not give) goes
to the run scratch, and an output is written there first and moved into place only once it is
known to be good. Same rules as ffmpeg; the check, run before this change, found 8 of 154
executed documents rows leaving their intermediate behind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent

DOCUMENTS_SKILL_DIR = Path(__file__).parents[2]

pikepdf = pytest.importorskip("pikepdf")


def _pdf(path: Path, pages: int = 3) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page()
    pdf.save(path)
    return path


def _pages(path: Path) -> int:
    with pikepdf.Pdf.open(path) as pdf:
        return len(pdf.pages)


def _run(plan: list[dict], sandbox: Path, utterance: str | None, **kw) -> list[dict]:
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=sandbox, root=sandbox)
    kw.setdefault("dry_run", False)
    return agent.execute_plan({"plan": plan}, utterance=utterance, confirmed=True, **kw)


def _files(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


TRIM_THEN_NUMBER = [
    {
        "tool": "remove_pages",
        "args": {"input": "sample.pdf", "pages": "-1", "output": "sample_trimmed.pdf"},
    },
    {"tool": "add_page_numbers", "args": {"input": "sample_trimmed.pdf"}},
]


def test_a_chain_leaves_only_its_deliverable(tmp_path: Path) -> None:
    _pdf(tmp_path / "sample.pdf")
    _run(TRIM_THEN_NUMBER, tmp_path, "remove the last page of sample.pdf, then add page numbers")
    assert _files(tmp_path) == {"sample.pdf", "sample_trimmed-numbered.pdf"}
    assert _pages(tmp_path / "sample_trimmed-numbered.pdf") == 2


def test_an_intermediate_the_user_named_stays(tmp_path: Path) -> None:
    _pdf(tmp_path / "sample.pdf")
    _run(
        TRIM_THEN_NUMBER,
        tmp_path,
        "remove the last page of sample.pdf into sample_trimmed.pdf, then number sample_trimmed.pdf",
    )
    assert _files(tmp_path) == {"sample.pdf", "sample_trimmed.pdf", "sample_trimmed-numbered.pdf"}


def test_a_default_output_of_a_routed_intermediate_lands_in_the_users_folder(
    tmp_path: Path,
) -> None:
    """compress_pdf names its output after its input. Its input is in scratch, so the name is
    anchored where the intermediate would have been: the user's folder, never the scratch."""
    _pdf(tmp_path / "sample.pdf")
    plan = [
        {
            "tool": "rotate_pages",
            "args": {"input": "sample.pdf", "degrees": 90, "output": "sample_rotated.pdf"},
        },
        {"tool": "compress_pdf", "args": {"input": "sample_rotated.pdf"}},
    ]
    results = _run(plan, tmp_path, "rotate sample.pdf 90 degrees and then compress it")
    assert _files(tmp_path) == {"sample.pdf", "sample_rotated-compressed.pdf"}
    verified = [r["result"] for r in results if r["tool"] == "verify_output"][0]
    assert verified["exists"] and verified["pages_preserved"]


def test_a_step_that_fails_mid_write_leaves_the_folder_as_it_was(
    tmp_path: Path, monkeypatch
) -> None:
    _pdf(tmp_path / "sample.pdf")

    def half_written(*, output: Path, **_kw) -> None:
        Path(output).write_bytes(b"%PDF-1.7 partial")
        raise RuntimeError("disk full")

    CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    import _skill_oop_documents_steps as steps  # loaded by the skill above

    monkeypatch.setattr(steps, "_write_text_overlay_pdf", half_written)
    plan = [{"tool": "add_page_numbers", "args": {"input": "sample.pdf"}}]
    with pytest.raises(RuntimeError, match="disk full"):
        _run(plan, tmp_path, "number sample.pdf")
    assert _files(tmp_path) == {"sample.pdf"}


def test_an_empty_result_is_refused_and_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    _pdf(tmp_path / "sample.pdf")
    CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    import _skill_oop_documents_steps as steps  # loaded by the skill above

    monkeypatch.setattr(steps, "_write_text_overlay_pdf", lambda **kw: None)
    with pytest.raises(ValueError, match="no file"):
        _run(
            [{"tool": "add_page_numbers", "args": {"input": "sample.pdf"}}],
            tmp_path,
            "number sample.pdf",
        )
    assert _files(tmp_path) == {"sample.pdf"}


def test_a_dry_run_reports_the_final_path_and_writes_nothing(tmp_path: Path) -> None:
    _pdf(tmp_path / "sample.pdf")
    results = _run(
        [{"tool": "rotate_pages", "args": {"input": "sample.pdf", "degrees": 90}}],
        tmp_path,
        "rotate sample.pdf",
        dry_run=True,
    )
    assert Path(results[0]["result"]["output"]) == (tmp_path / "sample-rotated.pdf").resolve()
    assert _files(tmp_path) == {"sample.pdf"}


def test_a_multi_range_split_delivers_every_file(tmp_path: Path) -> None:
    _pdf(tmp_path / "sample.pdf")
    _run(
        [{"tool": "split_pdf", "args": {"input": "sample.pdf", "ranges": "1,2-3"}}],
        tmp_path,
        "split sample.pdf into page 1 and pages 2-3",
    )
    assert _files(tmp_path) == {"sample.pdf", "sample-pages-1.pdf", "sample-pages-2_3.pdf"}


def test_a_merge_is_a_join_so_its_unnamed_result_is_routed(tmp_path: Path) -> None:
    """merge_pdfs reads several files and writes ONE (`joins_inputs: true`, like concat). Without
    that, the router takes it for a fan-out and leaves `combined.pdf` behind."""
    _pdf(tmp_path / "a.pdf", 1)
    _pdf(tmp_path / "b.pdf", 2)
    plan = [
        {"tool": "merge_pdfs", "args": {"inputs": ["a.pdf", "b.pdf"], "output": "combined.pdf"}},
        {"tool": "add_page_numbers", "args": {"input": "combined.pdf"}},
    ]
    _run(plan, tmp_path, "merge a.pdf and b.pdf, then add page numbers")
    assert _files(tmp_path) == {"a.pdf", "b.pdf", "combined-numbered.pdf"}
    assert _pages(tmp_path / "combined-numbered.pdf") == 3
