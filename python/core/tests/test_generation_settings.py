"""V4: one canonical source for the generation settings, and a guard on each consumer.

`contracts/runtime/generation.yaml` is that source. This side checks the two config files;
`native/crates/knaif-llm/tests/generation.rs` checks the native defaults against the same
file. Between them, a value cannot be changed in one place and quietly disagree in another.

The settings all agreed when the contract was written, so this is hygiene rather than a fix
— but the duplication had already produced one wrong finding (a superseded eval stanza read
as the live configuration), which is what an unguarded fourth copy costs.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (V4).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPO_ROOT / "contracts" / "runtime" / "generation.yaml"

DOC = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
SETTINGS: dict = DOC["settings"]
SHIPPED: dict = DOC["shipped_models"]

# The settings a `models.yaml` / `eval_backends.yaml` stanza can carry. `temperature` is not
# among them — neither file declares it; both runtimes decode greedily, and the Python
# orchestrator passes 0.0 as a literal. It is in the contract because it is a real setting
# that must not drift, and it is asserted against the orchestrator below rather than the YAML.
STANZA_KEYS = (
    "max_tokens",
    "n_ctx",
    "json_mode",
    "thinking_enabled",
    "flash_attn",
    "n_batch",
    "n_ubatch",
    "reset_cache_per_call",
)

# How a contract value is spelled in a Python stanza, where the two differ. llama-cpp-python 0.3.23
# takes flash attention as a bool only; `auto` resolves to enabled on every device the eval lane
# runs on (CUDA, CPU), so `true` is the Python spelling of the contract's `auto`.
PYTHON_SPELLING = {("flash_attn", "auto"): True}


def _python_value(key: str):
    value = SETTINGS[key]
    return PYTHON_SPELLING.get((key, value), value)


def _stanzas(filename: str) -> dict:
    doc = yaml.safe_load((REPO_ROOT / filename).read_text(encoding="utf-8"))
    return doc["models"] if filename == "models.yaml" else doc["backends"]


CONSUMERS = [(filename, name) for filename, names in SHIPPED.items() for name in names]


@pytest.mark.parametrize("filename,name", CONSUMERS, ids=[f"{f}:{n}" for f, n in CONSUMERS])
def test_a_shipped_stanza_matches_the_contract(filename: str, name: str) -> None:
    stanzas = _stanzas(filename)
    assert name in stanzas, f"{filename}: no stanza {name!r} — update the contract or the file"
    options = stanzas[name]["options"]
    for key in STANZA_KEYS:
        assert key in options, f"{filename}:{name} does not declare {key}"
        assert options[key] == _python_value(key), (
            f"{filename}:{name} sets {key}={options[key]!r}, "
            f"contracts/runtime/generation.yaml says {SETTINGS[key]!r}"
        )


@pytest.mark.filterwarnings("ignore:No model path provided")
def test_the_orchestrator_defaults_match_the_contract() -> None:
    """The Python runtime's own fallbacks, for a stanza that declares nothing.

    Native has no per-model configuration at all, so its behavior is always these defaults —
    which makes them the values that decide parity when a config file is silent.
    """
    from knaif.orchestrator import InferenceOrchestrator

    orch = InferenceOrchestrator(backend="llama_cpp", model_config={})
    assert orch.json_mode is SETTINGS["json_mode"]
    assert orch.thinking_enabled is SETTINGS["thinking_enabled"]


@pytest.mark.filterwarnings("ignore:No model path provided")
def test_the_no_think_suffix_follows_thinking_enabled() -> None:
    """`thinking_enabled: false` is not a passive default — it appends `/no_think` to the
    system turn, which native does unconditionally. If this ever stopped being the contract's
    value, the two runtimes would send different system prompts."""
    from knaif.orchestrator import InferenceOrchestrator

    orch = InferenceOrchestrator(backend="llama_cpp", model_config={})
    assert not SETTINGS["thinking_enabled"]
    assert orch._apply_thinking("SYSTEM").endswith("/no_think")


def test_the_orchestrator_decodes_greedily() -> None:
    """`temperature` is passed as a literal, not read from a stanza — so read the source.

    Every occurrence, not just one: asserting the contract's value appears somewhere would
    still pass with a second, non-zero literal on another backend's call path.
    """
    source = (REPO_ROOT / "python" / "core" / "knaif" / "orchestrator.py").read_text(
        encoding="utf-8"
    )
    literals = re.findall(r"temperature=([0-9.]+)", source)
    assert literals, "no temperature literal found — did the call path change?"
    assert {float(v) for v in literals} == {SETTINGS["temperature"]}


def test_the_contract_covers_every_output_affecting_setting() -> None:
    """A new sampler setting must be added here deliberately, not discovered in a diff."""
    assert set(SETTINGS) == {
        "max_tokens",
        "n_ctx",
        "temperature",
        "json_mode",
        "thinking_enabled",
        # docs/plans/2026-09-23-inference-config-parity.md: same tokens, greedy on both sides,
        # and these alone flipped 1.2% of eval outcomes between the lanes.
        "flash_attn",
        "n_batch",
        "n_ubatch",
        "reset_cache_per_call",
    }


def _loaded_kwargs(tmp_path, config: dict) -> dict:
    from unittest.mock import MagicMock, patch

    from knaif.orchestrator import InferenceOrchestrator

    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    module = MagicMock()
    with patch.dict("sys.modules", {"llama_cpp": module}):
        InferenceOrchestrator(backend="llama_cpp", model_config={"path": str(model), **config})
    return module.Llama.call_args.kwargs


def test_the_orchestrator_computes_with_the_contract_config_by_default(tmp_path) -> None:
    """An embedder that names only a model path gets the lanes' shared compute config, not
    llama-cpp-python's defaults (flash attention off, batch 512) that made Python disagree with
    native. Cache reuse is the exception, and deliberately so — see the next test."""
    kwargs = _loaded_kwargs(tmp_path, {"n_ctx": SETTINGS["n_ctx"]})
    assert kwargs["flash_attn"] is _python_value("flash_attn")
    assert kwargs["n_batch"] == SETTINGS["n_batch"]
    assert kwargs["n_ubatch"] == SETTINGS["n_ubatch"]


@pytest.mark.filterwarnings("ignore:No model path provided")
def test_cache_reuse_stays_an_embedder_default(tmp_path) -> None:
    """Reuse makes a repeated SDK call cheap, and outside an eval nothing compares two calls.
    The stanzas that evals and the shipped config read set it; the bare default does not."""
    from knaif.orchestrator import InferenceOrchestrator

    assert SETTINGS["reset_cache_per_call"] is True
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={})
    assert not orch.model_config.get("reset_cache_per_call")


def test_experiment_stanzas_are_not_pinned() -> None:
    """The scope line in the contract, asserted: study stanzas set what their experiment
    needed, and freezing them would freeze the record of the experiments themselves."""
    backends = _stanzas("eval_backends.yaml")
    pinned = set(SHIPPED["eval_backends.yaml"])
    assert pinned < set(backends), "every eval backend is pinned — the scope rule is not holding"
