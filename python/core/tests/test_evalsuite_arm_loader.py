"""Tests for load_arm_entries and discover_arms."""

from __future__ import annotations

import json
from pathlib import Path

from knaif.evalsuite.corpus import CorpusRow, save_corpus
from knaif.evalsuite.report import discover_arms, load_arm_entries


def _corpus(tmp_path: Path) -> tuple[Path, list[CorpusRow]]:
    rows = [
        CorpusRow(
            id="r001",
            utterances=["convert to mp4", "make it mp4"],
            expected_outcome="plan",
            tags=["convert"],
        ),
        CorpusRow(
            id="r002",
            utterances=["strip audio"],
            expected_outcome="plan",
            tags=["audio"],
        ),
    ]
    p = tmp_path / "eval.jsonl"
    save_corpus(rows, p)
    return p, rows


def _score_external_file(directory: Path, name: str = "score.json") -> Path:
    data = {
        "entries": [
            {
                "id": "r001",
                "utterance_idx": 0,
                "score": 1.0,
                "matched": ["extension=mp4"],
                "failed": [],
                "artifact_path": "results/r001__0/out.mp4",
                "baseline_path": "results/.baselines/r001/out.mp4",
            },
            {
                "id": "r002",
                "utterance_idx": 0,
                "score": 0.5,
                "matched": [],
                "failed": ["duration"],
                "artifact_path": "results/r002__0/out.mp4",
                "baseline_path": None,
            },
        ],
        "total": 2,
    }
    path = directory / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _local_runner_file(directory: Path, name: str = "ffmpeg_gemma_output_diff.json") -> Path:
    data = {
        "verifier": "output_diff",
        "total": 2,
        "outcome_accuracy": 1.0,
        "avg_knaif_score": 0.75,
        "rows": [
            {
                "id": "r001",
                "utterance": "convert to mp4",
                "utterance_idx": 0,
                "knaif_score": 1.0,
                "knaif_matched": ["extension=mp4"],
                "knaif_failed": [],
                "artifact_path": "sandbox/gemma/r001__0/out.mp4",
                "outcome_correct": True,
                "tags": ["convert"],
            },
            {
                "id": "r002",
                "utterance": "strip audio",
                "utterance_idx": 0,
                "knaif_score": 0.5,
                "knaif_matched": [],
                "knaif_failed": ["duration"],
                "artifact_path": "sandbox/gemma/r002__0/out.mp4",
                "outcome_correct": True,
                "tags": ["audio"],
            },
        ],
    }
    path = directory / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── load_arm_entries — score_external format ──────────────────────────────────


def test_score_external_arm_name_is_parent_dir(tmp_path: Path):
    arm_dir = tmp_path / "github-copilot"
    arm_dir.mkdir()
    json_file = _score_external_file(arm_dir)
    _, rows = _corpus(tmp_path)
    arm_name, _ = load_arm_entries(json_file, rows)
    assert arm_name == "github-copilot"


def test_score_external_with_rows_named_by_parent_and_uses_rows(tmp_path: Path):
    """Real score-external output carries BOTH `entries` and local-shaped `rows`.

    It must still be named by its arm subdir (not the "score" filename stem), and
    load from `rows` so clarify/reject rows — which never appear in `entries` — are
    not dropped from the arm.
    """
    arm_dir = tmp_path / "claude-code_claude-opus-4-8"
    arm_dir.mkdir()
    data = {
        "entries": [  # backward-compat: plan rows with artifacts only
            {"id": "r001", "utterance_idx": 0, "score": 1.0, "matched": [], "failed": []},
        ],
        "rows": [
            {
                "id": "r001",
                "utterance_idx": 0,
                "knaif_score": 1.0,
                "knaif_matched": [],
                "knaif_failed": [],
                "outcome_correct": True,
                "latency_ms": 640.0,
                "actual_outcome": "plan",
                "expected_outcome": "plan",
                "tags": ["convert"],
            },
            {
                "id": "r003",
                "utterance_idx": 0,
                "knaif_score": None,
                "knaif_matched": [],
                "knaif_failed": [],
                "outcome_correct": True,
                "latency_ms": None,
                "actual_outcome": "clarify",
                "expected_outcome": "clarify",
                "tags": ["ambiguous"],
            },
        ],
        "total": 2,
    }
    json_file = arm_dir / "score.json"
    json_file.write_text(json.dumps(data), encoding="utf-8")
    _, rows = _corpus(tmp_path)
    arm_name, entries = load_arm_entries(json_file, rows)
    assert arm_name == "claude-code_claude-opus-4-8"
    ids = {e.id for e in entries}
    assert "r003" in ids, "clarify row from `rows` must not be dropped"
    r001 = next(e for e in entries if e.id == "r001")
    assert r001.latency_ms == 640.0


def test_score_external_populates_utterance_from_corpus(tmp_path: Path):
    arm_dir = tmp_path / "copilot"
    arm_dir.mkdir()
    json_file = _score_external_file(arm_dir)
    _, rows = _corpus(tmp_path)
    _, entries = load_arm_entries(json_file, rows)
    r001 = next(e for e in entries if e.id == "r001")
    assert r001.utterance == "convert to mp4"


def test_score_external_populates_tags_from_corpus(tmp_path: Path):
    arm_dir = tmp_path / "copilot"
    arm_dir.mkdir()
    json_file = _score_external_file(arm_dir)
    _, rows = _corpus(tmp_path)
    _, entries = load_arm_entries(json_file, rows)
    r001 = next(e for e in entries if e.id == "r001")
    assert "convert" in r001.tags


def test_score_external_null_score_stays_none(tmp_path: Path):
    arm_dir = tmp_path / "copilot"
    arm_dir.mkdir()
    data = {
        "entries": [{"id": "r001", "utterance_idx": 0, "score": None, "matched": [], "failed": []}],
        "total": 1,
    }
    json_file = arm_dir / "score.json"
    json_file.write_text(json.dumps(data), encoding="utf-8")
    _, rows = _corpus(tmp_path)
    _, entries = load_arm_entries(json_file, rows)
    assert entries[0].score is None


def test_score_external_maps_matched_and_failed(tmp_path: Path):
    arm_dir = tmp_path / "copilot"
    arm_dir.mkdir()
    json_file = _score_external_file(arm_dir)
    _, rows = _corpus(tmp_path)
    _, entries = load_arm_entries(json_file, rows)
    r002 = next(e for e in entries if e.id == "r002")
    assert r002.failed == ["duration"]
    assert r002.score == 0.5


# ── load_arm_entries — local_runner format ────────────────────────────────────


def test_local_runner_arm_name_strips_skill_and_verifier(tmp_path: Path):
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    json_file = _local_runner_file(local_dir, "ffmpeg_gemma_output_diff.json")
    _, rows = _corpus(tmp_path)
    arm_name, _ = load_arm_entries(json_file, rows, skill="ffmpeg")
    assert arm_name == "gemma"


def test_local_runner_maps_knaif_score_to_score(tmp_path: Path):
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    json_file = _local_runner_file(local_dir)
    _, rows = _corpus(tmp_path)
    _, entries = load_arm_entries(json_file, rows, skill="ffmpeg")
    r001 = next(e for e in entries if e.id == "r001")
    assert r001.score == 1.0
    assert r001.matched == ["extension=mp4"]


def test_local_runner_preserves_outcome_correct(tmp_path: Path):
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    json_file = _local_runner_file(local_dir)
    _, rows = _corpus(tmp_path)
    _, entries = load_arm_entries(json_file, rows, skill="ffmpeg")
    assert all(e.outcome_correct is True for e in entries)


def test_local_runner_utterance_from_json(tmp_path: Path):
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    json_file = _local_runner_file(local_dir)
    _, rows = _corpus(tmp_path)
    _, entries = load_arm_entries(json_file, rows, skill="ffmpeg")
    r001 = next(e for e in entries if e.id == "r001")
    assert r001.utterance == "convert to mp4"


# ── discover_arms ─────────────────────────────────────────────────────────────


def test_discover_arms_finds_score_external_subdir(tmp_path: Path):
    arm_dir = tmp_path / "github-copilot"
    arm_dir.mkdir()
    _score_external_file(arm_dir)
    _, rows = _corpus(tmp_path)
    arms = discover_arms(tmp_path, rows)
    assert "github-copilot" in arms


def test_discover_arms_skips_dot_dirs(tmp_path: Path):
    hidden = tmp_path / ".baselines"
    hidden.mkdir()
    (hidden / "score.json").write_text('{"entries":[], "total":0}', encoding="utf-8")
    _, rows = _corpus(tmp_path)
    arms = discover_arms(tmp_path, rows)
    assert not arms


def test_discover_arms_local_dir_two_backends(tmp_path: Path):
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    _local_runner_file(local_dir, "ffmpeg_gemma_output_diff.json")
    _local_runner_file(local_dir, "ffmpeg_llama_output_diff.json")
    _, rows = _corpus(tmp_path)
    arms = discover_arms(tmp_path, rows, skill="ffmpeg")
    assert len(arms) == 2
    assert "gemma" in arms
    assert "llama" in arms


def test_discover_arms_skips_review_log(tmp_path: Path):
    arm_dir = tmp_path / "results"
    arm_dir.mkdir()
    (arm_dir / "review_log.json").write_text('{"entries":[]}', encoding="utf-8")
    _, rows = _corpus(tmp_path)
    arms = discover_arms(tmp_path, rows)
    assert not arms


def test_discover_arms_empty_returns_empty(tmp_path: Path):
    _, rows = _corpus(tmp_path)
    arms = discover_arms(tmp_path, rows)
    assert arms == {}
