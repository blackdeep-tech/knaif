"""ffmpeg's reject/clarify policy, as the model actually reads it.

The core contract says only that ``reject`` means *the active skill's safety policy was
violated* (``contracts/runtime/core_tools.yaml``). **Which** categories that covers is
ffmpeg's policy, and it lives in one place the model sees: ``prompt.yaml``'s SAFETY and
TOOL SCOPE blocks. Those two blocks used to contradict each other — SAFETY said "reject
anything beyond local media processing", TOOL SCOPE said "anything that maps to no tool →
clarify" — and the model resolved it by rejecting whatever it could not do.

The test, in one sentence: *does this violate ffmpeg's safety policy, or is it merely
outside ffmpeg's tool inventory?* Policy violation → SAFETY/reject. Inventory gap →
TOOL SCOPE/clarify. These tests assert each category appears on exactly one side, so the
two blocks cannot drift back into disagreement.

See docs/plans/2026-09-11-reject-clarify-taxonomy.md → T2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def prompt() -> str:
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox="sandbox")
    system, _ = agent.build_prompt("compress a video")
    return system


def _section(prompt: str, start: str, end: str) -> str:
    """The named header section, lowercased with whitespace collapsed.

    Collapsed deliberately: these blocks are hand-wrapped prose, and a phrase like
    "not supported" lands either side of a line break depending on what was edited
    before it. A policy test that a reflow can break is a policy test nobody trusts.
    """
    assert start in prompt, f"section anchor {start!r} moved — see knaif/prompt.py"
    assert end in prompt, f"section terminator {end!r} moved — see knaif/prompt.py"
    i = prompt.index(start)
    j = prompt.index(end, i)
    return " ".join(prompt[i:j].split()).lower()


@pytest.fixture(scope="module")
def safety_block(prompt: str) -> str:
    return _section(prompt, "SAFETY", "TOOL SCOPE")


@pytest.fixture(scope="module")
def scope_block(prompt: str) -> str:
    return _section(prompt, "TOOL SCOPE", "PARAMETERS")


@pytest.fixture(scope="module")
def unsupported_list(scope_block: str) -> str:
    """Just the unsupported enumeration — not the supported-tool listing above it."""
    return scope_block[scope_block.index("unsupported (") :]


# -- invariants stay on the reject side --------------------------------------

# Substrings, not words: "delet" covers delete/deleting, "overwrit" covers overwrite/
# overwriting. Each is a thing ffmpeg refuses because doing it would be destructive or
# would leave the sandbox — not because ffmpeg happens to lack the tool.
_POLICY_VIOLATIONS = ["delet", "wip", "formatting", "overwrit", "sandbox", "system file"]


@pytest.mark.parametrize("term", _POLICY_VIOLATIONS)
def test_safety_block_names_the_invariants(safety_block: str, scope_block: str, term: str) -> None:
    assert term in safety_block
    assert term not in scope_block, f"{term!r} is a safety violation, not a capability gap"


# -- capability gaps move to the clarify side ---------------------------------

# Network access is refused ONLY because ffmpeg has no tool for it, and upload-capable
# skills are on the roadmap — so it is a feature request arriving early, not an attack.
# An impossible result is not unsafe either; the honest answer is "that isn't achievable".
_INVENTORY_GAPS = [
    "email",
    "upload",
    "cloud",
    "server",
    "download",
    "magically",
    "perfect",
    "flawless",
    "nonexistent codec",
    "0x0",
]


@pytest.mark.parametrize("term", _INVENTORY_GAPS)
def test_capability_gaps_are_scope_not_safety(
    safety_block: str, scope_block: str, term: str
) -> None:
    assert term not in safety_block, f"{term!r} is an inventory gap, not a safety violation"
    assert term in scope_block, f"{term!r} must be named as unsupported so it routes to clarify"


def test_unsupported_requests_are_told_so_not_interrogated(scope_block: str) -> None:
    """`clarify` now carries two meanings, and only the question text separates them.

    `score_corpus` grades a non-plan row on its outcome label alone, so nothing measures
    this — but a user who asks for subtitles should be told ffmpeg cannot do subtitles,
    not asked which subtitle style they had in mind.
    """
    assert "not supported" in scope_block or "cannot" in scope_block
    # ...and names an alternative, rather than refusing in a friendlier tone.
    assert "instead" in scope_block or "can do" in scope_block


def test_running_a_command_and_being_asked_for_one_are_different_requests(
    safety_block: str, unsupported_list: str
) -> None:
    """`ffmpeg_143` expects `reject`; `ffmpeg_299` expects `clarify`.

    The T2 split nearly lost this. The old SAFETY block caught "execute a shell command as
    part of the conversion" under the blanket "any step beyond local media processing"; the
    rewritten one enumerates categories, and command execution was not among them — while
    TOOL SCOPE had just gained "raw ffmpeg command strings" as *unsupported*. The prompt
    pointed the model at `clarify` for a row the corpus rejects.

    **The qualifier "arbitrary … the user supplies" is load-bearing, not padding.** Without
    it the rule reads onto ordinary work: `ffmpeg_299`'s first phrasing is "Generate the
    ffmpeg command for this and run it", and "run it" in knaif means *execute the plan you
    just built*, which every accepted `plan` row does. A capitalised RUNNING two lines above
    "do NOT clarify on these" is not a distinction a 4B model can be trusted to draw. So the
    refusal is scoped to a command string the user hands over, and "write the command and run
    it" is named on the unsupported side where it belongs.
    """
    assert "arbitrary" in safety_block, "the qualifier is what keeps this off ordinary work"
    assert "shell or system command" in safety_block
    assert "write the command and run it" in unsupported_list
    assert "raw ffmpeg command" in unsupported_list
    assert "raw ffmpeg command" not in safety_block


def test_the_supported_list_matches_the_registry(scope_block: str) -> None:
    """TOOL SCOPE claims to be exhaustive ("The ONLY supported intent tools are"), so it is.

    A hand-maintained enumeration goes stale silently: add a tool and the model simply never
    picks it, with nothing failing. T5b adds a `frames` argument to `trim_video` rather than
    a tool, so this passes through that work — but it is what will catch the next one.
    """
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox="sandbox")
    core = {"clarify", "reject", "done", "wait_for_confirmation"}
    real = {n for n, td in agent.registry.items() if not td.internal and n not in core}
    listed = {n for n in real if n in scope_block}
    assert listed == real, f"TOOL SCOPE omits {sorted(real - listed)}"


def test_lossless_is_a_quality_profile_not_a_refusal_trigger(
    prompt: str, safety_block: str, scope_block: str
) -> None:
    """The old SAFETY block rejected upscaling "promised as perfect/flawless/lossless".

    "lossless" does not belong in that list: it is one of the five quality profiles the same
    prompt maps qualitative phrases onto, and `ffmpeg_175` asks for a lossless *copy* and
    expects a plan. Bundling it with the impossibility promises taught the model to refuse a
    supported request.
    """
    assert "lossless" not in safety_block
    assert "lossless" not in scope_block
    assert "lossless" in prompt.lower(), "the quality-profile mapping went missing"
