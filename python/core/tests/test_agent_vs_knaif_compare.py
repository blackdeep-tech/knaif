"""The 2026-10 rerun of the knaif-vs-agent comparison (`scripts/agent_vs_knaif/compare.py`).

Parsers are tested on recorded output (docs/plans/2026-10-01-llm-comparison-rerun.md: never on a
live CLI). The native arm's lines below are a real `knaif run --yes` with `KNAIF_TIMING=1`, plain
view, captured 2026-10-01; the timestamps are the seconds at which each line arrived.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[3] / "scripts" / "agent_vs_knaif"


def _load(name: str):
    sys.path.insert(0, str(HERE))
    try:
        spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(HERE))


@pytest.fixture(scope="module")
def compare():
    return _load("compare")


@pytest.fixture(scope="module")
def agents():
    return _load("agents")


CONVERT = [
    (1.30, "[knaif-timing] model load_from_file = 1093 ms"),
    (1.32, "[knaif-timing] new_context = 18 ms"),
    (1.56, "[knaif-timing] prompt_decode (2445 tokens) = 238 ms"),
    (1.72, "[knaif-timing] generation (32 tokens) = 158 ms"),
    (1.72, "[knaif-timing] generate_plan TOTAL = 417 ms"),
    (2.10, "running: ffmpeg -y -i clip.mov -c copy -movflags +faststart clip_converted.mp4"),
    (2.31, "✓ clip_converted.mp4"),
]


def test_native_run_splits_load_inference_and_ffmpeg(compare):
    r = compare.parse_native(CONVERT, exit_code=0)
    assert r["outcome"] == "plan"
    assert (r["load_ms"], r["prompt_ms"], r["gen_ms"]) == (1093, 238, 158)
    assert (r["prompt_tok"], r["gen_tok"]) == (2445, 32)
    assert r["ffmpeg_s"] == pytest.approx(0.21)
    # The table's time: inference + ffmpeg, no model load (owner, 2026-10-01).
    assert r["table_s"] == pytest.approx(0.238 + 0.158 + 0.21)


def test_native_chain_sums_every_ffmpeg_step(compare):
    lines = [
        (1.0, "[knaif-timing] prompt_decode (2400 tokens) = 200 ms"),
        (1.2, "[knaif-timing] generation (60 tokens) = 300 ms"),
        (1.3, "step 1 of 2:"),
        (1.4, "running: ffmpeg -y -i clip.mp4 -t 5 clip_trimmed.mp4"),
        (1.9, "✓ clip_trimmed.mp4"),
        (2.0, "step 2 of 2:"),
        (2.1, "running: ffmpeg -y -i clip_trimmed.mp4 -vf scale=-2:720 clip_trimmed_720p.mp4"),
        (3.1, "✓ clip_trimmed_720p.mp4"),
    ]
    r = compare.parse_native(lines, exit_code=0)
    assert r["ffmpeg_s"] == pytest.approx(1.5)
    assert r["outcome"] == "plan"


def test_native_question_and_refusal_are_outcomes_with_no_ffmpeg(compare):
    q = compare.parse_native([(1.0, "clarify: Which file?")], exit_code=0)
    assert (q["outcome"], q["ffmpeg_s"]) == ("clarify", 0.0)
    no = compare.parse_native([(1.0, "reject: I can't help with that.")], exit_code=0)
    assert no["outcome"] == "reject"


def test_native_failed_step_is_an_error(compare):
    lines = [
        (1.0, "running: ffmpeg -y -i a.mp4 b.mp4"),
        (1.4, "✗ b.mp4 (ffmpeg exited with exit code: 1)"),
    ]
    r = compare.parse_native(lines, exit_code=1)
    assert r["outcome"] == "error"
    assert r["ffmpeg_s"] == pytest.approx(0.4)


@pytest.mark.parametrize(
    ("name", "model", "flag"),
    [
        ("claude", "claude-opus-5-5", ["--effort", "medium"]),
        ("codex", "gpt-6-astra", ["-c", 'model_reasoning_effort="medium"']),
        ("copilot", "gpt-5.6-terra", ["--reasoning-effort", "medium"]),
    ],
)
def test_each_agent_gets_its_model_and_effort(agents, name, model, flag):
    argv = agents.AGENTS[name].build_argv("do it", model, "medium")
    assert model in argv
    i = argv.index(flag[0])
    assert argv[i : i + 2] == flag


def test_no_effort_leaves_the_07_02_argv_unchanged(agents):
    argv = agents.AGENTS["claude"].build_argv("do it", "claude-opus-4-8")
    assert "--effort" not in argv


def test_saved_text_carries_no_local_path(compare):
    root = Path("C:/Users/alice/work/knaif-llm-compare")
    text = r"Wrote C:\Users\alice\work\knaif-llm-compare\claude-opus\r1-03\out.mp4 for alice"
    clean = compare.scrub(text, [root, Path("C:/Users/alice")])
    assert "alice" not in clean
    assert "<run>" in clean


@pytest.fixture(scope="module")
def pricing():
    return _load("pricing")


@pytest.mark.parametrize(
    ("model", "tokens", "cli_cost"),
    [
        # Smoke rows of 2026-10-01: (uncached, cache_read, cache_write, output) and the CLI's
        # own `total_cost_usd`. Only the 1-hour cache-write rate reproduces them.
        ("claude-opus-5-5", (948, 47046, 47439, 257), 0.3948),
        ("claude-sonnet-5-5", (948, 61041, 14778, 248), 0.0747),
    ],
)
def test_claude_rows_reproduce_the_clis_own_cost(pricing, model, tokens, cli_cost):
    assert pricing.api_cost_2026_10(model, *tokens) == pytest.approx(cli_cost, rel=0.02)


def test_openai_rows_price_cached_input_at_its_rate(pricing):
    # gpt-6.1-sol: $2 input, $0.10 cached, $10 output per 1M (short context).
    cost = pricing.api_cost_2026_10("gpt-6.1-sol", 5142, 28160, 0, 102)
    assert cost == pytest.approx((5142 * 2 + 28160 * 0.10 + 102 * 10) / 1e6)


def test_a_model_without_a_published_price_is_not_guessed(pricing):
    assert pricing.api_cost_2026_10("some-unpriced-model", 1, 1, 1, 1) is None


def test_gpu_readings_parse_and_a_busy_gpu_is_seen(compare):
    # `nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits`
    assert compare.parse_gpu("3, 1024\n") == (3, 1024)
    assert compare.parse_gpu("garbage") is None
    # knaif's own timing is only meaningful on an otherwise idle GPU (owner, 2026-10-01).
    assert compare.gpu_busy([(2, 900), (0, 900), (4, 900)]) is False
    assert compare.gpu_busy([(2, 900), (61, 5200), (3, 900)]) is True


def test_guard_sees_a_changed_tree(compare):
    before = {"status": "", "dist": {"a.zip": 1}}
    assert compare.guard_diff(before, dict(before)) == []
    after = {"status": " D README.md", "dist": {}}
    problems = compare.guard_diff(before, after)
    assert len(problems) == 2
    assert "README.md" in problems[0], "the guard names what changed"
