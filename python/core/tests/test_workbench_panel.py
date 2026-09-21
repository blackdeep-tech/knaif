"""Tests for the workbench panel (docs/plans/2026-09-21-skill-prompt-workbench.md T6)."""

from __future__ import annotations

import sys
from pathlib import Path

_SHARED = Path("notebooks") / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from workbench.panel import (  # noqa: E402
    first_divergence,
    percentiles,
    timing_rows,
)
from workbench.runners import RunResult, Timings  # noqa: E402


def _result(
    runtime: str,
    *,
    plan: dict | None = None,
    commands: list[str] | None = None,
    timings: Timings | None = None,
    placement: dict[str, int] | None = None,
) -> RunResult:
    return RunResult(
        runtime=runtime,
        skill="ffmpeg",
        utterance="convert clip.mp4 to mkv",
        outcome="plan",
        plan=plan,
        commands=commands or [],
        artifacts=[],
        timings=timings or Timings(),
        placement=placement or {},
    )


# ── the timing table ──────────────────────────────────────────────────────────────────────


def test_the_table_has_no_single_time_row() -> None:
    """D4c: one "time" column across runtimes is forbidden, so the renderer cannot emit one."""
    native = _result(
        "native",
        timings=Timings(
            wall_ms=1895.0, generate_plan_total_ms=418.0, model_load_ms=954.0, warm=False
        ),
    )
    python = _result(
        "python",
        timings=Timings(
            wall_ms=134.0,
            generate_plan_total_ms=134.0,
            reused_tokens=28,
            prompt_tokens=1,
            warm=True,
        ),
    )
    labels = [row[0] for row in timing_rows([native, python])]
    assert "time" not in [label.lower() for label in labels]
    assert any("generate_plan" in label for label in labels)
    assert any("wall" in label.lower() for label in labels)


def test_wall_clock_is_labelled_cold_or_warm() -> None:
    """A 134 ms warm repeat beside a 1895 ms cold run is not a speed comparison."""
    native = _result("native", timings=Timings(wall_ms=1895.0, generate_plan_total_ms=418.0))
    python = _result(
        "python",
        timings=Timings(wall_ms=134.0, generate_plan_total_ms=134.0, reused_tokens=28, warm=True),
    )
    rendered = {row[0]: row[1:] for row in timing_rows([native, python])}
    wall = next(v for k, v in rendered.items() if "wall" in k.lower())
    assert "cold" in " ".join(wall)
    assert "warm" in " ".join(wall)


def test_reuse_is_shown_so_a_fast_repeat_explains_itself() -> None:
    python = _result(
        "python",
        timings=Timings(generate_plan_total_ms=134.0, prompt_tokens=1, reused_tokens=28, warm=True),
    )
    labels = [row[0] for row in timing_rows([python])]
    assert any("reused" in label.lower() for label in labels)


# ── the side-by-side ──────────────────────────────────────────────────────────────────────


def test_first_divergence_names_the_step_and_the_argument() -> None:
    """The panel's job is to point at the disagreement, not to print two blobs."""
    a = {
        "plan": [
            {"tool": "compress_video", "args": {"input": "clip.mp4"}},
            {"tool": "convert_video", "args": {"video_codec": "h265"}},
        ]
    }
    b = {
        "plan": [
            {"tool": "compress_video", "args": {"input": "clip.mp4"}},
            {"tool": "convert_video", "args": {"video_codec": "hevc"}},
        ]
    }
    d = first_divergence(a, b)
    assert d is not None
    assert d["step"] == 2
    assert d["field"] == "video_codec"
    assert d["left"] == "h265"
    assert d["right"] == "hevc"


def test_a_different_tool_diverges_at_the_tool() -> None:
    a = {"plan": [{"tool": "convert_video", "args": {}}]}
    b = {"plan": [{"tool": "compress_video", "args": {}}]}
    assert first_divergence(a, b)["field"] == "tool"


def test_a_shorter_plan_diverges_at_the_missing_step() -> None:
    a = {"plan": [{"tool": "convert_video", "args": {}}, {"tool": "done", "args": {}}]}
    b = {"plan": [{"tool": "convert_video", "args": {}}]}
    d = first_divergence(a, b)
    assert d["step"] == 2
    assert d["right"] is None


def test_identical_plans_do_not_diverge() -> None:
    a = {"plan": [{"tool": "convert_video", "args": {"x": 1}}]}
    assert first_divergence(a, {"plan": [{"tool": "convert_video", "args": {"x": 1}}]}) is None


def test_a_missing_plan_is_a_divergence_not_a_crash() -> None:
    """A run that produced no plan is a real outcome, and the panel must render it."""
    assert first_divergence(None, {"plan": []}) is not None


# ── statistics ────────────────────────────────────────────────────────────────────────────


def test_percentiles_are_observed_values_not_interpolations() -> None:
    """One rule: every reported percentile is a figure some run actually took.

    Nearest-rank, so p50 of [100, 200, 300, 400] is 200 — an observed sample — rather than the
    interpolated 250, which no run produced. `mean` is the one derived figure, and it is named
    `mean` precisely because it is not a sample.
    """
    stats = percentiles([100.0, 200.0, 300.0, 400.0])
    assert stats["n"] == 4
    assert stats["mean"] == 250.0
    assert stats["p50"] == 200.0
    assert stats["p95"] == 400.0
    assert stats["p50"] in (100.0, 200.0, 300.0, 400.0)
    assert stats["p95"] in (100.0, 200.0, 300.0, 400.0)


def test_percentiles_of_one_sample_say_n_equals_one() -> None:
    stats = percentiles([42.0])
    assert stats["n"] == 1
    assert stats["mean"] == stats["p50"] == stats["p95"] == 42.0


def test_percentiles_of_nothing_measured_is_empty_not_zero() -> None:
    assert percentiles([]) == {}
