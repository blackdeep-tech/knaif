"""Tests for evalsuite corpus.py: round-trip JSONL, schema validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.corpus import CorpusRow, load_corpus, save_corpus


def _row(**kwargs) -> CorpusRow:
    defaults: dict = {
        "id": "ffmpeg_v2_001",
        "utterances": ["Convert this MKV to MP4.", "Make it an mp4", "Save as mp4"],
        "expected_outcome": "plan",
        "fixture": "clip.mp4",
        "baseline": {
            "command": "ffmpeg -y -i clip.mkv -c:v libx264 -c:a aac out.mp4",
            "output": "baselines/ffmpeg_v2_001.mp4",
            "validated_by": "maintainer",
            "validated_at": "2026-05-24",
        },
        "expected_tool": "convert_video",
        "tolerances": {"duration_s": 0.5, "size_pct": 20},
        "tags": ["convert", "container"],
    }
    defaults.update(kwargs)
    return CorpusRow(**defaults)


# ── CorpusRow construction ──────────────────────────────────────────────────


def test_valid_plan_outcome():
    row = _row(expected_outcome="plan")
    assert row.expected_outcome == "plan"


def test_valid_clarify_outcome():
    row = CorpusRow(id="x", utterances=["make it better"], expected_outcome="clarify")
    assert row.expected_outcome == "clarify"


def test_valid_reject_outcome():
    row = CorpusRow(id="x", utterances=["delete everything"], expected_outcome="reject")
    assert row.expected_outcome == "reject"


def test_invalid_outcome_raises():
    with pytest.raises(ValueError, match="expected_outcome"):
        CorpusRow(id="x", utterances=["y"], expected_outcome="invalid")


def test_empty_utterances_raises():
    with pytest.raises(ValueError, match="utterances"):
        CorpusRow(id="x", utterances=[], expected_outcome="plan")


def test_defaults_are_empty_or_none():
    row = CorpusRow(id="x", utterances=["y"], expected_outcome="plan")
    assert row.fixture is None
    assert row.baseline is None
    assert row.expected_tool is None
    assert row.tolerances == {}
    assert row.tags == []


def test_multiple_utterances_stored():
    row = _row()
    assert len(row.utterances) == 3
    assert row.utterances[0] == "Convert this MKV to MP4."


# ── to_dict / from_dict round-trip ─────────────────────────────────────────


def test_to_dict_round_trip():
    row = _row()
    recovered = CorpusRow.from_dict(row.to_dict())
    assert recovered.id == row.id
    assert recovered.utterances == row.utterances
    assert recovered.expected_outcome == row.expected_outcome
    assert recovered.fixture == row.fixture
    assert recovered.baseline == row.baseline
    assert recovered.expected_tool == row.expected_tool
    assert recovered.tolerances == row.tolerances
    assert recovered.tags == row.tags


def test_from_dict_null_optional_fields():
    data = {
        "id": "x001",
        "utterances": ["something"],
        "expected_outcome": "clarify",
    }
    row = CorpusRow.from_dict(data)
    assert row.fixture is None
    assert row.baseline is None
    assert row.expected_tool is None
    assert row.tolerances == {}
    assert row.tags == []


# ── save_corpus / load_corpus ───────────────────────────────────────────────


def test_single_row_round_trip(tmp_path: Path):
    row = _row()
    path = tmp_path / "corpus.jsonl"
    save_corpus([row], path)
    loaded = load_corpus(path)
    assert len(loaded) == 1
    assert loaded[0].id == row.id
    assert loaded[0].utterances == row.utterances
    assert loaded[0].baseline == row.baseline


def test_multiple_rows_preserve_order(tmp_path: Path):
    rows = [_row(id=f"ffmpeg_v2_{i:03d}", utterances=[f"utterance {i}"]) for i in range(5)]
    path = tmp_path / "corpus.jsonl"
    save_corpus(rows, path)
    loaded = load_corpus(path)
    assert [r.id for r in loaded] == [f"ffmpeg_v2_{i:03d}" for i in range(5)]


def test_empty_file_returns_empty_list(tmp_path: Path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert load_corpus(path) == []


def test_comment_lines_ignored(tmp_path: Path):
    row = _row()
    path = tmp_path / "corpus.jsonl"
    path.write_text("# This is a comment\n" + json.dumps(row.to_dict()) + "\n", encoding="utf-8")
    loaded = load_corpus(path)
    assert len(loaded) == 1


def test_blank_lines_ignored(tmp_path: Path):
    row = _row()
    path = tmp_path / "corpus.jsonl"
    path.write_text("\n\n" + json.dumps(row.to_dict()) + "\n\n", encoding="utf-8")
    loaded = load_corpus(path)
    assert len(loaded) == 1


def test_missing_required_field_raises(tmp_path: Path):
    path = tmp_path / "bad.jsonl"
    # Missing 'utterances'
    path.write_text(json.dumps({"id": "x", "expected_outcome": "plan"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_corpus(path)


def test_invalid_json_raises(tmp_path: Path):
    path = tmp_path / "bad.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON parse error"):
        load_corpus(path)


def test_invalid_outcome_in_file_raises(tmp_path: Path):
    data = {"id": "x", "utterances": ["y"], "expected_outcome": "bad"}
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Schema error"):
        load_corpus(path)


def test_save_creates_parent_dirs(tmp_path: Path):
    path = tmp_path / "nested" / "dir" / "corpus.jsonl"
    save_corpus([_row()], path)
    assert path.exists()


# ── success_criteria ───────────────────────────────────────────────────────


def test_success_criteria_default_is_empty_dict():
    row = CorpusRow(id="x", utterances=["y"], expected_outcome="plan")
    assert row.success_criteria == {}


def test_success_criteria_stored_in_constructor():
    criteria = {"container": "mp4", "video_codec": "h264"}
    row = CorpusRow(
        id="x",
        utterances=["convert to mp4"],
        expected_outcome="plan",
        success_criteria=criteria,
    )
    assert row.success_criteria == criteria


def test_success_criteria_round_trip_via_dict():
    criteria = {"container": "mp4", "video_codec": "h264", "audio_codec": "aac"}
    row = CorpusRow(
        id="sc_001",
        utterances=["make it mp4"],
        expected_outcome="plan",
        success_criteria=criteria,
    )
    recovered = CorpusRow.from_dict(row.to_dict())
    assert recovered.success_criteria == criteria


def test_success_criteria_round_trip_via_file(tmp_path: Path):
    criteria = {"container": "webm", "video_codec": "vp9", "no_audio": True}
    row = CorpusRow(
        id="sc_002",
        utterances=["to webm"],
        expected_outcome="plan",
        success_criteria=criteria,
    )
    path = tmp_path / "corpus.jsonl"
    save_corpus([row], path)
    loaded = load_corpus(path)
    assert loaded[0].success_criteria == criteria


def test_legacy_row_without_success_criteria_loads(tmp_path: Path):
    data = {"id": "legacy_001", "utterances": ["do something"], "expected_outcome": "plan"}
    path = tmp_path / "corpus.jsonl"
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    loaded = load_corpus(path)
    assert loaded[0].success_criteria == {}


def test_success_criteria_not_in_row_to_dict_omits_when_empty():
    row = CorpusRow(id="x", utterances=["y"], expected_outcome="plan")
    d = row.to_dict()
    assert "success_criteria" not in d or d.get("success_criteria") == {}


# ── grade field (routing-only grading) ───────────────────────────────────────


def test_grade_defaults_to_full():
    row = CorpusRow(id="x", utterances=["y"], expected_outcome="plan")
    assert row.grade == "full"


def test_grade_accepts_routing():
    row = CorpusRow(id="x", utterances=["y"], expected_outcome="plan", grade="routing")
    assert row.grade == "routing"


def test_grade_invalid_value_raises():
    with pytest.raises(ValueError, match="grade"):
        CorpusRow(id="x", utterances=["y"], expected_outcome="plan", grade="bogus")


def test_grade_round_trip_via_dict():
    row = CorpusRow(id="x", utterances=["y"], expected_outcome="plan", grade="routing")
    recovered = CorpusRow.from_dict(row.to_dict())
    assert recovered.grade == "routing"


def test_grade_round_trip_via_file(tmp_path: Path):
    row = CorpusRow(
        id="g_001", utterances=["all my mp4s"], expected_outcome="plan", grade="routing"
    )
    path = tmp_path / "corpus.jsonl"
    save_corpus([row], path)
    loaded = load_corpus(path)
    assert loaded[0].grade == "routing"


def test_grade_omitted_from_dict_when_full():
    row = CorpusRow(id="x", utterances=["y"], expected_outcome="plan")
    assert "grade" not in row.to_dict()


def test_legacy_row_without_grade_loads_as_full(tmp_path: Path):
    data = {"id": "legacy_g", "utterances": ["do something"], "expected_outcome": "plan"}
    path = tmp_path / "corpus.jsonl"
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    loaded = load_corpus(path)
    assert loaded[0].grade == "full"


# ── multi-output rows (outputs / expected_tools) ─────────────────────────────


def _multi_output_kwargs() -> dict:
    return {
        "id": "ffmpeg_multi_001",
        "utterances": ["trim clip.mp4 to 3-5s, save it, then extract audio as mp3"],
        "expected_outcome": "plan",
        "fixture": "clip.mp4",
        "expected_tool": "trim_video",
        "expected_tools": ["trim_video", "extract_audio"],
        "outputs": [
            {
                "command": "ffmpeg -y -ss 3 -to 5 -i clip.mp4 -c:v libx264 -c:a aac clip_trimmed.mp4",
                "criteria": {"container": "mp4", "video_codec": "h264"},
                "tolerances": {"duration_s": 1.0},
            },
            {
                "command": "ffmpeg -y -i clip_trimmed.mp4 -vn -c:a libmp3lame clip_trimmed.mp3",
                "criteria": {"audio_codec": "mp3"},
            },
        ],
        "tags": ["complex", "trim", "extract_audio", "multi_output"],
    }


def test_default_outputs_and_expected_tools_are_none():
    row = CorpusRow(id="x", utterances=["y"], expected_outcome="plan")
    assert row.outputs is None
    assert row.expected_tools is None


def test_outputs_stored_in_constructor():
    row = CorpusRow(**_multi_output_kwargs())
    assert len(row.outputs) == 2
    assert row.outputs[0]["command"].startswith("ffmpeg")
    assert row.expected_tools == ["trim_video", "extract_audio"]


def test_outputs_round_trip_via_dict():
    row = CorpusRow(**_multi_output_kwargs())
    recovered = CorpusRow.from_dict(row.to_dict())
    assert recovered.outputs == row.outputs
    assert recovered.expected_tools == row.expected_tools


def test_outputs_round_trip_via_file(tmp_path: Path):
    row = CorpusRow(**_multi_output_kwargs())
    path = tmp_path / "corpus.jsonl"
    save_corpus([row], path)
    loaded = load_corpus(path)
    assert loaded[0].outputs == row.outputs
    assert loaded[0].expected_tools == row.expected_tools


def test_single_output_row_omits_multi_fields_from_dict():
    row = _row()
    d = row.to_dict()
    assert "outputs" not in d
    assert "expected_tools" not in d


def test_legacy_row_without_multi_fields_loads(tmp_path: Path):
    data = {"id": "legacy_002", "utterances": ["do something"], "expected_outcome": "plan"}
    path = tmp_path / "corpus.jsonl"
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    loaded = load_corpus(path)
    assert loaded[0].outputs is None
    assert loaded[0].expected_tools is None


def test_empty_outputs_list_raises():
    with pytest.raises(ValueError, match="outputs"):
        CorpusRow(id="x", utterances=["y"], expected_outcome="plan", outputs=[])


def test_output_entry_missing_command_raises():
    with pytest.raises(ValueError, match="command"):
        CorpusRow(
            id="x",
            utterances=["y"],
            expected_outcome="plan",
            outputs=[{"criteria": {"container": "mp4"}}],
        )


def test_multi_output_must_be_plan_outcome():
    with pytest.raises(ValueError, match="outputs"):
        CorpusRow(
            id="x",
            utterances=["y"],
            expected_outcome="clarify",
            outputs=[{"command": "ffmpeg -i a.mp4 b.mp4"}],
        )
