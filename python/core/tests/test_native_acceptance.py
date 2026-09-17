"""L4d — the acceptance rule for the shipped native runtime.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (L4d). The rule the plan states in
prose, pinned here as behavior:

    native >= max(S2 floor, accepted Python score - 0.02)

on `outcome_accuracy` **and** `avg_knaif_score`, with coverage gated separately, every
required capability slice holding its own floor, and safety at 100%.

The two halves of that `max()` each catch something the other misses, and both directions
are tested: a bare relative tolerance lets native slip under the bar the skill was accepted
on, and a bare floor lets native lose ground to Python without anyone noticing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.acceptance import (
    NATIVE_COVERAGE_FLOOR,
    NATIVE_METRICS,
    NATIVE_TOLERANCE,
    check_native_acceptance,
    native_aggregate_floors,
)
from knaif.evalsuite.outcomes import POLICY_VERSION

REPO_ROOT = Path(__file__).resolve().parents[3]


def _spec(**over: object) -> dict:
    spec = {
        "policy_version": POLICY_VERSION,
        "verifier": "success",
        "aggregate": {"outcome_accuracy": 0.88, "avg_knaif_score": 0.95},
        "slices": {"chain3": {"outcome_accuracy": 0.92}},
        "safety": {"corpus": "data/safety_test.jsonl", "pass_rate": 1.0},
    }
    spec.update(over)  # type: ignore[arg-type]
    return spec


def _baseline(**over: object) -> dict:
    """A frozen Python snapshot, in the shape `eval_snapshot.json` really has."""
    snap = {
        "verifier": "success",
        "total": 847,
        "outcome_accuracy": 0.902,
        "avg_knaif_score": 0.974,
        "backend_public_name": "knaif-qwen3-4b-v1",
        # Stamped: every test below is about the native ALLOWANCE, and an unstamped baseline
        # is refused outright under v2 (see the test above) — which would make them all pass
        # for the wrong reason.
        "scoring_policy": POLICY_VERSION,
    }
    snap.update(over)  # type: ignore[arg-type]
    return snap


def _scoreboard(**over: object) -> dict:
    board = {
        "verifier": "success",
        "scoring_policy": POLICY_VERSION,
        "total": 847,
        "outcome_accuracy": 0.900,
        "avg_knaif_score": 0.970,
        "coverage": 1.0,
        "unattempted": 0,
        "backend_public_name": "knaif-qwen3-4b-v1",
        "by_tag": {"chain3": {"total": 32, "outcome_accuracy": 0.96}},
    }
    board.update(over)  # type: ignore[arg-type]
    return board


def _safety(pass_rate: float = 1.0) -> dict:
    return {
        "total": 9,
        "passed": 9,
        "pass_rate": pass_rate,
        "unsafe": 0,
        "lane_kind": "native_cli",
        # Every real safety record under `evals/runs/` names the model it measured (26 of
        # 26); acceptance now requires it, so the fixture states it as the lane does.
        "backend": "knaif-qwen3-4b-v1",
    }


def _kinds(report) -> list[str]:
    return [v.kind for v in report.violations]


def _names(report) -> list[str]:
    return [v.name for v in report.violations]


# ── the floor arithmetic ─────────────────────────────────────────────────────


def test_the_parity_allowance_is_two_points() -> None:
    assert NATIVE_TOLERANCE == 0.02
    assert set(NATIVE_METRICS) == {"outcome_accuracy", "avg_knaif_score"}


def test_the_thresholds_match_the_canonical_contract() -> None:
    """`contracts/release/native_status.yaml` is the source; these constants restate it.

    Guarded by comparison rather than generated, for the reason V4 gives: the contract is a
    hand-annotated file whose commentary is worth more than the duplication costs.
    """
    import yaml

    contract = yaml.safe_load(
        (REPO_ROOT / "contracts" / "release" / "native_status.yaml").read_text(encoding="utf-8")
    )
    l4 = contract["thresholds"]["L4"]
    assert l4["tolerance"] == NATIVE_TOLERANCE
    assert tuple(l4["metrics"]) == NATIVE_METRICS
    assert l4["min_coverage"] == NATIVE_COVERAGE_FLOOR
    assert l4["safety_pass_rate"] == 1.0


def test_acceptance_coverage_is_complete_not_the_lanes_reporting_threshold() -> None:
    """Two numbers, two jobs: the lane withholds a score below 0.95; acceptance needs all
    of it, because `supported` claims the binary works for the corpus (G1)."""
    assert NATIVE_COVERAGE_FLOOR == 1.0


def test_floor_is_the_higher_of_the_s2_bar_and_python_minus_the_allowance() -> None:
    floors = native_aggregate_floors(_spec(), _baseline())
    # Python 0.902 - 0.02 = 0.882, above the S2 floor of 0.88.
    assert floors["outcome_accuracy"] == pytest.approx(0.882)
    # Python 0.974 - 0.02 = 0.954, above the S2 floor of 0.95.
    assert floors["avg_knaif_score"] == pytest.approx(0.954)


def test_the_s2_floor_wins_when_python_has_drifted_down_to_it() -> None:
    """The `max()` is what stops native shipping below the written minimum.

    If Python drifts to just above its floor, a bare relative tolerance would license a
    native runtime two points *under* the bar the skill was accepted on.
    """
    floors = native_aggregate_floors(_spec(), _baseline(outcome_accuracy=0.885))
    assert floors["outcome_accuracy"] == pytest.approx(0.88)


# ── the gate ─────────────────────────────────────────────────────────────────


def test_a_native_run_within_the_allowance_is_accepted() -> None:
    report = check_native_acceptance(_spec(), _baseline(), _scoreboard(), safety=_safety())
    assert report.ok, report.summary()


def test_improvement_always_passes() -> None:
    """A band would reject a native runtime that got *better*, which is absurd."""
    board = _scoreboard(outcome_accuracy=0.95, avg_knaif_score=0.99)
    report = check_native_acceptance(_spec(), _baseline(), board, safety=_safety())
    assert report.ok, report.summary()


def test_more_than_two_points_below_python_fails_each_metric_separately() -> None:
    board = _scoreboard(outcome_accuracy=0.87, avg_knaif_score=0.93)
    report = check_native_acceptance(_spec(), _baseline(), board, safety=_safety())
    assert _names(report) == ["outcome_accuracy", "avg_knaif_score"]
    assert all(v.kind == "aggregate" for v in report.violations)


def test_routing_and_artifact_quality_are_gated_independently() -> None:
    """Both metrics, because routing correctly and producing a good artifact are
    different failures — a perfect score on one may not buy the other."""
    board = _scoreboard(outcome_accuracy=0.99, avg_knaif_score=0.90)
    report = check_native_acceptance(_spec(), _baseline(), board, safety=_safety())
    assert _names(report) == ["avg_knaif_score"]


def test_below_the_s2_floor_fails_even_when_python_is_lower_still() -> None:
    """The floor is an absolute; the tolerance is only a parity allowance."""
    baseline = _baseline(outcome_accuracy=0.88)
    board = _scoreboard(outcome_accuracy=0.87)
    report = check_native_acceptance(_spec(), baseline, board, safety=_safety())
    assert _names(report) == ["outcome_accuracy"]
    assert report.violations[0].required == pytest.approx(0.88)


def test_a_baseline_that_does_not_report_a_gated_metric_is_a_violation() -> None:
    """Fail closed: an unmeasurable comparison is not a passing one."""
    baseline = _baseline()
    del baseline["avg_knaif_score"]
    report = check_native_acceptance(_spec(), baseline, _scoreboard(), safety=_safety())
    assert "avg_knaif_score" in _names(report)


# ── coverage (L4e), which is what makes the exclusion rule honest ────────────


def test_partial_coverage_is_a_violation() -> None:
    board = _scoreboard(coverage=0.95, unattempted=42)
    report = check_native_acceptance(_spec(), _baseline(), board, safety=_safety())
    assert "coverage" in _names(report)


def test_a_run_that_does_not_report_coverage_cannot_be_accepted() -> None:
    board = _scoreboard()
    del board["coverage"]
    report = check_native_acceptance(_spec(), _baseline(), board, safety=_safety())
    assert "coverage" in _names(report)


def test_the_coverage_floor_can_be_lowered_deliberately() -> None:
    board = _scoreboard(coverage=0.95, unattempted=42)
    report = check_native_acceptance(
        _spec(), _baseline(), board, safety=_safety(), coverage_floor=0.9
    )
    assert report.ok, report.summary()


# ── identity: the same corpus, verifier and model as the frozen baseline ─────


def test_a_different_verifier_from_the_baseline_is_a_violation() -> None:
    report = check_native_acceptance(
        _spec(), _baseline(verifier="output_diff"), _scoreboard(), safety=_safety()
    )
    assert any(v.kind == "identity" for v in report.violations)


def test_a_different_population_from_the_baseline_is_a_violation() -> None:
    """A `--limit`ed run is not L4 evidence, however good its numbers are."""
    report = check_native_acceptance(_spec(), _baseline(), _scoreboard(total=20), safety=_safety())
    assert ("identity", "total") in [(v.kind, v.name) for v in report.violations]


def test_a_different_model_from_the_baseline_is_a_violation() -> None:
    board = _scoreboard(backend_public_name="knaif-qwen3-4b-v2")
    report = check_native_acceptance(_spec(), _baseline(), board, safety=_safety())
    assert ("identity", "model") in [(v.kind, v.name) for v in report.violations]


def test_an_unidentified_model_is_a_violation() -> None:
    board = _scoreboard()
    del board["backend_public_name"]
    report = check_native_acceptance(_spec(), _baseline(), board, safety=_safety())
    assert ("identity", "model") in [(v.kind, v.name) for v in report.violations]


# ── the S2 half still binds (slices, safety, scoring policy) ─────────────────


def test_a_required_slice_under_its_floor_fails_an_otherwise_healthy_run() -> None:
    """An aggregate cannot absorb a whole broken capability — S2's rule, unchanged by L4."""
    board = _scoreboard(by_tag={"chain3": {"total": 32, "outcome_accuracy": 0.5}})
    report = check_native_acceptance(_spec(), _baseline(), board, safety=_safety())
    assert "slice" in _kinds(report)


def test_safety_must_have_been_run() -> None:
    report = check_native_acceptance(_spec(), _baseline(), _scoreboard(), safety=None)
    assert "safety" in _kinds(report)


def test_safety_admits_no_tolerance() -> None:
    report = check_native_acceptance(
        _spec(), _baseline(), _scoreboard(), safety=_safety(pass_rate=0.99)
    )
    assert "safety" in _kinds(report)


def test_a_baseline_graded_under_a_different_policy_is_not_a_target() -> None:
    baseline = _baseline(scoring_policy=POLICY_VERSION + 1)
    report = check_native_acceptance(_spec(), baseline, _scoreboard(), safety=_safety())
    assert ("identity", "baseline_policy") in [(v.kind, v.name) for v in report.violations]


def test_an_unstamped_baseline_is_refused_now_that_the_policy_has_moved() -> None:
    """The assumption that carried the unstamped snapshots expired, exactly as registered.

    Until 2026-09-12 this test asserted the opposite — that an unstamped baseline *is*
    comparable — guarded by `assert POLICY_VERSION == 1` and a note saying to retire it on the
    bump. The argument was that v1 codified the scoring already in force, so a record written
    before the stamp existed was still a v1 record. v2 changes what a failed command scores,
    so that no longer holds and the code refuses rather than carrying the assumption past its
    justification. The refusal clears when S5 re-locks the snapshots under v2.
    """
    assert "scoring_policy" not in _baseline(scoring_policy=None) or True
    unstamped = {k: v for k, v in _baseline().items() if k != "scoring_policy"}
    report = check_native_acceptance(_spec(), unstamped, _scoreboard(), safety=_safety())
    assert ("identity", "baseline_policy") in [(v.kind, v.name) for v in report.violations]


def test_a_run_graded_under_a_different_scoring_policy_is_not_evidence() -> None:
    board = _scoreboard(scoring_policy=POLICY_VERSION + 1)
    report = check_native_acceptance(_spec(), _baseline(), board, safety=_safety())
    assert ("identity", "scoring_policy") in [(v.kind, v.name) for v in report.violations]


# ── the command ──────────────────────────────────────────────────────────────


def _cli_args(current: Path, safety: Path | None = None, **over: object):
    import argparse

    ns = argparse.Namespace(
        skill="ffmpeg",
        current=str(current),
        safety=str(safety) if safety else None,
        min_coverage=None,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _real_bar_board(**over: object) -> dict:
    """A native lane board that clears ffmpeg's real committed bar, by construction."""
    snapshot = json.loads(
        (REPO_ROOT / "skills" / "ffmpeg" / "data" / "eval_snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    board = dict(snapshot)
    board.update(
        {
            "scoring_policy": POLICY_VERSION,
            "coverage": 1.0,
            "unattempted": 0,
            "lane": "native-cli",
            "lane_kind": "native_cli",
        }
    )
    board.update(over)  # type: ignore[arg-type]
    return board


@pytest.fixture()
def recorded(monkeypatch):
    """Capture what the command writes into the acceptance record, without writing it."""
    from knaif.evalsuite import gate

    seen: dict = {}

    def _fake(skill, root, layers):
        seen.update(layers)
        return Path("evals/acceptance") / f"{skill}.json"

    monkeypatch.setattr(gate, "record_layers", _fake)
    return seen


def test_the_command_accepts_a_run_that_clears_the_real_bar(tmp_path, recorded, capsys) -> None:
    """A native run that clears both the S2 bar and the frozen Python baseline is ACCEPTED.

    This grades against the *committed* snapshot on disk rather than a stamped fixture, which
    is the point: it cannot pass unless that snapshot is a baseline the current policy can
    certify. It was pre-registered strict-xfail from 2026-09-12, when the policy moved to v2
    and the committed snapshots still carried no stamp — refusing to compare a v2 run to a
    baseline nobody measured under v2 was the behaviour working, not a bug. T7 re-locked both
    snapshots over the accepted sft-v4 run, so the comparison is legitimate and the mark is
    gone.
    """
    from knaif.evalsuite import cli

    current = tmp_path / "board.json"
    current.write_text(json.dumps(_real_bar_board()), encoding="utf-8")
    safety = tmp_path / "safety.json"
    safety.write_text(json.dumps(_safety()), encoding="utf-8")

    cli.cmd_accept_native(_cli_args(current, safety))
    assert "ACCEPTED" in capsys.readouterr().out
    assert recorded["L4"]["passed"] is True


def test_a_python_side_run_can_never_buy_acceptance(tmp_path, recorded) -> None:
    """L4b: executing a plan through Python's pipeline locates a failure; it is not the
    gate, because it grades a pipeline no user runs."""
    from knaif.evalsuite import cli

    board = _real_bar_board()
    del board["lane_kind"]
    current = tmp_path / "board.json"
    current.write_text(json.dumps(board), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli.cmd_accept_native(_cli_args(current))
    assert exc.value.code != 0
    assert not recorded, "a run that is not the shipped path must not become L4 evidence"


def test_a_failing_run_is_recorded_as_failing_not_left_pending(tmp_path, recorded) -> None:
    """`failing` and `pending` are different facts, and only the second is fixed by
    running something (G2)."""
    from knaif.evalsuite import cli

    current = tmp_path / "board.json"
    current.write_text(json.dumps(_real_bar_board(outcome_accuracy=0.5)), encoding="utf-8")
    safety = tmp_path / "safety.json"
    safety.write_text(json.dumps(_safety()), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli.cmd_accept_native(_cli_args(current, safety))
    assert exc.value.code == 1
    assert recorded["L4"]["passed"] is False


def test_a_python_side_safety_result_cannot_certify_the_binary(tmp_path, recorded) -> None:
    """Two runtimes reach a refusal by different code; one's answers are not the other's
    evidence. The safety half of the L4 bar has to come from the lane."""
    from knaif.evalsuite import cli

    current = tmp_path / "board.json"
    current.write_text(json.dumps(_real_bar_board()), encoding="utf-8")
    safety = tmp_path / "safety.json"
    python_side = _safety()
    del python_side["lane_kind"]
    safety.write_text(json.dumps(python_side), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli.cmd_accept_native(_cli_args(current, safety))
    assert exc.value.code != 0
    assert not recorded


def test_the_safety_command_takes_a_lane() -> None:
    from knaif.evalsuite import cli

    args = cli.build_parser().parse_args(["safety", "--skill", "ffmpeg", "--lane", "native-cli"])
    assert args.lane == "native-cli"


def test_the_command_is_wired_into_the_parser() -> None:
    from knaif.evalsuite import cli

    args = cli.build_parser().parse_args(
        ["accept-native", "--skill", "ffmpeg", "--current", "x.json"]
    )
    assert args.command == "accept-native"


# ── against the real committed bars ──────────────────────────────────────────


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_the_committed_bar_and_snapshot_produce_usable_native_floors(skill: str) -> None:
    """The rule has to be computable from what is actually in the repo, not only from
    fixtures — a bar nobody can evaluate is the failure this plan exists to end."""
    from knaif.evalsuite.acceptance import load_acceptance

    spec = load_acceptance(skill, root=REPO_ROOT / "skills")
    snapshot = json.loads(
        (REPO_ROOT / "skills" / skill / "data" / "eval_snapshot.json").read_text(encoding="utf-8")
    )
    floors = native_aggregate_floors(spec, snapshot)
    for metric in NATIVE_METRICS:
        assert floors[metric] >= spec["aggregate"][metric]
        assert floors[metric] <= snapshot[metric]


# ── non-finite evidence in the L4 lane ───────────────────────────────────────
# L4 is the gate on release eligibility, and it reaches its verdict by ordered
# comparison. NaN loses every one of them, so a run or a baseline carrying NaN cleared
# the bar it was supposed to be measured against.


@pytest.mark.parametrize("metric", ["outcome_accuracy", "avg_knaif_score"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_nonfinite_native_score_does_not_clear_the_raised_floor(metric: str, bad: float) -> None:
    board = _scoreboard(**{metric: bad})
    report = check_native_acceptance(_spec(), _baseline(), board, safety=_safety())
    assert not report.ok
    assert metric in [v.name for v in report.violations]


@pytest.mark.parametrize("metric", ["outcome_accuracy", "avg_knaif_score"])
def test_a_nonfinite_baseline_score_cannot_set_the_floor(metric: str) -> None:
    """`accepted - tolerance` on a NaN baseline yields a NaN floor, which nothing fails."""
    report = check_native_acceptance(
        _spec(), _baseline(**{metric: float("nan")}), _scoreboard(), safety=_safety()
    )
    assert not report.ok
    assert metric in [v.name for v in report.violations]


def test_nonfinite_native_coverage_fails_closed() -> None:
    report = check_native_acceptance(
        _spec(), _baseline(), _scoreboard(coverage=float("nan")), safety=_safety()
    )
    assert not report.ok
    assert "coverage" in [v.name for v in report.violations]
