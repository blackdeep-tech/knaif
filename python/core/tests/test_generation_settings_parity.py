"""R3: the two runtimes' generation defaults must agree, and agree with the declared contract.

`contracts/parity/generation_settings.yaml` is the single declaration; this test asserts the
Python side (`models.yaml`, `eval_backends.yaml`) matches it, and — because nothing else in the
repo does — that the **Rust** literals match it too, by reading `knaif-llm/src/llama.rs`.

Reading Rust source from a Python test is deliberate. The alternative is trusting that whoever
edits one side remembers the other, which is exactly what failed here: this plan's first write-up
recorded a 512-vs-2048 divergence that did not exist, because the value was read from a superseded
`eval_backends.yaml` stanza. A test that names its sources cannot make that mistake. The Rust side
has its own half of this check in `native/crates/knaif-llm/tests/settings_parity.rs`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPO_ROOT / "contracts" / "parity" / "generation_settings.yaml"


@pytest.fixture(scope="module")
def contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def _stanza(path: Path, key: str) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    backends = doc.get("backends") or doc.get("models") or doc
    assert key in backends, f"{path.name} has no stanza {key!r}"
    return backends[key]["options"]


def test_models_yaml_matches_the_contract(contract: dict) -> None:
    want = contract["settings"]
    opts = _stanza(REPO_ROOT / contract["sources"]["python"]["file"], contract["model"])

    assert opts["max_tokens"] == want["max_tokens"]
    assert opts["n_ctx"] == want["n_ctx"]
    assert opts["thinking_enabled"] == want["thinking_enabled"]


def test_promoted_eval_stanza_matches_the_contract(contract: dict) -> None:
    """The eval stanza is a separate copy of the same numbers, and the one that misled the plan."""
    want = contract["settings"]
    src = contract["sources"]["python_eval"]
    opts = _stanza(REPO_ROOT / src["file"], src["stanza"])

    assert opts["max_tokens"] == want["max_tokens"]
    assert opts["n_ctx"] == want["n_ctx"]
    assert opts["thinking_enabled"] == want["thinking_enabled"]


def _env_fallback(text: str, var: str) -> int:
    """Return the `.unwrap_or(N)` that terminates the `std::env::var(VAR)` chain.

    Anchored on the variable name rather than searching the file for any `unwrap_or(...)`: the
    first draft of this test did the loose thing and matched an unrelated `unwrap_or(4)` in the
    thread-count helper, asserting 4 == 8192. A contract that reads the wrong line is worse than
    none, because it reports confidently.
    """
    start = text.find(f'std::env::var("{var}")')
    assert start != -1, f"no `std::env::var({var})` in the source"
    # Scan forward a short window rather than matching the whole builder chain: the chain contains
    # nested parens (`.and_then(|v| v.parse().ok())`) that a naive character class cannot span.
    window = text[start : start + 300]
    match = re.search(r"unwrap_or\((\d+)\)", window)
    assert match, f"no `unwrap_or(<n>)` terminating the {var} chain"
    return int(match.group(1))


def test_native_literals_match_the_contract(contract: dict) -> None:
    """Read the Rust defaults out of the source; nothing else links them to the YAML."""
    src = contract["sources"]["native"]
    want = contract["settings"]
    struct_text = (REPO_ROOT / src["struct_defaults"]).read_text(encoding="utf-8")
    env_text = (REPO_ROOT / src["env_defaults"]).read_text(encoding="utf-8")

    # The struct literal: `max_tokens: 512,` in the LlamaCppBackend constructor.
    struct_default = re.search(r"max_tokens:\s*(\d+)\s*,", struct_text)
    assert struct_default, f"no `max_tokens: <n>` literal in {src['struct_defaults']}"
    assert int(struct_default.group(1)) == want["max_tokens"], (
        f"native struct max_tokens={struct_default.group(1)} but the contract declares "
        f"{want['max_tokens']}; a chain is the longest output the model emits, so a smaller "
        f"value truncates multi-step plans on one runtime only"
    )

    # The env fallback, applied via `.with_max_tokens(...)` — a second copy of the same number.
    assert _env_fallback(env_text, "KNAIF_MAX_TOKENS") == want["max_tokens"], (
        "the KNAIF_MAX_TOKENS fallback disagrees with the struct default, so the shipped cap "
        "would depend on whether the variable happens to be set"
    )

    assert _env_fallback(struct_text, "KNAIF_N_CTX") == want["n_ctx"]

    # Thinking suppression is a string append, not a number.
    assert want["thinking_enabled"] is False
    assert src["thinking_literal"] in struct_text, "native no longer appends /no_think"


def test_declared_env_overrides_still_exist(contract: dict) -> None:
    """The overrides are legitimate escape hatches; assert they exist where the contract says.

    If one is removed or moves file, an operator following the plan's P3 instructions would
    silently measure nothing — which is how a one-variable experiment becomes a zero-variable one.
    The original plan text located KNAIF_MAX_TOKENS in llama.rs; it is in lib.rs. This test is why
    that is now written down correctly.
    """
    for name, rel in contract["env_overrides"].items():
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert name in text, f"{name} is declared as living in {rel} but is not there"
