"""Tests for knaif.cli.function_step.FunctionStep."""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.cli.function_step import FunctionStep
from knaif.handler_api import HandlerContext
from knaif.registry import ArgSchema, ToolDef


def _ctx(tmp_path: Path) -> HandlerContext:
    return HandlerContext(
        root=tmp_path,
        dry_run=True,
        confirmed=False,
        skill_dir=tmp_path,
    )


def _td(name: str = "my_tool", required_args: tuple = (), **schemas: ArgSchema) -> ToolDef:
    return ToolDef(
        name=name,
        description="test",
        required_args=required_args,
        arg_schemas=schemas,
    )


# ── basic dispatch ─────────────────────────────────────────────────────────────


def test_function_step_calls_fn(tmp_path):
    called = {}

    def fn(x: str) -> dict:
        called["x"] = x
        return {"out": x}

    step = FunctionStep("my_tool", fn, _td(required_args=("x",)))
    result = step.handle({"x": "hello"}, _ctx(tmp_path))
    assert called["x"] == "hello"
    assert result == {"out": "hello"}


def test_function_step_name():
    step = FunctionStep("greet", lambda: {}, _td())
    assert step.name == "greet"


def test_function_step_non_dict_result_wrapped(tmp_path):
    step = FunctionStep("greet", lambda name: f"hello {name}", _td(required_args=("name",)))
    result = step.handle({"name": "world"}, _ctx(tmp_path))
    assert result == {"result": "hello world"}


def test_function_step_none_result_wrapped(tmp_path):
    step = FunctionStep("noop", lambda: None, _td())
    result = step.handle({}, _ctx(tmp_path))
    assert result == {"result": None}


# ── ctx injection ─────────────────────────────────────────────────────────────


def test_function_step_injects_ctx_when_requested(tmp_path):
    received = {}

    def fn(x: str, ctx: HandlerContext) -> dict:
        received["ctx"] = ctx
        return {"ok": True}

    step = FunctionStep("t", fn, _td(required_args=("x",)))
    ctx = _ctx(tmp_path)
    step.handle({"x": "v"}, ctx)
    assert received["ctx"] is ctx


def test_function_step_no_ctx_when_not_in_signature(tmp_path):
    def fn(x: str) -> dict:
        return {"x": x}

    step = FunctionStep("t", fn, _td(required_args=("x",)))
    # Must not raise TypeError about unexpected 'ctx' keyword argument
    result = step.handle({"x": "v"}, _ctx(tmp_path))
    assert result == {"x": "v"}


# ── type coercion ─────────────────────────────────────────────────────────────


def test_function_step_coerces_string_to_integer(tmp_path):
    received = {}

    def fn(n: int) -> dict:
        received["n"] = n
        return {}

    step = FunctionStep("t", fn, _td(required_args=("n",), n=ArgSchema(type="integer")))
    step.handle({"n": "42"}, _ctx(tmp_path))
    assert received["n"] == 42
    assert isinstance(received["n"], int)


def test_function_step_coerces_string_to_number(tmp_path):
    received = {}

    def fn(ratio: float) -> dict:
        received["ratio"] = ratio
        return {}

    step = FunctionStep("t", fn, _td(required_args=("ratio",), ratio=ArgSchema(type="number")))
    step.handle({"ratio": "0.5"}, _ctx(tmp_path))
    assert received["ratio"] == pytest.approx(0.5)


def test_function_step_coerces_string_to_boolean_true(tmp_path):
    received = {}

    def fn(flag: bool) -> dict:
        received["flag"] = flag
        return {}

    step = FunctionStep("t", fn, _td(required_args=("flag",), flag=ArgSchema(type="boolean")))
    step.handle({"flag": "true"}, _ctx(tmp_path))
    assert received["flag"] is True


def test_function_step_coerces_string_to_boolean_false(tmp_path):
    received = {}

    def fn(flag: bool) -> dict:
        received["flag"] = flag
        return {}

    step = FunctionStep("t", fn, _td(required_args=("flag",), flag=ArgSchema(type="boolean")))
    step.handle({"flag": "false"}, _ctx(tmp_path))
    assert received["flag"] is False


def test_function_step_no_coerce_already_typed(tmp_path):
    received = {}

    def fn(n: int) -> dict:
        received["n"] = n
        return {}

    step = FunctionStep("t", fn, _td(required_args=("n",), n=ArgSchema(type="integer")))
    step.handle({"n": 7}, _ctx(tmp_path))
    assert received["n"] == 7


def test_function_step_coerce_failure_passes_original(tmp_path):
    """If coercion fails, the original value is passed through unchanged."""
    received = {}

    def fn(n) -> dict:
        received["n"] = n
        return {}

    step = FunctionStep("t", fn, _td(required_args=("n",), n=ArgSchema(type="integer")))
    step.handle({"n": "not_a_number"}, _ctx(tmp_path))
    assert received["n"] == "not_a_number"
