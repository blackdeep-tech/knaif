"""The two prompt factors S3g varies: example selection and retrieval `top_k`.

Both are settings the runtime resolves at inference time, so a run that does not record
them is not reproducible evidence — G2's provenance rule. And two runs that resolved them
differently are not a trend, exactly as two verifiers or two scoring policies are not.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (S3g, V1/V2, G2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from knaif.evalsuite import cli
from knaif.evalsuite.runner import _build_registry_override
from knaif.evalsuite.snapshot import diff_snapshots

REPO_ROOT = Path(__file__).resolve().parents[3]


class _Agent:
    """Just enough agent for the retrieval helper."""

    def __init__(self) -> None:
        self.registry: dict[str, Any] = {}
        self.prompt_examples: list[dict] = [{"request": "x", "plan": {"plan": []}}]


def test_top_k_reaches_retrieve_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _fake(query, registry, top_k=5, **kw):
        seen["top_k"] = top_k
        return {}

    monkeypatch.setattr("knaif.registry.retrieve_tools", _fake)
    _build_registry_override(_Agent(), "convert my clip", top_k=8)
    assert seen["top_k"] == 8


def test_top_k_defaults_to_the_shipped_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """The eval path must measure what the product does unless told otherwise."""
    seen: dict[str, Any] = {}

    def _fake(query, registry, top_k=5, **kw):
        seen["top_k"] = top_k
        return {}

    monkeypatch.setattr("knaif.registry.retrieve_tools", _fake)
    _build_registry_override(_Agent(), "convert my clip")
    assert seen["top_k"] == 5


# -- example selection --------------------------------------------------------


def test_static_examples_suppress_per_utterance_selection() -> None:
    """`static` is the native runtime's behavior: one fixed block, no filtering."""
    agent = cli._make_agent("ffmpeg", REPO_ROOT / "sandbox", None, examples="static")
    assert agent.prompt_examples == []
    assert agent.examples_block, "the static block itself must survive"


def test_selected_examples_are_the_default() -> None:
    agent = cli._make_agent("ffmpeg", REPO_ROOT / "sandbox", None)
    assert agent.prompt_examples, "Python's reference behavior filters examples per utterance"


def test_the_two_modes_build_different_prompts() -> None:
    """If they rendered the same prompt there would be nothing to measure."""
    from knaif.registry import retrieve_tools

    utterance = "make this quieter"
    selected = cli._make_agent("ffmpeg", REPO_ROOT / "sandbox", None)
    static = cli._make_agent("ffmpeg", REPO_ROOT / "sandbox", None, examples="static")
    override = retrieve_tools(utterance, selected.registry)

    sys_sel, _ = selected.build_prompt(utterance, registry_override=override)
    sys_stat, _ = static.build_prompt(utterance, registry_override=override)
    assert sys_sel != sys_stat


# -- provenance ---------------------------------------------------------------


def test_a_scoreboard_records_the_prompt_configuration() -> None:
    board: dict[str, Any] = {"verifier": "success"}
    cli._stamp_prompt_config(board, top_k=8, examples="static", retrieval=True)
    assert board["prompt_config"] == {"top_k": 8, "examples": "static", "retrieval": True}


def test_a_diff_refuses_two_different_prompt_configurations() -> None:
    base = {
        "verifier": "success",
        "total": 10,
        "outcome_accuracy": 0.9,
        "prompt_config": {"top_k": 5, "examples": "selected", "retrieval": True},
    }
    cur = {
        "verifier": "success",
        "total": 10,
        "outcome_accuracy": 0.9,
        "prompt_config": {"top_k": 8, "examples": "selected", "retrieval": True},
    }
    with pytest.raises(ValueError, match="[Pp]rompt"):
        diff_snapshots(base, cur)


def test_a_pre_provenance_baseline_still_diffs() -> None:
    base = {"verifier": "success", "total": 10, "outcome_accuracy": 0.9}
    cur = {
        "verifier": "success",
        "total": 10,
        "outcome_accuracy": 0.9,
        "prompt_config": {"top_k": 5, "examples": "selected", "retrieval": True},
    }
    assert diff_snapshots(base, cur)["regressions"] == []


def test_the_flags_are_wired_into_the_parser() -> None:
    args = cli.build_parser().parse_args(
        ["run", "--skill", "ffmpeg", "--top-k", "8", "--examples", "static"]
    )
    assert args.top_k == 8
    assert args.examples == "static"
