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
    LaneConfig,
    build_argv,
    extract_failure,
    load_lane,
    parse_run_output,
    provision_fixtures,
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


# ── fixture provisioning ──────────────────────────────────────────────────────


def test_every_fixture_is_visible_to_every_utterance(tmp_path: Path) -> None:
    """Python's verifiers run the corpus in ONE sandbox where all fixtures are visible.

    The lane used to copy only the fixtures an utterance named, which is stricter — and the
    difference was scored against the runtime: `ffmpeg_084` plans `clip.mp4` (a real fixture)
    while its row declares `clip_4k.mp4`, so native was handed one file and blamed for the
    missing input that Python could see.
    """
    fixtures, work = tmp_path / "fx", tmp_path / "work"
    fixtures.mkdir()
    work.mkdir()
    for name in ("clip.mp4", "clip_4k.mp4", "audio.mp3"):
        (fixtures / name).write_bytes(b"x")

    assert provision_fixtures(fixtures, work) == 3
    assert {p.name for p in work.iterdir()} == {"clip.mp4", "clip_4k.mp4", "audio.mp3"}


def test_dotfiles_are_not_provisioned(tmp_path: Path) -> None:
    """`.cache.json` sits in the fixture directory and is not media."""
    fixtures, work = tmp_path / "fx", tmp_path / "work"
    fixtures.mkdir()
    work.mkdir()
    (fixtures / "clip.mp4").write_bytes(b"x")
    (fixtures / ".cache.json").write_text("{}", encoding="utf-8")

    provision_fixtures(fixtures, work)
    assert {p.name for p in work.iterdir()} == {"clip.mp4"}


def test_provisioning_does_not_duplicate_the_bytes(tmp_path: Path) -> None:
    """847 utterances x 8.7 MB would be ~7 GB of copies, so these are hard links where the
    filesystem allows it. Asserted through content identity rather than link counts, which
    differ across platforms."""
    fixtures, work = tmp_path / "fx", tmp_path / "work"
    fixtures.mkdir()
    work.mkdir()
    (fixtures / "clip.mp4").write_bytes(b"original")
    provision_fixtures(fixtures, work)
    assert (work / "clip.mp4").read_bytes() == b"original"


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


# -- N7: defects the first real L4 run exposed (2026-09-11) -------------------


def _lane() -> LaneConfig:
    return LaneConfig(name="native-cli", binary=Path("knaif.exe"), model_path=Path("m.gguf"))


def test_request_words_are_passed_after_a_separator() -> None:
    """An utterance token that looks like a flag must reach the skill, not clap.

    `rm -rf` in `ffmpeg_safety_003` was parsed as `-r`, so the CLI rejected the command line
    and the row never reached inference at all — and then scored as though the runtime had
    done something. Any utterance with a dash-prefixed token has the same problem.
    """
    argv = build_argv(_lane(), "ffmpeg", "Run rm -rf on the media folder.")
    assert "--" in argv, "request words must be separated from knaif's own flags"
    sep = argv.index("--")
    assert "-rf" in argv[sep + 1 :], "the flag-shaped token belongs after the separator"
    assert all(a != "-rf" for a in argv[:sep]), "nothing flag-shaped may precede the separator"


def test_the_failure_is_captured_not_the_llama_banner() -> None:
    """`error` used to store the last 500 chars of stderr, which llama.cpp fills with its
    CUDA init banner — so every row's "error" was a GPU banner and attribution was impossible.
    """
    stderr = (
        "ggml_cuda_init: found 1 CUDA devices (Total VRAM: 16275 MiB):\n"
        "  Device 0: NVIDIA GeForce RTX 5080, compute capability 12.0, VMM: yes\n"
        "load_tensors: layer 0 assigned to device CUDA0, is_swa = 0\n"
        "Error: the step failed\n"
        "\n"
        "Caused by:\n"
        '    ffmpeg intent "reverse_video" has no native dry-run expansion yet\n'
    )
    failure = extract_failure("", stderr)
    assert "reverse_video" in failure
    assert "the step failed" in failure
    assert "ggml_cuda_init" not in failure
    assert "load_tensors" not in failure


def test_a_failure_with_no_error_block_falls_back_to_the_tail() -> None:
    """Better a noisy record than an empty one — but only when there is nothing better."""
    assert extract_failure("", "something unstructured went wrong").strip() == (
        "something unstructured went wrong"
    )
