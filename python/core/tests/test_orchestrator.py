"""Tests for knaif.orchestrator.InferenceOrchestrator."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from knaif.orchestrator import InferenceOrchestrator, _resync_win32_console_handles

# ── helpers ────────────────────────────────────────────────────────────────────


def _ollama_orch(tmp_path=None) -> InferenceOrchestrator:
    """Return an InferenceOrchestrator with a mocked Ollama connection."""
    import requests as req_mod

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    kwargs = {"root": tmp_path} if tmp_path else {}
    with patch.object(req_mod, "get", return_value=mock_resp):
        return InferenceOrchestrator(backend="ollama", **kwargs)


# ── __init__ ──────────────────────────────────────────────────────────────────


def test_init_invalid_backend():
    with pytest.raises(ValueError, match="Unsupported backend"):
        InferenceOrchestrator(backend="unknown")


def test_init_llama_cpp_no_model_config(tmp_path):
    """No model_config → llm stays None, no crash."""
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={})
    assert orch.llm is None
    assert orch.backend == "llama_cpp"


def test_init_llama_cpp_path_not_found(tmp_path):
    """Model file doesn't exist → llm stays None."""
    orch = InferenceOrchestrator(
        backend="llama_cpp",
        model_config={"path": "nonexistent_model.gguf"},
        root=tmp_path,
    )
    assert orch.llm is None


def test_init_llama_cpp_failure_warns_and_leaves_stdout_clean(tmp_path, capsys, recwarn):
    """Load failures are warnings, not prints.

    knaif is embedded in other people's CLIs, so anything this writes to stdout lands in
    the middle of *their* output — next to the JSON they are piping. The diagnostic still
    has to reach a direct caller, so it goes through the warnings machinery, which writes
    to stderr and is filterable.
    """
    # llama-cpp-python lives in the `llama` extra, not `dev`, so a fresh clone does not have
    # it — without this stub the import branch short-circuits and the missing-file branch
    # under test is never reached. Same patching pattern as test_init_llama_cpp_success.
    with patch.dict("sys.modules", {"llama_cpp": MagicMock()}):
        orch = InferenceOrchestrator(
            backend="llama_cpp",
            model_config={"path": "nonexistent_model.gguf"},
            root=tmp_path,
        )

    assert orch.llm is None
    assert capsys.readouterr().out == ""
    assert any("Model file not found" in str(w.message) for w in recwarn)


def test_init_llama_cpp_success(tmp_path):
    """Model file exists and Llama loads → llm is set."""
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"fake model data")

    mock_llm_instance = MagicMock()
    mock_llama_cls = MagicMock(return_value=mock_llm_instance)
    mock_llama_module = MagicMock()
    mock_llama_module.Llama = mock_llama_cls

    with patch.dict("sys.modules", {"llama_cpp": mock_llama_module}):
        orch = InferenceOrchestrator(
            backend="llama_cpp",
            model_config={"path": str(model_file), "n_gpu_layers": 0, "description": "test"},
        )

    assert orch.llm is mock_llm_instance


def test_init_llama_cpp_verbose_default_false(tmp_path):
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"fake")

    mock_llama_cls = MagicMock()
    mock_llama_module = MagicMock()
    mock_llama_module.Llama = mock_llama_cls

    with patch.dict("sys.modules", {"llama_cpp": mock_llama_module}):
        InferenceOrchestrator(
            backend="llama_cpp",
            model_config={"path": str(model_file)},
        )

    assert mock_llama_cls.call_args.kwargs["verbose"] is False


def test_init_llama_cpp_verbose_true_from_config(tmp_path):
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"fake")

    mock_llama_cls = MagicMock()
    mock_llama_module = MagicMock()
    mock_llama_module.Llama = mock_llama_cls

    with patch.dict("sys.modules", {"llama_cpp": mock_llama_module}):
        InferenceOrchestrator(
            backend="llama_cpp",
            model_config={"path": str(model_file), "verbose": True},
        )

    assert mock_llama_cls.call_args.kwargs["verbose"] is True


def test_init_llama_cpp_load_exception(tmp_path):
    """Exception during Llama() init → llm stays None."""
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"fake")

    mock_llama_module = MagicMock()
    mock_llama_module.Llama = MagicMock(side_effect=RuntimeError("bad model"))

    with patch.dict("sys.modules", {"llama_cpp": mock_llama_module}):
        orch = InferenceOrchestrator(
            backend="llama_cpp",
            model_config={"path": str(model_file)},
        )

    assert orch.llm is None


def test_init_llama_cpp_model_path_kwarg(tmp_path):
    """model_path kwarg (not model_config) is also accepted."""
    orch = InferenceOrchestrator(
        backend="llama_cpp",
        model_path="nonexistent.gguf",
        root=tmp_path,
    )
    assert orch.llm is None


def test_init_ollama_available():
    import requests as req_mod

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch.object(req_mod, "get", return_value=mock_resp):
        orch = InferenceOrchestrator(backend="ollama")
    assert orch.backend == "ollama"


def test_init_ollama_bad_status():
    import requests as req_mod

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch.object(req_mod, "get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="Ollama not responding"):
            InferenceOrchestrator(backend="ollama")


def test_init_ollama_connection_error():
    import requests as req_mod

    with patch.object(req_mod, "get", side_effect=ConnectionError("refused")):
        with pytest.raises(RuntimeError, match="Cannot reach Ollama"):
            InferenceOrchestrator(backend="ollama")


def test_init_json_mode_defaults_off():
    """Off by default: a JSON constraint demands valid JSON from the first token, which
    a reasoning model cannot satisfy while emitting its preamble — the request then hangs
    until it times out rather than failing fast. Opting in is a per-model decision, so the
    safe value is the default. Every committed backend sets this explicitly."""
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={})
    assert orch.json_mode is False


def test_init_json_mode_enabled():
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={"json_mode": True})
    assert orch.json_mode is True


def test_init_json_mode_disabled():
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={"json_mode": False})
    assert orch.json_mode is False


# ── _resolve_model_path ────────────────────────────────────────────────────────


def test_resolve_model_path_absolute(tmp_path):
    orch = _ollama_orch(tmp_path)
    abs_path = tmp_path / "model.gguf"
    result = orch._resolve_model_path(str(abs_path))
    assert result == abs_path.resolve()


def test_resolve_model_path_relative(tmp_path):
    orch = _ollama_orch(tmp_path)
    result = orch._resolve_model_path("models/model.gguf")
    assert result == (tmp_path / "models" / "model.gguf").resolve()


def test_resolve_model_path_no_root_uses_find_root():
    """Without an explicit root, falls back to _find_root."""
    import requests as req_mod

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch.object(req_mod, "get", return_value=mock_resp):
        orch = InferenceOrchestrator(backend="ollama")  # no root kwarg

    result = orch._resolve_model_path("relative/model.gguf")
    assert isinstance(result, Path)
    assert result.is_absolute()


# ── _find_root ────────────────────────────────────────────────────────────────


def test_find_root_returns_path():
    root = InferenceOrchestrator._find_root()
    assert isinstance(root, Path)
    assert root.is_absolute()


def test_find_root_finds_git_ancestor():
    root = InferenceOrchestrator._find_root()
    # We're inside a git repo; the root should have a .git dir or be cwd.
    assert (root / ".git").exists() or root == Path.cwd()


# ── _candidate_site_packages ──────────────────────────────────────────────────


def test_candidate_site_packages_returns_unique_paths():
    paths = InferenceOrchestrator._candidate_site_packages()
    assert isinstance(paths, list)
    assert len(paths) == len(set(paths)), "Paths should be deduplicated"


def test_candidate_site_packages_all_absolute():
    paths = InferenceOrchestrator._candidate_site_packages()
    for p in paths:
        assert p.is_absolute(), f"Expected absolute path, got {p}"


# ── infer ─────────────────────────────────────────────────────────────────────


def test_infer_llama_cpp_uninitialized():
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={})
    with pytest.raises(RuntimeError, match="not initialized"):
        orch.infer("system", "user")


def test_infer_llama_cpp_with_mock_llm():
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={})
    mock_llm = MagicMock()
    mock_llm.create_chat_completion.return_value = {
        "choices": [{"message": {"content": '{"plan":[]}'}}]
    }
    orch.llm = mock_llm

    result = orch.infer("sys", "usr", max_tokens=64)

    assert result == '{"plan":[]}'
    mock_llm.create_chat_completion.assert_called_once()


def test_infer_llama_cpp_appends_no_think_when_thinking_disabled():
    orch = InferenceOrchestrator(
        backend="llama_cpp", model_config={"thinking_enabled": False, "json_mode": False}
    )
    mock_llm = MagicMock()
    mock_llm.create_chat_completion.return_value = {"choices": [{"message": {"content": "ok"}}]}
    orch.llm = mock_llm

    orch.infer("you are a planner", "do thing")

    messages = mock_llm.create_chat_completion.call_args[1]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].endswith("/no_think")
    assert "you are a planner" in messages[0]["content"]


def test_infer_llama_cpp_no_suffix_when_thinking_enabled():
    orch = InferenceOrchestrator(
        backend="llama_cpp", model_config={"thinking_enabled": True, "json_mode": False}
    )
    mock_llm = MagicMock()
    mock_llm.create_chat_completion.return_value = {"choices": [{"message": {"content": "ok"}}]}
    orch.llm = mock_llm

    orch.infer("you are a planner", "do thing")

    messages = mock_llm.create_chat_completion.call_args[1]["messages"]
    assert messages[0]["content"] == "you are a planner"


def test_infer_llama_cpp_json_mode_off():
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={"json_mode": False})
    mock_llm = MagicMock()
    mock_llm.create_chat_completion.return_value = {"choices": [{"message": {"content": "hello"}}]}
    orch.llm = mock_llm

    result = orch.infer("sys", "usr")

    call_kwargs = mock_llm.create_chat_completion.call_args[1]
    assert "response_format" not in call_kwargs
    assert result == "hello"


def test_infer_ollama_success():
    import requests as req_mod

    orch = _ollama_orch()

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json.return_value = {"message": {"content": '{"plan":[]}'}}

    with patch.object(req_mod, "post", return_value=mock_post_resp):
        result = orch.infer("sys", "usr")

    assert result == '{"plan":[]}'


def test_infer_ollama_error():
    import requests as req_mod

    orch = _ollama_orch()

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 500
    mock_post_resp.text = "Internal Server Error"

    with patch.object(req_mod, "post", return_value=mock_post_resp):
        with pytest.raises(RuntimeError, match="Ollama error"):
            orch.infer("sys", "usr")


def test_infer_unknown_backend():
    orch = _ollama_orch()
    orch.backend = "bogus"
    with pytest.raises(ValueError, match="Unknown backend"):
        orch.infer("sys", "usr")


# ── infer_stream ──────────────────────────────────────────────────────────────


def test_infer_stream_llama_cpp_uninitialized():
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={})
    with pytest.raises(RuntimeError, match="not initialized"):
        list(orch.infer_stream("sys", "usr"))


def test_infer_stream_llama_cpp_yields_chunks():
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={})
    mock_llm = MagicMock()
    mock_llm.create_chat_completion.return_value = [
        {"choices": [{"delta": {"content": "CHUNK_A"}}]},
        {"choices": [{"delta": {"content": "CHUNK_B"}}]},
        {"choices": [{"delta": {}}]},
    ]
    orch.llm = mock_llm

    chunks = list(orch.infer_stream("sys", "usr"))

    assert "CHUNK_A" in chunks
    assert "CHUNK_B" in chunks


def test_infer_stream_llama_cpp_json_mode_off():
    orch = InferenceOrchestrator(backend="llama_cpp", model_config={"json_mode": False})
    mock_llm = MagicMock()
    mock_llm.create_chat_completion.return_value = [
        {"choices": [{"delta": {"content": "hi"}}]},
    ]
    orch.llm = mock_llm

    chunks = list(orch.infer_stream("sys", "usr"))

    call_kwargs = mock_llm.create_chat_completion.call_args[1]
    assert "response_format" not in call_kwargs
    assert "hi" in chunks


def test_infer_stream_ollama_success():
    import requests as req_mod

    orch = _ollama_orch()

    lines = [
        json.dumps({"message": {"content": "CHUNK_A"}, "done": False}).encode(),
        json.dumps({"message": {"content": "CHUNK_B"}, "done": True}).encode(),
    ]
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.iter_lines.return_value = iter(lines)

    with patch.object(req_mod, "post", return_value=mock_post_resp):
        chunks = list(orch.infer_stream("sys", "usr"))

    assert "CHUNK_A" in chunks
    assert "CHUNK_B" in chunks


def test_infer_stream_ollama_empty_content_skipped():
    import requests as req_mod

    orch = _ollama_orch()

    lines = [
        json.dumps({"message": {"content": ""}, "done": False}).encode(),
        json.dumps({"message": {}, "done": False}).encode(),
        json.dumps({"message": {"content": "ok"}, "done": True}).encode(),
    ]
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.iter_lines.return_value = iter(lines)

    with patch.object(req_mod, "post", return_value=mock_post_resp):
        chunks = list(orch.infer_stream("sys", "usr"))

    assert chunks == ["ok"]


def test_infer_stream_ollama_error():
    import requests as req_mod

    orch = _ollama_orch()

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 500
    mock_post_resp.text = "error"

    with patch.object(req_mod, "post", return_value=mock_post_resp):
        with pytest.raises(RuntimeError, match="Ollama error"):
            list(orch.infer_stream("sys", "usr"))


def test_infer_stream_unknown_backend():
    orch = _ollama_orch()
    orch.backend = "bogus"
    with pytest.raises(ValueError, match="Unknown backend"):
        list(orch.infer_stream("sys", "usr"))


# ── _resync_win32_console_handles ───────────────────────────────────────────────
# Regression guard: llama.cpp's native code can replace the Win32 console handle
# that click cached at import time, after which click.echo() fails with
# "OSError: Windows error: 6" (ERROR_INVALID_HANDLE). infer()/infer_stream() call
# _resync_win32_console_handles() to restore click's cached handle from the CRT.


def test_resync_console_handles_is_noop_off_windows():
    """On non-Windows the helper must do nothing and never raise."""
    if sys.platform == "win32":
        pytest.skip("Windows-specific no-op path")
    _resync_win32_console_handles()  # must not raise


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console handles only")
def test_resync_console_handles_restores_stale_handle():
    """A stale STDOUT_HANDLE is restored to the CRT's current fd-1 handle."""
    import msvcrt

    import click._winconsole as wc

    saved_out, saved_err = wc.STDOUT_HANDLE, wc.STDERR_HANDLE
    try:
        wc.STDOUT_HANDLE = -1  # simulate the handle llama.cpp invalidated
        wc.STDERR_HANDLE = -1

        _resync_win32_console_handles()

        assert wc.STDOUT_HANDLE == msvcrt.get_osfhandle(1)
        assert wc.STDERR_HANDLE == msvcrt.get_osfhandle(2)
    finally:
        wc.STDOUT_HANDLE, wc.STDERR_HANDLE = saved_out, saved_err
