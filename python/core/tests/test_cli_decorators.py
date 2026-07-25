"""Tests for knaif.cli decorator front door (@command, Arg, Opt) and build."""

from __future__ import annotations

from typing import Annotated, Optional

import pytest

import knaif.cli as nk
from knaif.cli.build import (
    _extract_meta,
    _schema_from_annotation,
    _unwrap,
    build_registry,
)
from knaif.registry import ToolDef

# ── @command decorator ────────────────────────────────────────────────────────


def test_command_attaches_metadata():
    @nk.command(help="Do something", keywords=["do", "run"])
    def do_thing(name: str) -> dict:
        return {"name": name}

    assert hasattr(do_thing, "_nk_command")
    meta = do_thing._nk_command
    assert meta["help"] == "Do something"
    assert "do" in meta["keywords"]


def test_command_without_keywords():
    @nk.command(help="Simple")
    def simple() -> dict:
        return {}

    assert simple._nk_command["keywords"] == []


def test_command_preserves_callable():
    @nk.command(help="Add")
    def add(a: str, b: str) -> dict:
        return {"v": a + b}

    assert add(a="x", b="y") == {"v": "xy"}


# ── Arg / Opt metadata ────────────────────────────────────────────────────────


def test_arg_creates_annotated_metadata():
    meta = nk.Arg(help="a title")
    assert meta.required is True
    assert meta.help == "a title"
    assert meta.choices is None


def test_opt_creates_annotated_metadata():
    meta = nk.Opt(help="priority", choices=["low", "med", "high"], default="med")
    assert meta.required is False
    assert meta.choices == ["low", "med", "high"]
    assert meta.default == "med"


def test_opt_default_is_none_when_not_given():
    meta = nk.Opt(help="flag")
    assert meta.default is None


# ── build_registry ────────────────────────────────────────────────────────────


def test_build_registry_creates_tool_def():
    @nk.command(help="Say hello")
    def greet(name: str) -> dict:
        return {"msg": f"hello {name}"}

    registry, tool_map = build_registry([greet])
    assert "greet" in registry
    td = registry["greet"]
    assert isinstance(td, ToolDef)
    assert td.description == "Say hello"
    assert "name" in td.required_args


def test_build_registry_creates_function_step():
    from knaif.cli.function_step import FunctionStep

    @nk.command(help="Do")
    def act(x: str) -> dict:
        return {}

    _, tool_map = build_registry([act])
    assert "act" in tool_map
    assert isinstance(tool_map["act"], FunctionStep)


def test_build_registry_keywords_from_decorator():
    @nk.command(help="Convert time", keywords=["convert", "change"])
    def convert(value: str) -> dict:
        return {}

    registry, _ = build_registry([convert])
    assert "convert" in registry["convert"].keywords
    assert "change" in registry["convert"].keywords


def test_build_registry_opt_becomes_optional_arg():
    @nk.command(help="Now")
    def now(
        tz: Annotated[str, nk.Opt(help="timezone")] = "UTC",
    ) -> dict:
        return {}

    registry, _ = build_registry([now])
    td = registry["now"]
    assert "tz" in td.optional_args
    assert "tz" not in td.required_args


def test_optional_annotation_still_derives_a_schema():
    """``X | None`` must derive the same schema as ``X``.

    An argument with a ``None`` default is normally typed ``X | None``. Before the union
    was unwrapped, the type lookup missed it and the argument silently lost its schema,
    so it skipped validation. The only alternative was a bare ``X`` with a ``None``
    default, which type checkers reject as implicit Optional — leaving no way to write an
    optional argument that was both correctly typed and correctly validated.
    """

    @nk.command(help="Now")
    def now(
        tz: Annotated[str | None, nk.Opt(help="timezone")] = None,
        # noqa intentional: the legacy `Optional[X]` spelling builds a `typing.Union`,
        # while `X | None` builds a `types.UnionType`. Both are unwrapped, so both need
        # coverage — rewriting this to `int | None` would leave that branch untested.
        count: Annotated[Optional[int], nk.Opt(help="count")] = None,  # noqa: UP045
        tags: Annotated[list | None, nk.Opt(help="tags")] = None,
    ) -> dict:
        return {}

    registry, _ = build_registry([now])
    schemas = registry["now"].arg_schemas
    assert schemas["tz"].type == "string"
    assert schemas["count"].type == "integer"
    assert schemas["tags"].type == "array"


def test_optional_wrapped_outside_annotated_still_resolves():
    """``Optional[Annotated[X | None, meta]]`` — the shape Python 3.10 produces.

    On 3.10, ``get_type_hints`` re-wraps any parameter with a ``None`` default in
    ``Optional[...]``, putting the union *outside* ``Annotated`` instead of inside it.
    Unwrapping either layer first is therefore wrong on one version or the other, and
    getting it wrong is silent: the schema and the Arg/Opt metadata both vanish and the
    argument stops being validated.

    3.11 dropped that behaviour, so the version-dependent test above cannot fail on a
    modern interpreter. Constructing the 3.10 shape explicitly keeps the regression
    catchable everywhere.
    """
    # noqa intentional: `Optional[...]` is the shape under test — it is what 3.10 builds.
    # Rewriting it to `X | None` would construct a `types.UnionType` and stop reproducing
    # the bug, which is the one thing this test exists to do.
    annotation = Optional[  # noqa: UP045
        Annotated[str | None, nk.Opt(help="timezone", choices=["a", "b"])]
    ]

    meta = _extract_meta(annotation)
    assert isinstance(meta, nk.Opt), "metadata must survive an outer Optional wrapper"
    assert meta.choices == ["a", "b"]

    assert _unwrap(annotation) is str
    assert _schema_from_annotation(annotation, meta).type == "enum"


def test_ambiguous_union_derives_no_schema():
    """Only ``X | None`` unwraps; a genuine multi-type union stays unsupported."""

    @nk.command(help="Odd")
    def odd(
        value: Annotated[str | int, nk.Opt(help="value")] = "x",
    ) -> dict:
        return {}

    registry, _ = build_registry([odd])
    assert registry["odd"].arg_schemas.get("value") is None


def test_build_registry_arg_becomes_required_arg():
    @nk.command(help="Convert")
    def convert(
        value: Annotated[str, nk.Arg(help="input value")],
    ) -> dict:
        return {}

    registry, _ = build_registry([convert])
    td = registry["convert"]
    assert "value" in td.required_args


def test_build_registry_opt_enum_from_choices():
    @nk.command(help="Format time")
    def fmt_time(
        fmt: Annotated[str, nk.Opt(choices=["iso", "unix", "human"])] = "iso",
    ) -> dict:
        return {}

    registry, _ = build_registry([fmt_time])
    td = registry["fmt_time"]
    schema = td.arg_schemas.get("fmt")
    assert schema is not None
    assert schema.type == "enum"
    assert "iso" in schema.enum
    assert "unix" in schema.enum


def test_build_registry_plain_int_annotation_maps_to_integer_schema():
    @nk.command(help="Repeat")
    def repeat(count: int) -> dict:
        return {}

    registry, _ = build_registry([repeat])
    schema = registry["repeat"].arg_schemas.get("count")
    assert schema is not None
    assert schema.type == "integer"


def test_build_registry_plain_bool_annotation_maps_to_boolean_schema():
    @nk.command(help="Toggle")
    def toggle(enabled: bool = False) -> dict:
        return {}

    registry, _ = build_registry([toggle])
    schema = registry["toggle"].arg_schemas.get("enabled")
    assert schema is not None
    assert schema.type == "boolean"


def test_build_registry_defaults_captured():
    @nk.command(help="Now")
    def now(fmt: str = "iso") -> dict:
        return {}

    registry, _ = build_registry([now])
    td = registry["now"]
    assert td.defaults.get("fmt") == "iso"


def test_build_registry_multiple_commands():
    @nk.command(help="A")
    def cmd_a(x: str) -> dict:
        return {}

    @nk.command(help="B")
    def cmd_b(y: str) -> dict:
        return {}

    registry, tool_map = build_registry([cmd_a, cmd_b])
    assert "cmd_a" in registry
    assert "cmd_b" in registry
    assert len(tool_map) == 2


def test_build_registry_raises_on_undecorated_function():
    def raw(x: str) -> dict:
        return {}

    with pytest.raises(ValueError, match="not decorated"):
        build_registry([raw])
