"""One set of flags for both CLIs (owner, 2026-10-23).

`contracts/parity/cli_flag_cases.json`. pins what each argv after `run` approves; the Rust half
is `cli_flag_cases` in apps/cli/src/main.rs.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from knaif.app import allowed, run_cmd

CASES = json.loads(
    (Path(__file__).parents[3] / "contracts" / "parity" / "cli_flag_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_cli_flags(case: dict) -> None:
    if "error" in case:
        with pytest.raises(click.UsageError) as err:
            run_cmd.make_context("run", list(case["argv"]))
        assert case["error"] in err.value.format_message()
        return
    params = run_cmd.make_context("run", list(case["argv"])).params
    allow = allowed(params["allow"], yes=params["yes"])
    assert {
        "download": allow.download,
        "overwrite": allow.overwrite,
        "model": allow.model,
    } == case["allow"]
    assert allow.asks_proceed(confirm=params["confirm"]) == case["asks_proceed"]
    if "stdout" in case:
        assert params["stdout"] == case["stdout"]
