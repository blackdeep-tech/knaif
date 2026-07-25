"""Tests for knaif.cli.click_adapter.from_click()."""

from __future__ import annotations

import click
import pytest

from knaif.cli.click_adapter import from_click
from knaif.cli.runner import App

# ── fixtures ───────────────────────────────────────────────────────────────────


@click.group()
def cli():
    """Test CLI group."""


@cli.command()
@click.argument("name")
@click.option("--greeting", "-g", default="Hello", help="Greeting word")
def greet(name, greeting):
    """Greet someone."""
    return {"msg": f"{greeting}, {name}"}


@cli.command()
@click.argument("value")
@click.option("--from-tz", default="UTC", help="Source timezone")
@click.option("--to-tz", default="UTC", help="Target timezone")
def convert(value, from_tz, to_tz):
    """Convert a time between timezones."""
    return {"value": value, "from_tz": from_tz, "to_tz": to_tz}


@click.group()
def typed_cli():
    pass


@typed_cli.command()
@click.argument("count", type=int)
@click.option("--flag/--no-flag", default=False)
@click.option("--fmt", type=click.Choice(["iso", "unix", "human"]), default="iso")
def sample(count, flag, fmt):
    """Sample with typed args."""
    return {}


# ── from_click() ───────────────────────────────────────────────────────────────


def test_from_click_returns_app():
    app = from_click(cli)
    assert isinstance(app, App)


def test_from_click_registers_all_commands():
    app = from_click(cli)
    assert "greet" in app.registry
    assert "convert" in app.registry


def test_from_click_description_from_docstring():
    app = from_click(cli)
    assert app.registry["greet"].description == "Greet someone."


def test_from_click_argument_is_required():
    app = from_click(cli)
    td = app.registry["greet"]
    assert "name" in td.required_args


def test_from_click_option_is_optional():
    app = from_click(cli)
    td = app.registry["greet"]
    assert "greeting" in td.optional_args
    assert "greeting" not in td.required_args


def test_from_click_option_default_captured():
    app = from_click(cli)
    td = app.registry["greet"]
    assert td.defaults.get("greeting") == "Hello"


def test_from_click_hyphenated_option_normalized():
    """--from-tz becomes from_tz in the registry."""
    app = from_click(cli)
    td = app.registry["convert"]
    assert "from_tz" in td.optional_args


def test_from_click_int_arg_schema():
    app = from_click(typed_cli)
    schema = app.registry["sample"].arg_schemas.get("count")
    assert schema is not None
    assert schema.type == "integer"


def test_from_click_bool_option_schema():
    app = from_click(typed_cli)
    schema = app.registry["sample"].arg_schemas.get("flag")
    assert schema is not None
    assert schema.type == "boolean"


def test_from_click_choice_option_schema():
    app = from_click(typed_cli)
    schema = app.registry["sample"].arg_schemas.get("fmt")
    assert schema is not None
    assert schema.type == "enum"
    assert "iso" in schema.enum
    assert "unix" in schema.enum


def test_from_click_callback_is_wrapped():
    """The callback from the click command is called via FunctionStep."""
    from knaif.cli.function_step import FunctionStep

    app = from_click(cli)
    assert isinstance(app._tool_map["greet"], FunctionStep)


def test_from_click_raises_on_non_group():
    with pytest.raises(TypeError, match="click.Group"):
        from_click(greet)  # type: ignore[arg-type]


# ── required click.Option ─────────────────────────────────────────────────────


@click.group()
def req_opt_cli():
    pass


@req_opt_cli.command()
@click.option("--value", required=True, help="Input datetime.")
@click.option("--fmt", default="iso", help="Output format.")
def req_convert(value, fmt):
    """Convert with a required option."""
    return {"value": value, "fmt": fmt}


def test_required_option_goes_to_required_args():
    app = from_click(req_opt_cli)
    td = app.registry["req-convert"]
    assert "value" in td.required_args
    assert "value" not in td.optional_args


def test_required_option_not_in_defaults():
    app = from_click(req_opt_cli)
    td = app.registry["req-convert"]
    assert "value" not in td.defaults


def test_required_option_no_sentinel_in_wrapper(tmp_path):
    """FunctionStep must not pre-fill required options with Sentinel."""
    from unittest.mock import MagicMock

    mock_orch = MagicMock()
    mock_orch.infer.return_value = (
        '{"plan": [{"tool": "req-convert", "args": {"value": "2026-06-20T15:00"}}]}'
    )
    app = from_click(req_opt_cli, orchestrator=mock_orch, root=tmp_path)
    results = app.invoke("convert 2026-06-20T15:00")
    assert results[0]["result"]["value"] == "2026-06-20T15:00"


def test_optional_option_default_still_filled_in_wrapper(tmp_path):
    """Optional option defaults are still pre-filled by the wrapper."""
    from unittest.mock import MagicMock

    mock_orch = MagicMock()
    mock_orch.infer.return_value = (
        '{"plan": [{"tool": "req-convert", "args": {"value": "2026-06-20T15:00"}}]}'
    )
    app = from_click(req_opt_cli, orchestrator=mock_orch, root=tmp_path)
    results = app.invoke("convert 2026-06-20T15:00")
    assert results[0]["result"]["fmt"] == "iso"
