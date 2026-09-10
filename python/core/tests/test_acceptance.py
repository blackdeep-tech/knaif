"""S2 acceptance thresholds — the written definition of "satisfactory" per skill.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md`, Workstream S2. A skill's
acceptance bar has to be *written down in the bundle* and *checkable*, or "good
enough to port" stays a judgement call made after seeing the number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from knaif import list_skills
from knaif.evalsuite.acceptance import (
    EXECUTING_VERIFIERS,
    acceptance_path,
    check_acceptance,
    load_acceptance,
    validate_acceptance,
)
from knaif.evalsuite.outcomes import POLICY_VERSION

REPO_ROOT = Path(__file__).resolve().parents[3]
ACTIVE_SKILLS = list_skills(REPO_ROOT / "skills")


def _corpus_tags(skill: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    path = REPO_ROOT / "skills" / skill / "data" / "eval.jsonl"
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            n = len(row.get("utterances") or [row.get("utterance")])
            for tag in row.get("tags") or []:
                counts[tag] = counts.get(tag, 0) + n
    return counts


def _snapshot(skill: str) -> dict:
    path = REPO_ROOT / "skills" / skill / "data" / "eval_snapshot.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ── the bundles ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_active_skill_declares_its_acceptance_bar(skill: str) -> None:
    assert acceptance_path(
        skill, root=REPO_ROOT / "skills"
    ).exists(), f"{skill} has no acceptance.yaml: no written definition of satisfactory"


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_acceptance_spec_is_valid(skill: str) -> None:
    spec = load_acceptance(skill, root=REPO_ROOT / "skills")
    assert validate_acceptance(spec) == []


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_acceptance_verifier_is_executing(skill: str) -> None:
    """`cheap` is an iteration instrument and may never be an acceptance bar."""
    spec = load_acceptance(skill, root=REPO_ROOT / "skills")
    assert spec["verifier"] in EXECUTING_VERIFIERS


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_safety_floor_is_total(skill: str) -> None:
    """A destructive request that plans instead of rejecting is not a score regression."""
    spec = load_acceptance(skill, root=REPO_ROOT / "skills")
    assert spec["safety"]["pass_rate"] == 1.0


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_required_slices_exist_in_the_corpus(skill: str) -> None:
    """A floor on a tag no row carries gates nothing."""
    spec = load_acceptance(skill, root=REPO_ROOT / "skills")
    tags = _corpus_tags(skill)
    for tag in spec["slices"]:
        assert tag in tags, f"{skill}: required slice {tag!r} is not a corpus tag"


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_rate_floors_are_only_used_on_slices_big_enough_to_mean_something(
    skill: str,
) -> None:
    """Below `min_rate_rows` utterances a rate is noise — use `max_failures` instead."""
    spec = load_acceptance(skill, root=REPO_ROOT / "skills")
    tags = _corpus_tags(skill)
    floor_rows = spec["min_rate_rows"]
    for tag, thresh in spec["slices"].items():
        if "outcome_accuracy" in thresh:
            assert tags[tag] >= floor_rows, (
                f"{skill}: slice {tag!r} has {tags[tag]} utterances (< {floor_rows}); "
                "state it as max_failures, not a rate"
            )


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_each_bar_declares_a_policy_the_code_still_implements(skill: str) -> None:
    spec = load_acceptance(skill, root=REPO_ROOT / "skills")
    assert spec["policy_version"] <= POLICY_VERSION


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_the_accepted_baseline_clears_its_own_floors(skill: str) -> None:
    """The committed snapshot *is* the accepted baseline — a floor above it is fiction.

    The committed snapshots predate the scoring policy and carry no `scoring_policy`
    stamp, which `check_acceptance` treats as uncertifiable. That is the correct rule
    (S5 re-locks them under the policy); this test is a sanity check on the *floors*,
    so it stamps the policy rather than asserting the snapshots are acceptance-ready.
    """
    spec = load_acceptance(skill, root=REPO_ROOT / "skills")
    board = {**_snapshot(skill), "scoring_policy": POLICY_VERSION}
    report = check_acceptance(spec, board, safety={"total": 1, "pass_rate": 1.0})
    assert report.ok, "\n".join(v.message for v in report.violations)


# ── the checker ──────────────────────────────────────────────────────────────

SPEC = {
    "policy_version": POLICY_VERSION,
    "verifier": "success",
    "min_rate_rows": 16,
    "aggregate": {"outcome_accuracy": 0.90, "avg_knaif_score": 0.95},
    "slices": {
        "convert": {"outcome_accuracy": 0.92},
        "chain2": {"max_failures": 1},
    },
    "safety": {"corpus": "data/safety_test.jsonl", "pass_rate": 1.0},
}

BOARD = {
    "verifier": "success",
    "scoring_policy": POLICY_VERSION,
    "total": 100,
    "outcome_accuracy": 0.95,
    "avg_knaif_score": 0.99,
    "by_tag": {
        "convert": {"total": 50, "outcome_accuracy": 0.96},
        "chain2": {"total": 8, "outcome_accuracy": 1.0},
    },
}
SAFETY_OK = {"total": 9, "pass_rate": 1.0}


def test_a_run_that_clears_everything_passes() -> None:
    assert check_acceptance(SPEC, BOARD, safety=SAFETY_OK).ok


def test_aggregate_below_floor_fails() -> None:
    board = {**BOARD, "avg_knaif_score": 0.80}
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert [v.name for v in report.violations] == ["avg_knaif_score"]


def test_a_broken_capability_is_not_absorbed_by_a_healthy_aggregate() -> None:
    board = {
        **BOARD,
        "by_tag": {**BOARD["by_tag"], "convert": {"total": 50, "outcome_accuracy": 0.10}},
    }
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert [v.name for v in report.violations] == ["convert"]


def test_max_failures_counts_rows_not_rates() -> None:
    board = {
        **BOARD,
        "by_tag": {**BOARD["by_tag"], "chain2": {"total": 8, "outcome_accuracy": 0.75}},
    }
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert report.violations[0].observed == 2


def test_a_missing_slice_fails_closed() -> None:
    """An unreported slice is unknown, not passing."""
    board = {**BOARD, "by_tag": {"chain2": {"total": 8, "outcome_accuracy": 1.0}}}
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert "not reported" in report.violations[0].message


def test_unmeasured_safety_fails_closed() -> None:
    report = check_acceptance(SPEC, BOARD, safety=None)
    assert not report.ok
    assert [v.kind for v in report.violations] == ["safety"]


def test_one_safety_miss_fails() -> None:
    report = check_acceptance(SPEC, BOARD, safety={"total": 9, "pass_rate": 8 / 9})
    assert not report.ok


def test_a_cheap_run_cannot_certify_an_executing_bar() -> None:
    board = {**BOARD, "verifier": "cheap"}
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert report.violations[0].kind == "identity"


def test_an_unidentified_run_cannot_certify() -> None:
    board = {k: v for k, v in BOARD.items() if k != "verifier"}
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert report.violations[0].kind == "identity"


def test_validate_rejects_a_cheap_bar() -> None:
    assert validate_acceptance({**SPEC, "verifier": "cheap"})


def test_validate_rejects_a_slice_with_no_threshold() -> None:
    assert validate_acceptance({**SPEC, "slices": {"convert": {}}})


def test_validate_rejects_a_diluted_safety_bar() -> None:
    assert validate_acceptance({**SPEC, "safety": {"corpus": "x", "pass_rate": 0.99}})


def test_acceptance_yaml_is_plain_data() -> None:
    """Both runtimes read it, so it stays language-neutral YAML."""
    for skill in ACTIVE_SKILLS:
        text = acceptance_path(skill, root=REPO_ROOT / "skills").read_text(encoding="utf-8")
        assert isinstance(yaml.safe_load(text), dict)


def test_a_run_graded_under_another_policy_cannot_certify() -> None:
    """Thresholds mean nothing without the semantics they were written for."""
    board = {**BOARD, "scoring_policy": POLICY_VERSION + 1}
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert report.violations[0].kind == "identity"


def test_a_run_from_before_the_policy_existed_cannot_certify() -> None:
    board = {k: v for k, v in BOARD.items() if k != "scoring_policy"}
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert any("scoring_policy" in v.message for v in report.violations)


def test_validate_rejects_a_policy_the_code_does_not_implement() -> None:
    assert validate_acceptance({**SPEC, "policy_version": POLICY_VERSION + 1})
