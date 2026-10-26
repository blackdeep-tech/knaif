"""The cases of `contracts/parity/cli_output_cases.json`. that run ffmpeg.

Core tests never start ffmpeg (python/core/tests/conftest.py), so the cases that execute an
ffmpeg plan are checked here, beside the skill whose binary they need; the rest are in
python/core/tests/test_cli_output_parity.py, which this runner mirrors.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from knaif.app import cli

REPO = Path(__file__).parents[4]
CASES = [
    c
    for c in json.loads(
        (REPO / "contracts" / "parity" / "cli_output_cases.json").read_text(encoding="utf-8")
    )["cases"]
    if c["skill"] == "ffmpeg" and c.get("fixtures") and "--dry-run" not in c["flags"]
]
_TIMING = re.compile(r"(?<!\d)\d+\.\d+ ?s(?![^\W_])")


def _masked(text: str) -> str:
    # Line ends as `\n`: a generated text fixture has the platform's.
    return _TIMING.sub("<t> s", text.replace("\r\n", "\n"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_cli_output_executing(
    case: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in case.get("files", []):
        (tmp_path / name).write_bytes(b"")
    for name in case["fixtures"]:
        source = REPO / "sandbox" / "fixtures" / "ffmpeg" / name
        if not source.is_file():
            pytest.skip(f"fixture {name} missing (regenerate the ffmpeg fixtures)")
        shutil.copy(source, tmp_path / name)
    rich = case.get("view") == "rich"
    env = {
        "KNAIF_LLM_BACKEND": "mock",
        "KNAIF_LLM_MOCK_RESPONSE": json.dumps(case["plan"]),
        "KNAIF_DUMP_PLAN": None,
        "KNAIF_DEBUG": None,
        "KNAIF_VIEW": "rich" if rich else None,
        "NO_COLOR": "1" if rich else None,
    }
    argv = ["run", case["skill"], *case["flags"], case["utterance"]]
    result = CliRunner().invoke(cli, argv, env=env)
    assert _masked(result.stdout) == case["stdout"]
    if "stderr_prefix" in case:
        assert result.stderr.startswith(case["stderr_prefix"]), result.stderr
    else:
        assert _masked(result.stderr) == case["stderr"]
    assert (result.exit_code == 0) == case["ok"], result.output
    if "files_after" in case:
        assert sorted(f.name for f in tmp_path.iterdir()) == case["files_after"]
