"""Tests for review_log.py and `evalsuite review` CLI command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from knaif.evalsuite.review_log import ReviewLog, load_review_log, save_review_log

# ── ReviewLog round-trip ──────────────────────────────────────────────────────


def test_review_log_add_and_get(tmp_path: Path):
    log = ReviewLog()
    log.mark(row_id="r001", utterance_idx=0, status="reviewed", notes="looks good")
    entry = log.get("r001", 0)
    assert entry is not None
    assert entry["status"] == "reviewed"
    assert entry["notes"] == "looks good"


def test_review_log_get_missing_returns_none():
    log = ReviewLog()
    assert log.get("r999", 0) is None


def test_review_log_overwrite(tmp_path: Path):
    log = ReviewLog()
    log.mark("r001", 0, "reviewed")
    log.mark("r001", 0, "rejected", notes="bad output")
    entry = log.get("r001", 0)
    assert entry["status"] == "rejected"
    assert entry["notes"] == "bad output"


def test_review_log_save_and_load(tmp_path: Path):
    log = ReviewLog()
    log.mark("r001", 0, "reviewed")
    log.mark("r002", 1, "rejected", notes="wrong codec")

    path = tmp_path / "review_log.json"
    save_review_log(log, path)

    loaded = load_review_log(path)
    assert loaded.get("r001", 0)["status"] == "reviewed"
    assert loaded.get("r002", 1)["notes"] == "wrong codec"


def test_review_log_load_missing_returns_empty(tmp_path: Path):
    log = load_review_log(tmp_path / "nonexistent.json")
    assert log.get("r001", 0) is None


def test_review_log_all_entries(tmp_path: Path):
    log = ReviewLog()
    log.mark("r001", 0, "reviewed")
    log.mark("r002", 0, "pending")
    entries = log.all_entries()
    assert len(entries) == 2
    ids = {e["id"] for e in entries}
    assert ids == {"r001", "r002"}


# ── evalsuite review CLI ───────────────────────────────────────────────────────


def _run(args: list[str]) -> None:
    from knaif.evalsuite.cli import main

    old_argv = sys.argv
    sys.argv = ["knaif.evalsuite"] + args
    try:
        main()
    finally:
        sys.argv = old_argv


def test_review_cli_marks_row(tmp_path: Path):
    log_path = tmp_path / "review_log.json"
    _run(
        [
            "review",
            "--log",
            str(log_path),
            "--row",
            "r001",
            "--utterance-idx",
            "0",
            "--status",
            "reviewed",
        ]
    )
    log = load_review_log(log_path)
    assert log.get("r001", 0)["status"] == "reviewed"


def test_review_cli_default_utterance_idx_zero(tmp_path: Path):
    log_path = tmp_path / "review_log.json"
    _run(
        [
            "review",
            "--log",
            str(log_path),
            "--row",
            "r001",
            "--status",
            "reviewed",
        ]
    )
    log = load_review_log(log_path)
    assert log.get("r001", 0) is not None


def test_review_cli_with_notes(tmp_path: Path):
    log_path = tmp_path / "review_log.json"
    _run(
        [
            "review",
            "--log",
            str(log_path),
            "--row",
            "r001",
            "--status",
            "rejected",
            "--notes",
            "output file has wrong codec",
        ]
    )
    log = load_review_log(log_path)
    assert log.get("r001", 0)["notes"] == "output file has wrong codec"


def test_review_cli_idempotent(tmp_path: Path):
    log_path = tmp_path / "review_log.json"
    for _ in range(3):
        _run(
            [
                "review",
                "--log",
                str(log_path),
                "--row",
                "r001",
                "--status",
                "reviewed",
            ]
        )
    data = json.loads(log_path.read_text(encoding="utf-8"))
    # Only one entry for r001__0
    assert sum(1 for e in data["entries"] if e["id"] == "r001" and e["utterance_idx"] == 0) == 1
