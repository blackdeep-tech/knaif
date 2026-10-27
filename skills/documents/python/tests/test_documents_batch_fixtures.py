"""Fixtures for folder rows and one-PDF-into-many rows.

- `batch_pdf/`: the folder a folder row copies ("compress every pdf here and add page numbers"):
  three plain text PDFs, one password-protected (a step that cannot open it fails, the rest
  finish) and one scanned (no text layer).
- `report.pdf`: 30 pages, for "one file per page" and "chunks of 10 pages".
- Regen runs this module once per fixture name with that name as the output path; a name in
  `batch_pdf/` must still generate into the fixture root, not into `batch_pdf/`.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("pikepdf")

FIXTURES_PY = Path(__file__).resolve().parents[2] / "eval" / "fixtures.py"


def _module():
    spec = importlib.util.spec_from_file_location("documents_fixtures", FIXTURES_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_batch_folder_and_the_report_are_generated(tmp_path: Path):
    import pikepdf

    _module().generate_documents_fixtures(tmp_path)
    batch = tmp_path / "batch_pdf"
    names = sorted(p.name for p in batch.iterdir())
    assert names == ["contract.pdf", "invoice.pdf", "locked.pdf", "minutes.pdf", "scan.pdf"]
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(batch / "locked.pdf")
    with pikepdf.open(tmp_path / "report.pdf") as pdf:
        assert len(pdf.pages) == 30


def test_the_names_are_registered_for_regen():
    names = _module().FIXTURE_NAMES
    for name in (
        "batch_pdf/invoice.pdf",
        "batch_pdf/contract.pdf",
        "batch_pdf/minutes.pdf",
        "batch_pdf/locked.pdf",
        "batch_pdf/scan.pdf",
        "report.pdf",
    ):
        assert name in names


def test_a_batch_name_generates_into_the_fixture_root(tmp_path: Path):
    out = tmp_path / "batch_pdf" / "invoice.pdf"
    out.parent.mkdir()
    subprocess.run([sys.executable, str(FIXTURES_PY), str(out)], check=True)
    assert (tmp_path / "sample.pdf").exists()
    assert (tmp_path / "report.pdf").exists()
    assert out.exists()
    assert not (out.parent / "sample.pdf").exists()


def test_the_one_file_and_merge_folders_are_generated(tmp_path: Path):
    """`one_report/` holds only report.pdf (30 pages); `pdfs/` three one-page parts whose text
    names them, so a merge's page order can be read back."""
    import pikepdf
    from pypdf import PdfReader

    _module().generate_documents_fixtures(tmp_path)
    assert sorted(p.name for p in (tmp_path / "one_report").iterdir()) == ["report.pdf"]
    with pikepdf.open(tmp_path / "one_report" / "report.pdf") as pdf:
        assert len(pdf.pages) == 30
    parts = tmp_path / "pdfs"
    assert sorted(p.name for p in parts.iterdir()) == ["part1.pdf", "part10.pdf", "part2.pdf"]
    for n in (1, 2, 10):
        text = PdfReader(parts / f"part{n}.pdf").pages[0].extract_text()
        assert f"Part {n}" in text


@pytest.mark.parametrize("name", ["one_report/report.pdf", "pdfs/part1.pdf"])
def test_every_folder_name_generates_into_the_fixture_root(tmp_path: Path, name: str):
    assert name in _module().FIXTURE_NAMES
    out = tmp_path / name
    out.parent.mkdir()
    subprocess.run([sys.executable, str(FIXTURES_PY), str(out)], check=True)
    assert (tmp_path / "sample.pdf").exists()
    assert out.exists()
    assert not (out.parent / "sample.pdf").exists()
