"""The release subset (plan 2026-10-14-faster-release-evidence, D1, T2b).

The subset is derived from the corpus, deterministic, and committed with the sha256 of the corpus
it came from; a corpus edit that is not followed by regenerating it fails here, not at release.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from knaif.evalsuite.release_subset import (
    build_subset,
    check_subset,
    corpus_sha256,
    select_rows,
    subset_path,
)


def _row(rid: str, tool: str = "trim_video", outcome: str = "plan", tags=("trim",), n=2) -> dict:
    return {
        "id": rid,
        "utterances": [f"{rid} utterance {i}" for i in range(n)],
        "expected_outcome": outcome,
        "expected_tool": tool,
        "tags": list(tags),
    }


def _write(path: Path, rows: list[dict], eol: str = "\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(eol.join(json.dumps(r) for r in rows).encode("utf-8") + eol.encode())
    return path


def test_the_first_row_of_each_stratum_is_picked_in_corpus_order() -> None:
    rows = [_row("a"), _row("b"), _row("c", tool="rotate_video"), _row("d", tags=("trim", "x"))]
    assert select_rows(rows) == [("a", 0), ("c", 0), ("d", 0)]


def test_tag_order_does_not_make_a_new_stratum() -> None:
    rows = [_row("a", tags=("x", "y")), _row("b", tags=("y", "x"))]
    assert select_rows(rows) == [("a", 0)]


def test_outcome_is_part_of_the_stratum() -> None:
    rows = [_row("a"), _row("b", outcome="clarify")]
    assert select_rows(rows) == [("a", 0), ("b", 0)]


@pytest.mark.parametrize("tag", ["chain2", "chain3", "edge", "safety", "hard2"])
def test_every_row_with_an_always_tag_is_picked(tag: str) -> None:
    rows = [_row("a", tags=(tag,)), _row("b", tags=(tag,))]
    assert select_rows(rows) == [("a", 0), ("b", 0)]


def test_the_corpus_hash_folds_crlf(tmp_path: Path) -> None:
    lf = _write(tmp_path / "lf.jsonl", [_row("a")])
    crlf = _write(tmp_path / "crlf.jsonl", [_row("a")], eol="\r\n")
    assert corpus_sha256(lf) == corpus_sha256(crlf)


def test_a_built_subset_names_its_corpus(tmp_path: Path) -> None:
    corpus = _write(tmp_path / "skills" / "x" / "data" / "eval.jsonl", [_row("a"), _row("b")])
    doc = build_subset("x", tmp_path)
    assert doc["skill"] == "x"
    assert doc["corpus"] == "eval.jsonl"
    assert doc["corpus_sha256"] == corpus_sha256(corpus)
    assert doc["rows"] == [["a", 0]]
    assert doc["count"] == 1


def test_an_edited_corpus_makes_the_subset_stale(tmp_path: Path) -> None:
    corpus = _write(tmp_path / "skills" / "x" / "data" / "eval.jsonl", [_row("a")])
    subset_path("x", tmp_path).write_text(json.dumps(build_subset("x", tmp_path)), "utf-8")
    assert check_subset("x", tmp_path) == []
    _write(corpus, [_row("a"), _row("b", tool="rotate_video")])
    (problem,) = check_subset("x", tmp_path)
    assert "release-subset" in problem


def test_a_missing_subset_is_reported(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "x" / "data" / "eval.jsonl", [_row("a")])
    (problem,) = check_subset("x", tmp_path)
    assert "release_subset.json" in problem


def test_the_cli_writes_the_subset(tmp_path: Path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    _write(tmp_path / "skills" / "x" / "data" / "eval.jsonl", [_row("a")])
    monkeypatch.chdir(tmp_path)
    cli.cmd_release_subset(argparse.Namespace(skill="x", check=False))
    assert json.loads(subset_path("x", tmp_path).read_text("utf-8"))["rows"] == [["a", 0]]
    cli.cmd_release_subset(argparse.Namespace(skill="x", check=True))


@pytest.mark.parametrize(
    "skill", sorted(p.parts[1] for p in Path("skills").glob("*/data/release_subset.json"))
)
def test_every_committed_subset_matches_its_corpus(skill: str) -> None:
    assert check_subset(skill, Path(".")) == []


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_each_shipped_skill_commits_a_subset(skill: str) -> None:
    assert subset_path(skill, Path(".")).exists()
