"""Golden parity: the preview of a saved text, over contracts/parity/text_preview_cases.json.

Owner, 2026-10-23: extract_text saves its text and the terminal previews it. The
Rust side (skills/documents/native/tests/text_preview_parity.rs) runs the same cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skills.documents.python.handlers import text_preview

DOC = json.loads(
    (Path(__file__).parents[4] / "contracts" / "parity" / "text_preview_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_text_preview(case: dict) -> None:
    assert text_preview(case["text"]) == case["expected"]
