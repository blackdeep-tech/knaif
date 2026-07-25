"""Clock CLI — knaif.cli @command decorator variant.

Usage::

    python -m knaif.examples.clock.app_decorator "what time is it in Tokyo"
    python -m knaif.examples.clock.app_decorator "convert 2026-06-20T15:00 from London to Tokyo"
    python -m knaif.examples.clock.app_decorator "how many days from 2026-01-01 to today"
    python -m knaif.examples.clock.app_decorator "list timezones in europe"
"""

from __future__ import annotations

from typing import Annotated

import knaif.cli as nk
from knaif.examples.clock.store import (
    FMT_CHOICES,
    UNIT_CHOICES,
)
from knaif.examples.clock.store import (
    convert as _convert,
)
from knaif.examples.clock.store import (
    diff as _diff,
)
from knaif.examples.clock.store import (
    now as _now,
)
from knaif.examples.clock.store import (
    zones as _zones,
)


@nk.command(
    help="Return the current date/time",
    keywords=["now", "time", "current", "today", "date"],
)
def now(
    tz: Annotated[
        str | None, nk.Opt(help="IANA timezone (e.g. Asia/Tokyo). Defaults to UTC.")
    ] = None,
    fmt: Annotated[str, nk.Opt(help="Output format.", choices=list(FMT_CHOICES))] = "iso",
    date_only: Annotated[bool, nk.Opt(help="Return date only, no time.")] = False,
) -> dict:
    return _now(tz=tz, fmt=fmt, date_only=date_only)


@nk.command(
    help="Convert a datetime between timezones",
    keywords=["convert", "change", "translate", "timezone"],
)
def convert(
    value: Annotated[str, nk.Arg(help="Input datetime (e.g. 2026-06-20T15:00).")],
    from_tz: Annotated[str | None, nk.Opt(help="Source timezone. Defaults to UTC.")] = None,
    to_tz: Annotated[str, nk.Opt(help="Target timezone.")] = "UTC",
    fmt: Annotated[str, nk.Opt(help="Output format.", choices=list(FMT_CHOICES))] = "iso",
) -> dict:
    return _convert(value=value, from_tz=from_tz, to_tz=to_tz, fmt=fmt)


@nk.command(
    help="Compute elapsed time between two datetimes",
    keywords=["diff", "difference", "elapsed", "between", "days", "hours"],
)
def diff(
    start: Annotated[str, nk.Arg(help="Start datetime (ISO 8601).")],
    end: Annotated[str, nk.Opt(help="End datetime or 'now'. Defaults to now.")] = "now",
    unit: Annotated[str, nk.Opt(help="Output unit.", choices=list(UNIT_CHOICES))] = "days",
) -> dict:
    return _diff(start=start, end=end, unit=unit)


@nk.command(
    help="List or search IANA timezone names",
    keywords=["zones", "timezones", "list", "search", "find"],
)
def zones(
    query: Annotated[
        str | None, nk.Opt(help="Optional substring filter (e.g. 'europe', 'america').")
    ] = None,
) -> dict:
    return _zones(query=query)


# Importable without loading a model (tests use execute_plan / mock directly).
app = nk.App([now, convert, diff, zones])

if __name__ == "__main__":
    # Wire a real local backend only when run as a script.
    _orch = nk.local_ollama() or nk.local_llama_cpp("models/Qwen3-4B-Q4_K_M.gguf")
    app = nk.App([now, convert, diff, zones], orchestrator=_orch)
    app.run()
