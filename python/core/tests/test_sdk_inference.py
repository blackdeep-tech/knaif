"""Tests for knaif.cli.inference — local backend resolution helpers."""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch

import pytest

from knaif.cli.inference import local_llama_cpp, local_ollama


class TestLocalLlamaCpp:
    def test_missing_model_reported_exactly_once(self, tmp_path):
        """Two layers know the model failed to load, and both used to say so — the
        orchestrator and this helper, in different words, about one fact. Only the helper
        knows the part that matters to the caller (falling back to mock), so it owns the
        message and the orchestrator's copy is suppressed."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = local_llama_cpp(str(tmp_path / "nonexistent.gguf"))

        assert result is None
        assert len(w) == 1
        assert "mock inference" in str(w[0].message)


class TestLocalOllama:
    def test_returns_none_on_unreachable_with_fallback(self):
        """When Ollama is unreachable and fallback_to_mock=True, returns None + warning."""
        with patch(
            "knaif.cli.inference.InferenceOrchestrator",
            side_effect=RuntimeError("connection refused"),
        ):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = local_ollama(fallback_to_mock=True)

        assert result is None
        assert len(w) == 1
        assert issubclass(w[0].category, UserWarning)
        assert "mock inference" in str(w[0].message).lower()

    def test_raises_on_unreachable_without_fallback(self):
        """When Ollama is unreachable and fallback_to_mock=False, propagates the error."""
        with patch(
            "knaif.cli.inference.InferenceOrchestrator",
            side_effect=RuntimeError("connection refused"),
        ):
            with pytest.raises(RuntimeError):
                local_ollama(fallback_to_mock=False)

    def test_returns_orchestrator_on_success(self):
        """When Ollama is reachable, returns the InferenceOrchestrator instance."""
        mock_orch = MagicMock()
        with patch("knaif.cli.inference.InferenceOrchestrator", return_value=mock_orch):
            result = local_ollama(model="qwen3:4b")

        assert result is mock_orch

    def test_forwards_model_and_url(self):
        """Model name and URL are forwarded to InferenceOrchestrator."""
        with patch("knaif.cli.inference.InferenceOrchestrator") as mock_cls:
            mock_cls.return_value = MagicMock()
            local_ollama(model="mistral", url="http://localhost:9999")

        mock_cls.assert_called_once_with(
            backend="ollama",
            model_name="mistral",
            ollama_url="http://localhost:9999",
            model_config={
                "json_mode": False,
                "thinking_enabled": True,
                "max_tokens": 2048,
                "request_timeout": 120,
            },
        )

    def test_json_mode_defaults_off(self):
        """`format: json` demands valid JSON from token 0, which a thinking-template
        model cannot satisfy while emitting a reasoning preamble — generation then
        never completes and the request times out. The default model here is a Qwen3,
        so defaulting this on would break the helper's own defaults."""
        with patch("knaif.cli.inference.InferenceOrchestrator") as mock_cls:
            mock_cls.return_value = MagicMock()
            local_ollama()

        assert mock_cls.call_args.kwargs["model_config"]["json_mode"] is False

    def test_thinking_left_enabled_so_ollama_separates_reasoning(self):
        """Counterintuitive but load-bearing: `think: false` does not stop a reasoning
        model reasoning, it only stops Ollama separating it — the reasoning then lands
        in `message.content` and destroys the JSON. Left enabled, reasoning goes to
        `message.thinking` and `content` stays clean."""
        with patch("knaif.cli.inference.InferenceOrchestrator") as mock_cls:
            mock_cls.return_value = MagicMock()
            local_ollama()

        assert mock_cls.call_args.kwargs["model_config"]["thinking_enabled"] is True

    def test_max_tokens_leaves_room_for_reasoning(self):
        """Ollama counts reasoning against the generation budget, and a preamble can
        exceed a thousand tokens, so the agent-level default of 256 would be spent
        before the answer starts."""
        with patch("knaif.cli.inference.InferenceOrchestrator") as mock_cls:
            mock_cls.return_value = MagicMock()
            local_ollama()

        assert mock_cls.call_args.kwargs["model_config"]["max_tokens"] >= 2048

    def test_explicit_model_config_overrides_defaults(self):
        with patch("knaif.cli.inference.InferenceOrchestrator") as mock_cls:
            mock_cls.return_value = MagicMock()
            local_ollama(model_config={"json_mode": True, "temperature": 0.5})

        cfg = mock_cls.call_args.kwargs["model_config"]
        assert cfg["json_mode"] is True
        assert cfg["temperature"] == 0.5
        assert cfg["request_timeout"] == 120

    def test_unreachable_warning_omits_the_exception_chain(self):
        """The underlying error is a urllib3 ConnectionError whose repr carries a nested
        exception chain and an object memory address. This is the first thing someone sees
        after `pip install knaif`, and none of that is actionable — the URL and the fix are
        the message."""
        noisy = RuntimeError(
            "HTTPConnectionPool(host='localhost', port=11434): Max retries exceeded "
            "(Caused by ConnectTimeoutError(<HTTPConnection object at 0x17d890a3e00>))"
        )
        with patch("knaif.cli.inference.InferenceOrchestrator", side_effect=noisy):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                local_ollama(fallback_to_mock=True)

        message = str(w[0].message)
        assert "0x" not in message
        assert "HTTPConnectionPool" not in message
        assert "ollama serve" in message

    def test_warning_mentions_ollama_url(self):
        """The fallback warning includes the attempted Ollama URL for actionability."""
        with patch(
            "knaif.cli.inference.InferenceOrchestrator",
            side_effect=RuntimeError("refused"),
        ):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                local_ollama(url="http://localhost:12345", fallback_to_mock=True)

        assert "12345" in str(w[0].message)
