#!/usr/bin/env python3
"""Print the exact ``(system, user)`` prompt the Python runtime would send, and stop.

The counterpart to ``knaif plan --skill X --dump-prompt`` on the native side. Both emit the
identical banner format so the two can be diffed line by line::

    uv run python scripts/dump_prompt.py --skill ffmpeg --batch utterances.txt > py.txt
    ./target/debug/knaif plan --skill ffmpeg --dump-prompt --batch utterances.txt > rs.txt
    diff py.txt rs.txt

Why this exists (P0 of docs/plans/2026-08-08-native-python-planning-parity.md): neither runtime
could show the prompt for an utterance that planned *successfully*. Native's ``$KNAIF_DEBUG``
fires only on a parse/validation failure, and Python had no dump at all — so the one artefact the
parity diagnosis rests on could not be produced on either side.

**Retrieval is on by default, because that is what the runtime does.** ``run_corpus`` calls
``retrieve_tools`` per utterance and passes the subset as ``registry_override``
(``evalsuite/runner.py:98``), and the fine-tuning dataset builder does the same
(``training/build_dataset.py:132``). A dump without it would show a prompt no production path
ever sends. ``--no-retrieval`` reproduces the unfiltered prompt, which is what native currently
builds — the divergence under investigation — and mirrors the eval suite's own flag of that name.

No model is loaded on either side: the prompt is a pure function of the skill bundle and the
utterance, so this needs no GGUF.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"


def dump_prompt_block(utterance: str, system: str, user: str) -> str:
    """Render one dump block.

    Byte-for-byte the same format as the Rust ``dump_prompt_block`` in ``apps/cli/src/main.rs`` —
    change one and you must change the other, or the diff this exists to produce becomes noise.
    Banner delimiters rather than JSON: JSON escaping collapses each message onto a single line,
    where every difference reads as "the line changed".
    """
    return (
        f"===== utterance =====\n{utterance}\n"
        f"===== system =====\n{system}\n"
        f"===== user =====\n{user}\n"
        f"===== end =====\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skill", required=True, help="skill bundle name, e.g. ffmpeg")
    parser.add_argument(
        "--batch",
        type=Path,
        help="file with one utterance per line; output order matches input order",
    )
    parser.add_argument(
        "--no-retrieval",
        action="store_true",
        help="pass the whole registry instead of the retrieved subset (what native does today)",
    )
    parser.add_argument("utterance", nargs="*", help="the request, when --batch is not used")
    args = parser.parse_args(argv)

    if args.batch:
        # splitlines() rather than read().split("\n"): matches the Rust side's `.lines()`, which
        # drops the trailing empty element a final newline would otherwise produce. Blank interior
        # lines are kept so the output stays aligned with the input, as `plan --batch` does.
        utterances = args.batch.read_text(encoding="utf-8").splitlines()
    else:
        utterances = [" ".join(args.utterance)]

    from knaif import CommandAgent
    from knaif.registry import retrieve_tools

    skill_dir = SKILLS_DIR / args.skill
    if not skill_dir.is_dir():
        print(f"no such skill bundle: {skill_dir}", file=sys.stderr)
        return 1
    agent = CommandAgent.from_skill(skill_dir, sandbox=REPO_ROOT / "sandbox")

    # newline="\n" on the stream, not just in the strings: a Windows console would otherwise
    # translate every \n to \r\n on redirect and flag every line of the diff as changed.
    out = open(sys.stdout.fileno(), "w", encoding="utf-8", newline="\n", closefd=False)
    for utterance in utterances:
        override = None if args.no_retrieval else retrieve_tools(utterance, agent.registry)
        system, user = agent.build_prompt(utterance, registry_override=override)
        out.write(dump_prompt_block(utterance, system, user))
    out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
