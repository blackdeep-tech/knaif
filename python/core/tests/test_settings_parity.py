"""L1c: the settings-parity contract.

Both runtimes must agree on the generation settings they actually use — `max_tokens`,
`n_ctx`, sampling, thinking suppression. They agree today at 512 / 8192 / greedy /
suppressed, but in **three** separate places: a Rust literal in `knaif-llm`, the promoted
stanza in `models.yaml`, and the eval backend in `eval_backends.yaml`. Nothing makes them
agree; they simply do, and that duplication has already produced one wrong finding (a
superseded stanza read as live).

Written deliberately as a *three-way* comparison, not "both read the canonical file".
V4 will create the canonical copy, and at that point this test should be rewritten to
assert each runtime reads it — but a test ported naively onto a single source becomes one
that can only ever pass. Until then, the point is that a change to any one of the three
fails here.

The values are read at their **point of use** — the Rust literal is parsed out of the
source that constructs the loader, not from a doc comment — so a hard-coded fallback
shadowing a config file still fails.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (L1c, V4).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
LLAMA_RS = REPO_ROOT / "native" / "crates" / "knaif-llm" / "src" / "llama.rs"
MODELS_YAML = REPO_ROOT / "models.yaml"
EVAL_BACKENDS = REPO_ROOT / "eval_backends.yaml"

#: The model both runtimes ship and evaluate. The contract is about *this* configuration;
#: other stanzas are study artifacts and deliberately out of scope.
PROMOTED = "knaif-qwen3-4b-v1"
PROMOTED_EVAL_BACKEND = "qwen3-4b-sft-v3-flat-q4"


def _rust_source() -> str:
    return LLAMA_RS.read_text(encoding="utf-8")


def _rust_int(field: str) -> int:
    """Read a struct-literal default out of the Rust loader, at its point of use."""
    src = _rust_source()
    match = re.search(rf"^\s*{field}:\s*(\d+),\s*$", src, re.MULTILINE)
    assert match, f"{LLAMA_RS.name} no longer sets `{field}` as a literal — update this contract"
    return int(match.group(1))


def _rust_n_ctx_default() -> int:
    src = _rust_source()
    match = re.search(r'KNAIF_N_CTX"\s*\)(?:.|\n)*?unwrap_or\((\d+)\)', src)
    assert match, "could not find the n_ctx default in llama.rs — update this contract"
    return int(match.group(1))


def _python_options() -> dict:
    doc = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8"))
    models = doc["models"] if "models" in doc else doc
    return models[PROMOTED]["options"]


def _eval_options() -> dict:
    doc = yaml.safe_load(EVAL_BACKENDS.read_text(encoding="utf-8"))
    return doc["backends"][PROMOTED_EVAL_BACKEND]["options"]


def test_max_tokens_agrees_across_all_three_places() -> None:
    assert (
        _rust_int("max_tokens") == _python_options()["max_tokens"] == _eval_options()["max_tokens"]
    )


def test_n_ctx_agrees_across_all_three_places() -> None:
    assert _rust_n_ctx_default() == _python_options()["n_ctx"] == _eval_options()["n_ctx"]


def test_thinking_is_suppressed_on_both_sides() -> None:
    """Python config says so; native hard-codes `/no_think` into the system turn."""
    assert _python_options()["thinking_enabled"] is False
    assert _eval_options()["thinking_enabled"] is False
    assert "/no_think" in _rust_source(), "native no longer suppresses Qwen3 thinking"


def test_both_runtimes_decode_greedily() -> None:
    """The comparison is only meaningful if neither side samples.

    Python pins temperature 0; native takes argmax. A sampler appearing on either side
    turns every downstream parity number into noise, so it is part of the contract.
    """
    assert "temperature" not in _python_options(), (
        "models.yaml now sets a temperature for the promoted model; native decodes argmax, "
        "so the two are no longer comparable"
    )
    src = _rust_source()
    assert (
        "sample_token_greedy" in src or "argmax" in src.lower()
    ), "native no longer decodes greedily — parity comparisons become sampling noise"


@pytest.mark.parametrize("field", ["max_tokens", "n_ctx"])
def test_the_eval_backend_is_the_shipped_configuration(field: str) -> None:
    """An eval run that measures a different configuration from the shipped one is not
    evidence about the product."""
    assert _eval_options()[field] == _python_options()[field]


def test_retrieval_top_k_agrees_across_runtimes() -> None:
    """`top_k` decides how much of the registry the model is shown.

    A disagreement here is not a settings nit: it is a different prompt, and it was real
    until V1 — native showed all 13 ffmpeg tools because retrieval was never called.
    """
    from knaif.registry import DEFAULT_TOP_K

    src = (REPO_ROOT / "native" / "crates" / "knaif-core" / "src" / "retrieval.rs").read_text(
        encoding="utf-8"
    )
    match = re.search(r"pub const DEFAULT_TOP_K: usize = (\d+);", src)
    assert match, "knaif-core no longer declares DEFAULT_TOP_K — update this contract"
    assert int(match.group(1)) == DEFAULT_TOP_K
