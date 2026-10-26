"""L2: the Python reference meets contracts/parity/known_folder_cases.json.

The same file is the native mirror's contract: both runtimes name known folders, gate the
known-folder examples and rewrite outputs the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.known_folders import SameNameFolder, named_folder, names_known_folder, rewrite_outputs
from knaif.registry import ToolDef

DOC = json.loads(
    (
        Path(__file__).resolve().parents[3] / "contracts" / "parity" / "known_folder_cases.json"
    ).read_text(encoding="utf-8")
)

_REGISTRY = {
    "convert_video": ToolDef(
        name="convert_video",
        description="",
        required_args=("inputs",),
        optional_args=("container", "output"),
    )
}


@pytest.mark.parametrize("case", DOC["named"], ids=lambda c: c["value"])
def test_named(case: dict) -> None:
    assert named_folder(case["value"]) == case["expected"]


@pytest.mark.parametrize("case", DOC["destination"], ids=lambda c: c["text"][:40])
def test_destination(case: dict) -> None:
    assert names_known_folder(case["text"]) is case["expected"]


def _fill(value, work: Path, known: Path):
    if isinstance(value, str):
        return value.replace("<work>", work.as_posix()).replace("<known>", known.as_posix())
    if isinstance(value, list):
        return [_fill(v, work, known) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, work, known) for k, v in value.items()}
    return value


@pytest.mark.parametrize("case", DOC["rewrite"], ids=lambda c: c["name"])
def test_rewrite(case: dict, tmp_path: Path) -> None:
    work, known = tmp_path / "work", tmp_path / "known"
    work.mkdir()
    for folder in case["folders"]:
        (work / folder).mkdir(parents=True)
    sandbox = Path(_fill(case["sandbox"], work, known)) if case.get("sandbox") else None
    run = lambda: rewrite_outputs(  # noqa: E731
        case["plan"],
        _REGISTRY,
        cwd=work,
        sandbox=sandbox,
        env={"KNAIF_KNOWN_FOLDERS_ROOT": str(known)},
    )
    if "expected_clarify" in case:
        with pytest.raises(SameNameFolder) as ask:
            run()
        assert str(ask.value) == _fill(case["expected_clarify"], work, known)
    elif "expected_error" in case:
        with pytest.raises(ValueError, match=case["expected_error"]):
            run()
    else:
        assert run() == _fill(case["expected_plan"], work, known)
