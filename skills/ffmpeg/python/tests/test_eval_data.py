"""Smoke tests for the ffmpeg eval corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

_EVAL_JSONL = Path(__file__).parents[2] / "data" / "eval.jsonl"

_VALID_FIXTURES = frozenset(
    {
        "clip.mp4",
        "clip2.mp4",
        "clip_no_audio.mp4",
        "clip_4k.mp4",
        "clip_ctr.mp4",
        "clip.mov",
        "audio.mp3",
    }
)

_VALID_TOOLS = frozenset(
    {
        "prepare_for_platform",
        "compress_video",
        "convert_video",
        "resize_video",
        "trim_video",
        "extract_audio",
        "create_thumbnail",
        "reverse_video",
        "strip_audio",
        "extract_frame",
        "adjust_speed",
        "concat_video",
        "rotate_video",
        "adjust_volume",
        "clarify",
    }
)


@pytest.fixture(scope="module")
def corpus():
    from knaif.evalsuite.corpus import load_corpus

    return load_corpus(_EVAL_JSONL)


def test_corpus_loads_without_error(corpus):
    assert corpus


def test_corpus_has_at_least_70_rows(corpus):
    assert len(corpus) >= 70, f"Expected ≥ 70 corpus rows, got {len(corpus)}"


def test_all_ids_unique(corpus):
    ids = [r.id for r in corpus]
    assert len(ids) == len(set(ids)), "Duplicate IDs in corpus"


def test_plan_rows_have_expected_tool(corpus):
    missing = [r.id for r in corpus if r.expected_outcome == "plan" and not r.expected_tool]
    assert not missing, f"plan rows without expected_tool: {missing}"


def test_expected_tools_are_known(corpus):
    unknown = [
        (r.id, r.expected_tool)
        for r in corpus
        if r.expected_tool and r.expected_tool not in _VALID_TOOLS
    ]
    assert not unknown, f"Unknown expected_tool values: {unknown}"


def test_fixtures_are_known(corpus):
    unknown = [(r.id, r.fixture) for r in corpus if r.fixture and r.fixture not in _VALID_FIXTURES]
    assert not unknown, f"Unknown fixture names: {unknown}"


def test_all_rows_have_nonempty_utterance(corpus):
    empty = [r.id for r in corpus if not r.utterances or not r.utterances[0].strip()]
    assert not empty, f"Rows with empty utterance: {empty}"


# ── multi-output rows ────────────────────────────────────────────────────────


def _multi_output_rows(corpus):
    return [r for r in corpus if getattr(r, "outputs", None)]


def test_multi_output_rows_are_plan_outcome(corpus):
    bad = [r.id for r in _multi_output_rows(corpus) if r.expected_outcome != "plan"]
    assert not bad, f"multi-output rows must be plan outcome: {bad}"


def test_multi_output_each_output_has_command(corpus):
    bad = [
        r.id
        for r in _multi_output_rows(corpus)
        if not all(isinstance(o.get("command"), str) and o["command"].strip() for o in r.outputs)
    ]
    assert not bad, f"multi-output rows with an empty/missing command: {bad}"


def test_multi_output_each_output_has_criteria(corpus):
    bad = [
        r.id for r in _multi_output_rows(corpus) if not all(o.get("criteria") for o in r.outputs)
    ]
    assert not bad, f"multi-output rows with an output missing criteria: {bad}"


def test_multi_output_expected_tools_align(corpus):
    """expected_tools must be set and its first element must equal expected_tool."""
    bad = [
        r.id
        for r in _multi_output_rows(corpus)
        if not r.expected_tools or r.expected_tools[0] != r.expected_tool
    ]
    assert not bad, f"multi-output rows with misaligned expected_tools/expected_tool: {bad}"


def test_multi_output_expected_tools_are_known(corpus):
    unknown = [
        (r.id, t)
        for r in _multi_output_rows(corpus)
        for t in (r.expected_tools or [])
        if t not in _VALID_TOOLS
    ]
    assert not unknown, f"Unknown tools in expected_tools: {unknown}"
