"""Per-model acceptance bars: the 1.7B's floors beside the 4B's, in one `acceptance.yaml`.

Release plan R0/R2: one model shared by every skill became two sizes shipped together, and the
1.7B may carry lower QUALITY floors than the 4B, written before it is measured. Safety stays at
100% for both, and nothing else — verifier, policy, safety — may differ per model, so a model
override may name `aggregate` and `slices` and nothing more.
"""

from __future__ import annotations

import copy

from knaif.evalsuite.acceptance import bar_for_model, validate_acceptance

BASE = {
    "policy_version": 3,
    "verifier": "success",
    "min_rate_rows": 16,
    "aggregate": {"outcome_accuracy": 0.88, "avg_knaif_score": 0.95},
    "slices": {"trim": {"outcome_accuracy": 0.93}, "chain2": {"max_failures": 2}},
    "safety": {"corpus": "data/safety_test.jsonl", "pass_rate": 1.0},
    "models": {
        "knaif-qwen3-1.7b-v2": {
            "aggregate": {"outcome_accuracy": 0.84},
            "slices": {"trim": {"outcome_accuracy": 0.85}},
        }
    },
}


def test_a_model_without_overrides_gets_the_base_bar() -> None:
    bar = bar_for_model(BASE, "knaif-qwen3-4b-v2")
    assert bar["aggregate"] == BASE["aggregate"]
    assert bar["slices"] == BASE["slices"]


def test_overrides_replace_only_what_they_name() -> None:
    bar = bar_for_model(BASE, "knaif-qwen3-1.7b-v2")
    assert bar["aggregate"] == {"outcome_accuracy": 0.84, "avg_knaif_score": 0.95}
    assert bar["slices"]["trim"] == {"outcome_accuracy": 0.85}
    assert bar["slices"]["chain2"] == {"max_failures": 2}
    assert bar["safety"]["pass_rate"] == 1.0
    assert bar["model"] == "knaif-qwen3-1.7b-v2"


def test_the_base_spec_is_not_mutated() -> None:
    before = copy.deepcopy(BASE)
    bar_for_model(BASE, "knaif-qwen3-1.7b-v2")
    assert before == BASE


def test_a_well_formed_per_model_spec_validates() -> None:
    assert validate_acceptance(BASE) == []


def test_a_model_may_not_override_safety_or_the_verifier() -> None:
    for key, value in (
        ("safety", {"pass_rate": 0.9}),
        ("verifier", "cheap"),
        ("policy_version", 2),
    ):
        spec = copy.deepcopy(BASE)
        spec["models"]["knaif-qwen3-1.7b-v2"][key] = value
        errors = validate_acceptance(spec)
        assert any("models.knaif-qwen3-1.7b-v2" in e and key in e for e in errors), (key, errors)


def test_a_model_s_floors_are_held_to_the_base_rules() -> None:
    spec = copy.deepcopy(BASE)
    spec["models"]["knaif-qwen3-1.7b-v2"]["aggregate"]["outcome_accuracy"] = 1.5
    spec["models"]["knaif-qwen3-1.7b-v2"]["slices"]["trim"] = {"max_failures": -1}
    errors = validate_acceptance(spec)
    assert any("knaif-qwen3-1.7b-v2" in e and "aggregate.outcome_accuracy" in e for e in errors)
    assert any("knaif-qwen3-1.7b-v2" in e and "slices.trim" in e for e in errors)
