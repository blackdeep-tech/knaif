"""FunctionStep — a Step that wraps a plain Python callable."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from knaif.handler_api import HandlerContext
from knaif.registry import ArgSchema, ToolDef
from knaif.tool import Step


def _coerce_value(value: Any, schema: ArgSchema) -> Any:
    """Coerce a string *value* to the type declared in *schema*.

    Only string → native conversions are attempted; non-string values and
    failed conversions pass through unchanged so the caller can decide.
    """
    if not isinstance(value, str):
        return value
    t = schema.type
    if t == "integer":
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    if t == "number":
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    if t == "boolean":
        if value.lower() in ("true", "1", "yes"):
            return True
        if value.lower() in ("false", "0", "no"):
            return False
    return value


class FunctionStep(Step):
    """A Step that dispatches to an ordinary Python callable.

    The callable receives keyword arguments matching the tool's arg names.
    If its signature includes a parameter named ``ctx``, a
    :class:`~knaif.handler_api.HandlerContext` is injected automatically.

    String values are coerced to the declared ``ArgSchema`` type before the
    call, so weak models that emit ``"42"`` for an integer field still work.
    """

    def __init__(self, name: str, fn: Callable, tool_def: ToolDef) -> None:
        self.name = name
        self._fn = fn
        self._tool_def = tool_def
        self._wants_ctx = "ctx" in inspect.signature(fn).parameters

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        coerced: dict[str, Any] = {}
        for key, value in args.items():
            schema = self._tool_def.arg_schemas.get(key)
            coerced[key] = _coerce_value(value, schema) if schema is not None else value

        if self._wants_ctx:
            coerced["ctx"] = ctx

        result = self._fn(**coerced)
        if isinstance(result, dict):
            return result
        return {"result": result}
