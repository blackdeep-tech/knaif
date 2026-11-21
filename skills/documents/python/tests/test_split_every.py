"""split_pdf_every (a new file every N pages), and its pieces as a set later steps run over.

A triggered sibling of split_pdf, so split_pdf's prompts stay as they were; the names are known
before running (owner, 2026-10-20). `every: 1` is one PDF per page, `every: 10` chunks of ten. The skill hook
(`planned_outputs`) names the pieces before anything runs, so "split report.pdf into one file per
page and stamp DRAFT on each" runs the watermark once per page file.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf

from knaif.agent import CommandAgent

SKILL_DIR = Path(__file__).parents[2]


def _pdf(path: Path, pages: int) -> None:
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page()
    pdf.save(path)


def _agent(tmp_path: Path, pages: int = 5) -> CommandAgent:
    _pdf(tmp_path / "report.pdf", pages)
    return CommandAgent.from_skill(SKILL_DIR, sandbox=tmp_path, root=tmp_path)


def _run(agent: CommandAgent, steps: list[dict], utterance: str) -> None:
    agent.execute_plan({"plan": steps}, utterance=utterance, dry_run=False, confirmed=True)


def _pages(path: Path) -> int:
    with pikepdf.open(path) as pdf:
        return len(pdf.pages)


def test_every_n_pages(tmp_path: Path) -> None:
    agent = _agent(tmp_path, pages=5)
    _run(
        agent,
        [{"tool": "split_pdf_every", "args": {"input": "report.pdf", "every": 2}}],
        "split report.pdf every 2 pages",
    )
    parts = {p.name: _pages(p) for p in tmp_path.glob("report-pages-*.pdf")}
    assert parts == {"report-pages-1_2.pdf": 2, "report-pages-3_4.pdf": 2, "report-pages-5.pdf": 1}


def test_the_hook_names_the_pieces_before_running(tmp_path: Path) -> None:
    agent = _agent(tmp_path, pages=3)
    ctx = agent._handler_context(True, True, False, tmp_path, tool="split_pdf")
    planned = agent.skill_instance.planned_outputs(
        "split_pdf_every", {"input": "report.pdf", "every": 1}, ctx
    )
    assert planned.name == "report-pages-%s.pdf"
    assert list(planned.files) == ["report-pages-1.pdf", "report-pages-2.pdf", "report-pages-3.pdf"]
    one = {"input": "report.pdf", "ranges": "1-3"}
    assert agent.skill_instance.planned_outputs("split_pdf", one, ctx) is None


def test_one_file_per_page_then_stamp_each(tmp_path: Path) -> None:
    agent = _agent(tmp_path, pages=3)
    _run(
        agent,
        [
            {"tool": "split_pdf_every", "args": {"input": "report.pdf", "every": 1}},
            {"tool": "watermark", "args": {"input": "report-pages-*.pdf", "text": "DRAFT"}},
        ],
        "split report.pdf into one file per page and stamp DRAFT on each",
    )
    left = sorted(p.name for p in tmp_path.iterdir())
    # The page files are only refined by the stamp, so they went with the scratch.
    assert left == [
        "report-pages-1-watermarked.pdf",
        "report-pages-2-watermarked.pdf",
        "report-pages-3-watermarked.pdf",
        "report.pdf",
    ]
    assert (agent.last_batch_report.done, agent.last_batch_report.failed) == (3, 0)
