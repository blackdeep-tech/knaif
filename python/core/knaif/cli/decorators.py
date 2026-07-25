"""Decorator front door for knaif.cli: @command, Arg, Opt, Ctx."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Arg:
    """Metadata for a required command argument (used with Annotated)."""

    help: str = ""
    required: bool = True
    choices: list[str] | None = None

    # Python 3.10's get_type_hints(include_extras=True) hashes Annotated metadata, and
    # @dataclass sets __hash__ = None whenever it generates __eq__. Without this, every
    # Annotated[..., Arg(...)] raises "unhashable type" there, which build_registry used to
    # swallow — so the argument silently lost its schema and skipped validation entirely.
    # 3.11+ stopped hashing, which is why this only ever showed on the declared floor.
    # Identity hashing is the right semantics here: these are per-parameter markers.
    __hash__ = object.__hash__


@dataclass
class Opt:
    """Metadata for an optional command argument (used with Annotated)."""

    help: str = ""
    required: bool = False
    choices: list[str] | None = None
    default: Any = None

    # See Arg.__hash__ above — same 3.10 constraint.
    __hash__ = object.__hash__


class Ctx:
    """Marker class: annotate a parameter with this to receive HandlerContext."""


def command(
    help: str = "",
    keywords: list[str] | None = None,
) -> Callable:
    """Decorator that marks a function as a knaif CLI command.

    Example::

        @nk.command(help="Return current time", keywords=["now", "time"])
        def now(tz: Annotated[str, nk.Opt(help="IANA timezone")] = "UTC") -> dict:
            ...
    """

    def decorator(fn: Callable) -> Callable:
        fn._nk_command = {  # type: ignore[attr-defined]
            "help": help,
            "keywords": list(keywords) if keywords else [],
        }
        return fn

    return decorator
