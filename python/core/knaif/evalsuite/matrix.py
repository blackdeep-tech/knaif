"""The acceptance matrix: which model x OS x backend cells a release must hold evidence for.

`contracts/release/acceptance_matrix.yaml` names, per release, the models it ships and the
OS x backend entries, each with its coverage: `full` (L4 + safety measured, a verdict required)
or `not-measured` (stated in the release notes, never required). The gate keys L3 evidence by
model and L4 evidence by `model|os|backend`, so a later passing run can no longer overwrite an
earlier failing one in the one slot the skill used to have.

See docs/plans/2026-09-25-release-1.2.md (R0 coverage table, R2 acceptance matrix).
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

import yaml

MATRIX_CONTRACT = Path("contracts/release/acceptance_matrix.yaml")

#: Layers whose evidence is per cell. L1/L2 are deterministic and hold for the tree as a whole.
CELL_LAYERS = ("L3", "L4")


def load_matrix(root: Path) -> dict[str, Any] | None:
    """The matrix contract, or None when the repo has none (pre-matrix releases)."""
    path = root / MATRIX_CONTRACT
    if not path.is_file():
        return None
    doc: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return doc if doc.get("current_release") else None


def cell_key(model: str, os_id: str, backend: str) -> str:
    """The L4 cell a run belongs to: `model|os|backend`."""
    return f"{model}|{os_id}|{backend}"


def required_cells(matrix: dict[str, Any], layer: str) -> list[str]:
    """Cells the current release needs valid evidence for, in contract order.

    L3 is one cell per model: parity compares the two runtimes on one binary, and the release
    plan asks for it per model. L4 is every model crossed with every `full` entry.
    """
    release = (matrix.get("releases") or {}).get(matrix["current_release"]) or {}
    models = list(release.get("models") or [])
    if layer == "L3":
        return models
    if layer == "L4":
        return [
            cell_key(model, entry["os"], entry["backend"])
            for model in models
            for entry in release.get("entries") or []
            if entry.get("coverage") == "full"
        ]
    return []


def backend_family(device: str | None) -> str | None:
    """`CUDA0` -> `cuda`, `Vulkan0` -> `vulkan`, `CPU` -> `cpu`: the matrix's backend names.

    `compute_backend` is derived from where the layers landed (native_lane.py), so this names
    what actually ran, not what the binary offered.
    """
    if not device:
        return None
    return device.rstrip("0123456789").lower()


def current_os() -> str:
    """This machine as a `platforms.yaml` id, for a run that did not record its own."""
    system = platform.system().lower()
    if system == "windows":
        return "windows-x64"
    if system == "linux":
        return "linux-x64"
    return "macos" if system == "darwin" else system
