"""Tests for the workbench panel (docs/plans/2026-09-21-skill-prompt-workbench.md T6)."""

from __future__ import annotations

import sys
from pathlib import Path

_SHARED = Path("notebooks") / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from workbench.panel import (  # noqa: E402
    describe_artifact,
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


# ── verbose ───────────────────────────────────────────────────────────────────────────────


def _full(**kw):
    from workbench.runners import RunResult, Timings

    base = {
        "runtime": "native",
        "skill": "ffmpeg",
        "utterance": "convert clip.mp4 to mkv",
        "outcome": "plan",
        "plan": {"plan": [{"tool": "convert_video", "args": {"container": "mkv"}}]},
        "commands": ["ffmpeg -y -i clip.mp4 -c copy out.mkv"],
        "artifacts": [],
        "timings": Timings(generate_plan_total_ms=418.0),
        "placement": {"CUDA0": 37},
        "stdout": "ffmpeg -y -i clip.mp4 -c copy out.mkv\n",
        "stderr": "load_tensors: layer 0 assigned to device CUDA0\n" * 50,
    }
    base.update(kw)
    return RunResult(**base)


def test_compact_shows_the_plan_the_command_where_and_when() -> None:
    """Default view: what was decided, what it renders to, where it ran, how long."""
    from workbench.panel import show

    text = show(_full())
    assert "PLAN" in text
    assert "convert_video" in text
    assert "COMMAND" in text
    assert "WHERE IT RAN" in text
    assert "TIME" in text


def test_compact_hides_the_load_trace() -> None:
    """The flood is the thing the checkbox exists to keep out of the way."""
    from workbench.panel import show

    text = show(_full())
    assert "load_tensors" not in text
    assert "STDERR" not in text


def test_verbose_adds_the_raw_output_and_the_full_plan() -> None:
    from workbench.panel import show

    text = show(_full(), verbose=True)
    assert "load_tensors" in text
    assert "STDERR" in text
    assert "STDOUT" in text
    # The plan as JSON, not just the one-line-per-step summary.
    assert '"container": "mkv"' in text


def test_verbose_shows_a_whole_load_trace() -> None:
    """A llama.cpp load is ~40k characters and states the layer placement in the MIDDLE.

    An earlier 4,000-char cap kept the head and the tail and dropped exactly that. Verbose is
    opt-in; when it is ticked, it shows the trace.
    """
    from workbench.panel import show

    filler = "noise\n" * 3000
    trace = filler + "load_tensors: layer 0 assigned to device CUDA0\n" + filler
    text = show(_full(stderr=trace), verbose=True)
    assert "load_tensors: layer 0" in text
    assert "elided" not in text


def test_a_pathological_trace_is_still_bounded() -> None:
    """The guard survives for the genuinely absurd, and says how much it dropped."""
    from workbench.panel import show

    text = show(_full(stderr="x" * 2_000_000), verbose=True)
    assert len(text) < 260_000
    assert "elided" in text


def test_a_truncated_trace_keeps_both_ends() -> None:
    """A 41k-char load trace states the layer placement at the TOP and any failure at the
    BOTTOM. Keeping only the tail hid exactly what verbose was ticked to see.
    """
    from workbench.panel import show

    trace = "load_tensors: layer 0 assigned to device CUDA0\n" + ("x" * 50_000) + "\nfinal line\n"
    text = show(_full(stderr=trace), verbose=True, limit=4000)
    assert "load_tensors" in text, "the head must survive"
    assert "final line" in text, "the tail must survive"
    assert "elided" in text
    assert len(text) < 30_000


def test_an_unmeasured_placement_says_how_to_measure_it() -> None:
    """ "Unknown" is only useful if it names the switch that would answer the question."""
    from workbench.panel import show

    text = show(_full(placement={}))
    assert "verbose" in text.lower()


def _fake_ffprobe(monkeypatch, stdout: str) -> None:
    import subprocess

    import workbench.panel as panel

    monkeypatch.setattr(panel.shutil, "which", lambda _name: "ffprobe")
    monkeypatch.setattr(
        panel.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=stdout, stderr=""),
    )


def test_an_empty_container_is_reported_not_raised(tmp_path, monkeypatch) -> None:
    # ffprobe prints `duration=N/A` for a container with no frames — e.g. a trim past the end.
    # That raised inside the panel and hid the plan that produced the empty file.
    empty = tmp_path / "Test2_intermediate.mov"
    empty.write_bytes(b"x" * 185)
    _fake_ffprobe(monkeypatch, "duration=N/A\n")

    assert "no streams" in describe_artifact(empty)


def test_an_unparsable_duration_is_left_out_not_raised(tmp_path, monkeypatch) -> None:
    clip = tmp_path / "live.mkv"
    clip.write_bytes(b"x" * 2048)
    _fake_ffprobe(monkeypatch, "codec_name=h264\nwidth=640\nheight=360\nduration=N/A\n")

    line = describe_artifact(clip)

    assert "h264 640x360" in line
    assert "N/A" not in line


def test_a_normal_media_file_shows_codec_size_and_duration(tmp_path, monkeypatch) -> None:
    clip = tmp_path / "Test1.mov"
    clip.write_bytes(b"x" * 2048)
    _fake_ffprobe(
        monkeypatch,
        "codec_name=h264\nwidth=1920\nheight=1080\nr_frame_rate=30/1\nduration=2.000000\n",
    )

    assert "h264 1920x1080 2.0s" in describe_artifact(clip)


def test_a_real_run_shows_each_command_with_its_outcome() -> None:
    from workbench.panel import show
    from workbench.runners import Execution

    text = show(
        _full(
            executions=[
                Execution("ffmpeg -y -i clip.mp4 Test1.mov", 0, None),
                Execution(
                    "ffmpeg -y -i Test1.mov Test2.mov",
                    -22,
                    "Output file does not contain any stream",
                ),
            ]
        )
    )

    assert "EXECUTED" in text
    assert "✓ exit 0    ffmpeg -y -i clip.mp4 Test1.mov" in text
    assert "✗ exit -22  ffmpeg -y -i Test1.mov Test2.mov" in text
    assert "Output file does not contain any stream" in text


# ── the model's plan beside the executed one ──────────────────────────────────────────────


def _fan_out(**kw) -> RunResult:
    emitted = {
        "plan": [
            {"tool": "trim_video", "args": {"input": "silent.mp4", "output": "part1.mp4"}},
            {"tool": "trim_video", "args": {"input": "silent.mp4", "output": "part2.mp4"}},
        ]
    }
    executed = {
        "plan": [
            {"tool": "trim_video", "args": {"input": "silent.mp4", "output": "part1.mp4"}},
            {"tool": "trim_video", "args": {"input": "part1.mp4", "output": "part2.mp4"}},
        ]
    }
    return RunResult(
        runtime="python",
        skill="ffmpeg",
        utterance="trim silent.mp4 twice",
        outcome="plan",
        plan=executed,
        commands=[],
        artifacts=[],
        timings=Timings(),
        **{"model_plan": emitted, **kw},
    )


def test_a_rewritten_plan_shows_what_the_model_said() -> None:
    """A correct plan scrambled by core read as a model failure, because only the rewritten
    one was on screen. When they differ, both are shown and the changed steps are marked."""
    from workbench.panel import show

    text = show(_fan_out())

    assert "MODEL SAID" in text
    model_section = text.split("MODEL SAID", 1)[1]
    assert "input=silent.mp4 output=part2.mp4" in model_section
    plan_section = text.split("MODEL SAID", 1)[0]
    rewritten = [line for line in plan_section.splitlines() if "input=part1.mp4" in line]
    assert rewritten and "*" in rewritten[0]
    unchanged = [line for line in plan_section.splitlines() if "output=part1.mp4" in line]
    assert unchanged and "*" not in unchanged[0]


def test_an_unrewritten_plan_is_shown_once() -> None:
    from workbench.panel import show

    same = _fan_out()
    result = RunResult(**{**same.__dict__, "model_plan": same.plan})

    assert "MODEL SAID" not in show(result)


def test_no_model_plan_means_no_comparison() -> None:
    """Native does not report its pre-rewrite plan; absent is not "the same"."""
    from workbench.panel import show

    assert "MODEL SAID" not in show(_fan_out(model_plan=None))


def test_a_run_stopped_at_a_gate_says_so() -> None:
    from workbench.panel import show

    stopped = RunResult(**{**_fan_out().__dict__, "stopped_at": "Proceed?"})

    text = show(stopped)
    assert "STOPPED" in text
    assert "Proceed?" in text
    assert "STOPPED" not in show(_fan_out())


# Captured verbatim from `ffprobe` on sandbox/fixtures/ffmpeg/clip.mp4 (2026-09-23).
_AV_PROBE = (
    "codec_name=h264\ncodec_type=video\nwidth=1920\nheight=1080\nr_frame_rate=30/1\n"
    "codec_name=aac\ncodec_type=audio\nr_frame_rate=0/0\nduration=10.000000\n"
)


def test_a_file_with_audio_shows_the_video_codec_first(tmp_path, monkeypatch) -> None:
    """One dict over every stream let the audio stream's `codec_name` overwrite the video's:
    the workbench listed `clip.mkv  aac 1920x1080` for an h264 file (2026-09-23)."""
    clip = tmp_path / "clip.mkv"
    clip.write_bytes(b"x" * 2048)
    _fake_ffprobe(monkeypatch, _AV_PROBE)

    line = describe_artifact(clip)

    assert "h264 1920x1080 10.0s" in line
    assert "audio aac" in line


def test_a_silent_video_says_it_has_no_audio(tmp_path, monkeypatch) -> None:
    """The point of `strip_audio`, visible without opening the file."""
    clip = tmp_path / "clip_silent.mp4"
    clip.write_bytes(b"x" * 2048)
    _fake_ffprobe(
        monkeypatch,
        "codec_name=h264\ncodec_type=video\nwidth=1920\nheight=1080\nr_frame_rate=30/1\n"
        "duration=10.000000\n",
    )

    assert "h264 1920x1080 10.0s  no audio" in describe_artifact(clip)


def test_an_audio_file_shows_its_codec_and_no_size(tmp_path, monkeypatch) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x" * 2048)
    _fake_ffprobe(
        monkeypatch, "codec_name=mp3\ncodec_type=audio\nr_frame_rate=0/0\nduration=12.500000\n"
    )

    line = describe_artifact(song)

    assert "mp3 12.5s" in line
    assert "x" not in line.split("MB", 1)[1]


def test_every_audio_output_is_probed(tmp_path, monkeypatch) -> None:
    """`clip_audio.aac  0.11 MB` said nothing: only video suffixes and mp3/wav/m4a were probed,
    so the file an extract_audio step wrote could not be checked from the panel."""
    _fake_ffprobe(
        monkeypatch, "codec_name=aac\ncodec_type=audio\nr_frame_rate=0/0\nduration=10.000000\n"
    )
    for suffix in (".aac", ".flac", ".ogg", ".opus"):
        song = tmp_path / f"clip_audio{suffix}"
        song.write_bytes(b"x" * 2048)
        assert "aac 10.0s" in describe_artifact(song), suffix
