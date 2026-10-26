"""The eval puts known-folder outputs and alerts where it can grade them, never in the user's.

- **Plan time.** Every eval run plans with ``KNAIF_KNOWN_FOLDERS_ROOT`` pointing at a
  placeholder inside the agent's sandbox (``runner.PLAN_KNOWN_FOLDERS``), so a folder row is not
  refused as "outside the sandbox" and no plan ever names the user's real Downloads. Nothing is
  written there: the run plans dry.
- **Replay.** A row's known folders are ``<row>.known/`` beside the row folder, as its run
  scratch is ``<row>.work/``: the row folder stands for the user's folder and holds only what
  that would. ffmpeg's command chain maps the placeholder there; a replay through the agent
  (documents, exact-files) runs with the variable pointing there.
- **Scoring.** On an executing run a ``expected_folder`` row is right only when the graded file
  is in that (redirected) folder, not just named by the plan.
- **exact-files** replays the plan with its alert put back, records the alert in
  ``<row>.alerts.jsonl`` (``KNAIF_OS_SINK``), and reports a delivery into a known folder as
  ``~/Downloads/clip.mp4``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from knaif.evalsuite.chain import resolve_command
from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.provisioning import (
    cleanup_row_dir,
    provision_row_dir,
    row_alert_sink,
    row_known_dir,
)
from knaif.evalsuite.runner import AgentOutput, plan_known_folders, run_corpus
from knaif.evalsuite.scoring import score_corpus
from knaif.known_folders import ROOT_ENV

SINK_ENV = "KNAIF_OS_SINK"


def _row(**kw) -> CorpusRow:
    base: dict = {
        "id": "f001",
        "utterances": ["convert clip.mov to mp4 and save it in Downloads"],
        "expected_outcome": "plan",
        "fixture": "clip.mov",
        "expected_tool": "convert_video",
        "tags": ["known_folder"],
        "expected_folder": "downloads",
    }
    base.update(kw)
    return CorpusRow(**base)


def _fixtures(tmp_path: Path) -> Path:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "clip.mov").write_bytes(b"fake")
    return fixtures


# ── layout ───────────────────────────────────────────────────────────────────


def test_a_rows_known_folders_and_sink_sit_beside_it(tmp_path: Path) -> None:
    row = tmp_path / "r__0"
    assert row_known_dir(row) == tmp_path / "r__0.known"
    assert row_alert_sink(row) == tmp_path / "r__0.alerts.jsonl"


def test_fresh_provisioning_and_cleanup_remove_them(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    row = tmp_path / "rows" / "r__0"
    for _ in range(2):
        (row_known_dir(row) / "Downloads").mkdir(parents=True, exist_ok=True)
        (row_known_dir(row) / "Downloads" / "old.mp4").write_bytes(b"x")
        row_alert_sink(row).write_text("{}\n", encoding="utf-8")
        provision_row_dir(fixtures, row, fresh=True)
        assert not row_known_dir(row).exists()
        assert not row_alert_sink(row).exists()
    (row_known_dir(row) / "Downloads").mkdir(parents=True)
    row_alert_sink(row).write_text("{}\n", encoding="utf-8")
    cleanup_row_dir(row)
    assert not row_known_dir(row).exists() and not row_alert_sink(row).exists()


def test_the_plan_time_folders_are_inside_the_agents_sandbox(tmp_path: Path) -> None:
    agent = MagicMock(sandbox=tmp_path / "fixtures")
    assert (tmp_path / "fixtures") in plan_known_folders(agent, tmp_path / "lane").parents


# ── the chain ────────────────────────────────────────────────────────────────


def test_a_known_folder_output_goes_beside_the_row(tmp_path: Path) -> None:
    fixtures = (tmp_path / "fixtures").resolve()
    plan_root = fixtures / ".known-folders"
    row = tmp_path / "rows" / "r__0"
    cmd = f"ffmpeg -y -i {fixtures / 'clip.mov'} {plan_root / 'Downloads' / 'clip.mp4'}"
    resolved, collapsed = resolve_command(cmd, row, anchors=[row, fixtures], known_root=plan_root)
    toks = resolved.split()
    assert Path(toks[-1]) == row_known_dir(row) / "Downloads" / "clip.mp4"
    assert Path(toks[toks.index("-i") + 1]) == row / "clip.mov"
    assert collapsed == []


def test_without_a_known_root_the_chain_is_unchanged(tmp_path: Path) -> None:
    fixtures = (tmp_path / "fixtures").resolve()
    row = tmp_path / "rows" / "r__0"
    cmd = f"ffmpeg -y -i {fixtures / 'clip.mov'} {fixtures / 'clip.mp4'}"
    assert resolve_command(cmd, row, anchors=[row, fixtures]) == (
        f"ffmpeg -y -i {row / 'clip.mov'} {row / 'clip.mp4'}",
        [],
    )


# ── the runner ───────────────────────────────────────────────────────────────


def _agent(sandbox: Path, seen: dict) -> MagicMock:
    agent = MagicMock()
    agent.sandbox = sandbox
    agent.infer.return_value = {
        "plan": [
            {
                "tool": "convert_video",
                "args": {"inputs": ["clip.mov"], "output": "Downloads/clip.mp4"},
            }
        ]
    }

    def execute_plan(*_a, **_k):
        seen["plan_time"] = os.environ.get(ROOT_ENV)
        out = Path(os.environ[ROOT_ENV]) / "Downloads" / "clip.mp4"
        return [
            {
                "tool": "convert_video",
                "result": {
                    "command": ["ffmpeg", "-y", "-i", str(sandbox / "clip.mov"), str(out)],
                    "outputs": [{"output": str(out)}],
                },
            }
        ]

    agent.execute_plan.side_effect = execute_plan
    agent.artifact_runner = None
    agent.last_parse_error = None
    agent.last_downloads = []
    return agent


def _fake_chain(seen: dict):
    def chain(commands, fixture_dir, out_dir, **kw):
        seen["known_root"] = kw.get("known_root")
        out = row_known_dir(out_dir) / "Downloads" / "clip.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return [
            {"command": "", "resolved_command": "", "returncode": 0, "stderr": "", "output": out}
        ]

    return chain


def test_the_runner_plans_inside_the_sandbox_and_replays_beside_the_row(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "users-own"))
    fixtures = _fixtures(tmp_path)
    lane = tmp_path / "lane"
    seen: dict = {}
    with patch("knaif.evalsuite.runner.run_command_chain", side_effect=_fake_chain(seen)):
        [out] = run_corpus(
            _agent(fixtures, seen),
            [_row()],
            execute=True,
            sandbox=lane,
            fixture_dir=fixtures,
            apply_retrieval=False,
        )
    plan_root = Path(seen["plan_time"])
    assert fixtures in plan_root.parents
    assert seen["known_root"] == plan_root
    assert not plan_root.exists()  # planning wrote nothing into the fixtures
    assert out.known_dir == row_known_dir(lane / "f001__0")
    assert out.artifact_path == out.known_dir / "Downloads" / "clip.mp4"
    assert os.environ[ROOT_ENV] == str(tmp_path / "users-own")  # restored


def test_a_plan_level_run_plans_inside_the_sandbox_too(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(ROOT_ENV, raising=False)
    fixtures = _fixtures(tmp_path)
    seen: dict = {}
    [out] = run_corpus(_agent(fixtures, seen), [_row()], use_mock=True, apply_retrieval=False)
    assert fixtures in Path(seen["plan_time"]).parents
    assert out.known_dir is None
    assert ROOT_ENV not in os.environ


def test_a_plan_replayed_through_the_agent_sees_the_rows_known_folders(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    lane = tmp_path / "lane"
    seen: dict = {}
    agent = _agent(fixtures, seen)

    def runner(artifact, fixture, row_dir):
        seen["replay"] = os.environ.get(ROOT_ENV)
        return None

    agent.artifact_runner = runner
    agent.execute_plan.side_effect = lambda *_a, **_k: [{"tool": "convert_document", "result": {}}]
    run_corpus(
        agent, [_row()], execute=True, sandbox=lane, fixture_dir=fixtures, apply_retrieval=False
    )
    assert seen["replay"] == str(row_known_dir(lane / "f001__0"))


# ── scoring ──────────────────────────────────────────────────────────────────


def _executed(tmp_path: Path, artifact: Path) -> AgentOutput:
    return AgentOutput(
        id="f001",
        utterance="convert clip.mov to mp4 and save it in Downloads",
        plan={
            "plan": [
                {
                    "tool": "convert_video",
                    "args": {"inputs": ["clip.mov"], "output": "Downloads/clip.mp4"},
                }
            ]
        },
        artifact="ffmpeg -i clip.mov clip.mp4",
        outcome="plan",
        latency_ms=1.0,
        artifact_path=artifact,
        artifact_paths=[artifact],
        known_dir=tmp_path / "r.known",
    )


def _ok(output, criteria, sandbox):
    from knaif.evalsuite.protocols import VerifyResult

    return VerifyResult(score=1.0, matched=["all"], verifier_kind="output")


def test_an_executed_folder_row_needs_the_file_in_that_folder(tmp_path: Path) -> None:
    inside = _executed(tmp_path, tmp_path / "r.known" / "Downloads" / "clip.mp4")
    beside = _executed(tmp_path, tmp_path / "r" / "Downloads" / "clip.mp4")
    wrong = _executed(tmp_path, tmp_path / "r.known" / "Desktop" / "clip.mp4")
    sb = score_corpus([inside, beside, wrong], [_row()], {"success": _ok}, "success", tmp_path)
    assert [r["outcome_correct"] for r in sb["rows"]] == [True, False, False]


# ── exact-files ──────────────────────────────────────────────────────────────


class _WritesIntoDownloads:
    """Stands in for the agent: writes where the known folders point and fires the alert."""

    def __init__(self, seen: dict) -> None:
        self.seen = seen

    def execute_plan(self, payload, **_k):
        self.seen["tools"] = [s["tool"] for s in payload["plan"]]
        out = Path(os.environ[ROOT_ENV]) / "Downloads" / "clip.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        if "alert_when_done" in self.seen["tools"]:
            with open(os.environ[SINK_ENV], "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": "alert", "outcome": "done"}) + "\n")
        return [
            {
                "tool": "convert_video",
                "args": payload["plan"][0]["args"],
                "result": {"outputs": [{"input": "clip.mov", "output": str(out), "returncode": 0}]},
            }
        ]


def _replay(tmp_path: Path, alert: bool, seen: dict) -> dict:
    from knaif.evalsuite.exact_files import replay_row

    return replay_row(
        lambda _folder: _WritesIntoDownloads(seen),
        {
            "plan": [
                {
                    "tool": "convert_video",
                    "args": {"inputs": ["clip.mov"], "output": "Downloads/clip.mp4"},
                }
            ]
        },
        "convert clip.mov to mp4, save it in Downloads and beep",
        _fixtures(tmp_path),
        tmp_path / "rows" / "r__0",
        alert=alert,
    )


def test_exact_files_puts_the_alert_back_and_reads_the_sink(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(SINK_ENV, raising=False)
    seen: dict = {}
    report = _replay(tmp_path, True, seen)
    assert seen["tools"] == ["convert_video", "alert_when_done"]
    assert [a["outcome"] for a in report["alerts"]] == ["done"]
    assert report["delivered"] == ["~/Downloads/clip.mp4"]
    assert report["leftovers"] == []
    assert SINK_ENV not in os.environ


def test_exact_files_without_an_alert_replays_the_plan_as_recorded(tmp_path: Path) -> None:
    seen: dict = {}
    report = _replay(tmp_path, False, seen)
    assert seen["tools"] == ["convert_video"]
    assert report["alerts"] == []


def test_a_leftover_in_a_known_folder_is_reported(tmp_path: Path) -> None:
    from knaif.evalsuite.exact_files import leftovers

    known = tmp_path / "r.known"
    (known / "Downloads").mkdir(parents=True)
    (known / "Downloads" / "clip.mp4").write_bytes(b"x")
    (known / "Downloads" / "clip.part.mp4").write_bytes(b"x")
    delivered = {(known / "Downloads" / "clip.mp4").resolve()}
    assert leftovers(known, inputs=set(), delivered=delivered) == ["Downloads/clip.part.mp4"]


def test_alert_problem() -> None:
    from knaif.evalsuite.exact_files import alert_problem

    done = [{"outcome": "done"}]
    assert alert_problem({"alerts": done, "error": None}, planned=True) is None
    assert alert_problem({"alerts": [], "error": None}, planned=False) is None
    assert "no alert" in alert_problem({"alerts": [], "error": None}, planned=True)
    assert "not asked" in alert_problem({"alerts": done, "error": None}, planned=False)
    assert "2 alerts" in alert_problem({"alerts": done * 2, "error": None}, planned=True)
    assert "failed" in alert_problem({"alerts": done, "error": "exit 1"}, planned=True)
    assert alert_problem({"alerts": [{"outcome": "failed"}], "error": "x"}, planned=True) is None


def test_the_native_replay_gets_the_same_folders_sink_and_alert(tmp_path: Path, monkeypatch):
    """`replay_row_native` runs the binary as `replay_row` runs the agent: the alert put back in
    the mock plan, the known folders at `<row>.known`, the sink at `<row>.alerts.jsonl`."""
    import subprocess

    from knaif.evalsuite.exact_files import replay_row_native

    monkeypatch.delenv(SINK_ENV, raising=False)
    fixtures = _fixtures(tmp_path)
    row = tmp_path / "rows" / "r__0"
    seen: dict = {}

    def fake_run(argv, **kw):
        seen["env"], seen["argv"] = kw["env"], argv
        plan = json.loads(kw["env"]["KNAIF_LLM_MOCK_RESPONSE"])
        seen["tools"] = [s["tool"] for s in plan["plan"]]
        out = Path(kw["env"][ROOT_ENV]) / "Downloads" / "clip.mp4"
        out.parent.mkdir(parents=True)
        out.write_bytes(b"mp4")
        with open(kw["env"][SINK_ENV], "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "alert", "outcome": "done"}) + "\n")
        argv_dump = ["ffmpeg", "-y", "-i", "clip.mov", str(out)]
        return subprocess.CompletedProcess(
            argv, 0, stdout=f"✓ {out}\n", stderr="===KNAIF-ARGV===" + json.dumps(argv_dump) + "\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    report = replay_row_native(
        Path("knaif.exe"),
        "ffmpeg",
        {
            "plan": [
                {
                    "tool": "convert_video",
                    "args": {"inputs": ["clip.mov"], "output": "Downloads/clip.mp4"},
                }
            ]
        },
        "convert clip.mov to mp4, save it in Downloads and beep",
        fixtures,
        row,
        alert=True,
    )
    assert seen["tools"] == ["convert_video", "alert_when_done"]
    assert Path(seen["env"][ROOT_ENV]) == row_known_dir(row).resolve()
    assert Path(seen["env"][SINK_ENV]) == row_alert_sink(row).resolve()
    assert report["delivered"] == ["~/Downloads/clip.mp4"]
    assert [a["outcome"] for a in report["alerts"]] == ["done"]
    assert report["leftovers"] == []
