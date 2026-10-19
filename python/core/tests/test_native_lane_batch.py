"""B1: plan every row in one batch, then execute each row from its plan with no model.

Plan `docs/plans/2026-10-14-faster-release-evidence.md`, Part B. The model is loaded once by
`knaif plan --batch`; each row then runs through `knaif run` as before, with the mock backend
handing the binary that row's batch plan, so everything after the model (the safety screen,
validation, expansion, the clarify gate, execution) is still the shipped path. A pre-drawn
cold-start sample runs live, with the model, and its plans must equal the batch's.

The subprocesses are faked: what is pinned here is what each row is handed and how its outcome
is read, which is where a mistake would produce a plausible number rather than a crash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from knaif.evalsuite import native_lane
from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.native_lane import (
    LaneConfig,
    batch_live_mismatches,
    batch_live_unplanned,
    batch_plan,
    run_native_corpus,
)


def _lane(tmp_path: Path) -> LaneConfig:
    return LaneConfig(
        name="t", binary=tmp_path / "knaif.exe", model_path=tmp_path / "m.gguf", timeout_s=5
    )


def _fixtures(tmp_path: Path) -> Path:
    fx = tmp_path / "fixtures"
    fx.mkdir()
    (fx / "clip.mp4").write_bytes(b"x")
    return fx


def _plan(tool: str) -> dict[str, Any]:
    return {"plan": [{"tool": tool, "args": {"inputs": ["clip.mp4"]}}]}


class _Recorder:
    """Stands in for `subprocess.run`, answering like the binary would."""

    def __init__(self, batch_stdout: str = "", device: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.batch_stdout = batch_stdout
        self.device = device

    def _trace(self, argv: list[str]) -> str:
        """llama.cpp's load trace: only a process that loads the model, and only under --verbose."""
        if self.device is None or "--model" not in argv or "--verbose" not in argv:
            return ""
        return "".join(
            f"load_tensors: layer {i:3d} assigned to device {self.device}, is_swa = 0\n"
            for i in range(3)
        )

    def __call__(self, argv, **kw) -> subprocess.CompletedProcess:
        argv = [str(a) for a in argv]
        call = {"argv": argv, "env": kw.get("env") or {}, "cwd": kw.get("cwd")}
        if "--batch" in argv:
            call["batch_file"] = Path(argv[argv.index("--batch") + 1]).read_text(encoding="utf-8")
            call["cwd_files"] = sorted(p.name for p in Path(kw["cwd"]).iterdir())
            self.calls.append(call)
            return subprocess.CompletedProcess(argv, 0, self.batch_stdout, self._trace(argv))
        self.calls.append(call)
        env = call["env"]
        plan = env.get("KNAIF_LLM_MOCK_RESPONSE") or json.dumps(_plan("live_tool"))
        stderr = f"{self._trace(argv)}{native_lane.PLAN_DUMP_MARKER}{plan}\n"
        return subprocess.CompletedProcess(argv, 0, "", stderr)


# ── batch planning: one process, one model load, line-aligned ─────────────────


def test_batch_plan_loads_the_model_once_and_keeps_the_order(tmp_path, monkeypatch) -> None:
    rec = _Recorder("\n".join(json.dumps(_plan(t)) for t in ("a", "b", "c")) + "\n")
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    plans = batch_plan(
        _lane(tmp_path),
        "ffmpeg",
        ["one", "two", "three"],
        fixture_dir=_fixtures(tmp_path),
        work_dir=tmp_path / "batch",
    )
    assert [p["plan"][0]["tool"] for p in plans] == ["a", "b", "c"]
    assert len(rec.calls) == 1, "the whole batch is one process: one model load"
    argv = rec.calls[0]["argv"]
    assert argv[1:4] == ["plan", "--skill", "ffmpeg"]
    assert "--json" in argv and argv[argv.index("--model") + 1].endswith("m.gguf")
    assert rec.calls[0]["batch_file"].splitlines() == ["one", "two", "three"]


def test_batch_planning_sees_the_same_files_a_row_does(tmp_path, monkeypatch) -> None:
    """Planning resolves bare stems against the working directory (`resolve_plan_stems`), so
    the batch must run where every fixture is visible, as each live row's directory is."""
    rec = _Recorder(json.dumps(_plan("a")) + "\n")
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    batch_plan(
        _lane(tmp_path),
        "ffmpeg",
        ["one"],
        fixture_dir=_fixtures(tmp_path),
        work_dir=tmp_path / "batch",
    )
    assert "clip.mp4" in rec.calls[0]["cwd_files"]


def test_a_batch_that_lost_a_line_is_refused(tmp_path, monkeypatch) -> None:
    """Output is line-aligned with input; one line short would shift every later row's plan
    onto the wrong utterance, and every one of them would still look like a plan."""
    monkeypatch.setattr(native_lane.subprocess, "run", _Recorder(json.dumps(_plan("a")) + "\n"))
    with pytest.raises(RuntimeError, match="1 plan line.* for 2 utterances"):
        batch_plan(
            _lane(tmp_path),
            "ffmpeg",
            ["one", "two"],
            fixture_dir=_fixtures(tmp_path),
            work_dir=tmp_path / "batch",
        )


def test_an_utterance_with_a_line_break_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError, match="line break"):
        batch_plan(
            _lane(tmp_path),
            "ffmpeg",
            ["one\ntwo"],
            fixture_dir=_fixtures(tmp_path),
            work_dir=tmp_path / "batch",
        )


# ── execution from the batch's plans ──────────────────────────────────────────


def _corpus() -> list[CorpusRow]:
    return [
        CorpusRow(id="r1", utterances=["convert clip.mp4 to mkv"], expected_outcome="plan"),
        CorpusRow(id="r2", utterances=["trim clip.mp4", "cut clip.mp4"], expected_outcome="plan"),
    ]


def _run(tmp_path, monkeypatch, plans, live=None) -> tuple[list, _Recorder]:
    rec = _Recorder()
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    outputs = run_native_corpus(
        _lane(tmp_path),
        "ffmpeg",
        _corpus(),
        fixture_dir=_fixtures(tmp_path),
        sandbox=tmp_path / "sb",
        plans=plans,
        live=live,
    )
    return outputs, rec


def test_a_replayed_row_runs_without_the_model_and_is_handed_its_plan(
    tmp_path, monkeypatch
) -> None:
    plans = {("r1", 0): _plan("convert_video"), ("r2", 0): _plan("trim_video"),
             ("r2", 1): _plan("trim_video")}  # fmt: skip
    outputs, rec = _run(tmp_path, monkeypatch, plans)
    assert len(rec.calls) == 3
    for call in rec.calls:
        assert "--model" not in call["argv"], "execution needs no model"
        assert call["argv"][1:3] == ["run", "ffmpeg"], "still the shipped `run` path"
        assert call["env"]["KNAIF_LLM_BACKEND"] == "mock"
        assert call["env"]["KNAIF_DUMP_PLAN"] == "1"
    assert json.loads(rec.calls[0]["env"]["KNAIF_LLM_MOCK_RESPONSE"]) == plans[("r1", 0)]
    assert [o.plan["plan"][0]["tool"] for o in outputs] == ["convert_video"] + ["trim_video"] * 2
    assert all(o.outcome == "plan" for o in outputs)


def test_a_row_whose_batch_planning_failed_is_an_error_and_never_runs(
    tmp_path, monkeypatch
) -> None:
    """`run` would have failed planning the same way: `plan --batch` and `run` share one
    `PlanSession::plan`. Replaying an empty plan instead would score a model failure as an
    empty plan."""
    plans = {("r1", 0): {"plan": [], "error": "invalid JSON from the model"},
             ("r2", 0): _plan("trim_video"), ("r2", 1): _plan("trim_video")}  # fmt: skip
    outputs, rec = _run(tmp_path, monkeypatch, plans)
    assert len(rec.calls) == 2
    first = outputs[0]
    assert (first.id, first.outcome, first.plan) == ("r1", "error", None)
    assert "invalid JSON from the model" in first.error


def test_cold_start_rows_run_live_even_when_the_batch_planned_them(tmp_path, monkeypatch) -> None:
    plans = {("r1", 0): _plan("convert_video"), ("r2", 0): _plan("trim_video"),
             ("r2", 1): _plan("trim_video")}  # fmt: skip
    outputs, rec = _run(tmp_path, monkeypatch, plans, live={("r2", 1)})
    live_call = rec.calls[2]
    assert "--model" in live_call["argv"]
    assert "KNAIF_LLM_MOCK_RESPONSE" not in live_call["env"]
    assert outputs[2].plan == _plan("live_tool")


def test_a_row_the_batch_did_not_plan_runs_live(tmp_path, monkeypatch) -> None:
    outputs, rec = _run(tmp_path, monkeypatch, {("r1", 0): _plan("convert_video")})
    assert ["--model" in c["argv"] for c in rec.calls] == [False, True, True]


def test_without_batch_plans_every_row_runs_live_as_before(tmp_path, monkeypatch) -> None:
    outputs, rec = _run(tmp_path, monkeypatch, None)
    assert all("--model" in c["argv"] for c in rec.calls)
    assert all("KNAIF_LLM_BACKEND" not in c["env"] for c in rec.calls)


def test_the_mock_never_leaks_into_a_live_row_from_the_parent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KNAIF_LLM_MOCK_RESPONSE", json.dumps(_plan("stale")))
    monkeypatch.setenv("KNAIF_LLM_BACKEND", "mock")
    _, rec = _run(tmp_path, monkeypatch, None)
    for call in rec.calls:
        assert "KNAIF_LLM_MOCK_RESPONSE" not in call["env"]
        assert "KNAIF_LLM_BACKEND" not in call["env"]


# ── the cold-start check: live plans equal the batch's ────────────────────────


def test_live_rows_whose_plan_differs_from_the_batch_are_reported(tmp_path, monkeypatch) -> None:
    plans = {("r1", 0): _plan("convert_video"), ("r2", 0): _plan("trim_video"),
             ("r2", 1): _plan("trim_video")}  # fmt: skip
    outputs, _ = _run(tmp_path, monkeypatch, plans, live={("r1", 0), ("r2", 1)})
    compared, mismatches = batch_live_mismatches(outputs, plans, {("r1", 0), ("r2", 1)})
    assert compared == 2
    assert mismatches == [["r1", 0], ["r2", 1]]  # the fake live model plans `live_tool`


def test_a_live_row_the_safety_screen_stopped_is_not_compared() -> None:
    """The unsafe-phrase screen rejects before any inference in `run`, so the live row has no
    plan to compare; `plan --batch` does not screen, so the batch has one. Not a mismatch."""
    from knaif.evalsuite.runner import AgentOutput

    out = AgentOutput(id="r1", utterance="format the drive", plan=None, artifact=None,
                      outcome="reject", latency_ms=1.0, utterance_idx=0)  # fmt: skip
    compared, mismatches = batch_live_mismatches([out], {("r1", 0): _plan("reject")}, {("r1", 0)})
    assert (compared, mismatches) == (0, [])


def test_a_live_row_without_a_plan_is_recorded_with_how_it_ended() -> None:
    """Not compared is not the same as fine: a live row that crashed (a backend that failed to
    load) has no plan either, and that is the failure the cold-start sample exists to catch."""
    from knaif.evalsuite.runner import AgentOutput

    def out(rid: str, **kw: Any) -> AgentOutput:
        base = {"id": rid, "utterance": "u", "plan": None, "artifact": None,
                "outcome": "reject", "latency_ms": 1.0, "utterance_idx": 0}  # fmt: skip
        return AgentOutput(**{**base, **kw})

    outputs = [out("screened"), out("crashed", outcome="error", error="backend failed"),
               out("planned", plan=_plan("trim_video"), outcome="plan"), out("not-live")]  # fmt: skip
    live = {("screened", 0), ("crashed", 0), ("planned", 0), ("never-ran", 0)}
    assert batch_live_unplanned(outputs, live) == [
        {"row": ["crashed", 0], "outcome": "error", "error": "backend failed"},
        {"row": ["never-ran", 0], "outcome": None, "error": "no output"},
        {"row": ["screened", 0], "outcome": "reject", "error": None},
    ]


# ── a batch-planned board is not acceptance evidence until the rules say so ───


def test_accept_native_refuses_a_batch_planned_board(tmp_path) -> None:
    from knaif.evalsuite import cli

    board = tmp_path / "board.json"
    board.write_text(
        json.dumps({"lane_kind": "native_cli", "packaged_layout": True, "planning": "batch"}),
        encoding="utf-8",
    )
    args = argparse.Namespace(skill="ffmpeg", current=str(board), safety=None)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_accept_native(args)
    assert "batch" in str(exc.value.code)


# ── `evalsuite native --batch-plan`: the board says how it was planned ─────────


def _native_args(tmp_path: Path, **kw) -> argparse.Namespace:
    corpus = tmp_path / "eval.jsonl"
    corpus.write_text(
        "\n".join(json.dumps(r) for r in (
            {"id": "r1", "utterances": ["convert clip.mp4 to mkv"], "expected_outcome": "plan"},
            {"id": "r2", "utterances": ["trim clip.mp4", "cut clip.mp4"], "expected_outcome": "plan"},
        )) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    base: dict[str, Any] = {
        "verifier": "success", "corpus": str(corpus), "skill": "ffmpeg",
        "sandbox": str(tmp_path / "sb"), "fixture_dir": str(_fixtures(tmp_path)),
        "config": "eval_backends.yaml", "lane": "t", "allow_unpackaged": True, "limit": None,
        "verbose": False, "only": None, "save": None, "min_coverage": 2.0,
        "batch_plan": False, "live_sample": None, "batch_plans": None, "subset": False,
        "store": None,
    }  # fmt: skip
    return argparse.Namespace(**{**base, **kw})


def _patch_native(tmp_path, monkeypatch) -> dict[str, Any]:
    from knaif.evalsuite import scoring

    lane = _lane(tmp_path)
    lane.binary.write_bytes(b"bin")
    lane.model_path.write_bytes(b"gguf")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(native_lane, "load_lane", lambda *a, **k: lane)
    monkeypatch.setattr(
        native_lane,
        "detect_backend",
        lambda *a, **k: native_lane.BackendMeasurement(placement={"CUDA0": 37}, enumerated="CUDA0"),
    )

    def fake_batch(lane, skill, utterances, **kw):
        seen["batch"] = list(utterances)
        return [_plan("convert_video") for _ in utterances]

    def fake_rows(*a, **kw):
        seen["rows_kw"] = kw
        return []

    monkeypatch.setattr(native_lane, "batch_plan", fake_batch)
    monkeypatch.setattr(native_lane, "run_native_corpus", fake_rows)
    monkeypatch.setattr(
        scoring, "score_corpus", lambda *a, **k: {"coverage": 1.0, "unattempted": 0}
    )
    return seen


def test_a_batch_run_plans_the_selected_rows_once_and_records_it(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    seen = _patch_native(tmp_path, monkeypatch)
    only = tmp_path / "only.json"
    only.write_text(json.dumps([["r2", 1], ["r1", 0]]), encoding="utf-8")
    live = tmp_path / "live.json"
    live.write_text(json.dumps([["r1", 0]]), encoding="utf-8")
    board = cli.cmd_native(
        _native_args(tmp_path, batch_plan=True, only=str(only), live_sample=str(live))
    )
    assert seen["batch"] == ["convert clip.mp4 to mkv", "cut clip.mp4"], "corpus order"
    assert set(seen["rows_kw"]["plans"]) == {("r1", 0), ("r2", 1)}
    assert seen["rows_kw"]["live"] == {("r1", 0)}
    assert board["planning"] == "batch"
    assert "--batch <rows>" in board["lane_entry_point"]
    assert {"plan_batch_s", "rows_s", "rows"} <= set(board["timing"])
    assert board["batch_live"]["sample"] == 1
    assert board["batch_live"]["rows"] == [["r1", 0]]
    # The fake lane runs nothing, so the live row has no output: recorded, not dropped.
    assert board["batch_live"]["unplanned"] == [
        {"row": ["r1", 0], "outcome": None, "error": "no output"}
    ]


def test_a_live_run_is_unchanged_and_carries_no_batch_fields(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    seen = _patch_native(tmp_path, monkeypatch)
    board = cli.cmd_native(_native_args(tmp_path))
    assert "batch" not in seen
    assert seen["rows_kw"]["plans"] is None
    assert "planning" not in board and "batch_live" not in board
    assert "--batch" not in board["lane_entry_point"]


def test_a_live_sample_without_batch_planning_is_refused(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    _patch_native(tmp_path, monkeypatch)
    live = tmp_path / "live.json"
    live.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit, match="cold-start"):
        cli.cmd_native(_native_args(tmp_path, live_sample=str(live)))


def test_plans_from_an_earlier_batch_are_used_without_planning_again(tmp_path, monkeypatch) -> None:
    """T0(b): execution alone, from a `plan --batch` output already recorded for this binary."""
    from knaif.evalsuite import cli

    seen = _patch_native(tmp_path, monkeypatch)
    recorded = tmp_path / "ffmpeg.inprocess.jsonl"
    recorded.write_text(
        "\n".join(json.dumps(_plan(t)) for t in ("convert_video", "trim_video", "trim_video"))
        + "\n",
        encoding="utf-8",
    )
    board = cli.cmd_native(_native_args(tmp_path, batch_plans=str(recorded)))
    assert "batch" not in seen, "no planning: the plans were given"
    assert seen["rows_kw"]["plans"][("r2", 1)] == _plan("trim_video")
    assert board["planning"] == "batch"
    assert board["timing"]["plan_batch_s"] is None
    assert board["plans_source"]["lines"] == 3
    assert len(board["plans_source"]["sha256"]) == 64


def test_recorded_plans_that_do_not_line_up_are_refused(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    _patch_native(tmp_path, monkeypatch)
    recorded = tmp_path / "short.jsonl"
    recorded.write_text(json.dumps(_plan("a")) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="1 line.* 3 selected"):
        cli.cmd_native(_native_args(tmp_path, batch_plans=str(recorded)))


def test_the_batch_file_is_found_from_the_folder_the_binary_runs_in(tmp_path, monkeypatch) -> None:
    """The binary runs with cwd = the batch folder, so a relative path to the batch file (the
    lane sandbox is relative by default) pointed nowhere: the first T0 CUDA stage, 2026-10-17."""
    seen: dict[str, Any] = {}

    def fake(argv, **kw):
        arg = Path(argv[argv.index("--batch") + 1])
        resolved = arg if arg.is_absolute() else Path(kw["cwd"]) / arg
        seen["exists"] = resolved.is_file()
        return subprocess.CompletedProcess(argv, 0, json.dumps(_plan("a")) + "\n", "")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(native_lane.subprocess, "run", fake)
    batch_plan(
        _lane(tmp_path),
        "ffmpeg",
        ["one"],
        fixture_dir=_fixtures(tmp_path),
        work_dir=Path("sb") / ".batch",
    )
    assert seen["exists"], "the binary would not find its batch file"


# -- the release subset (plan 2026-10-14-faster-release-evidence, D1/D2, T2b) --


def _with_subset(tmp_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Commit a subset beside the test corpus, derived from it as `release-subset` does."""
    from knaif.evalsuite.release_subset import SUBSET_FILE, derive_subset

    doc = derive_subset("ffmpeg", Path(args.corpus))
    (Path(args.corpus).parent / SUBSET_FILE).write_text(json.dumps(doc), encoding="utf-8")
    return doc


def test_a_subset_run_measures_the_committed_subset_and_says_so(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    seen = _patch_native(tmp_path, monkeypatch)
    args = _native_args(tmp_path, subset=True)
    doc = _with_subset(tmp_path, args)
    board = cli.cmd_native(args)
    assert seen["rows_kw"]["only"] == {("r1", 0)}, "one stratum: utterance 0 of its first row"
    assert board["population"] == {
        "kind": "subset",
        "corpus_sha256": doc["corpus_sha256"],
        "rows": 1,
    }


def test_a_stale_subset_is_refused(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    _patch_native(tmp_path, monkeypatch)
    args = _native_args(tmp_path, subset=True)
    _with_subset(tmp_path, args)
    with Path(args.corpus).open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"id": "r3", "utterances": ["x"], "expected_outcome": "clarify"}) + "\n"
        )
    with pytest.raises(SystemExit, match="regenerate"):
        cli.cmd_native(args)


def test_a_shard_of_the_subset_runs_and_one_outside_it_is_refused(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    seen = _patch_native(tmp_path, monkeypatch)
    inside, outside = tmp_path / "in.json", tmp_path / "out.json"
    inside.write_text(json.dumps([["r1", 0]]), encoding="utf-8")
    outside.write_text(json.dumps([["r2", 1]]), encoding="utf-8")
    args = _native_args(tmp_path, subset=True, only=str(inside))
    _with_subset(tmp_path, args)
    cli.cmd_native(args)
    assert seen["rows_kw"]["only"] == {("r1", 0)}
    args.only = str(outside)
    with pytest.raises(SystemExit, match="not in the release subset"):
        cli.cmd_native(args)


def test_a_full_run_records_no_population(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    _patch_native(tmp_path, monkeypatch)
    assert "population" not in cli.cmd_native(_native_args(tmp_path))


# -- the result store: record replies while planning, replay them (T2c) --


def test_a_replay_batch_runs_with_no_model_against_the_namespace(tmp_path, monkeypatch) -> None:
    rec = _Recorder(json.dumps(_plan("a")) + "\n")
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    monkeypatch.setenv("KNAIF_LLM_RECORD_DIR", "leaked from the parent shell")
    ns = tmp_path / "ns"
    batch_plan(_lane(tmp_path), "ffmpeg", ["one"], fixture_dir=_fixtures(tmp_path),
               work_dir=tmp_path / "batch", replay_dir=ns)  # fmt: skip
    call = rec.calls[0]
    assert "--model" not in call["argv"]
    assert call["env"]["KNAIF_LLM_BACKEND"] == "replay"
    assert call["env"]["KNAIF_LLM_REPLAY_DIR"] == str(ns.resolve())
    assert "KNAIF_LLM_RECORD_DIR" not in call["env"]


def test_a_recording_batch_plans_with_the_model_and_records(tmp_path, monkeypatch) -> None:
    rec = _Recorder(json.dumps(_plan("a")) + "\n")
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    ns = tmp_path / "ns"
    batch_plan(_lane(tmp_path), "ffmpeg", ["one"], fixture_dir=_fixtures(tmp_path),
               work_dir=tmp_path / "batch", record_dir=ns)  # fmt: skip
    call = rec.calls[0]
    assert "--model" in call["argv"]
    assert call["env"]["KNAIF_LLM_RECORD_DIR"] == str((tmp_path / "batch.record").resolve())
    assert "KNAIF_LLM_BACKEND" not in call["env"]
    assert ns.is_dir()


class _Recording(_Recorder):
    """A recording batch that writes one reply file per utterance, as the binary does."""

    def __init__(self, replies: dict[str, str], device: str) -> None:
        super().__init__("".join(json.dumps(_plan("a")) + "\n" for _ in replies), device)
        self.replies = replies

    def __call__(self, argv, **kw) -> subprocess.CompletedProcess:
        out = super().__call__(argv, **kw)
        staging = Path(kw["env"]["KNAIF_LLM_RECORD_DIR"])
        for key, text in self.replies.items():
            (staging / f"{key}.txt").write_text(text, encoding="utf-8")
        return out


def _record(tmp_path, monkeypatch, replies, device, ns):
    monkeypatch.setattr(native_lane.subprocess, "run", _Recording(replies, device))
    return batch_plan(_lane(tmp_path), "ffmpeg", list(replies), fixture_dir=_fixtures(tmp_path),
                      work_dir=tmp_path / "batch", record_dir=ns,
                      expect_device="CUDA0")  # fmt: skip


def test_recorded_replies_reach_the_namespace_once_the_batch_ran_where_expected(
    tmp_path, monkeypatch
) -> None:
    ns = tmp_path / "ns"
    _record(tmp_path, monkeypatch, {"k1": "r1", "k2": "r2"}, "CUDA0", ns)
    assert sorted(p.name for p in ns.iterdir()) == ["k1.txt", "k2.txt"]
    assert not (tmp_path / "batch.record").exists()


def test_replies_from_a_batch_on_another_device_never_reach_the_namespace(
    tmp_path, monkeypatch
) -> None:
    ns = tmp_path / "ns"
    with pytest.raises(native_lane.PlacementError):
        _record(tmp_path, monkeypatch, {"k1": "r1"}, "CPU", ns)
    assert not ns.exists() or not any(ns.iterdir())


def test_a_reply_that_differs_from_the_stored_one_moves_nothing(tmp_path, monkeypatch) -> None:
    ns = tmp_path / "ns"
    ns.mkdir()
    (ns / "k1.txt").write_text("stored", encoding="utf-8")
    with pytest.raises(RuntimeError, match="record conflict"):
        _record(tmp_path, monkeypatch, {"k1": "different", "k2": "new"}, "CUDA0", ns)
    assert sorted(p.name for p in ns.iterdir()) == ["k1.txt"]
    assert (ns / "k1.txt").read_text(encoding="utf-8") == "stored"


def test_store_rows_execute_by_replay_and_live_rows_never_see_the_store(
    tmp_path, monkeypatch
) -> None:
    rec = _Recorder()
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    monkeypatch.setenv("KNAIF_LLM_REPLAY_DIR", "leaked")
    plans = {("r1", 0): _plan("convert_video"), ("r2", 0): _plan("trim_video"),
             ("r2", 1): _plan("trim_video")}  # fmt: skip
    ns = tmp_path / "ns"
    run_native_corpus(_lane(tmp_path), "ffmpeg", _corpus(), fixture_dir=_fixtures(tmp_path),
                      sandbox=tmp_path / "sb", plans=plans, live={("r2", 1)},
                      replay_dir=ns)  # fmt: skip
    replayed, live = rec.calls[0], rec.calls[2]
    assert "--model" not in replayed["argv"]
    assert replayed["env"]["KNAIF_LLM_BACKEND"] == "replay"
    assert replayed["env"]["KNAIF_LLM_REPLAY_DIR"] == str(ns.resolve())
    assert "KNAIF_LLM_MOCK_RESPONSE" not in replayed["env"]
    assert "--model" in live["argv"]
    assert not {"KNAIF_LLM_BACKEND", "KNAIF_LLM_REPLAY_DIR", "KNAIF_LLM_RECORD_DIR"} & set(
        live["env"]
    )


def _patch_store(tmp_path, monkeypatch, recorded: set[str], *, leave_missing: bool = False):
    """A fake `batch_plan` that behaves like the binary against a namespace folder."""
    from knaif.evalsuite import scoring

    lane = _lane(tmp_path)
    lane.binary.write_bytes(b"bin")
    lane.model_path.write_bytes(b"gguf")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(native_lane, "load_lane", lambda *a, **k: lane)
    monkeypatch.setattr(
        native_lane,
        "detect_backend",
        lambda *a, **k: native_lane.BackendMeasurement(placement={"CUDA0": 37}, enumerated="CUDA0"),
    )

    def fake_batch(lane, skill, utterances, **kw):
        calls.append({"utterances": list(utterances), **kw})
        if kw.get("record_dir"):
            if not leave_missing:
                recorded.update(utterances)
                for u in utterances:  # the binary writes one file per prompt key
                    name = hashlib.sha256(u.encode()).hexdigest()
                    Path(kw["record_dir"], f"{name}.txt").write_text("reply", encoding="utf-8")
            return [_plan("live") for _ in utterances]
        miss = {"plan": [], "error": "replay miss: no stored reply for prompt x in y"}
        return [_plan("stored") if u in recorded else miss for u in utterances]

    seen: dict[str, Any] = {}
    monkeypatch.setattr(native_lane, "batch_plan", fake_batch)
    monkeypatch.setattr(native_lane, "run_native_corpus", lambda *a, **k: seen.update(k) or [])
    monkeypatch.setattr(
        scoring,
        "score_corpus",
        lambda *a, **k: {"coverage": 1.0, "unattempted": 0, "rows": [
            {"id": "r1", "utterance_idx": 0}, {"id": "r2", "utterance_idx": 0},
            {"id": "r2", "utterance_idx": 1}]},
    )  # fmt: skip
    return calls, seen


def test_a_first_store_run_plans_everything_with_the_model_and_says_so(
    tmp_path, monkeypatch
) -> None:
    from knaif.evalsuite import cli

    recorded: set[str] = set()
    calls, seen = _patch_store(tmp_path, monkeypatch, recorded)
    board = cli.cmd_native(_native_args(tmp_path, store=str(tmp_path / "store")))
    kinds = ["replay" if c.get("replay_dir") else "record" for c in calls]
    assert kinds == ["replay", "record", "replay"]
    assert len(calls[1]["utterances"]) == 3, "every miss is planned with the model"
    assert seen["replay_dir"] == calls[2]["replay_dir"]
    assert board["planning"] == "batch"
    assert board["store"]["measured"] == 3 and board["store"]["reused"] == 0
    assert {r["reply"] for r in board["rows"]} == {"measured"}
    key = json.loads((calls[0]["replay_dir"] / "key.json").read_text(encoding="utf-8"))
    from knaif.evalsuite.matrix import current_os

    assert key["platform"] == current_os(), "replies are kept per platform (owner, 2026-10-19)"
    assert key["backend"] == "cuda"


def test_a_second_store_run_reuses_every_reply_and_plans_nothing(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    recorded = {"convert clip.mp4 to mkv", "trim clip.mp4", "cut clip.mp4"}
    calls, _ = _patch_store(tmp_path, monkeypatch, recorded)
    board = cli.cmd_native(_native_args(tmp_path, store=str(tmp_path / "store")))
    assert [bool(c.get("replay_dir")) for c in calls] == [True], "no model, no second pass"
    assert board["store"]["measured"] == 0 and board["store"]["reused"] == 3
    assert {r["reply"] for r in board["rows"]} == {"reused"}
    assert board["timing"]["plan_batch_s"] is None


def test_a_reply_recorded_but_still_missing_stops_the_run(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    _patch_store(tmp_path, monkeypatch, set(), leave_missing=True)
    with pytest.raises(SystemExit, match="still missing"):
        cli.cmd_native(_native_args(tmp_path, store=str(tmp_path / "store")))


def test_a_store_run_states_its_entry_point(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    _patch_store(tmp_path, monkeypatch, set())
    board = cli.cmd_native(_native_args(tmp_path, store=str(tmp_path / "store")))
    assert "KNAIF_LLM_BACKEND=replay" in board["lane_entry_point"]
    assert "mock" not in board["lane_entry_point"]


# -- audit 2026-10-19 (Fable): store provenance --


def test_a_developer_build_cannot_write_the_release_store(tmp_path, monkeypatch) -> None:
    """Only the frozen artifact may put replies in `evals/store`; a developer build keeps its own."""
    from knaif.evalsuite import cli, store

    _patch_store(tmp_path, monkeypatch, set())
    shared = tmp_path / "evals" / "store"
    monkeypatch.setattr(store, "STORE_ROOT", shared)
    with pytest.raises(SystemExit, match="developer build"):
        cli.cmd_native(_native_args(tmp_path, store=str(shared)))
    assert not shared.exists(), "refused before anything was written"


def test_every_recorded_reply_names_the_binary_that_recorded_it(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    calls, _ = _patch_store(tmp_path, monkeypatch, set())
    cli.cmd_native(
        _native_args(tmp_path, store=str(tmp_path / "store"), save=str(tmp_path / "run"))
    )
    log = calls[0]["replay_dir"] / "recorded.jsonl"
    entries = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 3
    assert {e["binary_sha256"] for e in entries} == {hashlib.sha256(b"bin").hexdigest()}
    assert {e["run"] for e in entries} == {"run"}, "the run folder's name, never a path"
    assert all(len(e["key"]) == 64 for e in entries)


def test_cold_start_rows_are_labelled_live_not_reused(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    recorded = {"convert clip.mp4 to mkv", "trim clip.mp4", "cut clip.mp4"}
    _patch_store(tmp_path, monkeypatch, recorded)
    live = tmp_path / "live.json"
    live.write_text(json.dumps([["r2", 1]]), encoding="utf-8")
    board = cli.cmd_native(
        _native_args(tmp_path, store=str(tmp_path / "store"), live_sample=str(live))
    )
    reply = {(r["id"], r["utterance_idx"]): r["reply"] for r in board["rows"]}
    assert reply == {("r1", 0): "reused", ("r2", 0): "reused", ("r2", 1): "live"}


# -- every process that loads the model reports where it ran (audit 2026-10-19, P2) --


def _batch(tmp_path, **kw):
    return batch_plan(_lane(tmp_path), "ffmpeg", ["one"], fixture_dir=_fixtures(tmp_path),
                      work_dir=tmp_path / "batch", **kw)  # fmt: skip


def test_a_recording_batch_on_the_expected_device_passes(tmp_path, monkeypatch) -> None:
    rec = _Recorder(json.dumps(_plan("a")) + "\n", device="CUDA0")
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    _batch(tmp_path, record_dir=tmp_path / "ns", expect_device="CUDA0")
    assert "--verbose" in rec.calls[0]["argv"], "the load trace is how the device is known"


def test_a_recording_batch_on_another_device_voids_the_cell(tmp_path, monkeypatch) -> None:
    """A CUDA cell whose batch fell back to the CPU would record CPU replies into the CUDA
    namespace, where every later release would reuse them as CUDA's."""
    rec = _Recorder(json.dumps(_plan("a")) + "\n", device="CPU")
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    with pytest.raises(native_lane.PlacementError, match="CPU.*CUDA0"):
        _batch(tmp_path, record_dir=tmp_path / "ns", expect_device="CUDA0")


def test_a_batch_that_does_not_say_where_it_ran_voids_the_cell(tmp_path, monkeypatch) -> None:
    rec = _Recorder(json.dumps(_plan("a")) + "\n", device=None)
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    with pytest.raises(native_lane.PlacementError, match="did not report"):
        _batch(tmp_path, record_dir=tmp_path / "ns", expect_device="CUDA0")


def test_a_replay_batch_loads_no_model_and_is_not_checked(tmp_path, monkeypatch) -> None:
    rec = _Recorder(json.dumps(_plan("a")) + "\n", device=None)
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    _batch(tmp_path, replay_dir=tmp_path / "ns", expect_device="CUDA0")
    assert "--verbose" not in rec.calls[0]["argv"]


def _run_on(tmp_path, monkeypatch, device, plans=None, live=None):
    rec = _Recorder(device=device)
    monkeypatch.setattr(native_lane.subprocess, "run", rec)
    outputs = run_native_corpus(_lane(tmp_path), "ffmpeg", _corpus(),
                                fixture_dir=_fixtures(tmp_path), sandbox=tmp_path / "sb",
                                plans=plans, live=live, expect_device="CUDA0")  # fmt: skip
    return outputs, rec


def test_live_rows_on_the_expected_device_pass_and_replayed_rows_are_not_checked(
    tmp_path, monkeypatch
) -> None:
    plans = {("r1", 0): _plan("convert_video"), ("r2", 0): _plan("trim_video"),
             ("r2", 1): _plan("trim_video")}  # fmt: skip
    outputs, rec = _run_on(tmp_path, monkeypatch, "CUDA0", plans, live={("r2", 1)})
    assert ["--verbose" in c["argv"] for c in rec.calls] == [False, False, True]
    assert outputs[2].plan == _plan("live_tool"), "the trace does not disturb the plan dump"


def test_a_live_row_on_another_device_voids_the_cell(tmp_path, monkeypatch) -> None:
    with pytest.raises(native_lane.PlacementError, match=r"r1\[0\].*CPU"):
        _run_on(tmp_path, monkeypatch, "CPU")


def test_a_live_row_that_loaded_no_model_is_not_a_device_mismatch(tmp_path, monkeypatch) -> None:
    """The unsafe-phrase screen refuses before the model loads; such a row says nothing about
    the device, and `batch_live_unplanned` already reports how it ended."""
    outputs, _ = _run_on(tmp_path, monkeypatch, None)
    assert len(outputs) == 3


def test_every_lane_process_plans_in_process_never_through_a_daemon(tmp_path, monkeypatch) -> None:
    """L4 measures the binary loading the model itself (Part I). A daemon holding the model
    would answer without recording into the store or saying where its layers ran."""
    monkeypatch.delenv("KNAIF_NO_DAEMON", raising=False)
    _, rec = _run(tmp_path, monkeypatch, {("r1", 0): _plan("convert_video")})
    rec.batch_stdout = json.dumps(_plan("a")) + "\n"
    batch_plan(_lane(tmp_path), "ffmpeg", ["one"], fixture_dir=tmp_path / "fixtures",
               work_dir=tmp_path / "batch", record_dir=tmp_path / "ns")  # fmt: skip
    assert len(rec.calls) == 4
    assert all(c["env"].get("KNAIF_NO_DAEMON") == "1" for c in rec.calls)


def test_the_cell_expects_the_probed_device_everywhere_and_a_mismatch_is_void(
    tmp_path, monkeypatch
) -> None:
    from knaif.evalsuite import cli

    seen = _patch_native(tmp_path, monkeypatch)
    save = tmp_path / "board.json"
    args = _native_args(tmp_path, batch_plan=True)
    cli.cmd_native(args)
    assert seen["rows_kw"]["expect_device"] == "CUDA0"

    def on_cpu(*a, **kw):
        raise native_lane.PlacementError("r1[0] ran the model on CPU; the cell expected CUDA0")

    monkeypatch.setattr(native_lane, "run_native_corpus", on_cpu)
    args.save = str(save)
    with pytest.raises(SystemExit, match=r"VOID: r1\[0\] ran the model on CPU"):
        cli.cmd_native(args)
    assert not save.exists()
