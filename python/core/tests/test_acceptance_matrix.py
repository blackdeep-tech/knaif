"""The acceptance matrix: L3/L4 evidence per model x OS x backend, not one record per skill.

Until 2026-09-26 the gate held one L4 entry per skill, and each `accept-native` overwrote it.
So the last passing run hid an earlier failure: a CUDA pass recorded after a CPU failure read as
`supported`, although the CPU fallback — the path every machine without a usable GPU takes —
had failed. `contracts/release/acceptance_matrix.yaml` lists the cells a release must cover, and
the gate reports `supported` only when every full-coverage cell holds valid evidence.

See docs/plans/2026-09-25-release-1.2.md (R2, "The gate holds an acceptance matrix").
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from knaif.evalsuite.gate import evaluate_skill, record_layers
from knaif.evalsuite.matrix import backend_family, cell_key, load_matrix, required_cells

from .test_gate import make_tree


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return make_tree(tmp_path)


MODEL = "knaif-demo-v2"
SMALL = "knaif-demo-small-v2"


def _matrix(tree: Path, models=(MODEL,), entries=None) -> None:
    entries = entries or [
        {"os": "windows-x64", "backend": "cuda", "coverage": "full"},
        {"os": "windows-x64", "backend": "cpu", "coverage": "full"},
        {"os": "linux-x64", "backend": "vulkan", "coverage": "not-measured"},
    ]
    doc = {
        "current_release": "9.9.0",
        "releases": {"9.9.0": {"models": list(models), "entries": entries}},
    }
    (tree / "contracts" / "release" / "acceptance_matrix.yaml").write_text(
        yaml.safe_dump(doc), encoding="utf-8"
    )


def _contracts_and_l3(tree: Path, models=(MODEL,)) -> None:
    record_layers("demo", tree, {"L1": {"summary": "green"}, "L2": {"summary": "green"}})
    for m in models:
        record_layers("demo", tree, {"L3": {"cell": m, "summary": "parity", "passed": True}})


def _l4(tree: Path, model: str, os_: str, backend: str, passed: bool) -> None:
    record_layers(
        "demo",
        tree,
        {"L4": {"cell": cell_key(model, os_, backend), "summary": "run", "passed": passed}},
    )


def _derived(tree: Path) -> str:
    return evaluate_skill("demo", tree, "supported").derived


def test_a_cuda_pass_after_a_cpu_failure_is_not_supported(tree: Path) -> None:
    """The plan's RED test: the later pass must not hide the earlier failure."""
    _matrix(tree)
    _contracts_and_l3(tree)
    _l4(tree, MODEL, "windows-x64", "cpu", passed=False)
    _l4(tree, MODEL, "windows-x64", "cuda", passed=True)

    gate = evaluate_skill("demo", tree, "supported")
    assert gate.derived != "supported"
    l4 = next(s for s in gate.layers if s.layer == "L4")
    assert l4.state == "failing" and "cpu" in l4.detail


def test_every_full_cell_valid_is_supported(tree: Path) -> None:
    _matrix(tree)
    _contracts_and_l3(tree)
    _l4(tree, MODEL, "windows-x64", "cpu", passed=True)
    _l4(tree, MODEL, "windows-x64", "cuda", passed=True)
    assert _derived(tree) == "supported"


def test_a_missing_full_cell_is_pending_not_ignored(tree: Path) -> None:
    _matrix(tree)
    _contracts_and_l3(tree)
    _l4(tree, MODEL, "windows-x64", "cuda", passed=True)
    l4 = next(s for s in evaluate_skill("demo", tree, "supported").layers if s.layer == "L4")
    assert l4.state == "pending" and "cpu" in l4.detail


def test_a_not_measured_cell_is_not_required(tree: Path) -> None:
    """Linux Vulkan on a GPU cannot be measured on the hardware available (release plan R0)."""
    _matrix(tree)
    assert cell_key(MODEL, "linux-x64", "vulkan") not in required_cells(load_matrix(tree), "L4")


def test_each_model_needs_its_own_cells(tree: Path) -> None:
    """The 1.7B's pass cannot stand in for the 4B's, or the reverse."""
    _matrix(tree, models=(MODEL, SMALL))
    _contracts_and_l3(tree, models=(MODEL, SMALL))
    for os_, backend in (("windows-x64", "cpu"), ("windows-x64", "cuda")):
        _l4(tree, MODEL, os_, backend, passed=True)
    assert _derived(tree) != "supported"
    for os_, backend in (("windows-x64", "cpu"), ("windows-x64", "cuda")):
        _l4(tree, SMALL, os_, backend, passed=True)
    assert _derived(tree) == "supported"


def test_l3_is_required_per_model(tree: Path) -> None:
    _matrix(tree, models=(MODEL, SMALL))
    _contracts_and_l3(tree, models=(MODEL,))
    l3 = next(s for s in evaluate_skill("demo", tree, "supported").layers if s.layer == "L3")
    assert l3.state == "pending" and SMALL in l3.detail


def test_an_unkeyed_legacy_record_does_not_count_under_a_matrix(tree: Path) -> None:
    """A flat pre-matrix L4 says nothing about which cell it measured."""
    _matrix(tree)
    _contracts_and_l3(tree)
    record_layers("demo", tree, {"L4": {"summary": "old flat run", "passed": True}})
    assert _derived(tree) != "supported"


def test_without_a_matrix_the_flat_record_still_works(tree: Path) -> None:
    """Releases before the matrix keep their meaning: no contract, no cells."""
    record_layers(
        "demo",
        tree,
        {
            "L1": {"summary": "green"},
            "L2": {"summary": "green"},
            "L3": {"summary": "run", "passed": True},
            "L4": {"summary": "run", "passed": True},
        },
    )
    assert _derived(tree) == "supported"


@pytest.mark.parametrize(
    ("device", "family"),
    [("CUDA0", "cuda"), ("Vulkan0", "vulkan"), ("CPU", "cpu"), (None, None), ("Metal", "metal")],
)
def test_backend_family(device, family) -> None:
    assert backend_family(device) == family


def test_the_repo_matrix_is_well_formed() -> None:
    root = Path(__file__).resolve().parents[3]
    matrix = load_matrix(root)
    assert matrix is not None
    cells = required_cells(matrix, "L4")
    assert cells, "the current release requires no L4 cell at all"
    platforms = yaml.safe_load(
        (root / "contracts" / "release" / "platforms.yaml").read_text(encoding="utf-8")
    )
    known_os = {p["id"] for p in platforms["platforms"]}
    release = matrix["releases"][matrix["current_release"]]
    for entry in release["entries"]:
        assert entry["os"] in known_os, entry
        assert entry["backend"] in {"cuda", "vulkan", "cpu"}, entry
        assert entry["coverage"] in {"full", "not-measured"}, entry
