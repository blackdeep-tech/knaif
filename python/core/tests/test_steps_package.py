"""Tests for knaif.steps — shared step library package."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_knaif_steps_importable():
    import knaif.steps  # noqa: F401


def test_steps_yaml_exists():
    # steps.yaml is language-neutral reference data; it lives under contracts/runtime/.
    yaml_path = Path("contracts/runtime/steps.yaml")
    assert yaml_path.exists(), f"steps.yaml not found at {yaml_path}"


def test_steps_yaml_has_resolve_inputs():
    yaml_path = Path("contracts/runtime/steps.yaml")
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "resolve_inputs" in data
    entry = data["resolve_inputs"]
    assert "paths" in entry.get("required_args", [])
    assert entry.get("internal") is True
    assert entry.get("readonly") is True


def test_resolve_inputs_class_exported():
    from knaif.steps import ResolveInputs
    from knaif.tool import Step

    assert issubclass(ResolveInputs, Step)
    assert ResolveInputs.name == "resolve_inputs"
