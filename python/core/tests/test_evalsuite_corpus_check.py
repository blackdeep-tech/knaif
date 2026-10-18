"""`evalsuite corpus-check`: are proposed new rows fit to append? (plan 2026-10-14, G2.2/G2.4, T7)

New rows carry their own tag, parse under the corpus schema, use ids and utterances the corpus
does not have, and copy no training utterance (the train/eval separation
`test_train_data_integrity` enforces, applied before the rows land rather than after).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.corpus_check import check_new_rows, command_problems

EXISTING = [{"id": "x_001", "utterances": ["trim clip.mp4"], "expected_outcome": "plan"}]
TRAIN = [{"utterance": "Reverse clip.mp4!", "plan": {"plan": []}}]


def _row(**kw):
    base = {
        "id": "x_hard2_001",
        "utterances": ["cut clip.mp4 to 3 seconds and reverse it"],
        "expected_outcome": "plan",
        "fixture": "clip.mp4",
        "expected_tool": "trim_video",
        "expected_tools": ["trim_video", "reverse_video"],
        "tags": ["hard2", "chain2"],
        "success_criteria": {"container": "mp4"},
    }
    base.update(kw)
    return base


def test_a_clean_row_passes() -> None:
    assert check_new_rows([_row()], EXISTING, TRAIN, tag="hard2") == []


def test_the_tag_is_required() -> None:
    (p,) = check_new_rows([_row(tags=["chain2"])], EXISTING, TRAIN, tag="hard2")
    assert "hard2" in p


def test_an_id_the_corpus_has_is_refused() -> None:
    assert any("id" in p for p in check_new_rows([_row(id="x_001")], EXISTING, TRAIN, tag="hard2"))


def test_an_utterance_the_corpus_has_is_refused() -> None:
    problems = check_new_rows([_row(utterances=["Trim clip.mp4"])], EXISTING, TRAIN, tag="hard2")
    assert any("already in the corpus" in p for p in problems)


def test_an_utterance_copied_from_training_is_refused() -> None:
    problems = check_new_rows([_row(utterances=["reverse clip.mp4"])], EXISTING, TRAIN, tag="hard2")
    assert any("train" in p for p in problems)


def test_a_chain_names_its_first_tool_consistently() -> None:
    problems = check_new_rows([_row(expected_tool="reverse_video")], EXISTING, TRAIN, tag="hard2")
    assert any("expected_tool" in p for p in problems)


def test_a_schema_error_is_reported_not_raised() -> None:
    problems = check_new_rows([_row(expected_outcome="maybe")], EXISTING, TRAIN, tag="hard2")
    assert any("schema" in p for p in problems)


def test_two_new_rows_may_not_share_an_utterance() -> None:
    rows = [_row(), _row(id="x_hard2_002")]
    assert any("also in proposed" in p for p in check_new_rows(rows, EXISTING, TRAIN, tag="hard2"))


def test_a_shell_quoted_baseline_is_refused() -> None:
    cmd = 'ffmpeg -y -i clip.mp4 -filter_complex "[0:v]reverse[v]" -map [v] out.mp4'
    problems = check_new_rows([_row(baseline={"command": cmd})], EXISTING, TRAIN, tag="hard2")
    assert any("quote" in p for p in problems)


def test_a_shell_quoted_output_command_is_refused() -> None:
    outputs = [{"command": "ffmpeg -y -i clip.mp4 -vf 'scale=-2:480' out.mp4", "criteria": {}}]
    problems = check_new_rows([_row(outputs=outputs)], EXISTING, TRAIN, tag="hard2")
    assert any("quote" in p for p in problems)


@pytest.mark.parametrize(
    "corpus", sorted(Path("skills").glob("*/data/eval.jsonl")), ids=lambda p: p.parts[1]
)
def test_no_committed_reference_command_is_shell_quoted(corpus: Path) -> None:
    """The harness splits a reference command on whitespace (`evalsuite.chain`), as the skills
    render them; a shell quote reaches ffmpeg as a literal character and the baseline fails."""
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines() if line]
    bad = [p for row in rows for p in command_problems(row)]
    assert not bad, "\n".join(bad)


def test_the_cli_checks_a_proposal_file_against_the_real_corpus(tmp_path: Path, capsys) -> None:
    import argparse

    from knaif.evalsuite import cli

    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps(_row(id="ffmpeg_hard2_999")) + "\n", encoding="utf-8")
    cli.cmd_corpus_check(argparse.Namespace(skill="ffmpeg", rows=str(rows), tag="hard2"))
    assert "1 row(s) ok" in capsys.readouterr().out
