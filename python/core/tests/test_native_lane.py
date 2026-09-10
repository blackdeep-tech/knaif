"""L4a: the native lane's pure parts — classification, fixture selection, lane config.

The lane itself needs a GGUF and real subprocesses, so what is testable here is everything
that decides *what a row means*. That is the part worth pinning: a misclassification does not
crash, it produces a plausible number.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (L4a, L4c).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from knaif.evalsuite.native_lane import (
    PLAN_DUMP_MARKER,
    load_lane,
    needed_fixtures,
    parse_run_output,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class _Row:
    def __init__(self, fixture=None):
        self.fixture = fixture


# ── outcome classification ────────────────────────────────────────────────────


def test_a_successful_run_is_a_plan_outcome() -> None:
    stderr = f"{PLAN_DUMP_MARKER}" + '{"plan": [{"tool": "strip_audio", "args": {}}]}\n'
    got = parse_run_output("running fine\n✓ out.mp4\n", stderr, 0)
    assert got["outcome"] == "plan"
    assert got["plan"]["plan"][0]["tool"] == "strip_audio"


def test_the_plan_comes_from_the_run_that_executed_it() -> None:
    """Tool metrics describe the plan that produced the artifact, not a second inference."""
    stderr = (
        "ggml noise\n"
        + PLAN_DUMP_MARKER
        + '{"plan": [{"tool": "resize_video", "args": {"height": 720}}]}\n'
        "running: ffmpeg -y -i a.mp4 -vf scale=-2:720 out.mp4\n"
    )
    got = parse_run_output("✓ out.mp4\n", stderr, 0)
    assert got["plan"]["plan"][0]["args"] == {"height": 720}
    assert got["commands"] == ["ffmpeg -y -i a.mp4 -vf scale=-2:720 out.mp4"]


def test_a_missing_plan_dump_is_none_not_an_empty_plan() -> None:
    """A binary built before `$KNAIF_DUMP_PLAN` existed must not read as "planned nothing"."""
    assert parse_run_output("✓ out.mp4\n", "", 0)["plan"] is None


def test_clarify_and_reject_are_distinguished() -> None:
    assert parse_run_output("clarify: which file?\n", "", 0)["outcome"] == "clarify"
    assert parse_run_output("reject: out of scope\n", "", 0)["outcome"] == "reject"


def test_a_capability_gap_is_not_a_reject() -> None:
    """The distinction L4d's coverage number depends on. Checked before `reject:` on purpose."""
    out = 'not_implemented: the documents tool "redact" is not built\n'
    assert parse_run_output(out, "", 1)["outcome"] == "not_implemented"


def test_a_nonzero_exit_with_no_verdict_is_an_error() -> None:
    assert parse_run_output("", "ffmpeg exploded\n", 1)["outcome"] == "error"


def test_a_failed_chain_is_an_error_even_though_step_one_wrote_a_file() -> None:
    """The executor's partial-failure shape (Workstream E): a file exists, the run failed."""
    stderr = "running: ffmpeg -y -i a.mp4 s.mp4\nError: step 2 of 3 failed; step 1 had already completed\n"
    got = parse_run_output("step 1 of 3:\n✓ s.mp4\n", stderr, 1)
    assert got["outcome"] == "error"
    assert got["commands"] == ["ffmpeg -y -i a.mp4 s.mp4"]


# ── fixture selection ─────────────────────────────────────────────────────────


def test_only_the_named_fixtures_are_copied(tmp_path: Path) -> None:
    for name in ("clip.mp4", "clip2.mp4", "audio.mp3"):
        (tmp_path / name).write_bytes(b"x")
    got = needed_fixtures(_Row(), "join clip.mp4 and clip2.mp4", tmp_path)
    assert {p.name for p in got} == {"clip.mp4", "clip2.mp4"}


def test_the_rows_declared_fixture_is_included_even_when_unnamed(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"x")
    got = needed_fixtures(_Row(fixture="clip.mp4"), "make the video smaller", tmp_path)
    assert [p.name for p in got] == ["clip.mp4"]


def test_a_filename_with_no_fixture_is_not_invented(tmp_path: Path) -> None:
    """The corpus references missing files on purpose; materializing one erases the case."""
    (tmp_path / "clip.mp4").write_bytes(b"x")
    got = needed_fixtures(_Row(), "compress holiday.mp4", tmp_path)
    assert got == []


def test_a_fixture_named_twice_is_copied_once(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"x")
    got = needed_fixtures(_Row(fixture="clip.mp4"), "trim clip.mp4 then resize clip.mp4", tmp_path)
    assert [p.name for p in got] == ["clip.mp4"]


# ── lane config (L4c) ─────────────────────────────────────────────────────────


def _write_config(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def test_a_lane_under_backends_is_a_hard_error(tmp_path: Path) -> None:
    """Anything under `backends:` is constructed as an inference backend. Saying so beats
    letting the user discover it as a confusing orchestrator failure."""
    cfg = _write_config(tmp_path, {"backends": {"native-cli": {"kind": "native_cli"}}})
    with pytest.raises(SystemExit) as exc:
        load_lane(cfg, "native-cli", tmp_path)
    assert "backends:" in str(exc.value) and "lanes:" in str(exc.value)


def test_an_unknown_lane_lists_the_known_ones(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, {"lanes": {"native-cli": {"kind": "native_cli"}}})
    with pytest.raises(SystemExit) as exc:
        load_lane(cfg, "typo", tmp_path)
    assert "native-cli" in str(exc.value)


def test_a_missing_binary_fails_before_any_inference(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        {"lanes": {"l": {"kind": "native_cli", "binary": "nope.exe", "model_path": "m.gguf"}}},
    )
    with pytest.raises(SystemExit, match="binary not found"):
        load_lane(cfg, "l", tmp_path)


def test_the_repo_config_declares_the_native_lane_outside_backends() -> None:
    doc = yaml.safe_load((REPO_ROOT / "eval_backends.yaml").read_text(encoding="utf-8"))
    assert "native-cli" in (doc.get("lanes") or {})
    assert "native-cli" not in (doc.get("backends") or {})
    assert doc["lanes"]["native-cli"]["kind"] == "native_cli"


# ── the marker, on both sides ─────────────────────────────────────────────────


def test_the_plan_dump_marker_matches_the_native_source() -> None:
    """One string, two languages. A silent rename means the lane sees no plans at all and
    reports 0% tool accuracy — a fabricated catastrophe, not a visible failure."""
    main_rs = (REPO_ROOT / "apps" / "cli" / "src" / "main.rs").read_text(encoding="utf-8")
    assert f'const PLAN_DUMP_MARKER: &str = "{PLAN_DUMP_MARKER}";' in main_rs
