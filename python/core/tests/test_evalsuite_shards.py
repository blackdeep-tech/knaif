"""`evalsuite shards`: pre-drawn `native --only` files that split one L4 cell across CI jobs.

The draw must be deterministic (the shards are written into the run before any result), cover
exactly the rows asked for, and keep every shard a similar mix of the corpus so the jobs finish
at similar times.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from knaif.evalsuite.cli import cmd_shards, load_only
from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.shards import draw_shards


def _corpus(n_rows: int, per_row: int = 1) -> list[CorpusRow]:
    return [
        CorpusRow(
            id=f"r{i:02d}",
            utterances=[f"u{i}-{j}" for j in range(per_row)],
            expected_outcome="plan",
        )
        for i in range(n_rows)
    ]


def _all_pairs(corpus: list[CorpusRow]) -> list[tuple[str, int]]:
    return [(r.id, j) for r in corpus for j in range(len(r.utterances))]


def test_full_corpus_is_split_without_loss_or_overlap() -> None:
    corpus = _corpus(5, per_row=2)  # 10 utterances
    shards = draw_shards(corpus, count=3)
    flat = [p for s in shards for p in s]
    assert sorted(flat) == sorted(_all_pairs(corpus))
    assert len(flat) == len(set(flat))
    assert sorted(len(s) for s in shards) == [3, 3, 4]


def test_shards_are_round_robin_so_each_spans_the_corpus() -> None:
    corpus = _corpus(6)
    shards = draw_shards(corpus, count=2)
    assert shards[0] == [("r00", 0), ("r02", 0), ("r04", 0)]
    assert shards[1] == [("r01", 0), ("r03", 0), ("r05", 0)]


def test_rows_picks_evenly_spaced_utterances_in_corpus_order() -> None:
    corpus = _corpus(10)
    (only,) = draw_shards(corpus, count=1, rows=4)
    assert only == [("r00", 0), ("r02", 0), ("r05", 0), ("r07", 0)]


def test_the_draw_is_deterministic() -> None:
    corpus = _corpus(37, per_row=3)
    assert draw_shards(corpus, count=4, rows=50) == draw_shards(corpus, count=4, rows=50)


def test_rows_larger_than_the_corpus_takes_the_whole_corpus() -> None:
    corpus = _corpus(3)
    (only,) = draw_shards(corpus, count=1, rows=99)
    assert only == _all_pairs(corpus)


@pytest.mark.parametrize("count, rows", [(0, None), (4, 3), (1, 0)])
def test_impossible_requests_are_refused(count: int, rows: int | None) -> None:
    with pytest.raises(ValueError):
        draw_shards(_corpus(10), count=count, rows=rows)


def test_cli_writes_only_files_and_a_manifest(tmp_path: Path) -> None:
    corpus_path = tmp_path / "eval.jsonl"
    corpus_path.write_text(
        "".join(
            json.dumps({"id": r.id, "utterances": r.utterances, "expected_outcome": "plan"}) + "\n"
            for r in _corpus(7)
        ),
        encoding="utf-8",
    )
    out = tmp_path / "shards"
    cmd_shards(
        argparse.Namespace(skill="demo", corpus=str(corpus_path), count=3, rows=None, out=str(out))
    )

    manifest = json.loads((out / "shards.json").read_text(encoding="utf-8"))
    assert manifest["skill"] == "demo"
    assert manifest["corpus_sha256"] == hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    assert manifest["count"] == 3 and manifest["total"] == 7
    assert [s["file"] for s in manifest["shards"]] == [
        "shard-00.json",
        "shard-01.json",
        "shard-02.json",
    ]
    # No absolute or machine path in what gets uploaded and compared across machines.
    assert str(tmp_path) not in json.dumps(manifest)

    loaded = [load_only(str(out / s["file"])) for s in manifest["shards"]]
    assert set().union(*loaded) == {(f"r{i:02d}", 0) for i in range(7)}
    assert [s["rows"] for s in manifest["shards"]] == [len(x) for x in loaded]
