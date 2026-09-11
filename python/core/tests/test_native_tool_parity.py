"""Every public tool a skill declares must exist in the native runtime too.

The requirement is simple to state and was not checkable: `skills/<name>/tools.yaml` is the
one contract both runtimes read, so a tool declared there and unimplemented in Rust is a
capability the shipped binary silently lacks. That is not hypothetical — `reverse_video` was
exactly this. The native engine had implemented and tested `mode = "reverse"` all along; only
the dispatch arm was missing, so the tool was unreachable, and the first L4 run recorded it as
25 execution errors rather than as a missing capability.

Each native crate declares what it dispatches (`is_supported`); this asserts that declaration
covers the bundle. A tool added to `tools.yaml` without a native implementation fails here
rather than in front of a user.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from knaif.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Skills that ship natively, and the source declaring what their crate dispatches.
NATIVE_SKILLS = {
    "ffmpeg": "skills/ffmpeg/native/src/run.rs",
    "documents": "skills/documents/native/src/run.rs",
}

#: Control tools are provided by the host, not the skill crate.
CONTROL_TOOLS = {"clarify", "reject", "done", "wait_for_confirmation"}


def _public_tools(skill: str) -> set[str]:
    registry = load_registry(REPO_ROOT / "skills" / skill / "tools.yaml")
    return {
        name
        for name, tool in registry.items()
        if not getattr(tool, "internal", False) and name not in CONTROL_TOOLS
    }


def _declared_native(skill: str) -> set[str]:
    src = (REPO_ROOT / NATIVE_SKILLS[skill]).read_text(encoding="utf-8")
    body = re.search(r"pub fn is_supported\(tool: &str\) -> bool \{(.*?)\n\}", src, re.S)
    assert body, f"{NATIVE_SKILLS[skill]} does not declare `is_supported`"
    return set(re.findall(r'"([a-z_]+)"', body.group(1)))


@pytest.mark.parametrize("skill", sorted(NATIVE_SKILLS))
def test_every_public_tool_is_implemented_natively(skill: str) -> None:
    missing = _public_tools(skill) - _declared_native(skill)
    assert not missing, (
        f"{skill}: declared in tools.yaml but not implemented in the native runtime: "
        f"{sorted(missing)}. Both runtimes read the same contract, so a tool that exists only "
        "in Python is a capability the shipped binary does not have."
    )


@pytest.mark.parametrize("skill", sorted(NATIVE_SKILLS))
def test_the_native_runtime_claims_no_tool_the_bundle_does_not_declare(skill: str) -> None:
    """The other direction: a stale name in `is_supported` would make the guard above pass
    while pointing at nothing."""
    extra = _declared_native(skill) - _public_tools(skill) - CONTROL_TOOLS
    assert (
        not extra
    ), f"{skill}: `is_supported` names tools the bundle does not declare: {sorted(extra)}"
