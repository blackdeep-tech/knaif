"""The native lane runs 1.4.0's new rows the way the Python lane does.

The 1.4.0 Windows GPU cells (2026-11-01) graded every new kind of row as a binary failure, on
every backend and both models, because the lane never gave the binary what the Python lane gives
the agent: a scratch root it can find from the row's folder, the eval link server, known folders
and an alert sink beside the row (the binary wrote into the user's own Downloads), and a copy of
a folder row's own folder. What is pinned here is what each row is handed and how its result is
read; the subprocesses are faked, as in test_native_lane_batch.py.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from knaif.evalsuite import native_lane
from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.native_lane import LaneConfig, batch_plan, parse_run_output, run_native_corpus
from knaif.evalsuite.provisioning import row_alert_sink, row_known_dir, row_scratch_dir


@pytest.fixture(autouse=True)
def _no_help(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_lane, "_run_help", lambda binary: "")


def _lane(tmp_path: Path) -> LaneConfig:
    return LaneConfig(
        name="t", binary=tmp_path / "knaif.exe", model_path=tmp_path / "m.gguf", timeout_s=5
    )


def _fixtures(tmp_path: Path) -> Path:
    fx = tmp_path / "fixtures"
    (fx / "batch").mkdir(parents=True)
    (fx / "clip.mp4").write_bytes(b"root")
    (fx / "batch" / "a.mp4").write_bytes(b"a")
    (fx / "batch" / "b.mp4").write_bytes(b"b")
    return fx


def _plan(*tools: str) -> dict[str, Any]:
    return {"plan": [{"tool": t, "args": {}} for t in tools]}


class _Binary:
    """Stands in for `subprocess.run`: records what a row is handed, then runs *act* in it."""

    def __init__(
        self,
        plan: dict[str, Any] | None = None,
        act: Callable[[Path, dict[str, str]], None] | None = None,
        stderr: str = "",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.plan = plan or _plan("convert_video")
        self.act = act
        self.stderr = stderr

    def __call__(self, argv, **kw) -> subprocess.CompletedProcess:
        cwd, env = Path(kw["cwd"]), kw.get("env") or {}
        self.calls.append(
            {"argv": [str(a) for a in argv], "cwd": cwd, "env": env,
             "cwd_files": sorted(p.name for p in cwd.iterdir() if not p.name.startswith("."))}
        )  # fmt: skip
        if "--batch" in argv:
            n = len(Path(argv[argv.index("--batch") + 1]).read_text(encoding="utf-8").splitlines())
            return subprocess.CompletedProcess(argv, 0, (json.dumps(self.plan) + "\n") * n, "")
        if self.act is not None:
            self.act(cwd, env)
        stderr = f"{native_lane.PLAN_DUMP_MARKER}{json.dumps(self.plan)}\n{self.stderr}"
        return subprocess.CompletedProcess(argv, 0, "", stderr)


def _run(tmp_path, monkeypatch, rows, binary, sandbox: Path | None = None) -> list:
    monkeypatch.setattr(native_lane.subprocess, "run", binary)
    return run_native_corpus(
        _lane(tmp_path),
        "ffmpeg",
        rows,
        fixture_dir=_fixtures(tmp_path),
        sandbox=sandbox if sandbox is not None else tmp_path / "sb",
    )


def _row(fixture: str | None = None, **kw) -> CorpusRow:
    return CorpusRow(
        id="r1", utterances=["convert clip.mp4 to mkv"], expected_outcome="plan",
        fixture=fixture, **kw,
    )  # fmt: skip


# ── a relative sandbox: the binary runs inside the row, so every path it is handed is absolute ─


def test_every_path_the_row_is_handed_is_absolute_from_a_relative_sandbox(
    tmp_path, monkeypatch
) -> None:
    """The release scripts pass `sandbox/`; the binary runs with cwd = the row's folder, where a
    relative scratch root made a nested `sandbox/…` the grader never looked in."""
    monkeypatch.chdir(tmp_path)
    binary = _Binary()
    _run(tmp_path, monkeypatch, [_row()], binary, sandbox=Path("sb"))
    call = binary.calls[0]
    row = tmp_path / "sb" / "r1__0"
    assert call["cwd"] == row
    assert call["env"]["KNAIF_SCRATCH_ROOT"] == str(row_scratch_dir(row))
    assert call["env"]["KNAIF_KNOWN_FOLDERS_ROOT"] == str(row_known_dir(row))
    assert call["env"]["KNAIF_OS_SINK"] == str(row_alert_sink(row))


def test_a_routed_intermediate_is_graded_from_a_relative_sandbox(tmp_path, monkeypatch) -> None:
    def act(cwd: Path, env: dict[str, str]) -> None:
        run = cwd / env["KNAIF_SCRATCH_ROOT"] / "run-1"  # resolved where the binary runs
        run.mkdir(parents=True)
        (run / "clip_trimmed.mp4").write_bytes(b"mid")
        os.utime(run / "clip_trimmed.mp4", (1, 1))
        (cwd / "clip_trimmed_resized.mp4").write_bytes(b"final")

    monkeypatch.chdir(tmp_path)
    [out] = _run(tmp_path, monkeypatch, [_row()], _Binary(act=act), sandbox=Path("sb"))
    assert [p.name for p in out.artifact_paths] == ["clip_trimmed.mp4", "clip_trimmed_resized.mp4"]


# ── known folders: beside the row, never the user's own ────────────────────────────────────────


def test_a_file_saved_into_a_known_folder_lands_beside_the_row_and_is_graded(
    tmp_path, monkeypatch
) -> None:
    def act(cwd: Path, env: dict[str, str]) -> None:
        downloads = cwd / env["KNAIF_KNOWN_FOLDERS_ROOT"] / "Downloads"
        downloads.mkdir(parents=True)
        (downloads / "clip.mp4").write_bytes(b"out")

    [out] = _run(tmp_path, monkeypatch, [_row()], _Binary(act=act))
    known = row_known_dir(tmp_path / "sb" / "r1__0")
    assert out.known_dir == known
    assert out.artifact_paths == [known / "Downloads" / "clip.mp4"]


# ── alerts: recorded, never made, and taken out of the plan as the Python lane does ────────────


def test_an_alert_is_split_off_the_plan_and_counted(tmp_path, monkeypatch) -> None:
    binary = _Binary(plan=_plan("convert_video", "alert_when_done"))
    [out] = _run(tmp_path, monkeypatch, [_row()], binary)
    assert out.alert is True
    assert [s["tool"] for s in out.plan["plan"]] == ["convert_video"]


def test_a_live_alert_row_still_equals_its_batch_plan(tmp_path, monkeypatch) -> None:
    """The cold-start check compares the live plan with the batch's: both without the alert, and
    the alert itself on its own."""
    planned = _plan("convert_video", "alert_when_done")
    outputs = _run(tmp_path, monkeypatch, [_row()], _Binary(plan=planned))
    key = ("r1", 0)
    assert native_lane.batch_live_mismatches(outputs, {key: planned}, {key}) == (1, [])
    no_alert = _plan("convert_video")
    assert native_lane.batch_live_mismatches(outputs, {key: no_alert}, {key}) == (1, [["r1", 0]])


def test_a_plan_without_an_alert_has_none(tmp_path, monkeypatch) -> None:
    [out] = _run(tmp_path, monkeypatch, [_row()], _Binary())
    assert out.alert is False


# ── folder rows: a copy of the row's own folder, graded as one ─────────────────────────────────


def test_a_folder_row_runs_in_a_copy_of_its_own_folder(tmp_path, monkeypatch) -> None:
    binary = _Binary()
    [out] = _run(tmp_path, monkeypatch, [_row("batch/")], binary)
    assert binary.calls[0]["cwd_files"] == ["a.mp4", "b.mp4"], "not the other fixtures"
    assert out.folder_dir == tmp_path / "sb" / "r1__0"
    assert out.fixture_path == tmp_path / "fixtures" / "batch"


def test_a_file_row_still_sees_every_fixture(tmp_path, monkeypatch) -> None:
    binary = _Binary()
    [out] = _run(tmp_path, monkeypatch, [_row("clip.mp4")], binary)
    assert binary.calls[0]["cwd_files"] == ["batch", "clip.mp4"]
    assert out.folder_dir is None


def test_a_folder_row_reports_how_many_files_failed(tmp_path, monkeypatch) -> None:
    binary = _Binary(stderr="✗ b.mp4: broken\n1 done, 1 failed in 0.4s, 1.0 KB\n")
    [out] = _run(tmp_path, monkeypatch, [_row("batch/", expected_failures=1)], binary)
    assert out.batch_failures == 1


@pytest.mark.parametrize(
    ("stderr", "failed"),
    [
        ("8 done, 2 failed in 12.4s, 35.2 MB\n", 2),
        ("5 done in 3.1s, 12.0 MB\n", 0),
        ("running: ffmpeg -y -i clip.mp4 out.mkv\n", None),
    ],
)
def test_the_batch_summary_is_read_for_its_failures(stderr, failed) -> None:
    assert parse_run_output("", stderr, 0)["batch_failures"] == failed


def test_a_batch_that_finished_with_a_failed_file_is_a_plan() -> None:
    """The binary ends a batch with a failed file with its summary, then exits 1
    (`apps/cli/src/main.rs`); the run did what the plan asked of every other file, so the row is
    graded as a plan with its failures counted, as the Python lane's `BatchReport` is."""
    stderr = "✗ silent.mov: has no audio track to extract\n5 done, 1 failed in 1.2s, 3.0 MB\n"
    got = parse_run_output("", stderr, 1)
    assert (got["outcome"], got["batch_failures"]) == ("plan", 1)


def test_a_batch_that_stopped_on_an_error_is_still_an_error() -> None:
    stderr = "1 done, 1 failed in 0.4s, 1.0 KB\nError: step 3 of 3 failed\n"
    assert parse_run_output("", stderr, 1)["outcome"] == "error"


# ── batch planning: each folder row plans where it will run ────────────────────────────────────


def test_batch_planning_runs_each_folder_where_its_rows_run(tmp_path, monkeypatch) -> None:
    binary = _Binary()
    monkeypatch.setattr(native_lane.subprocess, "run", binary)
    plans = batch_plan(
        _lane(tmp_path),
        "ffmpeg",
        ["one", "every clip here", "two"],
        fixture_dir=_fixtures(tmp_path),
        work_dir=tmp_path / "lane" / ".batch",
        folders=[None, "batch/", None],
    )
    assert len(plans) == 3
    seen = {tuple(c["cwd_files"]) for c in binary.calls}
    assert ("a.mp4", "b.mp4") in seen, "the folder row planned in its folder"
    assert any("clip.mp4" in files for files in seen), "the others where every fixture is"
    assert len(binary.calls) == 2, "one model load per folder, not per row"


def test_batch_planning_keeps_the_corpus_order_across_folders(tmp_path, monkeypatch) -> None:
    def fake_one(lane, skill, utterances, *, fixture_dir, work_dir, **kw):
        return [{"plan": [], "u": u, "where": fixture_dir.name} for u in utterances]

    monkeypatch.setattr(native_lane, "_batch_plan_in", fake_one)
    plans = batch_plan(
        _lane(tmp_path),
        "ffmpeg",
        ["a", "b", "c", "d"],
        fixture_dir=_fixtures(tmp_path),
        work_dir=tmp_path / ".batch",
        folders=["batch/", None, "batch/", None],
    )
    assert [(p["u"], p["where"]) for p in plans] == [
        ("a", "batch"), ("b", "fixtures"), ("c", "batch"), ("d", "fixtures")
    ]  # fmt: skip
