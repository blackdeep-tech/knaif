"""Save and diff scoreboard snapshots for regression detection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_snapshot(scoreboard: dict[str, Any], path: Path | str) -> None:
    """Write a snapshot JSON — same format as the scoreboard, without per-row detail."""
    path = Path(path)
    snapshot = {k: v for k, v in scoreboard.items() if k != "rows"}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def load_snapshot(path: Path | str) -> dict[str, Any]:
    """Load a previously saved snapshot."""
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)  # type: ignore[no-any-return]


def diff_snapshots(
    baseline: dict[str, Any],
    current: dict[str, Any],
    threshold: float = 0.02,
) -> dict[str, Any]:
    """
    Compare *current* scoreboard to *baseline* snapshot.

    Returns a dict with regressions and improvements.
    A regression is a metric that dropped by more than *threshold*.
    """
    _MetricKey = str | tuple[str, str]
    metrics: list[_MetricKey] = [
        "outcome_accuracy",
        "avg_knaif_score",
        "avg_baseline_score",
        ("intent_metrics", "tool_accuracy"),
        ("intent_metrics", "arg_accuracy"),
        ("intent_metrics", "schema_validity"),
    ]

    def _get(d: dict[str, Any], key: _MetricKey) -> float | None:
        if isinstance(key, tuple):
            sub = d.get(key[0]) or {}
            return sub.get(key[1])
        return d.get(key)

    def _label(key: _MetricKey) -> str:
        if isinstance(key, tuple):
            return f"{key[0]}.{key[1]}"
        return str(key)

    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []

    for key in metrics:
        b_val = _get(baseline, key)
        c_val = _get(current, key)
        if b_val is None or c_val is None:
            continue
        delta = c_val - b_val
        entry = {
            "metric": _label(key),
            "baseline": b_val,
            "current": c_val,
            "delta": delta,
        }
        if delta < -threshold:
            regressions.append(entry)
        elif delta > threshold:
            improvements.append(entry)

    return {
        "regressions": regressions,
        "improvements": improvements,
        "threshold": threshold,
        "passed": len(regressions) == 0,
    }
