"""Golden parity for intent EXPANSION and command rendering — the layer below the planner.

`contracts/parity/expansion_cases.json` holds fixed plans and the ffmpeg argv they render to. The
Rust side (`skills/ffmpeg/native/tests/expansion_parity.rs`) renders the identical plans and must
produce the same commands, in the same order, in the same number.

**Why this exists.** Plan-envelope parity cannot see this layer. On 2026-08-10 both runtimes
emitted the same correct two-step plan for "cut … then extract the audio", and native's `run`
rendered only the first step — `steps.first()`, with no loop — so the audio was never produced.
Every prompt- and plan-level measurement was green throughout. The multi-step cases here are that
regression, and they need no model, so they gate every PR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "parity" / "expansion_cases.json"

sys.path.insert(0, str(REPO_ROOT / "scripts"))


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def agent(doc: dict):
    from knaif import CommandAgent

    return CommandAgent.from_skill(
        REPO_ROOT / "skills" / "ffmpeg", sandbox=REPO_ROOT / doc["sandbox"]
    )


def _render(agent, plan: dict) -> list[list[str]]:
    from parity_check import canon_token

    from knaif.evalsuite.runner import _extract_artifacts

    results = agent.execute_plan(plan, dry_run=True, confirmed=False)
    return [
        [canon_token(t.replace("\\", "/")) for t in cmd.split()]
        for cmd in _extract_artifacts(results)
    ]


def test_expansion_cases_render_as_recorded(doc: dict, agent) -> None:
    assert doc["cases"], "fixture file has no cases"
    for case in doc["cases"]:
        got = _render(agent, case["plan"])
        assert got == case["expected_commands"], f"{case['name']}: rendered commands changed"


def test_every_plan_step_renders_a_command(doc: dict, agent) -> None:
    """One command per step — the assertion that would have caught the dropped-step bug.

    Kept separate from the golden comparison on purpose: a count check states the invariant in a
    form that survives a legitimate change to any individual command's flags.
    """
    for case in doc["cases"]:
        steps = case["plan"]["plan"]
        got = _render(agent, case["plan"])
        assert len(got) == len(steps), (
            f"{case['name']}: {len(steps)} plan steps rendered {len(got)} commands — a step was "
            f"dropped or duplicated"
        )


def test_chain_intermediates_are_threaded(doc: dict, agent) -> None:
    """Step N's output must be step N+1's input, or the chain is a set of unrelated commands."""
    for case in doc["cases"]:
        commands = _render(agent, case["plan"])
        if len(commands) < 2:
            continue
        for earlier, later in zip(commands, commands[1:], strict=False):
            produced = earlier[-1]  # ffmpeg's output path is the final argv token
            assert produced in later, (
                f"{case['name']}: {produced!r} is produced by one step but not consumed by the "
                f"next — the chain is not threaded"
            )
