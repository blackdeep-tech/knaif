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

    Raises ``ValueError`` — instead of silently reporting "no regressions" — when the
    two scoreboards aren't a valid comparison: a declared verifier or population
    (``total``) mismatch, or a metric present in *baseline* that *current* doesn't
    report at all. (Audit finding F6: a `success`/847-row baseline previously diffed
    clean against an unrelated `cheap`/1-row current, and a baseline vs. `{}` also
    passed, because missing values were silently skipped rather than treated as
    unknown/regressed.)
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

    # Comparison identity: when the baseline declares one of these, the current scoreboard must
    # declare it too and it must match. Requiring only "if both declare, they must agree" was
    # itself fail-open — a current scoreboard that simply omits `verifier`/`total` skipped the
    # guard entirely and could still be certified against a baseline that declares them.
    baseline_verifier = baseline.get("verifier")
    current_verifier = current.get("verifier")
    if baseline_verifier is not None:
        if current_verifier is None:
            raise ValueError(
                f"current scoreboard does not declare a verifier; baseline was scored with "
                f"{baseline_verifier!r}. Cannot certify no regression against an unidentified run."
            )
        if baseline_verifier != current_verifier:
            raise ValueError(
                f"Verifier mismatch: baseline was scored with {baseline_verifier!r}, "
                f"current is {current_verifier!r}. Compare scoreboards produced by the "
                "same verifier."
            )

    baseline_total = baseline.get("total")
    current_total = current.get("total")
    if baseline_total is not None:
        if current_total is None:
            raise ValueError(
                f"current scoreboard does not declare a population (`total`); baseline "
                f"declares total={baseline_total}. Cannot certify no regression against an "
                "unidentified population."
            )
        if baseline_total != current_total:
            raise ValueError(
                f"Population mismatch: baseline total={baseline_total}, "
                f"current total={current_total}. Compare scoreboards over the same "
                "evaluation population (regenerate fixtures/current before diffing)."
            )

    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    dropped: list[str] = []

    for key in metrics:
        b_val = _get(baseline, key)
        c_val = _get(current, key)
        if b_val is None:
            continue  # baseline never reported this metric — nothing to regress against
        if c_val is None:
            dropped.append(_label(key))
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

    if dropped:
        raise ValueError(
            "current scoreboard is missing metric(s) baseline reported: "
            + ", ".join(dropped)
            + ". Cannot certify no regression without them."
        )

    return {
        "regressions": regressions,
        "improvements": improvements,
        "threshold": threshold,
        "passed": len(regressions) == 0,
    }
