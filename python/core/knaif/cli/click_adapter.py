"""from_click: build a knaif.cli App from an existing click.Group.

Mapped features
---------------
- Flat ``click.Group`` with ``click.Argument`` (required) and ``click.Option``
  (optional, with defaults) sub-commands.
- ``click.Choice`` → ``ArgSchema(type="enum")``.
- ``click.INT`` / ``click.FLOAT`` / ``click.BOOL`` / ``click.STRING`` →
  corresponding ``ArgSchema`` types.
- Hyphenated option names (``--from-tz``) are normalised to underscores
  (``from_tz``) in both the registry and the wrapper kwargs.
- Option defaults are pre-filled in a ``_make_wrapper`` so the callback always
  receives all parameters even when the model only sets a subset.

Unmapped / unsupported features
--------------------------------
- **Nested groups** (``click.Group`` inside a group): not walked; only top-level
  commands are registered. Flatten to a single group or use ``@nk.command``.
- **Variadic arguments** (``nargs=-1``): not mapped; use ``type: array`` via
  ``@nk.command`` + ``ArgSchema`` instead.
- **``click.Context`` parameters** (``pass_context=True``): ignored; context
  objects cannot appear in model-generated plans.
- **``click.File`` / ``click.Path``**: mapped as ``string``; add ``path_role``
  via ``@nk.command`` + explicit ``arg_schemas`` for sandbox path validation.
- **Callback-based validation**: not preserved; move validation into the
  function body or use ``ArgSchema`` enum/min/max constraints.

See ``docs/SDK.md`` → "from_click limitations" for the full table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

from knaif.cli.function_step import FunctionStep
from knaif.cli.runner import App
from knaif.registry import ArgSchema, ToolDef

if TYPE_CHECKING:
    from knaif.orchestrator import InferenceOrchestrator


def _schema_from_click_param(param: click.Parameter) -> ArgSchema | None:
    """Derive an ArgSchema from a click Parameter's type."""
    pt = param.type
    help_text = getattr(param, "help", None) or None
    if isinstance(pt, click.Choice):
        return ArgSchema(type="enum", enum=tuple(pt.choices), help=help_text)
    if isinstance(pt, click.types.IntParamType):
        return ArgSchema(type="integer", help=help_text)
    if isinstance(pt, click.types.FloatParamType):
        return ArgSchema(type="number", help=help_text)
    if isinstance(pt, click.types.BoolParamType):
        return ArgSchema(type="boolean", help=help_text)
    if isinstance(pt, click.types.StringParamType):
        return ArgSchema(type="string", help=help_text)
    return None


def from_click(
    group: click.Group,
    *,
    orchestrator: InferenceOrchestrator | None = None,
    **app_kwargs: Any,
) -> App:
    """Build a :class:`~knaif.cli.runner.App` from a :class:`click.Group`.

    Each sub-command becomes a tool. ``click.Argument`` → required arg;
    ``click.Option`` → optional arg. Hyphenated option names (``--from-tz``)
    are normalised to underscores (``from_tz``).

    Parameters
    ----------
    group:
        A ``click.Group`` whose sub-commands define the tool set.
    orchestrator:
        Optional inference backend forwarded to ``App``.
    **app_kwargs:
        Additional keyword arguments forwarded to ``App.__init__``.
    """
    if not isinstance(group, click.Group):
        raise TypeError(
            f"from_click() expects a click.Group, got {type(group).__name__!r}. "
            "Wrap individual commands in a click.group() first."
        )

    registry: dict[str, ToolDef] = {}
    tool_map: dict[str, FunctionStep] = {}

    for cmd_name, cmd in group.commands.items():
        required_args: list[str] = []
        optional_args: list[str] = []
        defaults: dict[str, Any] = {}
        arg_schemas: dict[str, ArgSchema] = {}

        for param in cmd.params:
            # Normalise hyphenated names to underscore
            arg_name = param.name.replace("-", "_") if param.name else param.name

            is_required_option = isinstance(param, click.Option) and param.required
            if isinstance(param, click.Argument) or is_required_option:
                required_args.append(arg_name)
            else:
                optional_args.append(arg_name)
                # Skip sentinel/unset defaults (click.core.Sentinel or similar)
                default_val = param.default
                is_real_default = default_val is not None and not (
                    hasattr(default_val, "__class__")
                    and default_val.__class__.__name__ == "Sentinel"
                )
                if is_real_default:
                    defaults[arg_name] = default_val

            schema = _schema_from_click_param(param)
            if schema is not None:
                arg_schemas[arg_name] = schema

        description = (cmd.help or "").strip()

        td = ToolDef(
            name=cmd_name,
            description=description,
            required_args=tuple(required_args),
            optional_args=tuple(optional_args),
            defaults=defaults,
            arg_schemas=arg_schemas,
        )
        registry[cmd_name] = td

        # Build a wrapper that fills in click option defaults so the callback
        # can be called with a partial kwargs dict (as FunctionStep does).
        # Required options have no real default — exclude them to avoid
        # pre-filling with click's Sentinel object.
        def _is_sentinel(v: Any) -> bool:
            return hasattr(v, "__class__") and v.__class__.__name__ == "Sentinel"

        option_defaults: dict[str, Any] = {}
        for _p in cmd.params:
            if not isinstance(_p, click.Option) or _p.required:
                continue
            _pname = _p.name.replace("-", "_")
            _default = _p.default
            if not _is_sentinel(_default):
                option_defaults[_pname] = _default

        def _make_wrapper(cb: Any, opt_defaults: dict[str, Any]) -> Any:
            def wrapper(**kwargs: Any) -> Any:
                filled = dict(opt_defaults)
                filled.update(kwargs)
                return cb(**filled)

            return wrapper

        tool_map[cmd_name] = FunctionStep(
            cmd_name, _make_wrapper(cmd.callback, option_defaults), td
        )

    # Build App directly without build_registry (we already have registry + tool_map)
    app = App.__new__(App)
    app.registry = registry
    app._tool_map = tool_map

    from knaif.agent import CommandAgent

    app._agent = CommandAgent.from_registry(
        registry,
        tool_map=tool_map,
        orchestrator=orchestrator,
        **app_kwargs,
    )
    return app
