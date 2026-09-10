"""L1a: the prompt-parity contract, Python side.

Fixed utterance x fixed registry x fixed prompt overrides -> an exact `(system, user)`
pair, compared byte for byte with no allow-list. The Rust side runs the identical
fixtures (`prompt_parity_cases` in `apps/cli/src/main.rs`), currently `#[ignore]`d because
it fails — deliberately, until V1-V3 converge the two builders. That is the only moment
the contract is proven to detect the bug it exists for.

Python holds the expected values because Python is the reference (Rule 1). So these tests
mostly guard against Python drifting away from a contract Rust is being ported *to* —
which is exactly what would silently invalidate the Rust half.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (L1a).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.prompt import build_prompt
from knaif.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "parity" / "prompt_cases.json"

DOC = json.loads(FIXTURES.read_text(encoding="utf-8"))
CASES = DOC["cases"]


def _registry(tmp_path: Path, name: str):
    path = tmp_path / f"{name}.yaml"
    path.write_text(DOC["registries"][name], encoding="utf-8")
    return load_registry(path)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_prompt_matches_the_contract(case: dict, tmp_path: Path) -> None:
    overrides = DOC["prompt_overrides"][case["prompt_overrides"]]
    system, user = build_prompt(
        case["utterance"],
        _registry(tmp_path, case["registry"]),
        system_header=overrides["system_header"],
        examples_block=overrides["examples_block"],
    )
    assert system == case["expected_system"]
    assert user == case["expected_user"]


def test_the_contract_covers_path_normalization() -> None:
    """Named in the plan because it is where the two runtimes are known to differ."""
    names = {c["name"] for c in CASES}
    assert {
        "windows_path_unquoted",
        "dot_relative_path",
        "quoted_windows_path",
        "bare_backslash_is_not_a_path",
    } <= names


def test_internal_tools_never_reach_the_model() -> None:
    assert "run_batch" in DOC["registries"]["demo"], "fixture must contain an internal tool"
    for case in CASES:
        assert "run_batch" not in case["expected_system"]


def test_control_tools_are_not_listed_as_available() -> None:
    """clarify/reject/done are system tools: always in the registry, never in the listing."""
    for case in CASES:
        listing = case["expected_system"]
        assert "  - clarify:" not in listing
        assert "  - reject:" not in listing
        assert "  - done:" not in listing


def test_expected_prompts_are_inline_not_sibling_goldens() -> None:
    """`* text=auto` has produced a CRLF-only failure in this repo three times.

    A contract that passes in CI and fails on the maintainer's box is one people learn to
    skip, so the expected strings live JSON-escaped inside the cases file.
    """
    siblings = list(FIXTURES.parent.glob("*.txt"))
    assert not siblings, f"expected prompts must stay inline; found {siblings}"


def test_the_contract_states_its_own_scope_and_coverage() -> None:
    """A contract that overstates what it proves is worse than none."""
    assert "not final token IDs" in DOC["_scope"].replace("NOT", "not")
    assert DOC["_coverage"] == {"ubuntu": "CI", "windows": "local", "macos": "unexercised"}
