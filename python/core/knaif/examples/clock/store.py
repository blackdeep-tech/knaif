"""Pure-Python business logic for the clock example.

No knaif imports. All functions accept plain Python types and return dicts.
tzdata is listed as an example-only dependency in pyproject.toml so that
zoneinfo works on hosts that lack a system IANA database (e.g. Windows).
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

FMT_CHOICES = ("iso", "unix", "rfc2822", "human")
UNIT_CHOICES = ("seconds", "minutes", "hours", "days")

_PARSE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _get_tz(tz_name: str | None) -> timezone | ZoneInfo:
    if not tz_name:
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError) as err:
        raise ValueError(f"Unknown timezone: {tz_name!r}") from err


def _format_dt(dt: datetime, fmt: str) -> str:
    if fmt == "iso":
        return dt.isoformat()
    if fmt == "unix":
        return str(int(dt.timestamp()))
    if fmt == "rfc2822":
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")
    if fmt == "human":
        return dt.strftime("%Y-%m-%d %H:%M %Z").strip()
    raise ValueError(f"Unknown format: {fmt!r}. Valid: {list(FMT_CHOICES)}")


def _parse_dt(value: str) -> datetime:
    for fmt in _PARSE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"Cannot parse datetime: {value!r}. "
        "Expected ISO 8601 (e.g. 2026-06-20 or 2026-06-20T15:00)."
    )


def now(
    tz: str | None = None,
    fmt: str = "iso",
    date_only: bool = False,
) -> dict:
    """Return the current date/time.

    Parameters
    ----------
    tz:
        IANA timezone name (e.g. 'Asia/Tokyo'). Defaults to UTC.
    fmt:
        Output format: iso | unix | rfc2822 | human.
    date_only:
        When True, return the date only (no time component).
    """
    tz_obj = _get_tz(tz)
    dt = datetime.now(tz=tz_obj)
    tz_label = str(tz_obj)
    if date_only:
        return {"date": dt.date().isoformat(), "timezone": tz_label}
    return {"time": _format_dt(dt, fmt), "timezone": tz_label}


def convert(
    value: str,
    from_tz: str | None = None,
    to_tz: str = "UTC",
    fmt: str = "iso",
) -> dict:
    """Convert a datetime string from one timezone to another.

    Parameters
    ----------
    value:
        Input datetime (ISO 8601, e.g. '2026-06-20T15:00').
    from_tz:
        Source timezone. Defaults to UTC when omitted.
    to_tz:
        Target timezone. Defaults to UTC.
    fmt:
        Output format: iso | unix | rfc2822 | human.
    """
    from_tz_obj = _get_tz(from_tz)
    to_tz_obj = _get_tz(to_tz)
    dt = _parse_dt(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=from_tz_obj)
    dt_target = dt.astimezone(to_tz_obj)
    return {
        "result": _format_dt(dt_target, fmt),
        "from": value,
        "from_tz": str(from_tz_obj),
        "to": str(to_tz_obj),
    }


def diff(
    start: str,
    end: str = "now",
    unit: str = "days",
) -> dict:
    """Compute elapsed time between two datetimes.

    Parameters
    ----------
    start:
        Start datetime (ISO 8601).
    end:
        End datetime (ISO 8601) or 'now'. Defaults to 'now'.
    unit:
        Output unit: seconds | minutes | hours | days.
    """

    def _parse(s: str) -> datetime:
        if s.lower() == "now":
            return datetime.now(timezone.utc)
        dt = _parse_dt(s)
        return dt.replace(tzinfo=timezone.utc)

    start_dt = _parse(start)
    end_dt = _parse(end)
    total_seconds = (end_dt - start_dt).total_seconds()

    divisors = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}
    if unit not in divisors:
        raise ValueError(f"Unknown unit: {unit!r}. Valid: {list(UNIT_CHOICES)}")

    return {"value": round(total_seconds / divisors[unit], 2), "unit": unit}


def zones(query: str | None = None) -> dict:
    """List or search IANA timezone names.

    Parameters
    ----------
    query:
        Optional substring filter (case-insensitive).
        E.g. 'europe', 'tokyo', 'america'.
    """
    all_zones = sorted(available_timezones())
    if query:
        q = query.lower()
        filtered = [z for z in all_zones if q in z.lower()]
    else:
        filtered = all_zones
    return {"zones": filtered, "count": len(filtered)}
