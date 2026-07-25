"""Tests for the clock example app (knaif/examples/clock/).

Covers:
- store.py pure-Python functions (unit tests)
- app_decorator.py: @nk.command wiring + execute_plan plumbing
- app_click.py: from_click() wiring + execute_plan plumbing
"""

from __future__ import annotations

import re

import pytest

from knaif.examples.clock.app_click import app as click_app
from knaif.examples.clock.app_decorator import app as decorator_app
from knaif.examples.clock.store import convert, diff, now, zones

# ── store ──────────────────────────────────────────────────────────────────────


class TestNow:
    def test_returns_time_key(self):
        result = now()
        assert "time" in result

    def test_iso_format_has_T(self):
        result = now(fmt="iso")
        assert "T" in result["time"]

    def test_unix_format_is_numeric(self):
        result = now(fmt="unix")
        assert result["time"].isdigit()

    def test_human_format_readable(self):
        result = now(fmt="human")
        assert re.search(r"\d{4}-\d{2}-\d{2}", result["time"])

    def test_rfc2822_format(self):
        result = now(fmt="rfc2822")
        # RFC2822 looks like "Fri, 20 Jun 2026 12:34:56 +0000"
        assert "," in result["time"]

    def test_date_only_returns_date_key(self):
        result = now(date_only=True)
        assert "date" in result
        assert "time" not in result
        assert re.match(r"\d{4}-\d{2}-\d{2}", result["date"])

    def test_tz_utc(self):
        result = now(tz="UTC")
        assert result["timezone"] == "UTC"

    def test_tz_tokyo(self):
        result = now(tz="Asia/Tokyo")
        assert "Tokyo" in result["timezone"]

    def test_tz_new_york(self):
        result = now(tz="America/New_York")
        assert "New_York" in result["timezone"]

    def test_unknown_tz_raises(self):
        with pytest.raises(ValueError, match="Unknown timezone"):
            now(tz="Fake/Zone")

    def test_invalid_fmt_raises(self):
        with pytest.raises(ValueError, match="Unknown format"):
            now(fmt="xml")


class TestConvert:
    def test_basic_utc_to_tokyo(self):
        result = convert("2026-06-20T00:00", from_tz="UTC", to_tz="Asia/Tokyo")
        assert "result" in result
        assert "T" in result["result"]

    def test_iso_format_by_default(self):
        result = convert("2026-06-20T12:00", to_tz="UTC")
        assert "T" in result["result"]

    def test_unix_format(self):
        result = convert("2026-06-20T00:00", to_tz="UTC", fmt="unix")
        assert result["result"].isdigit()

    def test_returns_from_and_to_fields(self):
        result = convert("2026-06-20T00:00", to_tz="UTC")
        assert "from" in result
        assert "to" in result

    def test_bad_datetime_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            convert("not-a-date", to_tz="UTC")

    def test_unknown_to_tz_raises(self):
        with pytest.raises(ValueError, match="Unknown timezone"):
            convert("2026-06-20T00:00", to_tz="Fake/Zone")


class TestDiff:
    def test_returns_value_and_unit(self):
        result = diff("2026-01-01", "2026-06-20")
        assert "value" in result
        assert result["unit"] == "days"

    def test_days_positive(self):
        result = diff("2026-01-01", "2026-01-02")
        assert result["value"] == pytest.approx(1.0)

    def test_hours_unit(self):
        result = diff("2026-01-01T00:00", "2026-01-01T06:00", unit="hours")
        assert result["value"] == pytest.approx(6.0)

    def test_seconds_unit(self):
        result = diff("2026-01-01T00:00:00", "2026-01-01T00:01:00", unit="seconds")
        assert result["value"] == pytest.approx(60.0)

    def test_end_defaults_to_now(self):
        result = diff("2020-01-01")
        assert result["value"] > 0

    def test_bad_start_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            diff("not-a-date")

    def test_unknown_unit_raises(self):
        with pytest.raises(ValueError, match="Unknown unit"):
            diff("2026-01-01", "2026-01-02", unit="fortnights")


class TestZones:
    def test_returns_zones_and_count(self):
        result = zones()
        assert "zones" in result
        assert "count" in result
        assert result["count"] == len(result["zones"])

    def test_zones_is_sorted_list(self):
        result = zones()
        assert result["zones"] == sorted(result["zones"])

    def test_filter_europe(self):
        result = zones("europe")
        assert all("Europe" in z for z in result["zones"])
        assert result["count"] > 0

    def test_filter_case_insensitive(self):
        result_lower = zones("EUROPE")
        result_mixed = zones("Europe")
        assert result_lower["count"] == result_mixed["count"]

    def test_no_match_returns_empty(self):
        result = zones("XxXnonexistentXxX")
        assert result["count"] == 0
        assert result["zones"] == []


# ── decorator app ──────────────────────────────────────────────────────────────


class TestDecoratorApp:
    def test_app_has_all_commands(self):
        for cmd in ("now", "convert", "diff", "zones"):
            assert cmd in decorator_app.registry

    def test_now_fmt_schema_is_enum(self):
        schema = decorator_app.registry["now"].arg_schemas.get("fmt")
        assert schema is not None
        assert schema.type == "enum"
        assert "iso" in schema.enum
        assert "unix" in schema.enum

    def test_convert_value_is_required(self):
        assert "value" in decorator_app.registry["convert"].required_args

    def test_diff_unit_schema_is_enum(self):
        schema = decorator_app.registry["diff"].arg_schemas.get("unit")
        assert schema is not None
        assert "days" in schema.enum

    def test_execute_now_via_plan(self, tmp_path):
        payload = {"plan": [{"tool": "now", "args": {"fmt": "iso"}}]}
        results = decorator_app._agent.execute_plan(payload, dry_run=True)
        assert "T" in results[0]["result"]["time"]

    def test_execute_now_date_only(self, tmp_path):
        payload = {"plan": [{"tool": "now", "args": {"date_only": True}}]}
        results = decorator_app._agent.execute_plan(payload, dry_run=True)
        assert "date" in results[0]["result"]

    def test_execute_convert(self):
        payload = {
            "plan": [
                {
                    "tool": "convert",
                    "args": {
                        "value": "2026-06-20T00:00",
                        "from_tz": "UTC",
                        "to_tz": "Asia/Tokyo",
                        "fmt": "iso",
                    },
                }
            ]
        }
        results = decorator_app._agent.execute_plan(payload, dry_run=True)
        assert "result" in results[0]["result"]

    def test_execute_diff(self):
        payload = {
            "plan": [
                {
                    "tool": "diff",
                    "args": {"start": "2026-01-01", "end": "2026-06-20", "unit": "days"},
                }
            ]
        }
        results = decorator_app._agent.execute_plan(payload, dry_run=True)
        assert results[0]["result"]["value"] > 0

    def test_execute_zones_filtered(self):
        payload = {"plan": [{"tool": "zones", "args": {"query": "europe"}}]}
        results = decorator_app._agent.execute_plan(payload, dry_run=True)
        assert results[0]["result"]["count"] > 0

    def test_invalid_fmt_enum_rejected_at_validation(self):
        from knaif.planner import validate_plan

        payload = {"plan": [{"tool": "now", "args": {"fmt": "xml"}}]}
        with pytest.raises(ValueError, match="must be one of"):
            validate_plan(payload, decorator_app.registry, decorator_app._agent.root)


# ── click adapter app ──────────────────────────────────────────────────────────


class TestClickApp:
    def test_app_has_all_commands(self):
        for cmd in ("now", "convert", "diff", "zones"):
            assert cmd in click_app.registry

    def test_now_fmt_schema_is_enum(self):
        schema = click_app.registry["now"].arg_schemas.get("fmt")
        assert schema is not None
        assert schema.type == "enum"

    def test_execute_now_via_plan(self):
        payload = {"plan": [{"tool": "now", "args": {"fmt": "iso"}}]}
        results = click_app._agent.execute_plan(payload, dry_run=True)
        assert "T" in results[0]["result"]["time"]
