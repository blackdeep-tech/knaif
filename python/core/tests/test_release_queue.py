"""scripts/release_queue.sh end to end: real bash, the relay, and a fake gh that keeps the issue in a file."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BASH = shutil.which("bash")
if BASH and os.name == "nt" and "system32" in BASH.lower():
    BASH = None  # WSL's bash.exe cannot see this Python
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

VERDICT = """NOT ACCEPTED - 2 of 255 thresholds unmet:
  [regression] ffmpeg_039#0: Python passed it, the binary did not.
  [regression] ffmpeg_alert_005#0: Python passed it, the binary did not ('error', expected 'clarify').
"""

QUEUE = """
R="{run}"
RELAY_REPO=o/r
RELAY_TITLE="T"
RELAY_AUTHORS="GalinDinkov"
source scripts/release_queue.sh
ok() {{ note "all good"; return 0; }}
missy() {{ touch "$R/missy-ran"; cell_check 4b cpu ffmpeg "$R/cell"; return 3; }}
step one 1 ok
step two 1 missy
queue_end "fin"
"""


def _decision(action: str) -> str:
    return (
        "ok\n\n```knaif-decision\nstep: two\naction: " + action + "\naccept:\n"
        "  - {kind: regression, row: ffmpeg_alert_005, models: [4b], backends: [cpu]}\n```\n"
    )


def _setup(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    run = tmp_path / "run"
    (run / "cell").mkdir(parents=True)
    (run / "cell" / "ffmpeg_verdict.txt").write_text(VERDICT, encoding="utf-8")
    (run / "accepted.yaml").write_text("- {kind: regression, row: ffmpeg_039}\n", encoding="utf-8")
    queue = tmp_path / "queue.sh"
    queue.write_text(QUEUE.format(run=run.as_posix()), encoding="utf-8")
    env = {
        **os.environ,
        "UV": "env",
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")]),
        "KNAIF_RELAY_GH": f"{Path(sys.executable).as_posix()} {(REPO / 'python/core/tests/fake_gh.py').as_posix()}",
        "FAKE_GH_DIR": str(tmp_path),
        "FAKE_GH_TITLE": "T",
        "RELAY_POLL": "0.1",
    }
    return run, queue, env


def _run(queue: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, queue.as_posix()], cwd=REPO, env=env, capture_output=True, text=True, timeout=300
    )


def _comments(tmp_path: Path) -> list[dict]:
    lines = (tmp_path / "comments.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip()]


def test_a_new_miss_waits_for_the_owner_and_their_rule_lets_it_through(tmp_path: Path) -> None:
    run, queue, env = _setup(tmp_path)
    env |= {"FAKE_GH_ANSWER_ON": "ffmpeg_alert_005", "FAKE_GH_ANSWER": _decision("continue")}
    p = _run(queue, env)
    assert p.returncode == 0, p.stdout + p.stderr

    q = run / "queue"
    assert (q / "done" / "one").exists() and (q / "done" / "two").exists()
    decided = [
        json.loads(x) for x in (q / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert decided[0]["action"] == "continue"
    assert decided[0]["accept"][0]["row"] == "ffmpeg_alert_005"

    reports = [c for c in _comments(tmp_path) if "```knaif-report" in c["body"]]
    statuses = [(c["body"].split("`")[1], c["body"].split(": ")[1].split(" ")[0]) for c in reports]
    assert statuses == [
        ("one", "started"),
        ("one", "done"),
        ("two", "started"),
        ("two", "waiting"),
        ("end", "done"),
    ]
    waiting = reports[3]["body"]
    assert "ffmpeg_alert_005" in waiting and "knaif-decision" in waiting

    # Started again: nothing runs twice.
    (run / "missy-ran").unlink()
    assert _run(queue, env).returncode == 0
    assert not (run / "missy-ran").exists()


def test_stop_ends_the_queue_with_exit_4(tmp_path: Path) -> None:
    run, queue, env = _setup(tmp_path)
    env |= {"FAKE_GH_ANSWER_ON": "ffmpeg_alert_005", "FAKE_GH_ANSWER": _decision("stop")}
    p = _run(queue, env)
    assert p.returncode == 4, p.stdout + p.stderr
    assert not (run / "queue" / "done" / "two").exists()
    assert "STOPPED at two" in (run / "queue" / "LOG").read_text(encoding="utf-8")


def test_a_queue_stopped_while_it_waited_waits_on_the_same_report(tmp_path: Path) -> None:
    run, queue, env = _setup(tmp_path)
    q = run / "queue"
    (q / "done").mkdir(parents=True)
    (q / "waiting").mkdir()
    (q / "done" / "one").touch()
    (q / "waiting" / "two").write_text("150\n", encoding="utf-8")
    with (tmp_path / "comments.jsonl").open("w", encoding="utf-8") as f:
        f.write(
            json.dumps({"id": 160, "body": _decision("continue"), "user": {"login": "GalinDinkov"}})
            + "\n"
        )
    p = _run(queue, env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert not (run / "missy-ran").exists()  # the step did not run again
    assert (q / "done" / "two").exists()
