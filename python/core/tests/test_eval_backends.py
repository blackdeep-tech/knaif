"""`just eval-native-backends <skill>`: the cross-backend check as a recipe, not a one-off (release R2).

2026-09-25's check (evals/runs/2026-09-25_backend-parity-v2_plans/) was a run script: plans for
every utterance on the CUDA, Vulkan and CPU builds, safety on each binary, decision flips against
CUDA held to a bound written first, and a full L4 for any kind over it. R5c needs it again for
every candidate, so it becomes `scripts/eval_backends.sh`, with its verdict in
`knaif.evalsuite.backends` where it can be tested.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from knaif.evalsuite.backends import decide

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "eval_backends.sh"


def _safety(passed: int = 11, total: int = 11, unsafe: int = 0) -> dict:
    return {"passed": passed, "total": total, "unsafe": unsafe}


def _flips(n: int, shared: int = 851, missing: int = 0) -> dict:
    return {"decision_flips": [f"r{i}" for i in range(n)], "shared": shared, "missing": missing}


def _inputs(**over):
    base = {
        "safety": {"cuda": _safety(), "vulkan": _safety(), "cpu": _safety()},
        "repeat": _flips(0),
        "flips": {"vulkan": _flips(29), "cpu": _flips(37)},
    }
    base.update(over)
    return base


def test_the_2026_09_25_ffmpeg_numbers_send_cpu_to_a_full_l4() -> None:
    v = decide(max_flips=35, **_inputs())
    assert v["status"] == "fallback"
    assert v["fallback"] == ["cpu"]


def test_all_within_the_bound_and_safe_passes() -> None:
    v = decide(max_flips=40, **_inputs())
    assert v["status"] == "pass" and v["fallback"] == []


def test_a_safety_miss_on_any_binary_fails_outright() -> None:
    inputs = _inputs()
    inputs["safety"]["vulkan"] = _safety(passed=10)
    v = decide(max_flips=40, **inputs)
    assert v["status"] == "fail"


def test_a_breach_fails_even_when_the_count_matches() -> None:
    inputs = _inputs()
    inputs["safety"]["cpu"] = _safety(unsafe=1)
    assert decide(max_flips=40, **inputs)["status"] == "fail"


def test_a_non_deterministic_control_makes_the_run_inconclusive() -> None:
    """The CUDA repeat is what says a flip is the backend and not run-to-run noise."""
    v = decide(max_flips=40, **_inputs(repeat=_flips(2)))
    assert v["status"] == "inconclusive"


def test_missing_utterances_are_over_the_bound() -> None:
    inputs = _inputs()
    inputs["flips"]["vulkan"] = _flips(0, missing=5)
    v = decide(max_flips=40, **inputs)
    assert "vulkan" in v["fallback"]


def test_the_recipe_exists_and_calls_the_script() -> None:
    text = (REPO / "justfile").read_text(encoding="utf-8")
    assert "eval-native-backends skill" in text and "scripts/eval_backends.sh" in text


def test_the_script_carries_the_hard_won_fixes() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "KNAIF_N_THREADS" in text, "the thread cap (the CPU hit 84 C uncapped)"
    assert "tr -d '\\r'" in text, "CRLF stripped when reading the fallback list"
    assert "KNAIF_PDFIUM_PATH=" not in text, "PDFium is bundled now; no override"


def test_the_bound_must_be_written_before_the_run() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    proc = subprocess.run([bash, SCRIPT.as_posix(), "ffmpeg"], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "--max-flips" in proc.stderr
