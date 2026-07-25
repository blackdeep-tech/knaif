"""build_registry: turn decorated functions into a (registry, tool_map) pair."""

from __future__ import annotations

import inspect
import types
import warnings
from collections.abc import Callable
from typing import Annotated, Any, Union, get_args, get_origin, get_type_hints

from knaif.cli.decorators import Arg, Opt
from knaif.cli.function_step import FunctionStep
from knaif.registry import ArgSchema, ToolDef

# Python built-in type → ArgSchema.type string
_PY_TYPE_TO_SCHEMA = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
}


def _unwrap(annotation: Any) -> Any:
    """Strip ``Annotated`` and ``Optional``/``X | None`` layers down to the bare type.

    Both wrappers occur, in either order, and the order is version-dependent. An optional
    argument is normally typed ``X | None``, and without unwrapping it the type lookup
    misses the union and the argument silently loses its schema — skipping validation.
    (Declaring a bare ``X`` with a ``None`` default is what type checkers reject as implicit
    Optional, so there is no spelling that avoids this.)

    On 3.10 there is a second layer: ``get_type_hints`` re-adds ``Optional[...]`` around any
    parameter whose default is ``None`` — legacy implicit-Optional behaviour, dropped in
    3.11. That yields ``Optional[Annotated[X | None, meta]]``, putting ``Union`` *outside*
    ``Annotated``, so unwrapping either one first is wrong on one version or the other.
    Peeling in a loop is order-independent and handles every arrangement.

    A genuine multi-type union (``int | str``) is left intact and derives no schema.
    """
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation = get_args(annotation)[0]
        elif origin in (Union, types.UnionType):
            non_none = [a for a in get_args(annotation) if a is not type(None)]
            if len(non_none) != 1:
                return annotation
            annotation = non_none[0]
        else:
            return annotation


def _schema_from_annotation(annotation: Any, meta: Arg | Opt | None) -> ArgSchema | None:
    """Derive an ArgSchema from a Python type annotation and optional Arg/Opt metadata."""
    annotation = _unwrap(annotation)

    if meta is not None and meta.choices:
        return ArgSchema(
            type="enum",
            enum=tuple(meta.choices),
            help=meta.help or None,
        )

    schema_type = _PY_TYPE_TO_SCHEMA.get(annotation)
    if schema_type is None:
        return None

    return ArgSchema(
        type=schema_type,
        help=(meta.help or None) if meta else None,
    )


def _extract_meta(annotation: Any) -> Arg | Opt | None:
    """Return the first Arg or Opt instance found, at any wrapper depth.

    Must peel the same layers as :func:`_unwrap`: on 3.10 the metadata sits inside
    ``Optional[Annotated[...]]``, so looking only at the outermost layer finds nothing and
    the parameter silently loses its help text, choices and default.
    """
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            args = get_args(annotation)
            for arg in args[1:]:
                if isinstance(arg, (Arg, Opt)):
                    return arg
            annotation = args[0]
        elif origin in (Union, types.UnionType):
            non_none = [a for a in get_args(annotation) if a is not type(None)]
            if len(non_none) != 1:
                return None
            annotation = non_none[0]
        else:
            return None


def build_registry(
    fns: list[Callable],
) -> tuple[dict[str, ToolDef], dict[str, FunctionStep]]:
    """Build a registry and tool_map from a list of @command-decorated functions.

    Each function must be decorated with ``@nk.command``. The function name
    becomes the tool name. Type annotations drive required/optional arg
    classification and ArgSchema generation.

    Returns ``(registry, tool_map)`` ready for
    ``CommandAgent.from_registry(registry, tool_map)``.
    """
    registry: dict[str, ToolDef] = {}
    tool_map: dict[str, FunctionStep] = {}

    for fn in fns:
        if not hasattr(fn, "_nk_command"):
            raise ValueError(
                f"Function {fn.__name__!r} is not decorated with @nk.command. "
                "All commands passed to build_registry must be decorated."
            )

        meta_map = fn._nk_command
        tool_name = fn.__name__

        sig = inspect.signature(fn)
        try:
            hints = get_type_hints(fn, include_extras=True)
        except Exception as exc:  # noqa: BLE001
            # Falling back to {} costs every parameter its ArgSchema, so the command keeps
            # working but stops validating its arguments. That is too quiet a failure for a
            # safety-relevant feature to make silently — it hid a 3.10-only TypeError for
            # the whole life of the SDK. Degrade, but say so.
            warnings.warn(
                f"Could not resolve type hints for {tool_name!r} ({exc}); its arguments "
                "will not be schema-validated.",
                UserWarning,
                stacklevel=2,
            )
            hints = {}

        required_args: list[str] = []
        optional_args: list[str] = []
        defaults: dict[str, Any] = {}
        arg_schemas: dict[str, ArgSchema] = {}

        for param_name, param in sig.parameters.items():
            if param_name == "ctx":
                continue  # ctx is injected by FunctionStep, not a tool arg

            annotation = hints.get(param_name, inspect.Parameter.empty)
            nk_meta = _extract_meta(annotation)

            has_default = param.default is not inspect.Parameter.empty

            # Classify as required or optional
            if isinstance(nk_meta, Opt) or has_default:
                optional_args.append(param_name)
                default_val = nk_meta.default if isinstance(nk_meta, Opt) else param.default
                if default_val is not None:
                    defaults[param_name] = default_val
            else:
                required_args.append(param_name)

            # Build ArgSchema
            schema = _schema_from_annotation(annotation, nk_meta)
            if schema is not None:
                arg_schemas[param_name] = schema

        td = ToolDef(
            name=tool_name,
            description=meta_map["help"],
            required_args=tuple(required_args),
            optional_args=tuple(optional_args),
            keywords=tuple(meta_map["keywords"]),
            defaults=defaults,
            arg_schemas=arg_schemas,
        )
        registry[tool_name] = td
        tool_map[tool_name] = FunctionStep(tool_name, fn, td)

    return registry, tool_map
