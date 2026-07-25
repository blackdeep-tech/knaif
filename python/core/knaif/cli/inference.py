"""Local backend resolution helpers for knaif.cli apps.

Provides convenience factories for Ollama and llama.cpp backends, each
falling back to None (mock backend) with a clear warning when unavailable.

The mock backend (None orchestrator) is appropriate for tests and offline
development; pass the returned orchestrator to App() or from_click() to
use a real local model.

Extension point
---------------
To wire a custom backend, implement the :class:`knaif.orchestrator.InferenceBackend`
protocol and pass the instance directly to ``App(orchestrator=...)``:

    class MyBackend:
        def infer(self, system, user, model_name=..., max_tokens=...) -> str: ...
        def infer_stream(self, system, user, model_name=..., max_tokens=...) -> Iterator[str]: ...

    app = nk.App([...], orchestrator=MyBackend())
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from knaif.orchestrator import InferenceOrchestrator, OllamaModelNotFoundError


def local_llama_cpp(
    model_path: str,
    *,
    n_ctx: int = 8192,
    n_gpu_layers: int = 99,
    n_threads: int = 8,
    json_mode: bool = False,
    thinking_enabled: bool = False,
    fallback_to_mock: bool = True,
    **kwargs: Any,
) -> InferenceOrchestrator | None:
    """Return a llama.cpp :class:`~knaif.orchestrator.InferenceOrchestrator`, or ``None``.

    Resolves *model_path* relative to the repository root (the nearest ancestor
    containing ``.git``). When *fallback_to_mock* is ``True`` (the default) and
    the model file is missing or llama-cpp-python is not installed, emits a
    :class:`UserWarning` and returns ``None`` so the caller falls back to mock
    inference.

    Parameters
    ----------
    model_path:
        Path to a GGUF model file — absolute, or relative to the repo root
        (e.g. ``"models/Qwen3-4B-Q4_K_M.gguf"``).
    n_ctx:
        Context window size in tokens.
    n_gpu_layers:
        Layers to offload to GPU; 99 = all.
    n_threads:
        CPU threads used during inference.
    json_mode:
        Enable GBNF JSON-constrained generation.  Set ``False`` for thinking
        models that emit ``<think>`` before the JSON object.
    thinking_enabled:
        When ``False``, appends ``/no_think`` to the system prompt (Qwen3
        convention).
    fallback_to_mock:
        When ``True``, a missing model file or import error emits a warning
        and returns ``None`` rather than raising.
    **kwargs:
        Additional keyword arguments forwarded to
        :class:`~knaif.orchestrator.InferenceOrchestrator`.
    """
    model_config = {
        "path": model_path,
        "n_ctx": n_ctx,
        "n_gpu_layers": n_gpu_layers,
        "n_threads": n_threads,
        "json_mode": json_mode,
        "thinking_enabled": thinking_enabled,
    }
    try:
        # The orchestrator warns about its own load failures, which is right for callers
        # who construct it directly. Here it would be the second report of one fact — this
        # helper re-reports the failure below, and only it knows the outcome that actually
        # matters to the caller (falling back to mock rather than raising).
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            orch = InferenceOrchestrator(backend="llama_cpp", model_config=model_config, **kwargs)
        if orch.llm is None:
            raise RuntimeError(f"Model failed to load: {model_path!r}")
        return orch
    except Exception as exc:  # noqa: BLE001
        if not fallback_to_mock:
            raise
        resolved = Path(model_path).expanduser()
        if not resolved.is_absolute():
            from knaif.orchestrator import InferenceOrchestrator as _IO

            resolved = _IO._find_root() / resolved
        if not resolved.exists():
            warnings.warn(
                f"Model file not found: {resolved}. "
                "Falling back to mock inference. "
                "Download a GGUF and update the path.",
                UserWarning,
                stacklevel=2,
            )
        else:
            warnings.warn(
                f"llama.cpp backend unavailable ({exc}). " "Falling back to mock inference.",
                UserWarning,
                stacklevel=2,
            )
        return None


def local_ollama(
    model: str = "qwen3:4b",
    *,
    url: str = "http://localhost:11434",
    json_mode: bool = False,
    thinking_enabled: bool = True,
    max_tokens: int = 2048,
    request_timeout: float = 120,
    fallback_to_mock: bool = True,
    **kwargs: Any,
) -> InferenceOrchestrator | None:
    """Return an Ollama :class:`~knaif.orchestrator.InferenceOrchestrator`, or ``None``.

    Tries to connect to Ollama at *url* with *model*. When *fallback_to_mock*
    is ``True`` (the default) and the connection fails, emits a
    :class:`UserWarning` and returns ``None`` so the caller falls back to
    mock inference. When *fallback_to_mock* is ``False``, the connection
    error propagates unchanged.

    Parameters
    ----------
    model:
        Ollama model name (e.g. ``"qwen3:4b"``). **Not** pulled automatically —
        Ollama models are pulled by ``ollama pull <name>``. (Automatic download
        applies to llama.cpp GGUFs via the native CLI's ``knaif models pull``,
        not here.)
    url:
        Ollama server base URL.
    json_mode:
        Constrain output to bare JSON (Ollama ``format: "json"``). **Off by
        default**, matching :func:`local_llama_cpp`. The constraint demands valid
        JSON from the first token, which a thinking-template model cannot satisfy
        while it emits a reasoning preamble — generation then never completes and
        the request times out. The default model here is a Qwen3, so enabling this
        by default would break the helper's own defaults. Turn it on only for a
        model you know emits bare JSON.
    thinking_enabled:
        Leave Ollama's own thinking behaviour alone. **On by default, which is what
        produces clean output** — the name reads backwards, so briefly: setting
        ``think: false`` does not stop a reasoning model reasoning, it only stops
        Ollama *separating* the reasoning, which then lands in ``message.content``
        and destroys the JSON. Left enabled, Ollama puts reasoning in
        ``message.thinking`` and knaif reads ``message.content``, which is clean.
        Set ``False`` only for a model with no thinking mode.
    max_tokens:
        Generation budget. Ollama counts reasoning against it, and a reasoning
        preamble can run well over a thousand tokens, so the agent-level default of
        256 would be spent before the answer begins. 2048 leaves room for both.
    request_timeout:
        Seconds allowed per request, covering cold model load plus generation.
    fallback_to_mock:
        When ``True``, an unreachable Ollama emits a warning and returns
        ``None`` rather than raising.
    **kwargs:
        Additional keyword arguments forwarded to
        :class:`~knaif.orchestrator.InferenceOrchestrator`. A ``model_config``
        passed here overrides the keyword defaults above.
    """
    model_config = {
        "json_mode": json_mode,
        "thinking_enabled": thinking_enabled,
        "max_tokens": max_tokens,
        "request_timeout": request_timeout,
        **(kwargs.pop("model_config", None) or {}),
    }
    try:
        return InferenceOrchestrator(
            backend="ollama",
            model_name=model,
            ollama_url=url,
            model_config=model_config,
            **kwargs,
        )
    except OllamaModelNotFoundError:
        # Server is up, the model just is not pulled. Reporting this as "unreachable"
        # sends the user to restart a server that is already running, while the real
        # one-line fix goes unread.
        if not fallback_to_mock:
            raise
        warnings.warn(
            f"Ollama is running, but the model {model!r} is not downloaded.\n"
            f"  Run: ollama pull {model}\n"
            "  Falling back to mock inference until then.",
            UserWarning,
            stacklevel=2,
        )
        return None
    except Exception:  # noqa: BLE001
        if not fallback_to_mock:
            raise
        # Deliberately not interpolating the exception: it is a urllib3
        # ConnectionError whose repr carries a nested exception chain and an object
        # memory address. That is a first-run message for someone who just ran
        # `pip install knaif`, and the address teaches them nothing — the URL and the
        # fix are already in the sentence. Pass fallback_to_mock=False to get the
        # original exception, chain and all.
        warnings.warn(
            f"Ollama at {url!r} is unreachable. "
            "Falling back to mock inference. "
            "Start it with 'ollama serve' and re-run to use a real model.",
            UserWarning,
            stacklevel=2,
        )
        return None
