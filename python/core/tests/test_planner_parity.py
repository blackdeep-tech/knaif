"""Golden parity: the Python deterministic pipeline over contracts/parity/planner_cases.json.

The Rust side (native/crates/knaif-core/tests/parity.rs) runs the identical fixtures; both must
produce the same valid/invalid outcome + error substring, proving the native validator port
agrees with the reference Python implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

from knaif.planner import apply_defaults, normalize_plan, parse_plan, validate_plan
from knaif.registry import load_registry

FIXTURES = Path("contracts/parity/planner_cases.json")


def test_planner_parity_cases(tmp_path):
    doc = json.loads(FIXTURES.read_text(encoding="utf-8"))
    registries = doc["registries"]

    for case in doc["cases"]:
        name = case["name"]
        reg_file = tmp_path / f"{name}.yaml"
        reg_file.write_text(registries[case["registry"]], encoding="utf-8")
        registry = load_registry(reg_file)

        payload = parse_plan(json.dumps(case["plan"]))
        normalize_plan(payload, registry)
        apply_defaults(payload, registry)

        try:
            validate_plan(payload, registry, root=Path("."), sandbox=None)
            valid, err = True, ""
        except ValueError as exc:
            valid, err = False, str(exc)

        assert (
            valid == case["valid"]
        ), f"{name}: expected valid={case['valid']}, got {valid} ({err})"
        if not case["valid"] and "error_contains" in case:
            assert (
                case["error_contains"] in err
            ), f"{name}: {err!r} missing {case['error_contains']!r}"
