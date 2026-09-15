"""S2 acceptance thresholds — the written definition of "satisfactory" per skill.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md`, Workstream S2. A skill's
acceptance bar has to be *written down in the bundle* and *checkable*, or "good
enough to port" stays a judgement call made after seeing the number.
"""

from __future__ import annotations

import json
import re
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


def _corpus_rows(skill: str) -> list[dict]:
    path = REPO_ROOT / "skills" / skill / "data" / "eval.jsonl"
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
@pytest.mark.parametrize("outcome", ["reject", "clarify"])
def test_control_outcome_tags_agree_with_the_expectation(skill: str, outcome: str) -> None:
    """The `reject` and `clarify` slices must contain exactly the rows that expect them.

    `reject` and `clarify` are both required slices *and* corpus tags, and nothing else ties
    the two together — so a relabel that moves `expected_outcome` without moving the tag
    leaves the row measured in the slice it just left. Relabelling 18 utterances from
    `reject` to `clarify` without this would have left both required slices scoring the
    wrong population, and each slice would still have looked healthy on its own.

    See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T4.
    """
    for row in _corpus_rows(skill):
        tags = set(row.get("tags") or [])
        expected = row.get("expected_outcome")
        if outcome in tags:
            assert expected == outcome, (
                f"{skill}:{row['id']} is tagged {outcome!r} but expects {expected!r} — "
                f"it would be scored inside the {outcome!r} slice it no longer belongs to"
            )
        if expected == outcome:
            assert outcome in tags, (
                f"{skill}:{row['id']} expects {outcome!r} but is not tagged {outcome!r} — "
                f"it is missing from the {outcome!r} slice that is supposed to measure it"
            )


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_each_bar_declares_a_policy_the_code_still_implements(skill: str) -> None:
    spec = load_acceptance(skill, root=REPO_ROOT / "skills")
    assert spec["policy_version"] <= POLICY_VERSION


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_the_accepted_baseline_clears_its_own_floors(skill: str) -> None:
    """The committed snapshot *is* the accepted baseline — a floor above it is fiction.

    Between T4's relabel and T7's re-lock this could not be asserted plainly: the snapshots
    described a corpus that no longer existed, so the skill carried a strict xfail and a
    second test watched whatever was still comparable. T7 locked both snapshots over the
    accepted sft-v4 run, so the baseline is a current, policy-stamped record again and the
    check is simply the check — no mark, no stamping, no rebased tag counts.
    """
    spec = load_acceptance(skill, root=REPO_ROOT / "skills")
    board = _snapshot(skill)
    assert board.get("scoring_policy") == POLICY_VERSION, (
        f"{skill}: the committed snapshot is stamped {board.get('scoring_policy')!r}, not "
        f"{POLICY_VERSION} — re-lock it rather than stamping it here"
    )
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


# ── a `plan` row must be reachable from its own utterance ────────────────────


def _token(word: str) -> re.Pattern[str]:
    """Match *word* on ASCII-alphanumeric boundaries only.

    `\b` is the wrong tool twice over here: "remove" would match "mov", and in
    "将MP3转换为FLAC" the CJK characters around `MP3` are word characters, so `\bmp3\b`
    does *not* match — silently flagging a Chinese utterance that names its file perfectly
    well. ASCII-only boundaries get both right.
    """
    return re.compile(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", re.I)


@pytest.mark.parametrize("skill", ACTIVE_SKILLS)
def test_every_plan_utterance_can_reach_a_file(skill: str) -> None:
    """An utterance that expects a `plan` must name something the plan can act on.

    Multi-utterance rows are paraphrase sets, and a paraphrase can lose the one thing the
    expected artifact depends on: "resize clip.mp4 to 480p" became "downscale to 480p",
    which names no file, refers to none, and cannot produce the row's artifact by any
    deterministic means. The corpus then scores the *correct* answer as a failure — and it
    did: in T6a `ffmpeg_218` and `ffmpeg_219` had the model spontaneously reply "Which file
    should I encode?", exactly right, counted wrong.

    The rule is derived from the corpus alone — its rows' own `fixture` names — and never
    from a scoreboard. When it was first run against T6a it flagged **13 utterances and all
    13 had failed**, while flagging nothing that passed: an independent rule and a
    measurement agreeing completely, which is why the fix was a relabel rather than a
    retrain target. See `evals/runs/2026-09-12_t6a-control_success/report.md`.

    `batch` rows are exempt by design: their expected plan *is* a glob, so naming no file is
    the request, not a gap in it.
    """
    rows = _corpus_rows(skill)
    fixtures = {(r.get("fixture") or "").lower() for r in rows if r.get("fixture")}
    fixtures.discard("")
    stems = {f.rsplit(".", 1)[0] for f in fixtures}
    by_ext: dict[str, list[str]] = {}
    for f in fixtures:
        if "." in f:
            by_ext.setdefault(f.rsplit(".", 1)[1], []).append(f)
    # "the mov" reaches a file only while exactly one fixture has that extension.
    unique_ext = {e for e, v in by_ext.items() if len(v) == 1}

    def reaches_a_file(utterance: str) -> bool:
        u = utterance.lower()
        return (
            any(_token(f).search(u) for f in fixtures)
            or any(_token(s).search(u) for s in stems)
            or any(_token(e).search(u) for e in unique_ext)
        )

    unreachable = [
        f"{row['id']}#{i} {utterance!r}"
        for row in rows
        if row.get("expected_outcome") == "plan" and "batch" not in set(row.get("tags") or [])
        for i, utterance in enumerate(row["utterances"])
        if not reaches_a_file(utterance)
    ]
    assert not unreachable, (
        f"{skill}: these utterances expect a plan but name no file the plan could act on — "
        "they are clarify rows wearing a plan label:\n  " + "\n  ".join(unreachable)
    )
