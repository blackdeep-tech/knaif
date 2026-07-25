"""Clock CLI — knaif.cli from_click() adapter variant.

Demonstrates wrapping an existing click CLI with knaif natural-language
dispatch. The click commands define the tool contract; knaif.from_click()
builds the registry and tool_map automatically.

Usage::

    python -m knaif.examples.clock.app_click "what time is it in Tokyo"
    python -m knaif.examples.clock.app_click "list timezones in europe"
"""

from __future__ import annotations

import click

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

FMT_TYPE = click.Choice(list(FMT_CHOICES))
UNIT_TYPE = click.Choice(list(UNIT_CHOICES))


@click.group()
def clock_cli():
    """Natural-language clock CLI powered by knaif."""


@clock_cli.command()
@click.option("--tz", default=None, help="IANA timezone (e.g. Asia/Tokyo). Defaults to UTC.")
@click.option("--fmt", type=FMT_TYPE, default="iso", help="Output format.")
@click.option("--date-only", is_flag=True, default=False, help="Return date only.")
def now(tz, fmt, date_only):
    """Return the current date/time."""
    return _now(tz=tz, fmt=fmt, date_only=date_only)


@clock_cli.command()
@click.option("--value", required=True, help="Input datetime (e.g. 2026-06-20T15:00).")
@click.option("--from-tz", default=None, help="Source timezone. Defaults to UTC.")
@click.option("--to-tz", default="UTC", help="Target timezone.")
@click.option("--fmt", type=FMT_TYPE, default="iso", help="Output format.")
def convert(value, from_tz, to_tz, fmt):
    """Convert a datetime between timezones."""
    return _convert(value=value, from_tz=from_tz, to_tz=to_tz, fmt=fmt)


@clock_cli.command()
@click.argument("start")
@click.option("--end", default="now", help="End datetime or 'now'.")
@click.option("--unit", type=UNIT_TYPE, default="days", help="Output unit.")
def diff(start, end, unit):
    """Compute elapsed time between two datetimes."""
    return _diff(start=start, end=end, unit=unit)


@clock_cli.command()
@click.option("--query", default=None, help="Optional substring filter.")
def zones(query):
    """List or search IANA timezone names."""
    return _zones(query=query)


# Importable without loading a model (tests use execute_plan / mock directly).
app = nk.from_click(clock_cli)

if __name__ == "__main__":
    # Wire a real local backend only when run as a script.
    _orch = nk.local_ollama() or nk.local_llama_cpp("models/Qwen3-4B-Q4_K_M.gguf")
    app = nk.from_click(clock_cli, orchestrator=_orch)
    app.run()
