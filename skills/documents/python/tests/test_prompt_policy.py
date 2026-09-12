"""documents' reject/clarify policy, as the model actually reads it.

``contracts/runtime/core_tools.yaml`` says only that ``reject`` means *the active skill's
safety policy was violated*. That makes declaring a policy each skill's job — and documents
had no SAFETY block at all, so after the contract split it inherited a rule that deferred to
a policy it never wrote down. These tests pin both halves of the split for documents, the
same way ``skills/ffmpeg/python/tests/test_prompt_policy.py`` does for ffmpeg.

documents is **not** a copy of ffmpeg's answer. It shares the destructive invariants and the
network categories, and it adds one of its own: forging a signature stays a ``reject``
because no change to the tool inventory turns it into a capability gap — unlike printing or
faxing, which are simply things this skill cannot do.

See docs/plans/2026-09-11-reject-clarify-taxonomy.md → T3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent

DOCUMENTS_SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def prompt() -> str:
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox="sandbox")
    system, _ = agent.build_prompt("compress report.pdf")
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
    # "Available tools:" ends the hand-written header and begins the generated tool listing.
    # Searching past it would read tool *descriptions* ("delete pages", "unlock a PDF") as
    # policy statements — which is how the first draft of this test managed to fail.
    return _section(prompt, "TOOL SCOPE", "Available tools:")


@pytest.fixture(scope="module")
def unsupported_list(scope_block: str) -> str:
    """Just the unsupported enumeration — not the supported-tool listing above it.

    The distinction matters: `unlock_pdf` is a tool name inside TOOL SCOPE, and a test that
    searched the whole block would read it as evidence that unlocking is unsupported.
    """
    return scope_block[scope_block.index("unsupported (") :]


# -- the policy exists at all -------------------------------------------------


def test_documents_declares_a_safety_policy(prompt: str) -> None:
    """The contract defers to the skill; a skill that declares nothing rejects nothing."""
    assert "SAFETY" in prompt
    assert "TOOL SCOPE" in prompt


# -- invariants stay on the reject side --------------------------------------

_POLICY_VIOLATIONS = [
    "delet",
    "shred",
    "wip",
    "formatting",
    "overwrit",
    "sandbox",
    "system file",
    "forg",
]


@pytest.mark.parametrize("term", _POLICY_VIOLATIONS)
def test_safety_block_names_the_invariants(safety_block: str, scope_block: str, term: str) -> None:
    assert term in safety_block
    assert term not in scope_block, f"{term!r} is a safety violation, not a capability gap"


# -- capability gaps move to the clarify side ---------------------------------

# Print and fax are the same category as email and upload under a different verb, and the
# earlier split between them had no argument behind it. One fine-tune serves every skill, so
# teaching "upload -> reject" would teach a future upload skill that its job is a refusal.
_INVENTORY_GAPS = ["email", "upload", "cloud", "download", "print", "fax", "translat"]


@pytest.mark.parametrize("term", _INVENTORY_GAPS)
def test_capability_gaps_are_scope_not_safety(
    safety_block: str, unsupported_list: str, term: str
) -> None:
    assert term not in safety_block, f"{term!r} is an inventory gap, not a safety violation"
    assert (
        term in unsupported_list
    ), f"{term!r} must be named as unsupported so it routes to clarify"


def test_unsupported_requests_are_told_so_not_interrogated(scope_block: str) -> None:
    """`clarify` carries two meanings here too, and only the question text separates them."""
    assert "not supported" in scope_block or "cannot" in scope_block
    assert "instead" in scope_block or "can do" in scope_block


def test_the_reject_verbs_carve_out_the_tools_that_share_them(safety_block: str) -> None:
    """documents is not ffmpeg: *delete* and *remove* are verbs of SUPPORTED operations here.

    ffmpeg can afford a flat "deleting ... storage" rule because no ffmpeg tool deletes
    anything. documents ships `remove_pages` and `unlock_pdf`, and twelve `eval.jsonl` rows
    that expect a **plan** ask in exactly these words — "remove page 3", "delete page 2",
    "delete the last page", "remove the password from sample-protected.pdf". The generated
    tool listing a thousand characters below even renders *"Delete specific pages and save a
    copy without them."* A flat rule pulls all of that toward `reject`, which is the very
    failure T1 exists to remove, arriving from the opposite direction.

    The refusal object must therefore be files and folders, not bare verbs, and the two
    colliding tools must be named as exclusions where the model reads the rule.
    """
    assert "files or folders" in safety_block, "the refusal object must be files, not a verb"
    assert "remove_pages" in safety_block
    assert "unlock_pdf" in safety_block
    assert "is not a deletion" in safety_block


def test_unlocking_a_pdf_is_not_a_refusal(scope_block: str, unsupported_list: str) -> None:
    """`unlock_pdf` is a shipped tool and `documents_016` expects a clarify, not a reject.

    Forging a signature is misuse; removing a password from your own PDF is the skill's job.
    The two must not be collapsed just because both mention a document's protection. Note
    SAFETY *does* name `unlock_pdf` — as an explicit exclusion, pinned by the test above.
    """
    assert "unlock_pdf" in scope_block, "unlock_pdf is a supported tool and must be listed"
    assert "unlock" not in unsupported_list


def test_the_supported_list_matches_the_registry(scope_block: str) -> None:
    """TOOL SCOPE claims to be exhaustive ("The ONLY supported intent tools are"), so it is.

    A hand-maintained enumeration goes stale silently: add a tool and the model simply never
    picks it, with nothing failing. This is also what will flag the T5 forge-vs-sign wording
    the day a redaction or signing tool lands — both are on the unsupported list today.
    """
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox="sandbox")
    core = {"clarify", "reject", "done", "wait_for_confirmation"}
    real = {n for n, td in agent.registry.items() if not td.internal and n not in core}
    listed = {n for n in real if n in scope_block}
    assert listed == real, f"TOOL SCOPE omits {sorted(real - listed)}"
