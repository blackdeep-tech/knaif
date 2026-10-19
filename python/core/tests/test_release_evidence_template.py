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


def _sourced(tmp_path: Path, platform: str, body: str, **env: str) -> subprocess.CompletedProcess:
    """Source the shared script as *platform* and run *body*; HOME is a scratch folder, so the
    macOS guard (no checkout inside the home folder) holds on any machine."""
    exports = " ".join(f"{k}='{v}'" for k, v in env.items())
    script = f"""
export VERSION=1.4.0 R='{(tmp_path / "run").as_posix()}' PLATFORM={platform} LANE=r140-x- {exports}
export HOME='{(tmp_path / "home").as_posix()}'
source scripts/release_evidence.sh
{body}
"""
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, cwd=ROOT)


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize(
    "platform,backends",
    [
        ("windows", {"cuda": "CUDA0", "vulkan": "Vulkan0", "cpu": "CPU"}),
        ("linux", {"cuda": "CUDA0", "cpu": "CPU"}),
        ("macos", {"metal": "MTL0", "cpu": "CPU"}),
    ],
)
def test_each_platform_runs_its_own_cells_on_their_devices(
    tmp_path: Path, platform: str, backends: dict[str, str]
) -> None:
    body = 'echo "gpu=$GPU_BACKENDS"\n' + "".join(
        f'backend_env {b}; echo "{b} $WANT ${{#ENVS[@]}} $(cell_backend {b})"\n' for b in backends
    )
    out = _sourced(tmp_path, platform, body, CPU_LANE="r140-x-cpu-").stdout.splitlines()
    assert out[0] == "gpu=" + " ".join(b for b in backends if b != "cpu")
    for line, (backend, want) in zip(out[1:], backends.items(), strict=True):
        name, got, n_env, key = line.split()
        assert (name, got) == (backend, want)
        assert int(n_env) >= 1, "never an empty array: bash 3.2 refuses one under set -u"
        assert key == ("mtl" if backend == "metal" else backend), "the cell key accept-native uses"


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_macos_cpu_cells_use_the_metal_less_copy(tmp_path: Path) -> None:
    out = _sourced(tmp_path, "macos", 'lane_for 4b metal; lane_for 4b cpu; echo "$EXE_REL"',
                   CPU_LANE="r140-mac-cpu-").stdout.split()  # fmt: skip
    assert out == [
        "r140-x-4b",
        "r140-mac-cpu-4b",
        "sandbox/r140/artifact/knaif-1.4.0-macos-arm64/bin/knaif",
    ]


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_an_unknown_platform_is_refused(tmp_path: Path) -> None:
    proc = _sourced(tmp_path, "freebsd", "echo SOURCED")
    assert proc.returncode == 2 and "SOURCED" not in proc.stdout
    assert "PLATFORM must be" in proc.stderr


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize(
    "platform,stage,env,refused",
    [
        ("linux", "l3", {}, "L3 runs on Windows (CUDA) and macOS (Metal)"),
        (
            "linux",
            "live",
            {"MODEL": "4b", "BACKEND": "vulkan", "SKILL": "ffmpeg"},
            "no vulkan cells",
        ),
        ("macos", "live", {"MODEL": "4b", "BACKEND": "cuda", "SKILL": "ffmpeg"}, "no cuda cells"),
        ("windows", "live", {"MODEL": "4b"}, "live needs MODEL"),
        ("windows", "bogus", {}, "unknown stage"),
    ],
)
def test_a_stage_the_platform_does_not_run_is_refused_before_anything_happens(
    tmp_path: Path, platform: str, stage: str, env: dict[str, str], refused: str
) -> None:
    proc = _sourced(tmp_path, platform, f"release_stage {stage}", CPU_LANE="c-", **env)
    assert proc.returncode == 2
    assert refused in proc.stderr
    assert not (tmp_path / "run").exists(), "refused before the run folder or its lock"


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_only_the_first_platform_draws_the_cold_start_sample(tmp_path: Path) -> None:
    """Drawn once per release (I5): the other platforms' runs use the same committed files."""
    other = (tmp_path / "windows-run").as_posix()
    proc = _sourced(tmp_path, "linux", "release_stage draw", COLD_START_DIR=other)
    assert proc.returncode == 1
    assert "draws its cold-start samples once" in (tmp_path / "run" / "verdicts.txt").read_text(
        encoding="utf-8"
    )
    out = _sourced(tmp_path, "linux", "cold_start ffmpeg", COLD_START_DIR=other).stdout.strip()
    assert out == f"{other}/cold_start_ffmpeg.json"


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_linux_logs_name_the_windows_checkout_without_its_path(tmp_path: Path) -> None:
    body = (
        "export REL_ORIGIN=/mnt/c/Work/someone/knaif/\n"
        'printf \'%s\\n\' /mnt/c/Work/someone/knaif/dist/k.tar.gz "$PWD/evals/x" "$HOME/r" | rel'
    )
    out = _sourced(tmp_path, "linux", body).stdout.splitlines()
    assert out == ["<windows-checkout>/dist/k.tar.gz", "evals/x", "~/r"]


@pytest.mark.skipif(shutil.which("cygpath") is None, reason="Git Bash only")
def test_windows_logs_drop_every_spelling_of_the_checkout_and_home(tmp_path: Path) -> None:
    body = r"""
win="$(cygpath -aw "$PWD")"; mixed="$(cygpath -am "$PWD")"; hw="$(cygpath -aw "$HOME")"
json="${win//\\/\\\\}"
printf '%s\n' "$win\\a.txt" "$mixed/b.txt" "$PWD/c.txt" "$json\\\\d.txt" "$hw\\e" "$HOME/f" | rel
"""
    (tmp_path / "home").mkdir()
    out = _sourced(tmp_path, "windows", body).stdout.splitlines()
    assert out == ["a.txt", "b.txt", "c.txt", "d.txt", "~\\e", "~/f"]


def _lock_case(tmp_path: Path, setup: str) -> subprocess.CompletedProcess:
    """Source the shared script and try to take the stage lock after *setup* ran."""
    script = f"""
export VERSION=0.0.0 R='{tmp_path.as_posix()}'
source scripts/release_evidence.sh 2>/dev/null
STAGE=test
mkdir -p "$R"
{setup}
( take_lock; echo TOOK; [ -f "$R/.running/pid" ] && echo HELD )
rc=$?
[ -d "$R/.running" ] && echo LEFT
echo "rc=$rc"
[ -n "${{p:-}}" ] && kill "$p" 2>/dev/null
"""
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, cwd=ROOT)


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_a_free_lock_is_taken_and_released(tmp_path: Path) -> None:
    out = _lock_case(tmp_path, "").stdout
    assert "TOOK" in out and "HELD" in out and "LEFT" not in out and "rc=0" in out


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_a_running_stage_keeps_its_lock(tmp_path: Path) -> None:
    proc = _lock_case(
        tmp_path,
        'mkdir "$R/.running"; sleep 30 >/dev/null 2>&1 & p=$!; echo $p > "$R/.running/pid"',
    )
    assert "TOOK" not in proc.stdout and "rc=2" in proc.stdout
    assert "another stage is running" in proc.stderr


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_a_lock_left_by_a_killed_stage_is_taken_over(tmp_path: Path) -> None:
    """A hard kill skips the EXIT trap; the lock then names a process that is gone."""
    proc = _lock_case(
        tmp_path, 'mkdir "$R/.running"; sleep 0 & p=$!; wait $p; echo $p > "$R/.running/pid"'
    )
    assert "TOOK" in proc.stdout and "rc=0" in proc.stdout and "LEFT" not in proc.stdout
    assert "stale lock" in (tmp_path / "COMPLETE").read_text(encoding="utf-8")


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_a_lock_just_taken_by_another_stage_is_not_stolen(tmp_path: Path) -> None:
    """Between `mkdir` and writing its pid another stage holds a lock with no pid yet."""
    proc = _lock_case(tmp_path, 'mkdir "$R/.running"')
    assert "TOOK" not in proc.stdout and "rc=2" in proc.stdout
