"""The eval sees a run's alert and known folder the way the pipeline does.

- `expected_alert` (true: the plan must ask for an alert) and `expected_folder` (a known-folder
  name) are optional row fields. A row without them scores exactly as before, so no committed
  baseline moves and the scoring policy stays the same.
- The runner splits the alert off the model's plan (`planner.split_alert`) before it looks at
  the first tool, and records whether there was one.
- A row's outcome is correct only when the alert matches what the row expects: a missing alert,
  or an alert on a row that asked for none (a false alert), is a wrong outcome.
- At plan level a row with `expected_folder` is correct only when the plan writes into that
  folder (`Downloads`, `Downloads/clip.mp4`, `~/Downloads/clip.mp4`).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.protocols import VerifyResult
from knaif.evalsuite.runner import AgentOutput, run_corpus
from knaif.evalsuite.scoring import plan_names_folder, score_corpus

ALERT = "alert_when_done"


def _convert(output: str | None = None) -> dict:
    args: dict = {"inputs": ["clip.mov"], "container": "mp4"}
    if output is not None:
        args["output"] = output
    return {"tool": "convert_video", "args": args}


def _row(**kw) -> CorpusRow:
    base: dict = {
        "id": "r001",
        "utterances": ["convert clip.mov to mp4 and beep when it's done"],
        "expected_outcome": "plan",
        "expected_tool": "convert_video",
        "tags": ["completion_alert"],
    }
    base.update(kw)
    return CorpusRow(**base)


def _output(plan: dict, alert: bool = False, outcome: str = "plan") -> AgentOutput:
    return AgentOutput(
        id="r001",
        utterance="convert clip.mov to mp4 and beep when it's done",
        plan=plan,
        artifact="ffmpeg -i clip.mov clip.mp4",
        outcome=outcome,
        latency_ms=1.0,
        alert=alert,
    )


def _perfect(output, criteria, sandbox) -> VerifyResult:
    return VerifyResult(score=1.0, matched=["all"], verifier_kind="command")


def _score(rows, outputs, tmp_path):
    return score_corpus(outputs, rows, {"cheap": _perfect}, "cheap", tmp_path)


# ── corpus fields ────────────────────────────────────────────────────────────


def test_row_accepts_an_expected_alert():
    assert _row(expected_alert=True).expected_alert is True


def test_a_row_expects_no_alert_by_default():
    assert _row().expected_alert is False


@pytest.mark.parametrize("bad", ["beep", 1, None])
def test_row_refuses_an_alert_that_is_not_a_boolean(bad):
    with pytest.raises(ValueError):
        _row(expected_alert=bad)


def test_row_refuses_an_unknown_folder():
    with pytest.raises(ValueError):
        _row(expected_folder="attic")


def test_alert_and_folder_round_trip():
    row = _row(expected_alert=True, expected_folder="downloads")
    again = CorpusRow.from_dict(row.to_dict())
    assert (again.expected_alert, again.expected_folder) == (True, "downloads")


def test_a_row_without_them_serializes_as_before():
    d = _row().to_dict()
    assert "expected_alert" not in d
    assert "expected_folder" not in d


# ── runner ───────────────────────────────────────────────────────────────────


def _agent(plan: dict) -> MagicMock:
    agent = MagicMock()
    agent.infer.return_value = plan
    agent.execute_plan.return_value = []
    agent.artifact_runner = None
    agent.last_parse_error = None
    return agent


def test_runner_splits_the_alert_off_and_records_it():
    agent = _agent({"plan": [{"tool": ALERT, "args": {}}, _convert()]})
    [out] = run_corpus(agent, [_row()], use_mock=True, apply_retrieval=False)
    assert out.alert is True
    assert [s["tool"] for s in out.plan["plan"]] == ["convert_video"]
    assert out.outcome == "plan"
    executed = agent.execute_plan.call_args.args[0]
    assert ALERT not in [s["tool"] for s in executed["plan"]]


def test_runner_records_no_alert_when_none_was_asked():
    [out] = run_corpus(
        _agent({"plan": [_convert()]}), [_row()], use_mock=True, apply_retrieval=False
    )
    assert out.alert is False


def test_runner_reads_an_alert_with_no_work_as_a_clarify():
    [out] = run_corpus(
        _agent({"plan": [{"tool": ALERT, "args": {}}]}),
        [_row()],
        use_mock=True,
        apply_retrieval=False,
    )
    assert out.outcome == "clarify"
    assert out.alert is False


# ── scoring: the alert ───────────────────────────────────────────────────────


def test_an_expected_alert_that_came_is_correct(tmp_path):
    sb = _score([_row(expected_alert=True)], [_output({"plan": [_convert()]}, True)], tmp_path)
    assert sb["rows"][0]["outcome_correct"] is True


def test_missing_alert_is_wrong(tmp_path):
    sb = _score([_row(expected_alert=True)], [_output({"plan": [_convert()]}, False)], tmp_path)
    assert sb["rows"][0]["outcome_correct"] is False


def test_false_alert_is_wrong(tmp_path):
    sb = _score([_row(tags=["convert"])], [_output({"plan": [_convert()]}, True)], tmp_path)
    assert sb["rows"][0]["outcome_correct"] is False


def test_a_row_and_output_without_alerts_score_as_before(tmp_path):
    sb = _score([_row(tags=["convert"])], [_output({"plan": [_convert()]})], tmp_path)
    assert sb["rows"][0]["outcome_correct"] is True
    assert "alerts" not in sb


def test_scoreboard_counts_alerts(tmp_path):
    rows = [
        _row(id="a", expected_alert=True),
        _row(id="b", expected_alert=True),
        _row(id="c"),
        _row(id="d"),
    ]
    outs = [
        _output({"plan": [_convert()]}, True),
        _output({"plan": [_convert()]}, False),
        _output({"plan": [_convert()]}, True),
        _output({"plan": [_convert()]}, False),
    ]
    for o, r in zip(outs, rows, strict=True):
        o.id = r.id
    sb = _score(rows, outs, tmp_path)
    assert sb["alerts"] == {"expected": 2, "correct": 1, "missed": 1, "false": 1}


# ── scoring: the known folder ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "output",
    [
        "Downloads",
        "downloads/clip.mp4",
        "Downloads/",
        "~/Downloads/clip.mp4",
        "~\\Downloads",
        "./Downloads/clip.mp4",
    ],
)
def test_plan_names_folder(output):
    assert plan_names_folder({"plan": [_convert(output)]}, "downloads")


@pytest.mark.parametrize("output", [None, "clip.mp4", "out/Downloads/clip.mp4", "Desktop"])
def test_plan_does_not_name_folder(output):
    assert not plan_names_folder({"plan": [_convert(output)]}, "downloads")


def test_home_is_named_by_a_bare_tilde():
    assert plan_names_folder({"plan": [_convert("~/clip.mp4")]}, "home")


def test_inputs_never_count_as_the_folder():
    plan = {"plan": [{"tool": "convert_video", "args": {"inputs": ["Downloads/clip.mov"]}}]}
    assert not plan_names_folder(plan, "downloads")


def test_folder_row_needs_the_folder(tmp_path: Path):
    row = _row(expected_folder="downloads", tags=["known_folder"])
    good = _score([row], [_output({"plan": [_convert("Downloads/clip.mp4")]})], tmp_path)
    bad = _score([row], [_output({"plan": [_convert("clip.mp4")]})], tmp_path)
    assert good["rows"][0]["outcome_correct"] is True
    assert bad["rows"][0]["outcome_correct"] is False
