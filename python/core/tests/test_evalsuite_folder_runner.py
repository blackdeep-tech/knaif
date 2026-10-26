"""The runner runs a folder row inside its own copy of the fixture folder.

For a row whose fixture is a folder (`batch/`):
- the fixture folder is copied whole into a fresh row folder (`<sandbox>/<id>__<utt>`), replacing
  whatever an earlier run left there;
- the agent plans and executes with its root and sandbox pointed at that copy, so a glob such as
  `*.mov` expands against the row's files only; both are restored afterwards, also on an error;
- an executing run executes for real (`dry_run=False`, confirmed); a plan-level run only
  dry-runs, in a copy of its own;
- the output records the row folder (`folder_dir`), the model's plan and the outcome, and a
  clarify or an error reaches the outcome as for any row.
Single-file rows are untouched by this path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.runner import run_corpus

PLAN = {"plan": [{"tool": "convert_video", "args": {"inputs": ["*.mov"], "container": "mp4"}}]}


def _row(**kw) -> CorpusRow:
    base: dict = {
        "id": "ffmpeg_batch_001",
        "utterances": ["convert all the .mov files here to mp4"],
        "expected_outcome": "plan",
        "fixture": "batch/",
        "expect_files": [{"name": "*.mp4", "count": 2}],
    }
    base.update(kw)
    return CorpusRow(**base)


@pytest.fixture
def fixtures(tmp_path: Path) -> Path:
    root = tmp_path / "fixtures"
    batch = root / "batch"
    batch.mkdir(parents=True)
    (batch / "a.mov").write_bytes(b"A")
    (batch / "b.mov").write_bytes(b"B")
    (root / "unrelated.mov").write_bytes(b"X")  # must never be seen by a folder row
    return root


def _agent(tmp_path: Path, plan: dict = PLAN) -> MagicMock:
    agent = MagicMock()
    agent.root = tmp_path / "orig_root"
    agent.sandbox = tmp_path / "orig_sandbox"
    agent.last_parse_error = None
    agent.last_model_plan = plan
    agent.artifact_runner = None
    seen: dict = {}

    def infer(utterance, **kw):
        seen["infer_root"] = agent.root
        seen["infer_sandbox"] = agent.sandbox
        seen["listing"] = sorted(p.name for p in Path(agent.sandbox).iterdir())
        return plan

    def execute_plan(payload, **kw):
        seen["exec_sandbox"] = agent.sandbox
        seen["exec_kw"] = kw
        if not kw.get("dry_run"):
            for src in Path(agent.sandbox).glob("*.mov"):
                src.with_suffix(".mp4").write_bytes(b"out")
        return [{"tool": "run_batch", "result": {}}]

    agent.infer.side_effect = infer
    agent.execute_plan.side_effect = execute_plan
    agent.seen = seen
    return agent


def _run(agent, tmp_path, fixtures, execute=True, row=None):
    sandbox = tmp_path / "rows"
    sandbox.mkdir(exist_ok=True)
    return run_corpus(
        agent,
        [row or _row()],
        use_mock=True,
        execute=execute,
        sandbox=sandbox,
        fixture_dir=fixtures,
        apply_retrieval=False,
    )


def test_a_folder_row_runs_in_its_own_copy(tmp_path, fixtures):
    agent = _agent(tmp_path)
    [out] = _run(agent, tmp_path, fixtures)
    row_dir = tmp_path / "rows" / "ffmpeg_batch_001__0"
    assert out.folder_dir == row_dir
    assert agent.seen["infer_sandbox"] == row_dir
    assert agent.seen["infer_root"] == row_dir
    assert agent.seen["listing"] == ["a.mov", "b.mov"]  # the row's files, nothing unrelated
    assert sorted(p.name for p in row_dir.iterdir()) == ["a.mov", "a.mp4", "b.mov", "b.mp4"]
    assert out.outcome == "plan"
    assert out.model_plan == PLAN
    assert out.fixture_path == fixtures / "batch"  # the untouched original, for grading


def test_an_executing_run_executes_for_real(tmp_path, fixtures):
    agent = _agent(tmp_path)
    _run(agent, tmp_path, fixtures)
    assert agent.seen["exec_kw"]["dry_run"] is False
    assert agent.seen["exec_kw"]["confirmed"] is True


def test_a_plan_level_run_only_dry_runs_in_a_copy(tmp_path, fixtures):
    agent = _agent(tmp_path)
    [out] = _run(agent, tmp_path, fixtures, execute=False)
    assert agent.seen["exec_kw"]["dry_run"] is True
    assert agent.seen["listing"] == ["a.mov", "b.mov"]
    assert not list((fixtures / "batch").glob("*.mp4"))  # the fixture itself is never written
    assert out.outcome == "plan"


def test_the_agent_is_pointed_back_afterwards(tmp_path, fixtures):
    agent = _agent(tmp_path)
    _run(agent, tmp_path, fixtures)
    assert agent.root == tmp_path / "orig_root"
    assert agent.sandbox == tmp_path / "orig_sandbox"


def test_the_agent_is_pointed_back_after_an_error(tmp_path, fixtures):
    agent = _agent(tmp_path)
    agent.execute_plan.side_effect = RuntimeError("boom")
    [out] = _run(agent, tmp_path, fixtures)
    assert out.outcome == "error"
    assert "boom" in (out.error or "")
    assert agent.sandbox == tmp_path / "orig_sandbox"


def test_a_stale_row_folder_is_replaced(tmp_path, fixtures):
    stale = tmp_path / "rows" / "ffmpeg_batch_001__0"
    stale.mkdir(parents=True)
    (stale / "left_over.mp4").write_bytes(b"old")
    _run(_agent(tmp_path), tmp_path, fixtures)
    assert not (stale / "left_over.mp4").exists()


def test_a_clarify_is_the_outcome(tmp_path, fixtures):
    clarify = {"plan": [{"tool": "clarify", "args": {"question": "which files?"}}]}
    agent = _agent(tmp_path, clarify)
    [out] = _run(agent, tmp_path, fixtures)
    assert out.outcome == "clarify"
    agent.execute_plan.assert_not_called()


def test_a_single_file_row_does_not_take_the_folder_path(tmp_path, fixtures):
    agent = _agent(tmp_path)
    row = _row(fixture="unrelated.mov", expect_files=None)
    [out] = _run(agent, tmp_path, fixtures, row=row)
    assert out.folder_dir is None


def test_the_batch_failures_reach_the_output(tmp_path, fixtures):
    """`expected_failures` grades this count (folder_rows.grade_folder)."""
    from knaif.batch_chain import BatchReport

    agent = _agent(tmp_path)
    inner = agent.execute_plan.side_effect

    def execute_plan(payload, **kw):
        agent.last_batch_report = BatchReport(files=["a.mov", "b.mov"], failures=[("b.mov", "x")])
        return inner(payload, **kw)

    agent.execute_plan.side_effect = execute_plan
    [out] = _run(agent, tmp_path, fixtures)
    assert out.batch_failures == 1


def test_no_batch_report_means_no_failure_count(tmp_path, fixtures):
    [out] = _run(_agent(tmp_path), tmp_path, fixtures)
    assert out.batch_failures is None
