"""A dry run of a chain previews a step whose input an earlier step has yet to write.

A many-files batches run (2026-10-22): "unlock sample-protected.pdf … and rotate every page 90
degrees" errored in its dry run, because `rotate_pages` counted the pages of `sample.pdf`, which
only the real run writes (the intermediate lives in the run scratch). The page-counting steps now
check such an input when they run, as native's previews do; a real run is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent

DOCUMENTS_SKILL_DIR = Path(__file__).parents[2]

pikepdf = pytest.importorskip("pikepdf")


def _pdf(path: Path, pages: int) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page()
    pdf.save(path)
    return path


def _files(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


MERGE = {"tool": "merge_pdfs", "args": {"inputs": ["a.pdf", "b.pdf"], "output": "merged.pdf"}}

LATER_STEPS = {
    "rotate_pages": {"input": "merged.pdf", "degrees": 90, "pages": "all"},
    "remove_pages": {"input": "merged.pdf", "pages": "1"},
    "reorder_pages": {"input": "merged.pdf", "order": "reverse"},
    "split_pdf": {"input": "merged.pdf", "ranges": "1-2"},
    "split_pdf_every": {"input": "merged.pdf", "every": 1},
}


@pytest.mark.parametrize("tool", sorted(LATER_STEPS))
def test_a_dry_run_previews_a_step_on_an_earlier_steps_output(tmp_path: Path, tool: str) -> None:
    _pdf(tmp_path / "a.pdf", 1)
    _pdf(tmp_path / "b.pdf", 2)
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    plan = {"plan": [MERGE, {"tool": tool, "args": dict(LATER_STEPS[tool])}]}
    results = agent.execute_plan(
        plan, utterance=f"merge a.pdf and b.pdf, then {tool}", dry_run=True, confirmed=True
    )
    assert [r["tool"] for r in results] == ["merge_pdfs", tool]
    assert results[1]["result"]["mode"] == "dry_run"
    assert _files(tmp_path) == {"a.pdf", "b.pdf"}


def test_a_dry_run_still_checks_the_pages_of_a_file_that_exists(tmp_path: Path) -> None:
    _pdf(tmp_path / "a.pdf", 3)
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    plan = {"plan": [{"tool": "rotate_pages", "args": {"input": "a.pdf", "degrees": 90}}]}
    results = agent.execute_plan(plan, utterance="rotate a.pdf", dry_run=True, confirmed=True)
    assert results[0]["result"]["rotated_pages"] == [1, 2, 3]
