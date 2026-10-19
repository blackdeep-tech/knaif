"""The release evidence script and its per-release template (plan 2026-10-14-faster-release-evidence,
Part I, T4).

The template carries the approved rules verbatim, so this keeps that copy equal to the plan's Part I:
a rule changed in one place and not the other would let a release run under rules nobody approved.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(".").resolve()
TEMPLATE = ROOT / "scripts" / "release_evidence_run_all.template.sh"
SHARED = ROOT / "scripts" / "release_evidence.sh"
PLAN = ROOT / "docs" / "plans" / "2026-10-14-faster-release-evidence.md"

BASH = shutil.which("bash")


def _plan_rules() -> list[str]:
    text = PLAN.read_text(encoding="utf-8")
    block = re.search(r"^(\*\*I1\. What is measured.*?)^\*\*I10\.", text, re.S | re.M)
    assert block, "Part I's rules I1-I9 not found in the plan"
    return block.group(1).rstrip().splitlines()


def _template_rules() -> list[str]:
    lines = TEMPLATE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("#   **I1. What is measured"))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("# ----"))
    body = lines[start:end]
    while body and body[-1] in ("#", ""):
        body.pop()
    return [ln[4:] if ln.startswith("#   ") else "" for ln in body]


def test_the_template_carries_part_i_verbatim() -> None:
    assert _template_rules() == _plan_rules()


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("script", [SHARED, TEMPLATE], ids=lambda p: p.name)
def test_the_scripts_parse(script: Path) -> None:
    subprocess.run([BASH, "-n", str(script)], check=True)


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_an_unfilled_template_refuses_to_run(tmp_path: Path) -> None:
    run = tmp_path / "evals" / "runs" / "2026-01-01_rX-windows_success"
    run.mkdir(parents=True)
    copy = run / "run_all.sh"
    copy.write_bytes(TEMPLATE.read_bytes())
    proc = subprocess.run([BASH, str(copy), "check"], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 2
    assert "placeholders" in proc.stderr


@pytest.mark.parametrize(
    "path,ignored",
    [
        ("evals/store/replies/abc/0123.txt", False),
        ("evals/runs/2026-01-01_rX-windows_success/cold_start_ffmpeg.json", False),
        ("evals/runs/2026-01-01_rX-windows_success/fingerprints.json", False),
        ("evals/runs/2026-01-01_rX-windows_success/store/replies/abc/0123.txt", True),
        ("evals/runs/2026-01-01_rX-windows_success/4b/cuda/ffmpeg_lane_success.json", True),
    ],
)
def test_what_a_release_run_commits(path: str, ignored: bool) -> None:
    proc = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
    assert (proc.returncode == 0) is ignored, path


def test_stored_replies_are_committed_byte_for_byte() -> None:
    """A reply is replayed into the binary as recorded; `* text=auto` would rewrite its line
    endings on a Windows checkout (found by the Codex audit's last step, 2026-10-19)."""
    out = subprocess.run(
        ["git", "check-attr", "text", "diff", "--", "evals/store/replies/x/y.txt"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert "text: unset" in out
    assert "diff: unset" in out
