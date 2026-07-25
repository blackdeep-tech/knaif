"""Tests for the ffmpeg skill RESULT_FORMATTER hook."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from knaif.skill import Skill

SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def format_fn():
    """Return the _format_results function from the loaded handlers module."""
    Skill.load(SKILL_DIR)
    handlers_mod = sys.modules.get("_skill_oop_ffmpeg_handlers")
    assert handlers_mod is not None, "_skill_oop_ffmpeg_handlers module not loaded"
    return handlers_mod._format_results


def test_result_formatter_dry_run_with_commands(format_fn):
    """A run_batch step with one dry-run output yields one 'command' item."""
    results: list[dict[str, Any]] = [
        {
            "tool": "run_batch",
            "result": {
                "mode": "dry_run",
                "outputs": [
                    {"command": ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"]},
                ],
            },
        }
    ]
    items = format_fn(results, dry_run=True)
    command_items = [i for i in items if i["kind"] == "command"]
    assert len(command_items) == 1
    assert command_items[0]["message"] == "ffmpeg -y -i in.mp4 out.mp4"


def test_result_formatter_execute_with_run_batch_outputs(format_fn):
    """run_batch outputs (returncode 0) yield 'output' items in execute mode."""
    results: list[dict[str, Any]] = [
        {
            "tool": "run_batch",
            "result": {
                "mode": "execute",
                "outputs": [
                    {"output": "/tmp/a.mp4", "returncode": 0},
                    {"output": "/tmp/b.mp4", "returncode": 0},
                ],
            },
        }
    ]
    items = format_fn(results, dry_run=False)
    output_items = [i for i in items if i["kind"] == "output"]
    assert len(output_items) == 2
    assert {i["message"] for i in output_items} == {"a.mp4", "b.mp4"}


def test_result_formatter_execute_skips_failed_outputs(format_fn):
    """run_batch outputs with non-zero returncode are excluded from 'output' items."""
    results: list[dict[str, Any]] = [
        {
            "tool": "run_batch",
            "result": {
                "mode": "execute",
                "outputs": [
                    {"output": "/tmp/ok.mp4", "returncode": 0},
                    {"output": "/tmp/bad.mp4", "returncode": 1},
                ],
            },
        }
    ]
    items = format_fn(results, dry_run=False)
    output_items = [i for i in items if i["kind"] == "output"]
    assert [i["message"] for i in output_items] == ["ok.mp4"]


def test_result_formatter_inspect_errors(format_fn):
    """inspect_media errors yield 'error' items and short-circuit further items."""
    results: list[dict[str, Any]] = [
        {
            "tool": "inspect_media",
            "result": {"errors": [{"file": "x.mp4", "error": "not found"}]},
        },
        {
            "tool": "run_batch",
            "result": {"outputs": [{"command": ["ffmpeg", "-y", "out.mp4"]}]},
        },
    ]
    items = format_fn(results, dry_run=True)
    assert any(i["kind"] == "error" and "not found" in i["message"] for i in items)
    # Bail-out: no command/output items follow.
    assert not any(i["kind"] in ("command", "output") for i in items)


def test_result_formatter_empty_batch_dry_run(format_fn):
    """No batch outputs in dry-run yields a single info item."""
    items = format_fn([], dry_run=True)
    assert items == [{"kind": "info", "message": "(nothing to execute)"}]


def test_result_formatter_multi_intent_accumulates_outputs(format_fn):
    """Two run_batch steps (one per intent) yield items for ALL produced files."""
    results: list[dict[str, Any]] = [
        {
            "tool": "run_batch",
            "result": {
                "mode": "execute",
                "outputs": [{"output": "/tmp/clip.mkv", "returncode": 0}],
            },
        },
        {
            "tool": "run_batch",
            "result": {
                "mode": "execute",
                "outputs": [{"output": "/tmp/clip_audio.mp3", "returncode": 0}],
            },
        },
    ]
    items = format_fn(results, dry_run=False)
    output_items = [i for i in items if i["kind"] == "output"]
    assert {i["message"] for i in output_items} == {"clip.mkv", "clip_audio.mp3"}


def test_result_formatter_multi_intent_dry_run_accumulates_commands(format_fn):
    """Two run_batch steps yield command items for ALL invocations."""
    results: list[dict[str, Any]] = [
        {
            "tool": "run_batch",
            "result": {"outputs": [{"command": ["ffmpeg", "-y", "-i", "a.mp4", "a.mkv"]}]},
        },
        {
            "tool": "run_batch",
            "result": {"outputs": [{"command": ["ffmpeg", "-y", "-i", "a.mp4", "a.mp3"]}]},
        },
    ]
    items = format_fn(results, dry_run=True)
    cmds = [i["message"] for i in items if i["kind"] == "command"]
    assert len(cmds) == 2
    assert any("a.mkv" in c for c in cmds)
    assert any("a.mp3" in c for c in cmds)
