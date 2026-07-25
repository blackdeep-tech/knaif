"""Phase E coverage — live external-binary paths, graceful degradation, messy PDFs.

Three coverage gaps from the build-plan audit (docs/plans/2026-06-22-...md, Phase E):

1. Live ``soffice`` (Office->PDF) and ``gs`` (lossy compress) runs. These skip
   cleanly when the binary is absent, so the suite — and the committed eval
   snapshot — stay portable on a binary-less CI box.
2. Graceful degradation: the no-binary paths that actually ship (gs is
   AGPL-banned from bundling, so the shipped default for ``balanced`` is the
   lossless fallback) must return a clear message / correct method, not crash.
3. Messy real-world PDFs: multi-column layout and large page counts exercise
   pypdf/pdfminer beyond the tiny happy-path fixtures (build-plan Risk #1).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.handler_api import HandlerContext
from knaif.skill import Skill

DOCUMENTS_SKILL_DIR = Path(__file__).parents[2]
OPTIONAL_DOCUMENT_MODULES = ("pikepdf", "pypdf", "docx", "pptx", "openpyxl", "PIL", "reportlab")


# ── helpers (kept local so this file is self-contained) ─────────────────────────


def _require_documents_extra() -> None:
    for module_name in OPTIONAL_DOCUMENT_MODULES:
        pytest.importorskip(module_name)


def _documents_handlers_module():
    Skill.load(DOCUMENTS_SKILL_DIR)
    return sys.modules["_skill_oop_documents_handlers"]


def _handler_context(root: Path) -> HandlerContext:
    return HandlerContext(
        root=root,
        sandbox=root,
        dry_run=False,
        confirmed=True,
        skill_dir=DOCUMENTS_SKILL_DIR,
    )


def _execute(
    tool: str, args: dict, sandbox: Path, *, dry_run: bool = True, confirmed: bool = False
):
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=sandbox, root=sandbox)
    return agent.execute_plan(
        {"plan": [{"tool": tool, "args": args}]},
        dry_run=dry_run,
        confirmed=confirmed,
    )[0]["result"]


def _load_fixture_module():
    module_path = DOCUMENTS_SKILL_DIR / "eval" / "fixtures.py"
    spec = importlib.util.spec_from_file_location("_documents_fixture_coverage", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pdf_text_pages(path: Path) -> list[str]:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


def _build_multicolumn_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    width, height = letter
    c = canvas.Canvas(str(path), pagesize=letter)
    left = ["LEFTCOL", "alpha", "bravo", "charlie"]
    right = ["RIGHTCOL", "delta", "echo", "foxtrot"]
    y = height - 72
    for left_word, right_word in zip(left, right, strict=True):
        c.drawString(72, y, left_word)
        c.drawString(width / 2 + 36, y, right_word)
        y -= 24
    c.showPage()
    c.save()


def _build_large_pdf(path: Path, pages: int) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    for index in range(pages):
        c.drawString(72, 720, f"Page {index + 1} token P{index + 1}X")
        c.showPage()
    c.save()


# ── (1) live external-binary paths ──────────────────────────────────────────────


@pytest.mark.parametrize("fixture_key", ["docx", "pptx", "xlsx"])
def test_convert_office_to_pdf_live(tmp_path: Path, fixture_key: str):
    """Office->PDF through the real soffice subprocess. Skips without LibreOffice."""
    _require_documents_extra()
    handlers = _documents_handlers_module()
    if not handlers.detect_external_tools()["soffice"]:
        pytest.skip("LibreOffice (soffice) is not installed")

    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    output = tmp_path / f"{fixture_key}-converted.pdf"

    result = _execute(
        "convert_document",
        {"input": str(manifest[fixture_key]), "to_format": "pdf", "output": str(output)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )

    assert result == {"output": str(output)}
    assert output.is_file() and output.stat().st_size > 0
    assert _execute("inspect_document", {"input": str(output)}, tmp_path)["pages"] >= 1


def test_run_compress_ghostscript_live(tmp_path: Path):
    """Lossy compress through the real gs subprocess. Skips without Ghostscript.

    gs is AGPL and will not ship bundled — this is informational coverage of the
    optional path; the shipped default is the lossless fallback below.
    """
    _require_documents_extra()
    handlers = _documents_handlers_module()
    if not handlers.detect_external_tools()["gs"]:
        pytest.skip("Ghostscript (gs) is not installed")

    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    output = tmp_path / "gs-live-compressed.pdf"

    result = handlers.RunCompressStep().handle(
        {
            "input": str(manifest["pdf"]),
            "compress_quality": "balanced",
            "inspection": {"pages": 3},
            "output": str(output),
        },
        _handler_context(tmp_path),
    )

    assert result["method"] == "ghostscript"
    assert result["text_preserved"] is True
    assert output.is_file() and output.stat().st_size > 0
    # gs /ebook keeps a real text layer (the whole point vs. raster).
    assert "Gamma page three" in "".join(_pdf_text_pages(output))
    assert _execute("inspect_document", {"input": str(output)}, tmp_path)["pages"] == 3


# ── (2) graceful degradation (the no-binary paths that ship) ────────────────────


def test_convert_office_to_pdf_without_libreoffice_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Without soffice, Office->PDF must surface a clear install message, not crash."""
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    handlers = _documents_handlers_module()
    monkeypatch.setattr(
        handlers._deps,
        "detect_external_tools",
        lambda: {"gs": None, "soffice": None, "tesseract": None},
    )

    with pytest.raises(handlers.DocumentsDependencyError, match="LibreOffice"):
        handlers.ConvertDocumentStep().handle(
            {"input": str(manifest["docx"]), "to_format": "pdf", "output": str(tmp_path / "x.pdf")},
            _handler_context(tmp_path),
        )


def test_run_compress_balanced_without_ghostscript_is_lossless(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The shipped default: balanced quality without gs falls back to lossless
    optimize, preserving the text layer (gs is banned from bundling)."""
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    handlers = _documents_handlers_module()
    output = tmp_path / "balanced-nogs.pdf"
    monkeypatch.setattr(
        handlers._deps,
        "detect_external_tools",
        lambda: {"gs": None, "soffice": None, "tesseract": None},
    )

    result = handlers.RunCompressStep().handle(
        {
            "input": str(manifest["pdf"]),
            "compress_quality": "balanced",
            "inspection": {"pages": 3},
            "output": str(output),
        },
        _handler_context(tmp_path),
    )

    assert result["method"] == "lossless-optimize"
    assert result["text_preserved"] is True
    assert output.is_file()
    assert "Gamma page three" in "".join(_pdf_text_pages(output))


# ── (3) messy real-world PDFs ───────────────────────────────────────────────────


def test_extract_text_handles_multicolumn_pdf(tmp_path: Path):
    """A two-column layout must still surface text from both columns."""
    _require_documents_extra()
    pdf = tmp_path / "multicolumn.pdf"
    _build_multicolumn_pdf(pdf)

    result = _execute("extract_text", {"input": str(pdf)}, tmp_path, dry_run=False, confirmed=True)
    text = result["text"]

    assert "LEFTCOL" in text and "RIGHTCOL" in text
    assert "charlie" in text and "foxtrot" in text


def test_inspect_and_split_large_pdf(tmp_path: Path):
    """Large page counts inspect and split correctly."""
    _require_documents_extra()
    pdf = tmp_path / "large.pdf"
    _build_large_pdf(pdf, pages=50)

    assert _execute("inspect_document", {"input": str(pdf)}, tmp_path)["pages"] == 50

    out_dir = tmp_path / "tail"
    result = _execute(
        "split_pdf",
        {"input": str(pdf), "ranges": "45-50", "output_dir": str(out_dir)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )

    assert result["count"] == 1
    tail = Path(result["outputs"][0])
    assert len(_pdf_text_pages(tail)) == 6
    assert "P50X" in "".join(_pdf_text_pages(tail))


def test_inspect_encrypted_pdf_reports_gracefully(tmp_path: Path):
    """An encrypted fixture inspects without crashing: it is flagged ``encrypted``
    and reports ``pages: 0`` rather than attempting decryption (encryption variety)."""
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)

    result = _execute("inspect_document", {"input": str(manifest["protected_pdf"])}, tmp_path)
    assert result["encrypted"] is True
    assert result["pages"] == 0
    assert result["has_text_layer"] is False


def test_find_in_encrypted_pdf_reports_clear_error(tmp_path: Path):
    """Searching a password-protected PDF should fail with a domain message,
    not a raw pypdf decryption traceback."""
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    handlers = _documents_handlers_module()

    with pytest.raises(ValueError, match="encrypted PDF.*password"):
        handlers.FindInDocumentStep().handle(
            {"input": str(manifest["protected_pdf"]), "query": "beta"},
            _handler_context(tmp_path),
        )
