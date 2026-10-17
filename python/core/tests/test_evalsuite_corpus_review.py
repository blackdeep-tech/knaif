"""Applying an approved corpus review (plan 2026-10-14-faster-release-evidence, G2, T7).

Rule G2.1: kept rows are immutable — never renumbered, never reworded. The review only removes
rows and appends rows. `apply_removals` removes the approved rows and leaves every other line
byte-identical; `eval_removed.jsonl` says what went and what stands in its place;
`eval_kept.sha256` pins every kept line so a later edit to one is caught by `check_kept_rows`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from knaif.evalsuite.corpus_review import apply_removals, check_kept_rows

LINES = [
    '{"id": "a", "utterances": ["mute clip.mp4", "silence it"], "expected_outcome": "plan"}',
    '{"id": "b", "utterances": ["Mute clip.mp4."], "expected_outcome": "plan"}',
    '{"id": "c",  "utterances": ["silence it"],   "expected_outcome": "plan"}',  # odd spacing
]


def _corpus(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    corpus = data / "eval.jsonl"
    corpus.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    return corpus


def _removal(rid="b", kept="a"):
    return {"id": rid, "kept": kept, "reason": f"every utterance is in {kept}"}


def test_removed_rows_go_and_every_other_line_is_byte_identical(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    apply_removals(corpus, [_removal()], date="2026-10-17")
    assert corpus.read_text(encoding="utf-8") == LINES[0] + "\n" + LINES[2] + "\n"


def test_the_removed_row_is_kept_on_record_with_what_replaces_it(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    apply_removals(corpus, [_removal()], date="2026-10-17")
    (entry,) = [
        json.loads(line)
        for line in (corpus.parent / "eval_removed.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert entry["id"] == "b" and entry["kept"] == "a" and entry["removed"] == "2026-10-17"
    assert entry["reason"] == "every utterance is in a"
    assert entry["row"] == json.loads(LINES[1]), "the row itself, so a past run's row can be read"
    assert len(entry["corpus_sha256_before"]) == 64


def test_every_kept_line_is_pinned_by_its_hash(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    apply_removals(corpus, [_removal()], date="2026-10-17")
    pins = dict(
        line.split("  ", 1)[::-1]
        for line in (corpus.parent / "eval_kept.sha256").read_text(encoding="utf-8").splitlines()
    )
    assert set(pins) == {"a", "c"}
    assert pins["c"] == hashlib.sha256(LINES[2].encode("utf-8")).hexdigest()


def test_a_second_review_appends_to_the_record(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    apply_removals(corpus, [_removal()], date="2026-10-17")
    apply_removals(corpus, [_removal("c", "a")], date="2026-11-01")
    record = (corpus.parent / "eval_removed.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(r)["id"] for r in record] == ["b", "c"]
    assert corpus.read_text(encoding="utf-8") == LINES[0] + "\n"


@pytest.mark.parametrize(
    "bad, message",
    [
        (_removal("zz", "a"), "zz is not in the corpus"),
        (_removal("b", "zz"), "zz is not in the corpus"),
        (_removal("b", "b"), "its own replacement"),
        (_removal("a", "b"), "not in b"),  # a has an utterance b lacks: not a duplicate
    ],
)
def test_a_removal_that_would_lose_a_test_is_refused(tmp_path: Path, bad, message) -> None:
    corpus = _corpus(tmp_path)
    before = corpus.read_bytes()
    with pytest.raises(ValueError, match=message):
        apply_removals(corpus, [bad], date="2026-10-17")
    assert corpus.read_bytes() == before, "nothing is written when any removal is refused"
    assert not (corpus.parent / "eval_removed.jsonl").exists()


def test_a_removal_cannot_remove_a_row_another_removal_keeps(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    with pytest.raises(ValueError, match="kept in place of"):
        apply_removals(corpus, [_removal("b", "a"), _removal("a", "c")], date="2026-10-17")


# ── the standing check (runs over the real corpora in test_corpus_integrity below) ───────────


def test_check_passes_after_a_clean_removal(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    apply_removals(corpus, [_removal()], date="2026-10-17")
    assert check_kept_rows(corpus) == []


def test_check_passes_when_no_review_happened_yet(tmp_path: Path) -> None:
    assert check_kept_rows(_corpus(tmp_path)) == []


def test_check_catches_a_reworded_kept_row(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    apply_removals(corpus, [_removal()], date="2026-10-17")
    corpus.write_text(corpus.read_text(encoding="utf-8").replace("silence it", "silence"), "utf-8")
    assert any("a" in p and "changed" in p for p in check_kept_rows(corpus))


def test_check_catches_a_removed_row_that_came_back_and_a_lost_kept_row(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    apply_removals(corpus, [_removal()], date="2026-10-17")
    corpus.write_text(LINES[1] + "\n" + LINES[2] + "\n", encoding="utf-8")  # b back, a gone
    problems = check_kept_rows(corpus)
    assert any("b" in p and "removed" in p for p in problems)
    assert any("a" in p and "missing" in p for p in problems)


def test_check_allows_appended_rows(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    apply_removals(corpus, [_removal()], date="2026-10-17")
    with corpus.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "d", "utterances": ["new"], "expected_outcome": "plan"}\n')
    assert check_kept_rows(corpus) == []


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_corpus_integrity(skill: str) -> None:
    """G2.1 over the real corpora: once a review has removed rows, every kept row is the line it
    was, no removed row is back, and nothing but appended rows is new."""
    assert check_kept_rows(Path("skills") / skill / "data" / "eval.jsonl") == []


def test_the_cli_applies_only_removals_the_audit_proposed(tmp_path: Path) -> None:
    import argparse

    from knaif.evalsuite import cli

    corpus = _corpus(tmp_path)
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"proposed_removals": [_removal()]}), encoding="utf-8")
    args = argparse.Namespace(
        skill="x", corpus=str(corpus), audit=str(audit), ids="b", date="2026-10-17"
    )
    cli.cmd_corpus_remove(args)
    assert (corpus.parent / "eval_removed.jsonl").exists()
    args.ids = "c"
    with pytest.raises(SystemExit, match="not proposed"):
        cli.cmd_corpus_remove(args)
