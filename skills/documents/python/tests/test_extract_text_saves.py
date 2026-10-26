"""`extract_text` saves the text as <name>.txt, one file or many (owner, 2026-10-23).

Found by the owner: "extract the text of <a pdf>" printed every page and saved
nothing. The text is now saved beside its source, under documents' free-name rule, and the
result still carries the pages and the text (the eval grades them, and the display previews
them).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from knaif.agent import CommandAgent

SKILL = Path(__file__).parents[2]
FIXTURE = Path(__file__).parents[4] / "sandbox" / "fixtures" / "documents" / "sample.pdf"

pytest.importorskip("pypdf")


def _run(folder: Path, *, dry_run: bool = False) -> list[dict]:
    agent = CommandAgent.from_skill(SKILL, sandbox=folder, root=folder)
    plan = {"plan": [{"tool": "extract_text", "args": {"input": "sample.pdf"}}]}
    return agent.execute_plan(
        plan, utterance="extract the text of sample.pdf", dry_run=dry_run, confirmed=True
    )


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    if not FIXTURE.exists():
        pytest.skip("documents fixtures not generated")
    shutil.copy(FIXTURE, tmp_path / "sample.pdf")
    return tmp_path


def test_one_files_text_is_saved_beside_it(folder: Path) -> None:
    result = _run(folder)[-1]["result"]
    saved = folder / "sample.txt"
    assert saved.is_file()
    assert Path(result["output"]).resolve() == saved.resolve()
    assert "Gamma page three" in saved.read_text(encoding="utf-8")
    # The result still carries what the eval grades and the display previews.
    assert len(result["pages"]) == 3 and "Gamma page three" in result["text"]


def test_a_taken_name_moves_to_the_next_free_one(folder: Path) -> None:
    (folder / "sample.txt").write_text("the user's own notes", encoding="utf-8")
    result = _run(folder)[-1]["result"]
    assert Path(result["output"]).name == "sample-1.txt"
    assert (folder / "sample.txt").read_text(encoding="utf-8") == "the user's own notes"


def test_a_dry_run_names_the_file_and_writes_nothing(folder: Path) -> None:
    result = _run(folder, dry_run=True)[-1]["result"]
    assert Path(result["output"]).name == "sample.txt"
    assert sorted(p.name for p in folder.iterdir()) == ["sample.pdf"]


def test_a_name_the_plan_gives_the_text_file_is_used(folder: Path) -> None:
    """A model check on the GPU: with the text saved, the model names the file ("output":
    "sample.txt") and the undeclared argument made Python ask instead (native accepted it)."""
    agent = CommandAgent.from_skill(SKILL, sandbox=folder, root=folder)
    plan = {
        "plan": [{"tool": "extract_text", "args": {"input": "sample.pdf", "output": "notes.txt"}}]
    }
    results = agent.execute_plan(
        plan,
        utterance="extract the text of sample.pdf into notes.txt",
        dry_run=False,
        confirmed=True,
    )
    assert results[-1]["tool"] == "extract_text", results
    assert "Gamma page three" in (folder / "notes.txt").read_text(encoding="utf-8")
    # The result still carries the text when the plan named the file (the GPU re-check: the
    # grade's text check found nothing, the text came back only without a name).
    assert "Gamma page three" in results[-1]["result"]["text"]
