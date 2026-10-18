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
    monkeypatch.setattr(gate, "record_layers", lambda skill, root, layers: seen.update(layers))
    return seen


def test_python_s_own_grades_on_the_real_subset_are_accepted_and_not_recorded(
    tmp_path, recorded, capsys
) -> None:
    from knaif.evalsuite import cli

    cli.cmd_accept_native(_write_cell(tmp_path, _real_subset_board()))
    out = capsys.readouterr().out
    assert "ACCEPTED" in out and "NOT ACCEPTED" not in out
    assert "not L4 evidence" in out
    assert recorded == {}, "a subset verdict is a measurement until T1's rules adopt it"


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
