"""What a compress did, in one line, as ffmpeg's compress says it (owner, 2026-10-28).

`compress_pdf` already kept the input's bytes when compressing did not make a PDF smaller
(2026-09-26), but never said so: the Python view showed the expansion's three steps as two
"wrote" lines ("run_compress", "verify_output") and the inspect read, where native shows one
`compress (<method>)`. Now both show that one line, then `30% smaller (…)` or, when the
original was kept, `⚠ <file>: couldn't make it smaller — kept it as it was`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from knaif.agent import CommandAgent

DOCUMENTS = Path(__file__).parents[2]
FIXTURE = DOCUMENTS.parents[1] / "contracts" / "parity" / "fixtures" / "documents" / "sample.pdf"


def test_a_compress_is_one_write_with_its_sizes(tmp_path: Path) -> None:
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
    # Every method grows this small text PDF (2026-09-26): the original is kept, and the size
    # the attempt came to is what the line reports.
    assert item["kept"] is True
    assert item["after"] >= item["before"]
    assert (tmp_path / "sample-compressed.pdf").read_bytes() == FIXTURE.read_bytes()
