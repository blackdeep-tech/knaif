"""Tests for the ffmpeg skill PREFLIGHTS dict and preflight integration."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from knaif.agent import CommandAgent
from knaif.skill import Skill

SKILL_DIR = Path(__file__).parents[2]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def skill() -> Skill:
    return Skill.load(SKILL_DIR)


@pytest.fixture()
def preflight_fn(skill):
    """Return the _preflight_inputs function from the loaded handlers module."""
    # The module is registered in sys.modules after Skill.load().
    handlers_mod = sys.modules.get("_skill_oop_ffmpeg_handlers")
    assert handlers_mod is not None, "_skill_oop_ffmpeg_handlers module not loaded"
    return handlers_mod._preflight_inputs


# ─────────────────────────────────────────────────────────────────────────────
# Coverage test
# ─────────────────────────────────────────────────────────────────────────────


def test_preflights_exported(skill):
    """Every tool is covered by the skill-level preflight (the OOP replacement
    for the legacy '*' wildcard). FFmpegSkill must override Skill.preflight."""
    from knaif.skill_base import Skill as SkillBase

    assert skill.skill_instance is not None
    assert (
        type(skill.skill_instance).preflight is not SkillBase.preflight
    ), "FFmpegSkill must override preflight (was the '*' wildcard)"


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests for _preflight_inputs
# ─────────────────────────────────────────────────────────────────────────────


def test_preflight_inputs_missing_file(preflight_fn, tmp_path):
    """A non-existent file path returns a non-empty error list containing 'not found'."""
    errs = preflight_fn(
        {"inputs": "missing_video.mp4"},
        root=tmp_path,
        sandbox=tmp_path,
    )
    assert errs, "expected at least one error for a missing file"
    assert any("not found" in e for e in errs)


def test_preflight_inputs_existing_file(preflight_fn, tmp_path):
    """An existing file path returns an empty error list."""
    real_file = tmp_path / "exists.mp4"
    real_file.write_bytes(b"")
    errs = preflight_fn(
        {"inputs": "exists.mp4"},
        root=tmp_path,
        sandbox=tmp_path,
    )
    assert errs == []


def test_preflight_inputs_skips_var_refs(preflight_fn, tmp_path):
    """Variable references (starting with '$') are silently skipped."""
    errs = preflight_fn(
        {"inputs": "$files"},
        root=tmp_path,
        sandbox=tmp_path,
    )
    assert errs == []


def test_preflight_inputs_uses_input_key(preflight_fn, tmp_path):
    """trim_video uses 'input' (singular) — the function must handle that key too."""
    errs = preflight_fn(
        {"input": "missing_clip.mp4"},
        root=tmp_path,
        sandbox=tmp_path,
    )
    assert errs, "expected an error for a missing file under 'input' key"
    assert any("not found" in e for e in errs)


def test_preflight_inputs_exact_planned_output_ok(preflight_fn, tmp_path):
    """An intermediate that matches an earlier step's output exactly is accepted."""
    errs = preflight_fn(
        {"inputs": ["clip_trimmed.mp4"]},
        root=tmp_path,
        sandbox=tmp_path,
        planned_output_names={"clip_trimmed.mp4"},
    )
    assert errs == []


def test_preflight_inputs_chaining_mismatch_suggests_earlier_output(preflight_fn, tmp_path):
    """A near-miss intermediate (typo of an earlier step's output) yields a
    chaining-aware error suggesting the produced filename — not a generic
    'model dropped the directory' hint.
    """
    errs = preflight_fn(
        {"inputs": ["clip_trimed.mp4"]},  # typo of clip_trimmed.mp4
        root=tmp_path,
        sandbox=tmp_path,
        planned_output_names={"clip_trimmed.mp4"},
    )
    assert errs
    joined = " ".join(errs)
    assert "clip_trimmed.mp4" in joined  # suggests the real produced output
    assert "earlier step" in joined
    assert "dropped the directory" not in joined


def test_preflight_inputs_empty_args(preflight_fn, tmp_path):
    """When neither 'inputs' nor 'input' is present the function returns []."""
    errs = preflight_fn({}, root=tmp_path, sandbox=tmp_path)
    assert errs == []


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests via CommandAgent
# ─────────────────────────────────────────────────────────────────────────────


def _make_agent(tmp_path: Path, confirmer: Any) -> CommandAgent:
    """Return a CommandAgent wired to the ffmpeg skill with approval gate on."""
    return CommandAgent.from_skill(
        SKILL_DIR,
        sandbox=tmp_path,
        require_approval=True,
        plan_confirmer=confirmer,
    )


def test_execute_plan_preflight_aborts_before_approval(tmp_path):
    """Preflight raises ValueError for a missing file BEFORE the approval gate fires."""
    approval_calls: list[str] = []

    def recording_confirmer(summary: str) -> bool:
        approval_calls.append(summary)
        return True

    agent = _make_agent(tmp_path, recording_confirmer)
    payload: dict[str, Any] = {
        "plan": [
            {
                "tool": "compress_video",
                "args": {"inputs": ["no_such_file.mp4"]},
            }
        ]
    }

    results = agent.execute_plan(payload, dry_run=False, confirmed=True)

    # Preflight converts the missing-file error to a clarify outcome.
    assert results[0]["tool"] == "clarify"
    assert results[0]["result"]["status"] == "clarification_needed"
    assert "not found" in results[0]["result"]["question"]

    # The approval gate must never have been reached.
    assert approval_calls == [], "plan_confirmer should not be called when preflight fails"


def test_execute_plan_preflight_skipped_in_dry_run(tmp_path):
    """dry_run=True bypasses preflights — no error even for a missing file."""
    agent = _make_agent(tmp_path, lambda _: False)  # confirmer declines (returns [])
    payload: dict[str, Any] = {
        "plan": [
            {
                "tool": "compress_video",
                "args": {"inputs": ["no_such_file.mp4"]},
            }
        ]
    }

    # dry_run=True — preflights are skipped; confirmer declines so [] is returned.
    result = agent.execute_plan(payload, dry_run=True, confirmed=False)
    # No ValueError raised — preflight was skipped.
    # The plan_confirmer declined, so execution returns [].
    assert result == []
