"""The result store's reply half (plan 2026-10-14-faster-release-evidence, E1/E2, T2c).

The store keeps the model's **raw replies**, not the plans the batch prints: a printed plan has
already passed parsing, validation, an occasional repair call, the clarify gate and stem
resolution, which are execution code that a later release may change. A reply is replayed into
the new binary (`KNAIF_LLM_BACKEND=replay`), which runs everything after the model afresh.

Under greedy decoding a reply is decided by the exact prompt plus four things, which together name
a **namespace**: the model's sha256, the inference fingerprint (`evalsuite.fingerprints`), the
backend's arithmetic class, and the run settings that change the arithmetic. Inside a namespace
folder the binary itself writes and reads `<prompt_key>.txt` (`KNAIF_LLM_RECORD_DIR`,
`KNAIF_LLM_REPLAY_DIR`); `key.json` says what the folder holds. A prompt the namespace does not
hold is a replay miss, and the misses are exactly the utterances a cell must plan with the model.

Never reused (E4): the cold-start sample, which runs live in every cell and must agree with the
replayed plans, is the check on everything a key cannot express.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

#: Default store root, committed like the run folders (owner, 2026-10-15).
STORE_ROOT = Path("evals/store")

#: Run settings that can change a reply on the same model, build and backend.
RUN_SETTINGS: tuple[str, ...] = (
    "KNAIF_N_THREADS",
    "KNAIF_N_THREADS_BATCH",
    "KNAIF_N_CTX",
    "KNAIF_MAX_TOKENS",
    "KNAIF_N_GPU_LAYERS",
    "KNAIF_PREFIX_REUSE",
)

#: What the binary's replay backend says on a miss (`knaif-llm`, `ReplayBackend`).
REPLAY_MISS = "replay miss"


def prompt_key(system: str, user: str) -> str:
    """The binary's `knaif_llm::prompt_key`: sha256 of `system`, a NUL byte and `user`, UTF-8."""
    return hashlib.sha256(system.encode("utf-8") + b"\0" + user.encode("utf-8")).hexdigest()


def run_settings(env: dict[str, str] | None = None) -> dict[str, str | None]:
    """The values of `RUN_SETTINGS` in *env* (default: this process's), unset as None."""
    env = os.environ if env is None else env
    return {name: (env.get(name) or None) for name in RUN_SETTINGS}


def namespace(components: dict[str, Any]) -> str:
    """The namespace id: sha256 of the components as canonical JSON."""
    return hashlib.sha256(json.dumps(components, sort_keys=True).encode("utf-8")).hexdigest()


def namespace_dir(root: Path | str, components: dict[str, Any]) -> Path:
    """The namespace's folder under *root*, created with its `key.json` if new."""
    folder = Path(root) / "replies" / namespace(components)
    folder.mkdir(parents=True, exist_ok=True)
    key = folder / "key.json"
    if not key.exists():
        key.write_text(json.dumps(components, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return folder


def is_replay_miss(envelope: dict[str, Any]) -> bool:
    """Whether a `plan --batch` line failed because the namespace holds no reply for its prompt."""
    return REPLAY_MISS in str(envelope.get("error") or "")
