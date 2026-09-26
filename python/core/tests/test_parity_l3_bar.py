"""L3 at the owner's bar: zero port bugs, plus bounded plan disagreement (release plan R0/R2).

The old bar was an equivalence rate of 1.0, unreachable by construction: the two runtimes link
different llama.cpp builds, and even three native backends do not agree 100%. It also lumped two
different findings into "mismatch":

* the runtimes chose DIFFERENT PLANS — model noise on a near-tie, bounded, not a port defect;
* the runtimes chose the SAME PLAN and rendered DIFFERENT COMMANDS — a porting defect.

Both CLIs now dump the plan they ran (`$KNAIF_DUMP_PLAN`), so command-mode rows can be split.
L3 fails on any port bug (and on any capability native has not built), and on plan disagreement
above a bound written before the run.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "parity_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("parity_check", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["parity_check"] = module
    spec.loader.exec_module(module)
    return module


pc = _load()

PLAN = [{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "container": "mkv"}}]
OTHER_PLAN = [{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "container": "webm"}}]
CMD_A = "ffmpeg -y -i clip.mp4 -c copy clip_converted.mkv"
CMD_B = "ffmpeg -y -i clip.mp4 -c:v libx264 clip_converted.mkv"


def _dump(plan: list[dict]) -> str:
    return f"{pc.PLAN_DUMP_MARKER}{json.dumps({'plan': plan})}\n"


def _native(cmd: str, plan: list[dict]):
    return pc.parse_native(cmd + "\n", "ggml log\n" + _dump(plan))


def _python(cmd: str, plan: list[dict]):
    return pc.parse_python(f"  $ {cmd}\n", _dump(plan))


def _row():
    return pc.Row(id="x_001", utterance="convert clip.mp4 to mkv", tags=[], is_chain=False)


def test_both_parsers_read_the_dumped_plan() -> None:
    assert _native(CMD_A, PLAN).dumped_plan == PLAN
    assert _python(CMD_A, PLAN).dumped_plan == PLAN


def test_same_plan_different_commands_is_a_port_bug() -> None:
    status, _ = pc.compare(
        _row(), _native(CMD_A, PLAN), _python(CMD_B, PLAN), strict=False, cwd="/w"
    )
    assert status == "port-bug"


def test_different_plans_are_plan_disagreement_not_a_port_bug() -> None:
    status, _ = pc.compare(
        _row(), _native(CMD_A, PLAN), _python(CMD_B, OTHER_PLAN), strict=False, cwd="/w"
    )
    assert status == "mismatch"


def test_equal_commands_are_a_match_whatever_the_plans() -> None:
    status, _ = pc.compare(
        _row(), _native(CMD_A, PLAN), _python(CMD_A, PLAN), strict=False, cwd="/w"
    )
    assert status == "match"


def test_without_a_dump_a_mismatch_cannot_be_called_a_port_bug() -> None:
    nat = pc.parse_native(CMD_A + "\n", "")
    py = pc.parse_python(f"  $ {CMD_B}\n", "")
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "mismatch"


def _counts(**over: int) -> dict[str, int]:
    base = {
        "match": 95,
        "mismatch": 0,
        "decline-divergence": 0,
        "not-comparable": 3,
        "native-not-implemented": 0,
        "port-bug": 0,
    }
    base.update(over)
    return base


def test_the_verdict_passes_bounded_disagreement_and_no_port_bugs() -> None:
    v = pc.l3_verdict(_counts(match=96, mismatch=3, **{"decline-divergence": 1}), 0.05)
    assert v["passed"] and v["port_bugs"] == 0 and v["plan_disagreement"] == 4
    assert v["plan_disagreement_rate"] == pytest.approx(0.04)


def test_one_port_bug_fails_whatever_the_rates() -> None:
    v = pc.l3_verdict(_counts(**{"port-bug": 1}), 0.5)
    assert not v["passed"]


def test_a_capability_native_has_not_built_fails() -> None:
    v = pc.l3_verdict(_counts(**{"native-not-implemented": 1}), 0.5)
    assert not v["passed"]


def test_disagreement_over_the_bound_fails() -> None:
    v = pc.l3_verdict(_counts(match=94, mismatch=6), 0.05)
    assert not v["passed"] and v["plan_disagreement_rate"] == pytest.approx(0.06)


def test_nothing_to_compare_does_not_pass() -> None:
    v = pc.l3_verdict(dict.fromkeys(_counts(), 0), 0.05)
    assert not v["passed"]


def test_evidence_runs_must_state_the_bound_before_running() -> None:
    """A bound chosen after seeing the result is not a bound."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--skill", "ffmpeg", "--label", "x"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "--max-plan-disagreement" in proc.stderr + proc.stdout


def test_the_python_cli_dumps_its_plan_like_native(tmp_path: Path) -> None:
    env = {**os.environ, "KNAIF_DUMP_PLAN": "1"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "knaif.app",
            "run",
            "ffmpeg",
            "convert",
            "clip.mp4",
            "to",
            "mkv",
            "--backend",
            "mock",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=REPO,
    )
    dumps = [ln for ln in proc.stderr.splitlines() if ln.startswith(pc.PLAN_DUMP_MARKER)]
    assert dumps, proc.stderr[-500:]
    assert "plan" in json.loads(dumps[0][len(pc.PLAN_DUMP_MARKER) :])


def test_the_marker_is_one_string_in_all_three_places() -> None:
    from knaif.app import PLAN_DUMP_MARKER as app_marker
    from knaif.evalsuite.native_lane import PLAN_DUMP_MARKER as lane_marker

    rust = (REPO / "apps" / "cli" / "src" / "main.rs").read_text(encoding="utf-8")
    assert f'const PLAN_DUMP_MARKER: &str = "{pc.PLAN_DUMP_MARKER}";' in rust
    assert app_marker == lane_marker == pc.PLAN_DUMP_MARKER
