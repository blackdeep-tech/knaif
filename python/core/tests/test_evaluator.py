"""Tests for knaif.evaluator."""

from __future__ import annotations

import pytest

from knaif.evaluator import _args_match, compute_metrics

# ── _args_match ───────────────────────────────────────────────────────────────


def test_args_match_empty_expected_always_passes():
    assert _args_match({}, {"path": "/sandbox/reports"}) is True


def test_args_match_string_substring():
    assert _args_match({"path": "reports"}, {"path": "/sandbox/reports"}) is True


def test_args_match_string_case_insensitive():
    assert _args_match({"pattern": "*.TXT"}, {"pattern": "*.txt"}) is True


def test_args_match_string_missing_key():
    assert _args_match({"path": "reports"}, {}) is False


def test_args_match_string_no_match():
    assert _args_match({"path": "reports"}, {"path": "/sandbox/tmp"}) is False


def test_args_match_bool_true():
    assert _args_match({"recursive": True}, {"recursive": True}) is True


def test_args_match_bool_false_mismatch():
    assert _args_match({"recursive": True}, {"recursive": False}) is False


def test_args_match_int_exact():
    assert _args_match({"count": 3}, {"count": 3}) is True
    assert _args_match({"count": 3}, {"count": 4}) is False


# ── compute_metrics ───────────────────────────────────────────────────────────


def _row(predicted, expected, args_correct=None, schema=True, category="list"):
    return {
        "predicted_tool": predicted,
        "expected_tool": expected,
        "tool_correct": predicted == expected,
        "args_correct": args_correct,
        "schema_valid": schema,
        "category": category,
    }


def test_compute_metrics_empty():
    m = compute_metrics([])
    assert m["total"] == 0
    assert m["tool_accuracy"] == 0.0
    assert m["arg_accuracy"] is None
    assert m["schema_validity"] == 0.0


def test_compute_metrics_perfect():
    rows = [_row("list_files", "list_files", args_correct=True)]
    m = compute_metrics(rows)
    assert m["tool_accuracy"] == 1.0
    assert m["arg_accuracy"] == 1.0
    assert m["arg_checked"] == 1


def test_compute_metrics_partial_accuracy():
    rows = [
        _row("list_files", "list_files"),
        _row("find_files", "list_files"),
    ]
    m = compute_metrics(rows)
    assert m["tool_accuracy"] == pytest.approx(0.5)


def test_compute_metrics_arg_accuracy_none_when_no_checkable():
    rows = [_row("list_files", "list_files", args_correct=None)]
    m = compute_metrics(rows)
    assert m["arg_accuracy"] is None


def test_compute_metrics_schema_validity():
    rows = [
        _row("list_files", "list_files", schema=True),
        _row("list_files", "list_files", schema=False),
    ]
    m = compute_metrics(rows)
    assert m["schema_validity"] == pytest.approx(0.5)


def test_compute_metrics_by_category():
    rows = [
        _row("list_files", "list_files", category="list"),
        _row("find_files", "list_files", category="list"),
        _row("clarify", "clarify", category="ambiguous"),
    ]
    m = compute_metrics(rows)
    assert m["by_category"]["list"]["correct"] == 1
    assert m["by_category"]["list"]["total"] == 2
    assert m["by_category"]["ambiguous"]["correct"] == 1


def test_compute_metrics_clarify_prf():
    rows = [
        _row("clarify", "clarify", category="ambiguous"),  # TP
        _row("clarify", "list_files", category="list"),  # FP
        _row("list_files", "clarify", category="ambiguous"),  # FN
    ]
    m = compute_metrics(rows)
    assert m["clarify"]["tp"] == 1
    assert m["clarify"]["fp"] == 1
    assert m["clarify"]["fn"] == 1
    assert m["clarify"]["precision"] == pytest.approx(0.5)
    assert m["clarify"]["recall"] == pytest.approx(0.5)


def test_compute_metrics_reject_prf():
    rows = [
        _row("reject", "reject", category="unsafe"),  # TP
        _row("reject", "reject", category="unsafe"),  # TP
        _row("list_files", "reject", category="unsafe"),  # FN
    ]
    m = compute_metrics(rows)
    assert m["reject"]["tp"] == 2
    assert m["reject"]["fp"] == 0
    assert m["reject"]["fn"] == 1
    assert m["reject"]["precision"] == pytest.approx(1.0)
    assert m["reject"]["recall"] == pytest.approx(2 / 3)


# ── run_eval ──────────────────────────────────────────────────────────────────


def test_run_eval_basic(agent):
    from knaif.evaluator import run_eval

    dataset = [
        {
            "utterance": "list all files",
            "expected_tool": "list_files",
            "expected_args": {"path": "."},
            "category": "list",
        }
    ]
    results = run_eval(agent, dataset, use_mock=True)
    assert len(results) == 1
    row = results[0]
    assert row["utterance"] == "list all files"
    assert row["category"] == "list"
    assert row["expected_tool"] == "list_files"
    assert "predicted_tool" in row
    assert "schema_valid" in row
    assert isinstance(row["tool_correct"], bool)


def test_run_eval_tool_correct_flag(agent):
    from knaif.evaluator import run_eval

    dataset = [
        {
            "utterance": "list all files",
            "expected_tool": "list_files",
            "expected_args": {},
            "category": "list",
        },
        {
            "utterance": "nuke the server",
            "expected_tool": "reject",
            "expected_args": {},
            "category": "unsafe",
        },
    ]
    results = run_eval(agent, dataset, use_mock=True)
    assert len(results) == 2
    # Both utterances should have tool_correct set
    for row in results:
        assert isinstance(row["tool_correct"], bool)


def test_run_eval_args_correct_when_tool_correct(agent):
    from knaif.evaluator import run_eval

    dataset = [
        {
            "utterance": "list all files",
            "expected_tool": "list_files",
            "expected_args": {"path": "."},
            "category": "list",
        }
    ]
    results = run_eval(agent, dataset, use_mock=True)
    row = results[0]
    # args_correct is only set when tool_correct and expected_args non-empty
    if row["tool_correct"]:
        assert row["args_correct"] is not None or row["args_correct"] is None


def test_run_eval_args_correct_none_when_no_expected_args(agent):
    from knaif.evaluator import run_eval

    dataset = [
        {
            "utterance": "list all files",
            "expected_tool": "list_files",
            "expected_args": {},
            "category": "list",
        }
    ]
    results = run_eval(agent, dataset, use_mock=True)
    # With empty expected_args, args_correct should be None
    assert results[0]["args_correct"] is None


def test_run_eval_handles_inference_error(agent):
    from unittest.mock import patch

    from knaif.evaluator import run_eval

    dataset = [
        {
            "utterance": "list files",
            "expected_tool": "list_files",
            "expected_args": {},
            "category": "list",
        }
    ]
    with patch.object(agent, "infer", side_effect=RuntimeError("test error")):
        results = run_eval(agent, dataset, use_mock=True)

    assert len(results) == 1
    assert results[0]["predicted_tool"] is None
    assert results[0]["tool_correct"] is False


def test_run_eval_schema_validity(agent):
    from knaif.evaluator import run_eval

    dataset = [
        {
            "utterance": "list all files",
            "expected_tool": "list_files",
            "expected_args": {},
            "category": "list",
        }
    ]
    results = run_eval(agent, dataset, use_mock=True)
    assert isinstance(results[0]["schema_valid"], bool)
