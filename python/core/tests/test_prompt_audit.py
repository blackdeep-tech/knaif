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
_DOCUMENTS_SKILL_DIR = Path("skills") / "documents"

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


@pytest.fixture()
def documents_agent(tmp_path: Path) -> CommandAgent:
    return CommandAgent.from_skill(_DOCUMENTS_SKILL_DIR, sandbox=tmp_path)


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

    Raised 14,000 → 15,000 on 2026-09-12 (T2 of
    docs/plans/2026-09-11-reject-clarify-taxonomy.md), which split the SAFETY block by the
    policy test and moved network access and impossible results to TOOL SCOPE's unsupported
    list. The prompt had six characters of headroom before the edit; it is 14,646 after it - T4 added the
    shell-execution rule the split had dropped, and T5b added `trim_video`'s frame count,
    paid for by removing a restatement of the chaining rule rather than raising this again.

    Raised 15,000 → 15,100 on 2026-09-22, for one 49-char line teaching "visually lossless"
    → ``best_possible``. Paying instead was the preferred move and was tried: every candidate
    saving meant deleting a phrase the model is measured on ("looking good" is the wording of
    an example in this very file), and HEAD had drifted to 14,956 — 44 chars of headroom, less
    than the smallest useful sentence. What the line buys is not the observed bug, which the
    ``quality`` alias table already absorbs deterministically, but the *silent* variant of it:
    with `best_possible` absent from the phrase table, "visually lossless" reads as `lossless`,
    a VALID enum value that no alias corrects and that means CRF 0 — a mathematically lossless
    file, many times larger, delivered without complaint. A wrong answer nothing reports is
    worse than the growth this ceiling guards against. **Unconfirmed by an eval arm**: the
    1.7B instruction-following risk below is real and this change has not been measured on it.

    **Context is not what this guards.** Every qwen3 backend runs ``n_ctx: 8192``
    (``eval_backends.yaml``, ``contracts/runtime/generation.yaml``), and the model is sent
    the *retrieved* prompt — retrieval is on by default in both the product and eval paths.
    15,000 chars is ~5,000 tok at a pessimistic 3.0 ch/tok, still inside an 8,192 window with
    512 of generation, and qwen3 supports 32K above that. What this number guards is
    instruction-following on a 1.7B as the policy block grows: unpredictable, measurable only
    in an eval arm. The 2026-06-18 precedent points the other way, which is why it moved.

    Raised 15,100 → 15,600 on 2026-10-11 for URL inputs: one rule (an http(s) URL is a valid
    input, passed exactly as written, never an output; "download" with no link given asks for
    it) and one URL example (to 15,537 on 2026-10-12, the rule tightened to pay for the
    no-link clause rather than raising this again). The example is shown only to a request that
    carries a URL (``select_examples``), so every other retrieved prompt grows by the rule alone.

    Raised 15,600 → 16,100 on 2026-10-12 for completion alerts and known folders: the
    `alert_when_done` core tool's two listing lines, one alert example and one known-folder
    example (to 16,079). None of them reaches a retrieved prompt that does not ask for it: the
    tool is retrieved only by its triggers ("beep", "let me know", …; ``retrieve_tools``), the
    alert example only with the tool, and the folder example only when the request names a known
    folder as a destination (``select_examples``). Every other retrieved prompt is unchanged,
    byte for byte.

    Raised 16,100 → 17,600 on 2026-10-20 for the one-file-into-many tools (to
    17,544): `split_video` and `extract_frames`' listing lines, one prompt rule and two examples.
    The tools join a prompt only by their triggers ("split", "pieces", "frames", …), the rule
    only with them (`rules:` in prompt.yaml) and the examples only with them (``select_examples``
    `triggered`). Measured over both corpora: 928 of 933 ffmpeg prompts and all 212 documents
    prompts are unchanged, byte for byte; the five that change carry those words (rows 204, 208
    and 302#1).

    Raised 17,600 → 18,600 on 2026-10-20 for the per-file batch rule (to 18,064):
    its two examples, marked `when: many` (the rule itself joins only a prompt whose request asks
    for many files, so the full prompt does not carry it). Both reach a retrieved prompt only when
    the request asks for many files (``nl_clarify_gate.asks_for_many_files``,
    contracts/parity/many_files_cases.json). Measured over both corpora: 49 of 933 existing ffmpeg
    prompts and 3 of 212 documents prompts change, every one of them a request for many files.
    """
    system, _ = ffmpeg_agent.build_prompt("compress a video")
    assert (
        len(system) < 18_600
    ), f"ffmpeg full prompt is {len(system)} chars — unexpected growth past the 18,600 ceiling"


def test_ffmpeg_retrieved_prompt_no_unexpected_growth(ffmpeg_agent: CommandAgent) -> None:
    """ffmpeg retrieved prompt must not grow past baseline (~8,276 chars).

    **This is the cap that binds**, not the full-prompt one above: the retrieved prompt is
    what the model is actually sent. It stayed at 10,000 through T2 deliberately. Measured
    over all 851 corpus utterances, the worst case is **8,844 chars** ("convert clip.mp4 to
    something suitable for streaming"), up from 8,244 before T2 — these additions are in the
    header, so they grow every retrieved prompt. That leaves ~1,150 chars of real margin.
    Past ~9,500, trim the wording rather than raising this.
    """
    utterance = "compress a video"
    retrieved = retrieve_tools(utterance, ffmpeg_agent.registry)
    system, _ = ffmpeg_agent.build_prompt(utterance, registry_override=retrieved)
    assert (
        len(system) < 10_000
    ), f"ffmpeg retrieved prompt is {len(system)} chars — unexpected growth from ~8,276 baseline"


def test_documents_prompt_no_unexpected_growth(documents_agent: CommandAgent) -> None:
    """documents prompt ceilings — new on 2026-09-12, and new for a reason.

    documents had no size guard at all until T3 of
    docs/plans/2026-09-11-reject-clarify-taxonomy.md gave it a SAFETY / TOOL SCOPE policy
    block of its own: **4,036 -> 5,392 full**, and the worst case over its 164 corpus
    utterances **2,146 -> 3,853 retrieved** ("sample-scanned.pdf is basically a photo of a
    contract…"). ffmpeg's equivalent block was what pushed *that* prompt to six characters
    under its ceiling, discovered only because a ceiling existed. The second skill gets one
    before it needs it, not after.

    Both ceilings are ~1.3x the measured worst case — loose enough not to trip on a wording
    edit, tight enough that another block this size has to be argued for. Measured against
    the **corpus** worst case rather than one probe utterance: the probe here renders 3,502
    retrieved, which understates the number the cap is actually near by ~350 chars.

    Context is not what this guards (worst case is ~1.3k tok against `n_ctx: 8192`). What it
    guards is instruction-following on a 4B as the header grows — documents' header roughly
    tripled, and that block has not yet been run against a model.

    Full ceiling raised 7,000 → 7,600 on 2026-10-20 for `split_pdf_every` (to
    7,423): its listing line, one prompt rule and one example, none of which reaches a retrieved
    prompt that does not carry its trigger words ("every 10 pages", "one file per page", …).
    Measured: all 212 documents corpus prompts unchanged, byte for byte.

    Raised 7,600 → 8,400 on 2026-10-20 for the per-file batch rule's two examples (`when: many`,
    to 7,996), which reach only a prompt whose request asks for many files: 3 of the 212 documents
    corpus prompts change.
    """
    system, _ = documents_agent.build_prompt("compress report.pdf")
    assert (
        len(system) < 8_400
    ), f"documents full prompt is {len(system)} chars — unexpected growth past the 8,400 ceiling"

    utterance = "compress report.pdf"
    retrieved = retrieve_tools(utterance, documents_agent.registry)
    system, _ = documents_agent.build_prompt(utterance, registry_override=retrieved)
    assert (
        len(system) < 5_000
    ), f"documents retrieved prompt is {len(system)} chars — past the 5,000 ceiling"


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
