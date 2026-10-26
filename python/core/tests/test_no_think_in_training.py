"""`/no_think` is in the system turn at inference, so it must be in the trained one too.

Qwen3 skips its thinking block when the system prompt ends in `/no_think`. Both runtimes append
it at inference (`orchestrator.py`, `knaif-llm` `llama.rs`), but the training set was built from
`agent.build_prompt()` alone, so the model was tuned on a system turn it is never served (model
review, R3). One function now writes the suffix, and the dataset builder uses it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from knaif.orchestrator import InferenceOrchestrator, with_no_think

ROOT = Path(__file__).resolve().parents[3]


def test_with_no_think_appends_the_suffix_after_a_blank_line():
    assert with_no_think("You plan.") == "You plan.\n\n/no_think"
    assert with_no_think("") == "/no_think"


def test_inference_uses_the_same_function():
    # No model path: nothing loads (it warns), which is all this check needs.
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={})
    assert orch._apply_thinking("You plan.") == with_no_think("You plan.")


def _build_dataset():
    spec = importlib.util.spec_from_file_location(
        "build_dataset", ROOT / "python" / "training" / "build_dataset.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_training_row_carries_the_served_system_turn():
    plan = {"plan": [{"tool": "strip_audio", "args": {"inputs": ["a.mp4"]}}]}
    row = _build_dataset().chat_row("ffmpeg", "You plan.", "mute a.mp4", plan)
    system, user, assistant = row["messages"]
    assert system == {"role": "system", "content": with_no_think("You plan.")}
    assert user == {"role": "user", "content": "mute a.mp4"}
    assert json.loads(assistant["content"]) == plan
    assert row["skill"] == "ffmpeg"
