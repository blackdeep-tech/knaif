"""Tests for multi-arm discovery and report generation via cmd_report."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from knaif.evalsuite.cli import main
from knaif.evalsuite.corpus import CorpusRow, save_corpus


def _run(args: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["knaif.evalsuite"] + args
    try:
        main()
    finally:
        sys.argv = old_argv


def _corpus(tmp_path: Path) -> Path:
    rows = [
        CorpusRow(
            id="r001", utterances=["convert to mp4"], expected_outcome="plan", tags=["convert"]
        ),
        CorpusRow(id="r002", utterances=["strip audio"], expected_outcome="plan", tags=["audio"]),
    ]
    p = tmp_path / "eval.jsonl"
    save_corpus(rows, p)
    return p


def _score_external_json(directory: Path, entries: list[dict]) -> Path:
    p = directory / "score.json"
    p.write_text(json.dumps({"entries": entries, "total": len(entries)}), encoding="utf-8")
    return p


def _local_runner_json(directory: Path, name: str, rows: list[dict]) -> Path:
    data = {"verifier": "output_diff", "total": len(rows), "rows": rows}
    p = directory / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _se_entry(row_id: str, score: float = 1.0) -> dict:
    return {
        "id": row_id,
        "utterance_idx": 0,
        "score": score,
        "matched": ["extension=mp4"],
        "failed": [],
        "artifact_path": None,
        "baseline_path": None,
    }


def _lr_row(row_id: str, score: float = 1.0) -> dict:
    return {
        "id": row_id,
        "utterance": "u",
        "utterance_idx": 0,
        "knaif_score": score,
        "knaif_matched": ["extension=mp4"],
        "knaif_failed": [],
        "artifact_path": None,
        "outcome_correct": True,
        "tags": [],
    }


# ── multi-arm discovery ───────────────────────────────────────────────────────


def test_report_discovers_two_arm_subdirs(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = _corpus(tmp_path)

    copilot_dir = results_dir / "github-copilot"
    copilot_dir.mkdir()
    _score_external_json(copilot_dir, [_se_entry("r001"), _se_entry("r002")])

    local_dir = results_dir / "local"
    local_dir.mkdir()
    _local_runner_json(
        local_dir, "ffmpeg_gemma_output_diff.json", [_lr_row("r001"), _lr_row("r002")]
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
    assert "github-copilot" in md
    assert "gemma" in md


def test_report_fallback_to_score_json_at_root(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = _corpus(tmp_path)

    _score_external_json(results_dir, [_se_entry("r001")])

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


def test_report_empty_results_dir_exits(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = _corpus(tmp_path)

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


def test_report_html_has_arm_column_per_arm(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    corpus_path = _corpus(tmp_path)

    copilot_dir = results_dir / "copilot"
    copilot_dir.mkdir()
    _score_external_json(copilot_dir, [_se_entry("r001")])

    local_dir = results_dir / "local"
    local_dir.mkdir()
    _local_runner_json(local_dir, "ffmpeg_gemma_output_diff.json", [_lr_row("r001")])

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
    assert "copilot" in html
    assert "gemma" in html
    assert "<table" in html


# ── flat-file scanning (local runner --save output) ───────────────────────────


def test_report_discovers_flat_local_runner_files(tmp_path: Path):
    """eval-report must work when flat JSON files sit directly in results_dir
    (the format produced by 'eval-success --save DIR')."""
    results_dir = tmp_path / "local3"
    results_dir.mkdir()
    corpus_path = _corpus(tmp_path)

    # Flat files directly in results_dir — no subdirs
    _local_runner_json(
        results_dir, "ffmpeg_qwen3-4b_success.json", [_lr_row("r001"), _lr_row("r002")]
    )
    _local_runner_json(
        results_dir, "ffmpeg_gemma3-4b_success.json", [_lr_row("r001", 0.5), _lr_row("r002")]
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
    assert "qwen3-4b" in md
    assert "gemma3-4b" in md


def test_report_cross_run_collision_uses_subdir_prefix(tmp_path: Path):
    """When two run subdirs contain the same backend, both arms must appear with
    distinct 'rundir/backend' names — not merged and not one silently dropped."""
    parent = tmp_path / "v2"
    parent.mkdir()
    corpus_path = _corpus(tmp_path)

    run2 = parent / "local2"
    run2.mkdir()
    _local_runner_json(run2, "ffmpeg_qwen3-4b_success.json", [_lr_row("r001", 0.8)])

    run3 = parent / "local3"
    run3.mkdir()
    _local_runner_json(run3, "ffmpeg_qwen3-4b_success.json", [_lr_row("r001", 1.0)])

    _run(
        ["report", "--skill", "ffmpeg", "--results-dir", str(parent), "--corpus", str(corpus_path)]
    )

    md = (parent / "report.md").read_text(encoding="utf-8")
    # Both runs must appear with distinct, subdir-qualified names
    assert "local2" in md
    assert "local3" in md
    # Entries must NOT be merged (total row count for qwen3-4b should not double)
    from knaif.evalsuite.corpus import load_corpus
    from knaif.evalsuite.report import discover_arms

    arms = discover_arms(parent, list(load_corpus(corpus_path)), "ffmpeg")
    assert len(arms) == 2, f"Expected 2 arms, got {len(arms)}: {list(arms)}"
    for name in arms:
        assert len(arms[name]) == 1, f"Arm {name!r} has {len(arms[name])} entries, expected 1"
