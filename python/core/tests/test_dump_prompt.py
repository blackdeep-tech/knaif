"""Guard the prompt-dump format, which is implemented twice — once per runtime.

`scripts/dump_prompt.py` and `dump_prompt_block` in `apps/cli/src/main.rs` must emit byte-identical
framing, because the whole point of the pair is `diff py.txt rs.txt`. If the two formats drift, the
diff fills with banner noise and the real divergence — the thing the parity plan exists to find —
is buried in it.

There is no shared source to generate both from: one is Rust in the shipped binary, the other is
Python in a script, and neither runtime can import the other. So this test reads the Rust format
literal out of the source and asserts the Python function reproduces it. That is weaker than a
generated copy, and stronger than a comment asking the next person to remember.

Context: P0 of docs/plans/2026-08-08-native-python-planning-parity.md.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUST_MAIN = REPO_ROOT / "apps" / "cli" / "src" / "main.rs"
DUMP_SCRIPT = REPO_ROOT / "scripts" / "dump_prompt.py"


def _load_dump_prompt():
    """Import scripts/dump_prompt.py by path — `scripts/` is not an importable package."""
    spec = importlib.util.spec_from_file_location("_dump_prompt", DUMP_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rust_format_literal() -> str:
    """Extract the format string from the Rust `dump_prompt_block`, unescaping as rustc would.

    The literal uses `\\` line continuations so the source stays inside the line limit; rustc drops
    the backslash, the newline, and the leading whitespace of the next line. Reproduce that, then
    substitute the `{...}` placeholders so the result can be compared against Python's output.
    """
    source = RUST_MAIN.read_text(encoding="utf-8")
    match = re.search(
        r"fn dump_prompt_block\([^)]*\)\s*->\s*String\s*\{.*?format!\(\s*(\".*?\")\s*\)",
        source,
        re.S,
    )
    assert match, "could not find dump_prompt_block's format! literal in main.rs"
    literal = match.group(1)[1:-1]  # strip the surrounding quotes
    # Rust's `\` at end-of-line swallows the newline and the next line's indentation.
    literal = re.sub(r"\\\n\s*", "", literal)
    return literal.replace("\\n", "\n")


def test_python_dump_matches_the_rust_format() -> None:
    module = _load_dump_prompt()
    expected = (
        _rust_format_literal()
        .replace("{utterance}", "UTT")
        .replace("{system}", "SYS")
        .replace("{user}", "USR")
    )
    assert module.dump_prompt_block("UTT", "SYS", "USR") == expected


def test_dump_block_is_lf_only_and_verbatim() -> None:
    module = _load_dump_prompt()
    # Messages carrying whitespace, quotes and backslashes must survive untouched: the dump is
    # evidence, and a dumper that normalizes its input is evidence of itself.
    system = "  leading\n\nblank above\ttab\n"
    user = 'trailing   \n"quotes" and \\backslashes\\'
    block = module.dump_prompt_block("u", system, user)
    assert system in block
    assert user in block
    assert "\r" not in block


def test_dump_block_separates_empty_messages() -> None:
    module = _load_dump_prompt()
    assert module.dump_prompt_block("", "", "") == (
        "===== utterance =====\n\n===== system =====\n\n===== user =====\n\n===== end =====\n"
    )


@pytest.mark.parametrize(
    "banner", ["===== utterance =====", "===== system =====", "===== user ====="]
)
def test_banners_do_not_occur_in_a_real_prompt(banner: str) -> None:
    """A delimiter that can appear inside a prompt would make the dump ambiguous to parse."""
    from knaif import CommandAgent
    from knaif.registry import retrieve_tools

    skill_dir = REPO_ROOT / "skills" / "ffmpeg"
    agent = CommandAgent.from_skill(skill_dir, sandbox=REPO_ROOT / "sandbox")
    utterance = "trim clip.mp4 to 5 seconds then resize to 720p"
    system, user = agent.build_prompt(
        utterance, registry_override=retrieve_tools(utterance, agent.registry)
    )
    assert banner not in system
    assert banner not in user
