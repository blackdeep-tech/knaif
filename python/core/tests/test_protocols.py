"""Tests for evalsuite protocol definitions."""

from pathlib import Path
from typing import Any

from knaif.evalsuite.protocols import (
    ReferenceLoader,
    Reviewer,
    Verifier,
    VerifyResult,
)


def test_verify_result_defaults():
    r = VerifyResult(score=0.75)
    assert r.score == 0.75
    assert r.matched == []
    assert r.failed == []
    assert r.verifier_kind == "plan"


def test_verify_result_fields():
    r = VerifyResult(score=0.5, matched=["a"], failed=["b"], verifier_kind="output")
    assert r.matched == ["a"]
    assert r.failed == ["b"]
    assert r.verifier_kind == "output"


class _StubVerifier:
    def __call__(
        self,
        artifact_path: Path,
        reference_path: Path,
        tolerances: dict[str, Any],
        sandbox: Path,
    ) -> VerifyResult:
        return VerifyResult(score=1.0)


class _StubReviewer:
    def render_row(
        self,
        row: dict[str, Any],
        arm_outputs: dict[str, Path | None],
        reference: Path | None,
    ) -> str:
        return "<div/>"


class _StubReferenceLoader:
    def __call__(self, row: dict[str, Any]) -> Path | None:
        return None


def test_stub_verifier_satisfies_protocol():
    assert isinstance(_StubVerifier(), Verifier)


def test_stub_reviewer_satisfies_protocol():
    assert isinstance(_StubReviewer(), Reviewer)


def test_stub_reference_loader_satisfies_protocol():
    assert isinstance(_StubReferenceLoader(), ReferenceLoader)


def test_verifier_is_callable():
    v = _StubVerifier()
    result = v(Path("a.mp4"), Path("b.mp4"), {}, Path("."))
    assert isinstance(result, VerifyResult)
    assert result.score == 1.0
