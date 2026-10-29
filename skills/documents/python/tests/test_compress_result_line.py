"""What a compress did, in one line, as ffmpeg's compress says it (owner, 2026-10-28).

`compress_pdf` already kept the input's bytes when compressing did not make a PDF smaller
(2026-09-26), but never said so: the Python view showed the expansion's three steps as two
"wrote" lines ("run_compress", "verify_output") and the inspect read, where native shows one
`compress (<method>)`. Now both show that one line, then `30% smaller (…)` or, when the
original was kept, `⚠ <file>: couldn't make it smaller — kept it as it was`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.skill import Skill

DOCUMENTS = Path(__file__).parents[2]
FIXTURE = DOCUMENTS.parents[1] / "contracts" / "parity" / "fixtures" / "documents" / "sample.pdf"


def _steps():
    Skill.load(DOCUMENTS)
    handlers = sys.modules["_skill_oop_documents_handlers"]
    return sys.modules[handlers.__package__ + ".steps"]


def _compress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attempt) -> dict:
    """Compress the sample with the attempt's bytes decided by `attempt(input, output)`.

    What the optimizer achieves on this 2 KB PDF differs by platform (pikepdf re-deflates: bigger
    on Windows, smaller on the Linux CI runner, 2026-10-29), and the line is what is tested here,
    so the compressor is stubbed and Ghostscript pinned off.
    """
    steps = _steps()
    monkeypatch.setattr(
        steps._deps,
        "detect_external_tools",
        lambda: {"gs": None, "soffice": None, "tesseract": None},
    )
    monkeypatch.setattr(steps, "_lossless_compress", attempt)
    shutil.copy(FIXTURE, tmp_path / "sample.pdf")
    agent = CommandAgent.from_skill(DOCUMENTS, sandbox=tmp_path, root=tmp_path)
    results = agent.execute_plan(
        {"plan": [{"tool": "compress_pdf", "args": {"input": "sample.pdf"}}]},
        utterance="compress sample.pdf",
        dry_run=False,
        confirmed=True,
    )
    items = agent.skill_instance.format_results(results, dry_run=False)
    (item,) = items
    assert item["kind"] == "wrote"
    assert item["message"].startswith("compress ("), item
    assert [Path(o).name for o in item["outputs"]] == ["sample-compressed.pdf"]
    assert item["before"] == FIXTURE.stat().st_size
    return item


def _bigger(source: Path, target: Path) -> None:
    target.write_bytes(source.read_bytes() + b" " * 1000)


def _smaller(source: Path, target: Path) -> None:
    """Three blank pages, as many as the sample has: 981 bytes against 2227."""
    import pikepdf

    with pikepdf.open(source) as pdf, pikepdf.new() as out:
        for _ in pdf.pages:
            out.add_blank_page()
        out.save(target)


def test_a_compress_that_cannot_shrink_keeps_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The original is kept, and the size the attempt came to is what the line reports."""
    item = _compress(tmp_path, monkeypatch, _bigger)
    assert item["kept"] is True
    assert item["after"] == item["before"] + 1000
    assert (tmp_path / "sample-compressed.pdf").read_bytes() == FIXTURE.read_bytes()


def test_a_compress_that_shrinks_reports_both_sizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _compress(tmp_path, monkeypatch, _smaller)
    assert item["kept"] is False
    assert item["after"] == (tmp_path / "sample-compressed.pdf").stat().st_size < item["before"]
