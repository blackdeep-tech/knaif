"""Tests for run_corpus execute=True mode and the artifact-runner hook."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.runner import AgentOutput, run_corpus
from knaif.skill import Skill

# Load the ffmpeg skill so its handlers module is registered.
#
# ffmpeg no longer supplies an `artifact_runner`: every one of its artifacts is a command
# line, and those go through `run_command_chain` now (T5b). `Skill.run_artifact` remains the
# hook for a skill whose artifact is not a command — `documents` hands over a JSON payload —
# so the tests below stub it rather than borrowing ffmpeg's.
_FFMPEG_DIR = Path("skills") / "ffmpeg"
Skill.load(_FFMPEG_DIR)
_ffmpeg_handlers = sys.modules["_skill_oop_ffmpeg_handlers"]


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
    agent.artifact_runner = MagicMock(return_value=None)
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


# ── the retired artifact runner ──────────────────────────────────────────────
#
# Five tests lived here pinning `_run_artifact`: that it substituted `-i` with the fixture
# path, that it redirected the output into a separate directory, and that it returned None
# on a non-zero exit, a missing binary and a non-ffmpeg command alike.
#
# Every one of those is a behaviour T5b removed on purpose. The substitution is what stopped
# Python executing the command the plan rendered, and the single None is what made a failed
# command indistinguishable from four other things while the row still scored as correct.
#
# The coverage moved rather than disappearing: path resolution is pinned in
# test_evalsuite_harness_fidelity.py (one rule, directories preserved, a collision surviving
# into execution) and the exit code is pinned by
# test_a_failed_command_makes_the_row_an_error_not_a_plan below.

# ── run_corpus execute=True ───────────────────────────────────────────────────


def test_run_corpus_execute_false_leaves_artifact_path_none(tmp_path: Path):
    outputs = run_corpus(_agent(), [_row()], execute=False)
    assert outputs[0].artifact_path is None


def _chain_writing(name: str = "out.mp4", returncode: int = 0):
    """Patch `run_command_chain` to write *name* into the row dir and report *returncode*.

    Core tests must not invoke ffmpeg. Stubbing at the chain boundary keeps the runner's own
    behaviour under test — which directory it uses, whether it takes the chain path, and what
    it does with an exit code — without pulling the binary in.
    """

    def _fake(commands, fixture_dir, out_dir, **kw):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        produced = out_dir / name
        if returncode == 0:
            produced.write_bytes(b"out")
        return [
            {
                "command": commands[0] if commands else "",
                "resolved_command": commands[0] if commands else "",
                "returncode": returncode,
                "stderr": "boom" if returncode else "",
                "output": produced,
            }
        ]

    return patch("knaif.evalsuite.runner.run_command_chain", side_effect=_fake)


def test_run_corpus_execute_true_sets_artifact_path(tmp_path: Path):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    with _chain_writing():
        outputs = run_corpus(
            agent := _agent(),
            [_row()],
            execute=True,
            sandbox=sandbox,
            fixture_dir=fixture_dir,
        )

    assert agent is not None
    assert outputs[0].artifact_path == sandbox / "ffmpeg_001__0" / "out.mp4"
    assert outputs[0].outcome == "plan"


def test_a_failed_command_makes_the_row_an_error_not_a_plan(tmp_path: Path):
    """The defect this repair exists for: a non-zero exit was invisible to the outcome.

    The row recorded `outcome = plan` and counted as *correct*, so a command that could not
    run scored the same as one that did; only the artifact score noticed, and not always.
    """
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")
    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    with _chain_writing(returncode=1):
        outputs = run_corpus(
            _agent(), [_row()], execute=True, sandbox=sandbox, fixture_dir=fixture_dir
        )

    assert outputs[0].outcome == "error"
    assert "exit 1" in (outputs[0].error or "")


def test_run_corpus_execute_true_creates_per_row_dirs(tmp_path: Path):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    created_dirs: list[Path] = []

    def _fake_chain(commands, fixture_dir, out_dir, **kw):
        created_dirs.append(Path(out_dir))
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        f = Path(out_dir) / "out.mp4"
        f.write_bytes(b"out")
        return [{"command": "", "resolved_command": "", "returncode": 0, "stderr": "", "output": f}]

    with patch("knaif.evalsuite.runner.run_command_chain", side_effect=_fake_chain):
        run_corpus(_agent(), [_row()], execute=True, sandbox=sandbox, fixture_dir=fixture_dir)

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

    with _chain_writing():
        outputs = run_corpus(
            _agent(), [row], execute=True, sandbox=sandbox, fixture_dir=fixture_dir
        )

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


def test_a_single_command_plan_takes_the_same_path_as_a_chain(tmp_path: Path):
    """One execution path for every command-based row — the point of T5b's harness repair.

    One-command plans used to go through `artifact_runner`, which rewrote `-i` to the fixture
    and the output into a separate directory. That is why Python could not reproduce
    `ffmpeg_175`: the `output == input` collision was removed before ffmpeg saw it. Two
    implementations of one rule are how the lanes drifted apart.
    """
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fixture")
    sandbox = tmp_path / "backend"
    sandbox.mkdir()

    agent = _agent()
    mock_exec = MagicMock(return_value=None)
    agent.artifact_runner = mock_exec

    with patch("knaif.evalsuite.runner.run_command_chain", return_value=[]) as chained:
        outputs = run_corpus(
            agent,
            [_row()],
            execute=True,
            sandbox=sandbox,
            fixture_dir=fixture_dir,
        )

    assert chained.called, "a single-command plan must take the chain path like any other"
    assert not mock_exec.called, "artifact_runner is for artifacts that are not command lines"
    assert outputs[0].artifact_commands == [outputs[0].artifact]


def test_artifact_runner_still_serves_a_non_command_artifact(tmp_path: Path):
    """`documents` hands over a JSON plan payload, not a shell command.

    Routing "every row" through the chain would have deleted its execution entirely — the
    hook stays for any skill whose artifact is not a command line. What both paths share is
    the contract: per-row provisioning, faithful paths, and a failure that reaches the outcome.
    """
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fixture")
    sandbox = tmp_path / "backend"
    sandbox.mkdir()
    produced = sandbox / "out.pdf"
    produced.write_bytes(b"x")

    agent = _agent()
    agent.execute_plan.return_value = [{"tool": "compress_pdf", "result": {}}]
    mock_exec = MagicMock(return_value=produced)
    agent.artifact_runner = mock_exec

    with patch("knaif.evalsuite.runner.run_command_chain") as chained:
        run_corpus(agent, [_row()], execute=True, sandbox=sandbox, fixture_dir=fixture_dir)

    assert not chained.called, "there is no command to chain"
    mock_exec.assert_called_once()
