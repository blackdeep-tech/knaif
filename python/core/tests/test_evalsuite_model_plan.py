"""A saved eval row keeps the plan the model wrote, next to the plan core went on with.

Core rewrites a model's plan before anything runs (the chain linker, the source threader, the
alert split), so `plan` alone cannot tell a model error from a correct plan that core changed.
The agent keeps the model's own plan in `last_model_plan`; the runner records it on each output
as `model_plan`, and the scoreboard writes it into each row. An agent without one records None.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.protocols import VerifyResult
from knaif.evalsuite.runner import AgentOutput, run_corpus
from knaif.evalsuite.scoring import score_corpus

MODEL = {
    "plan": [
        {
            "tool": "trim_video",
            "args": {"input": "clip.mp4", "start": 1, "end": 3, "output": "1.mp4"},
        },
        {"tool": "trim_video", "args": {"input": "clip.mp4", "start": 3, "output": "2.mp4"}},
    ]
}
REWRITTEN = {
    "plan": [
        {
            "tool": "trim_video",
            "args": {"input": "clip.mp4", "start": 1, "end": 3, "output": "1.mp4"},
        },
        {"tool": "trim_video", "args": {"input": "1.mp4", "start": 3, "output": "2.mp4"}},
    ]
}


def _row() -> CorpusRow:
    return CorpusRow(
        id="r001",
        utterances=["cut 1-3 s of clip.mp4 as 1.mp4 and the rest as 2.mp4"],
        expected_outcome="plan",
        expected_tool="trim_video",
    )


def _agent(model_plan) -> MagicMock:
    agent = MagicMock()
    agent.infer.return_value = REWRITTEN
    agent.last_model_plan = model_plan
    agent.execute_plan.return_value = []
    agent.artifact_runner = None
    agent.last_parse_error = None
    return agent


def test_runner_records_the_model_plan():
    [out] = run_corpus(_agent(MODEL), [_row()], use_mock=True, apply_retrieval=False)
    assert out.model_plan == MODEL
    assert out.plan == REWRITTEN


def test_runner_records_a_copy_not_the_agents_object():
    model = {"plan": [dict(MODEL["plan"][0])]}
    agent = _agent(model)
    [out] = run_corpus(agent, [_row()], use_mock=True, apply_retrieval=False)
    model["plan"].clear()
    assert len(out.model_plan["plan"]) == 1


def test_runner_records_none_when_the_agent_keeps_no_model_plan():
    agent = _agent(None)
    del agent.last_model_plan  # a MagicMock attribute would otherwise be a mock, not a plan
    [out] = run_corpus(agent, [_row()], use_mock=True, apply_retrieval=False)
    assert out.model_plan is None


def test_scoreboard_rows_carry_the_model_plan(tmp_path):
    out = AgentOutput(
        id="r001",
        utterance="cut 1-3 s of clip.mp4 as 1.mp4 and the rest as 2.mp4",
        plan=REWRITTEN,
        artifact="ffmpeg ...",
        outcome="plan",
        latency_ms=1.0,
        model_plan=MODEL,
    )

    def perfect(output, criteria, sandbox) -> VerifyResult:
        return VerifyResult(score=1.0, matched=["all"], verifier_kind="command")

    sb = score_corpus([out], [_row()], {"cheap": perfect}, "cheap", tmp_path)
    [row] = sb["rows"]
    assert row["model_plan"] == MODEL
    assert row["plan"] == REWRITTEN


def test_an_output_without_a_model_plan_saves_none(tmp_path):
    out = AgentOutput(
        id="r001", utterance="u", plan=REWRITTEN, artifact=None, outcome="plan", latency_ms=1.0
    )

    def perfect(output, criteria, sandbox) -> VerifyResult:
        return VerifyResult(score=1.0, matched=["all"], verifier_kind="command")

    [row] = score_corpus([out], [_row()], {"cheap": perfect}, "cheap", tmp_path)["rows"]
    assert row["model_plan"] is None
