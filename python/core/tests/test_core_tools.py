"""Tests for knaif.core_tools — built-in Step classes for control flow."""

from __future__ import annotations

from unittest.mock import MagicMock

from knaif.core_tools import (
    CORE_STEP_MAP,
    CORE_STEPS,
    CORE_TOOL_DEFS,
    ClarifyStep,
    DoneStep,
    RejectStep,
    WaitForConfirmationStep,
)
from knaif.tool import Step

# ── class shape ──────────────────────────────────────────────────────────────


def test_all_core_steps_are_step_subclasses():
    for cls in CORE_STEPS:
        assert issubclass(cls, Step), f"{cls} is not a Step subclass"


def test_core_step_names():
    assert ClarifyStep.name == "clarify"
    assert RejectStep.name == "reject"
    assert DoneStep.name == "done"
    assert WaitForConfirmationStep.name == "wait_for_confirmation"


def test_core_step_map_keys():
    assert set(CORE_STEP_MAP) == {"clarify", "reject", "done", "wait_for_confirmation"}


def test_core_step_map_values_are_instances():
    for name, obj in CORE_STEP_MAP.items():
        assert isinstance(obj, Step), f"CORE_STEP_MAP[{name!r}] is not a Step instance"


# ── metadata loaded from core_tools.yaml ─────────────────────────────────────


def test_core_tool_defs_loaded_from_yaml():
    """CORE_TOOL_DEFS is parsed from core_tools.yaml via the registry loader."""
    from knaif.registry import ToolDef

    assert set(CORE_TOOL_DEFS) == {"clarify", "reject", "done", "wait_for_confirmation"}
    assert all(isinstance(td, ToolDef) for td in CORE_TOOL_DEFS.values())


def test_core_tool_defs_match_step_map():
    """Every core Step has a metadata entry and vice versa (no drift)."""
    assert set(CORE_TOOL_DEFS) == set(CORE_STEP_MAP)


def test_core_tool_defs_fields():
    assert CORE_TOOL_DEFS["clarify"].required_args == ("question",)
    assert CORE_TOOL_DEFS["reject"].required_args == ("reason",)
    assert CORE_TOOL_DEFS["done"].required_args == ()
    wfc = CORE_TOOL_DEFS["wait_for_confirmation"]
    assert wfc.internal is True
    assert wfc.optional_args == ("prompt", "preview")
    assert "clarify" in CORE_TOOL_DEFS["clarify"].keywords


# ── the reject/clarify split ─────────────────────────────────────────────────
# One word was doing two jobs: `reject` read "unsafe or out-of-scope", which bundled a
# safety verdict with an inventory gap and contradicted every skill prompt's TOOL SCOPE
# rule ("if it maps to no tool → clarify"). The model resolved the contradiction by
# rejecting anything it could not do. These two tests pin the split so it cannot drift
# back. See docs/plans/2026-09-11-reject-clarify-taxonomy.md.


def test_reject_is_defined_by_safety_not_by_scope():
    """`reject` means the active skill's safety policy was violated — nothing else."""
    desc = CORE_TOOL_DEFS["reject"].description.lower()
    assert "safety policy" in desc
    assert "out-of-scope" not in desc
    assert "out of scope" not in desc


def test_clarify_covers_unsupported_requests_not_only_ambiguity():
    """The other half of the split: a request a skill has no tool for is a clarify."""
    desc = CORE_TOOL_DEFS["clarify"].description.lower()
    assert "clarif" in desc
    assert "not supported" in desc


def test_core_tool_descriptions_name_no_skill():
    """The contract binds every skill and SDK consumer, so it stays domain-agnostic.

    Which categories count as a safety violation is *skill* policy and belongs in
    `skills/<name>/prompt.yaml` + SPEC.md — not here (AGENTS.md: keep core
    domain-agnostic, do not hard-code skill-specific safety rules in core).
    """
    joined = " ".join(td.description.lower() for td in CORE_TOOL_DEFS.values())
    for word in ("ffmpeg", "video", "document", "pdf", "email", "upload", "download"):
        assert word not in joined, f"core contract mentions {word!r} — that is skill policy"


# ── behavior matches original CORE_HANDLERS functions ────────────────────────


def _ctx(confirmed=True):
    ctx = MagicMock()
    ctx.confirm.return_value = confirmed
    return ctx


def test_clarify_returns_question():
    result = ClarifyStep().handle({"question": "What format?"}, _ctx())
    assert result == {"status": "clarification_needed", "question": "What format?"}


def test_reject_returns_reason():
    result = RejectStep().handle({"reason": "Too dangerous"}, _ctx())
    assert result == {"status": "rejected", "reason": "Too dangerous"}


def test_done_returns_done():
    result = DoneStep().handle({}, _ctx())
    assert result == {"status": "done"}


def test_wait_for_confirmation_confirmed():
    ctx = _ctx(confirmed=True)
    ctx.confirm.return_value = True
    result = WaitForConfirmationStep().handle({"prompt": "OK?"}, ctx)
    assert result == {"status": "confirmed", "prompt": "OK?"}


def test_wait_for_confirmation_declined():
    ctx = _ctx(confirmed=False)
    ctx.confirm.return_value = False
    result = WaitForConfirmationStep().handle({"prompt": "OK?"}, ctx)
    assert result == {"status": "declined", "prompt": "OK?"}


def test_wait_for_confirmation_default_prompt():
    ctx = _ctx(confirmed=True)
    ctx.confirm.return_value = True
    result = WaitForConfirmationStep().handle({}, ctx)
    assert result["prompt"] == "Proceed?"


def test_wait_for_confirmation_passes_dict_preview():
    ctx = MagicMock()
    ctx.confirm.return_value = True
    preview = {"cmd": "rm -rf /"}
    WaitForConfirmationStep().handle({"prompt": "Sure?", "preview": preview}, ctx)
    ctx.confirm.assert_called_once_with("Sure?", preview)


def test_wait_for_confirmation_ignores_non_dict_preview():
    ctx = MagicMock()
    ctx.confirm.return_value = True
    WaitForConfirmationStep().handle({"prompt": "Sure?", "preview": "bad"}, ctx)
    ctx.confirm.assert_called_once_with("Sure?", None)
