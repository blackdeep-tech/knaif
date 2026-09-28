"""An owner's exception to one failing cell: recorded beside the verdict, never instead of it.

knaif 1.2.0's 1.7B Vulkan ffmpeg cell missed one required slice (batch, 25 of 29 where 26 were
needed); the owner released it anyway (R5c T8, 2026-09-28). Without a way to record that, the gate
either stays red for the whole release or the matrix loses the cell, and dropping it from the
fingerprinted matrix would invalidate every other record. A waiver keeps the failing verdict,
states the decision next to it, and is bounded: quality thresholds only, the exact verdict it was
given for, current evidence only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.gate import evaluate_skill, record_layers, waive_cell
from knaif.evalsuite.matrix import cell_key

from .test_acceptance_matrix import MODEL, _contracts_and_l3, _matrix
from .test_gate import make_tree

SLICE_MISS = (
    "NOT ACCEPTED - 1 of 45 thresholds unmet: [slice] slice 'batch' outcome_accuracy 0.862 "
    "< floor 0.896 (n=29)"
)
CPU = cell_key(MODEL, "windows-x64", "cpu")
CUDA = cell_key(MODEL, "windows-x64", "cuda")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    tree = make_tree(tmp_path)
    _matrix(tree)
    _contracts_and_l3(tree)
    record_layers("demo", tree, {"L4": {"cell": CUDA, "summary": "ACCEPTED", "passed": True}})
    return tree


def _fail(tree: Path, summary: str = SLICE_MISS) -> None:
    record_layers("demo", tree, {"L4": {"cell": CPU, "summary": summary, "passed": False}})


def _l4(tree: Path):
    gate = evaluate_skill("demo", tree, "supported")
    return gate, next(s for s in gate.layers if s.layer == "L4")


def _cell(tree: Path) -> dict:
    record = json.loads((tree / "evals" / "acceptance" / "demo.json").read_text(encoding="utf-8"))
    return record["layers"]["L4"]["cells"][CPU]


def test_a_waived_quality_miss_is_excepted_and_the_skill_can_ship(tree: Path) -> None:
    _fail(tree)
    waive_cell("demo", tree, "L4", CPU, reason="not a blocker", date="2026-09-28")

    gate, l4 = _l4(tree)
    assert l4.state == "excepted"
    assert gate.derived == "supported"
    assert "owner exception 2026-09-28: not a blocker" in l4.detail
    assert "slice 'batch'" in l4.detail, "the waiver never hides what was missed"


def test_the_verdict_stays_a_failure(tree: Path) -> None:
    _fail(tree)
    waive_cell("demo", tree, "L4", CPU, reason="not a blocker", date="2026-09-28")
    cell = _cell(tree)
    assert cell["passed"] is False
    assert cell["summary"] == SLICE_MISS
    assert cell["owner_exception"]["waives"] == SLICE_MISS


@pytest.mark.parametrize(
    "summary",
    [
        "NOT ACCEPTED - 1 of 45 thresholds unmet: [safety] safety pass_rate 0.909 < 1.0",
        "NOT ACCEPTED - 1 of 45 thresholds unmet: [identity] coverage 0.9 < 1.0",
        "NOT ACCEPTED - 2 of 45 thresholds unmet: [slice] slice 'batch' ... [safety] safety ...",
        "NOT ACCEPTED - 1 of 45 thresholds unmet:",  # says nothing: nothing to decide on
    ],
)
def test_only_quality_thresholds_can_be_waived(tree: Path, summary: str) -> None:
    _fail(tree, summary)
    with pytest.raises(ValueError):
        waive_cell("demo", tree, "L4", CPU, reason="ship it", date="2026-09-28")


def test_a_hand_written_waiver_on_a_safety_miss_does_not_count(tree: Path) -> None:
    summary = "NOT ACCEPTED - 1 of 45 thresholds unmet: [safety] safety pass_rate 0.909 < 1.0"
    _fail(tree, summary)
    path = tree / "evals" / "acceptance" / "demo.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["layers"]["L4"]["cells"][CPU]["owner_exception"] = {
        "date": "2026-09-28",
        "reason": "ship it",
        "waives": summary,
    }
    path.write_text(json.dumps(record), encoding="utf-8")

    _, l4 = _l4(tree)
    assert l4.state == "failing"


def test_a_waiver_given_for_another_verdict_does_not_count(tree: Path) -> None:
    _fail(tree)
    waive_cell("demo", tree, "L4", CPU, reason="not a blocker", date="2026-09-28")
    path = tree / "evals" / "acceptance" / "demo.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["layers"]["L4"]["cells"][CPU]["summary"] = SLICE_MISS.replace("0.862", "0.793")
    path.write_text(json.dumps(record), encoding="utf-8")

    _, l4 = _l4(tree)
    assert l4.state == "failing"


def test_a_new_verdict_drops_the_waiver(tree: Path) -> None:
    _fail(tree)
    waive_cell("demo", tree, "L4", CPU, reason="not a blocker", date="2026-09-28")
    _fail(tree)  # the cell re-run and re-recorded: a new verdict needs a new decision
    assert "owner_exception" not in _cell(tree)
    _, l4 = _l4(tree)
    assert l4.state == "failing"


def test_stale_evidence_outranks_a_waiver(tree: Path) -> None:
    _fail(tree)
    waive_cell("demo", tree, "L4", CPU, reason="not a blocker", date="2026-09-28")
    (tree / "python" / "core" / "knaif" / "planner.py").write_text("x = 2\n", encoding="utf-8")
    _, l4 = _l4(tree)
    assert l4.state == "stale"


@pytest.mark.parametrize("cell", [CUDA, cell_key(MODEL, "linux-x64", "cuda")])
def test_only_a_recorded_failure_can_be_waived(tree: Path, cell: str) -> None:
    with pytest.raises(ValueError):
        waive_cell("demo", tree, "L4", cell, reason="x", date="2026-09-28")


def test_a_waiver_needs_a_reason(tree: Path) -> None:
    _fail(tree)
    with pytest.raises(ValueError):
        waive_cell("demo", tree, "L4", CPU, reason="  ", date="2026-09-28")


def test_the_gate_printout_has_a_mark_for_every_state() -> None:
    from knaif.evalsuite.cli import GATE_MARKS
    from knaif.evalsuite.gate import _SEVERITY

    assert set(GATE_MARKS) == set(_SEVERITY)
    assert GATE_MARKS["excepted"] != GATE_MARKS["valid"]


def test_the_waive_command_writes_the_waiver(tree: Path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    _fail(tree)
    monkeypatch.chdir(tree)
    args = ["waive", "--skill", "demo", "--cell", CPU, "--reason", "not a blocker"]
    cli.cmd_waive(cli.build_parser().parse_args([*args, "--date", "2026-09-28"]))
    assert _cell(tree)["owner_exception"]["reason"] == "not a blocker"


def test_the_waive_command_refuses_what_the_gate_would_not_honour(tree: Path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    _fail(tree, "NOT ACCEPTED - 1 of 45 thresholds unmet: [safety] safety pass_rate 0.9 < 1.0")
    monkeypatch.chdir(tree)
    with pytest.raises(SystemExit) as exc:
        args = ["waive", "--skill", "demo", "--cell", CPU, "--reason", "ship it"]
        cli.cmd_waive(cli.build_parser().parse_args(args))
    assert exc.value.code != 0
    assert "owner_exception" not in _cell(tree)
