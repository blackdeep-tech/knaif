"""Documents grading checks the transformation, not only the tool and a file (release plan R3a).

87 of 132 plan rows were graded on the tool choice and that some output file existed, so
`documents_036` ("rotate sample.pdf 90 degrees") scored 1.0 with only page 1 rotated. The
`success` verifier now opens the produced artifact and checks what the row asks for: rotation per
page, page order and content, watermark and page-number text, encryption, the text layer, the
converted format, and that a compression is no larger than its input. Each check is pinned here
against the wrong result as well as the right one.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from knaif.agent import CommandAgent
from skills.documents.eval.verifiers import VERIFIERS

pypdf = pytest.importorskip("pypdf")

DOCUMENTS = Path(__file__).parents[2]
FIXTURE = Path(__file__).parents[4] / "contracts" / "parity" / "fixtures" / "documents"


def _grade(artifact: Path, criteria: dict, sandbox: Path, tool: str = "rotate_pages"):
    output = SimpleNamespace(
        outcome="plan",
        plan={"plan": [{"tool": tool, "args": {}}]},
        execution_results=[{"tool": tool, "result": {"output": str(artifact)}}],
        artifact_path=artifact,
        artifact_paths=[artifact],
    )
    return VERIFIERS["success"](output, {"expected_tool": tool, **criteria}, sandbox)


def _sample(tmp_path: Path) -> Path:
    shutil.copy(FIXTURE / "sample.pdf", tmp_path / "sample.pdf")
    return tmp_path / "sample.pdf"


def _write(pages, path: Path, *, password: str | None = None) -> Path:
    writer = pypdf.PdfWriter()
    for page in pages:
        writer.add_page(page)
    if password:
        writer.encrypt(password)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def _tool(tmp_path: Path, tool: str, args: dict) -> Path:
    _sample(tmp_path)
    agent = CommandAgent.from_skill(DOCUMENTS, sandbox=tmp_path, root=tmp_path)
    agent.execute_plan({"plan": [{"tool": tool, "args": args}]}, dry_run=False, confirmed=True)
    return tmp_path / args["output"]


# -- rotation ------------------------------------------------------------------------------------


def test_documents_036_rotating_one_page_of_three_fails(tmp_path: Path) -> None:
    pages = list(pypdf.PdfReader(_sample(tmp_path)).pages)
    pages[0].rotate(90)
    one = _write(pages, tmp_path / "one.pdf")
    assert _grade(one, {"rotation": 90}, tmp_path).score < 1.0


def test_rotating_every_page_passes(tmp_path: Path) -> None:
    pages = [p.rotate(90) for p in pypdf.PdfReader(_sample(tmp_path)).pages]
    every = _write(pages, tmp_path / "all.pdf")
    assert _grade(every, {"rotation": 90}, tmp_path).score == pytest.approx(1.0)


def test_rotation_can_name_one_page(tmp_path: Path) -> None:
    pages = list(pypdf.PdfReader(_sample(tmp_path)).pages)
    pages[1].rotate(180)
    art = _write(pages, tmp_path / "p2.pdf")
    assert _grade(art, {"rotation": [0, 180, 0]}, tmp_path).score == pytest.approx(1.0)
    assert _grade(art, {"rotation": 180}, tmp_path).score < 1.0


def test_rotation_one_of_accepts_either_direction_but_not_a_mix(tmp_path: Path) -> None:
    pages = [p.rotate(270) for p in pypdf.PdfReader(_sample(tmp_path)).pages]
    art = _write(pages, tmp_path / "ccw.pdf")
    assert _grade(art, {"rotation_one_of": [90, 270]}, tmp_path).score == pytest.approx(1.0)
    mixed = list(pypdf.PdfReader(_sample(tmp_path)).pages)
    mixed[0].rotate(90)
    mixed[1].rotate(270)
    art2 = _write(mixed, tmp_path / "mixed.pdf")
    assert _grade(art2, {"rotation_one_of": [90, 270]}, tmp_path).score < 1.0


# -- page order and content ----------------------------------------------------------------------


def test_page_texts_check_order_and_count(tmp_path: Path) -> None:
    p = list(pypdf.PdfReader(_sample(tmp_path)).pages)
    art = _write([p[1], p[2], p[0]], tmp_path / "reordered.pdf")
    assert _grade(art, {"page_texts": ["Beta", "Gamma", "Alpha"]}, tmp_path).score == 1.0
    assert _grade(art, {"page_texts": ["Alpha", "Beta", "Gamma"]}, tmp_path).score < 1.0
    assert _grade(art, {"page_texts": ["Beta", "Gamma"]}, tmp_path).score < 1.0


def test_an_empty_marker_accepts_any_page(tmp_path: Path) -> None:
    p = list(pypdf.PdfReader(_sample(tmp_path)).pages)
    art = _write([p[0], p[1]], tmp_path / "two.pdf")
    assert _grade(art, {"page_texts": ["Alpha", ""]}, tmp_path).score == 1.0


# -- overlays: watermark and page numbers, produced by the real tools ------------------------------


def test_every_page_contains_the_watermark(tmp_path: Path) -> None:
    art = _tool(tmp_path, "watermark", {"input": "sample.pdf", "text": "DRAFT", "output": "wm.pdf"})
    assert _grade(art, {"every_page_contains": "DRAFT"}, tmp_path).score == 1.0
    assert _grade(art, {"every_page_contains": "CONFIDENTIAL"}, tmp_path).score < 1.0


def test_page_numbers_start_where_asked(tmp_path: Path) -> None:
    art = _tool(
        tmp_path, "add_page_numbers", {"input": "sample.pdf", "start_at": 5, "output": "n.pdf"}
    )
    assert _grade(art, {"page_numbers_from": 5}, tmp_path).score == 1.0
    assert _grade(art, {"page_numbers_from": 1}, tmp_path).score < 1.0


# -- encryption, text layer, format, size -----------------------------------------------------------


def test_encryption_and_the_password(tmp_path: Path) -> None:
    pages = list(pypdf.PdfReader(_sample(tmp_path)).pages)
    locked = _write(pages, tmp_path / "locked.pdf", password="hunter2")
    assert (
        _grade(locked, {"artifact_encrypted": True, "decrypts_with": "hunter2"}, tmp_path).score
        == 1.0
    )
    assert _grade(locked, {"decrypts_with": "wrong"}, tmp_path).score < 1.0
    open_ = _write(pages, tmp_path / "open.pdf")
    assert _grade(open_, {"artifact_encrypted": False}, tmp_path).score == 1.0
    assert _grade(open_, {"artifact_encrypted": True}, tmp_path).score < 1.0


def test_artifact_text_and_format(tmp_path: Path) -> None:
    sample = _sample(tmp_path)
    assert _grade(sample, {"artifact_text_contains": "Gamma page three"}, tmp_path).score == 1.0
    assert _grade(sample, {"artifact_text_contains": "Scanned image"}, tmp_path).score < 1.0
    assert _grade(sample, {"artifact_format": "pdf"}, tmp_path).score == 1.0
    md = tmp_path / "out.md"
    md.write_text("# Sample\n\nAlpha appears here.\n", encoding="utf-8")
    assert (
        _grade(md, {"artifact_format": "md", "artifact_text_contains": "Alpha"}, tmp_path).score
        == 1.0
    )
    assert _grade(md, {"artifact_format": "pdf"}, tmp_path).score < 1.0


def test_a_compression_is_no_larger_than_its_input(tmp_path: Path) -> None:
    sample = _sample(tmp_path)
    same = tmp_path / "same.pdf"
    shutil.copy(sample, same)
    assert _grade(same, {"no_larger_than": "sample.pdf"}, tmp_path).score == 1.0
    bigger = tmp_path / "bigger.pdf"
    bigger.write_bytes(sample.read_bytes() + b"%" * 500)
    assert _grade(bigger, {"no_larger_than": "sample.pdf"}, tmp_path).score < 1.0


def test_no_larger_than_finds_the_input_beside_the_artifact(tmp_path: Path) -> None:
    """The eval runs each row in its own directory, so `sandbox` is not where the input is."""
    row_dir = tmp_path / "row_007"
    row_dir.mkdir()
    shutil.copy(FIXTURE / "sample.pdf", row_dir / "sample.pdf")
    out = row_dir / "sample-compressed.pdf"
    shutil.copy(FIXTURE / "sample.pdf", out)
    elsewhere = tmp_path / "base"
    elsewhere.mkdir()
    assert _grade(out, {"no_larger_than": "sample.pdf"}, elsewhere).score == 1.0


def test_a_missing_artifact_fails_every_artifact_check(tmp_path: Path) -> None:
    ghost = tmp_path / "ghost.pdf"
    result = _grade(ghost, {"rotation": 90, "page_texts": ["Alpha"]}, tmp_path)
    assert result.score < 1.0
    assert {"rotation: no artifact", "page_texts: no artifact"} <= set(result.failed)


def test_text_excludes_for_read_rows() -> None:
    """`extract the first 2 pages` must not return page 3's text."""
    output = SimpleNamespace(
        outcome="plan",
        plan={"plan": [{"tool": "extract_text", "args": {}}]},
        execution_results=[{"tool": "extract_text", "result": {"text": "Alpha Beta Gamma"}}],
    )
    crit = {"expected_tool": "extract_text", "text_contains": "Beta", "text_excludes": "Gamma"}
    assert VERIFIERS["success"](output, crit, Path(".")).score < 1.0
