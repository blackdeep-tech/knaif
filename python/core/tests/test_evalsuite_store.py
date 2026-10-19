"""The result store's reply half (plan 2026-10-14-faster-release-evidence, E1/E2, T2c).

A namespace is one (model, inference fingerprint, platform, backend class, run settings)
combination: the things that, besides the exact prompt, decide a raw reply under greedy decoding. Inside it, the
binary itself writes and reads `<prompt_key>.txt` (`KNAIF_LLM_RECORD_DIR`, `KNAIF_LLM_BACKEND=
replay`). Anything that could change a reply must change the namespace, and nothing else may.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.store import (
    COMPONENTS,
    RUN_SETTINGS,
    is_replay_miss,
    namespace,
    namespace_dir,
    prompt_key,
    run_settings,
)

BASE = {
    "model_sha256": "m" * 64,
    "inference": "i" * 64,
    "platform": "windows-x64",
    "backend": "cuda",
    "settings": {"KNAIF_N_THREADS": None, "KNAIF_N_CTX": None},
}


def test_the_prompt_key_matches_the_binary_s() -> None:
    """The literal is the Rust unit test's (`knaif-llm`, `the_prompt_key_is_...`)."""
    assert (
        prompt_key("s", "u") == "f30a8c69aa63889b1c10206f9668943724ee24dff65dcd7790e7d41426d3031e"
    )


@pytest.mark.parametrize(
    "field,value",
    [("model_sha256", "n" * 64), ("inference", "j" * 64), ("backend", "cpu"),
     ("platform", "linux-x64"),
     ("settings", {"KNAIF_N_THREADS": "8", "KNAIF_N_CTX": None})],
)  # fmt: skip
def test_each_component_moves_the_namespace(field: str, value: object) -> None:
    assert namespace({**BASE, field: value}) != namespace(BASE)


@pytest.mark.parametrize("field", COMPONENTS)
def test_a_namespace_missing_a_component_is_refused(field: str) -> None:
    """The platform joined on 2026-10-19 (owner): Linux CPU planned 3-6% of 1.3.0's rows unlike
    Windows CPU. A caller that left a component out would share replies it must not."""
    with pytest.raises(ValueError, match=field):
        namespace({k: v for k, v in BASE.items() if k != field})


def test_a_namespace_with_an_unknown_component_is_refused() -> None:
    with pytest.raises(ValueError, match="driver"):
        namespace({**BASE, "driver": "580"})


def test_the_namespace_is_deterministic() -> None:
    assert namespace(dict(reversed(list(BASE.items())))) == namespace(BASE)


def test_the_namespace_folder_says_what_it_holds(tmp_path: Path) -> None:
    folder = namespace_dir(tmp_path, BASE)
    assert folder == tmp_path / "replies" / namespace(BASE)
    assert json.loads((folder / "key.json").read_text("utf-8")) == BASE
    assert namespace_dir(tmp_path, BASE) == folder, "opening it again changes nothing"


def test_run_settings_read_what_changes_the_arithmetic(monkeypatch) -> None:
    for name in RUN_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KNAIF_N_THREADS", "8")
    settings = run_settings()
    assert settings["KNAIF_N_THREADS"] == "8"
    assert set(settings) == set(RUN_SETTINGS)
    assert {"KNAIF_N_CTX", "KNAIF_MAX_TOKENS", "KNAIF_N_GPU_LAYERS"} <= set(RUN_SETTINGS)
    # Prompt decode threads change the accumulation order on the CPU (llama.rs, daemon.rs).
    assert "KNAIF_N_THREADS_BATCH" in RUN_SETTINGS


def test_a_replay_miss_is_recognised_and_nothing_else_is() -> None:
    miss = {"plan": [], "error": "replay miss: no stored reply for prompt abc in x"}
    assert is_replay_miss(miss)
    assert not is_replay_miss({"plan": [], "error": "invalid plan: unknown tool"})
    assert not is_replay_miss({"plan": [{"tool": "x", "args": {}}]})
