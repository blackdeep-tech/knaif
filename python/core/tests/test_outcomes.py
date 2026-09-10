"""The shared outcome vocabulary, and the `not_implemented` marker's three-way sync.

A capability the runtime has not built and a request it deliberately declined are both
refusals to the user and opposite facts about the product. While both were recorded as
`reject`, coverage could not be computed from any acceptance record — which is what makes
L4's exclusion of unattempted rows from `avg_knaif_score` honest.
"""

from __future__ import annotations

import re
from pathlib import Path

from knaif.evalsuite.outcomes import (
    NOT_IMPLEMENTED_PREFIX,
    OUTCOMES,
    coverage,
    is_capability_gap,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_capability_gap_is_its_own_outcome() -> None:
    assert "not_implemented" in OUTCOMES
    assert is_capability_gap("not_implemented")
    assert not is_capability_gap("reject"), "a deliberate refusal is not a coverage gap"
    assert not is_capability_gap("clarify")


def test_coverage_counts_deliberate_refusals_as_attempted() -> None:
    assert coverage(["plan", "reject", "clarify"]) == 1.0
    assert coverage(["plan", "not_implemented"]) == 0.5
    assert coverage([]) == 0.0


def test_the_marker_is_identical_in_rust() -> None:
    """The native runtime writes it; Python reads it. A drift here is silent data loss."""
    main_rs = (REPO_ROOT / "apps" / "cli" / "src" / "main.rs").read_text(encoding="utf-8")
    match = re.search(r'const NOT_IMPLEMENTED_PREFIX: &str = "([^"]+)";', main_rs)
    assert match, "apps/cli/src/main.rs no longer defines NOT_IMPLEMENTED_PREFIX"
    assert match.group(1) == NOT_IMPLEMENTED_PREFIX


def test_the_marker_is_identical_in_the_parity_script() -> None:
    src = (REPO_ROOT / "scripts" / "parity_check.py").read_text(encoding="utf-8")
    match = re.search(r'^NOT_IMPLEMENTED_PREFIX = "([^"]+)"', src, re.MULTILINE)
    assert match, "scripts/parity_check.py no longer defines NOT_IMPLEMENTED_PREFIX"
    assert match.group(1) == NOT_IMPLEMENTED_PREFIX


def test_native_no_longer_labels_the_chain_gap_a_reject() -> None:
    main_rs = (REPO_ROOT / "apps" / "cli" / "src" / "main.rs").read_text(encoding="utf-8")
    assert 'println!(\n                "reject: this request needs' not in main_rs
