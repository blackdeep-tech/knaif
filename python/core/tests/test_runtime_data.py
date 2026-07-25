"""Language-neutral runtime data (core_tools.yaml, steps.yaml) lives under contracts/runtime/.

These are language-neutral runtime contracts — the Rust runtime reads the same files.
core_tools.yaml is also import-critical for the Python wheel, so a synced copy ships
inside the package; a drift guard keeps the two byte-identical (regenerate with
`just sync-runtime`). steps.yaml is reference-only (not loaded by Python) and lives solely
under contracts/runtime/.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONTRACTS_RUNTIME = Path("contracts/runtime")
PACKAGED_CORE_TOOLS = Path("python/core/knaif/core_tools.yaml")


def test_core_tools_loads_from_resolver():
    from knaif.core_tools import CORE_TOOL_DEFS

    assert {"clarify", "reject", "done", "wait_for_confirmation"} <= set(CORE_TOOL_DEFS)


def test_contracts_runtime_core_tools_is_canonical():
    assert (CONTRACTS_RUNTIME / "core_tools.yaml").exists()


def test_packaged_core_tools_matches_contracts_canonical():
    # Drift guard: the packaged copy the wheel ships must equal the contracts/runtime source.
    canonical = (CONTRACTS_RUNTIME / "core_tools.yaml").read_text(encoding="utf-8")
    packaged = PACKAGED_CORE_TOOLS.read_text(encoding="utf-8")
    assert packaged == canonical, "core_tools.yaml drift — run `just sync-runtime`"


def test_steps_yaml_lives_in_contracts_runtime():
    path = CONTRACTS_RUNTIME / "steps.yaml"
    assert path.exists()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "resolve_inputs" in data
    assert data["resolve_inputs"].get("internal") is True


def test_resolver_prefers_packaged_then_contracts_runtime():
    from knaif.core_tools import _resolve_runtime_yaml

    resolved = _resolve_runtime_yaml("core_tools.yaml")
    assert resolved.exists()
    assert resolved.name == "core_tools.yaml"
