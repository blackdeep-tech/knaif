"""Runtime model registry.

Reads ``models.yaml`` at the repo root and resolves a model name to a fully
configured :class:`~knaif.orchestrator.InferenceOrchestrator`. Used by the CLI
and by library callers that don't want to hand-build a model_config.

Distinct from ``eval_backends.yaml``: that file enumerates every backend the
eval suite benchmarks side-by-side. This file picks the *one* model used at
runtime for a CLI invocation or library call.

Resolution precedence (highest first):

1. explicit ``model_path`` — raw GGUF, no model_config (legacy escape hatch)
2. explicit ``name`` — typically from ``--model NAME``
3. ``skill_recommended`` — the skill's ``recommended_model`` field
4. registry ``default``
5. ``None`` — caller falls back to mock inference
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .orchestrator import InferenceOrchestrator


REGISTRY_FILENAME = "models.yaml"


def _find_registry_root(start: Path | None = None) -> Path:
    """Walk up from *start* (or cwd) looking for models.yaml or .git."""
    current = (start or Path.cwd()).resolve()
    for p in [current, *current.parents]:
        if (p / REGISTRY_FILENAME).exists() or (p / ".git").exists():
            return p
    return current


def load_models_registry(root: Path | str | None = None) -> dict[str, Any]:
    """Return the parsed models.yaml, or {} if the file is missing."""
    base = Path(root).resolve() if root else _find_registry_root()
    path = base / REGISTRY_FILENAME
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


def resolve_model_name(
    explicit_name: str | None,
    skill_recommended: str | None,
    registry: dict[str, Any],
) -> str | None:
    """Apply runtime precedence and return a model name (or None for mock)."""
    if explicit_name:
        return explicit_name
    if skill_recommended:
        return skill_recommended
    default = registry.get("default")
    return default if isinstance(default, str) else None


def build_orchestrator(
    name: str | None = None,
    skill_recommended: str | None = None,
    model_path: str | None = None,
    ollama_url: str = "http://localhost:11434",
    root: Path | str | None = None,
    registry: dict[str, Any] | None = None,
    verbose: bool = False,
    backend: str = "auto",
) -> InferenceOrchestrator | None:
    """Resolve a model and return a configured orchestrator, or None for mock.

    Raises :class:`KeyError` when an explicit *name* doesn't exist in the
    registry, so a typo at the command line fails loudly instead of silently
    falling through to the default.

    *backend* makes the CLI ``--backend`` flag authoritative. ``"auto"`` (the
    default) resolves the backend from the model registry entry. An explicit
    ``"ollama"`` or ``"llama_cpp"`` (``"llama-cpp"`` accepted) must match the
    resolved entry's backend, otherwise a :class:`RuntimeError` is raised rather
    than silently running the registry's backend.
    """
    from .orchestrator import InferenceOrchestrator

    requested = backend.replace("-", "_") if backend else "auto"

    if model_path:
        if requested == "ollama":
            raise RuntimeError("--backend ollama is incompatible with --model-path (a GGUF file).")
        return InferenceOrchestrator(
            backend="llama_cpp",
            model_path=model_path,
            ollama_url=ollama_url,
            root=root,
            verbose=verbose,
        )

    if registry is None:
        registry = load_models_registry(root)

    resolved = resolve_model_name(name, skill_recommended, registry)
    if not resolved:
        return None

    models = registry.get("models") or {}
    entry = models.get(resolved)
    if entry is None:
        available = sorted(models.keys())
        raise KeyError(
            f"Model {resolved!r} not found in models.yaml. Available: {available or '(none)'}"
        )

    backend = entry.get("backend")
    if requested in ("ollama", "llama_cpp") and requested != backend:
        raise RuntimeError(
            f"--backend {requested.replace('_', '-')} was requested but model "
            f"{resolved!r} is a {backend!r} model. Use --backend auto, pick a "
            f"matching --model, or pass --model-path for llama.cpp."
        )
    if backend == "llama_cpp":
        return InferenceOrchestrator(
            backend="llama_cpp",
            model_config=entry.get("options") or {},
            ollama_url=ollama_url,
            root=root,
            verbose=verbose,
        )
    if backend == "ollama":
        return InferenceOrchestrator(
            backend="ollama",
            # `options` was dropped here while the llama_cpp branch above forwarded it,
            # so an Ollama entry's json_mode / thinking_enabled / max_tokens were parsed
            # from models.yaml and then silently ignored — the settings a reasoning model
            # needs are exactly the ones that went missing.
            model_config=entry.get("options") or {},
            model_name=entry.get("model_name") or entry.get("model"),
            ollama_url=entry.get("ollama_url", ollama_url),
            root=root,
            verbose=verbose,
        )
    raise ValueError(f"Model {resolved!r} has unknown backend {backend!r}")
