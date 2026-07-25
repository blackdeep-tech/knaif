"""Tests for `evalsuite report --skill <name> --results-dir DIR`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from knaif.evalsuite.cli import main
from knaif.evalsuite.corpus import CorpusRow, save_corpus
from knaif.evalsuite.review_log import ReviewLog, save_review_log


def _run(args: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["knaif.evalsuite"] + args
    try:
        main()
    finally:
        sys.argv = old_argv


def _make_score_json(results_dir: Path, entries: list[dict]) -> Path:
    score_file = results_dir / "score.json"
    score_file.write_text(
        json.dumps({"entries": entries, "total": len(entries)}, indent=2),
        encoding="utf-8",
    )
    return score_file


def _entry(
    row_id: str = "r001",
    utt_idx: int = 0,
    score: float | None = 0.9,
    matched: list | None = None,
    failed: list | None = None,
) -> dict:
    return {
        "id": row_id,
        "utterance_idx": utt_idx,
        "entry_dir": f"results/{row_id}__{utt_idx}",
        "artifact_path": f"results/{row_id}__{utt_idx}/out.mp4",
        "baseline_path": f"baselines/{row_id}/out.mp4",
        "score": score,
        "matched": matched or ["container=mp4", "video_codec=h264"],
        "failed": failed or [],
    }


# ── report emits both report.md and report.html ───────────────────────────────


def test_report_creates_md_and_html(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = tmp_path / "eval.jsonl"
    save_corpus(
        [
            CorpusRow(
                id="r001", utterances=["convert to mp4"], expected_outcome="plan", tags=["convert"]
            ),
        ],
        corpus_path,
    )

    _make_score_json(results_dir, [_entry()])

    _run(
        [
            "report",
            "--skill",
            "ffmpeg",
            "--results-dir",
            str(results_dir),
            "--corpus",
            str(corpus_path),
        ]
    )

    assert (results_dir / "report.md").exists()
    assert (results_dir / "report.html").exists()


def test_report_md_contains_summary_section(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = tmp_path / "eval.jsonl"
    save_corpus(
        [
            CorpusRow(
                id="r001", utterances=["convert to mp4"], expected_outcome="plan", tags=["convert"]
            ),
            CorpusRow(
                id="r002", utterances=["strip audio"], expected_outcome="plan", tags=["audio"]
            ),
        ],
        corpus_path,
    )

    _make_score_json(
        results_dir,
        [
            _entry("r001", score=1.0),
            _entry("r002", score=0.5, failed=["duration"]),
        ],
    )

    _run(
        [
            "report",
            "--skill",
            "ffmpeg",
            "--results-dir",
            str(results_dir),
            "--corpus",
            str(corpus_path),
        ]
    )

    md = (results_dir / "report.md").read_text(encoding="utf-8")
    assert "## Summary" in md
    # Average score present
    assert "0.75" in md or "avg" in md.lower()


def test_report_md_contains_failures_section(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = tmp_path / "eval.jsonl"
    save_corpus(
        [
            CorpusRow(
                id="r001", utterances=["convert to mp4"], expected_outcome="plan", tags=["convert"]
            ),
        ],
        corpus_path,
    )

    _make_score_json(
        results_dir,
        [
            _entry("r001", score=0.3, failed=["video_codec", "duration"]),
        ],
    )

    _run(
        [
            "report",
            "--skill",
            "ffmpeg",
            "--results-dir",
            str(results_dir),
            "--corpus",
            str(corpus_path),
        ]
    )

    md = (results_dir / "report.md").read_text(encoding="utf-8")
    assert "r001" in md
    assert "video_codec" in md or "duration" in md


def test_report_html_contains_table(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = tmp_path / "eval.jsonl"
    save_corpus(
        [
            CorpusRow(
                id="r001", utterances=["convert to mp4"], expected_outcome="plan", tags=["convert"]
            ),
        ],
        corpus_path,
    )

    _make_score_json(results_dir, [_entry()])

    _run(
        [
            "report",
            "--skill",
            "ffmpeg",
            "--results-dir",
            str(results_dir),
            "--corpus",
            str(corpus_path),
        ]
    )

    html = (results_dir / "report.html").read_text(encoding="utf-8")
    assert "<table" in html
    assert "r001" in html


def test_report_skips_missing_score_json(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = tmp_path / "eval.jsonl"
    save_corpus(
        [
            CorpusRow(id="r001", utterances=["u"], expected_outcome="plan"),
        ],
        corpus_path,
    )

    # No score.json
    with pytest.raises(SystemExit):
        _run(
            [
                "report",
                "--skill",
                "ffmpeg",
                "--results-dir",
                str(results_dir),
                "--corpus",
                str(corpus_path),
            ]
        )


# ── review log integration ────────────────────────────────────────────────────


def test_report_review_log_annotates_md(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = tmp_path / "eval.jsonl"
    save_corpus(
        [CorpusRow(id="r001", utterances=["convert to mp4"], expected_outcome="plan", tags=[])],
        corpus_path,
    )
    _make_score_json(results_dir, [_entry("r001", score=1.0)])

    log = ReviewLog()
    log.mark("r001", 0, "reviewed")
    save_review_log(log, results_dir / "review_log.json")

    _run(
        [
            "report",
            "--skill",
            "ffmpeg",
            "--results-dir",
            str(results_dir),
            "--corpus",
            str(corpus_path),
        ]
    )

    md = (results_dir / "report.md").read_text(encoding="utf-8")
    assert "✓ reviewed" in md


def test_report_explicit_review_log_path(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = tmp_path / "eval.jsonl"
    save_corpus(
        [CorpusRow(id="r001", utterances=["u"], expected_outcome="plan", tags=[])],
        corpus_path,
    )
    _make_score_json(results_dir, [_entry("r001", score=1.0)])

    log = ReviewLog()
    log.mark("r001", 0, "rejected")
    log_path = tmp_path / "custom_log.json"
    save_review_log(log, log_path)

    _run(
        [
            "report",
            "--skill",
            "ffmpeg",
            "--results-dir",
            str(results_dir),
            "--corpus",
            str(corpus_path),
            "--review-log",
            str(log_path),
        ]
    )

    md = (results_dir / "report.md").read_text(encoding="utf-8")
    assert "✗ rejected" in md


def test_report_no_review_log_does_not_error(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = tmp_path / "eval.jsonl"
    save_corpus(
        [CorpusRow(id="r001", utterances=["u"], expected_outcome="plan", tags=[])],
        corpus_path,
    )
    _make_score_json(results_dir, [_entry()])

    # No review_log.json exists — should not raise
    _run(
        [
            "report",
            "--skill",
            "ffmpeg",
            "--results-dir",
            str(results_dir),
            "--corpus",
            str(corpus_path),
        ]
    )
    assert (results_dir / "report.md").exists()
