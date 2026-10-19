"""The native fingerprints the result store keys on (plan 2026-10-14-faster-release-evidence, A3,
T2, as amended by the owner 2026-10-19).

The gate's `native` fingerprint hashes the whole native tree, so any change stales every record.
The store needs finer keys, each covering exactly what determines its value (E1):

* A **plan** is the model's raw reply to one prompt. With greedy decoding it is determined by the
  prompt text, the model, the backend and the **inference** stack: the code and settings between
  the prompt and the reply (`knaif-llm`, model and backend-library resolution in `knaif-models`,
  the llama.cpp version, `generation.yaml`). The prompt text itself is hashed per utterance from
  the new binary's own dump (`KNAIF_DUMP_PROMPT`, no model needed), so the code that builds it
  needs no fingerprint: a version bump or an unrelated CLI change keeps every plan, a changed
  prompt or inference stack re-plans. A file-level planning fingerprint would move at every
  release, because the workspace version lives in one of its files.
* An **outcome** is what a replayed reply becomes: parsed, validated, gated, expanded, rendered,
  run. With the reply replayed through the mock that is every native file except the inference
  stack, so the **execution** fingerprint takes all of them, per skill: one skill's crate and
  bundle, plus everything shared. Per skill, not per tool (owner, 2026-10-19): ffmpeg renders
  every tool through one engine and documents' tools reach nearly every module of the skill.

Both are conservative where it is cheap to be: execution re-runs in a fraction of a second a row,
so the shared side takes the whole `Cargo.lock` and every manifest. Whatever a key cannot express
is caught by the cold-start sample, which re-plans and re-runs live in every cell (E3, I6).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .gate import NATIVE_PATTERNS, _sha256_tree

#: Between a prompt and the model's reply: decoding, generation settings, model and backend
#: library resolution, the llama.cpp bindings (their `Cargo.lock` entries are added separately).
INFERENCE: tuple[str, ...] = (
    "native/crates/knaif-llm/src/**/*.rs",
    "native/crates/knaif-llm/Cargo.toml",
    "native/crates/knaif-models/src/**/*.rs",
    "native/crates/knaif-models/Cargo.toml",
    "contracts/runtime/generation.yaml",
)

#: Every native file outside the inference stack and the skill crates, and what they read.
EXECUTION_SHARED: tuple[str, ...] = (
    "native/crates/knaif-core/src/**/*.rs",
    "native/crates/knaif-core/Cargo.toml",
    "native/crates/knaif-skill-api/src/**/*.rs",
    "native/crates/knaif-skill-api/Cargo.toml",
    "apps/cli/src/**/*.rs",
    "apps/cli/Cargo.toml",
    "Cargo.toml",
    "Cargo.lock",
    "contracts/runtime/**/*.yaml",
)

#: One skill's crate and the bundle files its runtime reads. Named, not `*.yaml`: the bundle's
#: top also holds `acceptance.yaml`, the grading bar, which no run reads.
EXECUTION_SKILL: tuple[str, ...] = (
    "skill.yaml",
    "tools.yaml",
    "prompt.yaml",
    "vocab.yaml",
    "profiles/**/*",
    "native/src/**/*.rs",
    "native/Cargo.toml",
)

_LOCK_PACKAGE = re.compile(r"^\[\[package\]\]\s*$", re.MULTILINE)


def _llama_lock_entries(root: Path) -> str:
    """The `Cargo.lock` entries of the llama.cpp bindings: their version decides the arithmetic."""
    lock = root / "Cargo.lock"
    if not lock.is_file():
        return ""
    text = lock.read_text(encoding="utf-8").replace("\r\n", "\n")
    blocks = [b.strip() for b in _LOCK_PACKAGE.split(text)]
    return "\n\n".join(b for b in blocks if re.search(r'^name = "llama-cpp', b, re.MULTILINE))


def _combine(parts: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def inference_fingerprint(root: Path | str = ".") -> str:
    """The inference stack: with the prompt text, model and backend, the store's plan key (E1)."""
    root = Path(root)
    return _combine(
        {
            "code": _sha256_tree(root, INFERENCE),
            "llama": hashlib.sha256(_llama_lock_entries(root).encode()).hexdigest(),
        }
    )


def execution_fingerprint(skill: str, root: Path | str = ".") -> str:
    """What turns *skill*'s replayed replies into files: the store's outcome key component (E1)."""
    root = Path(root)
    return _combine(
        {
            "shared": _sha256_tree(root, EXECUTION_SHARED),
            "skill": _sha256_tree(root / "skills" / skill, EXECUTION_SKILL),
        }
    )


def unclassified_native_files(root: Path | str = ".") -> list[str]:
    """Native source files (the gate's `native` fingerprint) that neither key covers."""
    root = Path(root)

    def files(base: Path, patterns: tuple[str, ...]) -> set[Path]:
        return {p for pattern in patterns for p in base.glob(pattern) if p.is_file()}

    native = files(root, NATIVE_PATTERNS)
    covered = files(root, INFERENCE) | files(root, EXECUTION_SHARED)
    for bundle in (root / "skills").glob("*"):
        covered |= files(bundle, EXECUTION_SKILL)
    return sorted(p.relative_to(root).as_posix() for p in native - covered)
