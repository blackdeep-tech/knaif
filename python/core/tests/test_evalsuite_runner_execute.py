"""Tests for run_corpus execute=True mode and the artifact-runner hook."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.runner import AgentOutput, run_corpus
from knaif.skill import Skill

# Load the ffmpeg skill so its handlers module (and _run_artifact) is registered.
_FFMPEG_DIR = Path("skills") / "ffmpeg"
Skill.load(_FFMPEG_DIR)
_ffmpeg_handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
_execute_against_fixture = _ffmpeg_handlers._run_artifact


def _row(
    id: str = "ffmpeg_001",
    utterance: str = "convert to mp4",
    fixture: str = "clip.mp4",
) -> CorpusRow:
    return CorpusRow(
        id=id,
        utterances=[utterance],
        expected_outcome="plan",
        fixture=fixture,
        expected_tool="convert_video",
        tags=["convert"],
    )


def _agent(artifact: str = "ffmpeg -y -i input.mp4 -c:v libx264 out.mp4") -> MagicMock:
    agent = MagicMock()
    agent.infer.return_value = {
        "plan": [{"tool": "convert_video", "args": {"inputs": ["input.mp4"], "container": "mp4"}}]
    }
    exec_result = [
        {
            "tool": "run_batch",
            "result": {"command": artifact.split(), "outputs": [{"output": "out.mp4"}]},
        }
    ]
    agent.execute_plan.return_value = exec_result
    # Default: provide a real artifact_runner so run_corpus exercises the hook.
    agent.artifact_runner = _execute_against_fixture
    return agent


# ── AgentOutput new fields ────────────────────────────────────────────────────


def test_agent_output_has_artifact_path():
    ao = AgentOutput(
        id="x",
        utterance="y",
        plan=None,
        artifact="cmd",
        outcome="plan",
        latency_ms=1.0,
        artifact_path=Path("out.mp4"),
    )
    assert ao.artifact_path == Path("out.mp4")


def test_agent_output_has_utterance_idx():
    ao = AgentOutput(
        id="x",
        utterance="y",
        plan=None,
        artifact=None,
        outcome="plan",
        latency_ms=1.0,
        utterance_idx=2,
    )
    assert ao.utterance_idx == 2


def test_agent_output_defaults():
    ao = AgentOutput(
        id="x",
        utterance="y",
        plan=None,
        artifact=None,
        outcome="plan",
        latency_ms=1.0,
    )
    assert ao.artifact_path is None
    assert ao.utterance_idx == 0


# ── _execute_against_fixture ─────────────────────────────────────────────────


def test_execute_against_fixture_substitutes_input(tmp_path: Path):
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"fake")
    out_dir = tmp_path / "row_001"
    command_str = "ffmpeg -y -i placeholder.mp4 -c:v libx264 out.mp4"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        # Create the expected output file so the function finds it
        out_dir.mkdir()
        (out_dir / "out.mp4").write_bytes(b"out")

        result = _execute_against_fixture(command_str, fixture, out_dir)

    called_cmd = mock_run.call_args[0][0]
    # Input after -i should be the fixture path
    i_idx = called_cmd.index("-i")
    assert called_cmd[i_idx + 1] == str(fixture)
    assert result is not None
    assert result.parent == out_dir


def test_execute_against_fixture_output_in_out_dir(tmp_path: Path):
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"fake")
    out_dir = tmp_path / "row_001"
    command_str = "ffmpeg -y -i placeholder.mp4 -c:v libx264 final.mp4"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        out_dir.mkdir()
        (out_dir / "final.mp4").write_bytes(b"out")
        result = _execute_against_fixture(command_str, fixture, out_dir)

    assert result is not None
    assert result.parent == out_dir


def test_execute_against_fixture_returns_none_on_ffmpeg_failure(tmp_path: Path):
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"fake")
    out_dir = tmp_path / "row_001"
    out_dir.mkdir()
    command_str = "ffmpeg -y -i placeholder.mp4 out.mp4"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        result = _execute_against_fixture(command_str, fixture, out_dir)

    assert result is None


def test_execute_against_fixture_returns_none_on_missing_ffmpeg(tmp_path: Path):
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"fake")
    out_dir = tmp_path / "row_001"
    out_dir.mkdir()
    command_str = "ffmpeg -y -i placeholder.mp4 out.mp4"

    with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
        result = _execute_against_fixture(command_str, fixture, out_dir)

    assert result is None


def test_execute_against_fixture_non_ffmpeg_command_returns_none(tmp_path: Path):
    result = _execute_against_fixture("convert input.png output.jpg", tmp_path / "f.mp4", tmp_path)
    assert result is None


# ── run_corpus execute=True ───────────────────────────────────────────────────


def test_run_corpus_execute_false_leaves_artifact_path_none(tmp_path: Path):
    outputs = run_corpus(_agent(), [_row()], execute=False)
    assert outputs[0].artifact_path is None


def test_run_corpus_execute_true_sets_artifact_path(tmp_path: Path):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    (sandbox / "ffmpeg_001__0").mkdir(parents=True)
    (sandbox / "ffmpeg_001__0" / "out.mp4").write_bytes(b"out")

    agent = _agent()
    agent.artifact_runner = MagicMock(return_value=sandbox / "ffmpeg_001__0" / "out.mp4")

    outputs = run_corpus(
        agent,
        [_row()],
        execute=True,
        sandbox=sandbox,
        fixture_dir=fixture_dir,
    )

    assert outputs[0].artifact_path is not None


def test_run_corpus_execute_true_creates_per_row_dirs(tmp_path: Path):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    created_dirs: list[Path] = []

    def _fake_exec(cmd, fixture, out_dir):
        created_dirs.append(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / "out.mp4"
        f.write_bytes(b"out")
        return f

    agent = _agent()
    agent.artifact_runner = _fake_exec
    run_corpus(agent, [_row()], execute=True, sandbox=sandbox, fixture_dir=fixture_dir)

    assert len(created_dirs) == 1
    assert created_dirs[0] == sandbox / "ffmpeg_001__0"


def test_run_corpus_execute_multiple_utterances(tmp_path: Path):
    row = CorpusRow(
        id="r001",
        utterances=["convert to mp4", "make it mp4", "save as mp4"],
        expected_outcome="plan",
        fixture="clip.mp4",
        expected_tool="convert_video",
        tags=["convert"],
    )
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")
    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    def _fake_exec(cmd, fixture, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / "out.mp4"
        f.write_bytes(b"out")
        return f

    agent = _agent()
    agent.artifact_runner = _fake_exec
    outputs = run_corpus(agent, [row], execute=True, sandbox=sandbox, fixture_dir=fixture_dir)

    # One output per utterance
    assert len(outputs) == 3
    assert [o.utterance_idx for o in outputs] == [0, 1, 2]
    assert [o.utterance for o in outputs] == ["convert to mp4", "make it mp4", "save as mp4"]


def test_run_corpus_execute_no_fixture_skips_execute(tmp_path: Path):
    row = CorpusRow(
        id="r001",
        utterances=["do something"],
        expected_outcome="clarify",
        fixture=None,
        tags=[],
    )
    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    agent = _agent()
    mock_exec = MagicMock()
    agent.artifact_runner = mock_exec
    outputs = run_corpus(
        agent,
        [row],
        execute=True,
        sandbox=sandbox,
        fixture_dir=tmp_path / "fixtures",
    )

    mock_exec.assert_not_called()
    assert outputs[0].artifact_path is None


# ── single-final-output chains must execute every command, not just the last ──────────────
#
# Fix review, pre-existing finding: only rows DECLARING multiple `outputs` took the chain
# branch. A two-intent plan with one final deliverable (ffmpeg_273: rotate → compress) fell
# to the single-artifact path, which runs only the LAST command and rewrites its input back
# to the original fixture — so the rotation never happened, the materialized file was
# unrotated, and `filter:transpose` was missing from the recorded command. The model's plan
# was correct; the harness under-measured it.


def _chain_agent(commands: list[str]) -> MagicMock:
    """An agent whose plan expands to one run_batch result per intent, in order."""
    agent = MagicMock()
    agent.infer.return_value = {
        "plan": [
            {"tool": "rotate_video", "args": {"inputs": ["clip.mp4"], "angle": 90}},
            {"tool": "compress_video", "args": {"inputs": ["clip_rotated.mp4"]}},
        ]
    }
    agent.execute_plan.return_value = [
        {"tool": "run_batch", "result": {"command": c.split()}} for c in commands
    ]
    agent.artifact_runner = _execute_against_fixture
    return agent


def test_single_output_chain_runs_every_command(tmp_path: Path):
    """A multi-command plan with no declared `outputs` must still run as a chain."""
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fixture")
    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    commands = [
        "ffmpeg -y -i clip.mp4 -vf transpose=1 clip_rotated.mp4",
        "ffmpeg -y -i clip_rotated.mp4 -c:v libx264 clip_out.mp4",
    ]
    agent = _chain_agent(commands)

    with patch("knaif.evalsuite.runner.run_command_chain") as chained:
        chained.return_value = [
            {"command": commands[0], "returncode": 0, "stderr": "", "output": "clip_rotated.mp4"},
            {"command": commands[1], "returncode": 0, "stderr": "", "output": "clip_out.mp4"},
        ]
        run_corpus(
            agent,
            [_row()],
            execute=True,
            sandbox=sandbox,
            fixture_dir=fixture_dir,
        )

    assert chained.called, "a multi-command plan must go through run_command_chain"
    passed_commands = chained.call_args[0][0]
    assert passed_commands == commands, f"every command must be chained, got {passed_commands}"


def test_chain_records_every_command_for_text_criteria(tmp_path: Path):
    """`artifact_commands` carries the whole chain so command-text criteria (filters/flags)
    can see a filter applied in an EARLIER step, not only the final command."""
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fixture")
    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    commands = [
        "ffmpeg -y -i clip.mp4 -vf transpose=1 clip_rotated.mp4",
        "ffmpeg -y -i clip_rotated.mp4 -c:v libx264 clip_out.mp4",
    ]
    agent = _chain_agent(commands)

    with patch("knaif.evalsuite.runner.run_command_chain") as chained:
        chained.return_value = [
            {"command": commands[0], "returncode": 0, "stderr": "", "output": "clip_rotated.mp4"},
            {"command": commands[1], "returncode": 0, "stderr": "", "output": "clip_out.mp4"},
        ]
        outputs = run_corpus(
            agent,
            [_row()],
            execute=True,
            sandbox=sandbox,
            fixture_dir=fixture_dir,
        )

    assert outputs[0].artifact_commands == commands
    # The single-command `artifact` keeps its meaning: the command producing the deliverable.
    assert outputs[0].artifact == commands[-1]


def test_single_command_plan_still_uses_the_artifact_runner(tmp_path: Path):
    """Regression guard: one-command plans must keep the existing artifact_runner path."""
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fixture")
    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    agent = _agent()
    mock_exec = MagicMock(return_value=None)
    agent.artifact_runner = mock_exec

    with patch("knaif.evalsuite.runner.run_command_chain") as chained:
        outputs = run_corpus(
            agent,
            [_row()],
            execute=True,
            sandbox=sandbox,
            fixture_dir=fixture_dir,
        )

    assert not chained.called, "a single-command plan must not take the chain path"
    mock_exec.assert_called_once()
    assert outputs[0].artifact_commands == [outputs[0].artifact]
