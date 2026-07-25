"""Tests for render_report_md multi-arm sections."""

from __future__ import annotations

from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.report import ArmEntry, render_report_md
from knaif.evalsuite.review_log import ReviewLog


def _entry(
    row_id: str = "r001",
    utt_idx: int = 0,
    utterance: str = "convert to mp4",
    score: float | None = 1.0,
    matched: list | None = None,
    failed: list | None = None,
    tags: list | None = None,
) -> ArmEntry:
    return ArmEntry(
        id=row_id,
        utterance_idx=utt_idx,
        utterance=utterance,
        score=score,
        matched=matched or ["extension=mp4"],
        failed=failed or [],
        artifact_path=None,
        baseline_path=None,
        outcome_correct=True,
        tags=tags or ["convert"],
    )


def _rows_by_id(*rows: CorpusRow) -> dict:
    return {r.id: r for r in rows}


# ── Summary section ───────────────────────────────────────────────────────────


def test_summary_section_present():
    arms = {"arm-a": [_entry()]}
    md = render_report_md(arms, {})
    assert "## Summary" in md


def test_summary_shows_pass_rate():
    arms = {"arm-a": [_entry("r001", score=1.0), _entry("r002", score=0.4)]}
    md = render_report_md(arms, {})
    assert "1/2" in md or "1/1" in md  # one pass out of two scored


def test_summary_shows_avg_score():
    arms = {"arm-a": [_entry("r001", score=1.0), _entry("r002", score=0.5)]}
    md = render_report_md(arms, {})
    assert "0.75" in md


# ── Per-tag breakdown ─────────────────────────────────────────────────────────


def test_per_tag_breakdown_section_present():
    arms = {"arm-a": [_entry(tags=["convert"]), _entry("r002", tags=["audio"])]}
    md = render_report_md(arms, {})
    assert "## Per-Tag Breakdown" in md


def test_per_tag_breakdown_lists_tags():
    arms = {"arm-a": [_entry(tags=["convert"]), _entry("r002", tags=["audio"])]}
    md = render_report_md(arms, {})
    assert "convert" in md
    assert "audio" in md


# ── Disagreements ─────────────────────────────────────────────────────────────


def test_disagreement_detected_when_arms_diverge():
    arms = {
        "arm-a": [_entry("r001", score=1.0)],
        "arm-b": [_entry("r001", score=0.1)],
    }
    md = render_report_md(arms, {})
    assert "## Top Disagreements" in md
    assert "r001" in md.split("## Top Disagreements")[1].split("## Close-Miss")[0]


def test_no_disagreement_when_both_pass():
    arms = {
        "arm-a": [_entry("r001", score=1.0)],
        "arm-b": [_entry("r001", score=0.98)],
    }
    md = render_report_md(arms, {})
    assert "No disagreements" in md


def test_no_disagreement_when_single_arm():
    arms = {"arm-a": [_entry("r001", score=1.0)]}
    md = render_report_md(arms, {})
    assert "No disagreements" in md


# ── Close-miss fails ──────────────────────────────────────────────────────────


def test_close_miss_appears_for_mid_score():
    arms = {"arm-a": [_entry("r001", score=0.7, failed=["duration"])]}
    md = render_report_md(arms, {})
    assert "## Close-Miss Fails" in md
    assert "r001" in md.split("## Close-Miss Fails")[1]


def test_close_miss_sorted_descending():
    arms = {
        "arm-a": [
            _entry("r001", score=0.6, failed=["a"]),
            _entry("r002", score=0.8, failed=["b"]),
            _entry("r003", score=0.4, failed=["c"]),
        ]
    }
    md = render_report_md(arms, {})
    section = md.split("## Close-Miss Fails")[1].split("## Sampled")[0]
    pos_r002 = section.index("r002")
    pos_r001 = section.index("r001")
    pos_r003 = section.index("r003")
    assert pos_r002 < pos_r001 < pos_r003


def test_perfect_score_not_in_close_miss():
    arms = {"arm-a": [_entry("r001", score=1.0)]}
    md = render_report_md(arms, {})
    section = md.split("## Close-Miss Fails")[1]
    assert "_No close-miss fails._" in section


# ── Sampled passes ────────────────────────────────────────────────────────────


def test_sampled_passes_reproducible():
    entries = [_entry(f"r{i:03d}", score=1.0) for i in range(30)]
    arms = {"arm-a": entries}
    md1 = render_report_md(arms, {})
    md2 = render_report_md(arms, {})
    assert md1 == md2


def test_sampled_passes_section_present():
    arms = {"arm-a": [_entry()]}
    md = render_report_md(arms, {})
    assert "## Sampled Passes" in md


# ── Review log annotations ────────────────────────────────────────────────────


def test_review_log_annotates_all_entries_section():
    log = ReviewLog()
    log.mark("r001", 0, "reviewed")
    arms = {"arm-a": [_entry("r001", score=1.0)]}
    md = render_report_md(arms, {}, review_log=log)
    assert "✓ reviewed" in md


def test_review_log_rejected_annotation():
    log = ReviewLog()
    log.mark("r001", 0, "rejected")
    arms = {"arm-a": [_entry("r001", score=0.6, failed=["x"])]}
    md = render_report_md(arms, {}, review_log=log)
    assert "✗ rejected" in md


def test_no_review_log_no_annotation():
    arms = {"arm-a": [_entry("r001", score=1.0)]}
    md = render_report_md(arms, {}, review_log=None)
    assert "✓ reviewed" not in md
    assert "✗ rejected" not in md


# ── Note / caveat ─────────────────────────────────────────────────────────────


def test_report_md_contains_caveat():
    arms = {"arm-a": [_entry()]}
    md = render_report_md(arms, {})
    assert "deterministic check" in md
