"""Tests for cheap and honest verifiers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from skills.ffmpeg.eval.verifiers import VERIFIERS


def _mock_output(artifact: str | None = "ffmpeg -y -i in.mp4 out.mp4") -> MagicMock:
    m = MagicMock()
    m.artifact = artifact
    m.artifact_path = None
    m.outcome = "plan"
    m.plan = {"plan": [{"tool": "convert_video", "args": {}}]}
    return m


# ── VERIFIERS dict ─────────────────────────────────────────────────────────────


def test_cheap_in_verifiers():
    assert "cheap" in VERIFIERS


def test_honest_in_verifiers():
    assert "honest" in VERIFIERS


# ── cheap ──────────────────────────────────────────────────────────────────────


def test_cheap_returns_full_score_for_valid_ffmpeg_artifact(tmp_path: Path):
    cheap = VERIFIERS["cheap"]
    result = cheap(_mock_output("ffmpeg -y -i in.mp4 out.mp4"), {}, tmp_path)
    assert result.score == pytest.approx(1.0)


def test_cheap_returns_zero_for_no_artifact(tmp_path: Path):
    cheap = VERIFIERS["cheap"]
    result = cheap(_mock_output(None), {}, tmp_path)
    assert result.score == pytest.approx(0.0)


def test_cheap_returns_partial_score_for_non_ffmpeg_artifact(tmp_path: Path):
    cheap = VERIFIERS["cheap"]
    result = cheap(_mock_output("something else entirely"), {}, tmp_path)
    assert result.score < 1.0


def test_cheap_verifier_kind_is_plan(tmp_path: Path):
    cheap = VERIFIERS["cheap"]
    result = cheap(_mock_output(), {}, tmp_path)
    assert result.verifier_kind == "plan"


# ── honest ─────────────────────────────────────────────────────────────────────


def test_honest_returns_zero_for_no_artifact(tmp_path: Path):
    honest = VERIFIERS["honest"]
    result = honest(_mock_output(None), {}, tmp_path)
    assert result.score == pytest.approx(0.0)


def test_honest_returns_full_score_when_artifact_path_exists(tmp_path: Path):
    out = tmp_path / "out.mp4"
    out.write_bytes(b"fake")
    output = _mock_output("ffmpeg -y -i in.mp4 out.mp4")
    output.artifact_path = out
    honest = VERIFIERS["honest"]
    result = honest(output, {}, tmp_path)
    assert result.score == pytest.approx(1.0)


def test_honest_verifier_kind_is_output(tmp_path: Path):
    honest = VERIFIERS["honest"]
    result = honest(_mock_output(None), {}, tmp_path)
    assert result.verifier_kind == "output"
