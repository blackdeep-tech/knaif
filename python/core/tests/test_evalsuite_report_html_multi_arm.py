"""Tests for render_report_html multi-arm table."""

from __future__ import annotations

from knaif.evalsuite.report import ArmEntry, render_report_html
from knaif.evalsuite.review_log import ReviewLog


def _entry(
    row_id: str = "r001",
    utt_idx: int = 0,
    score: float | None = 1.0,
    artifact_path: str | None = None,
    latency_ms: float | None = None,
    actual_outcome: str | None = "plan",
    is_warmup: bool = False,
) -> ArmEntry:
    return ArmEntry(
        id=row_id,
        utterance_idx=utt_idx,
        utterance="convert to mp4",
        score=score,
        matched=["extension=mp4"],
        failed=[],
        artifact_path=artifact_path,
        baseline_path=None,
        outcome_correct=True,
        tags=["convert"],
        latency_ms=latency_ms,
        actual_outcome=actual_outcome,
        is_warmup=is_warmup,
    )


# ── Structure ─────────────────────────────────────────────────────────────────


def test_html_has_results_table():
    arms = {"arm-a": [_entry()]}
    html = render_report_html(arms, {})
    assert '<table id="results-table">' in html


def test_html_has_summary_table():
    arms = {"arm-a": [_entry()]}
    html = render_report_html(arms, {})
    assert "<h2>Summary</h2>" in html


def test_html_contains_row_id():
    arms = {"arm-a": [_entry("r001")]}
    html = render_report_html(arms, {})
    assert "r001" in html


# ── Multi-arm columns ─────────────────────────────────────────────────────────


def test_html_has_column_per_arm():
    arms = {
        "copilot": [_entry("r001", score=1.0)],
        "gemma": [_entry("r001", score=0.8)],
    }
    html = render_report_html(arms, {})
    assert "copilot" in html
    assert "gemma" in html


def test_html_two_arm_score_columns_in_header():
    arms = {
        "arm-a": [_entry()],
        "arm-b": [_entry()],
    }
    html = render_report_html(arms, {})
    # The results table is the second <thead>; the first belongs to the summary table
    results_thead = html.split("<thead>")[2].split("</thead>")[0]
    assert "arm-a" in results_thead
    assert "arm-b" in results_thead


# ── Sort JS ───────────────────────────────────────────────────────────────────


def test_html_has_sort_js():
    arms = {"arm-a": [_entry()]}
    html = render_report_html(arms, {})
    assert "sortTable" in html


def test_html_th_has_onclick():
    arms = {"arm-a": [_entry()]}
    html = render_report_html(arms, {})
    assert "onclick" in html


# ── Path normalisation ────────────────────────────────────────────────────────


def test_html_artifact_paths_use_forward_slashes():
    arms = {"arm-a": [_entry(artifact_path=r"evals\copilot\r001__0\out.mp4")]}
    html = render_report_html(arms, {})
    assert "\\" not in html


# ── Review log styling ────────────────────────────────────────────────────────


def test_html_reviewed_row_has_css_class():
    log = ReviewLog()
    log.mark("r001", 0, "reviewed")
    arms = {"arm-a": [_entry("r001")]}
    html = render_report_html(arms, {}, review_log=log)
    assert 'class="reviewed"' in html


def test_html_rejected_row_has_css_class():
    log = ReviewLog()
    log.mark("r001", 0, "rejected")
    arms = {"arm-a": [_entry("r001")]}
    html = render_report_html(arms, {}, review_log=log)
    assert 'class="rejected"' in html


# ── Caveat ────────────────────────────────────────────────────────────────────


def test_html_contains_caveat():
    arms = {"arm-a": [_entry()]}
    html = render_report_html(arms, {})
    assert "deterministic check" in html


# ── Time-to-artifact summary columns ──────────────────────────────────────────


def test_html_summary_has_latency_columns():
    arms = {"arm-a": [_entry()]}
    html = render_report_html(arms, {})
    assert "Mean ms" in html
    assert "p50 ms" in html
    assert "p95 ms" in html


def test_html_summary_renders_latency_value():
    arms = {
        "arm-a": [
            _entry("r001", latency_ms=999.0, is_warmup=True),
            _entry("r002", latency_ms=120.0),
            _entry("r003", latency_ms=180.0),
        ]
    }
    html = render_report_html(arms, {})
    # Warmup row skipped, plan-outcome entries 120 + 180 → mean = 150ms
    summary_thead = html.split("<thead>")[1].split("</thead>")[0]
    assert "Mean ms" in summary_thead
    summary_tbody = html.split("<tbody>")[1].split("</tbody>")[0]
    assert "<td>150</td>" in summary_tbody


def test_html_summary_latency_dash_when_no_data():
    arms = {"arm-a": [_entry(latency_ms=None)]}
    html = render_report_html(arms, {})
    # No latency data → em-dash cells in summary
    summary_tbody = html.split("<tbody>")[1].split("</tbody>")[0]
    assert summary_tbody.count("—") >= 3


# ── outcome_correct rendering ─────────────────────────────────────────────────


def test_html_wrong_outcome_no_score_shows_fail_cell():
    """outcome_correct=False with no knaif_score renders as red 'n/a ✗', not grey n/a."""
    entry = ArmEntry(
        id="r001",
        utterance_idx=0,
        utterance="grab a still frame",
        score=None,
        matched=[],
        failed=[],
        artifact_path=None,
        baseline_path=None,
        outcome_correct=False,
        tags=["extract"],
    )
    html = render_report_html({"arm-a": [entry]}, {})
    assert 'class="fail">n/a ✗' in html


def test_html_correct_clarify_no_score_shows_na_cell():
    """outcome_correct=True with no knaif_score renders as grey n/a (not red)."""
    entry = ArmEntry(
        id="r001",
        utterance_idx=0,
        utterance="optimize my video for Instagram",
        score=None,
        matched=[],
        failed=[],
        artifact_path=None,
        baseline_path=None,
        outcome_correct=True,
        tags=["clarify"],
    )
    html = render_report_html({"arm-a": [entry]}, {})
    assert 'class="na">n/a<' in html
    assert 'class="fail">n/a ✗' not in html


# ── Self-contained ────────────────────────────────────────────────────────────


def test_html_no_external_script_links():
    arms = {"arm-a": [_entry()]}
    html = render_report_html(arms, {})
    assert "cdn" not in html.lower()
    assert "https://" not in html
