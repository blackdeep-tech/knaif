"""The shared scoring contract (Workstream S2 / L4d).

Both runtimes' records are graded by one definition, stamped with the policy version
in force when the record was written. What the contract fixes:

* a **correct refusal** is a success for `outcome_accuracy` and *absent* from
  `avg_knaif_score` — not a zero;
* an **unattempted** row (a capability that is not built) is a failure for
  `outcome_accuracy` and *excluded* from `avg_knaif_score`;
* which is only honest because **coverage is reported alongside**, so a partial port
  cannot score well by not trying.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.outcomes import POLICY_VERSION
from knaif.evalsuite.protocols import VerifyResult
from knaif.evalsuite.runner import AgentOutput
from knaif.evalsuite.scoring import score_corpus


def _row(rid: str, expected: str = "plan", tags: list[str] | None = None) -> CorpusRow:
    return CorpusRow(
        id=rid,
        utterances=[f"utterance for {rid}"],
        expected_outcome=expected,
        tags=tags or ["convert"],
        success_criteria={"output_exists": True},
    )


def _out(rid: str, outcome: str, artifact: str | None = "ffmpeg -i a.mp4 b.mp4") -> AgentOutput:
    return AgentOutput(
        id=rid,
        utterance=f"utterance for {rid}",
        utterance_idx=0,
        outcome=outcome,
        plan={"plan": [{"tool": "convert_video", "args": {}}]},
        artifact=artifact,
        latency_ms=10.0,
    )


def _verifiers(score: float = 1.0):
    return {"success": lambda output, criteria, sandbox: VerifyResult(score=score)}


def _score(rows, outputs, tmp_path: Path):
    return score_corpus(outputs, rows, _verifiers(), "success", tmp_path)


def test_scoreboard_declares_the_policy_it_was_graded_under(tmp_path: Path) -> None:
    board = _score([_row("a")], [_out("a", "plan")], tmp_path)
    assert board["scoring_policy"] == POLICY_VERSION


def test_a_correct_refusal_scores_the_outcome_and_stays_out_of_the_average(
    tmp_path: Path,
) -> None:
    rows = [_row("a"), _row("b", expected="reject")]
    outputs = [_out("a", "plan"), _out("b", "reject", artifact=None)]
    board = _score(rows, outputs, tmp_path)

    assert board["outcome_accuracy"] == 1.0
    # The refusal contributes no artifact, so it must not drag the quality average to 0.5.
    assert board["avg_knaif_score"] == 1.0
    assert board["coverage"] == 1.0


def test_an_unbuilt_capability_is_a_coverage_miss_not_a_quality_score(tmp_path: Path) -> None:
    rows = [_row("a"), _row("b")]
    outputs = [_out("a", "plan"), _out("b", "not_implemented", artifact=None)]
    board = _score(rows, outputs, tmp_path)

    assert board["outcome_accuracy"] == 0.5, "an unattempted row is an outcome failure"
    assert board["avg_knaif_score"] == 1.0, "and is excluded from the quality average"
    assert board["coverage"] == 0.5, "which is only honest because coverage says so"
    assert board["unattempted"] == 1


def test_coverage_is_reported_per_slice_too(tmp_path: Path) -> None:
    """L4e needs coverage per required slice; an aggregate hides which capability is missing."""
    rows = [_row("a", tags=["convert"]), _row("b", tags=["chain2"])]
    outputs = [_out("a", "plan"), _out("b", "not_implemented", artifact=None)]
    board = _score(rows, outputs, tmp_path)

    assert board["by_tag"]["convert"]["coverage"] == 1.0
    assert board["by_tag"]["chain2"]["coverage"] == 0.0
    assert board["by_tag"]["chain2"]["unattempted"] == 1


def test_outcome_counts_are_recorded(tmp_path: Path) -> None:
    rows = [_row("a"), _row("b"), _row("c", expected="clarify")]
    outputs = [
        _out("a", "plan"),
        _out("b", "not_implemented", artifact=None),
        _out("c", "clarify", artifact=None),
    ]
    board = _score(rows, outputs, tmp_path)
    assert board["by_outcome"] == {"plan": 1, "not_implemented": 1, "clarify": 1}


def test_a_full_run_has_full_coverage(tmp_path: Path) -> None:
    rows = [_row(f"r{i}") for i in range(4)]
    outputs = [_out(f"r{i}", "plan") for i in range(4)]
    board = _score(rows, outputs, tmp_path)
    assert board["coverage"] == 1.0
    assert board["unattempted"] == 0


# -- the policy travels with the record ---------------------------------------


def test_a_snapshot_diff_refuses_two_different_policies() -> None:
    """Two runs graded under different semantics are not a trend."""
    from knaif.evalsuite.snapshot import diff_snapshots

    base = {"verifier": "success", "scoring_policy": 1, "total": 10, "outcome_accuracy": 0.9}
    cur = {"verifier": "success", "scoring_policy": 2, "total": 10, "outcome_accuracy": 0.9}
    with pytest.raises(ValueError, match="Scoring-policy mismatch"):
        diff_snapshots(base, cur)


def test_a_snapshot_diff_refuses_an_unstamped_current() -> None:
    from knaif.evalsuite.snapshot import diff_snapshots

    base = {"verifier": "success", "scoring_policy": 1, "total": 10, "outcome_accuracy": 0.9}
    cur = {"verifier": "success", "total": 10, "outcome_accuracy": 0.9}
    with pytest.raises(ValueError, match="no scoring_policy"):
        diff_snapshots(base, cur)


def test_a_pre_policy_baseline_still_diffs() -> None:
    """Snapshots locked before the policy existed keep working until S5 re-locks them."""
    from knaif.evalsuite.snapshot import diff_snapshots

    base = {"verifier": "success", "total": 10, "outcome_accuracy": 0.9}
    cur = {"verifier": "success", "scoring_policy": 1, "total": 10, "outcome_accuracy": 0.9}
    assert diff_snapshots(base, cur)["regressions"] == []
