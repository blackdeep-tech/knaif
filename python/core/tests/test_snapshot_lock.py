"""A snapshot is the accepted bar, so it may only be locked from an executing run.

`cheap` is an iteration instrument: it grades the plan, not the files the plan produced.
A baseline locked at `cheap` therefore records a number no user can experience, and every
later regression check compares against it — so the mistake is silent and permanent until
someone re-reads the JSON.

`acceptance.py` already refuses a `cheap` scoreboard ("`cheap` may never be an acceptance
bar"), and `eval-native` refuses one at the CLI. The snapshot *writer* did not, while
`run --snapshot` defaults to `--verifier cheap` — one command away from a locked-in
fiction. These tests pin the writer closed.

See AGENTS.md (*Skill Lifecycle* → Evaluation) and
`docs/plans/2026-09-11-reject-clarify-taxonomy.md` (T9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.snapshot import load_snapshot, save_snapshot


def _scoreboard(verifier: str = "success") -> dict:
    return {
        "verifier": verifier,
        "scoring_policy": 1,
        "total": 10,
        "outcome_accuracy": 0.9,
        "avg_knaif_score": 0.95,
        "intent_metrics": {"tool_accuracy": 0.9, "schema_validity": 1.0},
        "rows": [{"id": "x", "outcome_correct": True}],
    }


def test_an_executing_scoreboard_locks(tmp_path: Path) -> None:
    path = tmp_path / "eval_snapshot.json"
    save_snapshot(_scoreboard("success"), path)

    saved = load_snapshot(path)
    assert saved["verifier"] == "success"
    assert "rows" not in saved, "per-row detail does not belong in a snapshot"


@pytest.mark.parametrize("verifier", ["output_diff", "honest"])
def test_every_executing_verifier_locks(tmp_path: Path, verifier: str) -> None:
    """`success` is the default, not the only honest instrument."""
    path = tmp_path / "eval_snapshot.json"
    save_snapshot(_scoreboard(verifier), path)
    assert load_snapshot(path)["verifier"] == verifier


def test_a_cheap_scoreboard_cannot_be_locked(tmp_path: Path) -> None:
    path = tmp_path / "eval_snapshot.json"

    with pytest.raises(ValueError, match="cheap"):
        save_snapshot(_scoreboard("cheap"), path)

    assert not path.exists(), "a refused lock must not leave a partial baseline behind"


def test_an_unidentified_scoreboard_cannot_be_locked(tmp_path: Path) -> None:
    """An omitted verifier is the fail-open case `diff_snapshots` already guards."""
    board = _scoreboard()
    del board["verifier"]

    with pytest.raises(ValueError, match="verifier"):
        save_snapshot(board, tmp_path / "eval_snapshot.json")


def test_the_refusal_does_not_overwrite_an_existing_baseline(tmp_path: Path) -> None:
    """The damaging case: re-locking over a good snapshot with a cheap run."""
    path = tmp_path / "eval_snapshot.json"
    save_snapshot(_scoreboard("success"), path)

    with pytest.raises(ValueError, match="cheap"):
        save_snapshot(_scoreboard("cheap"), path)

    assert load_snapshot(path)["verifier"] == "success"
    assert json.loads(path.read_text(encoding="utf-8"))["outcome_accuracy"] == 0.9
