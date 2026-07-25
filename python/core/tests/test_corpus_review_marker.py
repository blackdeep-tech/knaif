"""Dual-mode review state: single-command baselines AND multi-output chains.

`outputs_validated_by` records that a chain row's `outputs` were human-reviewed,
separately from `baseline.validated_by` (which covers the single-command baseline).
`needs_review` is the dual-mode predicate the authoring notebook uses to surface
*both* kinds of un-reviewed rows.
"""

from __future__ import annotations

from knaif.evalsuite.corpus import CorpusRow, needs_review


def _row(**kw) -> CorpusRow:
    base = {"id": "r", "utterances": ["u"], "expected_outcome": "plan"}
    base.update(kw)
    return CorpusRow(**base)


def test_outputs_validated_by_roundtrips():
    row = _row(
        outputs=[{"command": "ffmpeg -i a.mp4 b.mp4", "criteria": {}}],
        outputs_validated_by="human",
    )
    d = row.to_dict()
    assert d["outputs_validated_by"] == "human"
    assert CorpusRow.from_dict(d).outputs_validated_by == "human"


def test_outputs_validated_by_omitted_when_unset():
    row = _row(outputs=[{"command": "ffmpeg -i a.mp4 b.mp4"}])
    assert "outputs_validated_by" not in row.to_dict()


# ── needs_review: single-command baselines ───────────────────────────────────


def test_unvalidated_baseline_needs_review():
    assert needs_review(_row(baseline={"command": "ffmpeg ... out.mp4"})) is True


def test_validated_baseline_does_not_need_review():
    row = _row(baseline={"command": "ffmpeg ... out.mp4", "validated_by": "human"})
    assert needs_review(row) is False


# ── needs_review: multi-output chains ─────────────────────────────────────────


def test_unreviewed_outputs_needs_review():
    # Even when its single-command baseline was validated, an un-reviewed chain
    # must still surface (the exact "0 rows awaiting validation" bug).
    row = _row(
        baseline={"command": "ffmpeg ... out.mp4", "validated_by": "human"},
        outputs=[{"command": "ffmpeg -i a.mp4 b.mp4"}],
    )
    assert needs_review(row) is True


def test_reviewed_outputs_does_not_need_review():
    row = _row(
        outputs=[{"command": "ffmpeg -i a.mp4 b.mp4"}],
        outputs_validated_by="human",
    )
    assert needs_review(row) is False


def test_reviewed_outputs_with_unvalidated_baseline_does_not_loop():
    # A dual row carrying a leftover, never-validated single-command baseline must
    # NOT resurface once its outputs chain is reviewed. The chain is authoritative;
    # accepting it only stamps `outputs_validated_by`, so a baseline check here would
    # loop the row back every restart.
    row = _row(
        baseline={"command": "ffmpeg ... out.mp4", "validated_by": None},
        outputs=[{"command": "ffmpeg -i a.mp4 b.mp4"}],
        outputs_validated_by="human",
    )
    assert needs_review(row) is False


def test_clarify_row_never_needs_review():
    assert needs_review(CorpusRow(id="c", utterances=["u"], expected_outcome="clarify")) is False
