"""L4 on the release subset (plan 2026-10-14-faster-release-evidence, D2, T2b).

The bar is per-row regression against the accepted Python run on the same rows and model, the
subset's aggregates within the parity allowance of Python's on those rows, coverage complete and
safety at 100% on the binary. The S2 slice floors are not applied: over a subset most slices are
too small for a rate to mean anything.
"""

from __future__ import annotations

from typing import Any

from knaif.evalsuite.acceptance import check_native_subset_acceptance
from knaif.evalsuite.outcomes import POLICY_VERSION

SHA = "a" * 64
SPEC = {
    "policy_version": POLICY_VERSION,
    "verifier": "success",
    "aggregate": {"outcome_accuracy": 0.99},
    "slices": {"trim": {"outcome_accuracy": 0.99}},
    "safety": {"corpus": "data/safety_test.jsonl", "pass_rate": 1.0},
}
BASELINE = {
    "verifier": "success",
    "scoring_policy": POLICY_VERSION,
    "backend_public_name": "m",
    "total": 10,
}
SUBSET = {"corpus_sha256": SHA, "rows": [["a", 0], ["b", 0], ["c", 0]]}
SAFETY = {
    "total": 1,
    "pass_rate": 1.0,
    "lane_kind": "native_cli",
    "packaged_layout": True,
    "backend_public_name": "m",
}


def _row(rid: str, ok: bool, score: float | None = 1.0, idx: int = 0) -> dict[str, Any]:
    return {  # the fields a scored board row carries (`scoring.score_corpus`)
        "id": rid,
        "utterance": f"{rid} utterance",
        "utterance_idx": idx,
        "expected_outcome": "plan",
        "actual_outcome": "plan" if ok else "clarify",
        "outcome_correct": ok,
        "tags": ["trim"],
        "latency_ms": 1.0,
        "error": None,
        "artifact": None,
        "plan": None,
        "knaif_score": score,
        "knaif_matched": [],
        "knaif_failed": [],
        "verifier_kind": None,
        "baseline_score": None,
        "is_warmup": False,
    }


def _python(*rows: dict[str, Any]) -> dict[str, Any]:
    extra = [_row("z", True, idx=1)]  # a row outside the subset: never compared
    return {"verifier": "success", "backend_public_name": "m", "rows": [*rows, *extra]}


def _native(*rows: dict[str, Any], **kw: Any) -> dict[str, Any]:
    n = len(rows)
    scored = [r for r in rows if r["knaif_score"] is not None]
    board = {
        "verifier": "success",
        "scoring_policy": POLICY_VERSION,
        "backend_public_name": "m",
        "total": n,
        "coverage": 1.0,
        "unattempted": 0,
        "outcome_accuracy": sum(r["outcome_correct"] for r in rows) / n,
        "avg_knaif_score": sum(r["knaif_score"] for r in scored) / len(scored),
        "by_tag": {"trim": {"outcome_accuracy": 0.0, "total": n}},
        "population": {"kind": "subset", "corpus_sha256": SHA, "rows": n},
        "rows": list(rows),
    }
    board.update(kw)
    return board


def _check(python: dict[str, Any], native: dict[str, Any], safety=SAFETY):
    return check_native_subset_acceptance(SPEC, BASELINE, python, native, SUBSET, safety)


ALL_PASS = (_row("a", True), _row("b", True), _row("c", True))


def test_the_same_grades_pass_and_slice_floors_are_not_applied() -> None:
    report = _check(_python(*ALL_PASS), _native(*ALL_PASS))
    assert report.ok, report.summary()


def test_a_row_python_passed_and_native_failed_is_a_regression() -> None:
    native = _native(_row("a", True), _row("b", False, 0.0), _row("c", True))
    report = _check(_python(*ALL_PASS), native)
    assert any(v.name == "b#0" and v.kind == "regression" for v in report.violations)


def test_a_row_python_failed_may_fail_on_native_too() -> None:
    rows = (_row("a", True), _row("b", False, 0.0), _row("c", True))
    python = _python(*rows)
    # 2/3 on both sides: within the allowance, and no row got worse.
    assert _check(python, _native(*rows)).ok


def test_the_aggregate_must_stay_within_the_allowance_of_python_on_the_same_rows() -> None:
    native = _native(_row("a", True, 0.9), _row("b", True, 0.9), _row("c", True, 0.9))
    report = _check(_python(*ALL_PASS), native)
    assert any(v.name == "avg_knaif_score" for v in report.violations)


def test_a_board_that_is_not_this_subset_is_refused() -> None:
    other = _native(*ALL_PASS, population={"kind": "subset", "corpus_sha256": "b" * 64, "rows": 3})
    assert any(v.name == "population" for v in _check(_python(*ALL_PASS), other).violations)
    full = _native(*ALL_PASS)
    del full["population"]
    assert any(v.name == "population" for v in _check(_python(*ALL_PASS), full).violations)


def test_a_subset_row_the_native_board_lacks_is_refused() -> None:
    native = _native(_row("a", True), _row("b", True))
    report = _check(_python(*ALL_PASS), native)
    assert any(v.name == "population" and "c#0" in v.message for v in report.violations)


def test_a_subset_row_the_python_run_lacks_is_refused() -> None:
    report = _check(_python(_row("a", True), _row("b", True)), _native(*ALL_PASS))
    assert any(v.name == "python_rows" for v in report.violations)


def test_another_model_is_refused() -> None:
    report = _check(_python(*ALL_PASS), _native(*ALL_PASS, backend_public_name="other"))
    assert any(v.name == "model" for v in report.violations)


def test_safety_is_still_required() -> None:
    report = _check(_python(*ALL_PASS), _native(*ALL_PASS), safety=None)
    assert not report.ok


# -- the command, against the committed snapshot, its source run and the committed subset --

import argparse  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from knaif.evalsuite.scoring import aggregate_scored_rows  # noqa: E402
from knaif.evalsuite.snapshot import load_source_run  # noqa: E402


def _real_subset_board(flip: int | None = None) -> dict[str, Any]:
    """A native board with Python's own grades on ffmpeg's committed subset (4B baseline)."""
    snapshot = json.loads(Path("skills/ffmpeg/data/eval_snapshot.json").read_text("utf-8"))
    subset = json.loads(Path("skills/ffmpeg/data/release_subset.json").read_text("utf-8"))
    python = load_source_run(snapshot, Path("."))
    by_key = {(r["id"], r["utterance_idx"]): dict(r) for r in python["rows"]}
    rows = [by_key[(rid, idx)] for rid, idx in subset["rows"]]
    if flip is not None:
        passed = [r for r in rows if r["outcome_correct"]]
        passed[flip]["outcome_correct"] = False
    board = aggregate_scored_rows(rows, [], "success")
    board.update(
        scoring_policy=POLICY_VERSION,
        backend=snapshot["backend"],
        backend_public_name=snapshot["backend_public_name"],
        lane="native-cli",
        lane_kind="native_cli",
        packaged_layout=True,
        population={"kind": "subset", "corpus_sha256": subset["corpus_sha256"], "rows": len(rows)},
        # Windows CUDA: a platform 1.2.0 measured in full, so D1 lets the subset stand in.
        os="windows-x64",
        compute_backend="CUDA0",
    )
    return board


def _write_cell(tmp_path: Path, board: dict[str, Any]) -> argparse.Namespace:
    current, safety = tmp_path / "board.json", tmp_path / "safety.json"
    current.write_text(json.dumps(board), encoding="utf-8")
    safety_rows = [
        json.loads(ln)["id"]
        for ln in Path("skills/ffmpeg/data/safety_test.jsonl").read_text("utf-8").splitlines()
        if ln.strip()
    ]
    safety.write_text(
        json.dumps(
            {
                "total": len(safety_rows),
                "passed": len(safety_rows),
                "pass_rate": 1.0,
                "unsafe": 0,
                "lane_kind": "native_cli",
                "packaged_layout": True,
                "backend": board["backend_public_name"],
                # The lane's safety file names the binary, model, backend and OS of its board.
                **{k: board[k] for k in ("os", "compute_backend") if k in board},
            }
        ),
        encoding="utf-8",
    )
    return argparse.Namespace(
        skill="ffmpeg", current=str(current), safety=str(safety), min_coverage=None,
        population="subset",
    )  # fmt: skip


@pytest.fixture()
def recorded(monkeypatch):
    from knaif.evalsuite import gate

    seen: dict = {}

    def _fake(skill, root, layers):
        seen.update(layers)
        return Path("evals/acceptance") / f"{skill}.json"

    monkeypatch.setattr(gate, "record_layers", _fake)
    return seen


def test_python_s_own_grades_on_the_real_subset_are_accepted_and_recorded(
    tmp_path, recorded, capsys
) -> None:
    from knaif.evalsuite import cli

    board = _real_subset_board()
    cli.cmd_accept_native(_write_cell(tmp_path, board))
    out = capsys.readouterr().out
    assert "ACCEPTED" in out and "NOT ACCEPTED" not in out
    entry = recorded["L4"]
    assert entry["passed"] is True
    assert entry["cell"] == f"{board['backend_public_name']}|windows-x64|cuda"
    assert entry["population"] == board["population"]


def test_a_platform_never_measured_in_full_cannot_start_on_the_subset(tmp_path, recorded) -> None:
    """D1: a skill's first release on a platform measures the full corpus there."""
    from knaif.evalsuite import cli

    board = _real_subset_board()
    board["os"] = "freebsd-x64"
    with pytest.raises(SystemExit, match="full corpus"):
        cli.cmd_accept_native(_write_cell(tmp_path, board))
    assert recorded == {}


def _batch(board: dict[str, Any], **live: Any) -> dict[str, Any]:
    sample = [[f"r{i}", 0] for i in range(30)]
    board["planning"] = "batch"
    board["batch_live"] = {"sample": 30, "compared": 30, "mismatches": [], "rows": sample,
                           "unplanned": [], **live}  # fmt: skip
    return board


def test_a_batch_planned_subset_cell_whose_cold_start_held_is_accepted(
    tmp_path, recorded, capsys
) -> None:
    from knaif.evalsuite import cli

    cli.cmd_accept_native(_write_cell(tmp_path, _batch(_real_subset_board())))
    assert recorded["L4"]["passed"] is True
    assert recorded["L4"]["planning"] == "batch"


def test_a_cold_start_mismatch_sends_the_cell_back_to_a_live_run(tmp_path, recorded) -> None:
    from knaif.evalsuite import cli

    board = _batch(_real_subset_board(), mismatches=[["ffmpeg_001", 0]])
    with pytest.raises(SystemExit, match="live"):
        cli.cmd_accept_native(_write_cell(tmp_path, board))
    assert recorded == {}


def test_a_batch_planned_full_corpus_board_is_still_refused(tmp_path, recorded) -> None:
    from knaif.evalsuite import cli

    args = _write_cell(tmp_path, _batch(_real_subset_board()))
    args.population = "full"
    with pytest.raises(SystemExit, match="subset"):
        cli.cmd_accept_native(args)


def test_one_row_python_passed_failing_on_the_binary_is_not_accepted(
    tmp_path, recorded, capsys
) -> None:
    from knaif.evalsuite import cli

    with pytest.raises(SystemExit):
        cli.cmd_accept_native(_write_cell(tmp_path, _real_subset_board(flip=0)))
    assert "[regression]" in capsys.readouterr().out


def test_the_full_mode_refuses_a_subset_board(tmp_path, recorded) -> None:
    from knaif.evalsuite import cli

    args = _write_cell(tmp_path, _real_subset_board())
    args.population = "full"
    with pytest.raises(SystemExit, match="--population subset"):
        cli.cmd_accept_native(args)


def test_a_source_run_whose_bytes_changed_is_refused(tmp_path) -> None:
    board = tmp_path / "evals" / "runs" / "r" / "b.json"
    board.parent.mkdir(parents=True)
    board.write_bytes(b'{"rows": []}\n')
    snapshot = {
        "source_run": {"folder": "evals/runs/r", "scoreboard": "b.json", "sha256": "0" * 64}
    }
    with pytest.raises(ValueError, match="sha256"):
        load_source_run(snapshot, tmp_path)


def test_a_source_run_is_found_whatever_the_checkout_s_line_endings(tmp_path) -> None:
    import hashlib

    board = tmp_path / "evals" / "runs" / "r" / "b.json"
    board.parent.mkdir(parents=True)
    board.write_bytes(b'{\r\n "rows": []\r\n}\r\n')
    sha = hashlib.sha256(board.read_bytes()).hexdigest()
    board.write_bytes(board.read_bytes().replace(b"\r\n", b"\n"))  # a Linux checkout
    snapshot = {"source_run": {"folder": "evals/runs/r", "scoreboard": "b.json", "sha256": sha}}
    assert load_source_run(snapshot, tmp_path) == {"rows": []}


def test_a_snapshot_without_a_source_run_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError, match="source_run"):
        load_source_run({}, tmp_path)


# -- the cold-start check (Part I, I5) --

from knaif.evalsuite.acceptance import cold_start_problems  # noqa: E402


def _cold(**live: Any) -> dict[str, Any]:
    return _batch({}, **live)


def test_a_live_board_needs_no_cold_start() -> None:
    assert cold_start_problems({"rows": []}) == []


def test_a_held_cold_start_passes() -> None:
    assert cold_start_problems(_cold()) == []


def test_a_rejection_by_the_screen_is_not_a_cold_start_problem() -> None:
    unplanned = [{"row": ["r0", 0], "outcome": "reject", "error": None}]
    assert cold_start_problems(_cold(compared=29, unplanned=unplanned)) == []


def test_a_short_sample_fails() -> None:
    (p,) = cold_start_problems(_cold(sample=29, rows=[["r", 0]] * 29))
    assert "30" in p


def test_a_mismatch_fails() -> None:
    (p,) = cold_start_problems(_cold(mismatches=[["r3", 0]]))
    assert "r3#0" in p


def test_a_live_row_that_failed_fails() -> None:
    unplanned = [{"row": ["r0", 0], "outcome": "error", "error": "backend failed to load"}]
    (p,) = cold_start_problems(_cold(compared=29, unplanned=unplanned))
    assert "backend failed to load" in p


def test_a_board_that_predates_the_unplanned_record_fails() -> None:
    board = _cold()
    del board["batch_live"]["unplanned"]
    assert cold_start_problems(board)


# -- D1: which platforms have had a full-corpus cell --

from knaif.evalsuite.gate import earlier_platform_cell  # noqa: E402


def _archive(root: Path, version: str, cells: dict[str, dict[str, Any]]) -> None:
    path = root / "evals" / "acceptance" / "releases" / version / "x.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"skill": "x", "layers": {"L4": {"cells": cells}}}), "utf-8")


def test_an_earlier_full_cell_on_the_platform_is_found(tmp_path) -> None:
    _archive(tmp_path, "1.0.0", {"m|linux-x64|cpu": {"passed": False}})
    _archive(tmp_path, "1.1.0", {"m|linux-x64|cpu": {"passed": True, "composed": True}})
    assert earlier_platform_cell("x", tmp_path, "linux-x64", "cpu") == "1.1.0"


def test_a_subset_cell_or_another_platform_does_not_count(tmp_path) -> None:
    _archive(tmp_path, "1.0.0", {
        "m|linux-x64|cpu": {"passed": True, "population": {"kind": "subset"}},
        "m|linux-x64|cuda": {"passed": True},
    })  # fmt: skip
    assert earlier_platform_cell("x", tmp_path, "linux-x64", "cpu") is None


def test_an_owner_excepted_cell_counts(tmp_path) -> None:
    _archive(tmp_path, "1.0.0", {"m|macos|mtl": {"passed": False, "owner_exception": {"r": 1}}})
    assert earlier_platform_cell("x", tmp_path, "macos", "mtl") == "1.0.0"


@pytest.mark.parametrize(
    "os_id,backend",
    [("windows-x64", "cuda"), ("windows-x64", "vulkan"), ("windows-x64", "cpu"),
     ("linux-x64", "cuda"), ("linux-x64", "cpu"), ("macos", "mtl"), ("macos", "cpu")],
)  # fmt: skip
def test_every_shipped_platform_has_had_its_full_cell(os_id: str, backend: str) -> None:
    for skill in ("ffmpeg", "documents"):
        assert earlier_platform_cell(skill, Path("."), os_id, backend), (skill, os_id, backend)


def test_the_gate_names_a_subset_cell(tmp_path: Path) -> None:
    """Part I, I9: a cell measured on the release subset says so wherever the gate reports it."""
    from knaif.evalsuite.gate import evaluate_skill, record_layers
    from knaif.evalsuite.matrix import cell_key

    from .test_acceptance_matrix import MODEL, _contracts_and_l3, _matrix
    from .test_gate import make_tree

    tree = make_tree(tmp_path)
    _matrix(tree)
    _contracts_and_l3(tree)
    cuda = cell_key(MODEL, "windows-x64", "cuda")
    subset = {"kind": "subset", "corpus_sha256": SHA, "rows": 3}
    record_layers(
        "demo", tree, {"L4": {"cell": cuda, "summary": "ok", "passed": True, "population": subset}}
    )
    record_layers(
        "demo", tree, {"L4": {"cell": cell_key(MODEL, "windows-x64", "cpu"), "summary": "ok",
                              "passed": True}}
    )  # fmt: skip
    l4 = next(s for s in evaluate_skill("demo", tree, "supported").layers if s.layer == "L4")
    assert l4.state == "valid"
    assert "release subset" in l4.detail and cuda in l4.detail


def test_an_l3_record_over_the_subset_says_so(tmp_path: Path) -> None:
    from knaif.evalsuite.gate import load_acceptance_record, record_from_parity_run

    from .test_gate import make_tree

    tree = make_tree(tmp_path)
    run = tree / "evals" / "parity" / "2026-10-19_l3-subset"
    run.mkdir(parents=True)
    pop = {"kind": "subset", "corpus_sha256": SHA}
    meta = {"result": {"port_bugs": 0, "native_not_implemented": 0, "passed": True,
                       "plan_disagreement_rate": 0.0, "max_plan_disagreement": 0.0411},
            "model": {"sha256": "m" * 64}, "binary": {"sha256": "b" * 64}, "population": pop}  # fmt: skip
    (run / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    record_from_parity_run("demo", tree, run)
    entry = (load_acceptance_record("demo", tree) or {})["layers"]["L3"]
    entry = next(iter(entry["cells"].values())) if "cells" in entry else entry
    assert entry["population"] == pop


# -- audit 2026-10-19 (Fable): a worse file is a regression; the Python run is bound to the corpus --


def test_a_row_whose_file_scores_lower_than_python_s_is_a_regression() -> None:
    """Owner, 2026-10-19: the right kind of answer with a worse file still fails the row."""
    native = _native(_row("a", True), _row("b", True, 0.5), _row("c", True))
    report = _check(_python(*ALL_PASS), native)
    (v,) = [v for v in report.violations if v.kind == "regression"]
    assert v.name == "b#0" and "knaif" in v.message


def test_a_regression_message_shows_both_plans_and_commands() -> None:
    nat = _row("b", False, 0.0)
    nat["plan"] = {"plan": [{"tool": "clarify", "args": {}}]}
    py = _row("b", True)
    py["plan"] = {"plan": [{"tool": "trim_video", "args": {}}]}
    py["artifact"] = "ffmpeg -i clip.mp4 out.mp4"
    report = _check(
        _python(_row("a", True), py, _row("c", True)),
        _native(_row("a", True), nat, _row("c", True)),
    )
    (v,) = [v for v in report.violations if v.kind == "regression"]
    assert (
        "trim_video" in v.message and "clarify" in v.message and "ffmpeg -i clip.mp4" in v.message
    )


def test_the_python_run_must_have_graded_the_rows_as_they_stand() -> None:
    from knaif.evalsuite.corpus import CorpusRow

    corpus = [
        CorpusRow(id=r, utterances=[f"{r} utterance"], expected_outcome="plan") for r in "abc"
    ]
    assert check_native_subset_acceptance(
        SPEC, BASELINE, _python(*ALL_PASS), _native(*ALL_PASS), SUBSET, SAFETY, corpus=corpus
    ).ok
    corpus[1] = CorpusRow(id="b", utterances=["b reworded"], expected_outcome="plan")
    report = check_native_subset_acceptance(
        SPEC, BASELINE, _python(*ALL_PASS), _native(*ALL_PASS), SUBSET, SAFETY, corpus=corpus
    )
    assert any(v.name == "python_rows" and "b#0" in v.message for v in report.violations)
    corpus[1] = CorpusRow(id="b", utterances=["b utterance"], expected_outcome="clarify")
    report = check_native_subset_acceptance(
        SPEC, BASELINE, _python(*ALL_PASS), _native(*ALL_PASS), SUBSET, SAFETY, corpus=corpus
    )
    assert any(v.name == "python_rows" for v in report.violations)
