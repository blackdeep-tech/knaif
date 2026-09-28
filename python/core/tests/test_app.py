"""Direct tests for the knaif-cli command surface (python/core/knaif/app.py)."""

from __future__ import annotations

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
