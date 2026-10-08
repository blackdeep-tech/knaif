"""Where the Python runtime looks for the documents skill's external tools.

The native runtime resolves a tool through `$KNAIF_<CMD>_BIN`, then `PATH`, then the install folders
`skill.yaml` declares. The Python runtime used to read `PATH` only, so a LibreOffice or Ghostscript
that was installed (and found by `knaif skills deps`) but not on `PATH` was missing here.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from .. import _deps


def _fake_exe(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    path = directory / f"{name}{suffix}"
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture(autouse=True)
def _empty_path(monkeypatch, tmp_path):
    """No real tool on PATH and no override, so each test controls what is findable."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    for cmd in ("gs", "gswin64c", "gswin32c", "soffice", "libreoffice", "tesseract"):
        monkeypatch.delenv(f"KNAIF_{cmd.upper()}_BIN", raising=False)


def test_a_tool_in_a_declared_folder_is_found_when_not_on_path(tmp_path):
    exe = _fake_exe(tmp_path / "LibreOffice" / "program", "soffice")
    found = _deps.find_command(["soffice", "libreoffice"], [tmp_path / "LibreOffice" / "program"])
    assert Path(found) == exe


def test_path_wins_over_a_declared_folder(tmp_path, monkeypatch):
    on_path = _fake_exe(tmp_path / "bin", "tesseract")
    _fake_exe(tmp_path / "declared", "tesseract")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert Path(_deps.find_command(["tesseract"], [tmp_path / "declared"])) == on_path


def test_the_override_variable_wins_over_everything(tmp_path, monkeypatch):
    on_path = _fake_exe(tmp_path / "bin", "tesseract")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("KNAIF_TESSERACT_BIN", str(tmp_path / "mine" / "tesseract"))
    found = _deps.find_command(["tesseract"], [])
    assert found == str(tmp_path / "mine" / "tesseract")
    assert Path(found) != on_path


def test_nothing_found_is_none(tmp_path):
    assert _deps.find_command(["soffice"], [tmp_path]) is None


def test_wildcard_folders_expand_newest_version_first(tmp_path, monkeypatch):
    for version in ("gs9.9", "gs10.07.1", "gs10.2"):
        (tmp_path / "gs" / version / "bin").mkdir(parents=True)
    monkeypatch.setenv("KNAIF_TEST_ROOT", str(tmp_path))
    dirs = _deps.expand_dirs(["%KNAIF_TEST_ROOT%/gs/gs*/bin"])
    assert [d.parent.name for d in dirs] == ["gs10.07.1", "gs10.2", "gs9.9"]


def test_a_pattern_naming_an_unset_variable_is_skipped():
    assert _deps.expand_dirs(["%KNAIF_SURELY_UNSET_VARIABLE%/tool/bin"]) == []


def test_the_declared_folders_come_from_skill_yaml():
    dirs = _deps.declared_patterns("ghostscript", platform="windows")
    assert any("gs" in d for d in dirs)
    assert _deps.declared_patterns("ghostscript", platform="plan9") == []
    assert _deps.declared_patterns("no-such-tool", platform="windows") == []


def test_detect_external_tools_uses_the_folders(tmp_path, monkeypatch):
    exe = _fake_exe(tmp_path / "Tesseract-OCR", "tesseract")
    monkeypatch.setattr(
        _deps, "declared_patterns", lambda tool, platform=None: [str(tmp_path / "Tesseract-OCR")]
    )
    tools = _deps.detect_external_tools()
    assert Path(tools["tesseract"]) == exe
    assert tools["soffice"] is None or isinstance(tools["soffice"], str)


def test_ocr_runs_the_binary_that_was_found(tmp_path, monkeypatch):
    pytesseract = pytest.importorskip("pytesseract")
    exe = _fake_exe(tmp_path / "Tesseract-OCR", "tesseract")
    monkeypatch.setattr(
        _deps, "declared_patterns", lambda tool, platform=None: [str(tmp_path / "Tesseract-OCR")]
    )
    previous = pytesseract.pytesseract.tesseract_cmd
    try:
        assert _deps._require_pytesseract() is pytesseract
        assert Path(pytesseract.pytesseract.tesseract_cmd) == exe
    finally:
        pytesseract.pytesseract.tesseract_cmd = previous
