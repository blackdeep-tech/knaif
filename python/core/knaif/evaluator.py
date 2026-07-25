"""Evaluation harness: run_eval and compute_metrics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import CommandAgent


def _args_match(expected: dict[str, Any], predicted: dict[str, Any]) -> bool:
    """
    Partial match: every key in *expected* must appear in *predicted* with a
    compatible value.

    - bool / int: exact equality.
    - str: expected value must appear as a case-insensitive substring of the
      predicted value (allows full paths to match partial references).
    - Empty *expected* always passes.
    """
    for key, exp_val in expected.items():
        pred_val = predicted.get(key)
        if pred_val is None:
            return False
        if isinstance(exp_val, bool):
            if bool(pred_val) != exp_val:
                return False
        elif isinstance(exp_val, str):
            if exp_val.lower() not in str(pred_val).lower():
                return False
        else:
            if pred_val != exp_val:
                return False
    return True


def run_eval(
    agent: CommandAgent,
    dataset: list[dict[str, Any]],
    *,
    use_mock: bool = False,
) -> list[dict[str, Any]]:
    """
    Run every utterance in *dataset* through the agent pipeline and record
    predicted vs expected outcomes.
    """
    eval_results: list[dict[str, Any]] = []

    for item in dataset:
        try:
            plan_payload = agent.infer(item["utterance"], use_mock=use_mock)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ inference error for '{item['utterance']}': {exc}")
            plan_payload = {"plan": []}

        pred_tool = plan_payload["plan"][0]["tool"] if plan_payload.get("plan") else None
        pred_args = plan_payload["plan"][0].get("args", {}) if plan_payload.get("plan") else {}
        expected_args: dict = item.get("expected_args", {})
        tool_correct = pred_tool == item["expected_tool"]
        # Arg accuracy is only meaningful when the tool was correctly selected
        # and there are expected args to check against.
        args_correct: bool | None = (
            _args_match(expected_args, pred_args) if (tool_correct and expected_args) else None
        )

        try:
            agent.validate_plan(plan_payload)
            schema_valid = True
        except Exception:  # noqa: BLE001
            schema_valid = False

        eval_results.append(
            {
                "utterance": item["utterance"],
                "category": item["category"],
                "expected_tool": item["expected_tool"],
                "expected_args": expected_args,
                "predicted_tool": pred_tool,
                "predicted_args": pred_args,
                "tool_correct": tool_correct,
                "args_correct": args_correct,
                "schema_valid": schema_valid,
            }
        )

    return eval_results


def compute_metrics(eval_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate eval_results into summary metrics."""
    n = len(eval_results)
    tool_acc = sum(r["tool_correct"] for r in eval_results) / n if n else 0.0
    schema_pct = sum(r["schema_valid"] for r in eval_results) / n if n else 0.0

    arg_checked = [r for r in eval_results if r["args_correct"] is not None]
    arg_acc: float | None = (
        sum(r["args_correct"] for r in arg_checked) / len(arg_checked) if arg_checked else None
    )

    by_cat: dict[str, dict] = {}
    for r in eval_results:
        c = r["category"]
        by_cat.setdefault(c, {"correct": 0, "total": 0})
        by_cat[c]["total"] += 1
        if r["tool_correct"]:
            by_cat[c]["correct"] += 1

    def _prf(label: str) -> dict:
        tp = sum(
            1 for r in eval_results if r["predicted_tool"] == label and r["expected_tool"] == label
        )
        fp = sum(
            1 for r in eval_results if r["predicted_tool"] == label and r["expected_tool"] != label
        )
        fn = sum(
            1 for r in eval_results if r["predicted_tool"] != label and r["expected_tool"] == label
        )
        p = tp / (tp + fp) if (tp + fp) else 0.0
        rc = tp / (tp + fn) if (tp + fn) else 0.0
        return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": rc}

    return {
        "total": n,
        "tool_accuracy": tool_acc,
        "arg_accuracy": arg_acc,
        "arg_checked": len(arg_checked),
        "schema_validity": schema_pct,
        "by_category": by_cat,
        "clarify": _prf("clarify"),
        "reject": _prf("reject"),
    }
