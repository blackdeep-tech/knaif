"""`save_row`: write one row back without touching any other line (G2.1, kept rows immutable).

The authoring notebook used to rewrite the whole corpus from its parsed rows on every
approval, which re-serialises every line; once `eval_kept.sha256` pins the kept lines, that
would fail `test_corpus_integrity` on the first approval. Approving a baseline changes one
row, so one line is what gets written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.corpus import CorpusRow, load_corpus, save_row

LINES = [
    '{"id": "a", "utterances": ["x"], "expected_outcome": "plan", "baseline": {"command": "c1", "validated_by": null}}',
    '{"id": "b",  "utterances": ["y"],   "expected_outcome": "plan"}',  # odd spacing, kept as is
    '{"id": "c", "utterances": ["z"], "expected_outcome": "clarify"}',
]


def _corpus(tmp_path: Path, crlf: bool = False) -> Path:
    p = tmp_path / "eval.jsonl"
    end = "\r\n" if crlf else "\n"
    p.write_bytes("".join(line + end for line in LINES).encode("utf-8"))
    return p


def test_only_the_named_row_s_line_changes(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    rows = load_corpus(corpus)
    rows[0].baseline = {"command": "c1", "validated_by": "human"}
    save_row(corpus, rows[0])
    out = corpus.read_text(encoding="utf-8").splitlines()
    assert out[1] == LINES[1] and out[2] == LINES[2], "other lines are byte-identical"
    assert json.loads(out[0])["baseline"]["validated_by"] == "human"
    assert load_corpus(corpus)[0].baseline == {"command": "c1", "validated_by": "human"}


def test_the_file_s_line_endings_are_kept(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, crlf=True)
    rows = load_corpus(corpus)
    rows[2].expected_outcome = "reject"
    save_row(corpus, rows[2])
    raw = corpus.read_bytes()
    assert raw.count(b"\r\n") == 3 and raw.endswith(b"\r\n")
    assert raw.decode("utf-8").splitlines()[1] == LINES[1]


def test_an_unknown_id_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    before = corpus.read_bytes()
    with pytest.raises(ValueError, match="zz"):
        save_row(corpus, CorpusRow(id="zz", utterances=["q"], expected_outcome="plan"))
    assert corpus.read_bytes() == before
