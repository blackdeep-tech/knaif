"""Tests for scripts/flip_rate.py (docs/plans/2026-09-23-inference-config-parity.md T2).

The flip rate decides whether the existing evaluations stand, so the comparison itself has to be
right: a false flip would reopen a decision for nothing, a missed one would let noise pass as a
result.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(".").resolve()
SCRIPT = ROOT / "scripts" / "flip_rate.py"


def _load():
    spec = importlib.util.spec_from_file_location("flip_rate", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # `dataclass` resolves the defining module through sys.modules.
    sys.modules["flip_rate"] = module
    spec.loader.exec_module(module)
    return module


fr = _load()


def _step(tool: str, **args) -> dict:
    return {"tool": tool, "args": args}


# ── what counts as the same decision ──────────────────────────────────────────────────────


def test_a_decision_ignores_file_arguments() -> None:
    """Core rewrites only file arguments (chain linking, source threading), and the two lanes
    rewrite differently. What the MODEL chose is everything else."""
    python = [_step("trim_video", input="clip-chained.mkv", start="2", output="t.mkv")]
    native = [_step("trim_video", input="clip.mkv", start="2", output="t.mkv")]
    assert fr.decision(python) == fr.decision(native)
    assert fr.full(python) != fr.full(native)


def test_a_different_tool_or_argument_is_a_different_decision() -> None:
    assert fr.decision([_step("extract_audio", inputs=["a.mkv"])]) != fr.decision(
        [_step("strip_audio", inputs=["a.mkv"])]
    )
    assert fr.decision([_step("resize_video", width=320, height=200)]) != fr.decision(
        [_step("resize_video", width=200, height=320)]
    )


def test_argument_order_and_number_spelling_are_not_decisions() -> None:
    a = [_step("resize_video", width=320, height=200)]
    b = [{"tool": "resize_video", "args": {"height": "200", "width": 320.0}}]
    assert fr.decision(a) == fr.decision(b)


def test_a_clarify_or_reject_is_decided_by_the_tool_not_its_wording() -> None:
    """Two runs that both ask a question agree; the question's phrasing is not a decision."""
    assert fr.decision([_step("clarify", question="Which file?")]) == fr.decision(
        [_step("clarify", question="Which file do you mean?")]
    )
    assert fr.decision([_step("clarify", question="x")]) != fr.decision(
        [_step("reject", reason="x")]
    )


# ── loading the two kinds of run ──────────────────────────────────────────────────────────


def test_an_eval_run_is_keyed_by_row_and_utterance(tmp_path: Path) -> None:
    saved = {
        "rows": [
            {
                "id": "ffmpeg_001",
                "utterance_idx": 0,
                "plan": {"plan": [_step("convert_video", inputs=["clip.mp4"], container="mkv")]},
                "actual_outcome": "plan",
                "outcome_correct": True,
                "knaif_score": 1.0,
            },
            {
                "id": "ffmpeg_001",
                "utterance_idx": 1,
                "plan": None,
                "actual_outcome": "parse_error",
                "outcome_correct": False,
                "knaif_score": 0.0,
            },
        ]
    }
    path = tmp_path / "ffmpeg_x_cheap.json"
    path.write_text(json.dumps(saved), encoding="utf-8")

    run = fr.load_run(path)

    assert set(run) == {("ffmpeg_001", 0), ("ffmpeg_001", 1)}
    assert run[("ffmpeg_001", 0)].correct is True
    assert run[("ffmpeg_001", 1)].plan == []


def test_a_native_batch_is_keyed_like_the_corpus(tmp_path: Path) -> None:
    lines = [
        {"id": "ffmpeg_001", "utterance_idx": 0, "plan": [_step("convert_video")]},
        {"id": "ffmpeg_001", "utterance_idx": 1, "plan": []},
    ]
    path = tmp_path / "native.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    run = fr.load_run(path)

    assert run[("ffmpeg_001", 0)].plan == [_step("convert_video")]
    assert run[("ffmpeg_001", 0)].correct is None  # native plans are not graded here


def test_corpus_utterances_come_out_in_eval_order(tmp_path: Path) -> None:
    corpus = tmp_path / "eval.jsonl"
    corpus.write_text(
        json.dumps({"id": "a", "utterances": ["one", "two"]})
        + "\n"
        + json.dumps({"id": "b", "utterances": ["three"]})
        + "\n",
        encoding="utf-8",
    )
    assert fr.corpus_utterances(corpus) == [("a", 0, "one"), ("a", 1, "two"), ("b", 0, "three")]


# ── the comparison ────────────────────────────────────────────────────────────────────────


def test_compare_counts_decision_full_and_outcome_flips_over_shared_keys() -> None:
    same = [_step("convert_video", inputs=["clip.mp4"], container="mkv")]
    a = {
        ("r", 0): fr.Result(plan=same, correct=True),
        ("r", 1): fr.Result(plan=[_step("extract_audio", inputs=["c.mkv"])], correct=False),
        ("r", 2): fr.Result(plan=[_step("trim_video", input="c.mkv")], correct=True),
        ("only_a", 0): fr.Result(plan=same, correct=True),
    }
    b = {
        ("r", 0): fr.Result(plan=same, correct=True),
        ("r", 1): fr.Result(plan=[_step("strip_audio", inputs=["c.mkv"])], correct=True),
        ("r", 2): fr.Result(plan=[_step("trim_video", input="c-chained.mkv")], correct=True),
        ("only_b", 0): fr.Result(plan=same, correct=True),
    }

    report = fr.compare(a, b)

    assert report.shared == 3
    assert report.missing == 2
    assert report.decision_flips == [("r", 1)]
    assert report.full_flips == [("r", 1), ("r", 2)]
    assert report.outcome_flips == [("r", 1)]
    assert report.decision_rate == 1 / 3


def test_outcome_flips_need_both_sides_graded() -> None:
    step = [_step("strip_audio")]
    a = {("r", 0): fr.Result(plan=step, correct=True)}
    b = {("r", 0): fr.Result(plan=step, correct=None)}
    assert fr.compare(a, b).outcome_flips == []
    assert fr.compare(a, b).outcome_rate is None
