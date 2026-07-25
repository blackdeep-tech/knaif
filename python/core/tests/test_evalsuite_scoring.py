"""Tests for evalsuite scoring.py: verifier dispatch + aggregation correctness."""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.protocols import VerifyResult
from knaif.evalsuite.runner import AgentOutput
from knaif.evalsuite.scoring import score_corpus


def _row(
    id: str = "r001",
    utterance: str = "convert to mp4",
    expected_outcome: str = "plan",
    tags: list[str] | None = None,
    expected_tool: str | None = "convert_video",
) -> CorpusRow:
    return CorpusRow(
        id=id,
        utterances=[utterance],
        expected_outcome=expected_outcome,
        expected_tool=expected_tool,
        tags=tags or ["convert"],
    )


def _output(
    id: str = "r001",
    outcome: str = "plan",
    artifact: str | None = None,
    plan: dict | None = None,
    latency_ms: float = 100.0,
) -> AgentOutput:
    return AgentOutput(
        id=id,
        utterance="convert to mp4",
        plan=plan
        or {
            "plan": [
                {
                    "tool": "convert_video",
                    "args": {"inputs": ["clip.mov"], "container": "mp4"},
                }
            ]
        },
        artifact=artifact or "ffmpeg -y -i clip.mov -c:v libx264 -c:a aac clip_converted.mp4",
        outcome=outcome,
        latency_ms=latency_ms,
    )


def _perfect(output: AgentOutput, criteria: dict, sandbox: Path) -> VerifyResult:
    return VerifyResult(score=1.0, matched=["all"], verifier_kind="command")


# ── multi-output rows ────────────────────────────────────────────────────────


def test_score_corpus_multi_output_uses_grade_outputs(tmp_path: Path):
    row = CorpusRow(
        id="m1",
        utterances=["trim then extract"],
        expected_outcome="plan",
        expected_tool="trim_video",
        expected_tools=["trim_video", "extract_audio"],
        tags=["multi_output"],
        outputs=[
            {"command": "a", "criteria": {"video_codec": "h264"}},
            {"command": "b", "criteria": {"audio_codec": "mp3"}},
        ],
    )
    out = AgentOutput(
        id="m1",
        utterance="trim then extract",
        plan={"plan": [{"tool": "trim_video", "args": {}}]},
        artifact="ffmpeg -y -i clip.mp4 clip_trimmed.mp4",
        outcome="plan",
        latency_ms=10.0,
        artifact_paths=[tmp_path / "v.mp4", tmp_path / "a.mp3"],
    )

    captured: dict = {}

    def fake_grade(paths, spec, sandbox):
        captured["paths"] = paths
        captured["spec"] = spec
        return VerifyResult(score=0.75, matched=["ok"], verifier_kind="output")

    sb = score_corpus(
        [out], [row], {"success": _perfect, "grade_outputs": fake_grade}, "success", tmp_path
    )
    # The multi-output branch must route to grade_outputs (not the success verifier).
    assert captured["paths"] == out.artifact_paths
    assert captured["spec"] == row.outputs
    assert sb["rows"][0]["knaif_score"] == pytest.approx(0.75)


def test_score_corpus_single_output_still_uses_named_verifier(tmp_path: Path):
    # A non-multi-output row must still go through the chosen verifier.
    sb = score_corpus([_output()], [_row()], {"success": _perfect}, "success", tmp_path)
    assert sb["rows"][0]["knaif_score"] == pytest.approx(1.0)


def test_cheap_verifier_does_not_route_outputs_rows_to_grade_outputs(tmp_path: Path):
    """Under cheap (no execution → no artifacts), an `outputs` row must fall back to
    the chosen plan-level verifier, not score 0.0 via grade_outputs.

    Regression guard for the cheap-mode chain artifact: grade_outputs needs produced
    files, which cheap never makes, so it would score every chain 0.0.
    """
    row = CorpusRow(
        id="c1",
        utterances=["trim then resize"],
        expected_outcome="plan",
        expected_tool="trim_video",
        expected_tools=["trim_video", "resize_video"],
        tags=["complex"],
        outputs=[
            {"command": "a", "criteria": {"video_codec": "h264"}},
            {"command": "b", "criteria": {"max_width": 1280}},
        ],
    )
    out = AgentOutput(
        id="c1",
        utterance="trim then resize",
        plan={"plan": [{"tool": "trim_video", "args": {}}]},
        artifact="ffmpeg -y -i clip.mp4 clip_trimmed.mp4",
        outcome="plan",
        latency_ms=10.0,
        # cheap mode: no artifact_paths produced
    )
    called = {"grade": False}

    def fake_grade(paths, spec, sandbox):
        called["grade"] = True
        return VerifyResult(score=0.0, failed=["out0:output_not_produced"], verifier_kind="output")

    sb = score_corpus(
        [out], [row], {"cheap": _perfect, "grade_outputs": fake_grade}, "cheap", tmp_path
    )
    assert called["grade"] is False, "cheap must not route outputs rows to grade_outputs"
    assert sb["rows"][0]["knaif_score"] == pytest.approx(1.0)
    assert sb["rows"][0]["verifier_kind"] != "output"


def test_success_verifier_still_routes_outputs_rows_to_grade_outputs(tmp_path: Path):
    """The executing verifiers must keep using grade_outputs (artifact grading)."""
    row = CorpusRow(
        id="s1",
        utterances=["trim then resize"],
        expected_outcome="plan",
        expected_tool="trim_video",
        tags=["complex"],
        outputs=[{"command": "a", "criteria": {"video_codec": "h264"}}],
    )
    out = AgentOutput(
        id="s1",
        utterance="trim then resize",
        plan={"plan": [{"tool": "trim_video", "args": {}}]},
        artifact="ffmpeg -y -i clip.mp4 out.mp4",
        outcome="plan",
        latency_ms=10.0,
        artifact_paths=[tmp_path / "v.mp4"],
    )
    called = {"grade": False}

    def fake_grade(paths, spec, sandbox):
        called["grade"] = True
        return VerifyResult(score=0.9, matched=["ok"], verifier_kind="output")

    sb = score_corpus(
        [out], [row], {"success": _perfect, "grade_outputs": fake_grade}, "success", tmp_path
    )
    assert called["grade"] is True
    assert sb["rows"][0]["knaif_score"] == pytest.approx(0.9)


def _zero(output: AgentOutput, criteria: dict, sandbox: Path) -> VerifyResult:
    return VerifyResult(score=0.0, failed=["all"], verifier_kind="command")


# ── basic scoreboard structure ─────────────────────────────────────────────


def test_scoreboard_keys_present(tmp_path: Path):
    sb = score_corpus([_output()], [_row()], {"cheap": _perfect}, "cheap", tmp_path)
    assert "verifier" in sb
    assert "total" in sb
    assert "outcome_accuracy" in sb
    assert "avg_knaif_score" in sb
    assert "avg_baseline_score" in sb
    assert "intent_metrics" in sb
    assert "by_tag" in sb
    assert "rows" in sb


def test_total_count(tmp_path: Path):
    rows = [_row(id=f"r{i:03d}") for i in range(4)]
    outputs = [_output(id=f"r{i:03d}") for i in range(4)]
    sb = score_corpus(outputs, rows, {"cheap": _perfect}, "cheap", tmp_path)
    assert sb["total"] == 4


# ── outcome accuracy ───────────────────────────────────────────────────────


def test_outcome_accuracy_all_correct(tmp_path: Path):
    sb = score_corpus(
        [_output(outcome="plan")], [_row(expected_outcome="plan")], {}, "cheap", tmp_path
    )
    assert sb["outcome_accuracy"] == pytest.approx(1.0)


def test_outcome_accuracy_all_wrong(tmp_path: Path):
    sb = score_corpus(
        [_output(outcome="clarify")], [_row(expected_outcome="plan")], {}, "cheap", tmp_path
    )
    assert sb["outcome_accuracy"] == pytest.approx(0.0)


def test_outcome_accuracy_mixed(tmp_path: Path):
    rows = [_row(id=f"r{i:03d}") for i in range(4)]
    outputs = [_output(id=f"r{i:03d}", outcome="plan" if i < 3 else "clarify") for i in range(4)]
    sb = score_corpus(outputs, rows, {"cheap": _perfect}, "cheap", tmp_path)
    assert sb["outcome_accuracy"] == pytest.approx(0.75)


# ── knaif scores ───────────────────────────────────────────────────────────


def test_avg_knaif_score_perfect(tmp_path: Path):
    sb = score_corpus([_output()], [_row()], {"cheap": _perfect}, "cheap", tmp_path)
    assert sb["avg_knaif_score"] == pytest.approx(1.0)


def test_avg_knaif_score_zero(tmp_path: Path):
    sb = score_corpus([_output()], [_row()], {"cheap": _zero}, "cheap", tmp_path)
    assert sb["avg_knaif_score"] == pytest.approx(0.0)


def test_avg_baseline_score_is_none(tmp_path: Path):
    # Baseline scoring now happens in the output_diff runner (T10); score_corpus returns None.
    sb = score_corpus([_output()], [_row()], {"cheap": _perfect}, "cheap", tmp_path)
    assert sb["avg_baseline_score"] is None


# ── clarify / error rows skip verifier ────────────────────────────────────


def test_clarify_outcome_skips_verifier(tmp_path: Path):
    row = _row(expected_outcome="clarify")
    out = _output(
        outcome="clarify", artifact=None, plan={"plan": [{"tool": "clarify", "args": {}}]}
    )
    sb = score_corpus([out], [row], {"cheap": _perfect}, "cheap", tmp_path)
    assert sb["rows"][0]["knaif_score"] is None


def test_error_outcome_skips_verifier(tmp_path: Path):
    out = _output(outcome="error", artifact=None)
    sb = score_corpus([out], [_row()], {"cheap": _perfect}, "cheap", tmp_path)
    assert sb["rows"][0]["knaif_score"] is None


def test_missing_verifier_name_gives_none_score(tmp_path: Path):
    sb = score_corpus([_output()], [_row()], {}, "cheap", tmp_path)
    assert sb["rows"][0]["knaif_score"] is None


# ── by-tag breakdowns ─────────────────────────────────────────────────────


def test_by_tag_groups_correctly(tmp_path: Path):
    rows = [
        _row(id="r001", tags=["convert"]),
        _row(id="r002", tags=["platform"]),
        _row(id="r003", tags=["convert"]),
    ]
    outputs = [_output(id=r.id) for r in rows]
    sb = score_corpus(outputs, rows, {"cheap": _perfect}, "cheap", tmp_path)
    assert sb["by_tag"]["convert"]["total"] == 2
    assert sb["by_tag"]["platform"]["total"] == 1


def test_by_tag_multi_tag_row(tmp_path: Path):
    row = _row(id="r001", tags=["convert", "feature_gap"])
    out = _output(id="r001")
    sb = score_corpus([out], [row], {"cheap": _perfect}, "cheap", tmp_path)
    assert "convert" in sb["by_tag"]
    assert "feature_gap" in sb["by_tag"]


def test_by_tag_outcome_accuracy(tmp_path: Path):
    rows = [
        _row(id="r001", expected_outcome="plan", tags=["convert"]),
        _row(id="r002", expected_outcome="plan", tags=["convert"]),
    ]
    outputs = [
        _output(id="r001", outcome="plan"),
        _output(id="r002", outcome="clarify"),
    ]
    sb = score_corpus(outputs, rows, {}, "cheap", tmp_path)
    assert sb["by_tag"]["convert"]["outcome_accuracy"] == pytest.approx(0.5)


# ── intent metrics ────────────────────────────────────────────────────────


def test_intent_metrics_present(tmp_path: Path):
    sb = score_corpus([_output()], [_row()], {"cheap": _perfect}, "cheap", tmp_path)
    assert "tool_accuracy" in sb["intent_metrics"]
    assert "schema_validity" in sb["intent_metrics"]


def test_intent_metrics_tool_correct(tmp_path: Path):
    row = _row(expected_tool="convert_video")
    out = _output(
        plan={
            "plan": [
                {"tool": "convert_video", "args": {"inputs": ["clip.mov"], "container": "mp4"}}
            ]
        }
    )
    sb = score_corpus([out], [row], {}, "cheap", tmp_path)
    assert sb["intent_metrics"]["tool_accuracy"] == pytest.approx(1.0)


def test_intent_metrics_tool_wrong(tmp_path: Path):
    row = _row(expected_tool="resize_video")
    out = _output(plan={"plan": [{"tool": "convert_video", "args": {}}]})
    sb = score_corpus([out], [row], {}, "cheap", tmp_path)
    assert sb["intent_metrics"]["tool_accuracy"] == pytest.approx(0.0)


# ── rows list ─────────────────────────────────────────────────────────────


def test_rows_list_length_matches(tmp_path: Path):
    rows = [_row(id=f"r{i:03d}") for i in range(3)]
    outputs = [_output(id=f"r{i:03d}") for i in range(3)]
    sb = score_corpus(outputs, rows, {"cheap": _perfect}, "cheap", tmp_path)
    assert len(sb["rows"]) == 3


def test_rows_contain_expected_keys(tmp_path: Path):
    sb = score_corpus([_output()], [_row()], {"cheap": _perfect}, "cheap", tmp_path)
    row_entry = sb["rows"][0]
    for key in (
        "id",
        "utterance",
        "expected_outcome",
        "actual_outcome",
        "outcome_correct",
        "tags",
        "latency_ms",
        "knaif_score",
        "baseline_score",
    ):
        assert key in row_entry, f"Missing key: {key}"


# ── time-to-artifact aggregates ───────────────────────────────────────────


def test_time_to_artifact_block_present(tmp_path: Path):
    rows = [_row(id=f"r{i:03d}") for i in range(3)]
    outputs = [_output(id=f"r{i:03d}", latency_ms=200.0) for i in range(3)]
    sb = score_corpus(outputs, rows, {}, "cheap", tmp_path)
    tta = sb["time_to_artifact_ms"]
    assert tta is not None
    for key in ("count", "mean_ms", "p50_ms", "p95_ms", "max_ms", "total_s"):
        assert key in tta


def test_time_to_artifact_excludes_warmup_row(tmp_path: Path):
    # First row is 10s, others 100ms — without warmup skip the mean would be ~2s.
    rows = [_row(id=f"r{i:03d}") for i in range(4)]
    outputs = [
        _output(id="r000", latency_ms=10_000.0),
        _output(id="r001", latency_ms=100.0),
        _output(id="r002", latency_ms=100.0),
        _output(id="r003", latency_ms=100.0),
    ]
    sb = score_corpus(outputs, rows, {}, "cheap", tmp_path)
    tta = sb["time_to_artifact_ms"]
    assert tta["count"] == 3
    assert tta["mean_ms"] == pytest.approx(100.0)
    assert tta["max_ms"] == pytest.approx(100.0)


def test_time_to_artifact_single_row_not_dropped(tmp_path: Path):
    # With only one row, warmup-skip would erase all data; we keep it instead.
    sb = score_corpus([_output(latency_ms=250.0)], [_row()], {}, "cheap", tmp_path)
    tta = sb["time_to_artifact_ms"]
    assert tta is not None
    assert tta["count"] == 1
    assert tta["mean_ms"] == pytest.approx(250.0)


def test_time_to_artifact_skips_non_plan_outcomes(tmp_path: Path):
    # clarify/reject/error rows are excluded — they don't produce a final command.
    rows = [
        _row(id="r000", expected_outcome="plan"),
        _row(id="r001", expected_outcome="clarify"),
        _row(id="r002", expected_outcome="plan"),
    ]
    outputs = [
        _output(id="r000", outcome="plan", latency_ms=100.0),
        _output(id="r001", outcome="clarify", latency_ms=9_999.0),
        _output(id="r002", outcome="plan", latency_ms=200.0),
    ]
    sb = score_corpus(outputs, rows, {}, "cheap", tmp_path)
    tta = sb["time_to_artifact_ms"]
    # warmup (r000) dropped + clarify (r001) dropped → only r002 (200ms) remains
    assert tta["count"] == 1
    assert tta["mean_ms"] == pytest.approx(200.0)


def test_time_to_artifact_none_when_no_plan_rows(tmp_path: Path):
    out = _output(outcome="error", artifact=None, latency_ms=50.0)
    sb = score_corpus([out], [_row()], {}, "cheap", tmp_path)
    assert sb["time_to_artifact_ms"] is None


def test_per_tag_time_to_artifact(tmp_path: Path):
    rows = [
        _row(id="r000", tags=["convert"]),
        _row(id="r001", tags=["convert"]),
        _row(id="r002", tags=["convert"]),
        _row(id="r003", tags=["platform"]),
        _row(id="r004", tags=["platform"]),
    ]
    outputs = [
        _output(id="r000", latency_ms=999.0),  # warmup, skipped overall
        _output(id="r001", latency_ms=150.0),
        _output(id="r002", latency_ms=250.0),
        _output(id="r003", latency_ms=300.0),
        _output(id="r004", latency_ms=400.0),
    ]
    sb = score_corpus(outputs, rows, {}, "cheap", tmp_path)
    convert_tta = sb["by_tag"]["convert"]["time_to_artifact_ms"]
    platform_tta = sb["by_tag"]["platform"]["time_to_artifact_ms"]
    # r000 is warmup, so 'convert' tag sees only r001+r002 → mean=200
    assert convert_tta["count"] == 2
    assert convert_tta["mean_ms"] == pytest.approx(200.0)
    assert platform_tta["count"] == 2
    assert platform_tta["mean_ms"] == pytest.approx(350.0)


def test_first_row_marked_is_warmup(tmp_path: Path):
    rows = [_row(id=f"r{i:03d}") for i in range(2)]
    outputs = [_output(id=f"r{i:03d}") for i in range(2)]
    sb = score_corpus(outputs, rows, {}, "cheap", tmp_path)
    assert sb["rows"][0].get("is_warmup") is True
    assert not sb["rows"][1].get("is_warmup")
