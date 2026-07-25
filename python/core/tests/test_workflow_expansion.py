"""Tests for the base-layer Option-A additions: ctx.confirm, wait_for_confirmation,
and Skill EXPANDERS.

The ffmpeg skill is the integration test of these features; here we cover the
contract in isolation with tiny inline skills.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from knaif.agent import CommandAgent
from knaif.core_tools import CORE_STEP_MAP
from knaif.handler_api import HandlerContext

# Core control tools are Step classes now; the executor's cmd_* duplicates were removed.
cmd_wait_for_confirmation = CORE_STEP_MAP["wait_for_confirmation"].handle

# ─────────────────────────────────────────────────────────────────────────────
# ctx.confirm()
# ─────────────────────────────────────────────────────────────────────────────


def _ctx(tmp_path, *, confirmed: bool = False, confirmer=None) -> HandlerContext:
    return HandlerContext(
        root=tmp_path,
        sandbox=tmp_path,
        dry_run=False,
        confirmed=confirmed,
        skill_dir=tmp_path,
        confirmer=confirmer,
    )


def test_ctx_confirm_defaults_to_confirmed_flag(tmp_path):
    assert _ctx(tmp_path, confirmed=True).confirm("ok?") is True
    assert _ctx(tmp_path, confirmed=False).confirm("ok?") is False


def test_ctx_confirm_uses_injected_confirmer(tmp_path):
    seen: list[tuple[str, dict | None]] = []

    def confirmer(prompt, preview):
        seen.append((prompt, preview))
        return True

    ctx = _ctx(tmp_path, confirmed=False, confirmer=confirmer)
    assert ctx.confirm("Apply?", {"summary": "ok"}) is True
    assert seen == [("Apply?", {"summary": "ok"})]


def test_wait_for_confirmation_returns_confirmed(tmp_path):
    ctx = _ctx(tmp_path, confirmer=lambda p, pv: True)
    result = cmd_wait_for_confirmation({"prompt": "go?"}, ctx)
    assert result["status"] == "confirmed"


def test_wait_for_confirmation_returns_declined(tmp_path):
    ctx = _ctx(tmp_path, confirmer=lambda p, pv: False)
    result = cmd_wait_for_confirmation({"prompt": "go?"}, ctx)
    assert result["status"] == "declined"


# ─────────────────────────────────────────────────────────────────────────────
# Skill expanders and execute_plan expansion.
# ─────────────────────────────────────────────────────────────────────────────


def _write_demo_skill(skill_dir: Path) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        textwrap.dedent("""\
            name: demo
            description: "Tiny demo skill exercising intents + wait_for_confirmation."
            tools: tools.yaml
            skill_class: handlers.DemoSkill
            """),
        encoding="utf-8",
    )
    (skill_dir / "tools.yaml").write_text(
        textwrap.dedent("""\
            big_op:
              description: "High-level intent expanded into 3 steps."
              required_args: [label]
              safety_category: destructive
            small_op:
              description: "Internal worker step."
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
                    label = args["label"]
                    return [
                        {"tool": "small_op", "args": {"token": f"{label}-a"}},
                        {"tool": "small_op", "args": {"token": f"{label}-b"}},
                        {"tool": "wait_for_confirmation", "args": {"prompt": "ok?"}},
                        {"tool": "small_op", "args": {"token": f"{label}-c"}},
                    ]

            class DemoSkill(Skill):
                tools = [SmallOpStep, BigOpIntent]
            """),
        encoding="utf-8",
    )


def test_expanded_plan_runs_all_steps_when_confirmed(tmp_path):
    skill_dir = tmp_path / "demo_skill"
    _write_demo_skill(skill_dir)
    agent = CommandAgent.from_skill(
        skill_dir,
        sandbox=tmp_path,
        root=tmp_path,
        confirmer=lambda p, pv: True,
    )
    payload = {"plan": [{"tool": "big_op", "args": {"label": "x"}}]}
    results = agent.execute_plan(payload, dry_run=False, confirmed=True)
    tools = [r["tool"] for r in results]
    assert tools == ["small_op", "small_op", "wait_for_confirmation", "small_op"]
    assert results[-1]["result"]["echo"] == "x-c"


def test_expanded_plan_breaks_when_confirmation_declined(tmp_path):
    skill_dir = tmp_path / "demo_skill"
    _write_demo_skill(skill_dir)
    agent = CommandAgent.from_skill(
        skill_dir,
        sandbox=tmp_path,
        root=tmp_path,
        confirmer=lambda p, pv: False,
    )
    payload = {"plan": [{"tool": "big_op", "args": {"label": "x"}}]}
    results = agent.execute_plan(payload, dry_run=False, confirmed=True)
    tools = [r["tool"] for r in results]
    assert tools == ["small_op", "small_op", "wait_for_confirmation"]
    assert results[-1]["result"]["status"] == "declined"


def test_internal_tools_hidden_in_prompt(tmp_path):
    skill_dir = tmp_path / "demo_skill"
    _write_demo_skill(skill_dir)
    agent = CommandAgent.from_skill(skill_dir, sandbox=tmp_path, root=tmp_path)
    system, _ = agent.build_prompt("do the big op")
    assert "big_op" in system
    assert "small_op" not in system


def test_core_tools_merged_into_registry_even_when_not_declared(tmp_path):
    skill_dir = tmp_path / "demo_skill"
    _write_demo_skill(skill_dir)
    agent = CommandAgent.from_skill(skill_dir, sandbox=tmp_path, root=tmp_path)
    assert "clarify" in agent.registry
    assert "reject" in agent.registry
    assert "done" in agent.registry
    assert "wait_for_confirmation" in agent.registry
    assert agent.registry["wait_for_confirmation"].internal is True
