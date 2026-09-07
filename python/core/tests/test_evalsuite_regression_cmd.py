"""Unit tests for the per-skill `evalsuite regression` command (`cmd_regression`).

Audit finding F6 (docs/audits/2026-09-07-core-principles-and-rtx5080.md): with no
``--current`` (or a nonexistent one), the command silently compared the snapshot to
itself and always printed "No regressions ... OK". `diff_snapshots` was also fail-open
on a verifier/population mismatch and on a metric missing from *current* alone. These
tests pin the fail-closed behavior; `just eval-regression <skill>` is a no-op gate
until they pass.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from knaif.evalsuite import cli
from knaif.evalsuite.snapshot import diff_snapshots


def _snapshot(outcome=0.9, total=10, verifier="cheap") -> dict:
    return {
        "verifier": verifier,
        "total": total,
        "outcome_accuracy": outcome,
        "avg_knaif_score": 0.9,
        "intent_metrics": {"tool_accuracy": 0.9, "schema_validity": 1.0},
    }


def _reg_args(skill="alpha", current=None, threshold=0.02) -> argparse.Namespace:
    return argparse.Namespace(skill=skill, current=current, threshold=threshold)


def _write(path: Path, snap: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap), encoding="utf-8")


def _patch_snapshot(monkeypatch, tmp_path: Path, snap: dict) -> Path:
    snap_path = tmp_path / "snapshot.json"
    _write(snap_path, snap)
    monkeypatch.setattr(cli, "_snapshot_path", lambda skill: snap_path)
    return snap_path


# ── cmd_regression: fail-closed on missing/absent --current ───────────────────


def test_regression_requires_current(monkeypatch, tmp_path):
    _patch_snapshot(monkeypatch, tmp_path, _snapshot())

    with pytest.raises(SystemExit) as exc:
        cli.cmd_regression(_reg_args(current=None))
    assert exc.value.code != 0


def test_regression_current_missing_file_fails(monkeypatch, tmp_path):
    _patch_snapshot(monkeypatch, tmp_path, _snapshot())
    missing = tmp_path / "does-not-exist.json"

    with pytest.raises(SystemExit) as exc:
        cli.cmd_regression(_reg_args(current=str(missing)))
    assert exc.value.code != 0


# ── cmd_regression: happy path still works with a real --current ──────────────


def test_regression_clean_pass(monkeypatch, tmp_path, capsys):
    _patch_snapshot(monkeypatch, tmp_path, _snapshot(outcome=0.90))
    current_path = tmp_path / "current.json"
    _write(current_path, _snapshot(outcome=0.91))

    cli.cmd_regression(_reg_args(current=str(current_path)))  # must not raise
    assert "OK" in capsys.readouterr().out


def test_regression_real_regression_detected(monkeypatch, tmp_path):
    _patch_snapshot(monkeypatch, tmp_path, _snapshot(outcome=0.90))
    current_path = tmp_path / "current.json"
    _write(current_path, _snapshot(outcome=0.50))

    with pytest.raises(SystemExit) as exc:
        cli.cmd_regression(_reg_args(current=str(current_path)))
    assert exc.value.code == 1


# ── cmd_regression: not fooled by a same-scored but incompatible current ──────


def test_regression_verifier_mismatch_fails(monkeypatch, tmp_path):
    """F6's reproduction: success/847 vs cheap/1, identical score, must not pass."""
    _patch_snapshot(monkeypatch, tmp_path, _snapshot(outcome=1.0, total=847, verifier="success"))
    current_path = tmp_path / "current.json"
    _write(current_path, _snapshot(outcome=1.0, total=1, verifier="cheap"))

    with pytest.raises(SystemExit) as exc:
        cli.cmd_regression(_reg_args(current=str(current_path)))
    assert exc.value.code != 0


def test_regression_population_mismatch_fails(monkeypatch, tmp_path):
    _patch_snapshot(monkeypatch, tmp_path, _snapshot(outcome=1.0, total=847, verifier="success"))
    current_path = tmp_path / "current.json"
    _write(current_path, _snapshot(outcome=1.0, total=1, verifier="success"))

    with pytest.raises(SystemExit) as exc:
        cli.cmd_regression(_reg_args(current=str(current_path)))
    assert exc.value.code != 0


def test_regression_missing_metric_in_current_fails(monkeypatch, tmp_path):
    """F6's other reproduction: baseline has outcome_accuracy, current is `{}`."""
    _patch_snapshot(monkeypatch, tmp_path, _snapshot())
    current_path = tmp_path / "current.json"
    _write(current_path, {})

    with pytest.raises(SystemExit) as exc:
        cli.cmd_regression(_reg_args(current=str(current_path)))
    assert exc.value.code != 0


# ── diff_snapshots: unit-level coverage of the same fail-closed rules ─────────


def test_diff_snapshots_raises_on_verifier_mismatch():
    baseline = _snapshot(total=847, verifier="success")
    current = _snapshot(total=1, verifier="cheap")
    with pytest.raises(ValueError, match="[Vv]erifier"):
        diff_snapshots(baseline, current)


def test_diff_snapshots_raises_on_population_mismatch():
    baseline = _snapshot(total=847, verifier="success")
    current = _snapshot(total=1, verifier="success")
    with pytest.raises(ValueError, match="[Pp]opulation|total"):
        diff_snapshots(baseline, current)


def test_diff_snapshots_raises_when_current_drops_a_metric():
    baseline = _snapshot()
    with pytest.raises(ValueError):
        diff_snapshots(baseline, {})


def test_diff_snapshots_still_works_when_compatible():
    baseline = _snapshot(outcome=0.90)
    current = _snapshot(outcome=0.50)
    diff = diff_snapshots(baseline, current)
    assert diff["regressions"]
    assert not diff["passed"]
