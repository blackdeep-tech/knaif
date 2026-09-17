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
    # The safety half is stubbed -- this test is about the aggregate and slice floors,
    # not about safety identity, which has its own tests. The stub still has to look
    # like a real record: acceptance now requires safety evidence to name the model it
    # measured (all 26 real records under `evals/runs/` do), so it is named from the
    # snapshot rather than left anonymous.
    safety = {
        "total": 1,
        "pass_rate": 1.0,
        "skill": skill,
        "backend": board.get("backend"),
        "backend_public_name": board.get("backend_public_name"),
    }
    report = check_acceptance(spec, board, safety=safety)
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
    "coverage": 1.0,
    "unattempted": 0,
    "fixture_integrity": [],
    "outcome_accuracy": 0.95,
    "avg_knaif_score": 0.99,
    "by_tag": {
        "convert": {"total": 50, "outcome_accuracy": 0.96},
        "chain2": {"total": 8, "outcome_accuracy": 1.0},
    },
}
SAFETY_OK = {"total": 9, "pass_rate": 1.0}

# ── evidence integrity ───────────────────────────────────────────────────────
# A floor is a claim about a population. The 2026-09-17 audit showed the gate would
# accept an otherwise-passing scoreboard whose population was empty, whose fixtures were
# recorded as corrupt, or whose aggregates were NaN — the last because every comparison
# with NaN is False, so `observed + eps < floor` silently held. None of these proved a
# historical run was wrong; they proved the gate could not tell.


def test_incomplete_coverage_fails_closed() -> None:
    """A score over a population the run never finished is not evidence."""
    report = check_acceptance(
        SPEC, {**BOARD, "coverage": 0.0, "unattempted": 100}, safety=SAFETY_OK
    )
    assert not report.ok
    assert [v.name for v in report.violations] == ["coverage"]


def test_absent_coverage_fails_closed() -> None:
    board = {k: v for k, v in BOARD.items() if k != "coverage"}
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert [v.name for v in report.violations] == ["coverage"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_aggregate_fails_closed(bad: float) -> None:
    """NaN compares False against every floor; it must not read as 'met'."""
    report = check_acceptance(SPEC, {**BOARD, "avg_knaif_score": bad}, safety=SAFETY_OK)
    assert not report.ok
    assert [v.name for v in report.violations] == ["avg_knaif_score"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_nonfinite_slice_rate_fails_closed(bad: float) -> None:
    """A slice is graded by the same comparison the aggregate is — and NaN beats it too."""
    board = {
        **BOARD,
        "by_tag": {**BOARD["by_tag"], "convert": {"total": 50, "outcome_accuracy": bad}},
    }
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert [v.name for v in report.violations] == ["convert"]


def test_nonfinite_max_failures_rate_fails_closed() -> None:
    """`max_failures` converts a rate into a row count, and int(NaN) raises.

    A gate that crashes is not a gate that rejected the run: the caller sees a traceback
    from its own tooling, not a verdict, and the natural reaction is to work around it.
    """
    board = {
        **BOARD,
        "by_tag": {**BOARD["by_tag"], "chain2": {"total": 8, "outcome_accuracy": float("nan")}},
    }
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert [v.name for v in report.violations] == ["chain2"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_nonfinite_safety_rate_fails_closed(bad: float) -> None:
    """Safety is the one bar with no tolerance; it must not be cleared by a non-number."""
    report = check_acceptance(SPEC, BOARD, safety={"total": 9, "pass_rate": bad})
    assert not report.ok
    assert [v.kind for v in report.violations] == ["safety"]


@pytest.mark.parametrize("junk", ["n/a", [], {"v": 0.95}, None])
@pytest.mark.parametrize("field", ["coverage", "outcome_accuracy"])
def test_a_malformed_record_is_rejected_not_raised(field: str, junk: object) -> None:
    """A gate that raises has not rejected the run — it has broken.

    The caller sees a traceback from its own tooling rather than a verdict, and the
    reasonable reaction is to route around the broken check. Every recorded value goes
    through one reader, so "absent", "not a number" and "not numeric at all" land in the
    same place instead of three.
    """
    report = check_acceptance(SPEC, {**BOARD, field: junk}, safety=SAFETY_OK)
    assert not report.ok
    assert [v.name for v in report.violations] == [field]


def test_recorded_fixture_corruption_fails_closed() -> None:
    """The run itself recorded that what it measured against had drifted."""
    board = {**BOARD, "fixture_integrity": ["clip.mp4: content hash mismatch"]}
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert [v.name for v in report.violations] == ["fixture_integrity"]


def test_an_empty_safety_population_fails_closed() -> None:
    """A retained pass_rate over zero rows certifies nothing."""
    report = check_acceptance(SPEC, BOARD, safety={"total": 0, "pass_rate": 1.0})
    assert not report.ok
    assert [v.kind for v in report.violations] == ["safety"]


def test_safety_measured_on_a_different_model_fails_closed() -> None:
    """Safety is a claim about the model that ships, not about some other one."""
    board = {**BOARD, "backend": "the-candidate"}
    safety = {**SAFETY_OK, "backend": "some-other-model"}
    report = check_acceptance(SPEC, board, safety=safety)
    assert not report.ok
    assert [v.kind for v in report.violations] == ["safety"]


def test_matching_backends_still_pass() -> None:
    board = {**BOARD, "backend": "the-candidate"}
    safety = {**SAFETY_OK, "backend": "the-candidate"}
    assert check_acceptance(SPEC, board, safety=safety).ok


def test_the_native_lane_naming_split_is_not_a_mismatch() -> None:
    """`backend` does not mean the same thing on both records in the L4 lane.

    A native scoreboard records the *lane* in `backend` (`native-cli`) and the model in
    `backend_public_name`; the safety record it is paired with records the *model* in
    `backend`. Comparing the two strings directly rejects a run whose two halves name the
    same model — 4 of the 26 real scoreboard/safety pairs in `evals/runs/` are that shape.
    The records agree if any identifier they both carry agrees.
    """
    board = {**BOARD, "backend": "native-cli", "backend_public_name": "knaif-qwen3-4b-v1"}
    safety = {**SAFETY_OK, "backend": "knaif-qwen3-4b-v1"}
    report = check_acceptance(SPEC, board, safety=safety)
    assert report.ok, report.summary()


def test_another_skills_safety_result_cannot_certify_this_one() -> None:
    """A safety pass is a claim about *this* corpus, and the records say which they ran.

    `documents` has 9 safety rows and `ffmpeg` 11; a paired run produces both on the same
    backend, at the same pass rate, minutes apart. Nothing in the evidence distinguished
    them, so the smaller, easier corpus could certify the larger one.
    """
    spec = {**SPEC, "skill": "ffmpeg"}
    safety = {**SAFETY_OK, "skill": "documents", "total": 9}
    report = check_acceptance(spec, BOARD, safety=safety)
    assert not report.ok
    assert [v.kind for v in report.violations] == ["safety"]


def test_the_skills_own_safety_result_passes() -> None:
    spec = {**SPEC, "skill": "ffmpeg"}
    assert check_acceptance(spec, BOARD, safety={**SAFETY_OK, "skill": "ffmpeg"}).ok


def test_load_acceptance_stamps_the_skill_it_belongs_to() -> None:
    """Without this the checker cannot bind a spec to the evidence offered for it."""
    assert load_acceptance("ffmpeg", root=REPO_ROOT / "skills")["skill"] == "ffmpeg"


def test_a_genuinely_different_model_is_still_caught() -> None:
    board = {**BOARD, "backend": "native-cli", "backend_public_name": "knaif-qwen3-4b-v1"}
    safety = {**SAFETY_OK, "backend": "some-other-model"}
    report = check_acceptance(SPEC, board, safety=safety)
    assert not report.ok
    assert [v.kind for v in report.violations] == ["safety"]


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


# ── evidence must describe the whole corpus, and one consistent system ───────
# Found by an independent adversarial audit of the hardening above (2026-09-17).
# Each of these cleared the gate as first written.


def test_a_subset_run_cannot_certify_the_whole_corpus() -> None:
    """`coverage` is completeness *among returned rows*, not of the corpus.

    Score 13 of 851 utterances perfectly, chosen to touch every required tag, and the
    scorer honestly reports `coverage: 1.0` — it has no idea what it was not asked to run.
    The population has to be pinned from outside the run.
    """
    spec = {**SPEC, "skill": "ffmpeg", "expected_total": 851}
    report = check_acceptance(spec, {**BOARD, "total": 13}, safety=SAFETY_OK)
    assert not report.ok
    assert [v.name for v in report.violations] == ["total"]


def test_the_full_corpus_passes() -> None:
    spec = {**SPEC, "skill": "ffmpeg", "expected_total": 100}
    assert check_acceptance(spec, BOARD, safety=SAFETY_OK).ok


def test_load_acceptance_pins_the_corpus_population() -> None:
    spec = load_acceptance("ffmpeg", root=REPO_ROOT / "skills")
    assert spec["expected_total"] == _snapshot("ffmpeg")["total"]


def test_conflicting_public_model_names_are_a_mismatch() -> None:
    """Sharing an eval key does not make two different shipped models one model."""
    board = {**BOARD, "backend": "cand", "backend_public_name": "knaif-qwen3-4b-v2"}
    safety = {**SAFETY_OK, "backend": "cand", "backend_public_name": "knaif-qwen3-4b-v1"}
    report = check_acceptance(SPEC, board, safety=safety)
    assert not report.ok
    assert [v.kind for v in report.violations] == ["safety"]


@pytest.mark.parametrize("bad", [-1, 0, float("nan"), 2.5, "9"])
def test_a_safety_population_must_be_a_positive_count(bad: object) -> None:
    """`total: -1` is truthy, and truthiness was the whole test."""
    report = check_acceptance(SPEC, BOARD, safety={**SAFETY_OK, "total": bad})
    assert not report.ok
    assert [v.kind for v in report.violations] == ["safety"]


def test_an_empty_required_slice_is_not_a_passing_budget() -> None:
    """0 rows produce 0 failures, which is within every budget."""
    board = {**BOARD, "by_tag": {**BOARD["by_tag"], "chain2": {"total": 0, "outcome_accuracy": 0}}}
    report = check_acceptance(SPEC, board, safety=SAFETY_OK)
    assert not report.ok
    assert [v.name for v in report.violations] == ["chain2"]


@pytest.mark.parametrize(
    "slices",
    [
        {"convert": {"outcome_accuracy": float("nan")}},
        {"convert": {"outcome_accuracy": 1.5}},
        {"convert": {"max_failures": -1}},
    ],
)
def test_validate_rejects_an_unusable_slice_threshold(slices: dict) -> None:
    """A bar written as NaN is met by every measurement, including zero."""
    errors = validate_acceptance({**SPEC, "slices": slices})
    assert errors, f"{slices} was accepted as a threshold"


def test_safety_evidence_must_name_the_model_it_measured() -> None:
    """Omitting the name was the way past the identity check."""
    board = {**BOARD, "backend": "cand", "backend_public_name": "v2"}
    report = check_acceptance(SPEC, board, safety={"total": 9, "pass_rate": 1.0})
    assert not report.ok
    assert [v.kind for v in report.violations] == ["safety"]


def test_safety_measured_under_a_different_prompt_is_not_this_runs_evidence() -> None:
    """What the model is shown changes what it refuses, and both records stamp it."""
    board = {**BOARD, "backend": "cand", "prompt_config": {"top_k": 5, "examples": "selected"}}
    safety = {**SAFETY_OK, "backend": "cand", "prompt_config": {"top_k": 99, "examples": "static"}}
    report = check_acceptance(SPEC, board, safety=safety)
    assert not report.ok
    assert [v.kind for v in report.violations] == ["safety"]


def test_a_bar_that_does_not_validate_cannot_certify_anything() -> None:
    """The checker read thresholds the validator would have rejected, unasked."""
    spec = {**SPEC, "slices": {"convert": {"outcome_accuracy": float("nan")}}}
    report = check_acceptance(spec, BOARD, safety=SAFETY_OK)
    assert not report.ok
    assert [v.kind for v in report.violations] == ["identity"]
