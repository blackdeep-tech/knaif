"""Golden parity: Python prompt construction over contracts/parity/prompt_cases.json.

The Rust side (native/crates/knaif-core/tests/prompt_parity.rs) renders the identical fixtures and
must produce byte-identical `(system, user)` messages.

**Scope.** This compares the logical messages only. Each runtime then applies the GGUF's chat
template through a different llama.cpp binding, so identical messages here are necessary for
parity but are not proof of an identical final token sequence. Saying so explicitly matters: the
divergence that opened this plan was accepted on the strength of a check that was never built, and
a green test whose limits are unstated invites the same mistake.

R1 of docs/plans/2026-08-08-native-python-planning-parity.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.prompt import build_prompt, normalize_path_separators
from knaif.registry import load_registry

FIXTURES = Path("contracts/parity/prompt_cases.json")


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def _render(doc: dict, case: dict, tmp_path: Path) -> tuple[str, str]:
    reg_file = tmp_path / f"{case['name']}.yaml"
    reg_file.write_text(doc["registries"][case["registry"]], encoding="utf-8")
    registry = load_registry(reg_file)
    override = doc["overrides"][case["overrides"]]
    return build_prompt(
        case["utterance"],
        registry,
        system_header=override.get("system_header"),
        examples_block=override.get("examples_block"),
    )


def test_prompt_render_cases(doc: dict, tmp_path: Path) -> None:
    assert doc["cases"], "fixture file has no render cases"
    for case in doc["cases"]:
        system, user = _render(doc, case, tmp_path)
        assert system == case["expected_system"], f"{case['name']}: system message changed"
        assert user == case["expected_user"], f"{case['name']}: user message changed"


def test_default_block_cases(doc: dict, tmp_path: Path) -> None:
    """Pin Python's built-in header/examples fallback.

    The Rust side has the same cases as an ignored test: its `DEFAULT_EXAMPLES` carries 4 examples
    against Python's 9. Nothing shipped hits this path — documents, ffmpeg and io all supply a
    `prompt.yaml` examples block — but a newly authored skill or an SDK app without one does, so
    the gap is latent rather than absent.
    """
    for case in doc["default_block_cases"]:
        system, user = _render(doc, case, tmp_path)
        assert system == case["expected_system"], f"{case['name']}: default block changed"
        assert user == case["expected_user"], case["name"]


def test_internal_tools_are_never_listed(doc: dict, tmp_path: Path) -> None:
    """`hidden_tool` exists in the fixture registry purely so this cannot pass vacuously."""
    assert "hidden_tool" in doc["registries"]["prompt_demo"]
    for case in doc["cases"]:
        system, _ = _render(doc, case, tmp_path)
        assert "hidden_tool" not in system, case["name"]


def test_path_normalization_cases(doc: dict, tmp_path: Path) -> None:
    """Pin what normalization does, now that both runtimes use the same rule.

    Q5 (2026-08-09) replaced Python's path-token regex with native's unconditional
    replace-every-backslash. The old rule left ``convert "C:\\My Videos\\clip.mov" to mp4``
    untouched, because splitting on " " breaks a quoted path into fragments that no longer match —
    so the one failure the function exists to prevent survived whenever a Windows path had a space
    in it. It also did not spare the non-paths it was meant to: ``what does A\\B mean`` matched the
    regex and was rewritten anyway.
    """
    for case in doc["path_normalization_cases"]:
        got = normalize_path_separators(case["utterance"])
        assert got == case["python_normalized_utterance"], case["name"]
        system, user = _render(doc, case, tmp_path)
        assert system == case["expected_system"], case["name"]
        assert user == case["expected_user"], case["name"]


def test_every_backslash_is_rewritten(doc: dict) -> None:
    """The rule is unconditional; a fixture keeping a backslash means the rules diverged again."""
    for case in doc["path_normalization_cases"]:
        assert "\\" not in case["python_normalized_utterance"], (
            f"{case['name']}: a backslash survived normalization. If the narrower token rule came "
            f"back, the quoted-path case regresses — see the docstring in knaif.prompt."
        )
        assert case["python_normalized_utterance"] == case["utterance"].replace("\\", "/")
