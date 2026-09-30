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
    src = (REPO_ROOT / "native" / "crates" / "knaif-skill-api" / "src" / "capability.rs").read_text(
        encoding="utf-8"
    )
    match = re.search(r'pub const NOT_IMPLEMENTED_PREFIX: &str = "([^"]+)";', src)
    assert match, "knaif-skill-api no longer defines NOT_IMPLEMENTED_PREFIX"
    assert match.group(1) == NOT_IMPLEMENTED_PREFIX


def test_every_native_skill_can_reach_the_marker() -> None:
    """N6: the marker lived in `apps/cli`, so a skill could not use it and bailed with a bare
    error instead — which is how the first L4 run reported full coverage over a corpus it could
    not fully attempt. Each skill that ships natively must depend on the crate that owns it.
    """
    for skill in ("ffmpeg", "documents"):
        manifest = REPO_ROOT / "skills" / skill / "native" / "Cargo.toml"
        if not manifest.exists():
            continue
        assert "knaif-skill-api" in manifest.read_text(encoding="utf-8"), (
            f"{skill}'s native crate cannot reach the not_implemented marker, so an unbuilt "
            "capability there would be recorded as an error and vanish from coverage"
        )


def test_no_native_skill_declines_a_capability_with_a_bare_error() -> None:
    """The specific shape N6 was: a fall-through arm that says "not ... yet" in prose while
    carrying none of the marker the harness counts."""
    import glob

    offenders = []
    for path in glob.glob(str(REPO_ROOT / "skills" / "*" / "native" / "src" / "*.rs")):
        text = Path(path).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            if "yet" not in lowered or "not_implemented" in lowered:
                continue
            if "no native" in lowered or "not built" in lowered or "not supported" in lowered:
                window = "\n".join(text.splitlines()[max(0, i - 4) : i + 2])
                if "not_implemented" not in window:
                    offenders.append(f"{Path(path).name}:{i}: {line.strip()[:70]}")
    assert not offenders, (
        "a capability the native runtime does not have must be announced with "
        "`not_implemented_message`, or coverage cannot see it:\n  " + "\n  ".join(offenders)
    )


def test_the_marker_is_identical_in_the_parity_script() -> None:
    src = (REPO_ROOT / "scripts" / "parity_check.py").read_text(encoding="utf-8")
    match = re.search(r'^NOT_IMPLEMENTED_PREFIX = "([^"]+)"', src, re.MULTILINE)
    assert match, "scripts/parity_check.py no longer defines NOT_IMPLEMENTED_PREFIX"
    assert match.group(1) == NOT_IMPLEMENTED_PREFIX


def test_native_no_longer_labels_the_chain_gap_a_reject() -> None:
    main_rs = (REPO_ROOT / "apps" / "cli" / "src" / "main.rs").read_text(encoding="utf-8")
    assert 'println!(\n                "reject: this request needs' not in main_rs
