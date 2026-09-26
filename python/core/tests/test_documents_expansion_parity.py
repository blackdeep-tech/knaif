"""Golden expansion parity for the documents skill — the analogue of test_expansion_parity.py.

The rendered artefact here is the set of **output paths** a plan produces, not an ffmpeg argv:
documents' `preview` returns paths (or a read result), so that is the comparable surface.

**Why this exists.** `run` dispatches documents steps through the same loop as ffmpeg, which until
2026-08-10 executed only the first step of a plan (`steps.first()`, no loop). The corpus has seven
multi-step documents rows, so this skill was exposed to exactly the same defect — it simply was not
the one reported. The multi-step cases are the regression, and they need no model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "parity" / "documents_expansion_cases.json"


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def agent(doc: dict):
    from knaif import CommandAgent

    return CommandAgent.from_skill(
        REPO_ROOT / "skills" / "documents", sandbox=REPO_ROOT / doc["sandbox"]
    )


def _outputs(agent, plan: dict) -> list[str]:
    """Output basenames in production order, deduplicated.

    Documents expands each intent into inspect/run/verify sub-steps that repeat the same path, so
    first-seen order is what identifies the artefacts a plan actually produces.
    """
    results = agent.execute_plan(plan, dry_run=True, confirmed=False)
    out: list[str] = []
    for r in results:
        value = r.get("result") or {}
        for key in ("output", "outputs"):
            v = value.get(key)
            for path in [v] if isinstance(v, str) else (v or []):
                if isinstance(path, str):
                    base = path.replace("\\", "/").rsplit("/", 1)[-1]
                    if base not in out:
                        out.append(base)
    return out


def test_documents_expansion_matches_the_contract(doc: dict, agent) -> None:
    assert doc["cases"], "fixture file has no cases"
    for case in doc["cases"]:
        got = _outputs(agent, case["plan"])
        assert got == case["expected_outputs"], f"{case['name']}: produced outputs changed"


def test_every_step_produces_an_output(doc: dict, agent) -> None:
    """One artefact per plan step — the dropped-step regression, for this skill."""
    for case in doc["cases"]:
        steps = case["plan"]["plan"]
        got = _outputs(agent, case["plan"])
        assert len(got) == len(steps), (
            f"{case['name']}: {len(steps)} plan steps produced {len(got)} outputs — a step was "
            f"dropped or duplicated"
        )


def test_chain_intermediates_are_threaded(doc: dict, agent) -> None:
    """Step N's output must be the declared input of step N+1, or the chain is not a chain."""
    for case in doc["cases"]:
        steps = case["plan"]["plan"]
        if len(steps) < 2:
            continue
        outs = _outputs(agent, case["plan"])
        for i, later in enumerate(steps[1:]):
            produced = outs[i]
            declared = later.get("args", {}).get("input")
            assert declared == produced, (
                f"{case['name']}: step {i + 2} takes {declared!r} but step {i + 1} produced "
                f"{produced!r} — the chain is not threaded"
            )
