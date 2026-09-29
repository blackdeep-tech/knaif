"""Per-model snapshots: each model is gated against its own accepted baseline (release R2).

One `data/eval_snapshot.json` per skill meant locking the 1.7B's baseline replaced the 4B's, and
`check_native_acceptance` refused any run whose model differed from the one snapshot. The default
file keeps the model it holds (the 4B line); any other model's baseline is
`data/eval_snapshot.<model>.json`, chosen by the model a run names.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from knaif.evalsuite import cli
from knaif.evalsuite.snapshot import snapshot_path

BIG = "knaif-demo-4b-v2"
SMALL = "knaif-demo-1.7b-v2"


def _snap(model: str, accuracy: float) -> dict:
    return {
        "verifier": "success",
        "scoring_policy": 3,
        "total": 100,
        "outcome_accuracy": accuracy,
        "avg_knaif_score": accuracy,
        "backend": f"eval-{model}",
        "backend_public_name": model,
        "by_tag": {},
    }


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    data = tmp_path / "skills" / "demo" / "data"
    data.mkdir(parents=True)
    (data / "eval_snapshot.json").write_text(json.dumps(_snap(BIG, 0.9)), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_default_file_serves_its_own_model_and_no_model(repo: Path) -> None:
    default = Path("skills/demo/data/eval_snapshot.json")
    assert snapshot_path("demo") == default
    assert snapshot_path("demo", BIG) == default


def test_another_model_gets_its_own_file(repo: Path) -> None:
    assert snapshot_path("demo", SMALL) == Path(f"skills/demo/data/eval_snapshot.{SMALL}.json")


def test_a_skill_with_no_snapshot_yet_starts_with_the_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert snapshot_path("demo", SMALL) == Path("skills/demo/data/eval_snapshot.json")


def test_regression_compares_a_run_with_its_own_model_s_baseline(repo: Path) -> None:
    """A 1.7B run at the 1.7B's level is not a regression against the 4B's snapshot."""
    small = Path(f"skills/demo/data/eval_snapshot.{SMALL}.json")
    small.write_text(json.dumps(_snap(SMALL, 0.8)), encoding="utf-8")
    current = repo / "board.json"
    current.write_text(json.dumps(_snap(SMALL, 0.8)), encoding="utf-8")

    cli.cmd_regression(
        argparse.Namespace(skill="demo", current=str(current), threshold=0.02)
    )  # must not sys.exit(1)


def test_regression_without_the_model_s_baseline_fails_closed(repo: Path) -> None:
    current = repo / "board.json"
    current.write_text(json.dumps(_snap(SMALL, 0.8)), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_regression(argparse.Namespace(skill="demo", current=str(current), threshold=0.02))
    assert SMALL in str(exc.value.code)
