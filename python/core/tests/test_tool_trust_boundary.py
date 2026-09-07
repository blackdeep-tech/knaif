"""Tests for the F1/F2 trust-boundary fix.

Ref: docs/audits/2026-09-07-core-principles-and-rtx5080.md

F1 (critical): ``internal: true`` only hid a tool from the model's prompt.
``validate_step`` still accepted it against the full registry from any caller —
including a plan straight from ``agent.infer()`` — so a step naming ffmpeg's
internal ``run_preview``/``run_batch`` reached ``subprocess.run`` with a
caller/model-controlled argv.

F2 (high): the destructive-intent check read the *expanded leaf's*
``safety_category``, never the originating intent's. A ``destructive`` intent
(e.g. ``strip_audio``) that expands into ``safe`` leaves (ffmpeg's internal
run/verify steps) therefore executed side effects with ``confirmed=False``.

The two literal ffmpeg reproductions from the audit (real `run_preview` argv
execution, real `strip_audio` side effect) live in
``skills/ffmpeg/python/tests/test_trust_boundary_ffmpeg.py`` instead of here —
core tests must not shell out to ffmpeg (see conftest.py's `_no_media_binaries`).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from knaif.agent import CommandAgent


def _write_demo_skill(skill_dir: Path) -> None:
    """A destructive top-level intent that expands into one *safe*, *internal* leaf —
    the same shape as ffmpeg's `strip_audio` (destructive) -> `run_batch` (internal, safe).
    """
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        textwrap.dedent("""\
            name: demo
            description: "Tiny demo skill exercising the intent/leaf safety boundary."
            tools: tools.yaml
            skill_class: handlers.DemoSkill
            """),
        encoding="utf-8",
    )
    (skill_dir / "tools.yaml").write_text(
        textwrap.dedent("""\
            big_op:
              description: "Destructive high-level intent expanded into one safe leaf."
              required_args: [label]
              safety_category: destructive
            small_op:
              description: "Internal worker step — safe on its own, but reachable only
                through big_op's expansion."
              required_args: [token]
              internal: true
            """),
        encoding="utf-8",
    )
    (skill_dir / "handlers.py").write_text(
        textwrap.dedent("""\
            from knaif.skill_base import Skill
            from knaif.tool import Intent, Step

            class SmallOpStep(Step):
                name = "small_op"
                def handle(self, args, ctx):
                    return {"echo": args["token"]}

            class BigOpIntent(Intent):
                name = "big_op"
                def expand(self, args):
                    return [{"tool": "small_op", "args": {"token": args["label"]}}]

            class DemoSkill(Skill):
                tools = [SmallOpStep, BigOpIntent]
            """),
        encoding="utf-8",
    )


def _demo_agent(tmp_path: Path) -> CommandAgent:
    skill_dir = tmp_path / "demo_skill"
    _write_demo_skill(skill_dir)
    return CommandAgent.from_skill(skill_dir, sandbox=tmp_path, root=tmp_path)


# ── F1: internal tools are not model/caller-proposable ─────────────────────────


def test_validate_plan_rejects_internal_tool_at_top_level(tmp_path):
    agent = _demo_agent(tmp_path)
    payload = {"plan": [{"tool": "small_op", "args": {"token": "x"}}]}
    with pytest.raises(ValueError, match="internal"):
        agent.validate_plan(payload)


def test_execute_plan_rejects_internal_tool_named_directly(tmp_path):
    """The F1 shape: a caller (or model output) hands execute_plan a plan naming an
    internal tool directly, bypassing Intent.expand() entirely."""
    agent = _demo_agent(tmp_path)
    payload = {"plan": [{"tool": "small_op", "args": {"token": "x"}}]}
    with pytest.raises(ValueError):
        agent.execute_plan(payload, dry_run=False, confirmed=False)


def test_expanded_internal_leaf_still_executes(tmp_path):
    """Regression guard: Intent.expand() output (trusted, deterministic) must still
    be allowed to invoke internal leaf steps."""
    agent = _demo_agent(tmp_path)
    payload = {"plan": [{"tool": "big_op", "args": {"label": "x"}}]}
    results = agent.execute_plan(payload, dry_run=False, confirmed=True)
    assert results[-1]["result"]["echo"] == "x"


# ── F2: a destructive intent's safety requirement survives expansion ───────────


def test_destructive_intent_expanding_to_safe_leaf_blocked_without_confirmation(tmp_path):
    agent = _demo_agent(tmp_path)
    payload = {"plan": [{"tool": "big_op", "args": {"label": "x"}}]}
    with pytest.raises(ValueError, match="confirmed"):
        agent.execute_plan(payload, dry_run=False, confirmed=False)


def test_destructive_intent_dry_run_previews_without_confirmation(tmp_path):
    agent = _demo_agent(tmp_path)
    payload = {"plan": [{"tool": "big_op", "args": {"label": "x"}}]}
    results = agent.execute_plan(payload, dry_run=True, confirmed=False)
    assert results[-1]["result"]["echo"] == "x"


def test_destructive_intent_executes_when_confirmed(tmp_path):
    agent = _demo_agent(tmp_path)
    payload = {"plan": [{"tool": "big_op", "args": {"label": "x"}}]}
    results = agent.execute_plan(payload, dry_run=False, confirmed=True)
    assert results[-1]["result"]["echo"] == "x"


def test_direct_destructive_leaf_still_blocked(tmp_path):
    """Leaf-level enforcement is additive, not replaced: a directly-destructive
    step (not behind an intent) must still require confirmation."""
    skill_dir = tmp_path / "direct_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        textwrap.dedent("""\
            name: direct
            description: "A single directly-destructive tool, no intent wrapper."
            tools: tools.yaml
            skill_class: handlers.DirectSkill
            """),
        encoding="utf-8",
    )
    (skill_dir / "tools.yaml").write_text(
        textwrap.dedent("""\
            wipe:
              description: "Directly destructive, top-level tool."
              required_args: [token]
              safety_category: destructive
            """),
        encoding="utf-8",
    )
    (skill_dir / "handlers.py").write_text(
        textwrap.dedent("""\
            from knaif.skill_base import Skill
            from knaif.tool import Step

            class WipeStep(Step):
                name = "wipe"
                def handle(self, args, ctx):
                    return {"echo": args["token"]}

            class DirectSkill(Skill):
                tools = [WipeStep]
            """),
        encoding="utf-8",
    )
    agent = CommandAgent.from_skill(skill_dir, sandbox=tmp_path, root=tmp_path)
    payload = {"plan": [{"tool": "wipe", "args": {"token": "x"}}]}
    with pytest.raises(ValueError, match="confirmed"):
        agent.execute_plan(payload, dry_run=False, confirmed=False)
