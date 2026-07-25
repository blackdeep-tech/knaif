"""Tests for evalsuite runner.py: run_corpus over mock agent + canned corpus."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.runner import (
    AgentOutput,
    _extract_artifact,
    _extract_artifacts,
    run_corpus,
)


def _row(
    id: str = "r001", utterance: str = "convert to mp4", outcome: str = "plan", **kw
) -> CorpusRow:
    return CorpusRow(id=id, utterances=[utterance], expected_outcome=outcome, **kw)


def _agent(infer_return: dict | None = None, execute_return: list | None = None) -> MagicMock:
    agent = MagicMock()
    agent.infer.return_value = infer_return or {
        "plan": [{"tool": "convert_video", "args": {"inputs": ["clip.mov"], "container": "mp4"}}]
    }
    agent.execute_plan.return_value = execute_return or []
    return agent


# ── _extract_artifact ─────────────────────────────────────────────────────


def test_extract_artifact_from_run_preview():
    results = [
        {
            "tool": "run_preview",
            "result": {
                "command": ["ffmpeg", "-y", "-i", "input.mp4", "output.mp4"],
                "mode": "dry_run",
            },
        }
    ]
    assert _extract_artifact(results) == "ffmpeg -y -i input.mp4 output.mp4"


def test_extract_artifact_from_run_batch():
    results = [
        {
            "tool": "run_batch",
            "result": {
                "mode": "dry_run",
                "outputs": [{"command": ["ffmpeg", "-i", "a.mov", "a.mp4"]}],
            },
        }
    ]
    assert _extract_artifact(results) == "ffmpeg -i a.mov a.mp4"


def test_extract_artifact_none_when_no_command():
    results = [{"tool": "clarify", "result": {"question": "What do you want?"}}]
    assert _extract_artifact(results) is None


def test_extract_artifact_empty_results():
    assert _extract_artifact([]) is None


def test_extract_artifact_last_command_wins():
    results = [
        {"tool": "run_preview", "result": {"command": ["ffmpeg", "-i", "in.mp4", "preview.mp4"]}},
        {
            "tool": "run_batch",
            "result": {"outputs": [{"command": ["ffmpeg", "-i", "in.mp4", "final.mp4"]}]},
        },
    ]
    # Reversed walk — run_batch is last, so its command wins
    assert _extract_artifact(results) == "ffmpeg -i in.mp4 final.mp4"


# ── _extract_artifacts (plural — one command per intent's batch) ────────────


def _two_intent_results() -> list:
    """Mirror the flat exec_results of a real trim_video -> extract_audio plan."""
    return [
        {"tool": "resolve_inputs", "result": {"files": ["clip.mp4"]}},
        {"tool": "build_recipes", "result": {"recipes": []}},
        {
            "tool": "run_batch",
            "result": {
                "mode": "dry_run",
                "outputs": [
                    {"command": ["ffmpeg", "-y", "-ss", "3", "-i", "clip.mp4", "clip_trimmed.mp4"]}
                ],
            },
        },
        {"tool": "generate_report", "result": {"ok": True}},
        {"tool": "resolve_inputs", "result": {"files": ["clip_trimmed.mp4"]}},
        {
            "tool": "run_batch",
            "result": {
                "outputs": [
                    {"command": ["ffmpeg", "-y", "-i", "clip_trimmed.mp4", "clip_trimmed.mp3"]}
                ],
            },
        },
        {"tool": "generate_report", "result": {"ok": True}},
    ]


def test_extract_artifacts_returns_one_command_per_batch():
    cmds = _extract_artifacts(_two_intent_results())
    assert cmds == [
        "ffmpeg -y -ss 3 -i clip.mp4 clip_trimmed.mp4",
        "ffmpeg -y -i clip_trimmed.mp4 clip_trimmed.mp3",
    ]


def test_extract_artifacts_single_intent_returns_one():
    results = [
        {
            "tool": "run_batch",
            "result": {"outputs": [{"command": ["ffmpeg", "-i", "a.mov", "a.mp4"]}]},
        }
    ]
    assert _extract_artifacts(results) == ["ffmpeg -i a.mov a.mp4"]


def test_extract_artifacts_falls_back_to_preview_when_no_batch():
    results = [
        {"tool": "run_preview", "result": {"command": ["ffmpeg", "-i", "in.mp4", "prev.mp4"]}}
    ]
    assert _extract_artifacts(results) == ["ffmpeg -i in.mp4 prev.mp4"]


def test_extract_artifacts_empty_when_no_command():
    assert _extract_artifacts([{"tool": "clarify", "result": {"question": "?"}}]) == []


# ── run_corpus ────────────────────────────────────────────────────────────


def test_run_corpus_basic():
    outputs = run_corpus(_agent(), [_row()])
    assert len(outputs) == 1
    out = outputs[0]
    assert out.id == "r001"
    assert out.utterance == "convert to mp4"
    assert out.latency_ms >= 0.0


def test_run_corpus_plan_outcome():
    outputs = run_corpus(_agent(), [_row()])
    assert outputs[0].outcome == "plan"


def test_run_corpus_clarify_outcome():
    agent = _agent(
        infer_return={"plan": [{"tool": "clarify", "args": {"question": "What do you want?"}}]}
    )
    outputs = run_corpus(agent, [_row(outcome="clarify")])
    assert outputs[0].outcome == "clarify"
    assert outputs[0].artifact is None


def test_run_corpus_reject_outcome():
    agent = _agent(infer_return={"plan": [{"tool": "reject", "args": {"reason": "unsafe"}}]})
    outputs = run_corpus(agent, [_row(outcome="reject")])
    assert outputs[0].outcome == "reject"
    assert outputs[0].artifact is None


def test_run_corpus_infer_error_records_error_outcome():
    agent = MagicMock()
    agent.infer.side_effect = RuntimeError("inference failed")
    outputs = run_corpus(agent, [_row()])
    assert outputs[0].outcome == "error"
    assert "inference failed" in (outputs[0].error or "")


def test_run_corpus_execute_error_records_error_outcome():
    agent = _agent()
    agent.execute_plan.side_effect = ValueError("sandbox escape")
    outputs = run_corpus(agent, [_row()])
    assert outputs[0].outcome == "error"
    assert outputs[0].error is not None


def test_run_corpus_limit():
    rows = [_row(id=f"r{i:03d}", utterance=f"u{i}") for i in range(10)]
    outputs = run_corpus(_agent(), rows, limit=3)
    assert len(outputs) == 3
    assert outputs[0].id == "r000"
    assert outputs[2].id == "r002"


def test_run_corpus_limit_none_runs_all():
    rows = [_row(id=f"r{i:03d}", utterance=f"u{i}") for i in range(5)]
    outputs = run_corpus(_agent(), rows, limit=None)
    assert len(outputs) == 5


def test_run_corpus_captures_execution_results():
    exec_results = [{"tool": "run_preview", "result": {"command": ["ffmpeg", "-i", "a", "b"]}}]
    outputs = run_corpus(_agent(execute_return=exec_results), [_row()])
    assert outputs[0].execution_results == exec_results


def test_run_corpus_artifact_extracted_from_execution():
    exec_results = [
        {"tool": "run_preview", "result": {"command": ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"]}}
    ]
    outputs = run_corpus(_agent(execute_return=exec_results), [_row()])
    assert outputs[0].artifact == "ffmpeg -y -i in.mp4 out.mp4"


def test_run_corpus_multi_output_chains(tmp_path):
    from unittest.mock import patch

    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"src")
    sandbox = tmp_path / "sb"
    sandbox.mkdir()

    row = CorpusRow(
        id="m1",
        utterances=["trim then extract"],
        expected_outcome="plan",
        fixture="clip.mp4",
        expected_tool="trim_video",
        expected_tools=["trim_video", "extract_audio"],
        outputs=[
            {"command": "ffmpeg -y -ss 3 -i clip.mp4 clip_trimmed.mp4", "criteria": {}},
            {"command": "ffmpeg -y -i clip_trimmed.mp4 clip_trimmed.mp3", "criteria": {}},
        ],
    )
    agent = _agent(execute_return=_two_intent_results())

    def _fake_chain(commands, fdir, out_dir, **kw):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        produced = []
        for c in commands:
            p = out_dir / Path(c.split()[-1]).name
            p.write_bytes(b"x")
            produced.append({"command": c, "returncode": 0, "stderr": "", "output": p})
        return produced

    with patch("knaif.evalsuite.runner.run_command_chain", side_effect=_fake_chain):
        outputs = run_corpus(agent, [row], execute=True, sandbox=sandbox, fixture_dir=fixture_dir)

    out = outputs[0]
    # Both deliverables captured, in order.
    assert len(out.artifact_paths) == 2
    assert out.artifact_paths[0].name == "clip_trimmed.mp4"
    assert out.artifact_paths[1].name == "clip_trimmed.mp3"


# ── NL gate downgrade: execute_plan returns clarify ───────────────────────────


def test_run_corpus_gate_clarify_outcome():
    """When execute_plan returns a clarify result (NL gate fired), outcome must be 'clarify'."""
    # Model emits a plan (resize_video), but the gate downgraded it to clarify.
    gate_clarify_result = [
        {
            "tool": "clarify",
            "args": {"question": "Which file did you mean?"},
            "result": {"status": "clarification_needed", "question": "Which file did you mean?"},
            "output": None,
            "duration_ms": 0.0,
        }
    ]
    agent = _agent(
        infer_return={
            "plan": [{"tool": "resize_video", "args": {"inputs": ["clip_4k.mp4"], "height": 1080}}]
        },
        execute_return=gate_clarify_result,
    )
    outputs = run_corpus(agent, [_row(outcome="clarify")])
    assert (
        outputs[0].outcome == "clarify"
    ), "Runner must detect gate-fired clarify in exec_results and set outcome='clarify'"
    assert outputs[0].artifact is None


def test_run_corpus_gate_clarify_does_not_overwrite_model_clarify():
    """Model-level clarify (first_tool=clarify) still produces outcome='clarify'."""
    agent = _agent(
        infer_return={"plan": [{"tool": "clarify", "args": {"question": "Which file?"}}]}
    )
    agent.execute_plan.return_value = []  # execute_plan is not called for model clarify
    outputs = run_corpus(agent, [_row(outcome="clarify")])
    assert outputs[0].outcome == "clarify"
    # execute_plan must NOT have been called when the model already returned clarify
    agent.execute_plan.assert_not_called()


def test_run_corpus_single_output_still_uses_artifact_runner(tmp_path):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mov").write_bytes(b"src")
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    produced = sandbox / "out.mp4"
    produced.write_bytes(b"x")

    row = _row(id="s1", fixture="clip.mov")
    agent = _agent(
        execute_return=[
            {
                "tool": "run_batch",
                "result": {"outputs": [{"command": ["ffmpeg", "-i", "clip.mov", "out.mp4"]}]},
            }
        ]
    )
    agent.artifact_runner = MagicMock(return_value=produced)

    outputs = run_corpus(agent, [row], execute=True, sandbox=sandbox, fixture_dir=fixture_dir)
    out = outputs[0]
    assert agent.artifact_runner.called
    assert out.artifact_path == produced
    assert out.artifact_paths == [produced]


def test_agent_output_dataclass():
    ao = AgentOutput(
        id="x",
        utterance="y",
        plan=None,
        artifact="ffmpeg -y -i a b",
        outcome="plan",
        latency_ms=42.0,
    )
    assert ao.error is None
    assert ao.execution_results == []
