"""Inference orchestrator: llama.cpp and Ollama backends."""

from __future__ import annotations

import ctypes
import os
import site
import sys
import sysconfig
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

_DLL_DIRECTORY_HANDLES: list[object] = []
_PRELOADED_DLLS: list[ctypes.CDLL] = []


def _resync_win32_console_handles() -> None:
    """Re-sync click's cached STD_OUTPUT_HANDLE after llama.cpp inference.

    llama.cpp's native GPU/CPU code can close or replace the Win32 console
    handle that click captured at import time. After inference, this reads the
    CRT's current fd-1 handle (which _is_console() already validated as a real
    console) and writes it back into click._winconsole.STDOUT_HANDLE so
    subsequent WriteConsoleW calls use a valid handle.

    No-op on non-Windows or if click._winconsole is not importable.
    """
    if sys.platform != "win32":
        return
    try:
        import msvcrt

        import click._winconsole as _wc

        _wc.STDOUT_HANDLE = msvcrt.get_osfhandle(1)
        _wc.STDERR_HANDLE = msvcrt.get_osfhandle(2)
    except Exception:  # noqa: BLE001
        pass


@runtime_checkable
class InferenceBackend(Protocol):
    """Structural interface for inference backends (→ Rust trait).

    Both llama.cpp and Ollama backends satisfy this contract.
    Callers that only need inference can type-hint against this Protocol
    rather than the concrete InferenceOrchestrator class.
    """

    def infer(
        self,
        system: str,
        user: str,
        model_name: str = ...,
        max_tokens: int = ...,
    ) -> str: ...

    def infer_stream(
        self,
        system: str,
        user: str,
        model_name: str = ...,
        max_tokens: int = ...,
    ) -> Iterator[str]: ...


class OllamaModelNotFoundError(RuntimeError):
    """Ollama is reachable, but the requested model has not been pulled.

    Distinct from a connection failure so callers can tell "start the server" from
    "pull the model" — the two need opposite fixes, and reporting the second as the
    first sends the user chasing a problem they do not have. Subclasses RuntimeError,
    so existing handlers keep working.
    """


class InferenceOrchestrator:
    """
    Orchestrates model inference for command planning.
    Supports llama.cpp (default) and Ollama backends.
    """

    def __init__(
        self,
        backend: str = "llama_cpp",
        model_config: dict | None = None,
        model_path: str | None = None,
        ollama_url: str = "http://localhost:11434",
        root: Path | str | None = None,
        model_name: str | None = None,
        verbose: bool = False,
    ) -> None:
        self.backend = backend
        self.ollama_url = ollama_url
        self.model_name = model_name
        self.model_config = model_config or {}
        # Defaults to False: a JSON constraint demands valid JSON from the first token,
        # which a reasoning model cannot satisfy while emitting its preamble — the request
        # then hangs until it times out rather than failing fast. Opting in is a per-model
        # decision, so the safe value is the default. Every committed backend sets this
        # explicitly, so nothing in-tree depends on the old default.
        self.json_mode: bool = bool((model_config or {}).get("json_mode", False))
        self.thinking_enabled: bool = bool((model_config or {}).get("thinking_enabled", False))
        # Per-request HTTP budget for the Ollama backend. A cold model load plus a full
        # tool-registry prompt can far exceed a few seconds, so this covers load + generate.
        self.request_timeout: float = float((model_config or {}).get("request_timeout", 120))
        self.llm: Any = None
        self._root = Path(root).resolve() if root else None
        self._verbose = verbose

        if backend == "llama_cpp":
            self._init_llama_cpp(model_config, model_path)
        elif backend == "ollama":
            self._check_ollama(ollama_url, model_name)
        else:
            raise ValueError(f"Unsupported backend: {backend!r}")

    # ── private helpers ───────────────────────────────────────────────────────

    def _resolve_model_path(self, raw: str) -> Path:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        root = self._root or self._find_root()
        return (root / candidate).resolve()

    @staticmethod
    def _find_root() -> Path:
        current = Path.cwd()
        for p in [current, *current.parents]:
            if (p / ".git").exists():
                return p
        return current

    @staticmethod
    def _candidate_site_packages() -> list[Path]:
        candidates: list[Path] = []
        for raw_path in [*site.getsitepackages(), site.getusersitepackages()]:
            if raw_path:
                candidates.append(Path(raw_path))
        purelib = sysconfig.get_paths().get("purelib")
        if purelib:
            candidates.append(Path(purelib))

        unique: list[Path] = []
        seen: set[Path] = set()
        for path in candidates:
            resolved = path.resolve()
            if resolved not in seen:
                unique.append(resolved)
                seen.add(resolved)
        return unique

    @classmethod
    def _prepare_llama_cpp_dlls(cls) -> None:
        if sys.platform != "win32":
            return

        for env_var in ("CUDA_PATH", "HIP_PATH"):
            env_path = os.environ.get(env_var)
            if env_path and not Path(env_path).exists():
                os.environ.pop(env_var, None)

        dll_dirs: list[Path] = []
        for site_packages in cls._candidate_site_packages():
            dll_dirs.extend(
                [
                    site_packages / "llama_cpp" / "lib",
                    site_packages / "nvidia" / "cuda_runtime" / "bin",
                    site_packages / "nvidia" / "cublas" / "bin",
                ]
            )

        for dll_dir in dll_dirs:
            if not dll_dir.exists():
                continue
            dll_dir_str = str(dll_dir)
            if dll_dir_str not in os.environ.get("PATH", ""):
                os.environ["PATH"] = dll_dir_str + os.pathsep + os.environ.get("PATH", "")
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(dll_dir_str))

        preload_order = [
            ("nvidia", "cuda_runtime", "bin", "cudart64_12.dll"),
            ("nvidia", "cublas", "bin", "cublas64_12.dll"),
            ("nvidia", "cublas", "bin", "cublasLt64_12.dll"),
            ("llama_cpp", "lib", "ggml-base.dll"),
            ("llama_cpp", "lib", "ggml-cpu.dll"),
            ("llama_cpp", "lib", "ggml-cuda.dll"),
            ("llama_cpp", "lib", "ggml.dll"),
            ("llama_cpp", "lib", "llama.dll"),
        ]
        for site_packages in cls._candidate_site_packages():
            for parts in preload_order:
                dll_path = site_packages.joinpath(*parts)
                if dll_path.exists():
                    _PRELOADED_DLLS.append(ctypes.CDLL(str(dll_path)))

    def _init_llama_cpp(self, model_config: dict | None, model_path: str | None) -> None:
        try:
            self._prepare_llama_cpp_dlls()
            from llama_cpp import Llama
        except ImportError:
            # Reachable from a plain `pip install knaif`, so the hint must not name repo
            # tooling (a `just` recipe, `uv add`) that an installed user does not have.
            # llama-cpp-python publishes no wheels to PyPI, so the plain install compiles
            # from source — point at the prebuilt index rather than let that surprise them.
            warnings.warn(
                "llama.cpp backend unavailable: llama-cpp-python is not installed.\n"
                "  Install it with:  pip install 'knaif[llama]'\n"
                "  It builds from source (needs CMake + a C++ toolchain). To skip the "
                "build, use the prebuilt wheels:\n"
                "    pip install llama-cpp-python --extra-index-url "
                "https://abetlen.github.io/llama-cpp-python/whl/cpu",
                UserWarning,
                stacklevel=3,
            )
            return

        actual_path: str | None = None
        n_gpu_layers = 10
        n_ctx = 4096
        n_threads = 8
        model_name = "custom model"

        verbose = self._verbose
        if model_config and model_config.get("path"):
            actual_path = model_config["path"]
            n_gpu_layers = model_config.get("n_gpu_layers", 10)
            n_ctx = model_config.get("n_ctx", 4096)
            n_threads = model_config.get("n_threads", 8)
            # n_batch is the LOGICAL batch; compute is chunked by n_ubatch (default 512), so
            # raising n_batch does NOT speed up prompt decode — measured on a 3938-token prompt:
            # 512 -> 1721/1772 tok/s vs 8192 -> 1737/1857 tok/s, i.e. noise, while costing a much
            # larger compute buffer. Left at the llama.cpp default deliberately. Overridable for
            # experiments. See docs/PERFORMANCE.md §3.
            n_batch = model_config.get("n_batch", 512)
            model_name = model_config.get("description", "custom model")
            verbose = model_config.get("verbose", self._verbose)
        elif model_path:
            actual_path = model_path

        if not actual_path:
            warnings.warn(
                "No model path provided. Set model_config['path'] or model_path.",
                UserWarning,
                stacklevel=3,
            )
            return

        full_path = self._resolve_model_path(actual_path)
        if not full_path.exists():
            warnings.warn(
                f"Model file not found: {full_path}\n"
                "  Download a GGUF model and update the path in your configuration.",
                UserWarning,
                stacklevel=3,
            )
            return

        if verbose:
            print(f"Loading: {model_name}")
            print(f"   Path: {full_path}")
        try:
            self.llm = Llama(
                model_path=str(full_path),
                n_ctx=n_ctx,
                n_batch=n_batch,
                n_threads=n_threads,
                n_gpu_layers=n_gpu_layers,
                verbose=verbose,
            )
            if verbose:
                print(
                    f"Model loaded (GPU layers: {n_gpu_layers}, n_ctx: {n_ctx}, n_batch: {n_batch})"
                )
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"Error loading llama.cpp model: {exc}",
                UserWarning,
                stacklevel=3,
            )

    def _check_ollama(self, ollama_url: str, model_name: str | None = None) -> None:
        import requests

        try:
            resp = requests.get(f"{ollama_url}/api/tags", timeout=2)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {ollama_url}. Is 'ollama serve' running?\n  {exc}"
            ) from exc

        if resp.status_code != 200:
            raise RuntimeError(f"Ollama not responding at {ollama_url} (HTTP {resp.status_code})")
        if self._verbose:
            print(f"Ollama available at {ollama_url}")

        if not model_name:
            return

        available = {m["name"] for m in resp.json().get("models", [])}
        # Ollama stores untagged names as "name:latest"
        normalized = model_name if ":" in model_name else f"{model_name}:latest"
        if model_name not in available and normalized not in available:
            raise OllamaModelNotFoundError(
                f"Ollama model {model_name!r} is not downloaded.\n  Run: ollama pull {model_name}"
            )

    # ── public API ────────────────────────────────────────────────────────────

    def _apply_thinking(self, system: str) -> str:
        # Qwen3 honors a "/no_think" suffix in the system prompt to skip the
        # <think> block. No-op on other model families. llama.cpp has no native
        # thinking toggle in chat completions, unlike Ollama 0.5+.
        if not self.thinking_enabled:
            return f"{system}\n\n/no_think" if system else "/no_think"
        return system

    def infer(
        self,
        system: str,
        user: str,
        model_name: str = "mistral",
        max_tokens: int = 256,
    ) -> str:
        """Run chat completion and return raw assistant content."""
        if self.backend == "llama_cpp":
            if not self.llm:
                raise RuntimeError(
                    "llama.cpp backend not initialized. "
                    "Provide a valid model_config['path'] or model_path."
                )
            kwargs: dict[str, Any] = {}
            if self.json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            _max_tokens = int(self.model_config.get("max_tokens", max_tokens))
            response = cast(
                dict[str, Any],
                self.llm.create_chat_completion(
                    messages=[
                        {"role": "system", "content": self._apply_thinking(system)},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=_max_tokens,
                    temperature=0.0,
                    **kwargs,
                ),
            )
            _resync_win32_console_handles()
            return str(response["choices"][0]["message"]["content"])

        if self.backend == "ollama":
            import requests

            _num_predict = int(self.model_config.get("max_tokens", max_tokens))
            _temperature = float(self.model_config.get("temperature", 0.0))
            payload: dict[str, Any] = {
                "model": self.model_name or model_name,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "options": {"temperature": _temperature, "num_predict": _num_predict},
            }
            # format:"json" constrains output to bare JSON from token 0, which
            # conflicts with thinking-mode models that emit <think>...</think>
            # before the JSON object.  Only enable it when thinking is off.
            if self.json_mode and not self.thinking_enabled:
                payload["format"] = "json"
            # Ollama 0.5+ native thinking control — more reliable than /no_think suffix.
            if not self.thinking_enabled:
                payload["think"] = False
            try:
                resp = requests.post(
                    f"{self.ollama_url}/api/chat", json=payload, timeout=self.request_timeout
                )
            except requests.Timeout as exc:
                # Surfaced as a bare ReadTimeout traceback before. The usual cause is a
                # JSON-constrained request to a thinking-template model: `format: json`
                # demands valid JSON from token 0 while the template still emits a
                # reasoning preamble, so generation never satisfies the grammar.
                hint = (
                    "  The model may be JSON-constrained while emitting a reasoning "
                    "preamble — retry with json_mode=False.\n"
                    if payload.get("format") == "json"
                    else "  Raise request_timeout, or try a smaller model.\n"
                )
                raise RuntimeError(
                    f"Ollama did not respond within {self.request_timeout:g}s "
                    f"(model {payload['model']!r}).\n{hint}"
                ) from exc
            if resp.status_code == 200:
                return str(resp.json()["message"]["content"])
            raise RuntimeError(f"Ollama error: {resp.status_code} {resp.text}")

        raise ValueError(f"Unknown backend: {self.backend!r}")

    def infer_stream(
        self,
        system: str,
        user: str,
        model_name: str = "mistral",
        max_tokens: int = 256,
    ) -> Iterator[str]:
        """Stream chat completion, yielding raw text chunks as they arrive."""
        if self.backend == "llama_cpp":
            if not self.llm:
                raise RuntimeError(
                    "llama.cpp backend not initialized. "
                    "Provide a valid model_config['path'] or model_path."
                )
            _max_tokens = int(self.model_config.get("max_tokens", max_tokens))
            stream = self.llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": self._apply_thinking(system)},
                    {"role": "user", "content": user},
                ],
                max_tokens=_max_tokens,
                temperature=0.0,
                stream=True,
            )
            for chunk in stream:
                choices = chunk.get("choices", [])
                if choices:
                    content = choices[0].get("delta", {}).get("content") or ""
                    if content:
                        yield content
            _resync_win32_console_handles()
            return

        if self.backend == "ollama":
            import json

            import requests

            _num_predict = int(self.model_config.get("max_tokens", max_tokens))
            _temperature = float(self.model_config.get("temperature", 0.0))
            payload = {
                "model": self.model_name or model_name,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": True,
                "options": {"temperature": _temperature, "num_predict": _num_predict},
            }
            try:
                resp = requests.post(
                    f"{self.ollama_url}/api/chat",
                    json=payload,
                    timeout=self.request_timeout,
                    stream=True,
                )
            except requests.Timeout as exc:
                raise RuntimeError(
                    f"Ollama did not respond within {self.request_timeout:g}s "
                    f"(model {payload['model']!r}). Raise request_timeout, or try a "
                    "smaller model."
                ) from exc
            if resp.status_code != 200:
                raise RuntimeError(f"Ollama error: {resp.status_code} {resp.text}")
            for line in resp.iter_lines():
                if line:
                    data = json.loads(line)
                    content = (data.get("message") or {}).get("content") or ""
                    if content:
                        yield content
                    if data.get("done"):
                        break
            return

        raise ValueError(f"Unknown backend: {self.backend!r}")
