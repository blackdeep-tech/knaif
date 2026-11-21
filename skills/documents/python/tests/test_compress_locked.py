"""Compressing a password-protected PDF fails instead of writing a blank page.

Found 2026-10-20 by a run-order A/B: `compress every pdf here and add page numbers`
over the batch folder "compressed" `locked.pdf` through Ghostscript, which cannot read it and
wrote a one-page blank PDF; the step reported success and the batch counted the file as done.
pikepdf and pypdf already refuse an encrypted file; only the Ghostscript path did not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
import pytest

from knaif.agent import CommandAgent

DOCUMENTS_SKILL_DIR = Path(__file__).parents[2]


def _locked(path: Path) -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(path, encryption=pikepdf.Encryption(user="secret", owner="secret"))


def test_a_locked_pdf_is_not_compressed_into_a_blank_page(tmp_path: Path, monkeypatch) -> None:
    _locked(tmp_path / "locked.pdf")
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    steps = sys.modules[type(agent.tool_map["run_compress"]).__module__]
    calls: list[Path] = []
    monkeypatch.setattr(steps._deps, "detect_external_tools", lambda: {"gs": "gs"})
    monkeypatch.setattr(
        steps,
        "_ghostscript_compress",
        lambda src, dst, *a, **k: (calls.append(src), Path(dst).write_bytes(b"%PDF-1.4 blank")),
    )

    with pytest.raises(ValueError, match="password"):
        agent.execute_plan(
            {"plan": [{"tool": "compress_pdf", "args": {"input": "locked.pdf"}}]},
            dry_run=False,
            confirmed=True,
        )
    assert calls == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["locked.pdf"]
