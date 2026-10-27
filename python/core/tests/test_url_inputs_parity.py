"""URL inputs, Python side of contracts/parity/url_inputs_cases.json.

Which argument values are URLs, which URLs are refused before any request (other schemes,
credentials, no host), what a downloaded file is called, and which content types a skill
accepts. The native side consumes the same file once `knaif_core::urls` exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.urls import check_content_type, check_url, is_url, url_file_name

REPO = Path(__file__).resolve().parents[3]
DOC = json.loads((REPO / "contracts/parity/url_inputs_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["url_cases"], ids=lambda c: c["name"])
def test_url_class(case: dict) -> None:
    assert is_url(case["value"]) is case["is_url"]
    if not case["is_url"]:
        return
    error = check_url(case["value"])
    if "error_contains" in case:
        assert error is not None and case["error_contains"] in error
    else:
        assert error is None


@pytest.mark.parametrize("case", DOC["file_name_cases"], ids=lambda c: c["name"])
def test_download_file_name(case: dict) -> None:
    assert url_file_name(case["url"], case.get("content_type")) == case["expected"]


@pytest.mark.parametrize("case", DOC["validation_cases"], ids=lambda c: c["name"])
def test_validation(case: dict, tmp_path: Path) -> None:
    import copy

    from knaif.planner import validate_plan
    from knaif.registry import load_registry

    tools = tmp_path / "tools.yaml"
    tools.write_text(DOC["validation_tools_yaml"], encoding="utf-8")
    registry = load_registry(tools)
    registry.update(load_registry(REPO / "contracts" / "runtime" / "core_tools.yaml"))
    plan = copy.deepcopy(case["plan"])
    if case.get("ok"):
        validate_plan(plan, registry, tmp_path)
    else:
        with pytest.raises(ValueError) as err:
            validate_plan(plan, registry, tmp_path)
        assert case["error_contains"] in str(err.value)


@pytest.mark.parametrize("case", DOC["content_type_cases"], ids=lambda c: c["name"])
def test_content_type(case: dict) -> None:
    error = check_content_type(case["content_type"], case["accepted"])
    if case.get("ok"):
        assert error is None
    else:
        assert error is not None and case["error_contains"] in error
