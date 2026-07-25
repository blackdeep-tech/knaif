"""Prompt size and content audit tests.

Asserts budget ceilings, internal-tool leakage, retrieved tool count, and
absence of unsupported bracket-index variable references in prompt examples.
Ceilings are set slightly above the 2026-05-28 baseline and should be TIGHTENED
as prompt simplification work lands (see docs/plans/2026-05-27-ffmpeg-prompt-small-model.md).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.registry import retrieve_tools

_FFMPEG_SKILL_DIR = Path("skills") / "ffmpeg"
_IO_SKILL_DIR = Path("skills") / "io"

# Internal ffmpeg tools that must never appear in the model-visible tool block.
_FFMPEG_INTERNAL_TOOLS = [
    "resolve_inputs",
    "inspect_media",
    "load_platform_profile",
    "load_quality_profile",
    "build_recipes",
    "render_preview_command",
    "run_preview",
    "verify_preview",
    "wait_for_confirmation",
    "render_batch_commands",
    "run_batch",
    "run_concat",
    "verify_outputs",
    "generate_report",
]


@pytest.fixture()
def io_agent(tmp_path: Path) -> CommandAgent:
    return CommandAgent.from_skill(_IO_SKILL_DIR, sandbox=tmp_path)


@pytest.fixture()
def ffmpeg_agent(tmp_path: Path) -> CommandAgent:
    return CommandAgent.from_skill(_FFMPEG_SKILL_DIR, sandbox=tmp_path)


# ── size budgets ──────────────────────────────────────────────────────────────


def test_io_full_prompt_under_budget(io_agent: CommandAgent) -> None:
    """io full prompt must stay under 3,000 chars (baseline: 2,432)."""
    system, _ = io_agent.build_prompt("list files")
    assert (
        len(system) < 3_000
    ), f"io prompt grew to {len(system)} chars — tighten the budget or review the change"


def test_ffmpeg_full_prompt_no_unexpected_growth(ffmpeg_agent: CommandAgent) -> None:
    """ffmpeg full prompt ceiling.

    Raised 13,400 → 14,000 on 2026-06-18: the strengthened SAFETY / reject-vs-clarify
    rules (Cat C of docs/plans/2026-06-18-ffmpeg-prompt-optimization.md) added ~700 chars
    and lifted qwen3-4b outcome +0.024 / gemma3-4b +0.020. The retrieved prompt — the one
    actually used at inference — stays well under budget (see the test below), so this
    growth is confined to the unfiltered full prompt.
    """
    system, _ = ffmpeg_agent.build_prompt("compress a video")
    assert (
        len(system) < 14_000
    ), f"ffmpeg full prompt is {len(system)} chars — unexpected growth past the 14,000 ceiling"


def test_ffmpeg_retrieved_prompt_no_unexpected_growth(ffmpeg_agent: CommandAgent) -> None:
    """ffmpeg retrieved prompt must not grow past baseline (~8,276 chars)."""
    utterance = "compress a video"
    retrieved = retrieve_tools(utterance, ffmpeg_agent.registry)
    system, _ = ffmpeg_agent.build_prompt(utterance, registry_override=retrieved)
    assert (
        len(system) < 10_000
    ), f"ffmpeg retrieved prompt is {len(system)} chars — unexpected growth from ~8,276 baseline"


# ── tool-count guard ──────────────────────────────────────────────────────────


def test_ffmpeg_retrieved_prompt_at_most_five_intent_tools(ffmpeg_agent: CommandAgent) -> None:
    """A retrieved ffmpeg prompt must expose ≤ 5 model-visible intent tools."""
    utterance = "compress a video"
    retrieved = retrieve_tools(utterance, ffmpeg_agent.registry)
    system, _ = ffmpeg_agent.build_prompt(utterance, registry_override=retrieved)

    tool_block_start = system.find("Available tools:")
    examples_start = system.find("Examples:", tool_block_start if tool_block_start != -1 else 0)
    if tool_block_start == -1 or examples_start == -1:
        pytest.skip("Cannot locate tool block in prompt — prompt structure changed")

    tool_block = system[tool_block_start:examples_start]
    # Each visible tool entry starts with "  - <name>"
    tool_entries = [ln for ln in tool_block.splitlines() if ln.strip().startswith("- ")]
    assert (
        len(tool_entries) <= 5
    ), f"Retrieved prompt exposed {len(tool_entries)} intent tools — max is 5"


# ── internal-tool leakage ─────────────────────────────────────────────────────


def test_ffmpeg_internal_tools_absent_from_tool_block(ffmpeg_agent: CommandAgent) -> None:
    """Internal ffmpeg tools must not appear in the model-visible tool block."""
    system, _ = ffmpeg_agent.build_prompt("compress a video")

    tool_block_start = system.find("Available tools:")
    examples_start = system.find("Examples:", tool_block_start if tool_block_start != -1 else 0)
    tool_block_end = examples_start if examples_start != -1 else len(system)
    tool_block = system[tool_block_start:tool_block_end]

    leaked = [t for t in _FFMPEG_INTERNAL_TOOLS if t in tool_block]
    assert not leaked, f"Internal tools leaked into model-visible block: {leaked}"


def test_io_internal_tools_absent_from_tool_block(io_agent: CommandAgent) -> None:
    """System-only io tools (done, clarify, reject) must not appear as tool definitions."""
    system, _ = io_agent.build_prompt("list files")

    tool_block_start = system.find("Available tools:")
    examples_start = system.find("Examples:", tool_block_start if tool_block_start != -1 else 0)
    tool_block_end = examples_start if examples_start != -1 else len(system)
    tool_block = system[tool_block_start:tool_block_end]

    # clarify and reject should not appear as defined tool entries, only in examples
    for t in ("clarify", "reject"):
        # Allow them in examples section, not as tool definitions
        assert (
            f"  - {t}" not in tool_block
        ), f"System tool '{t}' appeared as a tool definition in the model-visible block"


# ── variable-reference syntax ─────────────────────────────────────────────────

_BRACKET_INDEX_RE = re.compile(r"\$[a-zA-Z_]\w*(?:\.\w+)?\[\d+\]")


def test_no_bracket_index_in_io_examples(io_agent: CommandAgent) -> None:
    """io prompt examples must not contain unsupported $x.field[N] references."""
    system, _ = io_agent.build_prompt("find files")
    matches = _BRACKET_INDEX_RE.findall(system)
    assert not matches, (
        f"io prompt contains unsupported bracket-index references: {matches}\n"
        "The planner only accepts $var or $var.field — fix the example in io/prompt.yaml"
    )


def test_no_bracket_index_in_ffmpeg_examples(ffmpeg_agent: CommandAgent) -> None:
    """ffmpeg prompt examples must not contain unsupported $x.field[N] references."""
    system, _ = ffmpeg_agent.build_prompt("compress a video")
    matches = _BRACKET_INDEX_RE.findall(system)
    assert not matches, f"ffmpeg prompt contains unsupported bracket-index references: {matches}"


# ── example filtering ─────────────────────────────────────────────────────────


def test_retrieved_ffmpeg_prompt_smaller_than_full(ffmpeg_agent: CommandAgent) -> None:
    """The filtered (retrieved) prompt must be materially smaller than the full prompt."""
    utterance = "compress a video"
    retrieved = retrieve_tools(utterance, ffmpeg_agent.registry)

    full_system, _ = ffmpeg_agent.build_prompt(utterance)
    filtered_system, _ = ffmpeg_agent.build_prompt(utterance, registry_override=retrieved)

    reduction = len(full_system) - len(filtered_system)
    assert reduction > 2_000, f"Example filtering only saved {reduction} chars — expected > 2,000"


def test_compress_prompt_excludes_unrelated_examples(ffmpeg_agent: CommandAgent) -> None:
    """A 'compress' query must not pull in unrelated examples like extract_frame or adjust_speed."""
    utterance = "compress interview.mp4 to 20 MB"
    retrieved = retrieve_tools(utterance, ffmpeg_agent.registry)
    system, _ = ffmpeg_agent.build_prompt(utterance, registry_override=retrieved)

    # These tools are not relevant to a compress query and should not appear in
    # examples (they should still appear in the Available tools block if retrieved).
    examples_start = system.find("Examples:")
    examples_section = system[examples_start:] if examples_start != -1 else system

    unrelated_tools_in_examples = [
        t
        for t in ("adjust_speed", "reverse_video", "rotate_video")
        if f'"tool": "{t}"' in examples_section
    ]
    assert (
        not unrelated_tools_in_examples
    ), f"Unrelated tools appeared in examples for a compress query: {unrelated_tools_in_examples}"


def test_extract_audio_prompt_includes_relevant_example(ffmpeg_agent: CommandAgent) -> None:
    """An 'extract audio' query must receive the extract_audio example."""
    utterance = "extract the audio from clip.mp4 as mp3"
    retrieved = retrieve_tools(utterance, ffmpeg_agent.registry)
    system, _ = ffmpeg_agent.build_prompt(utterance, registry_override=retrieved)

    examples_start = system.find("Examples:")
    examples_section = system[examples_start:] if examples_start != -1 else system
    assert (
        '"tool": "extract_audio"' in examples_section
    ), "extract_audio example missing from a query that should retrieve it"
