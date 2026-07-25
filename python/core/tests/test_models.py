"""Tests for knaif.models — runtime model registry and orchestrator builder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import yaml

from knaif.models import (
    build_orchestrator,
    load_models_registry,
    resolve_model_name,
)

# ── load_models_registry ──────────────────────────────────────────────────────


def test_load_models_registry_missing_file(tmp_path):
    assert load_models_registry(tmp_path) == {}


def test_load_models_registry_empty_file(tmp_path):
    (tmp_path / "models.yaml").write_text("")
    assert load_models_registry(tmp_path) == {}


def test_load_models_registry_non_mapping(tmp_path):
    (tmp_path / "models.yaml").write_text("- not a mapping\n")
    assert load_models_registry(tmp_path) == {}


def test_load_models_registry_happy(tmp_path):
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump(
            {
                "default": "qwen3-4b",
                "models": {
                    "qwen3-4b": {
                        "backend": "llama_cpp",
                        "options": {"path": "x.gguf", "n_gpu_layers": 99},
                    }
                },
            }
        )
    )
    data = load_models_registry(tmp_path)
    assert data["default"] == "qwen3-4b"
    assert "qwen3-4b" in data["models"]


# ── resolve_model_name ────────────────────────────────────────────────────────


def test_resolve_explicit_wins_over_skill_and_default():
    registry = {"default": "gemma3-1b"}
    assert resolve_model_name("qwen3-4b", "qwen3-1.7b", registry) == "qwen3-4b"


def test_resolve_skill_wins_over_default():
    registry = {"default": "gemma3-1b"}
    assert resolve_model_name(None, "qwen3-1.7b", registry) == "qwen3-1.7b"


def test_resolve_default_used_when_nothing_explicit():
    registry = {"default": "gemma3-1b"}
    assert resolve_model_name(None, None, registry) == "gemma3-1b"


def test_resolve_none_when_no_default_and_no_overrides():
    assert resolve_model_name(None, None, {}) is None


def test_resolve_default_must_be_string():
    """A non-string default (e.g. accidentally a list) is ignored."""
    assert resolve_model_name(None, None, {"default": ["x", "y"]}) is None


# ── build_orchestrator ────────────────────────────────────────────────────────


def _patch_orch():
    """Patch InferenceOrchestrator and return the mock class."""
    mock_cls = MagicMock()
    mock_cls.return_value = MagicMock(name="orch_instance")
    return patch("knaif.orchestrator.InferenceOrchestrator", mock_cls), mock_cls


def test_build_orchestrator_model_path_takes_precedence_over_registry():
    """--model-path bypasses the registry entirely."""
    registry = {"default": "qwen3-4b", "models": {"qwen3-4b": {"backend": "llama_cpp"}}}
    cm, mock_cls = _patch_orch()
    with cm:
        result = build_orchestrator(
            name="qwen3-4b",
            model_path="/abs/foo.gguf",
            registry=registry,
        )
    assert result is mock_cls.return_value
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["backend"] == "llama_cpp"
    assert kwargs["model_path"] == "/abs/foo.gguf"
    assert "model_config" not in kwargs


def test_build_orchestrator_llama_cpp_passes_options_as_model_config():
    registry = {
        "models": {
            "qwen3-4b": {
                "backend": "llama_cpp",
                "options": {"path": "models/q.gguf", "n_gpu_layers": 99},
            }
        }
    }
    cm, mock_cls = _patch_orch()
    with cm:
        build_orchestrator(name="qwen3-4b", registry=registry)
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["backend"] == "llama_cpp"
    assert kwargs["model_config"] == {"path": "models/q.gguf", "n_gpu_layers": 99}


def test_build_orchestrator_ollama_passes_model_name():
    registry = {
        "models": {
            "mistral": {"backend": "ollama", "model_name": "mistral"},
        }
    }
    cm, mock_cls = _patch_orch()
    with cm:
        build_orchestrator(name="mistral", registry=registry)
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["backend"] == "ollama"
    assert kwargs["model_name"] == "mistral"


def test_build_orchestrator_returns_none_when_nothing_resolved():
    """Empty registry + no explicit name + no skill recommendation → mock."""
    result = build_orchestrator(name=None, registry={})
    assert result is None


def test_build_orchestrator_uses_skill_recommended_when_no_explicit():
    registry = {"models": {"qwen3-1.7b": {"backend": "llama_cpp", "options": {"path": "x"}}}}
    cm, mock_cls = _patch_orch()
    with cm:
        build_orchestrator(skill_recommended="qwen3-1.7b", registry=registry)
    assert mock_cls.call_args.kwargs["model_config"] == {"path": "x"}


def test_build_orchestrator_uses_registry_default_when_nothing_else():
    registry = {
        "default": "qwen3-4b",
        "models": {"qwen3-4b": {"backend": "llama_cpp", "options": {"path": "x"}}},
    }
    cm, mock_cls = _patch_orch()
    with cm:
        build_orchestrator(registry=registry)
    assert mock_cls.call_args.kwargs["model_config"] == {"path": "x"}


def test_build_orchestrator_forwards_ollama_options():
    """The ollama branch must forward `options` like the llama_cpp branch does.

    It did not, so an Ollama entry's json_mode / thinking_enabled / max_tokens were
    parsed from models.yaml and then silently dropped — precisely the settings a
    reasoning model needs. Only the llama_cpp branch had a test, which is how the gap
    survived; this is its counterpart.
    """
    registry = {
        "default": "q",
        "models": {
            "q": {
                "backend": "ollama",
                "model": "qwen3:4b",
                "options": {"json_mode": False, "thinking_enabled": True, "max_tokens": 2048},
            }
        },
    }
    cm, mock_cls = _patch_orch()
    with cm:
        build_orchestrator(registry=registry)

    kwargs = mock_cls.call_args.kwargs
    assert kwargs["model_config"] == {
        "json_mode": False,
        "thinking_enabled": True,
        "max_tokens": 2048,
    }
    assert kwargs["model_name"] == "qwen3:4b"


def test_build_orchestrator_unknown_name_raises_key_error():
    registry = {"models": {"qwen3-4b": {"backend": "llama_cpp"}}}
    with pytest.raises(KeyError, match="not found in models.yaml"):
        build_orchestrator(name="typo", registry=registry)


def test_build_orchestrator_unknown_backend_raises_value_error():
    registry = {"models": {"x": {"backend": "unknown"}}}
    with pytest.raises(ValueError, match="unknown backend"):
        build_orchestrator(name="x", registry=registry)
