"""Direct tests for the knaif-cli command surface (python/core/knaif/app.py)."""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from knaif.app import cli
from knaif.models import build_orchestrator


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── skills ───────────────────────────────────────────────────────────────────


def test_skills_lists_builtin_skills(runner):
    result = runner.invoke(cli, ["skills"])
    assert result.exit_code == 0
    assert "ffmpeg" in result.output
    assert "documents" in result.output
    # io is `status: stale` — hidden from the listing (still loadable explicitly).
    assert "io" not in result.output


# ── run (mock backend, no model needed) ───────────────────────────────────────


def test_run_mock_dry_run_succeeds(runner):
    result = runner.invoke(
        cli,
        [
            "run",
            "ffmpeg",
            "convert",
            "test1.mov",
            "to",
            "mp4",
            "--backend",
            "mock",
            "--dry-run",
            "--auto-approve",
        ],
    )
    assert result.exit_code == 0
    assert "ffmpeg" in result.output


def test_run_silent_suppresses_output(runner):
    result = runner.invoke(
        cli,
        [
            "run",
            "ffmpeg",
            "convert",
            "test1.mov",
            "to",
            "mp4",
            "--backend",
            "mock",
            "--dry-run",
            "--silent",
        ],
    )
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_run_unknown_skill_errors(runner):
    result = runner.invoke(
        cli,
        ["run", "nonexistent_skill", "do", "something", "--backend", "mock"],
    )
    assert result.exit_code == 1
    assert "Error" in result.output


# ── --backend honesty (unit-level on build_orchestrator) ──────────────────────


def _registry() -> dict:
    return {
        "default": "llama-model",
        "models": {
            "llama-model": {"backend": "llama_cpp", "options": {}},
            "ollama-model": {"backend": "ollama", "model": "qwen3:4b"},
        },
    }


def test_backend_mismatch_is_rejected():
    with pytest.raises(RuntimeError, match="backend"):
        build_orchestrator(name="llama-model", registry=_registry(), backend="ollama")


def test_backend_ollama_with_model_path_is_rejected():
    with pytest.raises(RuntimeError, match="model-path"):
        build_orchestrator(model_path="model.gguf", backend="ollama")


# ── the CLI plans with the retrieved registry, like the eval lane and native ──────────────


def _capture_infer(monkeypatch):
    from knaif.agent import CommandAgent

    seen: list = []
    original = CommandAgent.infer

    def spy(self, utterance, **kwargs):
        seen.append((self, utterance, kwargs.get("registry_override")))
        return original(self, utterance, **kwargs)

    monkeypatch.setattr(CommandAgent, "infer", spy)
    return seen


def _expected_tools(agent, utterance: str) -> set[str]:
    from knaif.registry import retrieve_tools

    return set(retrieve_tools(utterance, agent.registry))


@pytest.mark.parametrize("command", ["run", "plan"])
def test_the_cli_shows_the_model_the_retrieved_tools(runner, monkeypatch, command):
    """`knaif-cli run`/`plan` called `agent.infer` with no `registry_override`, so the model saw
    every tool and unfiltered examples, while the eval lane and the native binary both retrieve
    first. The shipped Python CLI planned with a prompt nobody evaluated, and L3 (which drives
    this CLI) measured 11.9% plan disagreement against native where the eval lanes differ by
    1.97% (2026-09-27, evals/parity/2026-09-27_r5c-l3-4b-ffmpeg)."""
    seen = _capture_infer(monkeypatch)
    utterance = "encode clip.mp4 at crf 22"
    args = [command, "ffmpeg", *utterance.split(), "--backend", "mock"]
    if command == "run":
        args += ["--dry-run", "--auto-approve"]

    result = runner.invoke(cli, args)

    assert result.exit_code == 0, result.output
    agent, said, override = seen[0]
    assert said == utterance
    assert override is not None, "the CLI must pass the retrieved registry"
    assert set(override) == _expected_tools(agent, utterance)


# ── the argv dump (L3 compares it) ────────────────────────────────────────────────────────


def test_a_concat_command_is_dumped_once() -> None:
    """`run_concat` stores its one command twice (top level and in `outputs`); ffmpeg runs once,
    and the dump must say so. Dumped twice, every concat row read as a port bug in R5c L3
    (2026-09-28: 9 of 9 ffmpeg port bugs)."""
    from knaif.app import rendered_argvs

    cmd = ["ffmpeg", "-y", "-i", "a.mp4", "-i", "b.mp4", "combined.mp4"]
    results = [
        {
            "tool": "run_concat",
            "result": {
                "mode": "dry_run",
                "outputs": [
                    {"input": ["a.mp4", "b.mp4"], "output": "combined.mp4", "command": cmd}
                ],
                "command": cmd,
            },
        }
    ]
    assert rendered_argvs(results) == [cmd]


def test_a_batch_that_really_runs_a_command_twice_is_dumped_twice() -> None:
    """Commands are counted where they run, not de-duplicated by value."""
    from knaif.app import rendered_argvs

    cmd = ["ffmpeg", "-y", "-i", "a.mp4", "a_out.mp4"]
    results = [{"tool": "run_batch", "result": {"outputs": [{"command": cmd}, {"command": cmd}]}}]
    assert rendered_argvs(results) == [cmd, cmd]


def test_a_result_with_only_a_top_level_command_is_dumped() -> None:
    from knaif.app import rendered_argvs

    cmd = ["ffmpeg", "-y", "-i", "a.mp4", "b.mp4"]
    assert rendered_argvs([{"tool": "run_concat", "result": {"command": cmd}}]) == [cmd]


# ── 1.3.0: act without asking; ask before replacing a file ─────────────────────


def test_a_run_asks_only_when_confirm_is_opted_into():
    from knaif.app import approval_required

    assert approval_required(auto_approve=None, confirm=False) is False  # the default acts
    assert approval_required(auto_approve=None, confirm=True) is True
    assert approval_required(auto_approve=True, confirm=True) is False  # -y skips the question
    assert approval_required(auto_approve=False, confirm=False) is True  # -Y still asks


def test_planned_outputs_are_read_from_a_preview(tmp_path):
    from knaif.app import planned_outputs

    there = tmp_path / "there.mp4"
    there.write_bytes(b"x")
    gone = tmp_path / "gone.mp4"
    results = [
        {"tool": "run_ffmpeg", "result": {"command": ["ffmpeg", "-y", "-i", "a", str(there)]}},
        {"tool": "x", "result": {"output": str(gone), "outputs": [str(there)]}},
        {"tool": "y", "result": {"preview_output": str(gone)}},
        {"tool": "z", "result": None},
    ]
    assert planned_outputs(results) == [str(there)]


def test_overwrite_gate_asks_with_no_as_the_default():
    from knaif.app import overwrite_gate

    asked: list[tuple[str, bool]] = []

    def ask(question: str, default_yes: bool) -> bool | None:
        asked.append((question, default_yes))
        return True

    assert overwrite_gate([], False, ask) is True
    assert overwrite_gate(["out.mp4"], True, ask) is True
    assert asked == [], "nothing to replace, or --overwrite: no question"
    assert overwrite_gate(["out.mp4"], False, ask) is True
    assert asked == [("Replace out.mp4?", False)]
    assert overwrite_gate(["a", "b"], False, lambda q, d: False) is False


def test_overwrite_gate_without_a_terminal_names_the_flag():
    from knaif.app import overwrite_gate

    with pytest.raises(click.ClickException, match="--overwrite"):
        overwrite_gate(["out.mp4"], False, lambda q, d: None)


def test_run_accepts_confirm_and_overwrite(runner):
    result = runner.invoke(
        cli,
        ["run", "ffmpeg", "convert", "a.mov", "to", "mp4", "--backend", "mock"]
        + ["--dry-run", "--confirm", "--overwrite"],
    )
    assert result.exit_code == 0, result.output


def test_a_real_preview_finds_an_existing_ffmpeg_output(tmp_path, monkeypatch):
    """The gate reads a dry-run of the plan, so it must see what ffmpeg's expansion would write."""
    from knaif import create_agent
    from knaif.app import _existing_outputs

    monkeypatch.chdir(tmp_path)
    (tmp_path / "clip.mp4").write_bytes(b"x")
    (tmp_path / "silent.mp4").write_bytes(b"precious")
    agent = create_agent("ffmpeg", sandbox=str(tmp_path))
    plan = {
        "plan": [{"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "silent.mp4"}}]
    }
    found = _existing_outputs(agent, plan, "strip audio from clip.mp4")
    assert [Path(p).name for p in found] == ["silent.mp4"]
    assert (tmp_path / "silent.mp4").read_bytes() == b"precious"
