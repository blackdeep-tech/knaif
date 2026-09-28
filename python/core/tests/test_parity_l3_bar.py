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


# ── reading each side's command line the way it was written (L3 2026-09-27) ────────────────

# The two renders of "crop clip.mp4 to a square" from the stopped R5c run. Both runtimes pass
# `iw\,ih` to ffmpeg (L4 executed the row correctly on native). Native prints a POSIX-quoted
# line (`shell_join` doubles a backslash inside quotes); Python prints the argv unquoted.
CROP = r"crop=trunc(min(iw\,ih*1/1)/2)*2:trunc(min(ih\,iw*1/1)/2)*2"
CROP_NATIVE = (
    'ffmpeg -y -i clip.mp4 -vf "crop=trunc(min(iw\\\\,ih*1/1)/2)*2:trunc(min(ih\\\\,iw*1/1)/2)*2"'
    " -c:a copy clip_resized.mp4"
)
CROP_PYTHON = r"ffmpeg -y -i C:\w\clip.mp4 -vf " + CROP + r" -c:a copy C:\w\clip_resized.mp4"
CROP_PLAN = [
    {"tool": "resize_video", "args": {"inputs": ["clip.mp4"], "fit": "crop", "aspect": "1:1"}}
]


def test_an_ffmpeg_escape_survives_both_parsers() -> None:
    """The tokenizer forward-slashed every backslash to cope with Windows paths, so the filter's
    `\\,` became `/,` on Python's unquoted line and `//,` on native's quoted one: three rows of
    identical commands reported as port bugs."""
    nat = _native(CROP_NATIVE, CROP_PLAN)
    py = _python(CROP_PYTHON, CROP_PLAN)
    assert CROP in nat.commands[0]
    assert CROP in py.commands[0]
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="C:/w")
    assert status == "match"


def test_a_windows_path_is_still_forward_slashed() -> None:
    assert pc.to_argv(r"ffmpeg -i C:\w\sub\clip.mp4 out.mp4") == [
        "ffmpeg",
        "-i",
        "C:/w/sub/clip.mp4",
        "out.mp4",
    ]
    assert pc.to_argv('ffmpeg -i "C:\\\\w\\\\clip.mp4" out.mp4', quoted=True) == [
        "ffmpeg",
        "-i",
        "C:/w/clip.mp4",
        "out.mp4",
    ]


CHAIN_PLAN = [
    {"tool": "trim_video", "args": {"input": "clip.mp4", "start": "00:00:01", "end": "00:00:06"}},
    {"tool": "reverse_video", "args": {"inputs": ["clip_trimmed.mp4"]}},
]
TRIM = "ffmpeg -y -ss 00:00:01 -to 00:00:06 -i clip.mp4 -c:v libx264 clip_trimmed.mp4"
REVERSE = "ffmpeg -y -i clip_trimmed.mp4 -vf reverse clip_trimmed_reversed.mp4"


def _python_partial_chain(plan: list[dict]):
    """Python's dry-run of trim -> reverse: reverse stops at its preview/confirmation."""
    out = (
        "  • trim from 00:00:01 to 00:00:06 from clip.mp4\n"
        f"    $ {TRIM}\n    dry-run\n"
        "  • reverse clip_trimmed.mp4\n    (nothing to execute)\n    dry-run\n"
    )
    return pc.parse_python(out, _dump(plan))


def test_a_chain_python_renders_only_in_part_is_not_comparable_when_the_plans_agree() -> None:
    """ffmpeg_140: Python rendered the trim and printed "(nothing to execute)" for the reverse
    (its dry-run stops at the preview confirmation); native rendered both. The same plan with
    one side's commands cut short is not a port bug."""
    nat = _native(TRIM + "\n" + REVERSE, CHAIN_PLAN)
    status, _ = pc.compare(_row(), nat, _python_partial_chain(CHAIN_PLAN), strict=False, cwd="/w")
    assert status == "not-comparable"


def test_a_partial_python_chain_with_a_different_plan_is_still_disagreement() -> None:
    other = [CHAIN_PLAN[0]]
    nat = _native(TRIM + "\n" + REVERSE, CHAIN_PLAN)
    status, _ = pc.compare(_row(), nat, _python_partial_chain(other), strict=False, cwd="/w")
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


# ── the exact argv, as data (Codex audit of 62f7cc1, 2026-09-28) ───────────────────────────
# Display lines cannot carry every argv: Python joins with spaces and quotes nothing, so the
# corpus's `silent clip.mp4` (ffmpeg_288) splits in two, and any backslash rule mangles either a
# path or a filter escape. Both CLIs now dump each ffmpeg argv as JSON under $KNAIF_DUMP_PLAN,
# and the comparator reads that when it is there.


def _argv_dump(argv: list[str]) -> str:
    return f"{pc.ARGV_DUMP_MARKER}{json.dumps(argv)}\n"


def test_a_dumped_argv_is_taken_verbatim_over_the_display_line() -> None:
    argv = ["ffmpeg", "-y", "-i", "silent clip.mp4", "-vf", CROP, "silent clip_thumb.jpg"]
    nat = pc.parse_native("ffmpeg -y -i garbled\n", _dump(PLAN) + _argv_dump(argv))
    py = pc.parse_python("  $ ffmpeg -y -i also garbled\n", _dump(PLAN) + _argv_dump(argv))
    assert nat.commands == py.commands
    assert "silent clip.mp4" in nat.commands[0] and CROP in nat.commands[0]
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "match"


def test_dumped_windows_paths_compare_equal_and_filters_are_untouched() -> None:
    win = ["ffmpeg", "-i", r"C:\w\[draft]\clip.mp4", "-vf", r"drawtext=text='Hi\!'", "out.mp4"]
    fwd = ["ffmpeg", "-i", "C:/w/[draft]/clip.mp4", "-vf", r"drawtext=text='Hi\!'", "out.mp4"]
    a = pc.parse_python("", _dump(PLAN) + _argv_dump(win))
    b = pc.parse_native("", _dump(PLAN) + _argv_dump(fwd))
    assert a.commands == b.commands
    assert r"drawtext=text='Hi\!'" in a.commands[0]


def test_a_hash_in_a_filename_is_not_a_comment() -> None:
    assert pc.to_argv("ffmpeg -i clip#1.mp4 -c copy out.mp4") == [
        "ffmpeg",
        "-i",
        "clip#1.mp4",
        "-c",
        "copy",
        "out.mp4",
    ]


def test_a_partial_chain_still_compares_the_steps_both_rendered() -> None:
    """Codex P1: "not comparable" must cover only the steps Python did not render. A trim that
    renders differently is a port bug even when the reverse after it rendered nothing."""
    other_trim = TRIM.replace("-c:v libx264", "-c:v libx265")
    nat = _native(other_trim + "\n" + REVERSE, CHAIN_PLAN)
    status, _ = pc.compare(_row(), nat, _python_partial_chain(CHAIN_PLAN), strict=False, cwd="/w")
    assert status == "port-bug"


def test_the_python_cli_dumps_each_argv(tmp_path: Path) -> None:
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
    dumps = [ln for ln in proc.stderr.splitlines() if ln.startswith(pc.ARGV_DUMP_MARKER)]
    assert dumps, proc.stderr[-500:]
    argv = json.loads(dumps[0][len(pc.ARGV_DUMP_MARKER) :])
    assert argv[0] == "ffmpeg" and all(isinstance(a, str) for a in argv)


def test_the_argv_marker_is_one_string_in_all_three_places() -> None:
    from knaif.app import ARGV_DUMP_MARKER as app_marker

    rust = (REPO / "apps" / "cli" / "src" / "main.rs").read_text(encoding="utf-8")
    assert f'const ARGV_DUMP_MARKER: &str = "{pc.ARGV_DUMP_MARKER}";' in rust
    assert app_marker == pc.ARGV_DUMP_MARKER


# ── same plan, different outcome (Codex audit of the R5c L3 run, 2026-09-28) ───────────────


def test_same_plan_but_python_asks_and_native_runs_is_a_port_bug() -> None:
    """R5c L3 counted these as model disagreement: both runtimes planned `reverse_video` on
    "mov", Python's NL clarify gate asked, native had no such gate and ran. The plans agreed,
    so the difference is the port's, whatever the two outcomes are."""
    mov = [{"tool": "reverse_video", "args": {"inputs": ["mov"]}}]
    nat = _native("ffmpeg -y -i mov -vf reverse mov_reversed.mp4", mov)
    py = pc.parse_python("\n❓ CLARIFY: Which mov did you mean?\n", _dump(mov))
    status, note = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "port-bug"
    assert "outcome" in note


def test_different_plans_with_different_outcomes_stay_disagreement() -> None:
    nat = _native(CMD_A, PLAN)
    py = pc.parse_python("\n❓ CLARIFY: Which file?\n", _dump(OTHER_PLAN))
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "mismatch"


def test_both_asking_the_same_plan_is_a_match() -> None:
    mov = [{"tool": "reverse_video", "args": {"inputs": ["mov"]}}]
    nat = pc.parse_native("clarify: Which mov did you mean?\n", _dump(mov))
    py = pc.parse_python("\n❓ CLARIFY: Which mov did you mean?\n", _dump(mov))
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "match"
