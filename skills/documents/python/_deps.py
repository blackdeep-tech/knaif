"""documents skill — dependency shims + external-tool detection (see handlers.py)."""

from __future__ import annotations

import glob
import os
import re
import shutil
import sys
from pathlib import Path

import yaml


class DocumentsDependencyError(RuntimeError):
    """Raised when an optional document backend is not installed."""


_SKILL_YAML = Path(__file__).resolve().parents[1] / "skill.yaml"

# Each external tool: its declared `skill.yaml` name and the commands that can satisfy it, in
# preference order. Mirrors the `commands:` lists the native runtime reads from the same file.
_TOOLS = {
    "gs": ("ghostscript", ["gs", "gswin64c", "gswin32c"]),
    "soffice": ("libreoffice", ["soffice", "libreoffice"]),
    "tesseract": ("tesseract", ["tesseract"]),
}


def _platform_key(platform: str | None = None) -> str:
    platform = platform or sys.platform
    if platform.startswith("win"):
        return "windows"
    if platform == "darwin":
        return "macos"
    return "linux"


def declared_patterns(tool: str, platform: str | None = None) -> list[str]:
    """The install-folder patterns `skill.yaml` declares for ``tool`` on this platform."""

    try:
        spec = yaml.safe_load(_SKILL_YAML.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    for entry in (spec.get("dependencies") or {}).get("external_tools") or []:
        if entry.get("name") == tool:
            return list((entry.get(_platform_key(platform)) or {}).get("dirs") or [])
    return []


_VAR = re.compile(r"%([^%]+)%")


def _version_key(name: str) -> tuple:
    """Natural order, so ``gs10`` sorts after ``gs9``; case-insensitive."""

    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", name.lower())
        if part
    )


def expand_dirs(patterns: list[str]) -> list[Path]:
    """Existing folders matching ``patterns``: ``%VAR%`` expanded, wildcards globbed, and the
    matches of one wildcard pattern newest version first. A pattern naming an unset variable is
    skipped. The same rules as the native runtime's ``expand_dirs``."""

    found: list[Path] = []
    for pattern in patterns:
        missing = False

        def replace(match: re.Match[str]) -> str:
            nonlocal missing
            value = os.environ.get(match.group(1))
            if not value:
                missing = True
                return ""
            return value

        expanded = _VAR.sub(replace, pattern)
        if missing:
            continue
        matches = [Path(m) for m in glob.glob(expanded)]
        matches.sort(
            key=lambda path: _version_key(path.parent.name + "/" + path.name), reverse=True
        )
        found.extend(m for m in matches if m.is_dir())
    return found


def find_command(commands: list[str], dirs: list[Path]) -> str | None:
    """Resolve the first of ``commands`` that exists: its ``$KNAIF_<CMD>_BIN`` override, then
    ``PATH``, then ``dirs`` (the folders ``skill.yaml`` declares). The native runtime's order."""

    for cmd in commands:
        override = os.environ.get(f"KNAIF_{cmd.upper()}_BIN")
        if override:
            return override
    for cmd in commands:
        hit = shutil.which(cmd)
        if hit:
            return hit
    for cmd in commands:
        for directory in dirs:
            hit = shutil.which(cmd, path=str(directory))
            if hit:
                return hit
    return None


def detect_external_tools() -> dict[str, str | None]:
    """Return detected optional document subprocess tools.

    Looks where the native runtime does (override variable, ``PATH``, declared install folders), so
    a LibreOffice or Ghostscript that `knaif skills deps` reports is found here too."""

    return {
        key: find_command(commands, expand_dirs(declared_patterns(name)))
        for key, (name, commands) in _TOOLS.items()
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
    # pytesseract launches a bare `tesseract` unless told where it is, so a binary found through the
    # override variable or an install folder (not PATH) would pass detection and still fail to run.
    found = detect_external_tools()["tesseract"]
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
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
