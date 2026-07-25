"""Tests for `evalsuite score-external --skill <name> --results-dir DIR`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from knaif.evalsuite.cli import main
from knaif.evalsuite.corpus import CorpusRow, save_corpus
from knaif.evalsuite.protocols import VerifyResult


def _run(args: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["knaif.evalsuite"] + args
    try:
        main()
    finally:
        sys.argv = old_argv


def _make_results_dir(tmp_path: Path, entries: list[str]) -> Path:
    results_dir = tmp_path / "results"
    for entry in entries:
        d = results_dir / entry
        d.mkdir(parents=True)
        (d / "cmd.txt").write_text("ffmpeg -y -i fixture.mp4 out.mp4")
        (d / "out.mp4").write_bytes(b"artifact")
    return results_dir


def _make_corpus(tmp_path: Path, row_ids: list[str]) -> Path:
    corpus_path = tmp_path / "eval.jsonl"
    rows = [
        CorpusRow(
            id=rid,
            utterances=["convert to mp4"],
            expected_outcome="plan",
            fixture="clip.mp4",
            baseline={"command": f"ffmpeg -y -i input.mp4 -c:v libx264 {rid}_base.mp4"},
            expected_tool="convert_video",
            tags=["convert"],
        )
        for rid in row_ids
    ]
    save_corpus(rows, corpus_path)
    return corpus_path


# ── score-external writes score.json ─────────────────────────────────────────


def test_score_external_writes_score_json(tmp_path: Path):
    results_dir = _make_results_dir(tmp_path, ["r001__0", "r002__0"])
    corpus_path = _make_corpus(tmp_path, ["r001", "r002"])
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    baseline_path = tmp_path / "baseline.mp4"
    baseline_path.write_bytes(b"base")
    fake_result = VerifyResult(
        score=0.85, matched=["container=mp4"], failed=[], verifier_kind="output"
    )

    with (
        patch("knaif.evalsuite.cli._execute_against_fixture", return_value=baseline_path),
        patch(
            "knaif.evalsuite.cli._load_skill_verifiers",
            return_value=({"output_diff": MagicMock(return_value=fake_result)}, {}),
        ),
    ):
        _run(
            [
                "score-external",
                "--skill",
                "ffmpeg",
                "--results-dir",
                str(results_dir),
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(fixture_dir),
            ]
        )

    score_file = results_dir / "score.json"
    assert score_file.exists(), "score.json not written"
    data = json.loads(score_file.read_text(encoding="utf-8"))
    assert "entries" in data
    assert len(data["entries"]) == 2
    assert all(e["score"] == pytest.approx(0.85) for e in data["entries"])


def test_score_external_entry_contains_row_and_utt_idx(tmp_path: Path):
    results_dir = _make_results_dir(tmp_path, ["r001__0"])
    corpus_path = _make_corpus(tmp_path, ["r001"])
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    baseline_path = tmp_path / "baseline.mp4"
    baseline_path.write_bytes(b"base")
    fake_result = VerifyResult(score=0.75, matched=[], failed=["duration"], verifier_kind="output")

    with (
        patch("knaif.evalsuite.cli._execute_against_fixture", return_value=baseline_path),
        patch(
            "knaif.evalsuite.cli._load_skill_verifiers",
            return_value=({"output_diff": MagicMock(return_value=fake_result)}, {}),
        ),
    ):
        _run(
            [
                "score-external",
                "--skill",
                "ffmpeg",
                "--results-dir",
                str(results_dir),
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(fixture_dir),
            ]
        )

    data = json.loads((results_dir / "score.json").read_text(encoding="utf-8"))
    entry = data["entries"][0]
    assert entry["id"] == "r001"
    assert entry["utterance_idx"] == 0
    assert entry["score"] == pytest.approx(0.75)
    assert "duration" in " ".join(entry["failed"])


def test_score_external_skips_entry_without_corpus_row(tmp_path: Path):
    results_dir = _make_results_dir(tmp_path, ["r999__0"])  # no matching corpus row
    corpus_path = _make_corpus(tmp_path, ["r001"])
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()

    with (
        patch("knaif.evalsuite.cli._execute_against_fixture", return_value=None),
        patch(
            "knaif.evalsuite.cli._load_skill_verifiers",
            return_value=({"output_diff": MagicMock()}, {}),
        ),
    ):
        _run(
            [
                "score-external",
                "--skill",
                "ffmpeg",
                "--results-dir",
                str(results_dir),
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(fixture_dir),
            ]
        )

    data = json.loads((results_dir / "score.json").read_text(encoding="utf-8"))
    assert len(data["entries"]) == 0


def test_score_external_skips_entry_without_output_file(tmp_path: Path):
    results_dir = tmp_path / "results"
    d = results_dir / "r001__0"
    d.mkdir(parents=True)
    (d / "cmd.txt").write_text("ffmpeg -y -i input.mp4 out.mp4")
    # No output file created

    corpus_path = _make_corpus(tmp_path, ["r001"])
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    with (
        patch("knaif.evalsuite.cli._execute_against_fixture", return_value=None),
        patch(
            "knaif.evalsuite.cli._load_skill_verifiers",
            return_value=({"output_diff": MagicMock()}, {}),
        ),
    ):
        _run(
            [
                "score-external",
                "--skill",
                "ffmpeg",
                "--results-dir",
                str(results_dir),
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(fixture_dir),
            ]
        )

    data = json.loads((results_dir / "score.json").read_text(encoding="utf-8"))
    assert len(data["entries"]) == 0


# ── NEW: symmetric local-shaped scoreboard ────────────────────────────────────


def _make_corpus_with_clarify(tmp_path: Path) -> Path:
    from knaif.evalsuite.corpus import CorpusRow, save_corpus

    rows = [
        CorpusRow(
            id="r001",
            utterances=["convert to mp4"],
            expected_outcome="plan",
            fixture="clip.mp4",
            baseline={"command": "ffmpeg -y out.mp4"},
            expected_tool="convert_video",
            tags=["convert"],
            success_criteria={"container": "mp4"},
        ),
        CorpusRow(
            id="r002", utterances=["make it better"], expected_outcome="clarify", tags=["clarify"]
        ),
    ]
    p = tmp_path / "eval_mixed.jsonl"
    save_corpus(rows, p)
    return p


def _make_results_with_meta(results_dir: Path, elapsed_ms: int = 3200) -> None:
    entry_dir = results_dir / "r001__0"
    entry_dir.mkdir(parents=True, exist_ok=True)
    (entry_dir / "cmd.txt").write_text("ffmpeg -y -i in.mp4 out.mp4")
    (entry_dir / "out.mp4").write_bytes(b"fake")
    (entry_dir / "meta.json").write_text(json.dumps({"elapsed_ms": elapsed_ms}))

    clarify_dir = results_dir / "r002__0"
    clarify_dir.mkdir(parents=True, exist_ok=True)
    (clarify_dir / "cmd.txt").write_text("clarify: ambiguous request")


def test_score_external_new_scoreboard_has_local_schema_keys(tmp_path: Path):
    corpus_path = _make_corpus_with_clarify(tmp_path)
    results_dir = tmp_path / "results_new"
    _make_results_with_meta(results_dir)
    fake_result = VerifyResult(
        score=0.9, matched=["container=mp4"], failed=[], verifier_kind="output"
    )

    with (
        patch("knaif.evalsuite.cli._execute_against_fixture", return_value=tmp_path / "base.mp4"),
        patch(
            "knaif.evalsuite.cli._load_skill_verifiers",
            return_value=(
                {
                    "output_diff": MagicMock(return_value=fake_result),
                    "success": MagicMock(return_value=fake_result),
                },
                {},
            ),
        ),
    ):
        _run(
            [
                "score-external",
                "--skill",
                "ffmpeg",
                "--results-dir",
                str(results_dir),
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(tmp_path / "fixtures"),
                "--sandbox",
                str(tmp_path / "sandbox"),
            ]
        )

    data = json.loads((results_dir / "score.json").read_text())
    for key in (
        "total",
        "outcome_accuracy",
        "avg_knaif_score",
        "time_to_artifact_ms",
        "rows",
        "by_tag",
    ):
        assert key in data, f"Missing key: {key}"


def test_score_external_latency_from_meta_json(tmp_path: Path):
    corpus_path = _make_corpus_with_clarify(tmp_path)
    results_dir = tmp_path / "results_lat"
    _make_results_with_meta(results_dir, elapsed_ms=4500)
    fake_result = VerifyResult(
        score=1.0, matched=["container=mp4"], failed=[], verifier_kind="output"
    )

    with (
        patch("knaif.evalsuite.cli._execute_against_fixture", return_value=tmp_path / "base.mp4"),
        patch(
            "knaif.evalsuite.cli._load_skill_verifiers",
            return_value=(
                {
                    "output_diff": MagicMock(return_value=fake_result),
                    "success": MagicMock(return_value=fake_result),
                },
                {},
            ),
        ),
    ):
        _run(
            [
                "score-external",
                "--skill",
                "ffmpeg",
                "--results-dir",
                str(results_dir),
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(tmp_path / "fixtures"),
                "--sandbox",
                str(tmp_path / "sandbox"),
            ]
        )

    data = json.loads((results_dir / "score.json").read_text())
    plan_rows = [r for r in data["rows"] if r.get("actual_outcome") == "plan"]
    assert any(r.get("latency_ms") == pytest.approx(4500.0) for r in plan_rows)


def test_score_external_clarify_outcome_detected_from_cmd_txt(tmp_path: Path):
    corpus_path = _make_corpus_with_clarify(tmp_path)
    results_dir = tmp_path / "results_clar"
    _make_results_with_meta(results_dir)
    fake_result = VerifyResult(score=1.0, matched=[], failed=[], verifier_kind="output")

    with (
        patch("knaif.evalsuite.cli._execute_against_fixture", return_value=tmp_path / "base.mp4"),
        patch(
            "knaif.evalsuite.cli._load_skill_verifiers",
            return_value=(
                {
                    "output_diff": MagicMock(return_value=fake_result),
                    "success": MagicMock(return_value=fake_result),
                },
                {},
            ),
        ),
    ):
        _run(
            [
                "score-external",
                "--skill",
                "ffmpeg",
                "--results-dir",
                str(results_dir),
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(tmp_path / "fixtures"),
                "--sandbox",
                str(tmp_path / "sandbox"),
            ]
        )

    data = json.loads((results_dir / "score.json").read_text())
    clarify_rows = [r for r in data["rows"] if r.get("id") == "r002"]
    assert len(clarify_rows) == 1
    assert clarify_rows[0]["actual_outcome"] == "clarify"


def test_score_external_no_baseline_entry_has_null_score(tmp_path: Path):
    results_dir = _make_results_dir(tmp_path, ["r001__0"])
    corpus_path = tmp_path / "eval.jsonl"
    save_corpus(
        [
            CorpusRow(
                id="r001",
                utterances=["convert to mp4"],
                expected_outcome="plan",
                fixture="clip.mp4",
                baseline=None,  # no baseline command
                tags=["convert"],
            ),
        ],
        corpus_path,
    )
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    # success_criteria={} → success verifier returns 1.0 (no_criteria) — score is non-null
    # test that the row is present in both entries and rows
    fake_result = VerifyResult(
        score=1.0, matched=["no_criteria"], failed=[], verifier_kind="output"
    )
    with (
        patch("knaif.evalsuite.cli._execute_against_fixture", return_value=None),
        patch(
            "knaif.evalsuite.cli._load_skill_verifiers",
            return_value=(
                {
                    "output_diff": MagicMock(return_value=fake_result),
                    "success": MagicMock(return_value=fake_result),
                },
                {},
            ),
        ),
    ):
        _run(
            [
                "score-external",
                "--skill",
                "ffmpeg",
                "--results-dir",
                str(results_dir),
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(fixture_dir),
            ]
        )

    data = json.loads((results_dir / "score.json").read_text(encoding="utf-8"))
    # backward-compat entries still present
    assert "entries" in data
    # new local-shaped rows also present
    assert "rows" in data
    assert len(data["rows"]) == 1


def test_score_external_credits_command_text_filters_from_cmd_txt(tmp_path: Path):
    """The success verifier grades filters/flags/encoder against the command text.

    The local runner passes the command string as ``artifact`` and gets credit;
    score-external must do the same by reading ``cmd.txt`` so an external arm whose
    command contains the required filter is scored the same as local (apples-to-apples).
    """
    results_dir = tmp_path / "results"
    entry = results_dir / "r001__0"
    entry.mkdir(parents=True)
    # command text contains the required scale filter
    (entry / "cmd.txt").write_text("ffmpeg -y -i in.mp4 -vf scale=1280:720 -c:v libx264 out.mp4")
    (entry / "out.mp4").write_bytes(b"artifact")

    corpus_path = tmp_path / "eval.jsonl"
    save_corpus(
        [
            CorpusRow(
                id="r001",
                utterances=["resize and join"],
                expected_outcome="plan",
                fixture="clip.mp4",
                baseline=None,
                expected_tool="resize_video",
                success_criteria={"filters": ["scale"]},  # command-text only, no ffprobe
                tags=["resize"],
            ),
        ],
        corpus_path,
    )
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    # Use the REAL ffmpeg success verifier (no mock) so we exercise the artifact path.
    with patch("knaif.evalsuite.cli._execute_against_fixture", return_value=None):
        _run(
            [
                "score-external",
                "--skill",
                "ffmpeg",
                "--results-dir",
                str(results_dir),
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(fixture_dir),
            ]
        )

    data = json.loads((results_dir / "score.json").read_text(encoding="utf-8"))
    row = next(r for r in data["rows"] if r["id"] == "r001")
    assert row["knaif_score"] == pytest.approx(1.0), (
        f"scale filter in cmd.txt should be credited, got {row['knaif_score']} "
        f"matched={row['knaif_matched']} failed={row['knaif_failed']}"
    )
    assert "filter:scale" in row["knaif_matched"]
