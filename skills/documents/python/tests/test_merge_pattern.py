"""merge_pdfs over a pattern or a folder: "merge all the PDFs here into one".

Owner, 2026-10-19: `inputs` may name a glob or a folder; it expands through the
fs toolkit's `resolve_inputs`, like `concat_video`, in natural order (`a1`, `a2`, `a10`), PDFs only.
Each source page is a different width, so the merged file shows the order it was built in.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from knaif.agent import CommandAgent

SKILL_DIR = Path(__file__).parents[2]
WIDTHS = {"a1.pdf": 101, "a2.pdf": 102, "a10.pdf": 110}


def _sources(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, width in WIDTHS.items():
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(width, 200))
        pdf.save(folder / name)
    (folder / "notes.txt").write_text("not a pdf", encoding="utf-8")


def _widths(path: Path) -> list[int]:
    with pikepdf.open(path) as pdf:
        return [int(float(page.mediabox[2])) for page in pdf.pages]


def _merge(tmp_path: Path, inputs: list[str], utterance: str) -> None:
    agent = CommandAgent.from_skill(SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    plan = {"plan": [{"tool": "merge_pdfs", "args": {"inputs": inputs, "output": "all.pdf"}}]}
    agent.execute_plan(plan, utterance=utterance, dry_run=False, confirmed=True)


def test_a_glob_merges_in_natural_order(tmp_path: Path) -> None:
    _sources(tmp_path)
    _merge(tmp_path, ["*.pdf"], "merge all the PDFs here into one")
    assert _widths(tmp_path / "all.pdf") == [101, 102, 110]


def test_a_folder_merges_its_pdfs_only(tmp_path: Path) -> None:
    _sources(tmp_path / "scans")
    _merge(tmp_path, ["scans"], "merge every pdf in the scans folder into one")
    assert _widths(tmp_path / "all.pdf") == [101, 102, 110]


def test_named_files_keep_the_order_given(tmp_path: Path) -> None:
    _sources(tmp_path)
    _merge(tmp_path, ["a10.pdf", "a1.pdf"], "merge a10.pdf and a1.pdf")
    assert _widths(tmp_path / "all.pdf") == [110, 101]


def test_an_earlier_result_in_the_folder_is_not_merged_into_itself(tmp_path: Path) -> None:
    _sources(tmp_path)
    pikepdf.new().save(tmp_path / "all.pdf")  # a run before this one left it
    _merge(tmp_path, ["*.pdf"], "merge all the PDFs here into one")
    assert _widths(tmp_path / "all.pdf") == [101, 102, 110]


def test_a_merge_without_an_output_writes_merged_pdf(tmp_path: Path) -> None:
    """The model often leaves out merge_pdfs' output ("compress all PDFs here and merge them into
    one": 6 of 6 in one run); it defaults like concat_video's combined.mp4."""
    _sources(tmp_path)
    agent = CommandAgent.from_skill(SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    plan = {"plan": [{"tool": "merge_pdfs", "args": {"inputs": ["*.pdf"]}}]}
    agent.execute_plan(
        plan, utterance="merge all the PDFs here into one", dry_run=False, confirmed=True
    )
    assert _widths(tmp_path / "merged.pdf") == [101, 102, 110]


def _locked(path: Path) -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(path, encryption=pikepdf.Encryption(user="secret", owner="secret"))


def test_a_locked_pdf_among_the_inputs_says_so(tmp_path: Path) -> None:
    """ "merge all the PDFs here" over a folder holding a locked PDF (L3 parity,
    documents_batch_009): the dry run previews without opening it, as native does, and the run
    stops with native's sentence, not a pikepdf traceback."""
    _sources(tmp_path)
    _locked(tmp_path / "locked.pdf")
    agent = CommandAgent.from_skill(SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    plan = {"plan": [{"tool": "merge_pdfs", "args": {"inputs": ["*.pdf"], "output": "all.pdf"}}]}
    preview = agent.execute_plan(
        plan, utterance="merge all the PDFs here into one", dry_run=True, confirmed=False
    )
    assert any((r.get("result") or {}).get("mode") == "dry_run" for r in preview)
    with pytest.raises(ValueError) as stopped:
        agent.execute_plan(
            plan, utterance="merge all the PDFs here into one", dry_run=False, confirmed=True
        )
    assert str(stopped.value) == (
        "locked.pdf is password-protected. Unlock it first (with the password), then try again."
    )
    assert not (tmp_path / "all.pdf").exists()
