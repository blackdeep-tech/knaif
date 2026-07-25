"""documents skill — dependency shims + external-tool detection (see handlers.py)."""

from __future__ import annotations

import shutil


class DocumentsDependencyError(RuntimeError):
    """Raised when an optional document backend is not installed."""


def detect_external_tools() -> dict[str, str | None]:
    """Return detected optional document subprocess tools."""

    return {
        "gs": shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c"),
        "soffice": shutil.which("soffice") or shutil.which("libreoffice"),
        "tesseract": shutil.which("tesseract"),
    }


def _require_pikepdf():
    try:
        import pikepdf
    except ImportError as exc:  # pragma: no cover - covered when extra absent
        raise DocumentsDependencyError(
            "Install the documents dep group (uv pip install --group documents) for PDF page operations."
        ) from exc
    return pikepdf


def _require_pypdf():
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover - covered when extra absent
        raise DocumentsDependencyError(
            "Install the documents dep group (uv pip install --group documents) for PDF overlay operations."
        ) from exc
    return pypdf


def _require_pillow_image():
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - covered when extra absent
        raise DocumentsDependencyError(
            "Install the documents dep group (uv pip install --group documents) for image conversion."
        ) from exc
    return Image


def _require_pytesseract():
    try:
        import pytesseract
    except ImportError as exc:  # pragma: no cover - covered when OCR extra absent
        raise DocumentsDependencyError(
            "Install the documents-ocr dep group (uv pip install --group documents-ocr) for OCR support."
        ) from exc
    return pytesseract


def _require_pypdfium2():
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - covered when extra absent
        raise DocumentsDependencyError(
            "Install the documents dep group (uv pip install --group documents) for PDF rasterization."
        ) from exc
    return pdfium


def _require_tesseract() -> str:
    tesseract = detect_external_tools()["tesseract"]
    if not tesseract:
        raise DocumentsDependencyError("Install Tesseract and put it on PATH for OCR support.")
    return tesseract
